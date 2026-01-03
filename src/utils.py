import random
from typing import List, Union, Dict
from src.config import cfg
from src.schemas import PlayerProfile, Persona, GameState


class GameSetup:
    @staticmethod
    def generate_players(user_data: Union[str, List[Dict]]) -> List[PlayerProfile]:
        scenarios = cfg.scenarios
        profs = scenarios["professions"][:]
        traits = scenarios["traits"][:]
        names = scenarios["bot_names"][:]
        personalities_data = scenarios.get("personalities", [])
        bot_models = cfg.models["player_models"]

        random.shuffle(profs)
        random.shuffle(traits)
        random.shuffle(names)

        players = []

        humans_to_create = []
        if isinstance(user_data, str):
            humans_to_create.append({"name": user_data})
        elif isinstance(user_data, list):
            humans_to_create = user_data

        target_total = cfg.gameplay.get("setup", {}).get("total_players", 5)
        human_count = len(humans_to_create)
        bots_needed = max(0, target_total - human_count)

        for i in range(bots_needed):
            bot_name = names.pop() if names else f"Bot-{i + 1}"
            bot_prof = profs.pop() if profs else "Выживший"
            bot_trait = traits.pop() if traits else "Обычный"

            p_data = random.choice(personalities_data)
            persona = Persona(
                id=p_data["id"],
                description=p_data["description"],
                style_example="",
                multipliers=p_data.get("multipliers", {})
            )

            players.append(PlayerProfile(
                name=bot_name,
                profession=bot_prof,
                trait=bot_trait,
                personality=persona,
                is_human=False,
                llm_config=random.choice(bot_models)
            ))

        human_persona = Persona(id="human", description="Игрок", style_example="", multipliers={})

        for h in humans_to_create:
            p_name = h["name"]
            display_name = f"{p_name} (Вы)" if isinstance(user_data, str) else p_name

            hum_prof = profs.pop() if profs else "Счастливчик"
            hum_trait = traits.pop() if traits else "Живой"

            players.append(PlayerProfile(
                name=display_name,
                profession=hum_prof,
                trait=hum_trait,
                personality=human_persona,
                is_human=True,
            ))

        random.shuffle(players)
        return players

    @staticmethod
    def init_game_state() -> GameState:
        catastrophes = cfg.scenarios["catastrophes"]
        scenario = random.choice(catastrophes)
        topic = scenario["topics"][0]

        return GameState(
            round=1,
            phase="presentation",
            topic=f"{scenario['name']}: {topic}",
            history=[]
        )

    @staticmethod
    def get_display_name(p: PlayerProfile, round_num: int) -> str:
        """
        Формат: Имя: Профессия [, Черта]
        Статус и факторы скрыты от игроков.
        """
        visibility_rules = cfg.get_visibility(round_num)

        # Профессия видна всегда (если только не добавить в конфиг явный запрет)
        prof = p.profession if p.profession else "???"

        # Черта - зависит от раунда
        if visibility_rules.get("show_trait", False):
            trait = f", {p.trait}"
        else:
            trait = ""

        return f"{p.name}: {prof}{trait}"