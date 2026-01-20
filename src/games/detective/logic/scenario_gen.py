import uuid
import random
from typing import List, Tuple, Dict
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.games.detective.config import detective_cfg
from src.games.detective.schemas import DetectiveScenario, Fact, FactType, RoleType, DetectivePlayerProfile


class ScenarioGenerationError(Exception):
    """Исключение, выбрасываемое, если нейросеть не справилась с генерацией."""
    pass


class ScenarioGenerator:
    async def generate(self, player_names: List[str]) -> Tuple[DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        count = len(player_names)
        # Если игроков мало, просим сценарий минимум на 3 персоны, чтобы сюжет был интереснее
        total_facts = max(5, count * 2 + 1)

        system_prompt = detective_cfg.prompts["scenario_writer"]["system"].format(
            player_count=count,
            player_names=", ".join(player_names),
            total_facts=total_facts
        )

        model = core_cfg.models["player_models"][0]
        max_attempts = 3

        # --- ЦИКЛ ПОПЫТОК (RETRY LOOP) ---
        for attempt in range(1, max_attempts + 1):
            print(f"🧠 Детектив: Попытка генерации сценария ({attempt}/{max_attempts})...")

            try:
                # С каждой попыткой немного увеличиваем температуру для вариативности
                current_temp = 0.7 + (attempt * 0.1)

                response = await llm_client.generate(
                    model_config=model,
                    messages=[{"role": "system", "content": system_prompt}],
                    temperature=current_temp,
                    json_mode=True
                )

                data = llm_client.parse_json(response)

                # --- СТРОГАЯ ВАЛИДАЦИЯ ---
                if not data or "facts" not in data or "roles" not in data:
                    print(f"⚠️ Попытка {attempt}: Битый JSON или отсутствуют обязательные поля.")
                    continue

                roles_data = data.get("roles", [])
                generated_names = [r.get("player_name") for r in roles_data]

                # Проверяем, для всех ли игроков создана роль
                # Нейросеть обязана вернуть роль для КАЖДОГО имени из player_names
                missing_players = [name for name in player_names if name not in generated_names]

                if missing_players:
                    print(f"⚠️ Попытка {attempt}: Нейросеть забыла игроков: {missing_players}.")
                    continue  # Идем на следующую попытку

                # Если дошли сюда — генерация успешна!
                print(f"✅ Сценарий успешно сгенерирован с {attempt} попытки.")
                return self._parse_scenario(data, player_names)

            except Exception as e:
                print(f"⚠️ Попытка {attempt}: Критическая ошибка генерации: {e}")
                continue

        # Если цикл завершился без успеха:
        raise ScenarioGenerationError("Нейросеть не смогла сгенерировать связный сценарий за 3 попытки.")

    def _parse_scenario(self, data: Dict, player_names: List[str]) -> Tuple[
        DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        """Конвертирует сырой валидный JSON в объекты игры"""
        scenario = DetectiveScenario(
            title=data.get("title", "Unknown Case"),
            description=data.get("description", "..."),
            victim_name=data.get("victim", "Unknown"),
            murder_method=data.get("method", "Unknown"),
            true_solution=data.get("solution", "Unknown")
        )

        player_profiles: Dict[str, DetectivePlayerProfile] = {}
        roles_data = data.get("roles", [])
        facts_data = data.get("facts", [])

        # 1. Привязка Ролей
        for name in player_names:
            # Здесь мы уверены, что роль есть, благодаря валидации выше
            p_data = next((r for r in roles_data if r.get("player_name") == name))

            r_str = str(p_data.get("role", "INNOCENT")).upper()
            role_enum = RoleType.KILLER if "KILLER" in r_str else RoleType.INNOCENT

            player_profiles[name] = DetectivePlayerProfile(
                role=role_enum,
                bio=p_data.get("bio", ""),
                secret_objective=p_data.get("secret", "")
            )

        # 2. Создание Фактов
        random.shuffle(facts_data)

        for f_data in facts_data:
            fid = str(uuid.uuid4())[:8]

            ftype_str = str(f_data.get("type", "TESTIMONY")).upper()
            if "PHYSICAL" in ftype_str:
                ftype = FactType.PHYSICAL
            elif "MOTIVE" in ftype_str:
                ftype = FactType.MOTIVE
            elif "ALIBI" in ftype_str:
                ftype = FactType.ALIBI
            else:
                ftype = FactType.TESTIMONY

            # Генерация заголовка (keyword) для кнопки
            keyword = f_data.get("keyword")
            if not keyword:
                words = f_data.get("text", "Улика").split()
                # Берем первые 2-3 слова, если нейросеть забыла сгенерировать keyword
                keyword = " ".join(words[:2]) + "..." if words else "Улика"

            fact = Fact(
                id=fid,
                text=f_data.get("text", "???"),
                keyword=keyword[:20],  # Обрезаем, чтобы влезло в кнопку
                type=ftype,
                is_public=False
            )

            scenario.all_facts[fid] = fact

            # 3. Раздача Фактов
            owner_name = f_data.get("owner_name")
            target_profile = None

            if owner_name and owner_name in player_profiles:
                target_profile = player_profiles[owner_name]
            else:
                # Если владелец не указан, даем тому, у кого меньше карт (балансировка)
                target_profile = min(player_profiles.values(), key=lambda p: len(p.inventory))

            target_profile.inventory.append(fid)

        return scenario, player_profiles