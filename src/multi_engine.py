import asyncio
import shutil
from collections import Counter
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config import cfg
from src.lobbies import Lobby
from src.services.bot import BotEngine
from src.services.director import DirectorEngine
from src.services.judge import JudgeService
from src.schemas import PlayerProfile, GameState
from src.utils import GameSetup
from src.s3_service import s3_uploader

bot_engine = BotEngine()
judge_service = JudgeService()
director_engine = DirectorEngine()


def get_topic_base(round_num, trait="", cat_data=None):
    topics_cfg = cfg.gameplay["rounds"]["topics"]
    if round_num == 1:
        return topics_cfg[1]
    elif round_num == 2:
        return topics_cfg[2].format(trait=trait)
    else:
        if cat_data:
            idx = (round_num - 3) % len(cat_data["topics"])
            return topics_cfg[3].format(catastrophe_problem=cat_data["topics"][idx])
        return "ВЫЖИВАНИЕ."


def get_display_topic(gs: GameState, p: PlayerProfile, cat_data: dict) -> str:
    if gs.phase == "presentation":
        return get_topic_base(gs.round, p.trait, cat_data)
    elif gs.phase == "discussion":
        return "ОБСУЖДЕНИЕ. Кто лишний?"
    elif gs.phase == "runoff":
        candidates = ", ".join(gs.runoff_candidates)
        return f"ПЕРЕСТРЕЛКА. Оправдываются: {candidates}"
    return "..."


# --- УПРАВЛЕНИЕ СООБЩЕНИЯМИ ---
async def broadcast_turn_start(lobby: Lobby, text: str, bot: Bot, exclude_id: int = None):
    if lobby.logger: lobby.logger.log_chat_message("SYSTEM", text)
    lobby.turn_messages.clear()

    for p in lobby.players:
        if exclude_id and p["user_id"] == exclude_id: continue
        final_text = text
        if p["user_id"] < 0: final_text = f"<b>[DEBUG for {p['name']}]</b>\n{text}"
        try:
            msg = await bot.send_message(p["chat_id"], final_text, parse_mode="HTML")
            lobby.turn_messages[p["user_id"]] = msg.message_id
            if p["user_id"] < 0: await asyncio.sleep(0.3)
        except:
            pass


async def broadcast_speech(lobby: Lobby, text: str, bot: Bot, exclude_id: int = None):
    for p in lobby.players:
        if exclude_id and p["user_id"] == exclude_id: continue
        final_text = text
        if p["user_id"] < 0: final_text = f"<b>[DEBUG for {p['name']}]</b>\n{text}"

        msg_id_to_edit = lobby.turn_messages.get(p["user_id"])
        sent = False
        if msg_id_to_edit:
            try:
                await bot.edit_message_text(text=final_text, chat_id=p["chat_id"], message_id=msg_id_to_edit,
                                            parse_mode="HTML")
                sent = True
            except:
                sent = False
        if not sent:
            try:
                await bot.send_message(p["chat_id"], final_text, parse_mode="HTML")
            except:
                pass
        if p["user_id"] < 0: await asyncio.sleep(0.3)
    lobby.turn_messages.clear()


async def broadcast(lobby: Lobby, text: str, bot: Bot, exclude_id: int = None, reply_markup=None):
    if lobby.logger: lobby.logger.log_chat_message("SYSTEM", text)
    for p in lobby.players:
        if exclude_id and p["user_id"] == exclude_id: continue
        final_text = text
        if p["user_id"] < 0: final_text = f"<b>[DEBUG for {p['name']}]</b>\n{text}"
        try:
            await bot.send_message(p["chat_id"], final_text, parse_mode="HTML", reply_markup=reply_markup)
            if p["user_id"] < 0: await asyncio.sleep(0.3)
        except:
            pass


# --- ДВИЖОК ---
async def process_multi_turn(lobby: Lobby, bot: Bot):
    if not lobby.game_state: return
    gs = lobby.game_state
    players = lobby.game_players

    if lobby.current_turn_index >= len(players):
        if gs.phase == "presentation":
            gs.phase = "discussion"
            lobby.current_turn_index = 0

            sep = "=========================\n⚔️ <b>ФАЗА 2: ОБСУЖДЕНИЕ</b>\n========================="
            await broadcast(lobby, sep, bot)

            await asyncio.sleep(1)
            await process_multi_turn(lobby, bot)
            return
        elif gs.phase == "discussion":
            gs.phase = "voting"
            await start_multi_voting(lobby, bot)
            return
        elif gs.phase == "runoff":
            gs.phase = "voting"
            await start_multi_voting(lobby, bot)
            return
        return

    current_player = players[lobby.current_turn_index]

    skip_turn = False
    if not current_player.is_alive: skip_turn = True
    if gs.phase == "runoff" and current_player.name not in gs.runoff_candidates: skip_turn = True
    if skip_turn:
        lobby.current_turn_index += 1
        await process_multi_turn(lobby, bot)
        return

    actual_topic = get_display_topic(gs, current_player, lobby.catastrophe_data)

    if current_player.is_human:
        target_user = next((p for p in lobby.players if p["name"] == current_player.name), None)
        if target_user:
            await broadcast_turn_start(lobby, f"⏳ Ходит игрок <b>{current_player.name}</b>...", bot,
                                       exclude_id=target_user["user_id"])

            msg_text = f"👉 <b>ВАШ ХОД!</b>\nТема: {actual_topic}\nНапиши сообщение в чат."

            if target_user["user_id"] < 0:
                msg_text = f"<b>[DEBUG for {target_user['name']}]</b>\n{msg_text}"

            await bot.send_message(target_user["chat_id"], msg_text, parse_mode="HTML")
            return
    else:
        await broadcast_turn_start(lobby, f"⏳ Ходит игрок <b>{current_player.name}</b>...", bot)

        temp_gs = gs.model_copy()
        temp_gs.topic = actual_topic

        instr = await director_engine.get_hidden_instruction(current_player, players, temp_gs, logger=lobby.logger)
        speech = await bot_engine.make_turn(current_player, players, temp_gs, instr, logger=lobby.logger)

        gs.history.append(f"[{current_player.name}]: {speech}")
        if lobby.logger: lobby.logger.log_chat_message(current_player.name, speech)

        display_name = GameSetup.get_display_name(current_player, gs.round)
        await broadcast_speech(lobby, f"🤖 {display_name}:\n{speech}", bot)

        lobby.current_turn_index += 1
        await asyncio.sleep(2)
        await process_multi_turn(lobby, bot)


async def handle_human_message(lobby: Lobby, bot: Bot, text: str, user_name: str):
    if lobby.game_state.phase not in ["presentation", "discussion", "runoff"]: return
    if lobby.current_turn_index >= len(lobby.game_players): return
    current_player = lobby.game_players[lobby.current_turn_index]

    if not current_player.is_alive:
        lobby.current_turn_index += 1
        await process_multi_turn(lobby, bot)
        return

    if current_player.name != user_name: return

    lobby.game_state.history.append(f"[{current_player.name}]: {text}")

    if lobby.logger:
        lobby.logger.log_chat_message(current_player.name, text)
        actual_topic = get_display_topic(lobby.game_state, current_player, lobby.catastrophe_data)
        asyncio.create_task(judge_service.analyze_move(current_player, text, actual_topic, logger=lobby.logger))

    author_user_id = None
    for p in lobby.players:
        if p["name"] == user_name:
            author_user_id = p["user_id"]
            # "Принято" УБРАНО
            break

    display_name = GameSetup.get_display_name(current_player, lobby.game_state.round)
    await broadcast_speech(lobby, f"👤 <b>{display_name}</b>:\n{text}", bot, exclude_id=author_user_id)

    lobby.current_turn_index += 1
    await process_multi_turn(lobby, bot)


# --- ГОЛОСОВАНИЕ ---
async def start_multi_voting(lobby: Lobby, bot: Bot):
    lobby.votes.clear()
    title = "ГОЛОСОВАНИЕ"
    if lobby.game_state.phase == "runoff" or lobby.game_state.runoff_candidates:
        title = f"ПЕРЕГОЛОСОВАНИЕ ({' vs '.join(lobby.game_state.runoff_candidates)})"

    if lobby.logger: lobby.logger.log_game_event("VOTE_START", title)

    for p in lobby.players:
        game_p_self = next((gp for gp in lobby.game_players if gp.name == p["name"]), None)
        if not game_p_self or not game_p_self.is_alive: continue

        # Логика для фейков (Debug)
        if p["user_id"] < 0:
            debug_msg = f"🔧 [DEBUG] Голосование для <b>{p['name']}</b>. Используйте:\n<code>/vote_as {p['name']} [Цель]</code>"
            try:
                await bot.send_message(p["chat_id"], debug_msg, parse_mode="HTML")
            except:
                pass
            continue

        valid_targets = []
        if lobby.game_state.runoff_candidates:
            valid_targets = [t for t in lobby.game_players if t.name in lobby.game_state.runoff_candidates]
        else:
            valid_targets = [t for t in lobby.game_players if t.is_alive]

        kb = InlineKeyboardBuilder()
        for target in valid_targets:
            if target.name == p["name"] and not cfg.gameplay["voting"]["allow_self_vote"]: continue
            btn_text = f"☠ {target.name} [{target.profession}]"
            kb.add(InlineKeyboardButton(text=btn_text, callback_data=f"mvote_{target.name}"))
        kb.adjust(1)
        msg_text = f"🛑 <b>{title}</b>\nВыберите, кто покинет бункер."
        try:
            await bot.send_message(p["chat_id"], msg_text, reply_markup=kb.as_markup(), parse_mode="HTML")
        except:
            pass

    gs = lobby.game_state
    if gs.runoff_candidates:
        bot_targets = [t for t in lobby.game_players if t.name in gs.runoff_candidates]
    else:
        bot_targets = [t for t in lobby.game_players if t.is_alive]

    for p in lobby.game_players:
        if not p.is_human and p.is_alive:
            vote_target = await bot_engine.make_vote(p, bot_targets, gs, logger=lobby.logger)
            await handle_vote(lobby, bot, p.name, vote_target)


async def handle_vote(lobby: Lobby, bot: Bot, voter_name: str, target_name: str):
    lobby.votes[voter_name] = target_name
    if lobby.logger: lobby.logger.log_game_event("VOTE", f"{voter_name} -> {target_name}")
    alive_players = [p for p in lobby.game_players if p.is_alive]
    if len(lobby.votes) >= len(alive_players): await finish_voting(lobby, bot)


async def finish_voting(lobby: Lobby, bot: Bot):
    counts = Counter(lobby.votes.values())
    results = counts.most_common()
    if not results: return

    leader_name, leader_votes = results[0]
    leaders = [name for name, count in results if count == leader_votes]

    total_votes = sum(counts.values())
    result_text = "📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n"
    for name, cnt in counts.items():
        bar_len = int((cnt / total_votes) * 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)
        result_text += f"<code>{bar}</code> {cnt} - {name}\n"

    if lobby.logger: lobby.logger.log_game_event("VOTE_RESULTS", str(dict(counts)))
    gs = lobby.game_state

    if len(leaders) > 1:
        if gs.runoff_count >= 1:
            report = GameSetup.generate_game_report(lobby.game_players)
            await broadcast(lobby, f"{result_text}\n⚖️ <b>НИЧЬЯ №2! БУНКЕР ЗАКРЫТ.</b>\n{report}", bot)
            await close_multi_lobby(lobby, bot)
            return
        gs.phase = "runoff"
        gs.runoff_candidates = leaders
        gs.runoff_count += 1
        lobby.current_turn_index = 0
        lobby.votes.clear()
        await broadcast(lobby, f"{result_text}\n⚖️ <b>НИЧЬЯ!</b> ({' vs '.join(leaders)})\n🗣 ПЕРЕСТРЕЛКА.", bot)
        await asyncio.sleep(2)
        await process_multi_turn(lobby, bot)
        return

    await broadcast(lobby, f"{result_text}\n🚪 <b>{leader_name}</b> изгнан.", bot)
    for p in lobby.game_players:
        if p.name == leader_name:
            p.is_alive = False
            break

    leader_user = next((p for p in lobby.players if p["name"] == leader_name), None)
    if leader_user:
        report = GameSetup.generate_game_report(lobby.game_players)
        try:
            kb = InlineKeyboardBuilder()
            kb.add(InlineKeyboardButton(text="🔄 В Меню", callback_data="back_to_menu"))
            await bot.send_message(leader_user["chat_id"], f"💀 <b>ВАС ИЗГНАЛИ.</b>\n{report}",
                                   reply_markup=kb.as_markup(), parse_mode="HTML")
            lobby.remove_player(leader_user["user_id"])
        except:
            pass

    humans_alive = any(p.is_human and p.is_alive for p in lobby.game_players)
    if not humans_alive:
        report = GameSetup.generate_game_report(lobby.game_players)
        await broadcast(lobby, f"💀 <b>GAME OVER</b>. Все люди погибли.\n{report}", bot)
        await close_multi_lobby(lobby, bot)
        return
    survivors_count = sum(1 for p in lobby.game_players if p.is_alive)
    if survivors_count <= cfg.gameplay["rounds"]["target_survivors"]:
        report = GameSetup.generate_game_report(lobby.game_players)
        names = ", ".join([p.name for p in lobby.game_players if p.is_alive])
        await broadcast(lobby, f"🎉 <b>ПОБЕДА!</b> Выжили: {names}\n{report}", bot)
        await close_multi_lobby(lobby, bot)
        return

    gs.round += 1
    gs.phase = "presentation"
    lobby.current_turn_index = 0
    lobby.votes.clear()
    gs.runoff_candidates = []
    gs.runoff_count = 0
    base_topic = get_topic_base(gs.round, "...", lobby.catastrophe_data)
    gs.topic = base_topic

    await asyncio.sleep(2)

    sep = f"━━━━━━━━━━━━━━━━━━━\n🔥 <b>РАУНД {gs.round}</b>\n━━━━━━━━━━━━━━━━━━━\n<blockquote>{base_topic}</blockquote>"
    await broadcast(lobby, sep, bot)

    await process_multi_turn(lobby, bot)


async def close_multi_lobby(lobby: Lobby, bot: Bot):
    lobby.status = "finished"
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="🔄 В Меню", callback_data="back_to_menu"))
    await broadcast(lobby, "Игра окончена.", bot, reply_markup=kb.as_markup())

    if lobby.logger:
        try:
            path = lobby.logger.get_session_path()
            await asyncio.to_thread(s3_uploader.upload_session_folder, path)
            shutil.rmtree(path)
        except:
            pass