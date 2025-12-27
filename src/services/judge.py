import logging
from src.config import cfg
from src.llm import llm
from src.schemas import PlayerProfile
from src.logger_service import game_logger


class JudgeService:
    async def analyze_move(self,
                           player: PlayerProfile,
                           text: str,
                           topic: str) -> dict:

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
            json_mode=True
        )

        data = llm.parse_json(response)

        violation_type = data.get("violation_type", "none")
        argument_quality = data.get("argument_quality", "weak")

        # Получаем настройки весов
        logic_cfg = cfg.gameplay["decision_logic"]
        factors_weights = logic_cfg["factors"]
        mitigation = logic_cfg["mitigation"]

        score_change = 0

        # 1. ОБНОВЛЕНИЕ ФАКТОРОВ (ГРЕХОВ)
        if violation_type in factors_weights:
            base_weight = factors_weights[violation_type]
            # Записываем или обновляем фактор
            # Если фактор уже есть, берем максимум (чтобы не спамить +100 за каждый чих, если уже есть вирус)
            current_val = player.active_factors.get(violation_type, 0)
            player.active_factors[violation_type] = max(current_val, base_weight)
            score_change += base_weight

        # 2. ЛОГИКА ОПРАВДАНИЯ (Mitigation)
        # Если аргумент сильный, снижаем ВСЕ факторы
        if argument_quality == "strong":
            multiplier = mitigation["strong_argument"]  # 0.5
            for key in player.active_factors:
                player.active_factors[key] = int(player.active_factors[key] * multiplier)
            score_change -= 10  # Бонус к общему скору

        elif argument_quality == "bad":
            # Если аргумент плохой, увеличиваем факторы немного
            multiplier = mitigation["bad_argument"]  # 1.2
            for key in player.active_factors:
                player.active_factors[key] = int(player.active_factors[key] * multiplier)
            score_change += 10

        # 3. Расчет общего подозрения
        total_suspicion = sum(player.active_factors.values())

        # Логируем
        game_logger.log_llm_interaction(
            service_name="JudgeService",
            model_id=judge_model.get("model_id"),
            prompt=[{"role": "system", "content": system_prompt}],
            response=response,
            is_json_mode=True
        )

        game_logger.log_game_event(
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