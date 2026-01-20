import importlib
import pkgutil
from typing import Type, Dict, Optional
import src.games  # Импортируем пакет games, чтобы знать путь к нему
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

    @staticmethod
    def auto_discover():
        """
        Автоматически находит и импортирует все модули в папке src/games.
        Это вызывает код в __init__.py каждой игры, где происходит регистрация.
        """
        print("🔍 Scanning for games...")
        package = src.games
        prefix = package.__name__ + "."  # "src.games."

        # Сканируем подпапки в src/games
        for _, name, is_pkg in pkgutil.iter_modules(package.__path__, prefix):
            if is_pkg:
                try:
                    # Импортируем модуль (это триггерит __init__.py)
                    importlib.import_module(name)
                except Exception as e:
                    print(f"🔥 Failed to load game module {name}: {e}")