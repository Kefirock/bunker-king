from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer
from src.games.bunker.config import bunker_cfg


class JudgeAgent:
    async def analyze_move(self, player: BasePlayer, text: str, topic: str, logger=None) -> dict:
        attrs = player.attributes
        name = player.name
        prof = attrs.get("profession", "Неизвестно")
        trait = attrs.get("trait", "Неизвестно")

        prompt_template = bunker_cfg.prompts["judge"]["system"]
        system_prompt = prompt_template.format(
            name=name,
            profession=prof,
            trait=trait,
            text=text,
            topic=topic
        )

        judge_model = core_cfg.models["director_models"][0]

        response = await llm_client.generate(
            model_config=judge_model,
            messages=[{"role": "system", "content": system_prompt}],
            temperature=0.1,
            json_mode=True,
            logger=logger
        )

        data = llm_client.parse_json(response)

        violation_type = data.get("violation_type", "none")
        argument_quality = data.get("argument_quality", "weak")
        # НОВОЕ: Запоминаем описание поступка (резюме)
        action_comment = data.get("comment", "")

        # Сохраняем это в игрока, чтобы другие боты видели контекст ("Он сказал бред")
        if action_comment:
            # Ограничиваем длину, чтобы не забивать промпт
            player.attributes["last_action_desc"] = action_comment[:100]

        logic_cfg = bunker_cfg.gameplay["decision_logic"]
        factors_weights = logic_cfg["factors"]
        mitigation = logic_cfg["mitigation"]

        active_factors = attrs.get("active_factors", {})

        # Начисление угрозы
        if violation_type in factors_weights:
            base_weight = factors_weights[violation_type]
            current_val = active_factors.get(violation_type, 0)
            active_factors[violation_type] = max(current_val, base_weight)

        # Модификаторы
        multiplier = 1.0
        if argument_quality == "strong":
            multiplier = mitigation["strong_argument"]
        elif argument_quality == "bad":
            multiplier = mitigation["bad_argument"]

        for key in active_factors:
            active_factors[key] = int(active_factors[key] * multiplier)

        player.attributes["active_factors"] = active_factors

        # Обновляем глобальный статус для отображения
        total_suspicion = sum(active_factors.values())
        if total_suspicion > 100:
            player.attributes["status"] = "IMPOSTOR"  # Или высший приоритет
        elif "liar" in active_factors and active_factors["liar"] > 50:
            player.attributes["status"] = "LIAR"
        elif "biohazard" in active_factors and active_factors["biohazard"] > 50:
            player.attributes["status"] = "BIOHAZARD"
        elif total_suspicion > 50:
            player.attributes["status"] = "SUSPICIOUS"
        else:
            player.attributes["status"] = "NORMAL"

        return {
            "total_suspicion": total_suspicion,
            "type": violation_type,
            "quality": argument_quality,
            "new_facts": data.get("new_facts", [])
        }