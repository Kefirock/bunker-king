import uuid
import random
from typing import List, Tuple, Dict
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.games.detective.config import detective_cfg
from src.games.detective.schemas import DetectiveScenario, Fact, FactType, RoleType, DetectivePlayerProfile


class ScenarioGenerationError(Exception):
    pass


class ScenarioGenerator:
    async def generate(self, player_names: List[str]) -> Tuple[DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        count = len(player_names)

        # Промпт теперь не получает player_names, только количество
        system_prompt = detective_cfg.prompts["scenario_writer"]["system"].format(
            player_count=count,
            total_facts=count * 5
        )

        model = core_cfg.models["player_models"][0]
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            print(f"🧠 Детектив: Попытка генерации ({attempt}/{max_attempts})...")

            try:
                current_temp = 0.7 + (attempt * 0.1)
                response = await llm_client.generate(
                    model_config=model,
                    messages=[{"role": "system", "content": system_prompt}],
                    temperature=current_temp,
                    json_mode=True
                )

                data = llm_client.parse_json(response)

                # Валидация структуры
                if not data or "roles" not in data:
                    print(f"⚠️ Попытка {attempt}: Нет поля roles.")
                    continue

                roles_data = data.get("roles", [])

                # Проверяем, хватит ли ролей на всех игроков
                if len(roles_data) < count:
                    print(f"⚠️ Попытка {attempt}: Сгенерировано {len(roles_data)} ролей, нужно {count}.")
                    continue

                # Если все ок, парсим
                return self._parse_scenario(data, player_names)

            except Exception as e:
                print(f"⚠️ Попытка {attempt}: Ошибка {e}")
                continue

        raise ScenarioGenerationError("Не удалось сгенерировать сценарий.")

    def _parse_scenario(self, data: Dict, player_names: List[str]) -> Tuple[
        DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        scenario = DetectiveScenario(
            title=data.get("title", "Unknown Case"),
            description=data.get("description", "..."),
            victim_name=data.get("victim", "Unknown"),
            murder_method=data.get("method", "Unknown"),
            true_solution=data.get("solution", "Unknown")
        )

        player_profiles: Dict[str, DetectivePlayerProfile] = {}
        roles_data = data.get("roles", [])

        # Перемешиваем роли для случайности
        random.shuffle(roles_data)

        # МАППИНГ: Игрок[i] получает Роль[i]
        for i, real_name in enumerate(player_names):
            # Если ролей меньше, чем игроков (теоретически невозможно из-за проверки выше, но для safety)
            role_json = roles_data[i] if i < len(roles_data) else roles_data[0]

            char_name = role_json.get("character_name", f"Персонаж {i + 1}")

            r_str = str(role_json.get("role", "INNOCENT")).upper()
            role_enum = RoleType.KILLER if "KILLER" in r_str else RoleType.INNOCENT

            profile = DetectivePlayerProfile(
                character_name=char_name,  # Запоминаем имя персонажа
                role=role_enum,
                bio=role_json.get("bio", ""),
                secret_objective=role_json.get("secret", "")
            )

            # Парсинг фактов
            raw_facts = role_json.get("facts", [])

            # Добиваем до 5 фактов (дублированием, если мало)
            while len(raw_facts) < 5 and raw_facts:
                raw_facts.append(raw_facts[-1].copy())

            for f_data in raw_facts[:5]:
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

                keyword = f_data.get("keyword")
                if not keyword:
                    words = f_data.get("text", "Улика").split()
                    keyword = " ".join(words[:2]) + "..." if words else "Улика"

                fact = Fact(
                    id=fid,
                    text=f_data.get("text", "???"),
                    keyword=keyword[:20],
                    type=ftype,
                    is_public=False
                )

                scenario.all_facts[fid] = fact
                profile.inventory.append(fid)

            player_profiles[real_name] = profile

        return scenario, player_profiles