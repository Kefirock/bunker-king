from typing import List
from src.core.llm import llm_client
from src.core.config import core_cfg

NARRATOR_PROMPT = """
Ты — Повествователь в детективной игре.
Твоя задача — одной короткой фразой (макс 20 слов) описать атмосферу в комнате, основываясь на последних репликах.

СЦЕНАРИЙ: {title}
ПОСЛЕДНИЕ СОБЫТИЯ:
{history}

СТИЛЬ: Нуар, напряжение, саспенс. Не давай подсказок, только эмоции и описание обстановки (погода, тишина, взгляды).
Пример: "За окном грянул гром, и повисшая тишина стала почти осязаемой."
"""

class NarratorAgent:
    async def narrate(self, title: str, history: List[str]) -> str:
        # Если истории мало, молчим
        if len(history) < 3: return ""

        prompt = NARRATOR_PROMPT.format(
            title=title,
            history="\n".join(history[-5:])
        )

        model = core_cfg.models["director_models"][0] # Используем модель режиссера

        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9, # Высокая температура для художественности
            )
            # ИСПРАВЛЕНО: Используем одинарные кавычки снаружи, чтобы внутри использовать двойные
            return f'<i>*{response.strip().strip('"')}*</i>'
        except:
            return ""