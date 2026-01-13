import asyncio
import random
from typing import List, Dict, Optional
from collections import Counter

from src.core.abstract_game import GameEngine
from src.core.schemas import BasePlayer, BaseGameState, GameEvent
from src.core.logger import SessionLogger

# Импорты
from src.games.bunker.config import bunker_cfg
from src.games.bunker.utils import BunkerUtils
from src.games.bunker.logic.bot_agent import BotAgent
from src.games.bunker.logic.judge_agent import JudgeAgent
from src.games.bunker.logic.director_agent import DirectorAgent


class BunkerGame(GameEngine):
    def __init__(self, lobby_id: str):
        super().__init__(lobby_id)
        self.logger = SessionLogger("Bunker", lobby_id)

        self.bot_agent = BotAgent()
        self.judge_agent = JudgeAgent()
        self.director_agent = DirectorAgent()

        self.current_turn_index = 0
        self.votes: Dict[str, str] = {}

    def init_game(self, users_data: List[Dict]) -> List[GameEvent]:
        self.players = BunkerUtils.generate_initial_players(users_data)

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

        events = []
        dash_text = BunkerUtils.generate_dashboard(
            self.state.shared_data["topic"],
            self.state.round,
            self.state.phase,
            [p for p in self.players if p.is_alive]
        )
        events.append(GameEvent(type="update_dashboard", content=dash_text))

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
        events = []

        # 1. Определяем список активных игроков (только живые)
        alive_players = [p for p in self.players if p.is_alive]

        # Если перестрелка - только кандидаты
        if self.state.phase == "runoff":
            candidates = self.state.shared_data["runoff_candidates"]
            active_list = [p for p in alive_players if p.name in candidates]
        else:
            active_list = alive_players

        # 2. Проверка: Если круг закончился -> следующая фаза
        if self.current_turn_index >= len(active_list):
            return await self._next_phase()

        current_player = active_list[self.current_turn_index]

        # 3. Ход ЧЕЛОВЕКА
        if current_player.is_human:
            # Личное уведомление
            msg = f"👉 <b>ВАШ ХОД!</b>\nНапишите сообщение в чат."
            events.append(GameEvent(type="message", target_ids=[current_player.id], content=msg))

            # Уведомление остальным
            others_ids = [p.id for p in self.players if p.id != current_player.id]
            if others_ids:
                events.append(GameEvent(type="message",
                                        target_ids=others_ids,
                                        content=f"⏳ Ходит <b>{current_player.name}</b>..."))
            return events

        # 4. Ход БОТА
        else:
            # Генерируем уникальный токен для этого сообщения
            msg_token = f"turn_{self.state.round}_{self.state.phase}_{self.current_turn_index}"

            # А. Отправляем "Печатает..." с токеном
            events.append(GameEvent(
                type="message",
                content=f"⏳ <b>{current_player.name}</b> печатает...",
                token=msg_token
            ))

            # Б. Генерируем ответ (это займет время)
            # Примечание: main.py отправит первое сообщение, а потом вызовет switch_turn,
            # но здесь мы делаем всё в одном вызове process_turn, поэтому задержка будет тут.
            # Чтобы визуально это выглядело красиво, мы вернем events СЕЙЧАС,
            # но нам нужно как-то вызвать генерацию ПОТОМ.
            # В текущей архитектуре мы просто подождем тут (асинхронно).

            instr = await self.director_agent.get_hidden_instruction(
                current_player, self.players, self.state, logger=self.logger
            )

            speech = await self.bot_agent.make_turn(
                current_player, self.players, self.state, instr, logger=self.logger
            )

            await self.judge_agent.analyze_move(
                current_player, speech, self.state.shared_data["topic"], logger=self.logger
            )

            self.state.history.append(f"[{current_player.name}]: {speech}")

            display_name = BunkerUtils.get_display_name(current_player, self.state.round)
            final_msg = f"{display_name}:\n{speech}"

            # В. Редактируем сообщение по токену
            events.append(GameEvent(
                type="edit_message",
                content=final_msg,
                token=msg_token
            ))

            # Г. Передаем ход
            self.current_turn_index += 1
            events.append(GameEvent(type="switch_turn"))

            return events

    async def process_message(self, player_id: int, text: str) -> List[GameEvent]:
        events = []

        # Найти игрока
        player = next((p for p in self.players if p.id == player_id), None)
        if not player or not player.is_alive: return []

        # Если сейчас фаза голосования - игнорируем текст (или пишем варнинг)
        if self.state.phase == "voting":
            return [GameEvent(type="message", target_ids=[player_id], content="🤫 Сейчас идет голосование!")]

        # Проверка очередности (Строгий режим)
        alive_players = [p for p in self.players if p.is_alive]
        if self.state.phase == "runoff":
            candidates = self.state.shared_data["runoff_candidates"]
            active_list = [p for p in alive_players if p.name in candidates]
        else:
            active_list = alive_players

        # Кто должен ходить сейчас?
        if self.current_turn_index < len(active_list):
            expected_player = active_list[self.current_turn_index]
            if expected_player.id != player_id:
                # Если пишет не тот, чья очередь -> просто игнорируем или шлем варнинг
                # (В старой версии было свободное общение в Discussion, но ты просил строгий порядок)
                return [GameEvent(type="message", target_ids=[player_id],
                                  content=f"⚠️ Сейчас очередь игрока {expected_player.name}!")]
        else:
            # Если индекс вышел за пределы (странная ситуация), просто выходим
            return []

        # Логика обработки
        self.state.history.append(f"[{player.name}]: {text}")
        await self.judge_agent.analyze_move(
            player, text, self.state.shared_data["topic"], logger=self.logger
        )

        # Рассылка всем, КРОМЕ автора (Фикс Эха)
        display_name = BunkerUtils.get_display_name(player, self.state.round)
        msg = f"{display_name}:\n{text}"

        targets = [p.id for p in self.players if p.id != player_id]
        if targets:
            events.append(GameEvent(type="message", target_ids=targets, content=msg))

        # Сдвигаем ход
        self.current_turn_index += 1

        # Автоматически дергаем switch_turn, чтобы проверить, не пора ли менять фазу
        events.append(GameEvent(type="switch_turn"))

        return events

    async def handle_action(self, player_id: int, action_data: str) -> List[GameEvent]:
        if not action_data.startswith("vote_"): return []
        if self.state.phase != "voting": return []

        target_name = action_data.split("_", 1)[1]
        player = next((p for p in self.players if p.id == player_id), None)
        if not player: return []

        # Записываем голос
        self.votes[player.name] = target_name

        # Подтверждение (исчезающее или редактируемое)
        events = [
            GameEvent(type="callback_answer", target_ids=[player_id], content=f"Голос принят: {target_name}")
        ]

        # Проверка: все ли живые проголосовали?
        alive_count = sum(1 for p in self.players if p.is_alive)
        if len(self.votes) >= alive_count:
            res_events = await self._finish_voting()
            events.extend(res_events)

        return events

    # --- Внутренние методы ---

    async def _next_phase(self) -> List[GameEvent]:
        events = []

        if self.state.phase == "presentation":
            self.state.phase = "discussion"
            self.current_turn_index = 0

            dash = BunkerUtils.generate_dashboard(self.state.shared_data["topic"], self.state.round, self.state.phase,
                                                  [p for p in self.players if p.is_alive])
            events.append(GameEvent(type="update_dashboard", content=dash))
            events.append(GameEvent(type="message",
                                    content="🗣 <b>ФАЗА ОБСУЖДЕНИЯ</b>\nАргументируйте, почему вы должны остаться."))
            events.append(GameEvent(type="switch_turn"))

        elif self.state.phase in ["discussion", "runoff"]:
            self.state.phase = "voting"
            events.extend(await self._start_voting_phase())

        return events

    async def _start_voting_phase(self) -> List[GameEvent]:
        self.votes.clear()
        self.state.phase = "voting"
        events = []

        dash = BunkerUtils.generate_dashboard(self.state.shared_data["topic"], self.state.round, self.state.phase,
                                              [p for p in self.players if p.is_alive])
        events.append(GameEvent(type="update_dashboard", content=dash))

        targets = []
        if self.state.shared_data["runoff_candidates"]:
            targets = [p for p in self.players if p.name in self.state.shared_data["runoff_candidates"]]
        else:
            targets = [p for p in self.players if p.is_alive]

        keyboard_data = []
        for t in targets:
            # Кнопка: "☠ Имя" -> callback "vote_Имя"
            keyboard_data.append({"text": f"☠ {t.name}", "callback_data": f"vote_{t.name}"})

        events.append(GameEvent(
            type="message",
            content="🛑 <b>ГОЛОСОВАНИЕ</b>\nВыберите, кто покинет бункер.",
            reply_markup=keyboard_data
        ))

        # Боты голосуют сразу
        for p in self.players:
            if not p.is_human and p.is_alive:
                vote = await self.bot_agent.make_vote(p, targets, self.state, logger=self.logger)
                self.votes[p.name] = vote

        # Если одни боты, завершаем сразу
        alive_count = sum(1 for p in self.players if p.is_alive)
        if len(self.votes) >= alive_count:
            events.extend(await self._finish_voting())

        return events

    async def _finish_voting(self) -> List[GameEvent]:
        events = []
        if not self.votes:
            return [GameEvent(type="message", content="Ошибка голосования.")]

        counts = Counter(self.votes.values())
        results = counts.most_common()

        leader_name, leader_votes = results[0]
        leaders = [name for name, count in results if count == leader_votes]

        res_text = "📊 <b>ИТОГИ:</b>\n"
        for name, cnt in counts.items():
            res_text += f"{name}: {cnt}\n"
        events.append(GameEvent(type="message", content=res_text))

        # НИЧЬЯ
        if len(leaders) > 1:
            if self.state.shared_data["runoff_count"] >= 1:
                events.append(GameEvent(type="game_over", content="Ничья дважды. Бункер закрыт."))
                return events

            self.state.phase = "runoff"
            self.state.shared_data["runoff_candidates"] = leaders
            self.state.shared_data["runoff_count"] += 1
            self.current_turn_index = 0

            events.append(GameEvent(type="message", content=f"⚖️ <b>НИЧЬЯ!</b> Перестрелка: {', '.join(leaders)}"))
            events.append(GameEvent(type="switch_turn"))
            return events

        # ИЗГНАНИЕ
        eliminated = None
        for p in self.players:
            if p.name.strip() == leader_name.strip():
                eliminated = p
                break

        if eliminated:
            eliminated.is_alive = False
            events.append(GameEvent(type="message", content=f"🚪 <b>{eliminated.name}</b> был изгнан."))

        # ПРОВЕРКА ПОБЕДЫ
        survivors = [p for p in self.players if p.is_alive]
        humans_alive = any(p.is_human for p in survivors)
        target_survivors = bunker_cfg.gameplay["rounds"]["target_survivors"]

        if not humans_alive:
            events.append(GameEvent(type="game_over", content="💀 Все люди погибли. GAME OVER."))
            return events

        if len(survivors) <= target_survivors:
            events.append(GameEvent(type="game_over",
                                    content=f"🎉 <b>ПОБЕДА!</b> Выжили: {', '.join([p.name for p in survivors])}"))
            return events

        # СЛЕДУЮЩИЙ РАУНД
        self.state.round += 1
        self.state.phase = "presentation"
        self.state.shared_data["runoff_candidates"] = []
        self.state.shared_data["runoff_count"] = 0
        self.current_turn_index = 0
        self.votes.clear()

        cat = self.state.shared_data["catastrophe"]
        idx = (self.state.round - 1) % len(cat["topics"])
        new_topic = cat["topics"][idx]
        self.state.shared_data["topic"] = f"Раунд {self.state.round}: {new_topic}"

        events.append(GameEvent(type="message", content=f"🔥 <b>РАУНД {self.state.round}</b>\nТема: {new_topic}"))
        events.append(GameEvent(type="switch_turn"))

        return events

    def get_player_view(self, viewer_id: int) -> str:
        return ""