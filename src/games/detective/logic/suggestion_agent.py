from typing import List
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer
from src.games.detective.schemas import SuggestionData, Fact
from src.games.detective.config import detective_cfg


class SuggestionAgent:
    async def generate(self,
                       player: BasePlayer,
                       scenario_data: dict,
                       history: List[str],
                       public_facts: List[Fact],
                       all_facts_map: dict,
                       logger=None) -> SuggestionData:

        prof = player.attributes.get("detective_profile")
        if not prof: return SuggestionData(logic_text="...", defense_text="...", bluff_text="...")

        pub_txt = "; ".join([f.text for f in public_facts]) or "Нет"

        my_facts_txt = []
        for fid in prof.inventory:
            fact = all_facts_map.get(fid)
            if fact: my_facts_txt.append(f"[{fact.type}] {fact.text}")
        priv_txt = "; ".join(my_facts_txt) or "Пусто"

        prompt_template = detective_cfg.prompts["suggestion"]["system"]

        prompt = prompt_template.format(
            character_name=prof.character_name,
            tag=prof.tag,
            legend=prof.legend,

            victim=scenario_data.get("victim_name", "Неизвестный"),

            public_facts=pub_txt,
            private_facts=priv_txt,
            history="\n".join(history[-8:])
        )

        model = core_cfg.models["player_models"][0]

        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6,
                json_mode=True
            )
            data = llm_client.parse_json(response)
            return SuggestionData(
                logic_text=data.get("logic_text", ""),
                defense_text=data.get("defense_text", ""),
                bluff_text=data.get("bluff_text", "")
            )
        except Exception as e:
            if logger: logger.log_event("SUGGESTION_ERROR", str(e))
            return SuggestionData(logic_text="Ошибка AI", defense_text="...", bluff_text="...")