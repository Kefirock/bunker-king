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


class FactConnection(BaseModel):
    """Связь между двумя фактами"""
    from_fact: str  # ID первого факта
    to_fact: str    # ID второго факта
    connection_type: str = "CONTRADICTS"  # CONTRADICTS или REVEALS
    reason: str = ""  # Почему связаны


class RedHerring(BaseModel):
    """Ложный след — подозрительная тайна, не связанная с убийством"""
    character_name: str
    suspicious_secret: str  # Что выглядит подозрительно
    actual_truth: str       # Что это на самом деле (не убийство)


class SuggestionData(BaseModel):
    logic_text: str
    defense_text: str
    bluff_text: str


class BotEmotion(BaseModel):
    """Эмоциональное состояние бота, влияющее на поведение"""
    fear: float = 0.0          # Страх (растёт при обвинении против бота)
    aggression: float = 0.0     # Агрессия (растёт при противоречиях)
    confidence: float = 0.5     # Уверенность (падает при вскрытии улик против)


class BotState(BaseModel):
    """Состояние бота, которое сохраняется между ходами"""
    made_statements: List[str] = Field(default_factory=list)  # Что уже говорил
    accused_others: List[str] = Field(default_factory=list)   # Кого обвинял
    revealed_facts: List[str] = Field(default_factory=list)   # Какие факты вскрыл
    suspicion_map: Dict[str, float] = Field(default_factory=dict)  # Кого подозревает (имя -> уровень)
    relations: Dict[str, str] = Field(default_factory=dict)   # Отношения: имя -> "ally"/"enemy"
    emotion: BotEmotion = Field(default_factory=BotEmotion)


class DetectivePlayerProfile(BaseModel):
    character_name: str = "Неизвестный"
    tag: str = "Гость"
    archetype: str = "Обыватель"
    legend: str = ""
    role: RoleType = RoleType.INNOCENT

    # НОВЫЕ ПОЛЯ
    is_finder: bool = False  # Тот, кто нашел тело (ходит первым)
    secret_objective: str = ""
    assigned_marker: str = ""
    starting_location: str = ""

    inventory: List[str] = Field(default_factory=list)
    published_facts_count: int = 0
    last_suggestions: Optional[SuggestionData] = None
    
    # Состояние для ботов (используется только для ботов)
    bot_state: Optional[BotState] = None


class DetectiveScenario(BaseModel):
    title: str
    description: str

    victim_name: str
    time_of_death: str

    apparent_cause: str
    real_cause: str

    location_of_body: str

    murder_method: str
    true_solution: str

    timeline_truth: str = ""

    all_facts: Dict[str, Fact] = Field(default_factory=dict)
    fact_graph: List[FactConnection] = Field(default_factory=list)  # Связи между фактами
    red_herrings: List[RedHerring] = Field(default_factory=list)  # Ложные следы


class DetectiveStateData(BaseModel):
    scenario: DetectiveScenario
    public_facts: List[str] = Field(default_factory=list)
    turn_count: int = 0
    current_round: int = 1
    max_rounds: int = 3