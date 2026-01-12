import random
from typing import List
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer, BaseGameState
from src.games.bunker.config import bunker_cfg
from src.games.bunker.utils import BunkerUtils


class BotAgent:
    async def make_turn(self,
                        bot: BasePlayer,
                        all_players: List[BasePlayer],
                        state: BaseGameState,
                        director_instruction: str = "",
                        logger=None) -> str:

        attrs = bot.attributes
        gameplay = bunker_cfg.gameplay

        # 1. Формируем контекст (что бот видит)
        public_info_str = BunkerUtils.generate_dashboard(
            state.shared_data.get("topic", ""),
            state.round,
            state.phase,
            all_players
        )

        # 2. История
        history_text = "\n".join(state.history[-10:])

        # 3. Анализ угроз (кого атаковать)
        target_instruction = ""
        if state.phase in ["discussion", "runoff"]:
            best_target, max_threat, reasons = self._find_best_target(bot, all_players)
            if best_target and max_threat > 0:
                reasons_text = ", ".join(reasons)
                target_instruction = (
                    f"\n>>> АНАЛИЗ УГРОЗ <<<\n"
                    f"Цель: {best_target.name}. Причины: {reasons_text}."
                )

        # 4. Сборка промпта
        template = bunker_cfg.prompts["bot_player"]["system"]
        full_prompt = template.format(
            name=bot.name,
            profession=attrs.get("profession"),
            trait=attrs.get("trait"),
            personality=attrs.get("personality", {}).get("description", "Normal"),
            topic=state.shared_data.get("topic"),
            phase=state.phase,
            public_profiles=public_info_str,
            my_last_speech="...",  # Упростили для краткости
            memory=history_text,
            target_instruction=target_instruction,
            attack_threshold=gameplay["bots"]["thresholds"]["attack"],
            min_words=gameplay["bots"]["word_limits"]["min"],
            max_words=gameplay["bots"]["word_limits"]["max"]
        )

        if director_instruction:
            full_prompt += f"\n!!! ПРИКАЗ РЕЖИССЕРА: {director_instruction} !!!"

        # 5. Выбор модели (берем случайную из конфига моделей)
        bot_models = core_cfg.models["player_models"]
        model = random.choice(bot_models)

        response = await llm_client.generate(
            model_config=model,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": "Твой ход."}
            ],
            json_mode=True,
            logger=logger
        )

        decision = llm_client.parse_json(response)
        return decision.get("speech", "...")

    async def make_vote(self, bot: BasePlayer, candidates: List[BasePlayer], state: BaseGameState, logger=None) -> str:
        valid_targets = [p for p in candidates if p.name != bot.name and p.is_alive]

        # Расчет весов для голосования
        scored_targets = []
        threat_text = ""
        for target in valid_targets:
            score, reasons = self._calculate_threat(bot, target)
            scored_targets.append((target, score))
            reasons_str = ", ".join(reasons) if reasons else "Чист"
            threat_text += f"- {target.name}: Угроза {int(score)} ({reasons_str})\n"

        # Если есть явный враг, голосуем по логике, иначе спрашиваем LLM
        scored_targets.sort(key=lambda x: x[1], reverse=True)

        # Промпт для голосования
        template = bunker_cfg.prompts["bot_player"]["voting_user"]
        prompt = template.format(
            name=bot.name,
            profession=bot.attributes.get("profession"),
            personality=bot.attributes.get("personality", {}).get("description"),
            threat_assessment=threat_text,
            history="\n".join(state.history[-10:]),
            my_last_speech="...",
            candidates_list=", ".join([p.name for p in valid_targets])
        )

        model = core_cfg.models["player_models"][0]
        response = await llm_client.generate(
            model_config=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            json_mode=True,
            logger=logger
        )

        data = llm_client.parse_json(response)
        vote = data.get("vote")

        # Fallback
        if vote not in [p.name for p in valid_targets]:
            if scored_targets:
                vote = scored_targets[0][0].name
            else:
                vote = random.choice([p.name for p in valid_targets]) if valid_targets else ""

        return vote

    def _find_best_target(self, me: BasePlayer, others: List[BasePlayer]):
        best_target = None
        max_threat = -1
        best_reasons = []
        for target in others:
            if not target.is_alive or target.name == me.name: continue
            score, reasons = self._calculate_threat(me, target)
            if score > max_threat:
                max_threat = score
                best_target = target
                best_reasons = reasons
        return best_target, max_threat, best_reasons

    def _calculate_threat(self, me: BasePlayer, target: BasePlayer):
        score = 0
        reasons = []
        # Достаем множители страхов из personality
        my_mults = me.attributes.get("personality", {}).get("multipliers", {})

        target_factors = target.attributes.get("active_factors", {})

        for factor, weight in target_factors.items():
            if weight <= 0: continue
            mult = my_mults.get(factor, 1.0)
            final_weight = weight * mult
            score += final_weight
            if final_weight > 20:
                reasons.append(f"{factor.upper()}")

        return score, reasons