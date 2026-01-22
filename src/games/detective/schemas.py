from enum import Enum
from typing import List, Dict, Optional
from pydantic import BaseModel, Field

# --- ENUMS ---

class FactType(str, Enum):
    PHYSICAL = "PHYSICAL"     # Улика
    TESTIMONY = "TESTIMONY"   # Показания
    MOTIVE = "MOTIVE"         # Мотив
    ALIBI = "ALIBI"           # Алиби

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
    archetype: str = "Обыватель"          # НОВОЕ: Характер (напр. "Нервный параноик")
    relationships: str = "Нет связей"     # НОВОЕ: Описание отношений с жертвой и другими
    role: RoleType = RoleType.INNOCENT
    bio: str = ""
    secret_objective: str = ""
    inventory: List[str] = Field(default_factory=list)
    published_facts_count: int = 0
    last_suggestions: Optional[SuggestionData] = None

class DetectiveScenario(BaseModel):
    title: str
    description: str
    victim_name: str
    murder_method: str
    true_solution: str
    all_facts: Dict[str, Fact] = Field(default_factory=dict)

class DetectiveStateData(BaseModel):
    scenario: DetectiveScenario
    public_facts: List[str] = Field(default_factory=list)
    turn_count: int = 0