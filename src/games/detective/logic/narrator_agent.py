from typing import List
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.games.detective.config import detective_cfg


class NarratorAgent:
    async def narrate(self, title: str, history: List[str], current_round: int, max_rounds: int) -> str:
        # Если истории мало, молчим
        if len(history) < 3: return ""

        prompt_template = detective_cfg.prompts["narrator"]["system"]

        prompt = prompt_template.format(
            title=title,
            history="\n".join(history[-5:]),
            current_round=current_round,  # <--- НОВОЕ
            max_rounds=max_rounds  # <--- НОВОЕ
        )

        model = core_cfg.models["director_models"][0]

        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9,
            )

            clean_text = response.strip().strip('"')
            return f"<i>*{clean_text}*</i>"
        except:
            return ""