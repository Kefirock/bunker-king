import asyncio
import logging
import os
import sys
import random
import time

print("🔍 DEBUG: SERVER STARTUP")

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiohttp import web

from src.core.schemas import GameEvent
from src.core.lobby import lobby_manager, Lobby

try:
    from src.games.bunker.game import BunkerGame
except ImportError as e:
    print(f"🔥 IMPORT ERROR: {e}")
    sys.exit(1)

load_dotenv(os.path.join("Configs", ".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN: sys.exit("Error: BOT_TOKEN is missing")

bot = Bot(token=BOT_TOKEN, session=AiohttpSession(), default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

active_games = {}
dashboard_map = {}
message_tokens = {}


# === WEB SERVER & BACKGROUND TASKS ===

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
    """Фоновая задача: удаляет лобби, где нет активности 5 минут"""
    while True:
        await asyncio.sleep(60)  # Проверка раз в минуту
        try:
            now = time.time()
            # Копируем ключи, так как будем удалять
            for lid, lobby in list(lobby_manager.lobbies.items()):
                if lobby.status == "waiting" and (now - lobby.last_activity > 300):  # 300 сек = 5 мин
                    logging.info(f"♻️ Cleaning up inactive lobby {lid}")

                    # Оповещаем и удаляем
                    for uid, msg_id in lobby.user_interfaces.items():
                        try:
                            await bot.edit_message_text(
                                chat_id=uid,
                                message_id=msg_id,
                                text="⌛ <b>Время истекло.</b> Лобби закрыто из-за неактивности.",
                                reply_markup=None
                            )
                        except:
                            pass

                    lobby_manager.delete_lobby(lid)
        except Exception as e:
            logging.error(f"Cleanup error: {e}")


# === UI HELPERS (SYNCHRONOUS) ===

async def broadcast_lobby_ui(lobby: Lobby):
    """Обновляет интерфейс у ВСЕХ участников лобби"""
    # Текст меню
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

    # Клавиатура
    # (Для хоста - с управлением, для остальных - только выход)
    # Но так как мы не можем делать разные клавиатуры в одном вызове,
    # мы будем генерировать их внутри цикла.

    dead_users = []

    for user_id, message_id in lobby.user_interfaces.items():
        kb = InlineKeyboardBuilder()

        # Если это Хост
        if user_id == lobby.host_id:
            kb.add(InlineKeyboardButton(text="🚀 СТАРТ", callback_data=f"lobby_start_{lobby.lobby_id}"))
            kb.add(InlineKeyboardButton(text="🚪 Закрыть лобби", callback_data="lobby_leave"))
            kb.adjust(1)
        else:
            kb.add(InlineKeyboardButton(text="🚪 Выйти", callback_data="lobby_leave"))

        try:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=message_id,
                text=text,
                reply_markup=kb.as_markup()
            )
        except TelegramForbiddenError:
            # Бот заблокирован пользователем -> удаляем юзера
            dead_users.append(user_id)
        except TelegramBadRequest as e:
            # Сообщение не найдено или не изменилось
            if "message is not modified" not in str(e):
                # Если сообщение удалено пользователем -> тоже считаем выходом?
                # Пока нет, просто игнорируем.
                pass
        except Exception as e:
            logging.warning(f"Failed update lobby UI for {user_id}: {e}")

    # Удаляем "мертвых душ"
    if dead_users:
        for uid in dead_users:
            lobby_manager.leave_lobby(uid)
        # Если кто-то отвалился, рекурсивно обновляем список для оставшихся
        # (но с защитой от бесконечной рекурсии)
        if len(lobby.players) > 0:
            asyncio.create_task(broadcast_lobby_ui(lobby))


# === EVENT PROCESSOR ===

async def process_game_events(context_id: str, events: list[GameEvent]):
    if not events: return
    game = active_games.get(context_id)
    if not game: return

    for event in events:
        try:
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
                    if isinstance(tid, int) and tid < 0: continue
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

            elif event.type == "edit_message":
                targets = event.target_ids if event.target_ids else [p.id for p in game.players if p.is_human]
                for tid in targets:
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
                if event.target_ids:
                    await bot.answer_callback_query(callback_query_id=event.extra_data.get("query_id"),
                                                    text=event.content)

            elif event.type == "game_over":
                targets = [p.id for p in game.players if p.is_human]
                for tid in targets:
                    await bot.send_message(tid, f"🏁 <b>GAME OVER</b>\n{event.content}")

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


# === HANDLERS ===

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


# --- SOLO START ---
@router.callback_query(F.data == "start_bunker_solo")
async def start_bunker_handler(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user = callback.from_user
    lid = str(chat_id)
    game = BunkerGame(lobby_id=lid)
    active_games[lid] = game

    lobby_manager.leave_lobby(user.id)  # Чистим старое

    await callback.message.edit_text("🚀 Запуск симуляции...")
    events = game.init_game([{"id": user.id, "name": user.first_name}])
    for e in events:
        if e.type == "update_dashboard":
            e.type = "message"
            e.extra_data["is_dashboard"] = True

    await process_game_events(lid, events)
    turn_events = await game.process_turn()
    await process_game_events(lid, turn_events)


# --- LOBBY: CREATE & LIST ---

@router.callback_query(F.data == "lobby_create")
async def lobby_create_handler(callback: CallbackQuery):
    user = callback.from_user
    lobby_manager.leave_lobby(user.id)

    lobby = lobby_manager.create_lobby(user.id, user.first_name)

    # Запоминаем сообщение интерфейса
    lobby.user_interfaces[user.id] = callback.message.message_id

    # Обновляем UI (это отрисует меню лобби)
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


# --- LOBBY: JOIN & LEAVE ---

@router.callback_query(F.data.startswith("lobby_join_"))
async def lobby_join_btn_handler(callback: CallbackQuery):
    lobby_id = callback.data.split("_")[2]
    await join_lobby_logic(callback.message, lobby_id)


async def join_lobby_logic(message: Message, lobby_id: str):
    user = message.from_user
    lobby_manager.leave_lobby(user.id)  # Выходим из текущих

    success = lobby_manager.join_lobby(lobby_id, user.id, user.first_name)
    if success:
        lobby = lobby_manager.get_lobby(lobby_id)

        # ВАЖНО: Мы не шлем новое сообщение, мы редактируем ТЕКУЩЕЕ сообщение с кнопкой
        # (если это callback) или шлем новое (если это deeplink).

        if isinstance(message, Message) and not message.from_user.is_bot:
            # Это Deeplink, шлем новое
            msg = await message.answer("Подключение...")
            lobby.user_interfaces[user.id] = msg.message_id
        else:
            # Это кнопка, ID сообщения уже есть в контексте callback (но тут message - это объект Message)
            # В aiogram message при callback - это message, к которому привязана кнопка.
            lobby.user_interfaces[user.id] = message.message_id

        # Обновляем UI у ВСЕХ (включая нового)
        await broadcast_lobby_ui(lobby)

    else:
        # Если не нашли - пишем ошибку (но лучше edit, если это callback)
        await message.answer("❌ Лобби не найдено или игра уже идет.")


@router.callback_query(F.data == "lobby_leave")
async def lobby_leave_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    lobby = lobby_manager.leave_lobby(user_id)

    if lobby:
        # Если вышли - возвращаем в главное меню
        await cmd_start(callback.message, CommandObject())

        # Если лобби еще живо (не хост вышел), обновляем остальных
        # Если лобби умерло (хост вышел), broadcast не нужен, оно удалено
        current_lobby = lobby_manager.get_lobby(lobby.lobby_id)
        if current_lobby:
            await broadcast_lobby_ui(current_lobby)
    else:
        await callback.message.edit_text("Лобби больше нет.")
        await cmd_start(callback.message, CommandObject())


# --- LOBBY: START ---

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


# --- ROUTING ---

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

    # Обновляем активность лобби, чтобы не удалилось
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

    # Обновляем активность
    lobby = lobby_manager.get_lobby(game.lobby_id)
    if lobby: lobby.touch()

    events = await game.handle_action(player_id=callback.from_user.id, action_data=callback.data)
    if events: events[0].extra_data["query_id"] = callback.id
    await process_game_events(game.lobby_id, events)


async def main():
    await start_web_server()
    # Запускаем уборщика лобби
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