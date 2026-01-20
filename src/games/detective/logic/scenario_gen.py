import json
import uuid
from typing import List, Tuple, Dict, Any
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.games.detective.config import detective_cfg
from src.games.detective.schemas import DetectiveScenario, Fact, FactType, RoleType, DetectivePlayerProfile


class ScenarioGenerator:
    async def generate(self, player_names: List[str]) -> Tuple[DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        """
        Генерирует сценарий и профили для заданных игроков.
        Возвращает (Сценарий, Словарь {имя_игрока: Профиль})
        """
        count = len(player_names)
        # Генерируем с запасом: 3 факта на игрока + 2 общих
        total_facts = count * 3 + 2

        system_prompt = detective_cfg.prompts["scenario_writer"]["system"].format(
            player_count=count,
            player_names=", ".join(player_names),
            total_facts=total_facts
        )

        # Используем модель "поумнее" для сценария (например, Llama-70b или Qwen)
        model = core_cfg.models["player_models"][0]

        response = await llm_client.generate(
            model_config=model,
            messages=[{"role": "system", "content": system_prompt}],
            temperature=0.7,
            json_mode=True
        )

        data = llm_client.parse_json(response)

        # --- ПАРСИНГ ДАННЫХ ---

        # 1. Создаем объект Сценария
        scenario = DetectiveScenario(
            title=data.get("title", "Загадочное убийство"),
            description=data.get("description", "Случилось страшное..."),
            victim_name=data.get("victim", "Джон Доу"),
            murder_method=data.get("method", "Неизвестно"),
            true_solution=data.get("solution", "Убийца дворецкий")
        )

        # 2. Парсим Роли
        player_profiles: Dict[str, DetectivePlayerProfile] = {}
        roles_data = data.get("roles", [])

        # Fallback, если AI забыл кого-то
        for name in player_names:
            p_data = next((r for r in roles_data if r.get("player_name") == name), {})

            role_str = p_data.get("role", "INNOCENT").upper()
            role_enum = RoleType.KILLER if role_str == "KILLER" else RoleType.INNOCENT

            profile = DetectivePlayerProfile(
                role=role_enum,
                bio=p_data.get("bio", "Вы обычный свидетель."),
                secret_objective=p_data.get("secret", "Выжить.")
            )
            player_profiles[name] = profile

        # 3. Парсим Факты и раздаем их
        facts_data = data.get("facts", [])

        for f_data in facts_data:
            # Генерируем ID
            fid = str(uuid.uuid4())[:8]

            # Определяем тип
            ftype_str = f_data.get("type", "TESTIMONY").upper()
            try:
                ftype = FactType(ftype_str)
            except:
                ftype = FactType.TESTIMONY

            # Определяем владельца
            owner_name = f_data.get("owner_name")
            owner_id = None

            # Ищем ID игрока по имени (в реальной игре у нас есть ID, но генератор работает с именами)
            # Здесь мы пока просто привязываем факт к Имени, а GameEngine потом переведет это в ID

            fact = Fact(
                id=fid,
                text=f_data.get("text", "???"),
                type=ftype,
                is_public=False
            )

            scenario.all_facts[fid] = fact

            # Раздача в "руку"
            if owner_name and owner_name in player_profiles:
                player_profiles[owner_name].inventory.append(fid)
            else:
                # Если владелец не указан или AI ошибся в имени -> делаем публичным сразу или общим
                # Для упрощения: раздаем случайному, у кого мало карт
                target = min(player_profiles.values(), key=lambda p: len(p.inventory))
                target.inventory.append(fid)

        return scenario, player_profiles