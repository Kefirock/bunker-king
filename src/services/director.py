from typing import List
from src.config import cfg
from src.llm import llm
from src.schemas import GameState, PlayerProfile
import random


class DirectorEngine:
    def __init__(self):
        self.chaos_level = cfg.gameplay["director"]["chaos_level"]
        self.inject_chance = cfg.gameplay["director"]["inject_chance"]

    async def get_hidden_instruction(self,
                                     current_player: PlayerProfile,
                                     all_players: List[PlayerProfile],
                                     game_state: GameState,
                                     logger=None) -> str:

        suspicious_target = None
        max_score = 0

        for p in all_players:
            if p.name == current_player.name: continue
            if p.suspicion_score > 15 or p.status in ["SUSPICIOUS", "LIAR", "IMPOSTOR"]:
                if p.suspicion_score > max_score:
                    max_score = p.suspicion_score
                    suspicious_target = p

        should_inject = False
        if suspicious_target:
            should_inject = True
        else:
            chance = self.inject_chance * (self.chaos_level / 5.0)
            if game_state.phase == "discussion": chance += 0.2
            if random.random() < chance:
                should_inject = True

        if not should_inject:
            return ""

        model = cfg.models["director_models"][0]
        history_snippet = "\n".join(game_state.history[-5:])

        target_context = ""
        if suspicious_target:
            target_context = (
                f"\nВНИМАНИЕ: Игрок {suspicious_target.name} ведет себя подозрительно "
                f"(Уровень подозрения: {suspicious_target.suspicion_score}). "
                "Рекомендуется направить агрессию на него."
            )

        prompt = (
            f"Ты — Режиссер драмы. Идет игра на выживание.\n"
            f"Текущая тема: {game_state.topic}\n"
            f"Фаза: {game_state.phase}\n\n"
            f"СЕЙЧАС ХОДИТ: {current_player.name} ({current_player.profession}).\n"
            f"История чата:\n{history_snippet}\n"
            f"{target_context}\n\n"
            f"Дай ОДНО короткое скрытое указание игроку {current_player.name}. "
            "Цель: создать конфликт или устранить слабое звено.\n"
            "ВАЖНО: \n"
            "1. Не приказывай атаковать себя.\n"
            "2. Если есть подозрительный игрок — прикажи атаковать его.\n"
            "3. Максимум 15 слов.\n"
        )

        instruction = await llm.generate(
            model,
            [{"role": "user", "content": prompt}],
            temperature=0.8,
            logger=logger
        )

        return instruction.strip()