import asyncio
import random
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
        # 1. СОЗДАНИЕ ИГРОКОВ (ЛЮДИ + БОТЫ)
        self.players = []

        # Добавляем людей
        for u in users_data:
            # ID < 0 но > -50000 это боты, ID < -50000 это фейки админа (считаем их людьми для теста)
            is_human = u["id"] > 0 or u["id"] < -50000
            self.players.append(BasePlayer(id=u["id"], name=u["name"], is_human=is_human))

        # Авто-заполнение ботами до 4-5 человек
        target_players = 4
        current_count = len(self.players)

        if current_count < target_players:
            needed = target_players - current_count
            bot_names = DetectiveUtils.get_bot_names(needed)

            for i, b_name in enumerate(bot_names):
                # ID для ботов: -100, -101...
                bot_id = -100 - i
                self.players.append(BasePlayer(id=bot_id, name=b_name, is_human=False))

        # 2. ПЕРЕМЕШИВАНИЕ (ВАЖНО!)
        # Перемешиваем список игроков ПЕРЕД генерацией сценария.
        # Это гарантирует, что убийцей может стать кто угодно, и порядок хода будет случайным.
        random.shuffle(self.players)

        # 3. ГЕНЕРАЦИЯ СЦЕНАРИЯ
        # Собираем имена уже перемешанных игроков
        names = [p.name for p in self.players]

        try:
            scenario, profiles_map = await self.scenario_gen.generate(names)
        except ScenarioGenerationError as e:
            return [GameEvent(type="game_over", content=f"❌ Ошибка сценария: {e}")]

        # 4. ПРИСВОЕНИЕ ПРОФИЛЕЙ
        for p in self.players:
            prof = profiles_map.get(p.name)
            if prof:
                p.attributes["detective_profile"] = prof
            else:
                # Fallback, если что-то пошло не так
                p.attributes["detective_profile"] = DetectivePlayerProfile()

        # 5. ИНИЦИАЛИЗАЦИЯ СОСТОЯНИЯ
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

        # Список участников
        players_list = ", ".join([p.name for p in self.players])
        events.append(GameEvent(type="message", content=f"👥 <b>Участники:</b> {players_list}"))
        events.append(GameEvent(type="message", content="💡 <i>Игра началась. Говорим по очереди!</i>"))

        # Дашборды только людям
        for p in self.players:
            if p.is_human:
                await self._refresh_suggestions(p, silent=True)
                events.extend(self._create_dashboard_update(p, is_new=True))

        return events

    # --- GAME LOOP ---

    async def process_turn(self) -> List[GameEvent]:
        if self.state.phase == GamePhase.FINAL_VOTE:
            return await self._process_voting_turn()

        if not self.players: return []

        # Нарратор в начале круга (когда индекс снова 0)
        events = []
        if self.current_turn_index == 0 and len(self.state.history) > 3:
            scen_title = self.state.shared_data["scenario"]["title"]
            narrative = await self.narrator_agent.narrate(scen_title, self.state.history)
            if narrative:
                events.append(GameEvent(type="message", content=narrative))

        # Безопасное получение игрока
        self.current_turn_index = self.current_turn_index % len(self.players)
        current_player = self.players[self.current_turn_index]

        # БОТ
        if not current_player.is_human:
            msg_token = f"turn_{self.state.turn_count}_{current_player.id}"
            self.state.shared_data["turn_count"] += 1
            events.append(
                GameEvent(type="message", content=f"⏳ <b>{current_player.name}</b> печатает...", token=msg_token))
            events.append(GameEvent(type="bot_think", token=msg_token, extra_data={"bot_id": current_player.id}))
            return events

        # ЧЕЛОВЕК
        else:
            msg = "👉 <b>ВАШ ХОД!</b>\nНапишите сообщение в чат."
            events.append(GameEvent(type="message", target_ids=[current_player.id], content=msg))

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

        # 1. PREVIEW (МОЖНО ВСЕГДА)
        if action_data.startswith("preview_"):
            fid = action_data.split("_")[1]
            return self._preview_fact(p, fid)

        # 2. REVEAL (ТОЛЬКО В СВОЙ ХОД)
        elif action_data.startswith("reveal_"):
            if not is_my_turn:
                return [GameEvent(type="callback_answer", target_ids=[player_id], content="Только в свой ход!")]
            fid = action_data.split("_")[1]
            return await self._reveal_fact(p, fid)

        # 3. REFRESH (МОЖНО ВСЕГДА)
        elif action_data == "refresh_suggestions":
            return await self._refresh_suggestions(p)

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

        text = (
            f"🕵️‍♂️ <b>ИЗУЧЕНИЕ УЛИКИ</b>\n\n"
            f"🏷 <b>{fact['keyword']}</b>\n"
            f"📜 <i>{fact['text']}</i>\n\n"
            f"❓ <b>Тип:</b> {type_name}\n\n"
            f"Вы хотите предъявить это обвинение всем?"
        )
        kb = [{"text": "📢 ОПУБЛИКОВАТЬ", "callback_data": f"reveal_{fact_id}"}]

        return [
            GameEvent(type="callback_answer", target_ids=[player.id], content="Загрузка..."),
            GameEvent(type="message", target_ids=[player.id], content=text, reply_markup=kb)
        ]

    async def _reveal_fact(self, player: BasePlayer, fact_id: str) -> List[GameEvent]:
        scen_data = self.state.shared_data["scenario"]
        all_facts = scen_data["all_facts"]
        fact = all_facts.get(fact_id)

        if not fact: return []
        if fact["is_public"]:
            return [GameEvent(type="callback_answer", target_ids=[player.id], content="Уже вскрыто!")]

        fact["is_public"] = True
        self.state.shared_data["public_facts"].append(fact_id)

        prof = player.attributes["detective_profile"]
        prof.published_facts_count += 1

        events = []
        from src.games.detective.utils import FACT_TYPE_ICONS, FactType
        ftype = FactType(fact["type"])
        icon = FACT_TYPE_ICONS.get(ftype, "📄")
        msg = (
            f"⚡ <b>НОВАЯ УЛИКА!</b>\n"
            f"Игрок <b>{player.name}</b> выкладывает карту:\n\n"
            f"{icon} <b>{fact['keyword']}</b>\n"
            f"<i>{fact['text']}</i>"
        )
        events.append(GameEvent(type="message", content=msg))
        events.append(GameEvent(type="callback_answer", target_ids=[player.id], content="Опубликовано!"))

        for p in self.players:
            if p.is_human:
                await self._refresh_suggestions(p, silent=True)
                events.extend(self._create_dashboard_update(p))

        return events

    async def _refresh_suggestions(self, player: BasePlayer, silent=False) -> List[GameEvent]:
        scen_data = self.state.shared_data["scenario"]
        all_facts_dict = scen_data["all_facts"]
        all_facts_objs = {k: Fact(**v) for k, v in all_facts_dict.items()}
        pub_ids = self.state.shared_data["public_facts"]
        pub_facts = [all_facts_objs[fid] for fid in pub_ids if fid in all_facts_objs]

        sugg = await self.suggestion_agent.generate(
            player, self.state.history, pub_facts, all_facts_objs
        )
        player.attributes["detective_profile"].last_suggestions = sugg

        if silent: return []
        # Если не silent, обновляем и сообщение дашборда
        events = [GameEvent(type="callback_answer", target_ids=[player.id], content="Мысли обновлены")]
        events.extend(self._create_dashboard_update(player))
        return events

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
        report += f"\n🕵️‍♂️ <b>ОБВИНЯЕМЫЙ:</b> {winner_name}\n"
        report += f"🔪 <b>НАСТОЯЩИЙ УБИЙЦА:</b> {real_killer_name}\n\n"
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