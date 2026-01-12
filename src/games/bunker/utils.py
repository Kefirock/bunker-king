import random
from typing import List, Dict
from src.core.schemas import BasePlayer
from src.games.bunker.config import bunker_cfg


class BunkerUtils:
    @staticmethod
    def generate_initial_players(user_data: List[Dict]) -> List[BasePlayer]:
        """
        Создает список BasePlayer, заполняя attributes спецификой Бункера.
        user_data: список [{"id": 123, "name": "User"}, ...]
        """
        scenarios = bunker_cfg.scenarios
        profs = scenarios["professions"][:]
        traits = scenarios["traits"][:]
        names = scenarios["bot_names"][:]
        personalities = scenarios.get("personalities", [])

        random.shuffle(profs)
        random.shuffle(traits)
        random.shuffle(names)

        players = []

        # 1. Создаем Людей
        for u in user_data:
            prof = profs.pop() if profs else "Выживший"
            trait = traits.pop() if traits else "Счастливчик"

            # Атрибуты специфичные для Бункера
            attrs = {
                "profession": prof,
                "trait": trait,
                "health": 100,
                "status": "NORMAL",  # NORMAL, SUSPICIOUS, LIAR
                "active_factors": {},  # Для Судьи (накопленные грехи)
                "personality": {"id": "human", "description": "Живой Игрок"}
            }

            p = BasePlayer(
                id=u["id"],
                name=u["name"],
                is_human=True,
                attributes=attrs
            )
            players.append(p)

        # 2. Создаем Ботов (добиваем до нужного количества)
        target_total = bunker_cfg.gameplay.get("setup", {}).get("total_players", 5)
        bots_needed = max(0, target_total - len(players))

        for i in range(bots_needed):
            bot_name = names.pop() if names else f"Bot-{i + 1}"
            prof = profs.pop() if profs else "Выживший"
            trait = traits.pop() if traits else "Обычный"
            pers_data = random.choice(personalities)

            attrs = {
                "profession": prof,
                "trait": trait,
                "health": 100,
                "status": "NORMAL",
                "active_factors": {},
                "personality": pers_data  # Содержит description и multipliers
            }

            p = BasePlayer(
                id=-(i + 100),  # Отрицательный ID для ботов
                name=bot_name,
                is_human=False,
                attributes=attrs
            )
            players.append(p)

        random.shuffle(players)
        return players

    @staticmethod
    def get_display_name(p: BasePlayer, round_num: int, reveal_all: bool = False) -> str:
        """
        Генерирует строку "Bob - Врач, [Скрыто]" на основе правил видимости.
        """
        vis_rules = bunker_cfg.get_visibility(round_num)
        attrs = p.attributes

        prof = attrs.get("profession", "???")
        trait = attrs.get("trait", "???")
        status_marker = " 💀" if not p.is_alive else ""

        if reveal_all:
            return f"<b>{p.name}</b> - {prof}, {trait}{status_marker}"

        trait_part = f", {trait}" if vis_rules.get("show_trait", False) else ""
        return f"<b>{p.name}</b> - {prof}{trait_part}{status_marker}"

    @staticmethod
    def generate_dashboard(topic: str, round_num: int, phase: str, players: List[BasePlayer]) -> str:
        list_str = ""
        for p in players:
            list_str += f"- {BunkerUtils.get_display_name(p, round_num)}\n"

        return (
            f"🔔 <b>РАУНД {round_num}</b> | ФАЗА: {phase}\n"
            f"<blockquote>{topic}</blockquote>\n\n"
            f"👥 <b>ВЫЖИВШИЕ:</b>\n{list_str}"
        )