from abc import ABC, abstractmethod
from typing import List, Dict
from src.core.schemas import BasePlayer, BaseGameState, GameEvent


class GameEngine(ABC):
    def __init__(self, lobby_id: str, host_name: str):
        self.lobby_id = lobby_id
        self.host_name = host_name
        self.players: List[BasePlayer] = []
        self.state: BaseGameState = None

    @abstractmethod
    async def init_game(self, users_data: List[Dict]) -> List[GameEvent]:
        """Асинхронная инициализация игры"""
        pass

    @abstractmethod
    async def process_turn(self) -> List[GameEvent]:
        pass

    @abstractmethod
    async def execute_bot_turn(self, bot_id: int, token: str) -> List[GameEvent]:
        pass

    @abstractmethod
    async def process_message(self, player_id: int, text: str) -> List[GameEvent]:
        pass

    @abstractmethod
    async def handle_action(self, player_id: int, action_data: str) -> List[GameEvent]:
        pass

    @abstractmethod
    async def player_leave(self, player_id: int) -> List[GameEvent]:
        pass

    @abstractmethod
    def get_player_view(self, viewer_id: int) -> str:
        pass