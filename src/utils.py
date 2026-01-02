import random
from typing import List
from src.config import cfg
from src.schemas import PlayerProfile, Persona, GameState


class GameSetup:
    # src/utils.py
    @staticmethod
    def generate_players(user_data: list | str) -> List[PlayerProfile]:
        """
        user_data:
          - Если str: Имя одного игрока (Соло режим)
          - Если list: Список словарей [{'name': 'Max', 'id': 123}, ...] (Мультиплеер)
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

        # 1. Определяем список людей
        humans_to_create = []
        if isinstance(user_data, str):
            humans_to_create.append({"name": user_data})
        elif isinstance(user_data, list):
            humans_to_create = user_data

        # 2. Сколько нужно ботов?
        # Читаем конфиг setup.total_players (по умолчанию 5)
        target_total = cfg.gameplay.get("setup", {}).get("total_players", 5)
        human_count = len(humans_to_create)
        bots_needed = max(0, target_total - human_count)

        # 3. Создаем БОТОВ
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

        # 4. Создаем ЛЮДЕЙ
        human_persona = Persona(id="human", description="Игрок", style_example="", multipliers={})

        for h in humans_to_create:
            p_name = h["name"]
            # Если это соло, добавляем (Вы), если мульти - оставляем ник
            display_name = f"{p_name} (Вы)" if isinstance(user_data, str) else p_name

            players.append(PlayerProfile(
                name=display_name,
                profession=profs.pop() if profs else "Выживший",
                trait=traits.pop() if traits else "Счастливчик",
                personality=human_persona,
                is_human=True,
                # Важно: для мультиплеера можно сохранить ID юзера в поле (хак через personality или отдельное поле, но пока так)
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