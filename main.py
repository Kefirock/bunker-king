import asyncio
import logging
import os
import sys

# --- ДИАГНОСТИЧЕСКИЙ БЛОК ---
print("🔍 DEBUG: INSPECTING SERVER FILES")
target_config = "/app/src/games/bunker/config.py"
if os.path.exists(target_config):
    try:
        with open(target_config, "r", encoding="utf-8") as f:
            content = f.read()
            print(f"📄 Content of {target_config}:\n{'-' * 20}\n{content}\n{'-' * 20}")
    except Exception as e:
        print(f"⚠️ Could not read file: {e}")
else:
    print(f"❌ File {target_config} NOT FOUND!")
# ----------------------------

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
from aiohttp import web

# Импорты ядра
from src.core.schemas import GameEvent

# Импорт игры с отловом ошибок
try:
    from src.games.bunker.game import BunkerGame
except ImportError as e:
    print(f"🔥 CRITICAL IMPORT ERROR: {e}")
    # Пытаемся импортировать конфиг напрямую, чтобы увидеть детали
    try:
        import src.games.bunker.config

        print(f"DEBUG: Config dir contents: {dir(src.games.bunker.config)}")
    except Exception as ex:
        print(f"DEBUG: Even direct config import failed: {ex}")
    sys.exit(1)

load_dotenv(os.path.join("Configs", ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    sys.exit("Error: BOT_TOKEN is missing")

bot = Bot(token=BOT_TOKEN, session=AiohttpSession(), default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

active_games = {}
dashboard_map = {}


# === DUMMY SERVER ДЛЯ KOYEB ===
async def health_check(request):
    return web.Response(text="Bot is alive")


async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌍 Web server started on port {port}")


# === ЛОГИКА БОТА ===

async def process_game_events(chat_id: int, events: list[GameEvent]):
    if not events: return
    game = active_games.get(chat_id)
    if not game: return

    for event in events:
        try:
            if event.type == "message":
                targets = event.target_ids if event.target_ids else [chat_id]
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
                        dashboard_map[game.lobby_id] = sent_msg.message_id
                        try:
                            await bot.pin_chat_message(chat_id=tid, message_id=sent_msg.message_id)
                        except:
                            pass

            elif event.type == "update_dashboard":
                msg_id = dashboard_map.get(game.lobby_id)
                if msg_id:
                    try:
                        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=event.content)
                    except:
                        pass

            elif event.type == "callback_answer":
                if event.target_ids:
                    await bot.answer_callback_query(callback_query_id=event.extra_data.get("query_id"),
                                                    text=event.content)

            elif event.type == "game_over":
                await bot.send_message(chat_id, f"🏁 <b>GAME OVER</b>\n{event.content}")
                if chat_id in active_games:
                    del active_games[chat_id]
                return

            elif event.type == "switch_turn":
                await asyncio.sleep(1.5)
                new_events = await game.process_turn()
                await process_game_events(chat_id, new_events)

        except Exception as e:
            logging.error(f"Event Error ({event.type}): {e}")


@router.message(CommandStart())
async def cmd_start(message: Message):
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="☢️ Играть в Бункер (Соло)", callback_data="start_bunker_solo"))
    await message.answer("<b>🎮 ИГРОВОЙ ХАБ</b>\nВыберите игру:", reply_markup=kb.as_markup())


@router.callback_query(F.data == "start_bunker_solo")
async def start_bunker_handler(callback: CallbackQuery):
    user = callback.from_user
    chat_id = callback.message.chat.id
    game = BunkerGame(lobby_id=str(chat_id))
    active_games[chat_id] = game
    await callback.message.edit_text("🚀 Запуск симуляции...")

    events = game.init_game([{"id": user.id, "name": user.first_name}])
    for e in events:
        if e.type == "update_dashboard":
            e.type = "message"
            e.extra_data["is_dashboard"] = True

    await process_game_events(chat_id, events)
    turn_events = await game.process_turn()
    await process_game_events(chat_id, turn_events)


@router.message()
async def chat_message_handler(message: Message):
    chat_id = message.chat.id
    game = active_games.get(chat_id)
    if not game: return
    events = await game.process_message(player_id=message.from_user.id, text=message.text)
    await process_game_events(chat_id, events)


@router.callback_query(F.data.startswith("vote_"))
async def game_action_handler(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    game = active_games.get(chat_id)
    if not game: return
    events = await game.handle_action(player_id=callback.from_user.id, action_data=callback.data)
    if events:
        events[0].extra_data["query_id"] = callback.id
    await process_game_events(chat_id, events)


async def main():
    await start_web_server()
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