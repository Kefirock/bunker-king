from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Union


class GameEvent(BaseModel):
    """
    Событие, которое Игра возвращает Ядру.
    """
    type: str  # "message", "edit_message", "update_dashboard", "game_over", "switch_turn", "callback_answer"
    target_ids: List[Union[int, str]] = []  # Кому отправлять (пустой список = всем в лобби)

    content: str = ""
    reply_markup: Optional[Any] = None
    extra_data: Dict[str, Any] = {}

    # НОВОЕ ПОЛЕ: Уникальная метка сообщения (например "turn_bot_1")
    # Если указано, main.py запомнит ID сообщения под этим именем.
    token: Optional[str] = None


class BasePlayer(BaseModel):
    id: int
    name: str
    is_human: bool = False
    is_alive: bool = True
    attributes: Dict[str, Any] = Field(default_factory=dict)
    memory: List[str] = Field(default_factory=list)


class BaseGameState(BaseModel):
    game_id: str
    round: int = 1
    phase: str = "init"
    history: List[str] = Field(default_factory=list)
    shared_data: Dict[str, Any] = Field(default_factory=dict)