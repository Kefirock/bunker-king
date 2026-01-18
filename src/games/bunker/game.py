import asyncio
import random
from typing import List, Dict, Optional
from collections import Counter

from src.core.abstract_game import GameEngine
from src.core.schemas import BasePlayer, BaseGameState, GameEvent
from src.core.logger import SessionLogger

from src.games.bunker.config import bunker_cfg
from src.games.bunker.utils import BunkerUtils
from src.games.bunker.logic.bot_agent import BotAgent
from src.games.bunker.logic.judge_agent import JudgeAgent
from src.games.bunker.logic.director_agent import DirectorAgent


class BunkerGame(GameEngine):
    def __init__(self, lobby_id: str, host_name: str):
        super().__init__(lobby_id, host_name)
        self.logger = SessionLogger("Bunker", lobby_id, host_name)

        self.bot_agent = BotAgent()
        self.judge_agent = JudgeAgent()
        self.director_agent = DirectorAgent()

        self.current_turn_index = 0
        self.votes: Dict[str, str] = {}

    def init_game(self, users_data: List[Dict]) -> List[GameEvent]:
        self.players = BunkerUtils.generate_initial_players(users_data)

        catastrophe = random.choice(bunker_cfg.scenarios["catastrophes"])
        topic = self._get_global_topic(1, catastrophe)

        self.state = BaseGameState(
            game_id=self.lobby_id,
            round=1,
            phase="presentation",
            shared_data={
                "topic": topic,
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
                           f"Цель: Выжить любой ценой.")
                events.append(GameEvent(type="message", target_ids=[p.id], content=dossier))

        cat_name = catastrophe["name"]
        events.append(GameEvent(type="message", content=f"☢️ <b>ИГРА НАЧАЛАСЬ!</b>\nСценарий: <b>{cat_name}</b>"))
        return events

    # --- ЭТАП 1: ОБЪЯВЛЕНИЕ ХОДА ---
    async def process_turn(self) -> List[GameEvent]:
        events = []

        alive_players = [p for p in self.players if p.is_alive]
        if self.state.phase == "runoff":
            candidates = self.state.shared_data["runoff_candidates"]
            active_list = [p for p in alive_players if p.name in candidates]
        else:
            active_list = alive_players

        if self.current_turn_index >= len(active_list):
            return await self._next_phase()

        current_player = active_list[self.current_turn_index]
        personal_topic = self._get_personal_topic(current_player)

        # ХОД ЧЕЛОВЕКА
        if current_player.is_human:
            if self.state.phase == "presentation":
                msg = f"👉 <b>ВАШ ХОД!</b>\nТема: {personal_topic}"
            elif self.state.phase == "discussion":
                msg = f"👉 <b>ВАШ ХОД!</b>\nКого выгнать и почему? Назовите имя."
            elif self.state.phase == "runoff":
                msg = f"👉 <b>ВАШ ХОД!</b>\nЭто дуэль. Докажи, что ты полезнее врага!"
            else:
                msg = f"👉 <b>ВАШ ХОД!</b>"

            events.append(GameEvent(type="message", target_ids=[current_player.id], content=msg))

            others = [p.id for p in self.players if p.id != current_player.id]
            if others:
                events.append(
                    GameEvent(type="message", target_ids=others, content=f"⏳ Ходит <b>{current_player.name}</b>..."))
            return events

        # ХОД БОТА
        else:
            msg_token = f"turn_{self.state.round}_{self.state.phase}_{self.current_turn_index}"
            events.append(GameEvent(
                type="message",
                content=f"⏳ <b>{current_player.name}</b> печатает...",
                token=msg_token
            ))
            events.append(GameEvent(
                type="bot_think",
                token=msg_token,
                extra_data={"bot_id": current_player.id}
            ))
            return events

    # --- ЭТАП 2: ВЫПОЛНЕНИЕ ХОДА ---
    async def execute_bot_turn(self, bot_id: int, token: str) -> List[GameEvent]:
        bot = next((p for p in self.players if p.id == bot_id), None)
        if not bot: return []

        events = []
        personal_topic = self._get_personal_topic(bot)

        temp_shared = self.state.shared_data.copy()
        temp_shared["topic"] = personal_topic
        temp_state = self.state.model_copy()
        temp_state.shared_data = temp_shared

        try:
            instr = await self.director_agent.get_hidden_instruction(
                bot, self.players, temp_state, logger=self.logger
            )

            speech = await self.bot_agent.make_turn(
                bot, self.players, temp_state, instr, logger=self.logger
            )

            if not speech or speech == "...":
                speech = "*задумался и промолчал*"

            await self.judge_agent.analyze_move(
                bot, speech, personal_topic, self.state.round, logger=self.logger
            )

            self.state.history.append(f"[{bot.name}]: {speech}")

            display_name = BunkerUtils.get_display_name(bot, self.state.round)
            final_msg = f"{display_name}:\n{speech}"

            events.append(GameEvent(type="edit_message", content=final_msg, token=token))

        except Exception as e:
            print(f"🔥 Critical Bot Error: {e}")
            events.append(GameEvent(type="edit_message", content=f"⚠️ {bot.name} потерял связь.", token=token))

        self.current_turn_index += 1
        events.append(GameEvent(type="switch_turn"))
        return events

    async def process_message(self, player_id: int, text: str) -> List[GameEvent]:
        events = []
        player = next((p for p in self.players if p.id == player_id), None)
        if not player or not player.is_alive: return []

        if self.state.phase == "voting":
            return [GameEvent(type="message", target_ids=[player_id], content="🤫 Идет голосование!")]

        alive_players = [p for p in self.players if p.is_alive]
        if self.state.phase == "runoff":
            candidates = self.state.shared_data["runoff_candidates"]
            active_list = [p for p in alive_players if p.name in candidates]
        else:
            active_list = alive_players

        if self.current_turn_index < len(active_list):
            expected = active_list[self.current_turn_index]
            if expected.id != player_id:
                return [
                    GameEvent(type="message", target_ids=[player_id], content=f"⚠️ Сейчас очередь {expected.name}!")]
        else:
            return []

        self.state.history.append(f"[{player.name}]: {text}")

        personal_topic = self._get_personal_topic(player)
        await self.judge_agent.analyze_move(player, text, personal_topic, self.state.round, logger=self.logger)

        display_name = BunkerUtils.get_display_name(player, self.state.round)
        msg = f"{display_name}:\n{text}"

        others = [p.id for p in self.players if p.id != player_id]
        if others:
            events.append(GameEvent(type="message", target_ids=others, content=msg))

        self.current_turn_index += 1
        events.append(GameEvent(type="switch_turn"))
        return events

    async def handle_action(self, player_id: int, action_data: str) -> List[GameEvent]:
        if not action_data.startswith("vote_"): return []
        if self.state.phase != "voting": return []

        target_name = action_data.split("_", 1)[1]
        player = next((p for p in self.players if p.id == player_id), None)
        if not player: return []

        if player.name in self.votes:
            return [GameEvent(type="callback_answer", target_ids=[player_id], content="Вы уже голосовали")]

        self.votes[player.name] = target_name

        events = [
            GameEvent(type="callback_answer", target_ids=[player_id], content=f"Голос принят: {target_name}"),
            GameEvent(type="message", target_ids=[player_id], content=f"Вы -> <b>{target_name}</b>")
        ]

        alive_count = sum(1 for p in self.players if p.is_alive)
        if len(self.votes) >= alive_count:
            res_events = await self._finish_voting()
            events.extend(res_events)

        return events

    async def player_leave(self, player_id: int) -> List[GameEvent]:
        events = []
        player = next((p for p in self.players if p.id == player_id), None)
        if not player or not player.is_alive: return []

        player.is_alive = False

        # --- МАРКИРУЕМ ИСТОРИЮ ---
        self._mark_dead_in_history(player.name)
        # -------------------------

        events.append(GameEvent(type="message", content=f"🚪 <b>{player.name}</b> покинул игру."))

        survivors = [p for p in self.players if p.is_alive]
        humans_alive = any(p.is_human for p in survivors)
        target_survivors = bunker_cfg.gameplay["rounds"]["target_survivors"]

        if not humans_alive:
            report = BunkerUtils.generate_game_report(self.players, "💀 Все люди погибли. GAME OVER.")
            events.append(GameEvent(type="game_over", content=report))
            return events

        if len(survivors) <= target_survivors:
            report = BunkerUtils.generate_game_report(self.players, "🎉 <b>ПОБЕДА!</b> Бункер укомплектован.")
            events.append(GameEvent(type="game_over", content=report))
            return events

        dash = BunkerUtils.generate_dashboard(self.state.shared_data["topic"], self.state.round, self.state.phase,
                                              survivors)
        events.append(GameEvent(type="update_dashboard", content=dash))

        if self.state.phase == "voting":
            if player.name in self.votes:
                del self.votes[player.name]
            alive_count = len(survivors)
            if len(self.votes) >= alive_count:
                res = await self._finish_voting()
                events.extend(res)
        else:
            events.append(GameEvent(type="switch_turn"))

        return events

    # --- Внутренние методы ---

    def _mark_dead_in_history(self, player_name: str):
        """
        Переписывает историю чата, помечая реплики мертвого игрока тегом [DEAD].
        Также добавляет системное сообщение о смерти.
        """
        new_history = []
        prefix = f"[{player_name}]:"
        new_prefix = f"[{player_name} (DEAD)]:"

        for line in self.state.history:
            if line.startswith(prefix):
                new_history.append(line.replace(prefix, new_prefix, 1))
            else:
                new_history.append(line)

        self.state.history = new_history
        self.state.history.append(f">>> SYSTEM: {player_name} was ELIMINATED/LEFT. Do not talk to them. <<<")

    def _get_global_topic(self, round_num: int, catastrophe: dict) -> str:
        topics_cfg = bunker_cfg.gameplay["rounds"]["topics"]
        if round_num == 1:
            return topics_cfg[1].format(catastrophe=catastrophe["name"])
        elif round_num == 2:
            return topics_cfg[2].format(trait="Твоя черта")
        else:
            idx = (round_num - 3) % len(catastrophe["topics"])
            problem = catastrophe["topics"][idx]
            return topics_cfg[3].format(catastrophe_problem=problem)

    def _get_personal_topic(self, player: BasePlayer) -> str:
        if self.state.round == 2 and self.state.phase == "presentation":
            topics_cfg = bunker_cfg.gameplay["rounds"]["topics"]
            real_trait = player.attributes.get("trait", "???")
            return topics_cfg[2].format(trait=real_trait)
        return self.state.shared_data["topic"]

    async def _next_phase(self) -> List[GameEvent]:
        events = []
        if self.state.phase == "presentation":
            self.state.phase = "discussion"
            self.current_turn_index = 0

            dash = BunkerUtils.generate_dashboard(self.state.shared_data["topic"], self.state.round, self.state.phase,
                                                  [p for p in self.players if p.is_alive])
            events.append(GameEvent(type="update_dashboard", content=dash))
            events.append(GameEvent(type="message",
                                    content="🗣 <b>ФАЗА ОБСУЖДЕНИЯ</b>\nГлавный вопрос: <b>Против кого вы голосуете?</b>\nНазывайте имена."))
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

        candidates = []
        if self.state.shared_data["runoff_candidates"]:
            candidates = [p for p in self.players if p.name in self.state.shared_data["runoff_candidates"]]
        else:
            candidates = [p for p in self.players if p.is_alive]

        for p in self.players:
            if p.is_human and p.is_alive:
                my_targets = [t for t in candidates if t.name != p.name]
                if len(my_targets) == 1:
                    target = my_targets[0]
                    self.votes[p.name] = target.name
                    events.append(GameEvent(type="message", target_ids=[p.id],
                                            content=f"⚖️ Авто-голос против <b>{target.name}</b>"))
                else:
                    keyboard_data = []
                    for t in my_targets:
                        keyboard_data.append({"text": f"☠ {t.name}", "callback_data": f"vote_{t.name}"})
                    events.append(GameEvent(type="message", target_ids=[p.id], content="🛑 <b>ГОЛОСОВАНИЕ</b>",
                                            reply_markup=keyboard_data))

        for p in self.players:
            if not p.is_human and p.is_alive:
                vote = await self.bot_agent.make_vote(p, candidates, self.state, logger=self.logger)
                self.votes[p.name] = vote

        alive_count = sum(1 for p in self.players if p.is_alive)
        if len(self.votes) >= alive_count:
            events.extend(await self._finish_voting())

        return events

    async def _finish_voting(self) -> List[GameEvent]:
        events = []
        if not self.votes: return [GameEvent(type="message", content="Нет голосов.")]

        counts = Counter(self.votes.values())
        results = counts.most_common()
        leader_name, leader_votes = results[0]
        leaders = [name for name, count in results if count == leader_votes]

        res_text = "📊 <b>ИТОГИ:</b>\n"
        for name, cnt in counts.items():
            res_text += f"{name}: {cnt}\n"
        events.append(GameEvent(type="message", content=res_text))

        if len(leaders) > 1:
            if self.state.shared_data["runoff_count"] >= 1:
                report = BunkerUtils.generate_game_report(self.players, "⚖️ Ничья дважды. Бункер закрыт.")
                events.append(GameEvent(type="game_over", content=report))
                return events

            self.state.phase = "runoff"
            self.state.shared_data["runoff_candidates"] = leaders
            self.state.shared_data["runoff_count"] += 1
            self.current_turn_index = 0

            events.append(GameEvent(type="message", content=f"⚖️ <b>НИЧЬЯ!</b> Перестрелка: {', '.join(leaders)}"))
            events.append(GameEvent(type="switch_turn"))
            return events

        eliminated = None
        for p in self.players:
            if p.name.strip() == leader_name.strip():
                eliminated = p
                break

        if eliminated:
            eliminated.is_alive = False
            # --- МАРКИРУЕМ ИСТОРИЮ ---
            self._mark_dead_in_history(eliminated.name)
            # -------------------------
            events.append(GameEvent(type="message", content=f"🚪 <b>{eliminated.name}</b> был изгнан."))

        survivors = [p for p in self.players if p.is_alive]
        humans_alive = any(p.is_human for p in survivors)
        target_survivors = bunker_cfg.gameplay["rounds"]["target_survivors"]

        if not humans_alive:
            report = BunkerUtils.generate_game_report(self.players, "💀 Все люди погибли. GAME OVER.")
            events.append(GameEvent(type="game_over", content=report))
            return events

        if len(survivors) <= target_survivors:
            report = BunkerUtils.generate_game_report(self.players, "🎉 <b>ПОБЕДА!</b> Бункер укомплектован.")
            events.append(GameEvent(type="game_over", content=report))
            return events

        self.state.round += 1
        self.state.phase = "presentation"
        self.state.shared_data["runoff_candidates"] = []
        self.state.shared_data["runoff_count"] = 0
        self.current_turn_index = 0
        self.votes.clear()

        cat = self.state.shared_data["catastrophe"]
        new_topic = self._get_global_topic(self.state.round, cat)
        self.state.shared_data["topic"] = f"Раунд {self.state.round}: {new_topic}"

        events.append(GameEvent(type="message", content=f"🔥 <b>РАУНД {self.state.round}</b>"))
        events.append(GameEvent(type="switch_turn"))
        return events

    def get_player_view(self, viewer_id: int) -> str:
        return ""