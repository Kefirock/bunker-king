import random
from typing import List
from src.config import cfg
from src.llm import llm
from src.schemas import PlayerProfile, GameState, PublicPlayerInfo


class BotEngine:
    async def make_turn(self,
                        bot_profile: PlayerProfile,
                        all_players: List[PlayerProfile],
                        game_state: GameState,
                        director_instruction: str = "") -> str:

        # 1. Туман войны
        public_info = self._get_visible_profiles(all_players, game_state.round)
        public_info_str = "\n".join([str(p) for p in public_info])

        bot_settings = cfg.gameplay["bots"]
        thresholds = bot_settings["thresholds"]
        word_limits = bot_settings["word_limits"]
        mem_depth = bot_settings.get("memory_depth", 10)
        history_text = "\n".join(game_state.history[-mem_depth:]) if game_state.history else "Начало."

        # 2. Поиск последней фразы
        my_last_speech = "Пока не говорил."
        for line in reversed(game_state.history):
            if line.startswith(f"[{bot_profile.name}]"):
                parts = line.split(": ", 1)
                if len(parts) > 1: my_last_speech = parts[1]
                break

        # 3. Анализ угроз
        target_instruction = ""
        if game_state.phase in ["discussion", "runoff"]:
            best_target = None
            max_threat = -1
            best_reasons = []

            for target in all_players:
                if target.name == bot_profile.name: continue
                current_score, current_reasons = self._calculate_threat(bot_profile, target)

                if current_score > max_threat:
                    max_threat = current_score
                    best_target = target
                    best_reasons = current_reasons

            if best_target and max_threat > 0:
                reasons_text = ", ".join(best_reasons) if best_reasons else "интуиция"
                target_instruction = (
                    f"\n>>> ВНУТРЕННИЙ АНАЛИЗ УГРОЗ <<<\n"
                    f"Твои инстинкты подсказывают, что {best_target.name} — главная угроза (Уровень: {int(max_threat)}).\n"
                    f"Причины: {reasons_text}.\n"
                    f"В своей речи ты должен упомянуть {best_target.name} и эти причины."
                )

        # 4. Промпт
        phase_instructions = cfg.gameplay["phases"]
        current_phase_task = phase_instructions.get(game_state.phase, {}).get("instruction", "Говори.")
        director_part = f"\n!!! СКРЫТЫЙ ПРИКАЗ РЕЖИССЕРА: {director_instruction} !!!\n" if director_instruction else ""

        template = cfg.prompts["bot_player"]["system"]
        full_prompt = template.format(
            name=bot_profile.name,
            profession=bot_profile.profession,
            trait=bot_profile.trait,
            personality=bot_profile.personality.description,
            topic=game_state.topic,
            phase=game_state.phase,
            public_profiles=public_info_str,
            my_last_speech=my_last_speech,
            memory=history_text,
            target_instruction=target_instruction,
            min_words=word_limits["min"],
            max_words=word_limits["max"]
        )
        full_prompt += f"\n\nЗАДАЧА ТЕКУЩЕЙ ФАЗЫ ({game_state.phase}): {current_phase_task}"
        full_prompt += director_part

        # 5. Генерация
        response = await llm.generate(
            bot_profile.llm_config,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": f"Тема: {game_state.topic}. Действуй."}
            ],
            json_mode=True
        )

        decision = llm.parse_json(response)
        speech = decision.get("speech", "...")

        # Fallback на случай пустоты
        if not speech or speech.strip() == "..." or speech.strip() == "":
            speech = "Мне нужно немного подумать..."

        return speech

    async def make_vote(self, bot_profile: PlayerProfile, candidates: List[PlayerProfile],
                        game_state: GameState) -> str:

        valid_targets = [p for p in candidates if p.name != bot_profile.name]
        valid_names = [p.name for p in valid_targets]

        threat_assessment = ""
        scored_targets = []
        for target in valid_targets:
            danger_score, reasons = self._calculate_threat(bot_profile, target)
            scored_targets.append((target, danger_score, reasons))

        scored_targets.sort(key=lambda x: x[1], reverse=True)

        for target, score, reasons in scored_targets:
            reasons_str = ", ".join(reasons) if reasons else "Чист"
            threat_assessment += f"- {target.name}: Уровень угрозы {int(score)} ({reasons_str})\n"

        history_text = "\n".join(game_state.history[-15:])
        my_last_speech = "..."
        for line in reversed(game_state.history):
            if line.startswith(f"[{bot_profile.name}]"):
                parts = line.split(": ", 1)
                if len(parts) > 1: my_last_speech = parts[1]
                break

        template = cfg.prompts["bot_player"]["voting_user"]

        # Передаем список кандидатов в промпт
        candidates_str = ", ".join(valid_names)

        prompt = template.format(
            name=bot_profile.name,
            profession=bot_profile.profession,
            personality=bot_profile.personality.description,
            threat_assessment=threat_assessment,
            history=history_text,
            my_last_speech=my_last_speech,
            candidates_list=candidates_str
        )

        response = await llm.generate(
            bot_profile.llm_config,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            json_mode=True
        )

        data = llm.parse_json(response)
        vote_raw = data.get("vote", "")

        # --- НОРМАЛИЗАЦИЯ ГОЛОСА ---
        final_vote = None

        # 1. Точное совпадение
        if vote_raw in valid_names:
            final_vote = vote_raw
        else:
            # 2. Нечеткий поиск (без регистра)
            for name in valid_names:
                if vote_raw and (name.lower() in vote_raw.lower() or vote_raw.lower() in name.lower()):
                    final_vote = name
                    break

        # 3. Fallback (если имя не найдено или пустое - берем самую большую угрозу)
        if not final_vote:
            if scored_targets:
                final_vote = scored_targets[0][0].name
            else:
                final_vote = random.choice(valid_names)

        return final_vote

    def _calculate_threat(self, me: PlayerProfile, target: PlayerProfile):
        score = 0
        reasons = []
        my_mults = me.personality.multipliers

        for factor, weight in target.active_factors.items():
            if weight <= 0: continue

            mult = my_mults.get(factor, 1.0)
            final_weight = weight * mult
            score += final_weight

            intensity = "Low"
            if final_weight > 50:
                intensity = "HIGH"
            elif final_weight > 20:
                intensity = "Medium"

            reasons.append(f"{factor.upper()}={intensity}")

        return score, reasons

    def _get_visible_profiles(self, players: List[PlayerProfile], round_num: int) -> List[PublicPlayerInfo]:
        visibility_rules = cfg.get_visibility(round_num)
        visible_list = []
        for p in players:
            pub = PublicPlayerInfo(name=p.name)
            pub.profession = p.profession
            if visibility_rules.get("show_trait", False):
                pub.trait = p.trait
            else:
                pub.trait = "???"
            if visibility_rules.get("show_status", False):
                pub.status = p.status

            active_tags = [k.upper() for k, v in p.active_factors.items() if v > 20]
            pub.known_factors = active_tags

            visible_list.append(pub)
        return visible_list