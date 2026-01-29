from typing import List, Dict
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.games.detective.config import detective_cfg


class NarratorAgent:
    async def narrate(self, scenario_data: Dict, history: List[str], current_round: int, max_rounds: int,
                      logger=None) -> str:
        if len(history) < 3: return ""

        prompt_template = detective_cfg.prompts["narrator"]["system"]

        # Используем видимую причину
        cause_to_show = scenario_data.get("apparent_cause", "Неизвестно")

        prompt = prompt_template.format(
            title=scenario_data.get("title", ""),
            victim=scenario_data.get("victim_name", "Неизвестный"),
            cause=cause_to_show,

            history="\n".join(history[-5:]),
            current_round=current_round,
            max_rounds=max_rounds
        )

        model = core_cfg.models["director_models"][0]

        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,  # Снижена температура для реализма
                logger=logger
            )

            clean_text = response.strip().strip('"')
            return f"<i>*{clean_text}*</i>"
        except:
            return ""