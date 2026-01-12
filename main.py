import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties

# Новые импорты ядра и игр
from src.core.schemas import GameEvent
from src.games.bunker.game import BunkerGame

load_dotenv(os.path.join("Configs", ".env"))

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Инициализация бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    sys.exit("Error: BOT_TOKEN is missing in .env")

bot = Bot(token=BOT_TOKEN, session=AiohttpSession(), default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# === ГЛОБАЛЬНОЕ СОСТОЯНИЕ ===
# В продакшене лучше использовать Redis, но для бота достаточно словаря в памяти.
# chat_id -> GameEngine instance
active_games = {}
# game_id -> dashboard_message_id (чтобы знать, какое сообщение обновлять)
dashboard_map = {}


# === ОБРАБОТЧИК СОБЫТИЙ (ГЛАВНЫЙ МОСТ) ===
async def process_game_events(chat_id: int, events: list[GameEvent]):
    """
    Выполняет инструкции, полученные от Игрового Движка.
    """
    if not events: return

    game = active_games.get(chat_id)
    if not game: return

    for event in events:
        try:
            # 1. Отправка сообщения
            if event.type == "message":
                # Если указаны конкретные получатели - шлем им (для мультиплеера)
                # Если нет - шлем в текущий чат (для соло)
                targets = event.target_ids if event.target_ids else [chat_id]

                # Конвертация клавиатуры (если есть)
                kb = None
                if event.reply_markup:
                    builder = InlineKeyboardBuilder()
                    for btn in event.reply_markup:
                        builder.add(InlineKeyboardButton(text=btn["text"], callback_data=btn["callback_data"]))
                    builder.adjust(1)
                    kb = builder.as_markup()

                for tid in targets:
                    # Корректируем ID для ботов (у них отрицательные ID, им слать не надо)
                    if isinstance(tid, int) and tid < 0: continue

                    sent_msg = await bot.send_message(chat_id=tid, text=event.content, reply_markup=kb)

                    # Если нужно запомнить это сообщение как дашборд (хак для старта)
                    if event.extra_data.get("is_dashboard"):
                        dashboard_map[game.lobby_id] = sent_msg.message_id
                        await bot.pin_chat_message(chat_id=tid, message_id=sent_msg.message_id)

            # 2. Обновление Дашборда (Закрепа)
            elif event.type == "update_dashboard":
                msg_id = dashboard_map.get(game.lobby_id)
                if msg_id:
                    try:
                        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=event.content)
                    except Exception:
                        pass  # Часто падает, если текст не изменился

            # 3. Ответ на callback (чтобы часики не висели)
            elif event.type == "callback_answer":
                # Берем первый ID из таргетов
                if event.target_ids:
                    await bot.answer_callback_query(callback_query_id=event.extra_data.get("query_id"),
                                                    text=event.content)

            # 4. Конец игры
            elif event.type == "game_over":
                await bot.send_message(chat_id, f"🏁 <b>GAME OVER</b>\n{event.content}")
                if chat_id in active_games:
                    del active_games[chat_id]
                return  # Прерываем обработку, игры больше нет

            # 5. Передача хода (рекурсивный вызов логики ботов)
            elif event.type == "switch_turn":
                # Небольшая задержка для реалистичности
                await asyncio.sleep(1.5)
                # Вызываем process_turn у игры
                new_events = await game.process_turn()
                # Рекурсивно обрабатываем новые события
                await process_game_events(chat_id, new_events)

        except Exception as e:
            logging.error(f"Event Error ({event.type}): {e}")


# === MENU HANDLERS ===
@router.message(CommandStart())
async def cmd_start(message: Message):
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="☢️ Играть в Бункер (Соло)", callback_data="start_bunker_solo"))
    # В будущем тут появится: kb.add(InlineKeyboardButton(text="🕵️ Мафия", callback_data="start_mafia"))

    await message.answer(
        "<b>🎮 ИГРОВОЙ ХАБ</b>\nВыберите игру:",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data == "start_bunker_solo")
async def start_bunker_handler(callback: CallbackQuery):
    user = callback.from_user
    chat_id = callback.message.chat.id

    # 1. Создаем экземпляр игры
    # В качестве ID лобби используем chat_id пользователя (для соло)
    game = BunkerGame(lobby_id=str(chat_id))
    active_games[chat_id] = game

    await callback.message.edit_text("🚀 Запуск симуляции...")

    # 2. Инициализируем (передаем игрока)
    user_data = [{"id": user.id, "name": user.first_name}]
    events = game.init_game(user_data)

    # 3. Находим событие дашборда и помечаем его (чтобы process_events сохранил ID)
    for e in events:
        if e.type == "update_dashboard":
            # Меняем тип на message для первой отправки, но ставим флаг
            e.type = "message"
            e.extra_data["is_dashboard"] = True

    # 4. Запускаем цикл событий
    await process_game_events(chat_id, events)

    # 5. Сразу дергаем process_turn (вдруг первый ход бота?)
    turn_events = await game.process_turn()
    await process_game_events(chat_id, turn_events)


# === GAMEPLAY HANDLERS ===

@router.message()
async def chat_message_handler(message: Message):
    """Перехватывает ВСЕ текстовые сообщения"""
    chat_id = message.chat.id
    game = active_games.get(chat_id)

    # Если игры нет - игнорируем (или можно слать меню)
    if not game:
        return

    # Передаем текст в движок
    # Движок вернет события (ответ Судьи, отправка сообщения в чат и т.д.)
    events = await game.process_message(player_id=message.from_user.id, text=message.text)
    await process_game_events(chat_id, events)


@router.callback_query(F.data.startswith("vote_"))
async def game_action_handler(callback: CallbackQuery):
    """Обработка кнопок внутри игры"""
    chat_id = callback.message.chat.id
    game = active_games.get(chat_id)
    if not game: return

    # Передаем нажатие в движок
    events = await game.handle_action(player_id=callback.from_user.id, action_data=callback.data)

    # Добавляем ID callback-запроса, чтобы process_events мог его закрыть
    if events:
        events[0].extra_data["query_id"] = callback.id

    await process_game_events(chat_id, events)


async def main():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅ Core System Online. Waiting for players...")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass