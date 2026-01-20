from enum import Enum
from typing import List, Dict, Optional
from pydantic import BaseModel, Field


# --- ENUMS ---

class FactType(str, Enum):
    PHYSICAL = "PHYSICAL"  # Улика (предмет, след)
    TESTIMONY = "TESTIMONY"  # Показания
    MOTIVE = "MOTIVE"  # Мотив (финансы, отношения)
    ALIBI = "ALIBI"  # Алиби (местонахождение)


class RoleType(str, Enum):
    KILLER = "KILLER"
    INNOCENT = "INNOCENT"


class GamePhase(str, Enum):
    INIT = "INIT"
    BRIEFING = "BRIEFING"  # Чтение ролей
    INVESTIGATION = "INVESTIGATION"  # Основной геймплей
    FINAL_VOTE = "FINAL_VOTE"  # Обвинение


# --- DATA MODELS ---

class Fact(BaseModel):
    id: str
    text: str
    type: FactType
    is_public: bool = False
    source_player_id: Optional[int] = None  # Кто изначально владел фактом (для истории)


class SuggestionData(BaseModel):
    """Структура для Суфлера (Copy-Paste)"""
    logic_text: str  # Вариант с упором на факты
    defense_text: str  # Вариант эмоциональной защиты
    bluff_text: str  # Вариант увода темы/блефа


class DetectivePlayerProfile(BaseModel):
    """
    Эта модель будет лежать внутри BasePlayer.attributes
    """
    role: RoleType = RoleType.INNOCENT
    bio: str = ""  # Легенда персонажа
    secret_objective: str = ""  # Личная цель (помимо выживания)

    inventory: List[str] = Field(default_factory=list)  # ID фактов в руке
    published_facts_count: int = 0  # Счетчик для правила "минимум 2 факта"

    last_suggestions: Optional[SuggestionData] = None  # Кэш подсказок суфлера


class DetectiveScenario(BaseModel):
    """Глобальный объект сценария (генерируется ИИ перед стартом)"""
    title: str
    description: str
    victim_name: str
    murder_method: str
    true_solution: str  # Кто убил и почему (для финала)

    # Полная колода фактов для этой партии
    all_facts: Dict[str, Fact] = Field(default_factory=dict)


class DetectiveStateData(BaseModel):
    """
    Эта модель будет лежать внутри BaseGameState.shared_data
    """
    scenario: DetectiveScenario
    public_facts: List[str] = Field(default_factory=list)  # ID вскрытых фактов
    turn_count: int = 0