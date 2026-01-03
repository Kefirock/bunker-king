import asyncio
from collections import Counter
from aiogram import Bot
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config import cfg
from src.lobbies import Lobby
from src.services.bot import BotEngine
from src.services.director import DirectorEngine
from src.services.judge import JudgeService
from src.schemas import PlayerProfile, GameState
from src.utils import GameSetup

bot_engine = BotEngine()
judge_service = JudgeService()
director_engine = DirectorEngine()


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


async def broadcast(lobby: Lobby, text: str, bot: Bot, exclude_id: int = None, reply_markup=None):
    """Рассылает сообщение. Если получатель фейк (ID < 0), шлет на chat_id хоста с пометкой."""
    for p in lobby.players:
        if exclude_id and p["user_id"] == exclude_id:
            continue

        # Определение chat_id и текста
        target_chat_id = p["chat_id"]
        final_text = text

        # Если это ФЕЙК (отрицательный ID), добавляем пометку для Админа
        if p["user_id"] < 0:
            final_text = f"<b>[DEBUG for {p['name']}]</b>\n{text}"

        try:
            await bot.send_message(target_chat_id, final_text, parse_mode="HTML", reply_markup=reply_markup)
            # Небольшая задержка, чтобы Telegram не забанил за спам в один чат (когда много фейков)
            if p["user_id"] < 0:
                await asyncio.sleep(0.3)
        except:
            pass


async def process_multi_turn(lobby: Lobby, bot: Bot):
    """Главный цикл хода"""
    if not lobby.game_state: return

    gs = lobby.game_state
    players = lobby.game_players

    # Защита от выхода за границы
    if lobby.current_turn_index >= len(players):
        # Конец раунда/фазы
        if gs.phase == "presentation":
            gs.phase = "discussion"
            lobby.current_turn_index = 0
            await broadcast(lobby, f"⚔️ <b>ФАЗА 2: ОБСУЖДЕНИЕ</b>", bot)
            await asyncio.sleep(1)
            await process_multi_turn(lobby, bot)
            return
        elif gs.phase == "discussion":
            gs.phase = "voting"
            await start_multi_voting(lobby, bot)
            return
        return

    current_player = players[lobby.current_turn_index]
    actual_topic = get_display_topic(gs, current_player, lobby.catastrophe_data)

    # ХОД
    if current_player.is_human:
        # Ищем ID игрока по имени
        target_user = next((p for p in lobby.players if p["name"] == current_player.name), None)

        if target_user:
            await broadcast(lobby, f"👉 Ходит <b>{current_player.name}</b>...", bot, exclude_id=target_user["user_id"])

            msg_text = f"👤 <b>ТВОЙ ХОД!</b>\nТема: {actual_topic}\nНапиши сообщение в чат." \
                       f"\n<i>(Для фейка используй /fake_say текст)</i>"

            # Если фейк, шлем с пометкой
            if target_user["user_id"] < 0:
                msg_text = f"<b>[DEBUG for {target_user['name']}]</b>\n{msg_text}"

            await bot.send_message(target_user["chat_id"], msg_text, parse_mode="HTML")
            return
    else:
        # Ход бота
        await broadcast(lobby, f"🤖 <b>{current_player.name}</b> печатает...", bot)
        temp_gs = gs.model_copy()
        temp_gs.topic = actual_topic

        instr = await director_engine.get_hidden_instruction(current_player, players, temp_gs)
        speech = await bot_engine.make_turn(current_player, players, temp_gs, instr)

        gs.history.append(f"[{current_player.name}]: {speech}")

        display_name = GameSetup.get_display_name(current_player, gs.round)
        await broadcast(lobby, f"🤖 <b>{display_name}</b>:\n{speech}", bot)

        lobby.current_turn_index += 1
        await asyncio.sleep(2)
        await process_multi_turn(lobby, bot)


async def handle_human_message(lobby: Lobby, bot: Bot, text: str, user_name: str):
    """Обработка текста игрока (реального или фейка)"""
    # Проверка фазы
    if lobby.game_state.phase not in ["presentation", "discussion"]:
        return

    # Проверка индекса
    if lobby.current_turn_index >= len(lobby.game_players):
        return

    current_player = lobby.game_players[lobby.current_turn_index]

    if current_player.name != user_name:
        return

    lobby.game_state.history.append(f"[{current_player.name}]: {text}")
    await broadcast(lobby, f"👤 <b>{current_player.name}</b>:\n{text}", bot)

    lobby.current_turn_index += 1
    await process_multi_turn(lobby, bot)


# --- ЛОГИКА ГОЛОСОВАНИЯ ---

async def start_multi_voting(lobby: Lobby, bot: Bot):
    """Начинает фазу голосования"""
    lobby.votes.clear()

    # Формируем клавиатуру
    kb = InlineKeyboardBuilder()
    for p in lobby.game_players:
        if p.is_alive:
            kb.add(InlineKeyboardButton(text=f"☠ {p.name}", callback_data=f"mvote_{p.name}"))
    kb.adjust(1)
    markup = kb.as_markup()

    await broadcast(lobby, "🛑 <b>ГОЛОСОВАНИЕ ОБЪЯВЛЕНО</b>\nВыберите, кто покинет бункер.", bot, reply_markup=markup)

    # Сразу собираем голоса БОТОВ
    gs = lobby.game_state
    for p in lobby.game_players:
        if not p.is_human and p.is_alive:
            vote_target = await bot_engine.make_vote(p, [t for t in lobby.game_players if t.is_alive], gs)
            await handle_vote(lobby, bot, p.name, vote_target)


async def handle_vote(lobby: Lobby, bot: Bot, voter_name: str, target_name: str):
    """Принимает голос и проверяет, все ли проголосовали"""
    lobby.votes[voter_name] = target_name

    # Считаем живых участников
    alive_players = [p for p in lobby.game_players if p.is_alive]

    # Если все проголосовали
    if len(lobby.votes) >= len(alive_players):
        await finish_voting(lobby, bot)


async def finish_voting(lobby: Lobby, bot: Bot):
    """Подсчет итогов"""
    counts = Counter(lobby.votes.values())
    results = counts.most_common()

    if not results: return

    leader_name, leader_votes = results[0]
    result_text = "📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n"
    for name, cnt in counts.items():
        result_text += f"- {name}: {cnt}\n"

    # TODO: Обработка ничьей (пока просто кикаем первого)

    await broadcast(lobby, f"{result_text}\n🚪 <b>{leader_name}</b> изгнан.", bot)

    # Удаляем игрока (ставим is_alive=False)
    for p in lobby.game_players:
        if p.name == leader_name:
            p.is_alive = False
            break

    # Проверка условий победы
    humans_alive = any(p.is_human and p.is_alive for p in lobby.game_players)
    if not humans_alive:
        await broadcast(lobby, "💀 <b>GAME OVER</b>. Все люди погибли.", bot)
        lobby.status = "finished"
        return

    survivors_count = sum(1 for p in lobby.game_players if p.is_alive)
    if survivors_count <= cfg.gameplay["rounds"]["target_survivors"]:
        names = ", ".join([p.name for p in lobby.game_players if p.is_alive])
        await broadcast(lobby, f"🎉 <b>ПОБЕДА!</b> Выжили: {names}", bot)
        lobby.status = "finished"
        return

    # Новый раунд
    lobby.game_state.round += 1
    lobby.game_state.phase = "presentation"
    lobby.current_turn_index = 0
    lobby.votes.clear()

    # Обновляем топик
    base_topic = get_topic_base(lobby.game_state.round, "...", lobby.catastrophe_data)
    lobby.game_state.topic = base_topic

    await asyncio.sleep(3)
    await broadcast(lobby, f"🔔 <b>РАУНД {lobby.game_state.round}</b>\nТема: {base_topic}", bot)
    await process_multi_turn(lobby, bot)