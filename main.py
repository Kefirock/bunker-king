import asyncio
import logging
import os
import sys

# --- ДИАГНОСТИКА ---
print("🔍 DEBUG: SERVER STARTUP")
# -------------------

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
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

# active_games: { lobby_id : GameEngine }
active_games = {}

# dashboard_map: { lobby_id : { user_id : message_id } }
# Храним ID сообщения-закрепа для каждого игрока в каждом лобби
dashboard_map = {}

# message_tokens: { chat_id:token : message_id }
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


# === UI HELPERS ===

async def update_lobby_ui(chat_id: int, message_id: int, lobby: Lobby):
    """Рисует меню лобби"""
    # Считаем, сколько ботов добавится
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

    kb = InlineKeyboardBuilder()

    # Кнопку старта видит только хост, но отрисовываем всем (API телеграма не дает скрыть для одного)
    # В хендлере стоит проверка прав.
    kb.add(InlineKeyboardButton(text="🚀 СТАРТ", callback_data=f"lobby_start_{lobby.lobby_id}"))
    kb.add(InlineKeyboardButton(text="🚪 Выйти", callback_data="lobby_leave"))
    kb.adjust(1)

    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=kb.as_markup())
    except Exception:
        pass


# === EVENT PROCESSOR ===

async def process_game_events(context_id: str, events: list[GameEvent]):
    """
    context_id: lobby_id (строка)
    """
    if not events: return

    # Находим игру
    game = active_games.get(context_id)
    if not game: return

    for event in events:
        try:
            # 1. SEND MESSAGE
            if event.type == "message":
                # Если target_ids пуст -> шлем ВСЕМ ЛЮДЯМ в игре
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

                    # Логика Дашборда (Закрепа)
                    if event.extra_data.get("is_dashboard"):
                        # Инициализируем словарь для лобби
                        if game.lobby_id not in dashboard_map:
                            dashboard_map[game.lobby_id] = {}

                        # Запоминаем ID сообщения для конкретного юзера
                        dashboard_map[game.lobby_id][tid] = sent_msg.message_id

                        try:
                            await bot.pin_chat_message(chat_id=tid, message_id=sent_msg.message_id)
                        except:
                            pass

                    if event.token:
                        token_key = f"{tid}:{event.token}"
                        message_tokens[token_key] = sent_msg.message_id

            # 2. EDIT MESSAGE
            elif event.type == "edit_message":
                targets = event.target_ids if event.target_ids else [p.id for p in game.players if p.is_human]
                for tid in targets:
                    if isinstance(tid, int) and tid < 0: continue
                    token_key = f"{tid}:{event.token}"
                    msg_id = message_tokens.get(token_key)

                    if msg_id:
                        try:
                            await bot.edit_message_text(chat_id=tid, message_id=msg_id, text=event.content)
                        except:
                            pass
                    else:
                        await bot.send_message(chat_id=tid, text=event.content)

            # 3. UPDATE DASHBOARD (Синхронно у всех)
            elif event.type == "update_dashboard":
                if game.lobby_id in dashboard_map:
                    # Проходимся по всем, у кого записан ID дашборда
                    user_map = dashboard_map[game.lobby_id]
                    for uid, msg_id in user_map.items():
                        try:
                            await bot.edit_message_text(chat_id=uid, message_id=msg_id, text=event.content)
                        except:
                            pass

            # 4. CALLBACK / GAME OVER / SWITCH / THINK
            elif event.type == "callback_answer":
                if event.target_ids:
                    await bot.answer_callback_query(callback_query_id=event.extra_data.get("query_id"),
                                                    text=event.content)

            elif event.type == "game_over":
                targets = [p.id for p in game.players if p.is_human]
                for tid in targets:
                    await bot.send_message(tid, f"🏁 <b>GAME OVER</b>\n{event.content}")

                # Чистим память
                if game.lobby_id in active_games: del active_games[game.lobby_id]
                if game.lobby_id in dashboard_map: del dashboard_map[game.lobby_id]

                # Удаляем лобби из менеджера, чтобы игроки могли создать новое
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
    # Deep link join
    if args and args.startswith("join_"):
        lobby_id = args.split("_")[1]
        await join_lobby_logic(message, lobby_id)
        return

    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="☢️ Соло", callback_data="start_bunker_solo"))
    kb.add(InlineKeyboardButton(text="🆕 Создать", callback_data="lobby_create"))
    kb.add(InlineKeyboardButton(text="🔍 Найти", callback_data="lobby_list"))
    kb.adjust(1, 2)  # Кнопка Соло большая, остальные в ряд

    await message.answer("<b>🎮 B U N K E R</b>\nВыберите режим:", reply_markup=kb.as_markup())


# --- SOLO START ---
@router.callback_query(F.data == "start_bunker_solo")
async def start_bunker_handler(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user = callback.from_user

    # Для соло используем ID чата как ID лобби
    lid = str(chat_id)
    game = BunkerGame(lobby_id=lid)
    active_games[lid] = game

    # Регистрируем "фейковое" лобби в менеджере, чтобы роутинг сообщений работал
    # (даже в соло мы идем через lobby_manager)
    lobby_manager.create_lobby(user.id, user.first_name)
    # Но так как create_lobby генерит рандомный ID, нам надо его подменить или использовать
    # Упростим: Соло игра работает БЕЗ lobby_manager, но роутинг проверяет active_games напрямую по chat_id

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
    # Если игрок уже где-то есть, выкидываем его
    lobby_manager.leave_lobby(user.id)

    lobby = lobby_manager.create_lobby(user.id, user.first_name)
    lobby.chat_id = callback.message.chat.id
    lobby.menu_message_id = callback.message.message_id
    await update_lobby_ui(lobby.chat_id, lobby.menu_message_id, lobby)


@router.callback_query(F.data == "lobby_list")
async def lobby_list_handler(callback: CallbackQuery):
    lobbies = lobby_manager.get_all_waiting()
    kb = InlineKeyboardBuilder()

    if not lobbies:
        kb.add(InlineKeyboardButton(text="Нет активных комнат 🤷‍♂️", callback_data="dummy"))
    else:
        for l in lobbies:
            # "🚪 ABCD | Alex (1/6)"
            count = len(l.players)
            # Достаем имя хоста
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
    # Сначала выходим из старых
    lobby_manager.leave_lobby(user.id)

    success = lobby_manager.join_lobby(lobby_id, user.id, user.first_name)
    if success:
        lobby = lobby_manager.get_lobby(lobby_id)

        # Обновляем UI у того, кто присоединился
        # (или отправляем новое сообщение, если это deep link)
        await message.answer(f"✅ Вход в лобби <b>{lobby_id}</b> выполнен.")

        # Обновляем UI у ХОСТА (и всех, кто видит старое меню)
        if lobby.menu_message_id and lobby.chat_id:
            await update_lobby_ui(lobby.chat_id, lobby.menu_message_id, lobby)

        # Оповещаем остальных (опционально)
        # for pid in lobby.players:
        #     if pid != user.id: await bot.send_message(pid, f"➕ {user.first_name}")

    else:
        await message.answer("❌ Лобби не найдено или игра уже идет.")


@router.callback_query(F.data == "lobby_leave")
async def lobby_leave_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    lobby = lobby_manager.leave_lobby(user_id)

    if lobby:
        if lobby.host_id == user_id:
            await callback.message.edit_text("🚫 Вы распустили лобби.")
            # В идеале надо оповестить остальных, что лобби закрыто
        else:
            await callback.answer("Вы вышли.")
            await callback.message.edit_text("Вы покинули лобби.")
            # Обновляем UI для оставшихся
            await update_lobby_ui(lobby.chat_id, lobby.menu_message_id, lobby)
    else:
        await callback.message.edit_text("Лобби нет.")
        await cmd_start(callback.message, CommandObject())


# --- LOBBY: START ---

@router.callback_query(F.data.startswith("lobby_start_"))
async def lobby_start_handler(callback: CallbackQuery):
    lobby_id = callback.data.split("_")[2]
    lobby = lobby_manager.get_lobby(lobby_id)
    if not lobby: return

    # Проверка прав
    if callback.from_user.id != lobby.host_id:
        await callback.answer("Ждите лидера!", show_alert=True)
        return

    lobby.status = "playing"

    # Создаем игру
    game = BunkerGame(lobby_id=lobby_id)
    active_games[lobby_id] = game

    # Берем людей и запускаем. Боты добавятся внутри init_game.
    users_data = lobby.to_game_users_list()
    events = game.init_game(users_data)

    # Помечаем дашборд
    for e in events:
        if e.type == "update_dashboard":
            e.type = "message"
            e.extra_data["is_dashboard"] = True

    # В мультиплеере важно: рассылаем всем
    await process_game_events(lobby_id, events)

    turn_events = await game.process_turn()
    await process_game_events(lobby_id, turn_events)


# --- ROUTING ---

@router.message()
async def chat_message_handler(message: Message):
    chat_id = message.chat.id

    # 1. Поиск игры
    game = None

    # A. Проверяем Соло (ключ = chat_id)
    if str(chat_id) in active_games:
        game = active_games[str(chat_id)]
    else:
        # B. Проверяем Мульти (через lobby_manager)
        lid = lobby_manager.user_to_lobby.get(chat_id)
        if lid: game = active_games.get(lid)

    if not game: return

    events = await game.process_message(player_id=message.from_user.id, text=message.text)
    # Передаем ID лобби, чтобы движок знал контекст
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

    events = await game.handle_action(player_id=callback.from_user.id, action_data=callback.data)
    if events: events[0].extra_data["query_id"] = callback.id
    await process_game_events(game.lobby_id, events)


async def main():
    await start_web_server()
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