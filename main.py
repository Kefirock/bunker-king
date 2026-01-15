import asyncio
import logging
import os
import sys
import random
import time

print("🔍 DEBUG: SERVER STARTUP")

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiohttp import web

from src.core.schemas import GameEvent
from src.core.lobby import lobby_manager, Lobby
from src.core.s3 import s3_uploader

try:
    from src.games.bunker.game import BunkerGame
except ImportError as e:
    print(f"🔥 IMPORT ERROR: {e}")
    sys.exit(1)

load_dotenv(os.path.join("Configs", ".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN: sys.exit("Error: BOT_TOKEN is missing")

raw_admin_id = os.getenv("ADMIN_ID")
ADMIN_ID = int(raw_admin_id) if raw_admin_id else None

bot = Bot(token=BOT_TOKEN, session=AiohttpSession(), default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

active_games = {}
dashboard_map = {}
message_tokens = {}


# === WEB SERVER ===

async def health_check(request): return web.Response(text="Bot is alive")


async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌍 Web server started on port {port}")


async def cleanup_lobbies_task():
    while True:
        await asyncio.sleep(60)
        try:
            now = time.time()
            for lid, lobby in list(lobby_manager.lobbies.items()):
                if lobby.status == "waiting" and (now - lobby.last_activity > 300):
                    logging.info(f"♻️ Cleaning up inactive lobby {lid}")
                    for uid, msg_id in lobby.user_interfaces.items():
                        try:
                            await bot.edit_message_text(
                                chat_id=uid,
                                message_id=msg_id,
                                text="⌛ <b>Время истекло.</b> Лобби закрыто.",
                                reply_markup=None
                            )
                        except:
                            pass
                    lobby_manager.delete_lobby(lid)
        except Exception as e:
            logging.error(f"Cleanup error: {e}")


# === UI HELPERS ===

async def broadcast_lobby_ui(lobby: Lobby):
    from src.games.bunker.config import bunker_cfg
    total_needed = bunker_cfg.gameplay.get("setup", {}).get("total_players", 6)
    current_humans = len(lobby.players)
    bots_will_be_added = max(0, total_needed - current_humans)

    text = (
        f"🚪 <b>ЛОББИ: {lobby.lobby_id}</b>\n"
        f"Режим: ☢️ {lobby.game_type.upper()}\n"
        f"Статус: Ожидание игроков...\n\n"
        f"👥 <b>Состав ({current_humans}/{total_needed}):</b>\n"
        f"{lobby.get_players_list_text()}\n"
        f"<i>...ещё {bots_will_be_added} мест займет ИИ</i>"
    )

    dead_users = []
    for user_id, message_id in lobby.user_interfaces.items():
        if user_id < 0: continue

        kb = InlineKeyboardBuilder()
        if user_id == lobby.host_id:
            kb.add(InlineKeyboardButton(text="🚀 СТАРТ", callback_data=f"lobby_start_{lobby.lobby_id}"))
            kb.add(InlineKeyboardButton(text="🚪 Закрыть лобби", callback_data="lobby_leave"))
            kb.adjust(1)
        else:
            kb.add(InlineKeyboardButton(text="🚪 Выйти", callback_data="lobby_leave"))

        try:
            await bot.edit_message_text(chat_id=user_id, message_id=message_id, text=text, reply_markup=kb.as_markup())
        except TelegramForbiddenError:
            dead_users.append(user_id)
        except Exception:
            pass

    if dead_users:
        for uid in dead_users:
            lobby_manager.leave_lobby(uid)
        if len(lobby.players) > 0:
            asyncio.create_task(broadcast_lobby_ui(lobby))


# === EVENT PROCESSOR (ROUTING FIX) ===

async def process_game_events(context_id: str, events: list[GameEvent]):
    if not events: return
    game = active_games.get(context_id)
    if not game: return

    for event in events:
        try:
            # Typing status только живым людям
            if event.type in ["message", "bot_think"]:
                targets = [p.id for p in game.players if p.is_human]
                for tid in targets:
                    if tid > 0:
                        try:
                            await bot.send_chat_action(tid, "typing")
                        except:
                            pass

            if event.type == "message":
                targets = event.target_ids if event.target_ids else [p.id for p in game.players if p.is_human]
                kb = None
                if event.reply_markup:
                    builder = InlineKeyboardBuilder()
                    for btn in event.reply_markup:
                        builder.add(InlineKeyboardButton(text=btn["text"], callback_data=btn["callback_data"]))
                    builder.adjust(1)
                    kb = builder.as_markup()

                for tid in targets:
                    # 1. Реальный игрок (ID > 0)
                    if tid > 0:
                        sent_msg = await bot.send_message(chat_id=tid, text=event.content, reply_markup=kb)

                        if event.extra_data.get("is_dashboard"):
                            if game.lobby_id not in dashboard_map: dashboard_map[game.lobby_id] = {}
                            dashboard_map[game.lobby_id][tid] = sent_msg.message_id
                            try:
                                await bot.pin_chat_message(chat_id=tid, message_id=sent_msg.message_id)
                            except:
                                pass

                        if event.token:
                            message_tokens[f"{tid}:{event.token}"] = sent_msg.message_id

                    # 2. Фейковый игрок (ID <= -50000) -> Шлем Админу
                    # AI-боты (от -1000 до -5000) игнорируются
                    elif tid <= -50000 and ADMIN_ID:
                        fake_p = next((p for p in game.players if p.id == tid), None)
                        fake_name = fake_p.name if fake_p else f"ID {tid}"
                        debug_text = f"🔧 <b>[To {fake_name}]</b>:\n{event.content}"
                        try:
                            await bot.send_message(chat_id=ADMIN_ID, text=debug_text)
                        except:
                            pass

            elif event.type == "edit_message":
                targets = event.target_ids if event.target_ids else [p.id for p in game.players if p.is_human]
                for tid in targets:
                    # Редактируем только реальным людям
                    if tid > 0:
                        msg_id = message_tokens.get(f"{tid}:{event.token}")
                        if msg_id:
                            try:
                                await bot.edit_message_text(chat_id=tid, message_id=msg_id, text=event.content)
                            except:
                                pass
                        else:
                            await bot.send_message(chat_id=tid, text=event.content)

            elif event.type == "update_dashboard":
                if game.lobby_id in dashboard_map:
                    for uid, msg_id in dashboard_map[game.lobby_id].items():
                        try:
                            await bot.edit_message_text(chat_id=uid, message_id=msg_id, text=event.content)
                        except:
                            pass

            elif event.type == "callback_answer":
                if event.target_ids and event.target_ids[0] > 0:
                    await bot.answer_callback_query(callback_query_id=event.extra_data.get("query_id"),
                                                    text=event.content)

            elif event.type == "game_over":
                targets = [p.id for p in game.players if p.is_human]
                for tid in targets:
                    if tid > 0: await bot.send_message(tid, f"🏁 <b>GAME OVER</b>\n{event.content}")

                if hasattr(game, "logger") and game.logger:
                    path = game.logger.get_session_path()
                    asyncio.create_task(asyncio.to_thread(s3_uploader.upload_session_folder, path))

                if game.lobby_id in active_games: del active_games[game.lobby_id]
                if game.lobby_id in dashboard_map: del dashboard_map[game.lobby_id]
                lobby_manager.delete_lobby(game.lobby_id)
                return

            elif event.type == "switch_turn":
                await asyncio.sleep(0.5)
                new_events = await game.process_turn()
                await process_game_events(game.lobby_id, new_events)

            elif event.type == "bot_think":
                bot_events = await game.execute_bot_turn(event.extra_data["bot_id"], event.token)
                await process_game_events(game.lobby_id, bot_events)

        except Exception as e:
            logging.error(f"Event Error ({event.type}): {e}")


# === ADMIN COMMANDS ===

@router.message(Command("fake_join"))
async def cmd_fake_join(message: Message, command: CommandObject):
    user_id = message.from_user.id
    if not ADMIN_ID or user_id != ADMIN_ID:
        await message.reply(f"⛔ Вы не админ. Ваш ID: <code>{user_id}</code>")
        return

    lid = lobby_manager.user_to_lobby.get(user_id)
    if not lid:
        await message.reply("⚠️ Нет лобби.")
        return

    lobby = lobby_manager.get_lobby(lid)
    if not lobby or lobby.status != "waiting":
        await message.reply("❌ Лобби не найдено или игра уже идет.")
        return

    fake_name = command.args if command.args else f"Fake_{random.choice(['Bob', 'Alice', 'John'])}"
    # ID в диапазоне фейков (<= -50000)
    fake_id = -random.randint(50000, 99999)

    lobby.add_player(fake_id, fake_name)
    await message.reply(f"🤖 Фейк <b>{fake_name}</b> добавлен.")
    await broadcast_lobby_ui(lobby)


@router.message(Command("fake_say"))
async def cmd_fake_say(message: Message, command: CommandObject):
    user_id = message.from_user.id
    if not ADMIN_ID or user_id != ADMIN_ID: return

    lid = lobby_manager.user_to_lobby.get(user_id)
    if not lid or lid not in active_games: return

    game = active_games[lid]
    text = command.args
    if not text: return

    active_list = [p for p in game.players if p.is_alive]
    if game.state.phase == "runoff":
        active_list = [p for p in active_list if p.name in game.state.shared_data.get("runoff_candidates", [])]

    if game.current_turn_index >= len(active_list): return
    current_player = active_list[game.current_turn_index]

    if current_player.id > 0:
        await message.reply(f"Сейчас ход реального игрока {current_player.name}.")
        return

    events = await game.process_message(player_id=current_player.id, text=text)
    await process_game_events(game.lobby_id, events)


@router.message(Command("kick"))
async def cmd_kick(message: Message, command: CommandObject):
    chat_id = message.chat.id
    lid = lobby_manager.user_to_lobby.get(chat_id)
    if not lid and str(chat_id) in active_games: lid = str(chat_id)
    if not lid or lid not in active_games: return
    game = active_games[lid]
    lobby = lobby_manager.get_lobby(lid)

    if lobby and lobby.host_id != message.from_user.id:
        await message.reply("⛔ Только хост может кикать.")
        return

    target_name = command.args
    if not target_name:
        await message.reply("Укажите имя.")
        return

    target_player = next((p for p in game.players if target_name.lower() in p.name.lower() and p.is_human), None)

    if target_player:
        events = await game.player_leave(target_player.id)
        lobby_manager.leave_lobby(target_player.id)
        await message.reply(f"🥾 Игрок {target_player.name} кикнут.")
        await process_game_events(game.lobby_id, events)
    else:
        await message.reply("Игрок не найден.")


@router.message(Command("skip"))
async def cmd_skip(message: Message):
    chat_id = message.chat.id
    lid = lobby_manager.user_to_lobby.get(chat_id)
    if not lid and str(chat_id) in active_games: lid = str(chat_id)

    if lid and lid in active_games:
        await process_game_events(lid, [GameEvent(type="switch_turn")])
        await message.reply("⏩ Ход пропущен.")


@router.message(Command("vote_as"))
async def cmd_vote_as(message: Message, command: CommandObject):
    chat_id = message.chat.id
    lid = lobby_manager.user_to_lobby.get(chat_id)
    if not lid and str(chat_id) in active_games: lid = str(chat_id)
    if not lid or lid not in active_games: return
    game = active_games[lid]
    lobby = lobby_manager.get_lobby(lid)

    is_host = lobby and lobby.host_id == message.from_user.id
    is_admin = ADMIN_ID and message.from_user.id == ADMIN_ID

    if not (is_host or is_admin): return

    args = command.args.split(maxsplit=1) if command.args else []
    if len(args) < 2: return

    voter_name = args[0]
    target_name = args[1]

    voter = next((p for p in game.players if voter_name.lower() in p.name.lower()), None)
    if not voter: return

    action_data = f"vote_{target_name}"
    events = await game.handle_action(player_id=voter.id, action_data=action_data)

    if events:
        await message.reply(f"✅ Голос: {voter.name} -> {target_name}")
        await process_game_events(game.lobby_id, events)
    else:
        await message.reply("❌ Ошибка.")


# === HANDLERS (STANDARD) ===

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    args = command.args
    if args and args.startswith("join_"):
        lobby_id = args.split("_")[1]
        await join_lobby_logic(message, lobby_id)
        return

    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="☢️ Соло", callback_data="start_bunker_solo"))
    kb.add(InlineKeyboardButton(text="🆕 Создать", callback_data="lobby_create"))
    kb.add(InlineKeyboardButton(text="🔍 Найти", callback_data="lobby_list"))
    kb.adjust(1, 2)
    await message.answer("<b>🎮 B U N K E R</b>\nВыберите режим:", reply_markup=kb.as_markup())


@router.callback_query(F.data == "start_bunker_solo")
async def start_bunker_handler(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user = callback.from_user
    lid = str(chat_id)
    game = BunkerGame(lobby_id=lid)
    active_games[lid] = game
    lobby_manager.leave_lobby(user.id)

    await callback.message.edit_text("🚀 Запуск симуляции...")
    events = game.init_game([{"id": user.id, "name": user.first_name}])
    for e in events:
        if e.type == "update_dashboard":
            e.type = "message"
            e.extra_data["is_dashboard"] = True
    await process_game_events(lid, events)
    turn_events = await game.process_turn()
    await process_game_events(lid, turn_events)


@router.callback_query(F.data == "lobby_create")
async def lobby_create_handler(callback: CallbackQuery):
    user = callback.from_user
    lobby_manager.leave_lobby(user.id)
    lobby = lobby_manager.create_lobby(user.id, user.first_name)
    lobby.user_interfaces[user.id] = callback.message.message_id
    await broadcast_lobby_ui(lobby)


@router.callback_query(F.data == "lobby_list")
async def lobby_list_handler(callback: CallbackQuery):
    lobbies = lobby_manager.get_all_waiting()
    kb = InlineKeyboardBuilder()
    if not lobbies:
        kb.add(InlineKeyboardButton(text="Нет активных комнат 🤷‍♂️", callback_data="dummy"))
    else:
        for l in lobbies:
            count = len(l.players)
            host_name = l.players[l.host_id]['name']
            btn_text = f"🚪 {l.lobby_id} | {host_name} ({count})"
            kb.add(InlineKeyboardButton(text=btn_text, callback_data=f"lobby_join_{l.lobby_id}"))
    kb.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu"))
    kb.adjust(1)
    await callback.message.edit_text("<b>Список комнат:</b>", reply_markup=kb.as_markup())


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: CallbackQuery):
    await cmd_start(callback.message, CommandObject())


@router.callback_query(F.data.startswith("lobby_join_"))
async def lobby_join_btn_handler(callback: CallbackQuery):
    lobby_id = callback.data.split("_")[2]
    await join_lobby_logic(callback.message, lobby_id)


async def join_lobby_logic(message: Message, lobby_id: str):
    user = message.from_user
    lobby_manager.leave_lobby(user.id)
    success = lobby_manager.join_lobby(lobby_id, user.id, user.first_name)
    if success:
        lobby = lobby_manager.get_lobby(lobby_id)
        if isinstance(message, Message) and not message.from_user.is_bot:
            msg = await message.answer("Подключение...")
            lobby.user_interfaces[user.id] = msg.message_id
        else:
            lobby.user_interfaces[user.id] = message.message_id
        await broadcast_lobby_ui(lobby)
    else:
        await message.answer("❌ Лобби не найдено.")


@router.callback_query(F.data == "lobby_leave")
async def lobby_leave_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    lid = lobby_manager.user_to_lobby.get(user_id)

    if lid and lid in active_games:
        game = active_games[lid]
        game_events = await game.player_leave(user_id)
        await process_game_events(lid, game_events)

    lobby = lobby_manager.leave_lobby(user_id)
    if lobby:
        if lobby.host_id == user_id:
            if lid in active_games:
                await process_game_events(lid, [GameEvent(type="game_over", content="Хост вышел. Игра окончена.")])
        else:
            await callback.answer("Вы вышли.")
            current_lobby = lobby_manager.get_lobby(lobby.lobby_id)
            if current_lobby: await broadcast_lobby_ui(current_lobby)
    await cmd_start(callback.message, CommandObject())


@router.callback_query(F.data.startswith("lobby_start_"))
async def lobby_start_handler(callback: CallbackQuery):
    lobby_id = callback.data.split("_")[2]
    lobby = lobby_manager.get_lobby(lobby_id)
    if not lobby: return
    if callback.from_user.id != lobby.host_id:
        await callback.answer("Ждите лидера!", show_alert=True)
        return
    lobby.status = "playing"
    await callback.message.edit_text(f"🚀 <b>ИГРА ЗАПУЩЕНА!</b>")

    game = BunkerGame(lobby_id=lobby_id)
    active_games[lobby_id] = game

    users_data = lobby.to_game_users_list()
    events = game.init_game(users_data)
    for e in events:
        if e.type == "update_dashboard":
            e.type = "message"
            e.extra_data["is_dashboard"] = True

    await process_game_events(lobby_id, events)
    turn_events = await game.process_turn()
    await process_game_events(lobby_id, turn_events)


@router.message()
async def chat_message_handler(message: Message):
    chat_id = message.chat.id
    game = None
    if str(chat_id) in active_games:
        game = active_games[str(chat_id)]
    else:
        lid = lobby_manager.user_to_lobby.get(chat_id)
        if lid: game = active_games.get(lid)
    if not game: return
    lobby = lobby_manager.get_lobby(game.lobby_id)
    if lobby: lobby.touch()

    events = await game.process_message(player_id=message.from_user.id, text=message.text)
    await process_game_events(game.lobby_id, events)


@router.callback_query(F.data.startswith("vote_"))
async def game_action_handler(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    game = None
    if str(chat_id) in active_games:
        game = active_games[str(chat_id)]
    else:
        lid = lobby_manager.user_to_lobby.get(chat_id)
        if lid: game = active_games.get(lid)
    if not game: return
    lobby = lobby_manager.get_lobby(game.lobby_id)
    if lobby: lobby.touch()

    events = await game.handle_action(player_id=callback.from_user.id, action_data=callback.data)
    if events: events[0].extra_data["query_id"] = callback.id
    await process_game_events(game.lobby_id, events)


async def main():
    await start_web_server()
    asyncio.create_task(cleanup_lobbies_task())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅ Core System Online.")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass