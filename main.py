import asyncio
import logging
import os
import sys
import socket
import random
import shutil
import aiohttp  # <--- Для пинга самого себя
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

# Импорты проекта
from src.proxy_manager import ProxyManager
from src.config import cfg
from src.utils import GameSetup
from src.schemas import GameState, PlayerProfile
from src.services.bot import BotEngine
from src.services.judge import JudgeService
from src.services.director import DirectorEngine
from src.logger_service import game_logger

load_dotenv(os.path.join("Configs", ".env"))

# --- DNS FIX ---
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
        print("🔧 DNS Patch applied")
    except ImportError:
        print("⚠️ dnspython not found")
else:
    print("✅ DNS Patch disabled")

# Инициализация
bot_engine = BotEngine()
judge_service = JudgeService()
director_engine = DirectorEngine()
bot: Bot = None
dp = Dispatcher()
router = Router()
dp.include_router(router)


class GameFSM(StatesGroup):
    Lobby = State()
    GameLoop = State()
    HumanTurn = State()
    Voting = State()


# --- WEB & KEEP-ALIVE ---
async def health_check(request):
    return web.Response(text="Bunker Bot is alive")


async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 Dummy server listening on port {port}")

    # Запускаем пинг самого себя, чтобы Koyeb не уснул
    asyncio.create_task(keep_alive_task(port))


async def keep_alive_task(port):
    """Пингует локальный сервер каждые 5 минут, эмулируя активность."""
    url = f"http://127.0.0.1:{port}/"
    print(f"⏰ Keep-Alive task started for {url}")
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(300)  # 5 минут
            try:
                async with session.get(url) as resp:
                    await resp.text()
                # print("⏰ Self-ping success") # Можно раскомментить для дебага
            except Exception as e:
                print(f"⚠️ Self-ping failed: {e}")


# --- ИГРА ---
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
        return "ВЫЖИВАНИЕ. Докажи свою пользу."


def get_display_topic(gs: GameState, player_trait: str = "", catastrophe_data: dict = None) -> str:
    if gs.phase == "presentation":
        return get_topic_for_round_base(gs.round, player_trait, catastrophe_data)
    elif gs.phase == "discussion":
        return "ОБСУЖДЕНИЕ. Кто лишний?"
    elif gs.phase == "runoff":
        candidates_str = ", ".join(gs.runoff_candidates)
        return f"ПЕРЕСТРЕЛКА. {candidates_str} на грани вылета."
    return "..."


# --- HANDLERS ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="☢️ НАЧАТЬ ИГРУ", callback_data="start_game"))
    await message.answer("<b>BUNKER 3.0</b>", reply_markup=kb.as_markup(), parse_mode="HTML")
    await state.set_state(GameFSM.Lobby)


@router.message(Command("logs"))
async def cmd_get_logs(message: Message):
    """Архивирует и шлет логи (вызывается также автоматически в конце игры)."""
    logs_dir = "Logs"
    if not os.path.exists(logs_dir):
        await message.answer("📂 Папка с логами пуста.")
        return

    try:
        subdirs = [os.path.join(logs_dir, d) for d in os.listdir(logs_dir) if os.path.isdir(os.path.join(logs_dir, d))]
        if not subdirs:
            await message.answer("📂 Нет активных сессий.")
            return

        latest_session = max(subdirs, key=os.path.getmtime)
        session_name = os.path.basename(latest_session)

        # await message.answer(f"📦 Собираю логи: {session_name}...")

        shutil.make_archive(session_name, 'zip', latest_session)
        logfile = FSInputFile(f"{session_name}.zip")
        await message.answer_document(logfile, caption=f"🗂 Логи сессии: {session_name}")
        os.remove(f"{session_name}.zip")

    except Exception as e:
        print(f"Log Error: {e}")
        await message.answer(f"❌ Не удалось собрать логи: {e}")


@router.callback_query(F.data == "start_game")
async def start_game_handler(callback: CallbackQuery, state: FSMContext):
    user_name = callback.from_user.first_name
    game_logger.new_session(user_name)
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
        role = p.profession if p.is_human else "???"
        intro += f"- {p.name}: {role}\n"
    await callback.message.edit_text(intro, parse_mode="HTML")
    await start_round(callback.message.chat.id, state)


async def start_round(chat_id: int, state: FSMContext):
    data = await state.get_data()
    gs = GameState(**data["game_state"])
    gs.phase = "presentation"
    base_topic = get_topic_for_round_base(gs.round, trait="...", catastrophe_data=data.get("catastrophe"))
    gs.topic = base_topic
    msg = f"🔔 <b>РАУНД {gs.round}</b>\nТема: {base_topic}\n\n🗣 <b>ФАЗА 1: ПРЕЗЕНТАЦИЯ</b>"
    await bot.send_message(chat_id, msg, parse_mode="HTML")
    await state.update_data(game_state=gs.model_dump(), current_turn_index=0)
    await process_turn(chat_id, state)


async def process_turn(chat_id: int, state: FSMContext):
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]
    gs = GameState(**data["game_state"])
    idx = data["current_turn_index"]
    cat_data = data.get("catastrophe", {})

    if gs.phase == "runoff":
        active_players_list = [p for p in players if p.name in gs.runoff_candidates]
    else:
        active_players_list = players

    if idx >= len(active_players_list):
        if gs.phase == "presentation":
            gs.phase = "discussion"
            await state.update_data(game_state=gs.model_dump(), current_turn_index=0)
            disc_topic = get_display_topic(gs)
            await bot.send_message(chat_id, f"⚔️ <b>ФАЗА 2: ОБСУЖДЕНИЕ</b>\n{disc_topic}", parse_mode="HTML")
            await asyncio.sleep(1)
            await process_turn(chat_id, state)
            return
        elif gs.phase == "discussion":
            gs.phase = "voting"
            await state.update_data(game_state=gs.model_dump())
            await start_voting(chat_id, state)
            return
        elif gs.phase == "runoff":
            gs.phase = "voting"
            await state.update_data(game_state=gs.model_dump())
            await start_voting(chat_id, state)
            return

    current_player = active_players_list[idx]
    actual_topic = get_display_topic(gs, player_trait=current_player.trait, catastrophe_data=cat_data)
    temp_gs = gs.model_copy()
    temp_gs.topic = actual_topic

    if current_player.is_human:
        await bot.send_message(chat_id, f"👤 <b>Твой ход</b>:\n{actual_topic}", parse_mode="HTML")
        await state.update_data(game_state=gs.model_dump())
        await state.set_state(GameFSM.HumanTurn)
        return
    else:
        await bot.send_chat_action(chat_id, "typing")
        instr = await director_engine.get_hidden_instruction(current_player, players, temp_gs)
        speech = await bot_engine.make_turn(current_player, players, temp_gs, director_instruction=instr)
        await bot.send_message(chat_id, f"🤖 <b>{current_player.name}</b>:\n{speech}", parse_mode="HTML")
        verdict = await judge_service.analyze_move(current_player, speech, actual_topic)
        current_player.suspicion_score = verdict["total_suspicion"]

        thresholds = cfg.gameplay["judge"]["status_thresholds"]
        if current_player.suspicion_score >= thresholds["impostor"]:
            current_player.status = "IMPOSTOR"
        elif current_player.suspicion_score >= thresholds["liar"]:
            current_player.status = "LIAR"
        else:
            current_player.status = "NORMAL"

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

    if gs.phase == "runoff":
        active_list = [p for p in players if p.name in gs.runoff_candidates]
    else:
        active_list = players

    player = active_list[data["current_turn_index"]]
    actual_topic = get_display_topic(gs, player_trait=player.trait, catastrophe_data=cat_data)

    verdict = await judge_service.analyze_move(player, message.text, actual_topic)
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
    title = "ГОЛОСОВАНИЕ"
    if gs.runoff_candidates:
        targets = [p for p in players if p.name in gs.runoff_candidates]
        title = f"ПЕРЕГОЛОСОВАНИЕ ({' vs '.join(gs.runoff_candidates)})"
    kb = InlineKeyboardBuilder()
    for p in targets:
        if not p.is_human or cfg.gameplay["voting"]["allow_self_vote"]:
            kb.add(InlineKeyboardButton(text=f"☠ {p.name}", callback_data=f"vote_{p.name}"))
    kb.adjust(1)
    await bot.send_message(chat_id, f"🛑 <b>{title}</b>", reply_markup=kb.as_markup(), parse_mode="HTML")
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
    for bot_p in players:
        if not bot_p.is_human:
            vote = await bot_engine.make_vote(bot_p, valid_targets, gs)
            votes.append(vote)

    counts = Counter(votes)
    results = counts.most_common()
    leader_name, leader_votes = results[0]
    leaders = [name for name, count in results if count == leader_votes]
    result_text = f"📊 <b>ИТОГИ:</b> {dict(counts)}\n"

    if len(leaders) > 1:
        if gs.runoff_count >= cfg.gameplay["voting"]["max_runoffs"]:
            loser_name = random.choice(leaders)
            result_text += f"⚖️ Снова ничья. Жребий выбрал: <b>{loser_name}</b>"
            await callback.message.answer(result_text, parse_mode="HTML")
            await eliminate_player(loser_name, chat_id, state)
            return
        gs.phase = "runoff"
        gs.runoff_candidates = leaders
        gs.runoff_count += 1
        await state.update_data(game_state=gs.model_dump(), current_turn_index=0)
        await callback.message.answer(f"{result_text}\n⚖️ <b>НИЧЬЯ!</b> Перестрелка.", parse_mode="HTML")
        await process_turn(chat_id, state)
        return

    await callback.message.answer(f"{result_text}🚪 <b>{leader_name}</b> изгнан.", parse_mode="HTML")
    await eliminate_player(leader_name, chat_id, state)


async def eliminate_player(loser_name: str, chat_id: int, state: FSMContext):
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]
    survivors = [p for p in players if p.name != loser_name]

    # --- АВТОМАТИЧЕСКАЯ ОТПРАВКА ЛОГОВ ПОСЛЕ ИГРЫ (для Free Tier без Volumes) ---
    async def send_logs_auto():
        # Создаем фейковое сообщение, чтобы переиспользовать код cmd_get_logs
        # (Это немного хак, но рабочий и быстрый)
        dummy_msg = Message(message_id=0, date=datetime.datetime.now(), chat=bot.get_chat(chat_id=chat_id))
        # Присваиваем боту и чату правильные атрибуты вручную, если нужно,
        # но проще просто скопировать логику отправки:
        try:
            logs_dir = "Logs"
            if os.path.exists(logs_dir):
                subdirs = [os.path.join(logs_dir, d) for d in os.listdir(logs_dir) if
                           os.path.isdir(os.path.join(logs_dir, d))]
                if subdirs:
                    latest = max(subdirs, key=os.path.getmtime)
                    name = os.path.basename(latest)
                    shutil.make_archive(name, 'zip', latest)
                    await bot.send_document(chat_id, FSInputFile(f"{name}.zip"),
                                            caption=f"🏁 Игра окончена. Логи: {name}")
                    os.remove(f"{name}.zip")
        except Exception as e:
            print(f"Auto-log error: {e}")

    # --------------------------------------------------------------------------

    if not any(p.is_human for p in survivors):
        await bot.send_message(chat_id, "💀 <b>GAME OVER</b>. Вы погибли.", parse_mode="HTML")
        await send_logs_auto()  # Шлем логи
        await state.clear()
        return

    if len(survivors) <= cfg.gameplay["rounds"]["target_survivors"]:
        names = ", ".join([p.name for p in survivors])
        await bot.send_message(chat_id, f"🎉 <b>ПОБЕДА!</b> Выжили: {names}", parse_mode="HTML")
        await send_logs_auto()  # Шлем логи
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


# --- ЗАПУСК ---
async def main():
    await start_dummy_server()
    global bot

    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN is missing")
        return

    enable_proxy = os.getenv("ENABLE_PROXY", "false").lower() == "true"
    proxy_manager = None
    if enable_proxy:
        print("🚀 Proxy Mode: ENABLED.")
        proxy_manager = ProxyManager("proxies.txt")
    else:
        print("🚀 Proxy Mode: DISABLED.")

    print("🚀 Starting Bot Loop...")

    while True:
        session = None
        current_proxy = None
        if enable_proxy and proxy_manager:
            current_proxy = proxy_manager.get_next_proxy()
            if current_proxy:
                session = AiohttpSession(proxy=current_proxy)
            else:
                session = AiohttpSession()
        else:
            session = AiohttpSession()

        bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))

        try:
            print("Trying to start polling...")
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        except (TelegramNetworkError, OSError, asyncio.TimeoutError) as e:
            print(f"🔥 NETWORK ERROR: {e}")
            if not enable_proxy: await asyncio.sleep(5)
        except Exception as e:
            print(f"❌ CRITICAL ERROR: {e}")
            await asyncio.sleep(5)
        finally:
            if bot and bot.session: await bot.session.close()


if __name__ == "__main__":
    import datetime  # Нужен для хака с датой в авто-логах

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped!")