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
    tag: str = "Гость"
    archetype: str = "Обыватель"
    legend: str = ""
    role: RoleType = RoleType.INNOCENT

    # НОВЫЕ ПОЛЯ
    secret_objective: str = ""  # Вторичная цель (Вор, Любовник)
    assigned_marker: str = ""  # Физический маркер (Грязь, Запах)
    starting_location: str = ""  # Где был в момент убийства (из Алиби-матрицы)

    inventory: List[str] = Field(default_factory=list)
    published_facts_count: int = 0
    last_suggestions: Optional[SuggestionData] = None


class DetectiveScenario(BaseModel):
    title: str
    description: str

    # Полицейский протокол
    victim_name: str
    time_of_death: str
    cause_of_death: str
    location_of_body: str

    # Структурные данные
    tech_level: str = "1920s"  # Эпоха
    available_rooms: List[str] = []  # Список комнат в этом сценарии
    alibi_matrix: str = ""  # Текстовое описание кто с кем был

    murder_method: str
    true_solution: str

    all_facts: Dict[str, Fact] = Field(default_factory=dict)


class DetectiveStateData(BaseModel):
    scenario: DetectiveScenario
    public_facts: List[str] = Field(default_factory=list)
    turn_count: int = 0
    current_round: int = 1
    max_rounds: int = 3