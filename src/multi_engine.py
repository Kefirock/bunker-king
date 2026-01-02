# src/multi_engine.py
import asyncio
from aiogram import Bot
from src.config import cfg
from src.lobbies import Lobby, lobby_manager
from src.services.bot import BotEngine
from src.services.director import DirectorEngine
from src.services.judge import JudgeService
from src.schemas import PlayerProfile, GameState
from src.logger_service import game_logger


# Импортируем хелперы из main (чтобы не дублировать логику топиков)
# В идеале их надо вынести в utils, но пока просто скопируем логику
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
    return "..."


# Инициализация сервисов
bot_engine = BotEngine()
judge_service = JudgeService()
director_engine = DirectorEngine()


async def broadcast(lobby: Lobby, text: str, bot: Bot, exclude_id: int = None):
    """Рассылает сообщение всем игрокам лобби"""
    for p in lobby.players:
        if exclude_id and p["user_id"] == exclude_id:
            continue
        try:
            await bot.send_message(p["chat_id"], text, parse_mode="HTML")
        except:
            pass  # Игрок заблочил бота


async def process_multi_turn(lobby: Lobby, bot: Bot):
    """Главный цикл хода в мультиплеере"""
    if not lobby.game_state: return

    gs = lobby.game_state
    players = lobby.game_players
    idx = lobby.current_turn_index
    cat_data = lobby.catastrophe_data

    # 1. Проверка конца фазы
    # (Упростим: пока поддерживаем только Presentation и Discussion, без Runoff для краткости)
    if idx >= len(players):
        if gs.phase == "presentation":
            gs.phase = "discussion"
            lobby.current_turn_index = 0
            await broadcast(lobby, f"⚔️ <b>ФАЗА 2: ОБСУЖДЕНИЕ</b>", bot)
            await asyncio.sleep(1)
            await process_multi_turn(lobby, bot)
            return
        elif gs.phase == "discussion":
            # Тут должно быть голосование, пока просто финиш раунда
            await broadcast(lobby, "🏁 <b>РАУНД ЗАВЕРШЕН (MVP Stop)</b>", bot)
            return

    current_player = players[idx]
    actual_topic = get_display_topic(gs, current_player, cat_data)

    # 2. ХОД
    if current_player.is_human:
        # Находим chat_id этого человека
        target_user = None
        for p in lobby.players:
            # Ищем по имени (не самый надежный способ, но простой для MVP)
            # Лучше хранить ID в PlayerProfile, но пока по имени
            if p["name"] == current_player.name:
                target_user = p
                break

        if target_user:
            # Уведомляем всех
            await broadcast(lobby, f"👉 Ходит <b>{current_player.name}</b>...", bot, exclude_id=target_user["user_id"])
            # Уведомляем игрока
            await bot.send_message(target_user["chat_id"],
                                   f"👤 <b>ТВОЙ ХОД!</b>\nТема: {actual_topic}\nНапиши сообщение в чат.",
                                   parse_mode="HTML")
            # Мы выходим из цикла. Движок ждет сообщения от юзера в main.py
            return
    else:
        # Ход бота
        await broadcast(lobby, f"🤖 <b>{current_player.name}</b> печатает...", bot)
        temp_gs = gs.model_copy()
        temp_gs.topic = actual_topic

        instr = await director_engine.get_hidden_instruction(current_player, players, temp_gs)
        speech = await bot_engine.make_turn(current_player, players, temp_gs, instr)

        gs.history.append(f"[{current_player.name}]: {speech}")
        await broadcast(lobby, f"🤖 <b>{current_player.name}</b>:\n{speech}", bot)

        # Следующий ход
        lobby.current_turn_index += 1
        await asyncio.sleep(2)
        await process_multi_turn(lobby, bot)


async def handle_human_message(lobby: Lobby, bot: Bot, text: str, user_name: str):
    """Обработка ответа человека"""
    gs = lobby.game_state
    current_player = lobby.game_players[lobby.current_turn_index]

    # Проверка, что ходит именно он
    if current_player.name != user_name:
        return  # Игнорим чужие сообщения

    gs.history.append(f"[{current_player.name}]: {text}")

    # Рассылаем его сообщение всем остальным
    await broadcast(lobby, f"👤 <b>{current_player.name}</b>:\n{text}", bot)

    # Переход хода
    lobby.current_turn_index += 1
    await process_multi_turn(lobby, bot)