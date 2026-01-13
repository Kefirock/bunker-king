import asyncio
import logging
import os
import sys

# --- ДИАГНОСТИКА ---
print("🔍 DEBUG: SERVER STARTUP")
# -------------------

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
from aiohttp import web

from src.core.schemas import GameEvent

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
message_tokens = {}  # хранит ID сообщений: "chat_id:token" -> message_id


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


# === EVENT PROCESSOR ===

async def process_game_events(chat_id: int, events: list[GameEvent]):
    if not events: return
    game = active_games.get(chat_id)
    if not game: return

    for event in events:
        try:
            # 1. SEND MESSAGE
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

                    # Dashboard logic
                    if event.extra_data.get("is_dashboard"):
                        dashboard_map[game.lobby_id] = sent_msg.message_id
                        try:
                            await bot.pin_chat_message(chat_id=tid, message_id=sent_msg.message_id)
                        except:
                            pass

                    # Token logic
                    if event.token:
                        token_key = f"{tid}:{event.token}"
                        message_tokens[token_key] = sent_msg.message_id

            # 2. EDIT MESSAGE
            elif event.type == "edit_message":
                targets = event.target_ids if event.target_ids else [chat_id]
                for tid in targets:
                    if isinstance(tid, int) and tid < 0: continue
                    token_key = f"{tid}:{event.token}"
                    msg_id = message_tokens.get(token_key)

                    if msg_id:
                        try:
                            await bot.edit_message_text(chat_id=tid, message_id=msg_id, text=event.content)
                        except Exception as ex:
                            logging.warning(f"Edit failed: {ex}")
                    else:
                        await bot.send_message(chat_id=tid, text=event.content)

            # 3. UPDATE DASHBOARD
            elif event.type == "update_dashboard":
                msg_id = dashboard_map.get(game.lobby_id)
                if msg_id:
                    try:
                        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=event.content)
                    except:
                        pass

            # 4. CALLBACK ANSWER
            elif event.type == "callback_answer":
                if event.target_ids:
                    await bot.answer_callback_query(callback_query_id=event.extra_data.get("query_id"),
                                                    text=event.content)

            # 5. GAME OVER
            elif event.type == "game_over":
                await bot.send_message(chat_id, f"🏁 <b>GAME OVER</b>\n{event.content}")
                if chat_id in active_games: del active_games[chat_id]
                return

            # 6. SWITCH TURN (Loop)
            elif event.type == "switch_turn":
                await asyncio.sleep(0.5)
                new_events = await game.process_turn()
                await process_game_events(chat_id, new_events)

            # 7. НОВОЕ: BOT THINK (Async execution)
            elif event.type == "bot_think":
                # Здесь мы реально вызываем тяжелую функцию
                # Токен уже сохранен на шаге "message", теперь бот подумает и отредактирует его
                bot_events = await game.execute_bot_turn(event.extra_data["bot_id"], event.token)
                await process_game_events(chat_id, bot_events)

        except Exception as e:
            logging.error(f"Event Error ({event.type}): {e}")


# === HANDLERS ===

@router.message(CommandStart())
async def cmd_start(message: Message):
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="☢️ Играть в Бункер (Соло)", callback_data="start_bunker_solo"))
    await message.answer("<b>🎮 ИГРОВОЙ ХАБ</b>\nВыберите игру:", reply_markup=kb.as_markup())


@router.callback_query(F.data == "start_bunker_solo")
async def start_bunker_handler(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user = callback.from_user
    game = BunkerGame(lobby_id=str(chat_id))
    active_games[chat_id] = game
    await callback.message.edit_text("🚀 Инициализация...")

    events = game.init_game([{"id": user.id, "name": user.first_name}])
    for e in events:
        if e.type == "update_dashboard":
            e.type = "message"
            e.extra_data["is_dashboard"] = True

    await process_game_events(chat_id, events)

    # Start loop
    turn_events = await game.process_turn()
    await process_game_events(chat_id, turn_events)


@router.message()
async def chat_message_handler(message: Message):
    game = active_games.get(message.chat.id)
    if not game: return
    events = await game.process_message(player_id=message.from_user.id, text=message.text)
    await process_game_events(message.chat.id, events)


@router.callback_query(F.data.startswith("vote_"))
async def game_action_handler(callback: CallbackQuery):
    game = active_games.get(callback.message.chat.id)
    if not game: return
    events = await game.handle_action(player_id=callback.from_user.id, action_data=callback.data)
    if events: events[0].extra_data["query_id"] = callback.id
    await process_game_events(callback.message.chat.id, events)


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