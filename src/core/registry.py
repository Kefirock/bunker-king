from typing import Type, Dict, Optional
from src.core.abstract_game import GameEngine

class GameRegistry:
    _games: Dict[str, Type[GameEngine]] = {}
    _display_names: Dict[str, str] = {}

    @classmethod
    def register(cls, game_id: str, game_cls: Type[GameEngine], display_name: str):
        """
        Регистрирует класс игры.
        :param game_id: Уникальный ID (например, 'bunker')
        :param game_cls: Класс игры (наследник GameEngine)
        :param display_name: Красивое имя для кнопок (например, '☢️ Бункер')
        """
        cls._games[game_id] = game_cls
        cls._display_names[game_id] = display_name
        print(f"🎮 Game registered: {display_name} ({game_id})")

    @classmethod
    def get_game_class(cls, game_id: str) -> Optional[Type[GameEngine]]:
        return cls._games.get(game_id)

    @classmethod
    def get_all_games(cls) -> Dict[str, str]:
        """Возвращает словарь {id: display_name} для меню"""
        return cls._display_names

    @classmethod
    def exists(cls, game_id: str) -> bool:
        return game_id in cls._games