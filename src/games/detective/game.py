import asyncio
from collections import Counter
from typing import List, Dict

from src.core.abstract_game import GameEngine
from src.core.schemas import BasePlayer, BaseGameState, GameEvent
from src.core.logger import SessionLogger

from src.games.detective.schemas import DetectiveStateData, DetectiveScenario, DetectivePlayerProfile, GamePhase, Fact, \
    RoleType
from src.games.detective.logic.scenario_gen import ScenarioGenerator, ScenarioGenerationError
from src.games.detective.logic.suggestion_agent import SuggestionAgent
from src.games.detective.logic.bot_agent import DetectiveBotAgent
from src.games.detective.logic.narrator_agent import NarratorAgent
from src.games.detective.utils import DetectiveUtils
from src.games.detective.config import detective_cfg


class DetectiveGame(GameEngine):
    def __init__(self, lobby_id: str, host_name: str):
        super().__init__(lobby_id, host_name)
        self.logger = SessionLogger("Detective", lobby_id, host_name)

        self.scenario_gen = ScenarioGenerator()
        self.suggestion_agent = SuggestionAgent()
        self.bot_agent = DetectiveBotAgent()
        self.narrator_agent = NarratorAgent()

        self.current_turn_index = 0
        self.votes = {}

    async def init_game(self, users_data: List[Dict]) -> List[GameEvent]:
        self.logger.log_event("INIT_START", f"Init for {len(users_data)} users")
        names = [u["name"] for u in users_data]

        # 1. Генерация сценария (Сюжет + Роли)
        try:
            scenario, profiles_map = await self.scenario_gen.generate(names, logger=self.logger)
        except ScenarioGenerationError as e:
            self.logger.log_event("INIT_ERROR", f"Scenario failed: {e}")
            error_msg = f"❌ <b>ОШИБКА ЗАПУСКА</b>\n\nНейросеть не смогла сгенерировать стабильный сценарий.\nПричина: <i>{str(e)}</i>"
            return [GameEvent(type="game_over", content=error_msg)]

        # 2. Создание игроков
        self.players = []
        for u in users_data:
            is_human = u["id"] > 0 or u["id"] < -50000
            p = BasePlayer(id=u["id"], name=u["name"], is_human=is_human)
            p.attributes["detective_profile"] = profiles_map.get(u["name"], DetectivePlayerProfile())
            self.players.append(p)

        # 3. Авто-заполнение ботами
        target_players = detective_cfg.gameplay.get("setup", {}).get("total_players", 5)

        if len(self.players) < target_players:
            needed = target_players - len(self.players)
            bot_names = DetectiveUtils.get_bot_names(needed)
            for i, b_name in enumerate(bot_names):
                self.players.append(BasePlayer(id=-100 - i, name=b_name, is_human=False))
            self.logger.log_event("AUTO_FILL", f"Added {needed} bots")

        import random
        random.shuffle(self.players)

        # 4. Перегенерация для полного состава (чтобы у ботов были роли и связи)
        full_names = [p.name for p in self.players]
        try:
            scenario, profiles_map = await self.scenario_gen.generate(full_names, logger=self.logger)
        except ScenarioGenerationError as e:
            self.logger.log_event("INIT_ERROR", f"Full gen failed: {e}")
            return [GameEvent(type="game_over", content="Ошибка генерации ролей для ботов.")]

        for p in self.players:
            p.attributes["detective_profile"] = profiles_map.get(p.name, DetectivePlayerProfile())

        # Логируем роли
        roles_log = {p.name: p.attributes["detective_profile"].dict(include={'character_name', 'role'}) for p in
                     self.players}
        self.logger.log_event("ROLES", "Roles assigned", roles_log)

        max_rounds = detective_cfg.gameplay.get("setup", {}).get("max_rounds", 3)

        self.state = BaseGameState(
            game_id=self.lobby_id,
            round=1,
            phase=GamePhase.INVESTIGATION,
            history=[],
            shared_data=DetectiveStateData(
                scenario=scenario,
                public_facts=[],
                turn_count=0,
                current_round=1,
                max_rounds=max_rounds
            ).dict()
        )

        events = []

        # --- СТАРТОВЫЕ СООБЩЕНИЯ ---

        # А. Протокол
        protocol = (
            f"📄 <b>ПОЛИЦЕЙСКИЙ ПРОТОКОЛ</b>\n\n"
            f"👤 <b>Жертва:</b> {scenario.victim_name}\n"
            f"🕒 <b>Время:</b> {scenario.time_of_death}\n"
            f"📍 <b>Место:</b> {scenario.location_of_body}\n"
            f"💀 <b>Причина:</b> {scenario.cause_of_death}\n"
        )
        events.append(GameEvent(type="message", content=protocol))

        # Б. Вступление
        events.append(GameEvent(type="message", content=f"🕵️‍♂️ <b>ДЕЛО: {scenario.title}</b>\n{scenario.description}"))

        # В. Список персонажей (с Тегами)
        char_list = []
        for p in self.players:
            prof = p.attributes["detective_profile"]
            char_list.append(f"🔹 {prof.character_name} [{prof.tag}]")

        events.append(GameEvent(type="message", content=f"👥 <b>ПОДОЗРЕВАЕМЫЕ:</b>\n" + "\n".join(char_list)))
        events.append(GameEvent(type="message",
                                content=f"💡 <i>Игра началась. У вас есть <b>{max_rounds} круга</b> обсуждения.</i>"))

        return events

    # --- GAME LOOP ---

    async def process_turn(self) -> List[GameEvent]:
        if self.state.phase == GamePhase.FINAL_VOTE:
            return await self._process_voting_turn()

        if not self.players: return []

        events = []

        # Смена раунда при завершении круга
        if self.current_turn_index >= len(self.players):
            self.current_turn_index = 0
            self.state.shared_data["current_round"] += 1

            cur_round = self.state.shared_data["current_round"]
            max_round = self.state.shared_data["max_rounds"]

            self.logger.log_event("ROUND", f"Round {cur_round}/{max_round}")

            if cur_round > max_round:
                return await self._start_voting()

            events.append(GameEvent(type="message", content=f"🔔 <b>Раунд {cur_round}/{max_round}</b>"))

            # Нарратор с учетом контекста
            if len(self.state.history) > 3:
                scen_data = self.state.shared_data["scenario"]
                narrative = await self.narrator_agent.narrate(
                    scen_data,
                    self.state.history,
                    cur_round,
                    max_round,
                    logger=self.logger
                )
                if narrative:
                    events.append(GameEvent(type="message", content=narrative))

        current_player = self.players[self.current_turn_index]
        prof = current_player.attributes["detective_profile"]
        display_name = f"{prof.character_name} [{prof.tag}]"

        self.logger.log_event("TURN", f"Current turn: {current_player.name} ({display_name})")

        # ХОД БОТА
        if not current_player.is_human:
            t_count = self.state.shared_data["turn_count"]
            msg_token = f"turn_{t_count}_{current_player.id}"
            self.state.shared_data["turn_count"] += 1

            events.append(GameEvent(type="message", content=f"⏳ <b>{display_name}</b> пишет...", token=msg_token))
            events.append(GameEvent(type="bot_think", token=msg_token, extra_data={"bot_id": current_player.id}))
            return events

        # ХОД ЧЕЛОВЕКА
        else:
            msg = "👉 <b>ВАШ ХОД!</b>\nНапишите сообщение в чат."
            events.append(GameEvent(type="message", target_ids=[current_player.id], content=msg))

            await self._refresh_suggestions(current_player, silent=True)
            events.extend(self._create_dashboard_update(current_player, is_new=True))

            others = [p.id for p in self.players if p.id != current_player.id]
            if others:
                events.append(GameEvent(
                    type="message",
                    target_ids=others,
                    content=f"⏳ Ходит <b>{display_name}</b>..."
                ))
            return events

    async def execute_bot_turn(self, bot_id: int, token: str) -> List[GameEvent]:
        bot = next((p for p in self.players if p.id == bot_id), None)
        if not bot: return []

        scen_data = self.state.shared_data["scenario"]
        all_facts_dict = scen_data["all_facts"]
        all_facts_objs = {k: Fact(**v) for k, v in all_facts_dict.items()}
        pub_ids = self.state.shared_data["public_facts"]
        pub_facts = [all_facts_objs[fid] for fid in pub_ids if fid in all_facts_objs]

        decision = await self.bot_agent.make_turn(
            bot,
            self.players,
            scen_data,
            self.state.history,
            pub_facts,
            all_facts_objs,
            self.state.shared_data["current_round"],
            self.state.shared_data["max_rounds"],
            logger=self.logger
        )

        speech = decision.get("speech", "...")
        fact_to_reveal = decision.get("reveal_fact_id")

        events = []
        if fact_to_reveal:
            self.logger.log_event("BOT_ACTION", f"{bot.name} revealed fact {fact_to_reveal}")
            reveal_events = await self._reveal_fact(bot, fact_to_reveal)
            events.extend(reveal_events)

        prof = bot.attributes["detective_profile"]
        display_name = f"{prof.character_name} [{prof.tag}]"

        self.state.history.append(f"[{display_name}]: {speech}")

        final_msg = f"<b>{display_name}</b>:\n{speech}"
        events.append(GameEvent(type="edit_message", content=final_msg, token=token))

        self.current_turn_index += 1
        events.append(GameEvent(type="switch_turn"))
        return events

    async def process_message(self, player_id: int, text: str) -> List[GameEvent]:
        p = next((x for x in self.players if x.id == player_id), None)
        if not p: return []

        active_player = self.players[self.current_turn_index % len(self.players)]
        active_prof = active_player.attributes["detective_profile"]
        active_display = f"{active_prof.character_name} [{active_prof.tag}]"

        if p.id != active_player.id:
            return [GameEvent(
                type="message",
                target_ids=[player_id],
                content=f"⚠️ <b>Не ваш ход!</b> Сейчас говорит {active_display}."
            )]

        self.logger.log_event("CHAT", f"{p.name} -> {text}")

        my_prof = p.attributes["detective_profile"]
        my_display = f"{my_prof.character_name} [{my_prof.tag}]"

        self.state.history.append(f"[{my_display}]: {text}")

        msg = f"<b>{my_display}</b>: {text}"
        others = [x.id for x in self.players if x.id != player_id]
        events = [GameEvent(type="message", target_ids=others, content=msg)]

        self.current_turn_index += 1
        events.append(GameEvent(type="switch_turn"))
        return events

    async def handle_action(self, player_id: int, action_data: str) -> List[GameEvent]:
        p = next((x for x in self.players if x.id == player_id), None)
        if not p: return []

        self.logger.log_event("ACTION", f"{p.name} -> {action_data}")

        active_player = self.players[self.current_turn_index % len(self.players)]
        is_my_turn = (p.id == active_player.id)

        if action_data.startswith("preview_"):
            fid = action_data.split("_")[1]
            return self._preview_fact(p, fid)

        elif action_data.startswith("reveal_"):
            if not is_my_turn:
                return [GameEvent(type="callback_answer", target_ids=[player_id], content="Только в свой ход!")]

            fid = action_data.split("_")[1]
            return await self._reveal_fact(p, fid)

        elif action_data.startswith("vote_"):
            return await self._handle_human_vote(p, action_data.split("_")[1])

        return []

    # --- INTERNAL LOGIC ---

    def _preview_fact(self, player: BasePlayer, fact_id: str) -> List[GameEvent]:
        scen_data = self.state.shared_data["scenario"]
        fact = scen_data["all_facts"].get(fact_id)
        if not fact: return [GameEvent(type="callback_answer", target_ids=[player.id], content="Ошибка факта")]

        from src.games.detective.utils import FACT_TYPE_NAMES
        type_name = FACT_TYPE_NAMES.get(fact['type'], fact['type'])

        text = f"🕵️‍♂️ <b>ИЗУЧЕНИЕ УЛИКИ</b>\n\n🏷 <b>{fact['keyword']}</b>\n📜 <i>{fact['text']}</i>\n\n❓ <b>Тип:</b> {type_name}\n\nВы хотите предъявить это обвинение всем?"
        kb = [{"text": "📢 ОПУБЛИКОВАТЬ", "callback_data": f"reveal_{fact_id}"}]

        return [
            GameEvent(type="callback_answer", target_ids=[player.id], content="Загрузка..."),
            GameEvent(type="message", target_ids=[player.id], content=text, reply_markup=kb)
        ]

    async def _reveal_fact(self, player: BasePlayer, fact_id: str) -> List[GameEvent]:
        prof = player.attributes["detective_profile"]

        if prof.published_facts_count >= 2:
            return [GameEvent(type="callback_answer", target_ids=[player.id], content="⛔ Лимит исчерпан (макс 2)")]

        scen_data = self.state.shared_data["scenario"]
        all_facts = scen_data["all_facts"]
        fact = all_facts.get(fact_id)
        if not fact: return []
        if fact["is_public"]:
            return [GameEvent(type="callback_answer", target_ids=[player.id], content="Уже вскрыто!")]

        self.logger.log_event("FACT_REVEAL", f"{player.name} revealed {fact['text']}")

        fact["is_public"] = True
        self.state.shared_data["public_facts"].append(fact_id)
        prof.published_facts_count += 1

        events = []
        from src.games.detective.utils import FACT_TYPE_ICONS, FactType
        ftype = FactType(fact["type"])
        icon = FACT_TYPE_ICONS.get(ftype, "📄")

        char_name = prof.character_name
        tag = prof.tag
        msg = f"⚡ <b>НОВАЯ УЛИКА!</b>\n<b>{char_name}</b> [{tag}] выкладывает карту:\n\n{icon} <b>{fact['keyword']}</b>\n<i>{fact['text']}</i>"

        events.append(GameEvent(type="message", content=msg))
        events.append(GameEvent(type="callback_answer", target_ids=[player.id], content="Опубликовано!"))

        if player.is_human:
            events.extend(self._create_dashboard_update(player, is_new=False))

        return events

    async def _refresh_suggestions(self, player: BasePlayer, silent=False) -> List[GameEvent]:
        scen_data = self.state.shared_data["scenario"]
        all_facts_dict = scen_data["all_facts"]
        all_facts_objs = {k: Fact(**v) for k, v in all_facts_dict.items()}
        pub_ids = self.state.shared_data["public_facts"]
        pub_facts = [all_facts_objs[fid] for fid in pub_ids if fid in all_facts_objs]

        sugg = await self.suggestion_agent.generate(
            player,
            scen_data,
            self.state.history,
            pub_facts,
            all_facts_objs,
            logger=self.logger
        )
        player.attributes["detective_profile"].last_suggestions = sugg
        return []

    async def _start_voting(self) -> List[GameEvent]:
        self.logger.log_event("PHASE", "FINAL_VOTE STARTED")
        self.state.phase = GamePhase.FINAL_VOTE
        self.votes = {}
        events = [GameEvent(type="message",
                            content="🛑 <b>ВРЕМЯ ИСТЕКЛО! ПОЛИЦИЯ УЖЕ ЗДЕСЬ.</b>\nПришло время указать на убийцу.")]

        candidates = [p for p in self.players]

        for p in self.players:
            if p.is_human:
                kb = []
                for cand in candidates:
                    # НЕ ПОКАЗЫВАЕМ СЕБЯ (Голосовать против себя нельзя)
                    if cand.id == p.id: continue

                    prof = cand.attributes["detective_profile"]
                    btn_text = f"{prof.character_name} [{prof.tag}]"
                    kb.append({"text": btn_text, "callback_data": f"vote_{cand.name}"})

                events.append(
                    GameEvent(type="message", target_ids=[p.id], content="👉 <b>Кто совершил преступление?</b>",
                              reply_markup=kb))

        events.append(GameEvent(type="switch_turn"))
        return events

    async def _process_voting_turn(self) -> List[GameEvent]:
        events = []
        scen_data = self.state.shared_data["scenario"]
        all_facts_dict = scen_data["all_facts"]
        all_facts_objs = {k: Fact(**v) for k, v in all_facts_dict.items()}
        pub_ids = self.state.shared_data["public_facts"]
        pub_facts = [all_facts_objs[fid] for fid in pub_ids if fid in all_facts_objs]

        for p in self.players:
            if not p.is_human and p.name not in self.votes:
                # Бот возвращает Имя Персонажа (строка)
                vote_target_char = await self.bot_agent.make_vote(
                    p, self.players, scen_data, self.state.history, pub_facts, logger=self.logger
                )

                # Маппинг: Имя Персонажа -> Объект Игрока
                target_player = next((tp for tp in self.players if
                                      tp.attributes["detective_profile"].character_name == vote_target_char), None)

                # Если бот ошибся с именем, голосуем рандомно против кого-то другого
                if not target_player:
                    others = [op for op in self.players if op.id != p.id]
                    target_player = others[0] if others else p

                self.votes[p.name] = target_player.name
                self.logger.log_event("BOT_VOTE_MAPPED",
                                      f"{p.name} -> {target_player.name} (TargetChar: {vote_target_char})")

        if len(self.votes) == len(self.players):
            events.extend(await self._finish_game())
        return events

    async def _handle_human_vote(self, player: BasePlayer, target_name: str) -> List[GameEvent]:
        if player.name in self.votes:
            return [GameEvent(type="callback_answer", target_ids=[player.id], content="Голос уже принят")]

        self.logger.log_event("VOTE", f"{player.name} -> {target_name}")
        self.votes[player.name] = target_name

        target_p = next((p for p in self.players if p.name == target_name), None)
        target_char = target_p.attributes["detective_profile"].character_name if target_p else target_name

        events = [
            GameEvent(type="callback_answer", target_ids=[player.id], content=f"Выбор: {target_char}"),
            GameEvent(type="message", target_ids=[player.id], content=f"🗳️ Ваш голос: <b>{target_char}</b>")
        ]
        if len(self.votes) == len(self.players):
            events.extend(await self._finish_game())
        else:
            events.append(GameEvent(type="switch_turn"))
        return events

    async def _finish_game(self) -> List[GameEvent]:
        self.logger.log_event("GAME_END", "Tallying votes")
        events = []
        scen_data = self.state.shared_data["scenario"]
        counts = Counter(self.votes.values())
        results = counts.most_common()
        winner_name, votes_cnt = results[0]

        real_killer = next((p for p in self.players if p.attributes["detective_profile"].role == RoleType.KILLER), None)
        real_killer_char = real_killer.attributes["detective_profile"].character_name if real_killer else "Никто"

        winner_p = next((p for p in self.players if p.name == winner_name), None)
        winner_char = winner_p.attributes["detective_profile"].character_name if winner_p else winner_name

        self.logger.log_event("RESULT", f"Accused: {winner_name}, Real: {real_killer.name if real_killer else 'None'}")

        report = f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n"
        for name, cnt in counts.items():
            p = next((p for p in self.players if p.name == name), None)
            c_name = p.attributes["detective_profile"].character_name if p else name
            report += f"- {c_name}: {cnt}\n"

        report += f"\n🕵️‍♂️ <b>ОБВИНЯЕМЫЙ:</b> {winner_char}\n🔪 <b>НАСТОЯЩИЙ УБИЙЦА:</b> {real_killer_char}\n\n"

        if winner_name == real_killer.name:
            report += "🎉 <b>ПОБЕДА ДЕТЕКТИВОВ!</b> Преступник пойман."
        else:
            report += "💀 <b>ПОБЕДА УБИЙЦЫ!</b> Вы обвинили невиновного."

        report += f"\n\n📜 <b>РАЗГАДКА:</b>\n{scen_data['true_solution']}"
        events.append(GameEvent(type="game_over", content=report))
        return events

    def _create_dashboard_update(self, player: BasePlayer, is_new=False) -> List[GameEvent]:
        scen_data = self.state.shared_data["scenario"]
        all_facts_dict = scen_data["all_facts"]
        all_facts_objs = {k: Fact(**v) for k, v in all_facts_dict.items()}
        text = DetectiveUtils.get_private_dashboard(player, all_facts_objs)
        kb = DetectiveUtils.get_inventory_keyboard(player, all_facts_objs)
        token = f"dash_{player.id}"
        if is_new:
            return [GameEvent(type="message", target_ids=[player.id], content=text, reply_markup=kb, token=token,
                              extra_data={"is_dashboard": True})]
        else:
            return [GameEvent(type="edit_message", target_ids=[player.id], content=text, reply_markup=kb, token=token)]

    def get_player_view(self, viewer_id: int) -> str:
        return "Detective View"

    async def player_leave(self, player_id: int) -> List[GameEvent]:
        p = next((x for x in self.players if x.id == player_id), None)
        if not p: return []
        char_name = p.attributes["detective_profile"].character_name
        self.logger.log_event("PLAYER_LEFT", f"{p.name} ({char_name}) left")
        return [GameEvent(type="message", content=f"🚪 {char_name} покинул комнату...")]