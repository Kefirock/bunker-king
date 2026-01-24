from enum import Enum
from typing import List, Dict, Optional
from pydantic import BaseModel, Field


# --- ENUMS ---

class FactType(str, Enum):
    PHYSICAL = "PHYSICAL"  # Вещдок
    TESTIMONY = "TESTIMONY"  # Показания
    MOTIVE = "MOTIVE"  # Мотив
    ALIBI = "ALIBI"  # Алиби/Обстоятельство


class RoleType(str, Enum):
    KILLER = "KILLER"
    INNOCENT = "INNOCENT"


class GamePhase(str, Enum):
    INIT = "INIT"
    BRIEFING = "BRIEFING"
    INVESTIGATION = "INVESTIGATION"
    FINAL_VOTE = "FINAL_VOTE"


# --- DATA MODELS ---

class Fact(BaseModel):
    id: str
    text: str
    keyword: str = "Улика"
    type: FactType
    is_public: bool = False
    source_player_id: Optional[int] = None


class SuggestionData(BaseModel):
    logic_text: str
    defense_text: str
    bluff_text: str


class DetectivePlayerProfile(BaseModel):
    character_name: str = "Неизвестный"
    archetype: str = "Обыватель"

    # НОВОЕ: Единая литературная легенда (кто я, кого знаю, что думаю)
    legend: str = ""

    role: RoleType = RoleType.INNOCENT
    secret_objective: str = ""
    inventory: List[str] = Field(default_factory=list)
    published_facts_count: int = 0
    last_suggestions: Optional[SuggestionData] = None


class DetectiveScenario(BaseModel):
    title: str
    description: str

    # НОВОЕ: Полицейский протокол
    victim_name: str
    time_of_death: str
    cause_of_death: str
    location_of_body: str

    murder_method: str  # Техническое описание для бота
    true_solution: str  # Полная разгадка

    all_facts: Dict[str, Fact] = Field(default_factory=dict)


class DetectiveStateData(BaseModel):
    scenario: DetectiveScenario
    public_facts: List[str] = Field(default_factory=list)
    turn_count: int = 0
    current_round: int = 1
    max_rounds: int = 3