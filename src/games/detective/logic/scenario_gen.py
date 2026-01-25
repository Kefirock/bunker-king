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
    async def generate(self, player_names: List[str], logger=None) -> Tuple[
        DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        count = len(player_names)
        model = core_cfg.models["player_models"][0]
        max_attempts = 3

        # --- ШАГ 1: ГЕНЕРАЦИЯ СЮЖЕТА И РОЛЕЙ ---

        master_prompt = detective_cfg.prompts["scenario_master"]["system"].format(
            player_count=count
        )

        scenario_data = None

        if logger: logger.log_event("GEN_STEP_1", "Generating Master Scenario")

        for attempt in range(1, max_attempts + 1):
            try:
                print(f"🧠 Шаг 1: Сюжет ({attempt}/{max_attempts})...")
                response = await llm_client.generate(
                    model_config=model,
                    messages=[{"role": "system", "content": master_prompt}],
                    temperature=0.8,
                    json_mode=True
                )
                data = llm_client.parse_json(response)

                # Валидация
                required_fields = ["roles", "victim", "solution"]
                if not data or any(f not in data for f in required_fields) or len(data["roles"]) < count:
                    print("⚠️ Шаг 1: Ошибка структуры.")
                    continue

                scenario_data = data
                break
            except Exception as e:
                print(f"⚠️ Шаг 1 Ошибка: {e}")
                continue

        if not scenario_data:
            raise ScenarioGenerationError("Не удалось сгенерировать сюжет.")

        # --- ШАГ 2: ГЕНЕРАЦИЯ УЛИК (ДЕТАЛИЗАЦИЯ) ---

        # Передаем больше контекста для генератора фактов
        roles_desc = []
        for r in scenario_data["roles"]:
            roles_desc.append(
                f"- Имя: {r.get('character_name')} ({r.get('tag')})\n"
                f"  Роль: {r.get('role')}\n"
                f"  Легенда: {r.get('legend')}\n"
                f"  Секрет: {r.get('secret')}"
            )

        facts_prompt = detective_cfg.prompts["fact_generator"]["system"].format(
            victim=scenario_data.get("victim"),
            cause=scenario_data.get("cause_of_death"),
            location=scenario_data.get("location_of_body"),
            solution=scenario_data.get("solution"),
            characters_list="\n".join(roles_desc)
        )

        facts_data_map = {}

        if logger: logger.log_event("GEN_STEP_2", "Generating Facts")

        try:
            print(f"🧠 Шаг 2: Улики (Фактура)...")
            response_facts = await llm_client.generate(
                model_config=model,
                messages=[{"role": "system", "content": facts_prompt}],
                temperature=0.85,  # Высокая температура для креатива в деталях
                json_mode=True
            )
            parsed_facts = llm_client.parse_json(response_facts)

            for item in parsed_facts.get("facts_by_character", []):
                facts_data_map[item.get("character_name")] = item.get("facts", [])

        except Exception as e:
            print(f"⚠️ Ошибка генерации фактов: {e}")
            if logger: logger.log_event("GEN_FACTS_ERROR", str(e))

        # --- СБОРКА РЕЗУЛЬТАТА ---
        return self._assemble_game_objects(scenario_data, facts_data_map, player_names)

    def _assemble_game_objects(self,
                               scen_data: Dict,
                               facts_map: Dict,
                               player_names: List[str]) -> Tuple[DetectiveScenario, Dict[str, DetectivePlayerProfile]]:

        scenario = DetectiveScenario(
            title=scen_data.get("title", "Дело без названия"),
            description=scen_data.get("description", "..."),
            victim_name=scen_data.get("victim", "Неизвестный"),
            time_of_death=scen_data.get("time_of_death", "Неизвестно"),
            cause_of_death=scen_data.get("cause_of_death", "Неизвестно"),
            location_of_body=scen_data.get("location_of_body", "Неизвестно"),
            murder_method=scen_data.get("method", "Unknown"),
            true_solution=scen_data.get("solution", "Unknown")
        )

        player_profiles: Dict[str, DetectivePlayerProfile] = {}
        roles_data = scen_data.get("roles", [])

        random.shuffle(roles_data)

        for i, real_name in enumerate(player_names):
            role_json = roles_data[i] if i < len(roles_data) else roles_data[0]

            char_name = role_json.get("character_name", f"Персонаж {i + 1}")
            r_str = str(role_json.get("role", "INNOCENT")).upper()
            role_enum = RoleType.KILLER if "KILLER" in r_str else RoleType.INNOCENT

            profile = DetectivePlayerProfile(
                character_name=char_name,
                tag=role_json.get("tag", "Гость"),
                legend=role_json.get("legend", ""),
                role=role_enum,
                secret_objective=role_json.get("secret", "")
            )

            # Достаем факты по имени персонажа
            # (Иногда LLM чуть меняет имя, поэтому можно добавить fuzzy match, но пока строго)
            raw_facts = facts_map.get(char_name, [])

            # Fallback
            while len(raw_facts) < 5:
                raw_facts.append({
                    "text": f"Я заметил что-то странное возле {scenario.location_of_body}, но не придал значения.",
                    "keyword": "Странность",
                    "type": "TESTIMONY"
                })

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

                keyword = f_data.get("keyword", "Улика")
                if len(keyword) > 20: keyword = keyword[:20] + "."

                fact = Fact(
                    id=fid,
                    text=f_data.get("text", "???"),
                    keyword=keyword,
                    type=ftype,
                    is_public=False
                )

                scenario.all_facts[fid] = fact
                profile.inventory.append(fid)

            player_profiles[real_name] = profile

        return scenario, player_profiles