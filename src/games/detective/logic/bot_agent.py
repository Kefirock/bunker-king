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
                        all_facts_map: Dict[str, Fact],
                        current_round: int,
                        max_rounds: int,
                        logger=None) -> Dict[str, Any]:

        prof: DetectivePlayerProfile = bot.attributes.get("detective_profile")
        if not prof: return {}

        pub_str = "; ".join([f"[{f.type}] {f.text}" for f in public_facts]) or "Нет"

        inv_lines = []
        for fid in prof.inventory:
            fact = all_facts_map.get(fid)
            if fact and not fact.is_public:
                inv_lines.append(f"ID: {fid} | [{fact.type}] {fact.text}")

        inv_str = "\n".join(inv_lines) if inv_lines else "Пусто"

        prompt_template = detective_cfg.prompts["bot_player"]["main"]

        # ИСПРАВЛЕНО: Добавлен аргумент tag
        prompt = prompt_template.format(
            name=bot.name,
            tag=prof.tag,
            character_name=prof.character_name,
            archetype=prof.archetype,
            legend=prof.legend,
            objective=prof.secret_objective,

            scenario_title=scenario_data.get("title", ""),
            victim=scenario_data.get("victim_name", "Неизвестный"),
            cause=scenario_data.get("cause_of_death", "Неизвестно"),

            public_facts=pub_str,
            inventory=inv_str,
            history="\n".join(history[-10:]),
            published_count=prof.published_facts_count,
            current_round=current_round,
            max_rounds=max_rounds
        )

        model = core_cfg.models["player_models"][0]

        try:
            temp_boost = 0.2 * (current_round / max_rounds)
            temp = 0.7 + temp_boost

            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temp,
                json_mode=True,
                logger=logger
            )
            data = llm_client.parse_json(response)

            if logger:
                logger.log_event("BOT_DECISION", f"{bot.name} ({prof.character_name}) acted", data)

            return data
        except Exception as e:
            print(f"Bot Error {bot.name}: {e}")
            if logger: logger.log_event("BOT_ERROR", f"Error: {e}")
            return {"speech": "...", "reveal_fact_id": None}

    async def make_vote(self,
                        bot: BasePlayer,
                        candidates: List[BasePlayer],
                        scenario_data: Dict,
                        history: List[str],
                        public_facts: List[Fact],
                        logger=None) -> str:

        prof: DetectivePlayerProfile = bot.attributes.get("detective_profile")

        if prof.role == RoleType.KILLER:
            others = [p for p in candidates if p.id != bot.id]
            target = random.choice(others).name if others else candidates[0].name
            if logger: logger.log_event("BOT_VOTE", f"{bot.name} (KILLER) auto-voted against {target}")
            return target

        pub_str = "; ".join([f"{f.text}" for f in public_facts])

        cand_str = ", ".join([
            f"{p.name} ({p.attributes['detective_profile'].character_name})"
            for p in candidates
        ])

        prompt_template = detective_cfg.prompts["bot_player"]["vote"]

        prompt = prompt_template.format(
            character_name=prof.character_name,
            scenario_title=scenario_data.get("title", ""),

            victim=scenario_data.get("victim_name", "Неизвестный"),

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
                json_mode=True,
                logger=logger
            )
            data = llm_client.parse_json(response)
            target = data.get("vote_target_name", "")

            if logger: logger.log_event("BOT_VOTE_DECISION", f"{bot.name} voted", data)

            if any(p.name == target for p in candidates):
                return target
            return random.choice(candidates).name

        except Exception as e:
            if logger: logger.log_event("BOT_VOTE_ERROR", str(e))
            return random.choice(candidates).name