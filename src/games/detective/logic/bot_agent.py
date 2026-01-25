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

        prompt = prompt_template.format(
            name=bot.name,
            tag=prof.tag,
            character_name=prof.character_name,
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
            # Динамическая температура
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

        # Исключаем себя из списка кандидатов (фикс само-голосования)
        valid_candidates = [p for p in candidates if p.id != bot.id]

        if not valid_candidates:
            # Если голосовать не за кого (остался один), возвращаем пустоту или себя (технически)
            return bot.attributes['detective_profile'].character_name

        if prof.role == RoleType.KILLER:
            # Убийца голосует случайно против любого другого
            target = random.choice(valid_candidates)
            target_char = target.attributes['detective_profile'].character_name
            if logger: logger.log_event("BOT_VOTE", f"{bot.name} (KILLER) auto-voted against {target_char}")
            return target_char

        pub_str = "; ".join([f"{f.text}" for f in public_facts])

        # Формируем список имен ПЕРСОНАЖЕЙ для промпта
        cand_str = ", ".join([
            f"{p.attributes['detective_profile'].character_name}"
            for p in valid_candidates
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
            target_char = data.get("vote_target_name", "")

            if logger: logger.log_event("BOT_VOTE_DECISION", f"{bot.name} voted against char {target_char}")

            # Проверяем, есть ли такой персонаж в валидных кандидатах
            if any(p.attributes['detective_profile'].character_name == target_char for p in valid_candidates):
                return target_char

            # Если LLM ошиблась с именем, берем случайного
            return random.choice(valid_candidates).attributes['detective_profile'].character_name

        except Exception as e:
            if logger: logger.log_event("BOT_VOTE_ERROR", str(e))
            return random.choice(valid_candidates).attributes['detective_profile'].character_name