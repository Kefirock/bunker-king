from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Literal


class Persona(BaseModel):
    id: str
    description: str
    style_example: str
    # Персональные множители страхов (из scenarios.yaml)
    multipliers: Dict[str, float] = Field(default_factory=dict)


class PlayerProfile(BaseModel):
    """Истинное досье игрока"""
    name: str
    profession: str
    trait: str
    personality: Persona
    is_human: bool = False
    is_alive: bool = True

    suspicion_score: int = 0
    status: Literal["NORMAL", "SUSPICIOUS", "LIAR", "IMPOSTOR"] = "NORMAL"

    # НОВОЕ: Активные факторы опасности (Tag -> Current Value)
    # Пример: {"biohazard": 100, "liar": 30}
    active_factors: Dict[str, int] = Field(default_factory=dict)

    # Конфигурация модели
    llm_config: Optional[Dict] = None


class PublicPlayerInfo(BaseModel):
    """То, что видят другие игроки (через LLM)"""
    name: str
    profession: Optional[str] = None
    trait: Optional[str] = None
    status: Optional[str] = None

    # Добавляем видимые факторы для промптов
    known_factors: List[str] = []

    def __str__(self):
        """
        Формирует описание игрока для ПРОМПТА БОТА.
        ВАЖНО: Не добавлять сюда технические теги в скобках, иначе боты их читают.
        """
        prof = self.profession if self.profession else "???"
        tr = f", {self.trait}" if self.trait else ""

        # Заменяем CAPS-статусы на литературное описание для ИИ
        st = ""
        if self.status == "SUSPICIOUS":
            st = " (Ведет себя подозрительно)"
        elif self.status == "LIAR":
            st = " (Пойман на лжи)"
        elif self.status == "IMPOSTOR":
            st = " (Явная угроза)"

        return f"- {self.name}: {prof}{tr}{st}"


class GameState(BaseModel):
    round: int = 1
    phase: Literal["presentation", "discussion", "voting", "runoff"] = "presentation"
    topic: str
    history: List[str] = []

    runoff_candidates: List[str] = []
    runoff_count: int = 0

    claimed_facts: Dict[str, List[str]] = {}


class DirectorTrigger(BaseModel):
    type: Literal["inject", "twist", "whisper"]
    target: Optional[str] = None
    content: str