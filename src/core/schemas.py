from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Union


class GameEvent(BaseModel):
    """
    Событие, которое Игра возвращает Ядру, чтобы то отправило сообщение.
    """
    type: str  # "message", "update_dashboard", "game_over", "switch_turn"
    target_ids: List[Union[int, str]] = []  # Кому отправлять (пустой список = всем)
    content: str = ""
    reply_markup: Optional[Any] = None  # Для клавиатур
    extra_data: Dict[str, Any] = {}


class BasePlayer(BaseModel):
    """
    Универсальный игрок.
    В attributes лежит всё специфичное: {"role": "Mafia"} или {"profession": "Doctor"}
    """
    id: int  # user_id или отрицательный для бота
    name: str
    is_human: bool = False
    is_alive: bool = True

    # Словарь для хранения специфики конкретной игры
    attributes: Dict[str, Any] = Field(default_factory=dict)

    # "Память" игрока (что он знает/видит)
    memory: List[str] = Field(default_factory=list)


class BaseGameState(BaseModel):
    """
    Универсальное состояние.
    """
    game_id: str
    round: int = 1
    phase: str = "init"  # Например: "day", "night", "discussion"
    history: List[str] = Field(default_factory=list)

    # Общее хранилище данных игры
    shared_data: Dict[str, Any] = Field(default_factory=dict)