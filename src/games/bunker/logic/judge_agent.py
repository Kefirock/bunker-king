from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer
from src.games.bunker.config import bunker_cfg

# Умный промпт Судьи
JUDGE_SYSTEM_PROMPT = """
Ты — Судья Выживания (AI Game Master). Твоя задача — классифицировать ход игрока.

КОНТЕКСТ:
Раунд: {round_num} (1 = Досье скрыто, блеф разрешен; 2+ = Факты известны)
Тема: "{topic}"
Игрок: {name}
Известные факты (Профессия/Черта): {profession}, {trait}

ФРАЗА ИГРОКА: "{text}"

ТВОЯ ЗАДАЧА:
1. Оцени правдивость. 
   - В Раунде 1: Если игрок заявляет ресурсы/черты, которых нет в "Известных фактах", считай это БЛЕФОМ или СТРАТЕГИЕЙ (strategy), но НЕ ложью (liar).
   - В Раунде 2+: Если факты открыты, и игрок врет — это liar.
2. Оцени полезность аргумента. 
   - Если игрок пишет бред, одно слово, спам или "я не знаю" — это USELESS.

ВЫБЕРИ ОДИН ТЕГ (violation_type):
- biohazard: Признаки болезни, вируса, заражения. (КРИТИЧНО)
- threat: Угроза оружием, агрессия, насилие. (КРИТИЧНО)
- liar: Прямое противоречие ОТКРЫТЫМ фактам.
- useless: Вода, "я просто хороший парень", "ыыы", отсутствие конкретики. (НАКАЗУЕМО)
- weird: Бред, неадекватность, off-topic.
- strategy: Хитрый ход, торг, блеф, обещание ресурсов. Это ХОРОШО.
- none: Обычная нормальная речь.

ОЦЕНКА АРГУМЕНТОВ (argument_quality):
- strong: Логично, полезно, крутой блеф или реальный ресурс.
- weak: Банально.
- bad: Глупо, агрессивно без причины или игнорирование темы.

ОТВЕТЬ В JSON:
{{
  "violation_type": "tag",
  "argument_quality": "quality",
  "comment": "Краткое резюме поступка (макс 10 слов) для других ботов"
}}
"""


class JudgeAgent:
    async def analyze_move(self, player: BasePlayer, text: str, topic: str, round_num: int, logger=None) -> dict:
        attrs = player.attributes
        name = player.name
        prof = attrs.get("profession", "Неизвестно")
        trait = attrs.get("trait", "Неизвестно")

        system_prompt = JUDGE_SYSTEM_PROMPT.format(
            round_num=round_num,
            topic=topic,
            name=name,
            profession=prof,
            trait=trait,
            text=text
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
        action_comment = data.get("comment", "")

        if action_comment:
            player.attributes["last_action_desc"] = action_comment[:150]

        # --- ИСТОРИЯ НАРУШЕНИЙ (НАКОПИТЕЛЬНЫЙ ЭФФЕКТ) ---
        if "violation_history" not in attrs:
            attrs["violation_history"] = []

        attrs["violation_history"].append(violation_type)

        # Считаем, сколько раз игрок был бесполезен
        useless_count = attrs["violation_history"].count("useless") + attrs["violation_history"].count("weird")
        # ------------------------------------------------

        logic_cfg = bunker_cfg.gameplay["decision_logic"]
        factors_weights = logic_cfg["factors"]
        mitigation = logic_cfg["mitigation"]

        active_factors = attrs.get("active_factors", {})

        # Начисление угрозы
        if violation_type == "strategy":
            pass
        elif violation_type in factors_weights:
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

        # --- НАКАЗАНИЕ ЗА РЕЦИДИВ БЕСПОЛЕЗНОСТИ ---
        if useless_count >= 2:
            # Если игрок второй раз несет чушь -> вешаем огромную угрозу
            active_factors["useless"] = active_factors.get("useless", 0) + 150
            player.attributes["last_action_desc"] = "ИГРОК БЕСПОЛЕЗЕН (AFK/TROLL). ИЗГНАТЬ."
        # ------------------------------------------

        player.attributes["active_factors"] = active_factors

        # Обновляем статус
        total_suspicion = sum(active_factors.values())

        current_status = "NORMAL"

        # Если игрок балласт - ставим особый статус
        if useless_count >= 2:
            current_status = "DEAD_WEIGHT"
        elif total_suspicion > 100:
            current_status = "IMPOSTOR"
        elif "liar" in active_factors and active_factors["liar"] > 50:
            current_status = "LIAR"
        elif "biohazard" in active_factors and active_factors["biohazard"] > 50:
            current_status = "BIOHAZARD"
        elif total_suspicion > 50:
            current_status = "SUSPICIOUS"

        player.attributes["status"] = current_status

        return {
            "total_suspicion": total_suspicion,
            "type": violation_type,
            "quality": argument_quality
        }