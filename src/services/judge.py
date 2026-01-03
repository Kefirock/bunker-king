from src.config import cfg
from src.llm import llm
from src.schemas import PlayerProfile

class JudgeService:
    async def analyze_move(self,
                           player: PlayerProfile,
                           text: str,
                           topic: str,
                           logger=None) -> dict:

        prompt_template = cfg.prompts["judge"]["system"]
        system_prompt = prompt_template.format(
            name=player.name,
            profession=player.profession,
            trait=player.trait,
            text=text,
            topic=topic
        )

        judge_model = cfg.models["director_models"][0]

        response = await llm.generate(
            judge_model,
            [{"role": "system", "content": system_prompt}],
            temperature=0.1,
            json_mode=True,
            logger=logger
        )

        data = llm.parse_json(response)
        violation_type = data.get("violation_type", "none")
        argument_quality = data.get("argument_quality", "weak")

        logic_cfg = cfg.gameplay["decision_logic"]
        factors_weights = logic_cfg["factors"]
        mitigation = logic_cfg["mitigation"]

        score_change = 0

        if violation_type in factors_weights:
            base_weight = factors_weights[violation_type]
            current_val = player.active_factors.get(violation_type, 0)
            player.active_factors[violation_type] = max(current_val, base_weight)
            score_change += base_weight

        if argument_quality == "strong":
            multiplier = mitigation["strong_argument"]
            for key in player.active_factors:
                player.active_factors[key] = int(player.active_factors[key] * multiplier)
            score_change -= 10
        elif argument_quality == "bad":
            multiplier = mitigation["bad_argument"]
            for key in player.active_factors:
                player.active_factors[key] = int(player.active_factors[key] * multiplier)
            score_change += 10

        total_suspicion = sum(player.active_factors.values())

        if logger:
            logger.log_llm_interaction(
                service_name="JudgeService",
                model_id=judge_model.get("model_id"),
                prompt=[{"role": "system", "content": system_prompt}],
                response=response,
                is_json_mode=True
            )

            logger.log_game_event(
                "JUDGE_DECISION",
                f"Analyzed {player.name}",
                {
                    "violation": violation_type,
                    "quality": argument_quality,
                    "active_factors": player.active_factors,
                    "total": total_suspicion
                }
            )

        return {
            "score": score_change,
            "total_suspicion": total_suspicion,
            "type": violation_type,
            "comment": data.get("comment", ""),
            "new_facts": data.get("new_facts", [])
        }