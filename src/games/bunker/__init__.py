from src.core.registry import GameRegistry
from src.games.bunker.game import BunkerGame

# Как только этот файл импортируется реестром, игра регистрируется
GameRegistry.register(
    game_id="bunker",
    game_cls=BunkerGame,
    display_name="☢️ Бункер"
)