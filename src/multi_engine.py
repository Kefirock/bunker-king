import asyncio
import shutil
from collections import Counter
from aiogram import Bot
from aiogram.types import InlineKeyboardButton
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


async def broadcast(lobby: Lobby, text: str, bot: Bot, exclude_id: int = None, reply_markup=None):
    """Рассылает сообщение."""
    # Логируем в файл лобби
    if lobby.logger:
        lobby.logger.log_chat_message("SYSTEM", text)

    for p in lobby.players:
        if exclude_id and p["user_id"] == exclude_id:
            continue

        target_chat_id = p["chat_id"]
        final_text = text

        if p["user_id"] < 0:
            final_text = f"<b>[DEBUG for {p['name']}]</b>\n{text}"

        try:
            await bot.send_message(target_chat_id, final_text, parse_mode="HTML", reply_markup=reply_markup)
            if p["user_id"] < 0: await asyncio.sleep(0.3)
        except:
            pass


async def process_multi_turn(lobby: Lobby, bot: Bot):
    """Главный цикл хода"""
    if not lobby.game_state: return

    gs = lobby.game_state
    players = lobby.game_players

    if lobby.current_turn_index >= len(players):
        if gs.phase == "presentation":
            gs.phase = "discussion"
            lobby.current_turn_index = 0
            await broadcast(lobby, f"⚔️ <b>ФАЗА 2: ОБСУЖДЕНИЕ</b>", bot)
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
            await broadcast(lobby, f"👉 Ходит <b>{current_player.name}</b>...", bot, exclude_id=target_user["user_id"])
            msg_text = f"👤 <b>ТВОЙ ХОД!</b>\nТема: {actual_topic}\nНапиши сообщение в чат."
            if target_user["user_id"] < 0:
                msg_text = f"<b>[DEBUG for {target_user['name']}]</b>\n{msg_text}"
            await bot.send_message(target_user["chat_id"], msg_text, parse_mode="HTML")
            return
    else:
        await broadcast(lobby, f"🤖 <b>{current_player.name}</b> печатает...", bot)
        temp_gs = gs.model_copy()
        temp_gs.topic = actual_topic

        instr = await director_engine.get_hidden_instruction(current_player, players, temp_gs)
        speech = await bot_engine.make_turn(current_player, players, temp_gs, instr)

        gs.history.append(f"[{current_player.name}]: {speech}")

        # Логируем речь бота
        if lobby.logger:
            lobby.logger.log_chat_message(current_player.name, speech)

        display_name = GameSetup.get_display_name(current_player, gs.round)
        await broadcast(lobby, f"🤖 <b>{display_name}</b>:\n{speech}", bot)

        lobby.current_turn_index += 1
        await asyncio.sleep(2)
        await process_multi_turn(lobby, bot)


async def handle_human_message(lobby: Lobby, bot: Bot, text: str, user_name: str):
    """Обработка текста игрока"""
    if lobby.game_state.phase not in ["presentation", "discussion", "runoff"]: return
    if lobby.current_turn_index >= len(lobby.game_players): return

    current_player = lobby.game_players[lobby.current_turn_index]

    if not current_player.is_alive:
        lobby.current_turn_index += 1
        await process_multi_turn(lobby, bot)
        return

    if current_player.name != user_name: return

    lobby.game_state.history.append(f"[{current_player.name}]: {text}")

    # Логируем речь человека
    if lobby.logger:
        lobby.logger.log_chat_message(current_player.name, text)
        # Судья тоже пишет в этот логгер
        actual_topic = get_display_topic(lobby.game_state, current_player, lobby.catastrophe_data)
        asyncio.create_task(judge_service.analyze_move(current_player, text, actual_topic, logger=lobby.logger))

    author_user_id = None
    for p in lobby.players:
        if p["name"] == user_name:
            author_user_id = p["user_id"]
            break

    await broadcast(lobby, f"👤 <b>{current_player.name}</b>:\n{text}", bot, exclude_id=author_user_id)

    lobby.current_turn_index += 1
    await process_multi_turn(lobby, bot)


# --- ЛОГИКА ГОЛОСОВАНИЯ ---

async def start_multi_voting(lobby: Lobby, bot: Bot):
    lobby.votes.clear()
    title = "ГОЛОСОВАНИЕ"
    if lobby.game_state.phase == "runoff" or lobby.game_state.runoff_candidates:
        title = f"ПЕРЕГОЛОСОВАНИЕ ({' vs '.join(lobby.game_state.runoff_candidates)})"

    if lobby.logger:
        lobby.logger.log_game_event("VOTE_START", title)

    for p in lobby.players:
        game_p_self = next((gp for gp in lobby.game_players if gp.name == p["name"]), None)
        if not game_p_self or not game_p_self.is_alive:
            continue

        valid_targets = []
        if lobby.game_state.runoff_candidates:
            valid_targets = [t for t in lobby.game_players if t.name in lobby.game_state.runoff_candidates]
        else:
            valid_targets = [t for t in lobby.game_players if t.is_alive]

        if p["user_id"] < 0:
            candidates = []
            for target in valid_targets:
                if target.name == p["name"] and not cfg.gameplay["voting"]["allow_self_vote"]:
                    continue
                candidates.append(target.name)
            cand_str = " | ".join(candidates)
            debug_msg = f"🗳 <b>[DEBUG {p['name']}] {title}!</b>\nВарианты: {cand_str}"
            try:
                await bot.send_message(p["chat_id"], debug_msg, parse_mode="HTML")
            except:
                pass
            continue

        kb = InlineKeyboardBuilder()
        for target in valid_targets:
            if target.name == p["name"] and not cfg.gameplay["voting"]["allow_self_vote"]:
                continue
            kb.add(InlineKeyboardButton(text=f"☠ {target.name}", callback_data=f"mvote_{target.name}"))

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
            vote_target = await bot_engine.make_vote(p, bot_targets, gs)
            await handle_vote(lobby, bot, p.name, vote_target)


async def handle_vote(lobby: Lobby, bot: Bot, voter_name: str, target_name: str):
    lobby.votes[voter_name] = target_name
    if lobby.logger:
        lobby.logger.log_game_event("VOTE", f"{voter_name} -> {target_name}")

    alive_players = [p for p in lobby.game_players if p.is_alive]
    if len(lobby.votes) >= len(alive_players):
        await finish_voting(lobby, bot)


async def finish_voting(lobby: Lobby, bot: Bot):
    counts = Counter(lobby.votes.values())
    results = counts.most_common()
    if not results: return

    leader_name, leader_votes = results[0]
    leaders = [name for name, count in results if count == leader_votes]

    result_text = "📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n"
    for name, cnt in counts.items():
        result_text += f"- {name}: {cnt}\n"

    if lobby.logger:
        lobby.logger.log_game_event("VOTE_RESULTS", str(dict(counts)))

    gs = lobby.game_state

    # --- НИЧЬЯ ---
    if len(leaders) > 1:
        if gs.runoff_count >= 1:
            await broadcast(lobby, f"{result_text}\n⚖️ <b>НИЧЬЯ №2!</b>\n🚫 <b>БУНКЕР ЗАКРЫЛСЯ.</b>\n💀 <b>GAME OVER</b>",
                            bot)
            await close_multi_lobby(lobby)
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

    # --- ПОБЕДИТЕЛЬ ---
    await broadcast(lobby, f"{result_text}\n🚪 <b>{leader_name}</b> изгнан.", bot)

    for p in lobby.game_players:
        if p.name == leader_name:
            p.is_alive = False
            break

    leader_user = next((p for p in lobby.players if p["name"] == leader_name), None)
    if leader_user:
        try:
            msg = "💀 <b>GAME OVER</b>. Вас изгнали."
            if leader_user["user_id"] < 0: msg = f"<b>[DEBUG {leader_name}]</b> {msg}"
            await bot.send_message(leader_user["chat_id"], msg, parse_mode="HTML")
            lobby.remove_player(leader_user["user_id"])
        except:
            pass

    humans_alive = any(p.is_human and p.is_alive for p in lobby.game_players)
    if not humans_alive:
        await broadcast(lobby, "💀 <b>GAME OVER</b>. Все люди погибли.", bot)
        await close_multi_lobby(lobby)
        return

    survivors_count = sum(1 for p in lobby.game_players if p.is_alive)
    if survivors_count <= cfg.gameplay["rounds"]["target_survivors"]:
        names = ", ".join([p.name for p in lobby.game_players if p.is_alive])
        await broadcast(lobby, f"🎉 <b>ПОБЕДА!</b> Выжили: {names}", bot)
        await close_multi_lobby(lobby)
        return

    gs.round += 1
    gs.phase = "presentation"
    lobby.current_turn_index = 0
    lobby.votes.clear()
    gs.runoff_candidates = []
    gs.runoff_count = 0

    base_topic = get_topic_base(gs.round, "...", lobby.catastrophe_data)
    gs.topic = base_topic

    await asyncio.sleep(3)
    await broadcast(lobby, f"🔔 <b>РАУНД {gs.round}</b>\nТема: {base_topic}", bot)
    await process_multi_turn(lobby, bot)


async def close_multi_lobby(lobby: Lobby):
    """Тихо завершает игру и выгружает логи"""
    lobby.status = "finished"
    if lobby.logger:
        try:
            path = lobby.logger.get_session_path()
            await asyncio.to_thread(s3_uploader.upload_session_folder, path)
            shutil.rmtree(path)
        except:
            pass