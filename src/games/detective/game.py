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
        names = [u["name"] for u in users_data]

        try:
            scenario, profiles_map = await self.scenario_gen.generate(names)
        except ScenarioGenerationError as e:
            error_msg = f"❌ <b>ОШИБКА ЗАПУСКА</b>\n\nНейросеть не смогла сгенерировать стабильный сценарий после 3-х попыток.\nПричина: <i>{str(e)}</i>\n\nПожалуйста, попробуйте запустить лобби заново."
            return [GameEvent(type="game_over", content=error_msg)]

        self.players = []
        for u in users_data:
            is_human = u["id"] > 0 or u["id"] < -50000
            p = BasePlayer(id=u["id"], name=u["name"], is_human=is_human)
            prof = profiles_map.get(u["name"])
            p.attributes["detective_profile"] = prof
            self.players.append(p)

        # Авто-заполнение ботами
        target_players = 4
        if len(self.players) < target_players:
            needed = target_players - len(self.players)
            bot_names = DetectiveUtils.get_bot_names(needed)
            for i, b_name in enumerate(bot_names):
                self.players.append(BasePlayer(id=-100 - i, name=b_name, is_human=False))

        # Перемешивание
        import random
        random.shuffle(self.players)

        # Перегенерация сценария под новый состав (для консистентности имен)
        # В идеале нужно делать это до создания игроков, но для простоты MVP
        # мы просто оставим профили как есть (боты получат дефолтные профили, если их нет в мапе)
        # FIX: Чтобы боты не были "пустыми", нужно прогнать генерацию для ПОЛНОГО списка имен
        full_names = [p.name for p in self.players]
        try:
            scenario, profiles_map = await self.scenario_gen.generate(full_names)
        except ScenarioGenerationError:
            return [GameEvent(type="game_over", content="Ошибка генерации с ботами")]

        # Обновляем профили с учетом ботов
        for p in self.players:
            p.attributes["detective_profile"] = profiles_map.get(p.name, DetectivePlayerProfile())

        self.state = BaseGameState(
            game_id=self.lobby_id,
            round=1,
            phase=GamePhase.INVESTIGATION,
            history=[],
            shared_data=DetectiveStateData(
                scenario=scenario,
                public_facts=[]
            ).dict()
        )

        events = []
        events.append(GameEvent(type="message", content=f"🕵️‍♂️ <b>ДЕЛО: {scenario.title}</b>\n{scenario.description}"))
        events.append(GameEvent(type="message", content=f"👥 <b>Участники:</b> {', '.join(full_names)}"))
        events.append(GameEvent(type="message", content="💡 <i>Игра началась. Говорим по очереди!</i>"))

        return events

    # --- GAME LOOP ---

    async def process_turn(self) -> List[GameEvent]:
        if self.state.phase == GamePhase.FINAL_VOTE:
            return await self._process_voting_turn()

        if not self.players: return []

        events = []
        if self.current_turn_index == 0 and len(self.state.history) > 3:
            scen_title = self.state.shared_data["scenario"]["title"]
            narrative = await self.narrator_agent.narrate(scen_title, self.state.history)
            if narrative:
                events.append(GameEvent(type="message", content=narrative))

        self.current_turn_index = self.current_turn_index % len(self.players)
        current_player = self.players[self.current_turn_index]

        if not current_player.is_human:
            msg_token = f"turn_{self.state.turn_count}_{current_player.id}"
            self.state.shared_data["turn_count"] += 1
            events.append(
                GameEvent(type="message", content=f"⏳ <b>{current_player.name}</b> печатает...", token=msg_token))
            events.append(GameEvent(type="bot_think", token=msg_token, extra_data={"bot_id": current_player.id}))
            return events
        else:
            # Упрощенная строка без многострочного f-string во избежание ошибок
            msg = "👉 <b>ВАШ ХОД!</b>\nНапишите сообщение в чат, чтобы завершить ход."
            events.append(GameEvent(type="message", target_ids=[current_player.id], content=msg))

            # Эпизодический UI: Создаем новый дашборд
            await self._refresh_suggestions(current_player, silent=True)
            events.extend(self._create_dashboard_update(current_player, is_new=True))

            others = [p.id for p in self.players if p.id != current_player.id]
            if others:
                events.append(
                    GameEvent(type="message", target_ids=others, content=f"⏳ Ходит <b>{current_player.name}</b>..."))
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
            bot, self.players, scen_data, self.state.history, pub_facts, all_facts_objs
        )

        speech = decision.get("speech", "...")
        fact_to_reveal = decision.get("reveal_fact_id")

        events = []
        if fact_to_reveal:
            reveal_events = await self._reveal_fact(bot, fact_to_reveal)
            events.extend(reveal_events)

        self.state.history.append(f"[{bot.name}]: {speech}")
        final_msg = f"<b>{bot.name}</b>:\n{speech}"
        events.append(GameEvent(type="edit_message", content=final_msg, token=token))

        self.current_turn_index += 1
        events.append(GameEvent(type="switch_turn"))
        return events

    async def process_message(self, player_id: int, text: str) -> List[GameEvent]:
        p = next((x for x in self.players if x.id == player_id), None)
        if not p: return []

        if text.strip() == "/finish":
            if self.state.phase == GamePhase.INVESTIGATION:
                return await self._start_voting()
            else:
                return [GameEvent(type="message", target_ids=[player_id], content="Голосование уже идет!")]

        active_player = self.players[self.current_turn_index % len(self.players)]
        if p.id != active_player.id:
            return [GameEvent(type="message", target_ids=[player_id],
                              content=f"⚠️ <b>Не ваш ход!</b> Сейчас говорит {active_player.name}.")]

        self.state.history.append(f"[{p.name}]: {text}")
        msg = f"<b>{p.name}</b>: {text}"

        others = [x.id for x in self.players if x.id != player_id]
        events = [GameEvent(type="message", target_ids=others, content=msg)]

        self.current_turn_index += 1
        events.append(GameEvent(type="switch_turn"))
        return events

    async def handle_action(self, player_id: int, action_data: str) -> List[GameEvent]:
        p = next((x for x in self.players if x.id == player_id), None)
        if not p: return []

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

        # Упрощенная конкатенация строк
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

        fact["is_public"] = True
        self.state.shared_data["public_facts"].append(fact_id)
        prof.published_facts_count += 1

        events = []
        from src.games.detective.utils import FACT_TYPE_ICONS, FactType
        ftype = FactType(fact["type"])
        icon = FACT_TYPE_ICONS.get(ftype, "📄")

        # Упрощенная конкатенация строк
        msg = f"⚡ <b>НОВАЯ УЛИКА!</b>\nИгрок <b>{player.name}</b> выкладывает карту:\n\n{icon} <b>{fact['keyword']}</b>\n<i>{fact['text']}</i>"

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

        sugg = await self.suggestion_agent.generate(player, self.state.history, pub_facts, all_facts_objs)
        player.attributes["detective_profile"].last_suggestions = sugg
        return []

    async def _start_voting(self) -> List[GameEvent]:
        self.state.phase = GamePhase.FINAL_VOTE
        self.votes = {}
        events = [GameEvent(type="message", content="🛑 <b>СТОП ИГРА! ПРИШЛО ВРЕМЯ ОБВИНЕНИЙ.</b>\nКто убийца?")]
        candidates = [p for p in self.players]
        for p in self.players:
            if p.is_human:
                kb = []
                for cand in candidates:
                    kb.append({"text": cand.name, "callback_data": f"vote_{cand.name}"})
                events.append(GameEvent(type="message", target_ids=[p.id], content="👉 <b>Выберите виновного:</b>",
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
                vote_target = await self.bot_agent.make_vote(p, self.players, scen_data, self.state.history, pub_facts)
                self.votes[p.name] = vote_target

        if len(self.votes) == len(self.players):
            events.extend(await self._finish_game())
        return events

    async def _handle_human_vote(self, player: BasePlayer, target_name: str) -> List[GameEvent]:
        if player.name in self.votes:
            return [GameEvent(type="callback_answer", target_ids=[player.id], content="Голос уже принят")]
        self.votes[player.name] = target_name
        events = [
            GameEvent(type="callback_answer", target_ids=[player.id], content=f"Выбор: {target_name}"),
            GameEvent(type="message", target_ids=[player.id], content=f"🗳️ Ваш голос: <b>{target_name}</b>")
        ]
        if len(self.votes) == len(self.players):
            events.extend(await self._finish_game())
        else:
            events.append(GameEvent(type="switch_turn"))
        return events

    async def _finish_game(self) -> List[GameEvent]:
        events = []
        scen_data = self.state.shared_data["scenario"]
        counts = Counter(self.votes.values())
        results = counts.most_common()
        winner_name, votes_cnt = results[0]
        real_killer = next((p for p in self.players if p.attributes["detective_profile"].role == RoleType.KILLER), None)
        if not real_killer:
            real_killer_name = "Никто"
        else:
            real_killer_name = real_killer.name

        report = f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n"
        for name, cnt in counts.items():
            report += f"- {name}: {cnt}\n"

        # Упрощенная конкатенация
        report += f"\n🕵️‍♂️ <b>ОБВИНЯЕМЫЙ:</b> {winner_name}\n🔪 <b>НАСТОЯЩИЙ УБИЙЦА:</b> {real_killer_name}\n\n"

        if winner_name == real_killer_name:
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
        return [GameEvent(type="message", content="Игрок ушел, но расследование продолжается...")]