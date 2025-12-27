import random
from typing import List
from src.config import cfg
from src.schemas import PlayerProfile, Persona, GameState


class GameSetup:
    @staticmethod
    def generate_players(user_name: str) -> List[PlayerProfile]:
        """
        Создает список игроков: 1 Человек + (N-1) Ботов.
        Данные берутся случайно из scenarios.yaml
        """
        scenarios = cfg.scenarios
        profs = scenarios["professions"][:]
        traits = scenarios["traits"][:]
        names = scenarios["bot_names"][:]
        # Загружаем характеры из конфига
        personalities_data = scenarios.get("personalities", [])

        # Перемешиваем колоды
        random.shuffle(profs)
        random.shuffle(traits)
        random.shuffle(names)

        players = []

        # 1. Создаем БОТОВ
        bot_models = cfg.models["player_models"]

        for i in range(4):
            # Случайный характер из списка
            p_data = random.choice(personalities_data)
            persona = Persona(
                id=p_data["id"],
                description=p_data["description"],
                style_example="",
                multipliers=p_data.get("multipliers", {})
            )

            bot = PlayerProfile(
                name=names[i],
                profession=profs.pop(),
                trait=traits.pop(),
                personality=persona,
                is_human=False,
                llm_config=random.choice(bot_models)
            )
            players.append(bot)

        # 2. Создаем ЧЕЛОВЕКА
        # Человеку даем нейтральный профиль
        human_persona = Persona(
            id="human",
            description="Игрок",
            style_example="",
            multipliers={}
        )

        human = PlayerProfile(
            name=f"{user_name} (Вы)",
            profession=profs.pop(),
            trait=traits.pop(),
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