from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer
from src.games.bunker.config import bunker_cfg


class JudgeAgent:
    async def analyze_move(self, player: BasePlayer, text: str, topic: str, logger=None) -> dict:
        # Достаем данные из атрибутов
        attrs = player.attributes
        name = player.name
        prof = attrs.get("profession", "Неизвестно")
        trait = attrs.get("trait", "Неизвестно")

        # Формируем промпт
        prompt_template = bunker_cfg.prompts["judge"]["system"]
        system_prompt = prompt_template.format(
            name=name,
            profession=prof,
            trait=trait,
            text=text,
            topic=topic
        )

        # Выбираем модель (берем первую из director_models в глобальном конфиге)
        # Важно: core_cfg.models загружает models.yaml
        judge_model = core_cfg.models["director_models"][0]

        response = await llm_client.generate(
            model_config=judge_model,
            messages=[{"role": "system", "content": system_prompt}],
            temperature=0.1,
            json_mode=True,
            logger=logger
        )

        data = llm_client.parse_json(response)

        # Обработка результатов
        violation_type = data.get("violation_type", "none")
        argument_quality = data.get("argument_quality", "weak")

        # Логика весов
        logic_cfg = bunker_cfg.gameplay["decision_logic"]
        factors_weights = logic_cfg["factors"]
        mitigation = logic_cfg["mitigation"]

        # Обновляем факторы игрока (грехи)
        active_factors = attrs.get("active_factors", {})

        if violation_type in factors_weights:
            base_weight = factors_weights[violation_type]
            current_val = active_factors.get(violation_type, 0)
            active_factors[violation_type] = max(current_val, base_weight)

        # Применяем модификаторы аргументов
        multiplier = 1.0
        if argument_quality == "strong":
            multiplier = mitigation["strong_argument"]
        elif argument_quality == "bad":
            multiplier = mitigation["bad_argument"]

        # Пересчитываем все факторы с учетом множителя
        for key in active_factors:
            active_factors[key] = int(active_factors[key] * multiplier)

        player.attributes["active_factors"] = active_factors

        total_suspicion = sum(active_factors.values())

        return {
            "total_suspicion": total_suspicion,
            "type": violation_type,
            "quality": argument_quality,
            "new_facts": data.get("new_facts", [])
        }