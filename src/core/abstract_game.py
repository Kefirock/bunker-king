from abc import ABC, abstractmethod
from typing import List, Dict
from src.core.schemas import BasePlayer, BaseGameState, GameEvent


class GameEngine(ABC):
    def __init__(self, lobby_id: str):
        self.lobby_id = lobby_id
        self.players: List[BasePlayer] = []
        self.state: BaseGameState = None

    @abstractmethod
    def init_game(self, users_data: List[Dict]) -> List[GameEvent]:
        """
        Инициализация игры. Раздача ролей, генерация первого события.
        users_data: список [{"id": 123, "name": "User"}, ...]
        """
        pass

    @abstractmethod
    async def process_message(self, player_id: int, text: str) -> List[GameEvent]:
        """
        Обработка текстового сообщения от игрока.
        """
        pass

    @abstractmethod
    async def process_turn(self) -> List[GameEvent]:
        """
        Логика хода (особенно для ботов). Вызывается Ядром, когда пришло время.
        """
        pass

    @abstractmethod
    async def handle_action(self, player_id: int, action_data: str) -> List[GameEvent]:
        """
        Обработка нажатия кнопок (голосование и т.д.)
        """
        pass

    @abstractmethod
    def get_player_view(self, viewer_id: int) -> str:
        """
        Возвращает "картину мира" для конкретного игрока (учитывая туман войны).
        Нужно для LLM, чтобы бот понимал, что он видит.
        """
        pass