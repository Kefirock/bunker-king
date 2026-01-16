import random
from typing import List
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer, BaseGameState
from src.games.bunker.config import bunker_cfg


class DirectorAgent:
    def __init__(self):
        director_cfg = bunker_cfg.gameplay["director"]
        self.chaos_level = director_cfg["chaos_level"]
        self.inject_chance = director_cfg["inject_chance"]

    async def get_hidden_instruction(self,
                                     current_player: BasePlayer,
                                     all_players: List[BasePlayer],
                                     state: BaseGameState,
                                     logger=None) -> str:

        # Ищем самую подозрительную цель
        suspicious_target = None
        max_score = 0

        for p in all_players:
            # Игнорируем себя и МЕРТВЫХ
            if p.name == current_player.name: continue
            if not p.is_alive: continue

            # Сумма факторов опасности
            score = sum(p.attributes.get("active_factors", {}).values())
            status = p.attributes.get("status", "NORMAL")

            if score > 15 or status in ["SUSPICIOUS", "LIAR"]:
                if score > max_score:
                    max_score = score
                    suspicious_target = p

        # Рандомный шанс вмешательства
        should_inject = False
        if suspicious_target:
            should_inject = True
        else:
            chance = self.inject_chance * (self.chaos_level / 5.0)
            if state.phase == "discussion": chance += 0.2
            if random.random() < chance:
                should_inject = True

        if not should_inject:
            return ""

        # Генерация инструкции
        model = core_cfg.models["director_models"][0]
        history_snippet = "\n".join(state.history[-5:])

        target_context = ""
        if suspicious_target:
            target_context = (
                f"\nВНИМАНИЕ: Игрок {suspicious_target.name} подозрителен "
                f"(Уровень: {max_score}). Направь агрессию на него."
            )

        prompt = (
            f"Ты — Режиссер. Игра 'Бункер'. Тема: {state.shared_data.get('topic')}\n"
            f"Фаза: {state.phase}\n"
            f"Ходит: {current_player.name} ({current_player.attributes.get('profession')}).\n"
            f"История:\n{history_snippet}\n"
            f"{target_context}\n\n"
            f"Дай ОДНО скрытое указание игроку {current_player.name} (макс 15 слов). "
            "Цель: конфликт или устранение слабых."
        )

        instruction = await llm_client.generate(
            model_config=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            logger=logger
        )
        return instruction.strip()