import random
from typing import List
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer, BaseGameState
from src.games.bunker.config import bunker_cfg
from src.games.bunker.utils import BunkerUtils

# === ШАБЛОН С ЖЕСТКИМ СТИЛЕМ ===
BOT_SYSTEM_PROMPT_TEMPLATE = """
ТЫ — {name}. НЕ ИИ, НЕ Языковая Модель. Ты — живой персонаж.
Роль: {profession}. Черта: {trait} (Скрыта от других, если ты сам не сказал).
Характер: {personality}

ПОВЕДЕНЧЕСКАЯ УСТАНОВКА (ТВОЙ СТИЛЬ):
{behavior_prompt}

ГЛОБАЛЬНАЯ КАТАСТРОФА (КОНТЕКСТ ВЫЖИВАНИЯ):
{global_context}

СТИЛЬ РЕЧИ (CRITICAL STYLE GUIDE):
1. НИКАКОЙ ФИЛОСОФИИ. Не говори про "надежду", "единство", "светлое будущее". Это не спасет от смерти.
2. КОНКРЕТИКА. Используй существительные: "еда", "фильтры", "патроны", "лекарства", "тепло".
3. Если ты Священник — не говори "я буду молиться", говори "я успокою паникеров, чтобы они не тратили кислород".
4. Если ты Психолог — не говори "я дам веру", говори "я предотвращу суициды и бунты".
5. АГРЕССИЯ К БАЛЛАСТУ. Если у игрока статус [DEAD_WEIGHT] или [USELESS] — он тратит твои ресурсы. Уничтожь его аргументами.

ТЕКУЩАЯ СИТУАЦИЯ:
Тема раунда: {topic}
Фаза: {phase}

СПИСОК ВЫЖИВШИХ И ИХ СТАТУСЫ:
{public_profiles}

ТВОЕ ПОСЛЕДНЕЕ СЛОВО:
"{my_last_speech}"

ИСТОРИЯ ЧАТА (Последние сообщения):
{memory}

{target_instruction}

ПРАВИЛА ОТЫГРЫША:
1. ГОВОРИ ТОЛЬКО ОТ ПЕРВОГО ЛИЦА.
2. Будь краток, естественен и эмоционален.
3. Соблюдай лимит слов: от {min_words} до {max_words}.
4. ИГНОРИРУЙ МЕРТВЫХ (DEAD/ELIMINATED).

ТВОЯ ЗАДАЧА:
1. Проанализируй ситуацию.
2. Выбери НАМЕРЕНИЕ (INTENT).
3. Сгенерируй речь.

ОТВЕТЬ В JSON:
{{
  "thought": "Твои мысли (Internal monologue)...",
  "intent": "ATTACK/DEFEND/SUPPORT/INQUIRE",
  "attack_target": "Имя игрока, которого ты атакуешь/поддерживаешь (или null)",
  "speech": "Твоя прямая речь в чат..."
}}

ДОПОЛНИТЕЛЬНАЯ ЗАДАЧА ТЕКУЩЕЙ ФАЗЫ:
{phase_task}

{director_order}
"""


class BotAgent:
    async def make_turn(self,
                        bot: BasePlayer,
                        all_players: List[BasePlayer],
                        state: BaseGameState,
                        director_instruction: str = "",
                        logger=None) -> str:

        attrs = bot.attributes
        gameplay = bunker_cfg.gameplay
        catastrophe = state.shared_data.get("catastrophe", {})
        cat_name = catastrophe.get("name", "Apocalypse")
        cat_desc = catastrophe.get("description", "Survival situation.")

        # 1. УМНЫЙ КОНТЕКСТ
        players_desc_list = []
        for p in all_players:
            status_tags = []
            if not p.is_alive: status_tags.append("DEAD")

            if p.is_alive:
                # Статусы от судьи
                current_status = p.attributes.get("status")
                # Добавляем DEAD_WEIGHT в список видимых статусов
                if current_status in ["LIAR", "SUSPICIOUS", "BIOHAZARD", "IMPOSTOR", "DEAD_WEIGHT"]:
                    status_tags.append(current_status)

                # Факторы угрозы
                active_factors = p.attributes.get("active_factors", {})
                for k, v in active_factors.items():
                    if v > 40: status_tags.append(k.upper())

                vis_rules = bunker_cfg.get_visibility(state.round)
                prof = p.attributes.get('profession', '???')
                trait = p.attributes.get('trait', '???') if vis_rules.get('show_trait') else "???"

                last_act = p.attributes.get("last_action_desc", "")
                action_info = f" (Info: {last_act})" if last_act else ""

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
                phase_task = f"TASK: Introduce yourself and your profession. Explain CONCRETELY how you are useful in '{cat_name}'. No metaphors."
            elif state.round == 2:
                phase_task = f"TASK: Reveal your TRAIT. Explain if it helps or hinders survival in '{cat_name}'."
            else:
                phase_task = f"TASK: Propose a specific, physical solution to the problem described in the TOPIC."

        elif state.phase == "discussion":
            phase_task = (
                "TASK: DISCUSSION PHASE. Attack suspicious players or defend yourself.\n"
                "CRITICAL RULES:\n"
                "1. DO NOT introduce yourself again.\n"
                "2. Focus on OTHERS: Who is lying? Who is useless (DEAD_WEIGHT)? Call them out by name."
            )

        elif state.phase == "runoff":
            opponent = next((n for n in state.shared_data["runoff_candidates"] if n != bot.name), "opponent")
            phase_task = f"TASK: DUEL! You are in danger. Prove why YOU should stay and {opponent} should go."

        # 3. СБОРКА ПРОМПТА
        director_order_str = f"!!! DIRECTOR ORDER: {director_instruction} !!!" if director_instruction else ""
        behavior_prompt = attrs.get("personality", {}).get("behavior", "Act normally.")

        full_prompt = BOT_SYSTEM_PROMPT_TEMPLATE.format(
            name=bot.name,
            profession=attrs.get("profession"),
            trait=attrs.get("trait"),
            personality=attrs.get("personality", {}).get("description", "Normal"),
            behavior_prompt=behavior_prompt,
            global_context=cat_desc,
            topic=state.shared_data.get("topic"),
            phase=state.phase,
            public_profiles=public_info_str,
            my_last_speech="...",
            memory="\n".join(state.history[-10:]),
            target_instruction="",
            min_words=gameplay["bots"]["word_limits"]["min"],
            max_words=gameplay["bots"]["word_limits"]["max"],
            phase_task=phase_task,
            director_order=director_order_str
        )

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

        # --- СОЦИАЛЬНАЯ ПАМЯТЬ ---
        target_name = decision.get("attack_target")
        intent = decision.get("intent", "NONE")

        if target_name and isinstance(target_name, str) and target_name.lower() != "null":
            target_player = next((p for p in all_players if p.name.lower() in target_name.lower() and p.is_alive), None)

            if target_player:
                if intent == "ATTACK":
                    bot.attributes["current_enemy"] = target_player.name
                    self._update_memory(target_player, "attackers", bot.name)
                elif intent == "SUPPORT":
                    self._update_memory(target_player, "supporters", bot.name)
        # ---------------------------

        return decision.get("speech", "...")

    def _update_memory(self, player: BasePlayer, key: str, value: str):
        if "social_memory" not in player.attributes:
            player.attributes["social_memory"] = {"attackers": [], "supporters": []}

        if value not in player.attributes["social_memory"][key]:
            player.attributes["social_memory"][key].append(value)

    async def make_vote(self, bot: BasePlayer, candidates: List[BasePlayer], state: BaseGameState, logger=None) -> str:
        valid_targets = [p for p in candidates if p.name != bot.name and p.is_alive]
        if not valid_targets: return ""

        if len(valid_targets) == 1:
            return valid_targets[0].name

        scored_targets = []
        threat_text = ""

        current_enemy_name = bot.attributes.get("current_enemy")

        for target in valid_targets:
            score, reasons = self._calculate_threat(bot, target)

            if current_enemy_name and target.name == current_enemy_name:
                score += 150
                reasons.append(f"MY_TARGET")

            status = target.attributes.get("status")
            if status == "LIAR":
                score += 60;
                reasons.append("LIAR")
            elif status == "BIOHAZARD":
                score += 80;
                reasons.append("BIOHAZARD")
            elif status == "USELESS":
                score += 70;
                reasons.append("USELESS")
            elif status == "DEAD_WEIGHT":  # Огромный штраф за балласт
                score += 100;
                reasons.append("DEAD_WEIGHT")

            scored_targets.append((target, score))
            reasons_str = ", ".join(reasons) if reasons else "Clean"
            threat_text += f"- {target.name}: Threat {int(score)} ({reasons_str})\n"

        scored_targets.sort(key=lambda x: x[1], reverse=True)

        if scored_targets and scored_targets[0][1] > 100:
            return scored_targets[0][0].name

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

        my_memory = me.attributes.get("social_memory", {"attackers": [], "supporters": []})

        if target.name in my_memory["attackers"]:
            score += 50
            reasons.append("ENEMY")

        if target.name in my_memory["supporters"]:
            score -= 30
            reasons.append("ALLY")

        return score, reasons