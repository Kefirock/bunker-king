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

        # Перемешиваем колоды
        random.shuffle(profs)
        random.shuffle(traits)
        random.shuffle(names)

        players = []

        # 1. Сначала определяем количество людей (пока 1, в будущем список)
        # В будущем здесь будет: humans_list = [user1, user2, ...]
        human_count = 1

        # 2. Считываем целевое количество игроков из конфига
        # Если настройки нет, по умолчанию 5
        target_total = cfg.gameplay.get("setup", {}).get("total_players", 5)

        # Вычисляем, сколько нужно ботов
        bots_needed = max(0, target_total - human_count)

        # 3. Создаем БОТОВ
        for i in range(bots_needed):
            # Безопасное получение имени (если ботов больше, чем имен в списке)
            if names:
                bot_name = names.pop()
            else:
                bot_name = f"Bot-{i + 1}"

            # Безопасное получение профессии (если закончились)
            if profs:
                bot_prof = profs.pop()
            else:
                bot_prof = "Безработный"

            # Безопасное получение черты
            if traits:
                bot_trait = traits.pop()
            else:
                bot_trait = "Обычный человек"

            # Случайный характер
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

        # 4. Создаем ЧЕЛОВЕКА (User)
        human_persona = Persona(
            id="human",
            description="Игрок",
            style_example="",
            multipliers={}
        )

        # Для человека тоже берем уникальные данные, если остались
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