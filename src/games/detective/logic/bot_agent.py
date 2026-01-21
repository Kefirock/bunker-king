import random
from typing import List, Dict, Any, Optional
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer
from src.games.detective.schemas import Fact, DetectivePlayerProfile, RoleType
from src.games.detective.config import detective_cfg


class DetectiveBotAgent:
    async def make_turn(self,
                        bot: BasePlayer,
                        all_players: List[BasePlayer],
                        scenario_data: Dict,
                        history: List[str],
                        public_facts: List[Fact],
                        all_facts_map: Dict[str, Fact]) -> Dict[str, Any]:

        prof: DetectivePlayerProfile = bot.attributes.get("detective_profile")
        if not prof: return {}

        pub_str = "; ".join([f"[{f.type}] {f.text}" for f in public_facts]) or "Нет"

        inv_lines = []
        for fid in prof.inventory:
            fact = all_facts_map.get(fid)
            if fact and not fact.is_public:
                inv_lines.append(f"ID: {fid} | [{fact.type}] {fact.text}")

        inv_str = "\n".join(inv_lines) if inv_lines else "Пусто (или все вскрыто)"

        # Загружаем промпт
        prompt_template = detective_cfg.prompts["bot_player"]["main"]

        prompt = prompt_template.format(
            name=bot.name,
            character_name=prof.character_name,  # <-- Передаем имя персонажа
            role=prof.role,
            bio=prof.bio,
            objective=prof.secret_objective,
            scenario_title=scenario_data.get("title", ""),
            scenario_desc=scenario_data.get("description", ""),
            public_facts=pub_str,
            inventory=inv_str,
            history="\n".join(history[-10:]),
            published_count=prof.published_facts_count
        )

        model = core_cfg.models["player_models"][0]

        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                json_mode=True
            )
            return llm_client.parse_json(response)
        except Exception as e:
            print(f"Bot Error {bot.name}: {e}")
            return {"speech": "...", "reveal_fact_id": None}

    async def make_vote(self,
                        bot: BasePlayer,
                        candidates: List[BasePlayer],
                        scenario_data: Dict,
                        history: List[str],
                        public_facts: List[Fact]) -> str:

        prof: DetectivePlayerProfile = bot.attributes.get("detective_profile")

        if prof.role == RoleType.KILLER:
            others = [p for p in candidates if p.id != bot.id]
            if others: return random.choice(others).name
            return candidates[0].name

        pub_str = "; ".join([f"{f.text}" for f in public_facts])

        # Формируем список кандидатов: "Имя (Персонаж)"
        cand_str = ", ".join([
            f"{p.name} ({p.attributes['detective_profile'].character_name})"
            for p in candidates
        ])

        prompt_template = detective_cfg.prompts["bot_player"]["vote"]

        prompt = prompt_template.format(
            name=bot.name,
            character_name=prof.character_name,  # <-- Передаем имя персонажа
            role=prof.role,
            scenario_title=scenario_data.get("title", ""),
            public_facts=pub_str,
            history="\n".join(history[-15:]),
            candidates=cand_str
        )

        model = core_cfg.models["player_models"][0]
        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                json_mode=True
            )
            data = llm_client.parse_json(response)
            target = data.get("vote_target_name", "")

            if any(p.name == target for p in candidates):
                return target
            return random.choice(candidates).name

        except:
            return random.choice(candidates).name