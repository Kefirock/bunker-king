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

        # 1. УМНЫЙ КОНТЕКСТ
        players_desc_list = []
        for p in all_players:
            status_tags = []
            if not p.is_alive: status_tags.append("DEAD")

            if p.is_alive:
                if p.attributes.get("status") in ["LIAR", "SUSPICIOUS", "BIOHAZARD", "IMPOSTOR"]:
                    status_tags.append(p.attributes.get("status"))

                active_factors = p.attributes.get("active_factors", {})
                for k, v in active_factors.items():
                    if v > 40: status_tags.append(k.upper())

                vis_rules = bunker_cfg.get_visibility(state.round)
                prof = p.attributes.get('profession', '???')
                trait = p.attributes.get('trait', '???') if vis_rules.get('show_trait') else "???"

                # Добавляем резюме последнего действия от Судьи
                last_act = p.attributes.get("last_action_desc", "")
                action_info = f" (Last action: {last_act})" if last_act else ""

                status_tags = list(set(status_tags))
                tags_str = f"[{', '.join(status_tags)}]" if status_tags else ""

                players_desc_list.append(f"- {p.name}: {prof}, {trait} {tags_str}{action_info}")
            else:
                players_desc_list.append(f"- {p.name}: [ELIMINATED/DEAD]")

        public_info_str = "\n".join(players_desc_list)

        # 2. ОПРЕДЕЛЕНИЕ ЗАДАЧИ
        phase_task = "Speak naturally."
        if state.phase == "presentation":
            if state.round == 1:
                phase_task = "TASK: Introduce yourself, state your profession. Prove you are useful. Be brief."
            elif state.round == 2:
                phase_task = "TASK: Reveal your TRAIT. Explain why it is not a problem or how it helps."
            else:
                phase_task = "TASK: Propose a solution to the catastrophe problem based on your skills."

        elif state.phase == "discussion":
            phase_task = (
                "TASK: DISCUSSION PHASE. Attack suspicious players or defend yourself.\n"
                "CRITICAL RULES:\n"
                "1. DO NOT introduce yourself again.\n"
                "2. Focus on OTHERS: Who is lying? Who is useless? Call them out by name."
            )

        elif state.phase == "runoff":
            opponent = next((n for n in state.shared_data["runoff_candidates"] if n != bot.name), "opponent")
            phase_task = f"TASK: DUEL! You are in danger. Prove why YOU should stay and {opponent} should go. Be aggressive."

        # 3. СБОРКА ПРОМПТА
        # Мы обновляем шаблон JSON, требуя указать цель атаки
        json_format = """
        {
          "thought": "Internal monologue...",
          "intent": "ATTACK/DEFEND/SUPPORT/INQUIRE",
          "attack_target": "Name of the player you are attacking/suspecting (OR null if none)",
          "speech": "Your message to the chat..."
        }
        """

        template = bunker_cfg.prompts["bot_player"]["system"]
        # Заменяем старую инструкцию JSON на новую
        template = template.replace(
            '{\n  "thought": "Твой внутренний анализ...",\n  "intent": "ОДНО_СЛОВО",\n  "speech": "Твоя прямая речь..."\n}',
            json_format)

        full_prompt = template.format(
            name=bot.name,
            profession=attrs.get("profession"),
            trait=attrs.get("trait"),
            personality=attrs.get("personality", {}).get("description", "Normal"),
            topic=state.shared_data.get("topic"),
            phase=state.phase,
            public_profiles=public_info_str,
            my_last_speech="...",
            memory="\n".join(state.history[-10:]),
            target_instruction="",
            attack_threshold=gameplay["bots"]["thresholds"]["attack"],
            min_words=gameplay["bots"]["word_limits"]["min"],
            max_words=gameplay["bots"]["word_limits"]["max"]
        )

        full_prompt += f"\n\nCURRENT OBJECTIVE: {phase_task}"

        if director_instruction:
            full_prompt += f"\n!!! DIRECTOR ORDER: {director_instruction} !!!"

        bot_models = core_cfg.models["player_models"]
        model = random.choice(bot_models)

        response = await llm_client.generate(
            model_config=model,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": f"Topic: {state.shared_data.get('topic')}. Action!"}
            ],
            json_mode=True,
            logger=logger
        )

        decision = llm_client.parse_json(response)

        # --- ЛОГИКА ПАМЯТИ ВРАГА ---
        target_name = decision.get("attack_target")
        if target_name and isinstance(target_name, str) and target_name.lower() != "null":
            # Ищем точное совпадение имени среди игроков
            found_enemy = next((p.name for p in all_players if p.name.lower() in target_name.lower()), None)
            if found_enemy:
                bot.attributes["current_enemy"] = found_enemy
        # ---------------------------

        return decision.get("speech", "...")

    async def make_vote(self, bot: BasePlayer, candidates: List[BasePlayer], state: BaseGameState, logger=None) -> str:
        valid_targets = [p for p in candidates if p.name != bot.name and p.is_alive]
        if not valid_targets: return ""

        if len(valid_targets) == 1:
            return valid_targets[0].name

        scored_targets = []
        threat_text = ""

        # Кого мы ненавидим в данный момент?
        current_enemy_name = bot.attributes.get("current_enemy")

        for target in valid_targets:
            score, reasons = self._calculate_threat(bot, target)

            # --- БОНУС ЗА ПОСЛЕДОВАТЕЛЬНОСТЬ ---
            if current_enemy_name and target.name == current_enemy_name:
                score += 150  # Огромный вес, чтобы подтвердить свои слова делом
                reasons.append(f"PUBLIC_ENEMY (I attacked {target.name})")
            # -----------------------------------

            status = target.attributes.get("status")
            if status == "LIAR":
                score += 60
                reasons.append("LIAR")
            elif status == "BIOHAZARD":
                score += 80
                reasons.append("BIOHAZARD")
            elif status == "USELESS":
                score += 70
                reasons.append("USELESS")

            scored_targets.append((target, score))
            reasons_str = ", ".join(reasons) if reasons else "Clean"
            threat_text += f"- {target.name}: Threat {int(score)} ({reasons_str})\n"

        scored_targets.sort(key=lambda x: x[1], reverse=True)

        # Если есть явный лидер по угрозе (>100), голосуем без вопросов
        if scored_targets and scored_targets[0][1] > 100:
            return scored_targets[0][0].name

        # Иначе спрашиваем LLM (для нюансов)
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
        raw_vote = data.get("vote", "").strip()

        final_vote = ""
        for t in valid_targets:
            if t.name in raw_vote:
                final_vote = t.name
                break

        if not final_vote:
            final_vote = scored_targets[0][0].name if scored_targets else random.choice([p.name for p in valid_targets])

        return final_vote

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