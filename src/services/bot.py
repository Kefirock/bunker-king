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

        # 2. Поиск своей последней фразы
        my_last_speech = "Пока не говорил."
        for line in reversed(game_state.history):
            if line.startswith(f"[{bot_profile.name}]"):
                parts = line.split(": ", 1)
                if len(parts) > 1: my_last_speech = parts[1]
                break

        # 3. ЛОГИКА ЦЕЛЕУКАЗАНИЯ (NEW)
        # Мы считаем угрозы ДО того, как бот откроет рот.
        target_instruction = ""
        if game_state.phase in ["discussion", "runoff"]:
            best_target = None
            max_threat = -1

            # Проходим по всем игрокам (кроме себя)
            for target in all_players:
                if target.name == bot_profile.name: continue

                # Используем ту же математику, что и при голосовании
                score, reasons = self._calculate_threat(bot_profile, target)

                if score > max_threat:
                    max_threat = score
                    best_target = target

            # Если есть явный враг (score > 0), добавляем инструкцию
            if best_target and max_threat > 0:
                reasons_text = ", ".join(reasons[0]) if isinstance(reasons[0], list) else ", ".join(reasons)
                target_instruction = (
                    f"\n>>> ВНУТРЕННИЙ АНАЛИЗ УГРОЗ <<<\n"
                    f"Твои инстинкты подсказывают, что {best_target.name} — главная угроза (Уровень: {int(max_threat)}).\n"
                    f"Причины: {reasons_text}.\n"
                    f"В своей речи ты должен упомянуть {best_target.name} и эти причины."
                )

        # 4. Формирование промпта
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

            # Вставляем нашу инструкцию по цели
            target_instruction=target_instruction,

            attack_threshold=thresholds["attack"],
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
        return decision.get("speech", "...")

    async def make_vote(self, bot_profile: PlayerProfile, candidates: List[PlayerProfile],
                        game_state: GameState) -> str:
        """
        Умное голосование на основе факторов.
        """
        valid_targets = [p for p in candidates if p.name != bot_profile.name]

        threat_assessment = ""
        # Сортируем цели по уровню угрозы для наглядности в промпте
        scored_targets = []
        for target in valid_targets:
            danger_score, reasons = self._calculate_threat(bot_profile, target)
            scored_targets.append((target, danger_score, reasons))

        # Сортировка от опасного к безопасному
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
        prompt = template.format(
            name=bot_profile.name,
            profession=bot_profile.profession,
            personality=bot_profile.personality.description,
            threat_assessment=threat_assessment,
            history=history_text,
            my_last_speech=my_last_speech
        )

        response = await llm.generate(
            bot_profile.llm_config,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            json_mode=True
        )

        data = llm.parse_json(response)
        vote = data.get("vote")

        target_names = [p.name for p in valid_targets]
        if vote not in target_names:
            # Fallback: берем самого опасного по математике
            if scored_targets:
                vote = scored_targets[0][0].name
            else:
                vote = random.choice(target_names)

        return vote

    def _calculate_threat(self, me: PlayerProfile, target: PlayerProfile):
        """
        Threat = BaseFactor * PersonalityMultiplier
        """
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