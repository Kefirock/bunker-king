import random
from typing import List, Dict
from src.core.schemas import BasePlayer
from src.games.bunker.config import bunker_cfg


class BunkerUtils:
    @staticmethod
    def generate_initial_players(user_data: List[Dict]) -> List[BasePlayer]:
        """
        user_data: список только живых людей [{'id': 123, 'name': 'Bob'}]
        """
        scenarios = bunker_cfg.scenarios
        profs = scenarios["professions"][:]
        traits = scenarios["traits"][:]
        names = scenarios["bot_names"][:]
        personalities = scenarios.get("personalities", [])

        # Берем настройки из конфига (по умолчанию 6 игроков)
        target_total = bunker_cfg.gameplay.get("setup", {}).get("total_players", 6)

        random.shuffle(profs)
        random.shuffle(traits)
        random.shuffle(names)

        players = []

        # 1. Создаем ЛЮДЕЙ
        for u in user_data:
            p_name = u["name"]
            prof = profs.pop() if profs else "Выживший"
            trait = traits.pop() if traits else "Счастливчик"

            attrs = {
                "profession": prof,
                "trait": trait,
                "health": 100,
                "status": "NORMAL",
                "active_factors": {},
                # Люди играют сами за себя, у них нет Personality-множителей страха
                "personality": {"id": "human", "description": "Живой Игрок"}
            }

            p = BasePlayer(
                id=u["id"],
                name=p_name,
                is_human=True,
                attributes=attrs
            )
            players.append(p)

        # 2. Авто-заполнение БОТАМИ
        # Если пришло 2 человека, а надо 6 -> создаем 4 бота
        bots_needed = max(0, target_total - len(players))

        for i in range(bots_needed):
            bot_name = names.pop() if names else f"CPU-{i + 1}"
            prof = profs.pop() if profs else "Бродяга"
            trait = traits.pop() if traits else "Обычный"
            pers_data = random.choice(personalities)

            attrs = {
                "profession": prof,
                "trait": trait,
                "health": 100,
                "status": "NORMAL",
                "active_factors": {},
                "personality": pers_data
            }

            # Генерируем безопасный ID для бота
            fake_id = -(2000 + i)

            p = BasePlayer(
                id=fake_id,
                name=bot_name,
                is_human=False,
                attributes=attrs
            )
            players.append(p)

        random.shuffle(players)
        return players

    @staticmethod
    def get_display_name(p: BasePlayer, round_num: int, reveal_all: bool = False) -> str:
        vis_rules = bunker_cfg.get_visibility(round_num)
        attrs = p.attributes

        prof = attrs.get("profession", "???")
        trait = attrs.get("trait", "???")
        status_marker = " 💀" if not p.is_alive else ""

        if not p.is_alive or reveal_all:
            role_info = ""
            if attrs.get("status") == "LIAR": role_info = " [ЛЖЕЦ]"
            return f"<b>{p.name}</b> - {prof}, {trait}{role_info}{status_marker}"

        trait_part = f", {trait}" if vis_rules.get("show_trait", False) else ""
        return f"<b>{p.name}</b> - {prof}{trait_part}{status_marker}"

    @staticmethod
    def generate_dashboard(topic: str, round_num: int, phase: str, players: List[BasePlayer]) -> str:
        list_str = ""
        for p in players:
            list_str += f"- {BunkerUtils.get_display_name(p, round_num)}\n"

        phase_map = {
            "presentation": "ПРЕДСТАВЛЕНИЕ",
            "discussion": "ОБСУЖДЕНИЕ",
            "voting": "ГОЛОСОВАНИЕ",
            "runoff": "ПЕРЕСТРЕЛКА"
        }
        phase_ru = phase_map.get(phase, phase)

        return (
            f"🔔 <b>РАУНД {round_num}</b> | {phase_ru}\n"
            f"<blockquote>{topic}</blockquote>\n\n"
            f"👥 <b>ВЫЖИВШИЕ:</b>\n{list_str}"
        )