import random
from typing import List
from src.config import cfg
from src.schemas import PlayerProfile, Persona, GameState


class GameSetup:
    @staticmethod
    def generate_players(user_name: str) -> List[PlayerProfile]:
        """
        Создает список игроков: Люди + Боты, заполняя слоты до total_players.
        """
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

        # 1. Настройки количества
        human_count = 1
        target_total = cfg.gameplay.get("setup", {}).get("total_players", 5)
        bots_needed = max(0, target_total - human_count)

        # 2. Создаем БОТОВ
        for i in range(bots_needed):
            if names:
                bot_name = names.pop()
            else:
                bot_name = f"Bot-{i + 1}"

            bot_prof = profs.pop() if profs else "Безработный"
            bot_trait = traits.pop() if traits else "Обычный человек"

            p_data = random.choice(personalities_data)
            persona = Persona(
                id=p_data["id"],
                description=p_data["description"],
                style_example="",
                multipliers=p_data.get("multipliers", {})
            )

            bot = PlayerProfile(
                name=bot_name,
                profession=bot_prof,
                trait=bot_trait,
                personality=persona,
                is_human=False,
                llm_config=random.choice(bot_models)
            )
            players.append(bot)

        # 3. Создаем ЧЕЛОВЕКА
        human_persona = Persona(
            id="human",
            description="Игрок",
            style_example="",
            multipliers={}
        )

        human = PlayerProfile(
            name=f"{user_name} (Вы)",
            profession=profs.pop() if profs else "Выживший",
            trait=traits.pop() if traits else "Счастливчик",
            personality=human_persona,
            is_human=True
        )
        players.append(human)

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
    def get_display_name(player: PlayerProfile, round_num: int) -> str:
        """
        Форматирует имя для отображения В ТЕЛЕГРАМЕ (UI).
        Раунд 1: Имя [Профессия]
        Раунд 2+: Имя [Профессия, Черта]
        """
        # Если игрок человек - не добавляем лишнего, он знает о себе
        if player.is_human:
            # Можно добавить (Вы), но оно обычно уже есть в player.name
            return f"<b>{player.name}</b> [{player.profession}, {player.trait}]"

        if round_num == 1:
            return f"<b>{player.name}</b> [{player.profession}]"
        else:
            return f"<b>{player.name}</b> [{player.profession}, {player.trait}]"