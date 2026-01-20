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

        # Кэш сообщений дашборда {player_id: message_token}
        # Чтобы мы могли их обновлять через edit_message
        self.private_dash_tokens = {}

    def init_game(self, users_data: List[Dict]) -> List[GameEvent]:
        # 1. Запуск генерации (синхронно ждем, так как init_game не async в базе,
        # но в main.py мы вызываем его без await. Тут нужен фикс архитектуры или run_until_complete.
        # Для простоты пока предполагаем, что scenario_gen быстрый или делаем заглушку)

        # ВНИМАНИЕ: В реальном коде лучше делать init_game async,
        # но сейчас мы сделаем loop.run_until_complete для совместимости
        loop = asyncio.get_event_loop()
        names = [u["name"] for u in users_data]
        scenario, profiles_map = loop.run_until_complete(self.scenario_gen.generate(names))

        # 2. Создаем игроков
        self.players = []
        for u in users_data:
            p = BasePlayer(id=u["id"], name=u["name"], is_human=True)
            # Прикрепляем профиль
            prof = profiles_map.get(u["name"])
            p.attributes["detective_profile"] = prof
            self.players.append(p)

        # 3. Состояние
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

        # 4. Приветствие и Личные дашборды
        events.append(GameEvent(type="message", content=f"🕵️‍♂️ <b>ДЕЛО: {scenario.title}</b>\n{scenario.description}"))

        # Рассылка личных дашбордов
        for p in self.players:
            events.extend(self._create_dashboard_update(p, is_new=True))

        return events

    async def process_turn(self) -> List[GameEvent]:
        # В детективе нет строгих ходов. Этот метод может вызываться для смены фаз.
        return []

    async def process_message(self, player_id: int, text: str) -> List[GameEvent]:
        p = next((x for x in self.players if x.id == player_id), None)
        if not p: return []

        self.state.history.append(f"[{p.name}]: {text}")

        # В детективе мы не блокируем очередь ходов. Все говорят одновременно.
        # Просто ретранслируем сообщение
        msg = f"<b>{p.name}</b>: {text}"

        # Создаем событие отправки всем (кроме автора, телеграм сам показывает автору)
        others = [x.id for x in self.players if x.id != player_id]
        events = [GameEvent(type="message", target_ids=others, content=msg)]

        return events

    async def execute_bot_turn(self, bot_id: int, token: str) -> List[GameEvent]:
        # Пока без ботов в MVP
        return []

    async def handle_action(self, player_id: int, action_data: str) -> List[GameEvent]:
        p = next((x for x in self.players if x.id == player_id), None)
        if not p: return []

        # 1. ВСКРЫТИЕ ФАКТА
        if action_data.startswith("reveal_"):
            fid = action_data.split("_")[1]
            return await self._reveal_fact(p, fid)

        # 2. ОБНОВЛЕНИЕ МЫСЛЕЙ (СУФЛЕР)
        elif action_data == "refresh_suggestions":
            return await self._refresh_suggestions(p)

        return []

    # --- INTERNAL LOGIC ---

    async def _reveal_fact(self, player: BasePlayer, fact_id: str) -> List[GameEvent]:
        # Получаем данные из state (приходится парсить обратно, т.к. shared_data это dict)
        scen_data = self.state.shared_data["scenario"]
        # В MVP мы не восстанавливаем полный объект DetectiveScenario каждый раз для скорости,
        # работаем с dict, но для типизации лучше восстановить.
        # Упрощение: ищем факт в dict
        all_facts = scen_data["all_facts"]
        fact = all_facts.get(fact_id)

        if not fact: return []
        if fact["is_public"]:
            return [GameEvent(type="callback_answer", target_ids=[player.id], content="Уже вскрыто!")]

        # Обновляем состояние
        fact["is_public"] = True
        self.state.shared_data["public_facts"].append(fact_id)

        # Обновляем профиль игрока
        prof: DetectivePlayerProfile = player.attributes["detective_profile"]
        prof.published_facts_count += 1

        events = []

        # 1. Анонс всем
        msg = f"⚡ <b>НОВАЯ УЛИКА!</b>\nИгрок {player.name} вскрывает факт:\n\n📜 <b>{fact['text']}</b>"
        events.append(GameEvent(type="message", content=msg))

        # 2. Обновляем личный дашборд игрока (кнопка пропадет)
        events.extend(self._create_dashboard_update(player))

        return events

    async def _refresh_suggestions(self, player: BasePlayer) -> List[GameEvent]:
        events = [GameEvent(type="callback_answer", target_ids=[player.id], content="Думаю...")]

        # Подготовка данных для агента
        scen_data = self.state.shared_data["scenario"]
        all_facts_dict = scen_data["all_facts"]
        # Превращаем dict обратно в объекты Fact для агента
        all_facts_objs = {k: Fact(**v) for k, v in all_facts_dict.items()}

        pub_ids = self.state.shared_data["public_facts"]
        pub_facts = [all_facts_objs[fid] for fid in pub_ids if fid in all_facts_objs]

        # Генерация
        sugg = await self.suggestion_agent.generate(
            player, self.state.history, pub_facts, all_facts_objs
        )

        # Сохраняем в профиль
        player.attributes["detective_profile"].last_suggestions = sugg

        # Обновляем дашборд
        events.extend(self._create_dashboard_update(player))

        return events

    def _create_dashboard_update(self, player: BasePlayer, is_new=False) -> List[GameEvent]:
        scen_data = self.state.shared_data["scenario"]
        all_facts_dict = scen_data["all_facts"]
        # Конвертация для утилит
        all_facts_objs = {k: Fact(**v) for k, v in all_facts_dict.items()}

        text = DetectiveUtils.get_private_dashboard(player, all_facts_objs)
        kb = DetectiveUtils.get_inventory_keyboard(player, all_facts_objs)

        token = f"dash_{player.id}"

        if is_new:
            # Если новый, мы отправляем message и запоминаем токен (в main.py)
            return [GameEvent(
                type="message",
                target_ids=[player.id],
                content=text,
                reply_markup=kb,
                token=token,
                extra_data={"is_dashboard": True}  # Чтобы запинилось
            )]
        else:
            # Обновляем существующий
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