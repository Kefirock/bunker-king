import random
from typing import List
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer, BaseGameState
from src.games.bunker.config import bunker_cfg

# Новый, умный промпт Режиссера
DIRECTOR_SYSTEM_PROMPT = """
Ты — Режиссер психологического триллера "Бункер". Твоя цель — создавать драму, напряжение и интересные сюжетные повороты.

ТЕКУЩИЙ ИГРОК: {player_name} ({player_prof})
ХАРАКТЕР: {player_behavior}
ФАЗА ИГРЫ: {phase}
ТЕМА: {topic}

ПОДОЗРИТЕЛЬНЫЕ ЦЕЛИ (Кого можно атаковать):
{targets_list}

ИСТОРИЯ ЧАТА (Последние реплики):
{history}

ТВОЯ ЗАДАЧА:
Дай ОДНО короткое (макс 15 слов) скрытое указание игроку {player_name}.
Не используй всегда "Атакуй". Выбирай стратегию в зависимости от характера игрока:

1. PROVOKE (Провокация): Если игрок агрессивен, натрави его на подозрительную цель.
2. ALLY (Альянс): Если игрок слаб или паникер, прикажи ему искать защиты у сильного.
3. DOUBT (Сомнение): Посей зерно сомнения насчет честности лидера или самого активного игрока.
4. DEFLECT (Отвод глаз): Если сам игрок под ударом, прикажи ему перевести тему.

ВАЖНО:
- Указание должно быть скрытым мотивом ("Спроси, откуда у него еда", а не "Скажи: откуда еда").
- Учитывай характер: Моралиста проси давить на совесть, Прагматика — на выгоду.
"""


class DirectorAgent:
    def __init__(self):
        director_cfg = bunker_cfg.gameplay["director"]
        self.chaos_level = director_cfg["chaos_level"]
        self.inject_chance = director_cfg["inject_chance"]

    async def get_hidden_instruction(self,
                                     current_player: BasePlayer,
                                     all_players: List[BasePlayer],
                                     state: BaseGameState,
                                     logger=None) -> str:

        # 1. Шанс вмешательства
        # В фазе обсуждения вмешиваемся чаще
        chance = self.inject_chance
        if state.phase == "discussion": chance += 0.3
        if state.phase == "runoff": chance = 1.0  # В дуэли всегда даем советы

        if random.random() > chance:
            return ""

        # 2. Сбор данных
        # Ищем потенциальные цели для атаки (Лжецы, Бесполезные, или просто враги)
        suspicious_targets = []
        for p in all_players:
            if p.name == current_player.name: continue
            if not p.is_alive: continue

            # Проверяем статусы
            status = p.attributes.get("status", "NORMAL")
            score = sum(p.attributes.get("active_factors", {}).values())

            # Добавляем в список целей, если есть за что зацепиться
            if status in ["LIAR", "BIOHAZARD", "IMPOSTOR"] or score > 30:
                reason = status if status != "NORMAL" else "Suspicious"
                suspicious_targets.append(f"{p.name} ({reason})")

        targets_str = ", ".join(suspicious_targets) if suspicious_targets else "Нет явных врагов. Создай интригу."

        # Берем поведение из профиля (оно появилось в Итерации 2)
        player_behavior = current_player.attributes.get("personality", {}).get("behavior", "Act normally.")

        # 3. Формирование промпта
        prompt = DIRECTOR_SYSTEM_PROMPT.format(
            player_name=current_player.name,
            player_prof=current_player.attributes.get("profession", "Survivor"),
            player_behavior=player_behavior,
            phase=state.phase,
            topic=state.shared_data.get("topic", "Survival"),
            targets_list=targets_str,
            history="\n".join(state.history[-5:])
        )

        # 4. Запрос к LLM
        model = core_cfg.models["director_models"][0]

        instruction = await llm_client.generate(
            model_config=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,  # Высокая температура для креативности
            logger=logger
        )

        return instruction.strip()