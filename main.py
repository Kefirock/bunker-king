import asyncio
import logging
import os
import sys
import random
from collections import Counter
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import web  # <--- НОВЫЙ ИМПОРТ

from src.config import cfg
from src.utils import GameSetup
from src.schemas import GameState, PlayerProfile
from src.services.bot import BotEngine
from src.services.judge import JudgeService
from src.services.director import DirectorEngine
from src.logger_service import game_logger

# Загрузка переменных окружения (локально)
load_dotenv(os.path.join("Configs", ".env"))

# Инициализация сервисов
bot_engine = BotEngine()
judge_service = JudgeService()
director_engine = DirectorEngine()

# Настройка бота и прокси
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")

if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN is missing in Configs/.env or Environment Variables")
    sys.exit(1)

if PROXY_URL:
    print(f"📡 Using Proxy: {PROXY_URL}")
    session = AiohttpSession(proxy=PROXY_URL)
    bot = Bot(token=BOT_TOKEN, session=session)
else:
    print("📡 Direct connection (No Proxy)")
    bot = Bot(token=BOT_TOKEN)

dp = Dispatcher()
router = Router()
dp.include_router(router)


# FSM Состояния
class GameFSM(StatesGroup):
    Lobby = State()
    GameLoop = State()
    HumanTurn = State()
    Voting = State()


# --- ФЕЙКОВЫЙ ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def health_check(request):
    """Просто возвращает 200 OK, чтобы Render знал, что мы живы"""
    return web.Response(text="Bunker Bot is running!")


async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()

    # Render передает порт через переменную окружения PORT
    # Локально будет использоваться 8080
    port = int(os.getenv("PORT", 8080))

    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 Dummy server started on port {port}")


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_topic_for_round_base(round_num: int, trait: str = "", catastrophe_data: dict = None) -> str:
    """Возвращает базовую тему раунда (для фазы презентации)"""
    topics_cfg = cfg.gameplay["rounds"]["topics"]
    if round_num == 1:
        return topics_cfg[1]
    elif round_num == 2:
        return topics_cfg[2].format(trait=trait)
    else:
        # Для 3+ раунда берем случайную проблему из сценария
        if catastrophe_data and "topics" in catastrophe_data:
            # Берем проблему по модулю, чтобы не выйти за границы списка
            idx = (round_num - 3) % len(catastrophe_data["topics"])
            problem = catastrophe_data["topics"][idx]
            return topics_cfg[3].format(catastrophe_problem=problem)
        return "ВЫЖИВАНИЕ. Докажи свою пользу."


def get_display_topic(gs: GameState, player_trait: str = "", catastrophe_data: dict = None) -> str:
    """
    Возвращает текст, который видит игрок в зависимости от текущей фазы.
    """
    if gs.phase == "presentation":
        return get_topic_for_round_base(gs.round, player_trait, catastrophe_data)
    elif gs.phase == "discussion":
        return "ОБСУЖДЕНИЕ. Кто лишний? Назови имя того, против кого будешь голосовать, и объясни почему."
    elif gs.phase == "runoff":
        candidates_str = ", ".join(gs.runoff_candidates)
        return f"ПЕРЕСТРЕЛКА. {candidates_str} на грани вылета. Докажи, что ты полезнее."
    return "..."


# --- ОБРАБОТЧИКИ (HANDLERS) ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="☢️ НАЧАТЬ ИГРУ", callback_data="start_game"))
    await message.answer("<b>BUNKER 2.5: WEIGHTED DECISIONS</b>", reply_markup=kb.as_markup(), parse_mode="HTML")
    await state.set_state(GameFSM.Lobby)


@router.callback_query(F.data == "start_game")
async def start_game_handler(callback: CallbackQuery, state: FSMContext):
    user_name = callback.from_user.first_name

    # Новая сессия логов
    game_logger.new_session(user_name)
    game_logger.log_game_event("SYSTEM", f"New game started for user: {user_name}")

    # Генерация игроков и состояния
    players = GameSetup.generate_players(user_name)
    game_state = GameSetup.init_game_state()
    game_state.topic = get_topic_for_round_base(1)

    # Поиск сценария (катастрофы)
    current_catastrophe = cfg.scenarios["catastrophes"][0]  # Fallback
    for cat in cfg.scenarios["catastrophes"]:
        if cat["name"] in game_state.topic:
            current_catastrophe = cat
            break

    # Сохраняем в FSM
    await state.update_data(
        players=[p.model_dump() for p in players],
        game_state=game_state.model_dump(),
        catastrophe=current_catastrophe,
        current_turn_index=0
    )

    # Интро
    intro = f"🌍 <b>СЦЕНАРИЙ:</b> {current_catastrophe['name']}\n\n👥 <b>ИГРОКИ:</b>\n"
    for p in players:
        role = p.profession if p.is_human else "???"
        intro += f"- {p.name}: {role}\n"
        # Логируем скрытые данные для дебага
        game_logger.log_game_event("SYSTEM", "Player Created",
                                   {"name": p.name, "profession": p.profession, "trait": p.trait,
                                    "is_human": p.is_human})

    await callback.message.edit_text(intro, parse_mode="HTML")
    await start_round(callback.message.chat.id, state)


async def start_round(chat_id: int, state: FSMContext):
    data = await state.get_data()
    gs = GameState(**data["game_state"])

    # Сброс фазы на презентацию
    gs.phase = "presentation"

    # Тема раунда (общая)
    base_topic = get_topic_for_round_base(gs.round, trait="...", catastrophe_data=data.get("catastrophe"))
    gs.topic = base_topic

    msg = f"🔔 <b>РАУНД {gs.round}</b>\nТема: {base_topic}\n\n🗣 <b>ФАЗА 1: ПРЕЗЕНТАЦИЯ</b>"
    await bot.send_message(chat_id, msg, parse_mode="HTML")
    game_logger.log_game_event("SYSTEM", f"Round {gs.round} Started", {"topic": base_topic, "phase": gs.phase})

    await state.update_data(game_state=gs.model_dump(), current_turn_index=0)
    await process_turn(chat_id, state)


async def process_turn(chat_id: int, state: FSMContext):
    """Главный цикл хода"""
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]
    gs = GameState(**data["game_state"])
    idx = data["current_turn_index"]
    cat_data = data.get("catastrophe", {})

    # 1. Определяем список активных игроков (кто ходит в этой фазе)
    if gs.phase == "runoff":
        active_players_list = [p for p in players if p.name in gs.runoff_candidates]
    else:
        active_players_list = players

    # 2. Если все походили -> Смена фазы
    if idx >= len(active_players_list):
        if gs.phase == "presentation":
            gs.phase = "discussion"
            await state.update_data(game_state=gs.model_dump(), current_turn_index=0)

            disc_topic = get_display_topic(gs)
            await bot.send_message(chat_id, f"⚔️ <b>ФАЗА 2: ОБСУЖДЕНИЕ</b>\n{disc_topic}", parse_mode="HTML")

            game_logger.log_game_event("SYSTEM", "Phase Changed", {"new_phase": gs.phase})
            await asyncio.sleep(1)
            await process_turn(chat_id, state)
            return

        elif gs.phase == "discussion":
            gs.phase = "voting"
            await state.update_data(game_state=gs.model_dump())
            game_logger.log_game_event("SYSTEM", "Phase Changed", {"new_phase": gs.phase})
            await start_voting(chat_id, state)
            return

        elif gs.phase == "runoff":
            gs.phase = "voting"
            await state.update_data(game_state=gs.model_dump())
            game_logger.log_game_event("SYSTEM", "Phase Changed", {"new_phase": gs.phase, "runoff_vote": True})
            await start_voting(chat_id, state)
            return

    # 3. Ход текущего игрока
    current_player = active_players_list[idx]

    # Актуальный топик (нужен для подстановки черты, если это раунд 2)
    actual_topic = get_display_topic(gs, player_trait=current_player.trait, catastrophe_data=cat_data)

    # Временный стейт для передачи в LLM
    temp_gs = gs.model_copy()
    temp_gs.topic = actual_topic

    if current_player.is_human:
        # Ход человека
        await bot.send_message(chat_id, f"👤 <b>Твой ход</b>:\n{actual_topic}", parse_mode="HTML")
        game_logger.log_game_event("HUMAN_TURN", f"User {current_player.name} is making a move. Phase: {gs.phase}")
        await state.update_data(game_state=gs.model_dump())
        await state.set_state(GameFSM.HumanTurn)
        return
    else:
        # Ход бота
        await bot.send_chat_action(chat_id, "typing")

        # Директор дает скрытую инструкцию (находит козла отпущения)
        instr = await director_engine.get_hidden_instruction(current_player, players, temp_gs)
        game_logger.log_game_event("DIRECTOR", f"Director instruction for {current_player.name}",
                                   {"instruction": instr if instr else "None"})

        # Бот генерирует речь
        speech = await bot_engine.make_turn(current_player, players, temp_gs, director_instruction=instr)
        await bot.send_message(chat_id, f"🤖 <b>{current_player.name}</b>:\n{speech}", parse_mode="HTML")

        game_logger.log_chat_message(current_player.name, speech)
        game_logger.log_game_event("BOT_SPEECH", f"{current_player.name} spoke.", {"speech": speech, "phase": gs.phase})

        # Судья анализирует и раздает ярлыки (теги)
        verdict = await judge_service.analyze_move(current_player, speech, actual_topic)

        # Обновляем счетчик подозрений
        current_player.suspicion_score = verdict["total_suspicion"]

        game_logger.log_game_event("JUDGE", f"Verdict for {current_player.name}",
                                   {"score_delta": verdict['score'], "total": verdict['total_suspicion'],
                                    "type": verdict['type'], "comment": verdict['comment']})

        # Обновляем статус (для красоты и других ботов)
        thresholds = cfg.gameplay["judge"]["status_thresholds"]
        if current_player.suspicion_score >= thresholds["impostor"]:
            current_player.status = "IMPOSTOR"
        elif current_player.suspicion_score >= thresholds["liar"]:
            current_player.status = "LIAR"
        elif current_player.suspicion_score >= thresholds["suspicious"]:
            current_player.status = "SUSPICIOUS"
        else:
            current_player.status = "NORMAL"

        gs.history.append(f"[{current_player.name}]: {speech}")

        # Сохраняем
        data["players"] = [p.model_dump() for p in players]
        data["game_state"] = gs.model_dump()
        data["current_turn_index"] += 1

        await state.update_data(data)
        await asyncio.sleep(1.5)  # Пауза для реализма
        await process_turn(chat_id, state)


@router.message(GameFSM.HumanTurn)
async def human_turn_handler(message: Message, state: FSMContext):
    """Обработка ответа человека"""
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]
    gs = GameState(**data["game_state"])
    cat_data = data.get("catastrophe", {})

    # Находим правильного игрока в списке
    if gs.phase == "runoff":
        active_list = [p for p in players if p.name in gs.runoff_candidates]
    else:
        active_list = players

    idx = data["current_turn_index"]
    player = active_list[idx]

    actual_topic = get_display_topic(gs, player_trait=player.trait, catastrophe_data=cat_data)

    game_logger.log_chat_message(player.name, message.text)
    game_logger.log_game_event("HUMAN_SPEECH", f"User {player.name} spoke.",
                               {"speech": message.text, "phase": gs.phase})

    # Судья анализирует человека
    verdict = await judge_service.analyze_move(player, message.text, actual_topic)

    player.suspicion_score = verdict["total_suspicion"]

    game_logger.log_game_event("JUDGE", f"Verdict for {player.name}",
                               {"score_delta": verdict['score'], "total": verdict['total_suspicion'],
                                "type": verdict['type'], "comment": verdict['comment']})

    thresholds = cfg.gameplay["judge"]["status_thresholds"]
    if player.suspicion_score >= thresholds["impostor"]:
        player.status = "IMPOSTOR"
    elif player.suspicion_score >= thresholds["liar"]:
        player.status = "LIAR"
    elif player.suspicion_score >= thresholds["suspicious"]:
        player.status = "SUSPICIOUS"
    else:
        player.status = "NORMAL"

    gs.history.append(f"[{player.name}]: {message.text}")

    # Обновляем объект игрока в общем списке
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


# --- ГОЛОСОВАНИЕ ---

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
        # Проверка allow_self_vote из конфига
        if not p.is_human or cfg.gameplay["voting"]["allow_self_vote"]:
            kb.add(InlineKeyboardButton(text=f"☠ {p.name}", callback_data=f"vote_{p.name}"))
    kb.adjust(1)

    await bot.send_message(chat_id, f"🛑 <b>{title}</b>", reply_markup=kb.as_markup(), parse_mode="HTML")
    game_logger.log_game_event("VOTING", f"Voting started. Phase: {gs.phase}",
                               {"candidates": [p.name for p in targets]})
    await state.set_state(GameFSM.Voting)


@router.callback_query(GameFSM.Voting, F.data.startswith("vote_"))
async def voting_handler(callback: CallbackQuery, state: FSMContext):
    target_name = callback.data.split("_")[1]
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]
    gs = GameState(**data["game_state"])

    chat_id = callback.message.chat.id

    await callback.message.edit_reply_markup(reply_markup=None)

    # Определяем пул кандидатов
    if gs.runoff_candidates:
        valid_targets_objs = [p for p in players if p.name in gs.runoff_candidates]
    else:
        valid_targets_objs = players

    votes = [target_name]
    game_logger.log_game_event("VOTE", f"Human {callback.from_user.first_name} voted for {target_name}")

    # Голоса ботов
    for bot_p in players:
        if not bot_p.is_human:
            vote = await bot_engine.make_vote(bot_p, valid_targets_objs, gs)
            votes.append(vote)
            game_logger.log_game_event("VOTE", f"Bot {bot_p.name} voted for {vote}")

    counts = Counter(votes)
    results = counts.most_common()
    leader_name, leader_votes = results[0]

    leaders = [name for name, count in results if count == leader_votes]

    result_text = f"📊 <b>ИТОГИ:</b> {dict(counts)}\n"
    game_logger.log_game_event("VOTE_RESULTS", "Voting results calculated",
                               {"counts": dict(counts), "leaders": leaders})

    # Сценарий Ничьей (Runoff)
    if len(leaders) > 1:
        max_runoffs = cfg.gameplay["voting"]["max_runoffs"]
        if gs.runoff_count >= max_runoffs:
            loser_name = random.choice(leaders)
            result_text += f"⚖️ Снова ничья. Жребий выбрал: <b>{loser_name}</b>"
            await callback.message.answer(result_text, parse_mode="HTML")
            game_logger.log_game_event("SYSTEM", "Runoff exhausted. Random elimination.", {"loser": loser_name})
            await eliminate_player(loser_name, chat_id, state)
            return

        gs.phase = "runoff"
        gs.runoff_candidates = leaders
        gs.runoff_count += 1

        await state.update_data(game_state=gs.model_dump(), current_turn_index=0)

        msg = f"{result_text}\n⚖️ <b>НИЧЬЯ!</b> Перестрелка между: {', '.join(leaders)}.\nИм дается слово для оправдания."
        await callback.message.answer(msg, parse_mode="HTML")
        game_logger.log_game_event("SYSTEM", "Runoff initiated.",
                                   {"candidates": leaders, "runoff_count": gs.runoff_count})
        await process_turn(chat_id, state)
        return

    # Есть победитель
    await callback.message.answer(f"{result_text}🚪 <b>{leader_name}</b> изгнан.", parse_mode="HTML")
    game_logger.log_game_event("SYSTEM", "Player eliminated.", {"loser": leader_name})
    await eliminate_player(leader_name, chat_id, state)


async def eliminate_player(loser_name: str, chat_id: int, state: FSMContext):
    data = await state.get_data()
    players = [PlayerProfile(**p) for p in data["players"]]
    gs = GameState(**data["game_state"])

    survivors = [p for p in players if p.name != loser_name]

    target = cfg.gameplay["rounds"]["target_survivors"]
    human_alive = any(p.is_human for p in survivors)

    if not human_alive:
        await bot.send_message(chat_id, "💀 <b>GAME OVER</b>. Вы погибли.", parse_mode="HTML")
        game_logger.log_game_event("GAME_OVER", "Human player eliminated.")
        await state.clear()
        return

    if len(survivors) <= target:
        names = ", ".join([p.name for p in survivors])
        await bot.send_message(chat_id, f"🎉 <b>ПОБЕДА!</b> Выжили: {names}", parse_mode="HTML")
        game_logger.log_game_event("GAME_OVER", "Victory achieved.", {"survivors": names})
        await state.clear()
        return

    gs.runoff_candidates = []
    gs.runoff_count = 0
    gs.round += 1

    # Новый раунд
    cat_data = data.get("catastrophe", {})
    new_topic = get_topic_for_round_base(gs.round, trait="...", catastrophe_data=cat_data)
    gs.topic = new_topic

    await state.update_data(
        players=[p.model_dump() for p in survivors],
        game_state=gs.model_dump()
    )

    await asyncio.sleep(2)
    await start_round(chat_id, state)


async def main():
    # Запускаем фейковый веб-сервер (чтобы Render не убил бота на Free Tier)
    await start_dummy_server()

    # Удаляем вебхук и запускаем поллинг
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())