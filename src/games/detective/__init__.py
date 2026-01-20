from src.core.registry import GameRegistry
from src.games.detective.game import DetectiveGame

# Саморегистрация Детектива
GameRegistry.register(
    game_id="detective",
    game_cls=DetectiveGame,
    display_name="🕵️‍♂️ Детектив"
)