import asyncio
import logging
import os
import sys
import socket
import random
import shutil
import aiohttp
from collections import Counter
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
from aiohttp import web
from aiogram.exceptions import TelegramNetworkError

# Импорты
from src.proxy_manager import ProxyManager
from src.config import cfg
from src.utils import GameSetup
from src.schemas import GameState, PlayerProfile
from src.services.bot import BotEngine
from src.services.judge import JudgeService
from src.services.director import DirectorEngine
from src.logger_service import GameLogger  # <-- CLASS IMPORT
from src.s3_service import s3_uploader
from src.lobbies import lobby_manager, Lobby
from src.multi_engine import process_multi_turn, handle_human_message, broadcast, handle_vote

load_dotenv(os.path.join("Configs", ".env"))

# DNS FIX
if os.getenv("ENABLE_DNS_FIX", "false").lower() == "true":
    try:
        import dns.resolver

        original_getaddrinfo = socket.getaddrinfo


        def global_dns_patch(host, port, family=0, type=0, proto=0, flags=0):
            try:
                if host in ["localhost", "127.0.0.1", "0.0.0.0"]:
                    return original_getaddrinfo(host, port, family, type, proto, flags)
            except:
                pass
            try:
                resolver = dns.resolver.Resolver()
                resolver.nameservers = ['8.8.8.8', '8.8.4.4']
                answer = resolver.resolve(host, 'A')
                ip_list = [r.to_text() for r in answer]
                selected_ip = random.choice(ip_list)
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (selected_ip, port))]
            except:
                return original_getaddrinfo(host, port, family, type, proto, flags)


        socket.getaddrinfo = global_dns_patch
    except:
        pass

bot_engine = BotEngine()
judge_service = JudgeService()
director_engine = DirectorEngine()
bot: Bot = None
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Хранилище логгеров для SOLO режима: chat_id -> GameLogger
solo_sessions = {}


class GameFSM(StatesGroup):
    Lobby = State()
    GameLoop = State()
    HumanTurn = State()
    Voting = State()
    MultiMenu = State()
    MultiLobby = State()
    MultiGame = State()


async def health_check(request): return web.Response(text="Bunker Bot is alive")


async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    asyncio.create_task(keep_alive_task(port))


async def keep_alive_task(port):
    public_url = os.getenv("APP_PUBLIC_URL")
    url = public_url if public_url else f"http://127.0.0.1:{port}/"
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(300)
            try:
                async with session.get(url) as resp:
                    await resp.text()
            except:
                pass


# --- Helper Functions ---
def get_topic_for_round_base(round_num: int, trait: str = "", catastrophe_data: dict = None) -> str:
    topics_cfg = cfg.gameplay["rounds"]["topics"]
    if round_num == 1:
        return topics_cfg[1]
    elif round_num == 2:
        return topics_cfg[2].format(trait=trait)
    else:
        if catastrophe_data and "topics" in catastrophe_data:
            idx = (round_num - 3) % len(catastrophe_data["topics"])
            return topics_cfg[3].format(catastrophe_problem=catastrophe_data["topics"][idx])
        return "ВЫЖИВАНИЕ."


def get_display_topic(gs: GameState, player_trait: str = "", catastrophe_data: dict = None) -> str:
    if gs.phase == "presentation":
        return get_topic_for_round_base(gs.round, player_trait, catastrophe_data)
    elif gs.phase == "discussion":
        return "ОБСУЖДЕНИЕ. Кто лишний?"
    elif gs.phase == "runoff":
        return f"ПЕРЕСТРЕЛКА. {', '.join(gs.runoff_candidates)} на грани."
    return "..."


# ================= MENU =================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="👤 SOLO GAME", callback_data="mode_solo"))
    kb.add(InlineKeyboardButton(text="👥 MULTIPLAYER", callback_data="mode_multi"))
    await message.answer("<b>BUNKER 3.0</b>\nВыберите режим:", reply_markup=kb.as_markup(), parse_mode="HTML")
    await state.clear()


# ================= SOLO MODE =================
@router.callback_query(F.data == "mode_solo")
async def solo_mode_entry(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="☢️ НАЧАТЬ ИГРУ", callback_data="start_game"))
    await callback.message.edit_text("<b>👤 SOLO MODE</b>\nВы будете играть с 4 ботами.", reply_markup=kb.as_markup(),
                                     parse_mode="HTML")
    await state.set_state(GameFSM.Lobby)


@router.callback_query(F.data == "start_game")
async def start_game_handler(callback: CallbackQuery, state: FSMContext):
    user_name = callback.from_user.first_name
    chat_id = callback.message.chat.id

    # Создаем персональный логгер для соло
    logger = GameLogger("Solo", user_name)
    solo_sessions[chat_id] = logger

    players = GameSetup.generate_players(user_name)
    game_state = GameSetup.init_game_state()
    game_state.topic = get_topic_for_round_base(1)

    current_catastrophe = cfg.scenarios["catastrophes"][0]
    for cat in cfg.scenarios["catastrophes"]:
        if cat["name"] in game_state.topic:
            current_catastrophe = cat
            break

    await state.update_data(
        players=[p.model_dump() for p in players],
        game_state=game_state.model_dump(),
        catastrophe=current_catastrophe,
        current_turn_index=0
    )

    intro = f"🌍 <b>СЦЕНАРИЙ:</b> {current_catastrophe['name']}\n\n👥 <b>ИГРОКИ:</b>\n"
    for p in players:
        intro += f"- {GameSetup.get_display_name(p, 1)}\n"

    await callback.message.edit_text(intro, parse_mode="HTML")
    await start_round(chat_id, state)


async def start_round(chat_id: int, state: FSMContext):
    data = await state.get_data()
    gs = GameState(**data["game_state"])
    gs.phase = "presentation"
    base_topic = get_topic_for_round_base(gs.round, trait="...", catastrophe_data=data.get("catastrophe"))
    gs.topic = base_topic

    players = [PlayerProfile(**p) for p in data["players"]]
    active_list_str = "\n".join([f"- {GameSetup.get_display_name(p, gs.round)}" for p in players if p.is_alive])

    await bot.send_message(chat_id, f"🔔 <b>РАУНД {gs.round}</b>\nТема: {base_topic}\n\n{active_list_str}",
                           parse_mode="HTML")
    await state.update_data(game_state=gs.model_dump(), current_turn_index=0)
    await process_turn(chat_id, state)


async def process_turn(chat_id: int, state: FSMContext):
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]
    gs = GameState(**data["game_state"])
    idx = data["current_turn_index"]
    cat_data = data.get("catastrophe", {})

    logger = solo_sessions.get(chat_id)

    if gs.phase == "runoff":
        active_list = [p for p in players if p.name in gs.runoff_candidates]
    else:
        active_list = players

    if idx >= len(active_list):
        if gs.phase == "presentation":
            gs.phase = "discussion"
            await state.update_data(game_state=gs.model_dump(), current_turn_index=0)
            await bot.send_message(chat_id, f"⚔️ <b>ФАЗА 2: ОБСУЖДЕНИЕ</b>\n{get_display_topic(gs)}", parse_mode="HTML")
            await asyncio.sleep(1)
            await process_turn(chat_id, state)
            return
        elif gs.phase in ["discussion", "runoff"]:
            gs.phase = "voting"
            await state.update_data(game_state=gs.model_dump())
            await start_voting(chat_id, state)
            return

    current_player = active_list[idx]
    actual_topic = get_display_topic(gs, current_player.trait, cat_data)

    if current_player.is_human:
        await bot.send_message(chat_id, f"👤 <b>Твой ход</b>:\n{actual_topic}", parse_mode="HTML")
        await state.update_data(game_state=gs.model_dump())
        await state.set_state(GameFSM.HumanTurn)
        return
    else:
        await bot.send_chat_action(chat_id, "typing")
        instr = await director_engine.get_hidden_instruction(current_player, players, gs, logger=logger)
        speech = await bot_engine.make_turn(current_player, players, gs, director_instruction=instr, logger=logger)

        if logger: logger.log_chat_message(current_player.name, speech)

        await bot.send_message(chat_id, f"🤖 {GameSetup.get_display_name(current_player, gs.round)}:\n{speech}",
                               parse_mode="HTML")

        verdict = await judge_service.analyze_move(current_player, speech, actual_topic, logger=logger)
        current_player.suspicion_score = verdict["total_suspicion"]

        gs.history.append(f"[{current_player.name}]: {speech}")
        data["players"] = [p.model_dump() for p in players]
        data["game_state"] = gs.model_dump()
        data["current_turn_index"] += 1
        await state.update_data(data)
        await asyncio.sleep(1.5)
        await process_turn(chat_id, state)


@router.message(GameFSM.HumanTurn)
async def human_turn_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]
    gs = GameState(**data["game_state"])
    cat_data = data.get("catastrophe", {})
    logger = solo_sessions.get(message.chat.id)

    if gs.phase == "runoff":
        active_list = [p for p in players if p.name in gs.runoff_candidates]
    else:
        active_list = players

    player = active_list[data["current_turn_index"]]
    actual_topic = get_display_topic(gs, player.trait, cat_data)

    if logger: logger.log_chat_message(player.name, message.text)

    verdict = await judge_service.analyze_move(player, message.text, actual_topic, logger=logger)
    player.suspicion_score = verdict["total_suspicion"]

    gs.history.append(f"[{player.name}]: {message.text}")
    for i, p in enumerate(players):
        if p.name == player.name:
            players[i] = player
            break

    data["players"] = [p.model_dump() for p in players]
    data["game_state"] = gs.model_dump()
    data["current_turn_index"] += 1
    await state.update_data(data)
    await state.set_state(GameFSM.GameLoop)
    await process_turn(message.chat.id, state)


async def start_voting(chat_id: int, state: FSMContext):
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]
    gs = GameState(**data["game_state"])
    targets = players
    if gs.runoff_candidates:
        targets = [p for p in players if p.name in gs.runoff_candidates]

    kb = InlineKeyboardBuilder()
    for p in targets:
        if not p.is_human or cfg.gameplay["voting"]["allow_self_vote"]:
            kb.add(InlineKeyboardButton(text=f"☠ {p.name}", callback_data=f"vote_{p.name}"))
    kb.adjust(1)
    await bot.send_message(chat_id, f"🛑 <b>ГОЛОСОВАНИЕ</b>", reply_markup=kb.as_markup(), parse_mode="HTML")
    await state.set_state(GameFSM.Voting)


@router.callback_query(GameFSM.Voting, F.data.startswith("vote_"))
async def voting_handler(callback: CallbackQuery, state: FSMContext):
    target_name = callback.data.split("_")[1]
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]
    gs = GameState(**data["game_state"])
    chat_id = callback.message.chat.id
    await callback.message.edit_reply_markup(reply_markup=None)

    if gs.runoff_candidates:
        valid_targets = [p for p in players if p.name in gs.runoff_candidates]
    else:
        valid_targets = players

    votes = [target_name]

    logger = solo_sessions.get(chat_id)

    for bot_p in players:
        if not bot_p.is_human:
            vote = await bot_engine.make_vote(bot_p, valid_targets, gs, logger=logger)
            votes.append(vote)

    counts = Counter(votes)
    results = counts.most_common()
    leader_name, leader_votes = results[0]
    leaders = [name for name, count in results if count == leader_votes]

    result_text = f"📊 <b>ИТОГИ:</b>\n"
    for name, cnt in counts.items():
        result_text += f"- {name}: {cnt}\n"

    # НИЧЬЯ
    if len(leaders) > 1:
        if gs.runoff_count >= 1:
            result_text += f"\n⚖️ <b>СНОВА НИЧЬЯ!</b>\n🚫 <b>БУНКЕР ЗАКРЫЛСЯ.</b>\n💀 <b>GAME OVER</b>"
            await callback.message.answer(result_text, parse_mode="HTML")
            await eliminate_player("EVERYONE_DIED", chat_id, state)
            return

        gs.phase = "runoff"
        gs.runoff_candidates = leaders
        gs.runoff_count += 1
        await state.update_data(game_state=gs.model_dump(), current_turn_index=0)
        await callback.message.answer(f"{result_text}\n⚖️ <b>НИЧЬЯ!</b> Перестрелка.", parse_mode="HTML")
        await process_turn(chat_id, state)
        return

    await callback.message.answer(f"{result_text}\n🚪 <b>{leader_name}</b> изгнан.", parse_mode="HTML")
    await eliminate_player(leader_name, chat_id, state)


async def eliminate_player(loser_name: str, chat_id: int, state: FSMContext):
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]

    # ТИХАЯ ОТПРАВКА ЛОГОВ (SOLO)
    async def finish_solo_session():
        logger = solo_sessions.get(chat_id)
        if logger:
            try:
                path = logger.get_session_path()
                await asyncio.to_thread(s3_uploader.upload_session_folder, path)
                shutil.rmtree(path)
            except:
                pass
            del solo_sessions[chat_id]

    if loser_name == "EVERYONE_DIED":
        await finish_solo_session()
        await state.clear()
        return

    survivors = [p for p in players if p.name != loser_name]

    if not any(p.is_human for p in survivors):
        await bot.send_message(chat_id, "💀 <b>GAME OVER</b>. Вы погибли.", parse_mode="HTML")
        await finish_solo_session()
        await state.clear()
        return

    if len(survivors) <= cfg.gameplay["rounds"]["target_survivors"]:
        names = ", ".join([p.name for p in survivors])
        await bot.send_message(chat_id, f"🎉 <b>ПОБЕДА!</b> Выжили: {names}", parse_mode="HTML")
        await finish_solo_session()
        await state.clear()
        return

    gs = GameState(**data["game_state"])
    gs.runoff_candidates = []
    gs.runoff_count = 0
    gs.round += 1
    gs.topic = get_topic_for_round_base(gs.round, trait="...", catastrophe_data=data.get("catastrophe"))

    await state.update_data(players=[p.model_dump() for p in survivors], game_state=gs.model_dump())
    await asyncio.sleep(2)
    await start_round(chat_id, state)


# ================= MULTIPLAYER HANDLERS =================

@router.callback_query(F.data == "mode_multi")
async def multi_mode_entry(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="🆕 Создать комнату", callback_data="lobby_create"))
    kb.add(InlineKeyboardButton(text="🔍 Найти комнату", callback_data="lobby_list"))
    kb.add(InlineKeyboardButton(text="🔙 Назад", callback_data="mode_back_to_start"))
    kb.adjust(1)
    await callback.message.edit_text("<b>👥 MULTIPLAYER MENU</b>\nВыберите действие:", reply_markup=kb.as_markup(),
                                     parse_mode="HTML")
    await state.set_state(GameFSM.MultiMenu)


@router.callback_query(F.data == "mode_back_to_start")
async def back_to_start(callback: CallbackQuery, state: FSMContext):
    await cmd_start(callback.message, state)


@router.callback_query(F.data == "lobby_create")
async def create_lobby_handler(callback: CallbackQuery, state: FSMContext):
    user = callback.from_user
    lobby = lobby_manager.create_lobby(user.id, user.first_name)
    lobby.menu_message_id = callback.message.message_id
    await update_lobby_message(bot, lobby)
    await state.set_state(GameFSM.MultiLobby)


@router.callback_query(F.data == "lobby_list")
async def list_lobbies_handler(callback: CallbackQuery, state: FSMContext):
    lobbies = lobby_manager.get_all_waiting()
    kb = InlineKeyboardBuilder()
    total_needed = cfg.gameplay.get("setup", {}).get("total_players", 5)

    if not lobbies:
        kb.add(InlineKeyboardButton(text="Нет активных комнат 🥺", callback_data="none"))

    for l in lobbies:
        btn_text = f"Комната {l.lobby_id} ({len(l.players)}/{total_needed})"
        kb.add(InlineKeyboardButton(text=btn_text, callback_data=f"join_{l.lobby_id}"))

    kb.add(InlineKeyboardButton(text="🔙 Назад", callback_data="mode_multi"))
    kb.adjust(1)
    await callback.message.edit_text("<b>Список доступных комнат:</b>", reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("join_"))
async def join_lobby_handler(callback: CallbackQuery, state: FSMContext):
    lobby_id = callback.data.split("_")[1]
    lobby = lobby_manager.get_lobby(lobby_id)
    if not lobby:
        await callback.answer("Комната не найдена.", show_alert=True)
        return

    user = callback.from_user
    lobby.add_player(user.id, callback.message.chat.id, user.first_name)

    if user.id == lobby.host_id:
        lobby.menu_message_id = callback.message.message_id

    await update_lobby_message(bot, lobby)
    await state.set_state(GameFSM.MultiLobby)


async def update_lobby_message(bot: Bot, lobby: Lobby):
    if not lobby.menu_message_id: return
    total_needed = cfg.gameplay.get("setup", {}).get("total_players", 5)

    players_list = ""
    for p in lobby.players:
        mark = " ⭐" if p["user_id"] == lobby.host_id else ""
        players_list += f"- {p['name']}{mark}\n"

    text = (
        f"🚪 <b>LOBBY {lobby.lobby_id}</b>\nIгроков: {len(lobby.players)} / {total_needed}\n<b>Список:</b>\n{players_list}")

    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="🚀 START GAME", callback_data=f"start_multi_{lobby.lobby_id}"))
    kb.add(InlineKeyboardButton(text="🔙 Выйти", callback_data=f"leave_lobby_{lobby.lobby_id}"))

    try:
        await bot.edit_message_text(text=text, chat_id=lobby.host_id, message_id=lobby.menu_message_id,
                                    reply_markup=kb.as_markup(), parse_mode="HTML")
    except:
        pass


@router.callback_query(F.data.startswith("leave_lobby"))
async def leave_lobby_handler(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    lobby = None
    if len(parts) > 2:
        lobby = lobby_manager.get_lobby(parts[2])
    else:
        lobby = lobby_manager.find_lobby_by_user(callback.from_user.id)

    if not lobby:
        await callback.message.delete()
        await multi_mode_entry(callback, state)
        return

    user_id = callback.from_user.id
    if user_id == lobby.host_id:
        lobby_manager.delete_lobby(lobby.lobby_id)
        for p in lobby.players:
            try:
                await bot.send_message(p["chat_id"], "🚫 Лидер закрыл лобби.")
            except:
                pass
    else:
        lobby.remove_player(user_id)
        await callback.answer("Вышли.")
        await callback.message.delete()
        await update_lobby_message(bot, lobby)

    await state.set_state(GameFSM.MultiMenu)
    await multi_mode_entry(callback, state)


@router.callback_query(F.data.startswith("start_multi_"))
async def start_multi_handler(callback: CallbackQuery, state: FSMContext):
    lobby_id = callback.data.split("_")[2]
    lobby = lobby_manager.get_lobby(lobby_id)
    if not lobby or lobby.host_id != callback.from_user.id: return

    lobby.status = "playing"

    # Инициализация ЛОГГЕРА
    host_name = lobby.players[0]['name']
    lobby.logger = GameLogger("Multiplayer", host_name)

    humans_data = [{"name": p["name"], "id": p["user_id"]} for p in lobby.players]
    lobby.game_players = GameSetup.generate_players(humans_data)
    lobby.game_state = GameSetup.init_game_state()
    lobby.game_state.topic = get_topic_for_round_base(1)

    intro = f"🎬 <b>ИГРА НАЧАЛАСЬ!</b>\n\n"
    for p in lobby.game_players:
        # Показываем профессию ВСЕМ
        intro += f"- {p.name}: {p.profession}\n"

    await broadcast(lobby, intro, bot)
    await asyncio.sleep(2)
    await process_multi_turn(lobby, bot)


@router.message(Command("fake_join"))
async def cmd_fake_join(message: Message):
    lobby = lobby_manager.find_lobby_by_user(message.from_user.id)
    if not lobby or lobby.status != "waiting": return
    fake_id = -random.randint(1000, 99999)
    fake_name = f"Fake_{random.choice(['Bob', 'Alice', 'John'])}"
    lobby.add_player(fake_id, message.chat.id, fake_name)
    try:
        await message.delete()
    except:
        pass
    await update_lobby_message(bot, lobby)


@router.message(Command("fake_say"))
async def cmd_fake_say(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2: return
    text = args[1]
    lobby = lobby_manager.find_lobby_by_user(message.from_user.id)
    if not lobby or lobby.status != "playing": return
    if lobby.current_turn_index >= len(lobby.game_players): return
    current_player = lobby.game_players[lobby.current_turn_index]
    player_data = next((p for p in lobby.players if p["name"] == current_player.name), None)
    if player_data and player_data["user_id"] < 0:
        await handle_human_message(lobby, bot, text, current_player.name)
        try:
            await message.delete()
        except:
            pass


@router.message(Command("vote_as"))
async def cmd_vote_as(message: Message):
    args = message.text.split()
    if len(args) < 3: return
    voter_name = args[1]
    target_name = args[2]
    lobby = lobby_manager.find_lobby_by_user(message.from_user.id)
    if not lobby or lobby.status != "playing": return
    try:
        await message.delete()
    except:
        pass
    from src.multi_engine import handle_vote
    await handle_vote(lobby, bot, voter_name, target_name)


@router.callback_query(F.data.startswith("mvote_"))
async def multi_vote_handler(callback: CallbackQuery):
    target_name = callback.data.split("_")[1]
    user = callback.from_user
    lobby = lobby_manager.find_lobby_by_user(user.id)
    if not lobby or not lobby.game_state or lobby.game_state.phase != "voting": return
    lobby_p = next((p for p in lobby.players if p["user_id"] == user.id), None)
    if lobby_p:
        game_p = next((p for p in lobby.game_players if p.name == lobby_p["name"]), None)
        if not game_p or not game_p.is_alive:
            await callback.answer("Мертвые не голосуют.")
            return
        from src.multi_engine import handle_vote
        await handle_vote(lobby, bot, lobby_p["name"], target_name)
        await callback.answer(f"Голос за {target_name}")
        await callback.message.edit_text(f"✅ Голос: <b>{target_name}</b>", parse_mode="HTML")


@router.message()
async def global_message_handler(message: Message):
    user = message.from_user
    lobby = lobby_manager.find_lobby_by_user(user.id)
    if lobby and lobby.status == "playing":
        await handle_human_message(lobby, bot, message.text, user.first_name)


async def main():
    await start_dummy_server()
    global bot
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN: return
    enable_proxy = os.getenv("ENABLE_PROXY", "false").lower() == "true"
    proxy_manager = ProxyManager("proxies.txt") if enable_proxy else None

    while True:
        session = None
        if enable_proxy and proxy_manager:
            current = proxy_manager.get_next_proxy()
            session = AiohttpSession(proxy=current) if current else AiohttpSession()
        else:
            session = AiohttpSession()

        bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(5)
        finally:
            if bot and bot.session: await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except:
        pass