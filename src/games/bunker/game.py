import asyncio
import random
from typing import List, Dict, Optional
from collections import Counter

from src.core.abstract_game import GameEngine
from src.core.schemas import BasePlayer, BaseGameState, GameEvent
from src.core.logger import SessionLogger

# Импорты специфики Бункера
from src.games.bunker.config import bunker_cfg
from src.games.bunker.utils import BunkerUtils
from src.games.bunker.logic.bot_agent import BotAgent
from src.games.bunker.logic.judge_agent import JudgeAgent
from src.games.bunker.logic.director_agent import DirectorAgent


class BunkerGame(GameEngine):
    def __init__(self, lobby_id: str):
        super().__init__(lobby_id)
        self.logger = SessionLogger("Bunker", lobby_id)

        # Агенты
        self.bot_agent = BotAgent()
        self.judge_agent = JudgeAgent()
        self.director_agent = DirectorAgent()

        # Внутренние счетчики
        self.current_turn_index = 0
        self.votes: Dict[str, str] = {}  # {voter_name: target_name}

    def init_game(self, users_data: List[Dict]) -> List[GameEvent]:
        """Запуск игры: генерация игроков, первого топика и дашборда"""
        self.players = BunkerUtils.generate_initial_players(users_data)

        # Выбираем сценарий
        catastrophe = random.choice(bunker_cfg.scenarios["catastrophes"])
        topic = catastrophe["topics"][0]

        self.state = BaseGameState(
            game_id=self.lobby_id,
            round=1,
            phase="presentation",
            shared_data={
                "topic": f"{catastrophe['name']}: {topic}",
                "catastrophe": catastrophe,
                "runoff_candidates": [],
                "runoff_count": 0
            }
        )

        # Формируем события для отправки в чат
        events = []

        # 1. Рассылка Дашборда (Закреп)
        dash_text = BunkerUtils.generate_dashboard(
            self.state.shared_data["topic"],
            self.state.round,
            self.state.phase,
            self.players
        )
        events.append(GameEvent(type="update_dashboard", content=dash_text))

        # 2. Личные досье (в ЛС)
        for p in self.players:
            if p.is_human:
                dossier = (f"📂 <b>ТВОЕ ДОСЬЕ:</b>\n"
                           f"Роль: {p.attributes['profession']}\n"
                           f"Черта: {p.attributes['trait']}\n"
                           f"Цель: Выжить.")
                events.append(GameEvent(type="message", target_ids=[p.id], content=dossier))

        events.append(GameEvent(type="message", content="☢️ <b>ИГРА НАЧАЛАСЬ!</b>"))

        return events

    async def process_turn(self) -> List[GameEvent]:
        """Обработка текущего хода. Вызывается из main.py циклично."""
        events = []

        # Проверка конца круга
        alive_players = [p for p in self.players if p.is_alive]
        if self.state.phase == "runoff":
            # В перестрелке участвуют только кандидаты
            candidates = self.state.shared_data["runoff_candidates"]
            active_list = [p for p in alive_players if p.name in candidates]
        else:
            active_list = alive_players

        if self.current_turn_index >= len(active_list):
            return await self._next_phase()

        current_player = active_list[self.current_turn_index]

        # Генерация события "Ход игрока"
        if current_player.is_human:
            msg = f"👉 <b>ВАШ ХОД!</b>\nНапишите сообщение в чат."
            events.append(GameEvent(type="message", target_ids=[current_player.id], content=msg))
            # Для остальных - уведомление
            events.append(GameEvent(type="message",
                                    content=f"⏳ Ходит <b>{current_player.name}</b>...",
                                    extra_data={"exclude_ids": [current_player.id]}))
            return events

        else:
            # === ХОД БОТА ===
            # 1. Уведомление "Печатает..."
            events.append(GameEvent(type="message", content=f"🤖 <b>{current_player.name}</b> пишет..."))

            # 2. Логика Режиссера (нужен ли вброс?)
            instr = await self.director_agent.get_hidden_instruction(
                current_player, self.players, self.state, logger=self.logger
            )

            # 3. Генерация речи
            speech = await self.bot_agent.make_turn(
                current_player, self.players, self.state, instr, logger=self.logger
            )

            # 4. Анализ Судьей (сразу проверяем сами себя, чтобы обновить факторы)
            await self.judge_agent.analyze_move(
                current_player, speech, self.state.shared_data["topic"], logger=self.logger
            )

            self.state.history.append(f"[{current_player.name}]: {speech}")

            # 5. Результат
            display_name = BunkerUtils.get_display_name(current_player, self.state.round)
            final_msg = f"{display_name}:\n{speech}"
            events.append(GameEvent(type="message", content=final_msg))

            # Переход к следующему
            self.current_turn_index += 1
            events.append(GameEvent(type="switch_turn"))  # Сигнал для main.py вызвать process_turn снова
            return events

    async def process_message(self, player_id: int, text: str) -> List[GameEvent]:
        """Обработка текста от человека"""
        events = []

        # Найти игрока
        player = next((p for p in self.players if p.id == player_id), None)
        if not player or not player.is_alive: return []

        # Проверка, его ли очередь (в упрощенном варианте)
        # Для простоты: если фаза не голосование - принимаем сообщение
        if self.state.phase == "voting":
            return [GameEvent(type="message", target_ids=[player_id], content="Сейчас идет голосование!")]

        # Логирование и Анализ
        self.state.history.append(f"[{player.name}]: {text}")
        await self.judge_agent.analyze_move(
            player, text, self.state.shared_data["topic"], logger=self.logger
        )

        # Рассылка всем
        display_name = BunkerUtils.get_display_name(player, self.state.round)
        msg = f"{display_name}:\n{text}"
        events.append(GameEvent(type="message", content=msg))

        # Если это был активный игрок в свою очередь - двигаем индекс
        # (Упрощенная проверка, в реальной игре надо строже)
        alive_players = [p for p in self.players if p.is_alive]
        if self.state.phase in ["presentation", "runoff"]:
            # В этих фазах строгая очередность
            current_turn_p = alive_players[self.current_turn_index] if self.current_turn_index < len(
                alive_players) else None
            if current_turn_p and current_turn_p.id == player_id:
                self.current_turn_index += 1
                events.append(GameEvent(type="switch_turn"))

        return events

    async def handle_action(self, player_id: int, action_data: str) -> List[GameEvent]:
        """Обработка кнопок (Голосование)"""
        if not action_data.startswith("vote_"): return []
        if self.state.phase != "voting": return []

        target_name = action_data.split("_", 1)[1]
        player = next((p for p in self.players if p.id == player_id), None)
        if not player: return []

        # Записываем голос
        self.votes[player.name] = target_name

        events = [
            GameEvent(type="callback_answer", target_ids=[player_id], content=f"Голос за {target_name}"),
            GameEvent(type="message", target_ids=[player_id], content=f"Вы проголосовали за: <b>{target_name}</b>")
        ]

        # Проверка: все ли проголосовали?
        alive_count = sum(1 for p in self.players if p.is_alive)
        if len(self.votes) >= alive_count:
            res_events = await self._finish_voting()
            events.extend(res_events)

        return events

    # --- Внутренние методы ---

    async def _next_phase(self) -> List[GameEvent]:
        """Смена фаз: Presentation -> Discussion -> Voting"""
        events = []

        if self.state.phase == "presentation":
            self.state.phase = "discussion"
            self.current_turn_index = 0

            # Обновляем дашборд
            dash = BunkerUtils.generate_dashboard(self.state.shared_data["topic"], self.state.round, self.state.phase,
                                                  self.players)
            events.append(GameEvent(type="update_dashboard", content=dash))
            events.append(
                GameEvent(type="message", content="🗣 <b>ФАЗА ОБСУЖДЕНИЯ</b>\nСпорьте, обвиняйте, защищайтесь."))
            # Запускаем ход (обсуждение тоже по кругу для порядка)
            events.append(GameEvent(type="switch_turn"))

        elif self.state.phase in ["discussion", "runoff"]:
            self.state.phase = "voting"
            events.extend(await self._start_voting_phase())

        return events

    async def _start_voting_phase(self) -> List[GameEvent]:
        self.votes.clear()
        self.state.phase = "voting"
        events = []

        # Обновляем дашборд
        dash = BunkerUtils.generate_dashboard(self.state.shared_data["topic"], self.state.round, self.state.phase,
                                              self.players)
        events.append(GameEvent(type="update_dashboard", content=dash))

        # Формируем клавиатуру для голосования
        # В abstract_game мы не зависели от aiogram, но тут придется вернуть структуру,
        # которую main.py превратит в клавиатуру.
        # Пусть это будет dict: {"text": "name", "callback": "vote_name"}

        targets = []
        if self.state.shared_data["runoff_candidates"]:
            targets = [p for p in self.players if p.name in self.state.shared_data["runoff_candidates"]]
        else:
            targets = [p for p in self.players if p.is_alive]

        keyboard_data = []
        for t in targets:
            keyboard_data.append({"text": f"☠ {t.name}", "callback_data": f"vote_{t.name}"})

        events.append(GameEvent(
            type="message",
            content="🛑 <b>ГОЛОСОВАНИЕ</b>\nВыберите, кто покинет бункер.",
            reply_markup=keyboard_data  # main.py должен это обработать
        ))

        # Заставляем ботов проголосовать
        for p in self.players:
            if not p.is_human and p.is_alive:
                vote = await self.bot_agent.make_vote(p, targets, self.state, logger=self.logger)
                self.votes[p.name] = vote

        # Проверяем, вдруг одни боты остались и голосование уже кончилось
        alive_count = sum(1 for p in self.players if p.is_alive)
        if len(self.votes) >= alive_count:
            events.extend(await self._finish_voting())

        return events

    async def _finish_voting(self) -> List[GameEvent]:
        events = []
        if not self.votes:
            # Если никто не проголосовал (баг или все ливнули)
            return [GameEvent(type="message", content="Никто не проголосовал.")]

        counts = Counter(self.votes.values())
        results = counts.most_common()

        leader_name, leader_votes = results[0]
        leaders = [name for name, count in results if count == leader_votes]

        # Текст итогов
        res_text = "📊 <b>ИТОГИ:</b>\n"
        for name, cnt in counts.items():
            res_text += f"{name}: {cnt}\n"
        events.append(GameEvent(type="message", content=res_text))

        # НИЧЬЯ
        if len(leaders) > 1:
            if self.state.shared_data["runoff_count"] >= 1:
                events.append(GameEvent(type="game_over", content="Ничья дважды. Бункер закрыт. Все погибли."))
                return events

            self.state.phase = "runoff"
            self.state.shared_data["runoff_candidates"] = leaders
            self.state.shared_data["runoff_count"] += 1
            self.current_turn_index = 0

            events.append(GameEvent(type="message", content=f"⚖️ <b>НИЧЬЯ!</b> Перестрелка: {', '.join(leaders)}"))
            events.append(GameEvent(type="switch_turn"))
            return events

        # ИЗГНАНИЕ
        # --- ФИКС: Более надежный поиск по имени ---
        eliminated = None
        for p in self.players:
            if p.name.strip() == leader_name.strip():
                eliminated = p
                break

        if eliminated:
            eliminated.is_alive = False
            # Важно: если человек говорил прямо перед смертью, история могла сохраниться
            # но в active_list следующего раунда он уже не попадет.
            events.append(GameEvent(type="message", content=f"🚪 <b>{eliminated.name}</b> был изгнан."))
        else:
            # Если имя не найдено (крайне редкий случай)
            events.append(
                GameEvent(type="message", content=f"⚠️ Ошибка: Не удалось найти игрока '{leader_name}' для изгнания."))

        # ПРОВЕРКА ПОБЕДЫ
        survivors = [p for p in self.players if p.is_alive]
        humans_alive = any(p.is_human for p in survivors)
        target_survivors = bunker_cfg.gameplay["rounds"]["target_survivors"]

        if not humans_alive:
            events.append(GameEvent(type="game_over", content="💀 Все люди погибли. GAME OVER."))
            return events

        if len(survivors) <= target_survivors:
            events.append(GameEvent(type="game_over",
                                    content=f"🎉 <b>ПОБЕДА!</b> Бункер укомплектован.\nВыжили: {', '.join([p.name for p in survivors])}"))
            return events

        # СЛЕДУЮЩИЙ РАУНД
        self.state.round += 1
        self.state.phase = "presentation"
        self.state.shared_data["runoff_candidates"] = []
        self.state.shared_data["runoff_count"] = 0
        self.current_turn_index = 0
        self.votes.clear()

        # Обновляем тему
        cat = self.state.shared_data["catastrophe"]
        idx = (self.state.round - 1) % len(cat["topics"])
        new_topic = cat["topics"][idx]
        self.state.shared_data["topic"] = f"Раунд {self.state.round}: {new_topic}"

        events.append(GameEvent(type="message", content=f"🔥 <b>РАУНД {self.state.round}</b>\nТема: {new_topic}"))
        events.append(GameEvent(type="switch_turn"))

        return events
    
    def get_player_view(self, viewer_id: int) -> str:
        # Для LLM пока не используется напрямую, так как BotAgent формирует это сам
        return ""