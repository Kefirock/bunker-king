import asyncio
from typing import List, Dict

from src.core.abstract_game import GameEngine
from src.core.schemas import BasePlayer, BaseGameState, GameEvent
from src.core.logger import SessionLogger

from src.games.detective.schemas import DetectiveStateData, DetectiveScenario, DetectivePlayerProfile, GamePhase
from src.games.detective.logic.scenario_gen import ScenarioGenerator
from src.games.detective.logic.suggestion_agent import SuggestionAgent
from src.games.detective.utils import DetectiveUtils


class DetectiveGame(GameEngine):
    def __init__(self, lobby_id: str, host_name: str):
        super().__init__(lobby_id, host_name)
        self.logger = SessionLogger("Detective", lobby_id, host_name)

        self.scenario_gen = ScenarioGenerator()
        self.suggestion_agent = SuggestionAgent()

        self.private_dash_tokens = {}

        # ИСПРАВЛЕНО: УБРАН loop.run_until_complete, ДОБАВЛЕН async/await

    async def init_game(self, users_data: List[Dict]) -> List[GameEvent]:
        names = [u["name"] for u in users_data]

        # Теперь это легальный асинхронный вызов
        scenario, profiles_map = await self.scenario_gen.generate(names)

        self.players = []
        for u in users_data:
            p = BasePlayer(id=u["id"], name=u["name"], is_human=True)
            prof = profiles_map.get(u["name"])
            p.attributes["detective_profile"] = prof
            self.players.append(p)

        self.state = BaseGameState(
            game_id=self.lobby_id,
            round=1,
            phase=GamePhase.BRIEFING,
            history=[],
            shared_data=DetectiveStateData(
                scenario=scenario,
                public_facts=[]
            ).dict()
        )

        events = []

        events.append(GameEvent(type="message", content=f"🕵️‍♂️ <b>ДЕЛО: {scenario.title}</b>\n{scenario.description}"))

        for p in self.players:
            events.extend(self._create_dashboard_update(p, is_new=True))

        return events

    async def process_turn(self) -> List[GameEvent]:
        return []

    async def process_message(self, player_id: int, text: str) -> List[GameEvent]:
        p = next((x for x in self.players if x.id == player_id), None)
        if not p: return []

        self.state.history.append(f"[{p.name}]: {text}")

        msg = f"<b>{p.name}</b>: {text}"

        others = [x.id for x in self.players if x.id != player_id]
        events = [GameEvent(type="message", target_ids=others, content=msg)]

        return events

    async def execute_bot_turn(self, bot_id: int, token: str) -> List[GameEvent]:
        return []

    async def handle_action(self, player_id: int, action_data: str) -> List[GameEvent]:
        p = next((x for x in self.players if x.id == player_id), None)
        if not p: return []

        if action_data.startswith("reveal_"):
            fid = action_data.split("_")[1]
            return await self._reveal_fact(p, fid)

        elif action_data == "refresh_suggestions":
            return await self._refresh_suggestions(p)

        return []

    # --- INTERNAL LOGIC ---

    async def _reveal_fact(self, player: BasePlayer, fact_id: str) -> List[GameEvent]:
        scen_data = self.state.shared_data["scenario"]
        all_facts = scen_data["all_facts"]
        fact = all_facts.get(fact_id)

        if not fact: return []
        if fact["is_public"]:
            return [GameEvent(type="callback_answer", target_ids=[player.id], content="Уже вскрыто!")]

        fact["is_public"] = True
        self.state.shared_data["public_facts"].append(fact_id)

        prof: DetectivePlayerProfile = player.attributes["detective_profile"]
        prof.published_facts_count += 1

        events = []
        msg = f"⚡ <b>НОВАЯ УЛИКА!</b>\nИгрок {player.name} вскрывает факт:\n\n📜 <b>{fact['text']}</b>"
        events.append(GameEvent(type="message", content=msg))
        events.extend(self._create_dashboard_update(player))

        return events

    async def _refresh_suggestions(self, player: BasePlayer) -> List[GameEvent]:
        events = [GameEvent(type="callback_answer", target_ids=[player.id], content="Думаю...")]

        scen_data = self.state.shared_data["scenario"]
        all_facts_dict = scen_data["all_facts"]
        all_facts_objs = {k: Fact(**v) for k, v in all_facts_dict.items()}

        pub_ids = self.state.shared_data["public_facts"]
        pub_facts = [all_facts_objs[fid] for fid in pub_ids if fid in all_facts_objs]

        sugg = await self.suggestion_agent.generate(
            player, self.state.history, pub_facts, all_facts_objs
        )

        player.attributes["detective_profile"].last_suggestions = sugg

        events.extend(self._create_dashboard_update(player))

        return events

    def _create_dashboard_update(self, player: BasePlayer, is_new=False) -> List[GameEvent]:
        scen_data = self.state.shared_data["scenario"]
        all_facts_dict = scen_data["all_facts"]
        all_facts_objs = {k: Fact(**v) for k, v in all_facts_dict.items()}

        text = DetectiveUtils.get_private_dashboard(player, all_facts_objs)
        kb = DetectiveUtils.get_inventory_keyboard(player, all_facts_objs)

        token = f"dash_{player.id}"

        if is_new:
            return [GameEvent(
                type="message",
                target_ids=[player.id],
                content=text,
                reply_markup=kb,
                token=token,
                extra_data={"is_dashboard": True}
            )]
        else:
            return [GameEvent(
                type="edit_message",
                target_ids=[player.id],
                content=text,
                reply_markup=kb,
                token=token
            )]

    def get_player_view(self, viewer_id: int) -> str:
        return "Detective View"

    async def player_leave(self, player_id: int) -> List[GameEvent]:
        return [GameEvent(type="message", content="Игрок ушел, но расследование продолжается...")]