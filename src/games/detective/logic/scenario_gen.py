import uuid
import random
import difflib
from typing import List, Tuple, Dict, Any
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.games.detective.config import detective_cfg
from src.games.detective.schemas import DetectiveScenario, Fact, FactType, RoleType, DetectivePlayerProfile


class ScenarioGenerationError(Exception):
    pass


class ScenarioGenerator:
    def _build_advanced_skeleton(self, player_count: int) -> str:
        mod = detective_cfg.modules

        if not mod or "locations" not in mod:
            print("⚠️ WARNING: 'modules.yaml' issue. Using defaults.")
            mod = {
                "tech_levels": [{"name": "1920s", "constraints": "Классика."}],
                "locations": {"mansion": {"name": "Особняк", "rooms": ["Холл", "Сад", "Кухня"]}},
                "victims": ["Хозяин"], "methods": ["Яд"], "motives": ["Деньги"],
                "twists": ["Нет"], "markers": [{"text": "Пятно", "implication": "Грязь"}],
                "secondary_objectives": [{"name": "Вор", "desc": "Украл"}]
            }

        tech = random.choice(mod.get("tech_levels", [{"name": "1920s"}]))

        settings_keys = list(mod.get("locations", {}).keys())
        if not settings_keys: settings_keys = ["mansion"]
        sett_key = random.choice(settings_keys)
        setting_data = mod["locations"].get(sett_key, {"name": "Дом", "rooms": ["Холл"]})
        setting_name = setting_data["name"]
        rooms = setting_data["rooms"]

        victim = random.choice(mod.get("victims", ["Жертва"]))
        method = random.choice(mod.get("methods", ["Удар"]))

        roles_logic = []
        for i in range(player_count):
            if i == 0:
                role_type = "KILLER"
                objective = "Скрыть преступление, путать следы."
            elif i == 1:
                role_type = "INNOCENT"
                sec_obj = random.choice(
                    mod.get("secondary_objectives", [{"name": "Свидетель", "desc": "Видел лишнее"}]))
                objective = f"ВТОРИЧНАЯ ЦЕЛЬ: {sec_obj['name']} ({sec_obj['desc']})."
            else:
                role_type = "INNOCENT"
                objective = "Найти убийцу."

            roles_logic.append({
                "id": i,
                "type": role_type,
                "obj": objective,
                "is_finder": False  # По умолчанию
            })

        random.shuffle(roles_logic)

        # Назначаем НАШЕДШЕГО (того, кто нашел тело)
        # Лучше всего, если это невиновный, но для интриги может быть кто угодно.
        # Берем первого попавшегося INNOCENT
        finder = next((r for r in roles_logic if r["type"] == "INNOCENT"), roles_logic[0])
        finder["is_finder"] = True
        finder["obj"] += " ТЫ НАШЕЛ ТЕЛО. Опиши этот момент в своей легенде."

        killer = next(r for r in roles_logic if r["type"] == "KILLER")
        crime_scene = random.choice(rooms) if rooms else "Кабинет"

        alibi_report = []
        alibi_report.append(f"- МЕСТО УБИЙСТВА: {crime_scene}. ВРЕМЯ: ~23:00.")
        alibi_report.append(f"- УБИЙЦА (Персонаж #{killer['id'] + 1}) был на месте преступления.")

        others = [r for r in roles_logic if r["type"] != "KILLER"]

        for p in others:
            safe_rooms = [r for r in rooms if r != crime_scene]
            solo_room = random.choice(safe_rooms) if safe_rooms else "Коридор"
            alibi_report.append(f"- Персонаж #{p['id'] + 1} был ОДИН в локации '{solo_room}'. Алиби не подтверждено.")

        markers_report = []
        k_marker = random.choice(mod.get("markers", [{"text": "Нервозность", "implication": "Страх"}]))
        markers_report.append(f"- УБИЙЦА имеет след: {k_marker['text']} ({k_marker['implication']}).")

        innocent_suspect = next((r for r in roles_logic if "ВТОРИЧНАЯ ЦЕЛЬ" in r["obj"]), None)
        if innocent_suspect:
            i_marker = random.choice(mod.get("markers", [{"text": "Пятно", "implication": "Грязь"}]))
            markers_report.append(
                f"- ПОДОЗРЕВАЕМЫЙ (Персонаж #{innocent_suspect['id'] + 1}) имеет след: {i_marker['text']} (Ложный след).")

        skeleton = (
            f"=== ФИЗИКА МИРА ===\n"
            f"ЭПОХА: {tech.get('name', '20s')}\n"
            f"СЕТТИНГ: {setting_name}\n"
            f"ДОСТУПНЫЕ КОМНАТЫ: {', '.join(rooms)}\n\n"

            f"=== ПРЕСТУПЛЕНИЕ ===\n"
            f"ЖЕРТВА: {victim}\n"
            f"СПОСОБ: {method}\n"
            f"ЛОКАЦИЯ ТЕЛА: {crime_scene}\n\n"

            f"=== РОЛИ И ЦЕЛИ ===\n"
        )

        for r in roles_logic:
            role_desc = f"Персонаж #{r['id'] + 1}: Роль {r['type']}."
            if r['is_finder']: role_desc += " [НАШЕДШИЙ ТЕЛО!]"
            skeleton += f"{role_desc} {r['obj']}\n"

        skeleton += f"\n=== АЛИБИ И СЛЕДЫ (СЛАБЫЕ) ===\n"
        skeleton += "\n".join(alibi_report)
        skeleton += "\n" + "\n".join(markers_report)

        return skeleton

    async def generate(self, player_names: List[str], logger=None) -> Tuple[
        DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        count = len(player_names)
        model = core_cfg.models["player_models"][0]
        max_attempts = 3

        # ШАГ 0
        try:
            plot_skeleton = self._build_advanced_skeleton(count)
        except Exception as e:
            raise ScenarioGenerationError(f"Ошибка сборки скелета: {e}")

        if logger:
            logger.log_event("DIRECTOR_MODE", "Skeleton assembled", {"skeleton": plot_skeleton})

        # ШАГ 1
        master_prompt = detective_cfg.prompts["scenario_master"]["system"].format(
            player_count=count,
            plot_skeleton=plot_skeleton
        )

        scenario_data = None

        if logger: logger.log_event("GEN_STEP_1", "Generating Master Scenario")

        for attempt in range(1, max_attempts + 1):
            try:
                print(f"🧠 Шаг 1: Сюжет ({attempt}/{max_attempts})...")
                response = await llm_client.generate(
                    model_config=model,
                    messages=[{"role": "system", "content": master_prompt}],
                    temperature=0.85,
                    json_mode=True
                )
                data = llm_client.parse_json(response)

                required_fields = ["roles", "victim", "solution"]
                if not data or any(f not in data for f in required_fields) or len(data["roles"]) < count:
                    continue

                scenario_data = data
                break
            except Exception as e:
                print(f"⚠️ Шаг 1 Ошибка: {e}")
                continue

        if not scenario_data:
            raise ScenarioGenerationError("Не удалось сгенерировать сюжет.")

        # ШАГ 2
        roles_desc = []
        expected_chars = []
        for r in scenario_data["roles"]:
            char_name = r.get('character_name', 'Unknown')
            expected_chars.append(char_name)
            roles_desc.append(
                f"- Имя: {char_name} ({r.get('tag')})\n"
                f"  Роль: {r.get('role')}\n"
                f"  Легенда: {r.get('legend')}\n"
                f"  Секрет: {r.get('secret')}"
            )

        timeline_info = scenario_data.get("timeline_truth", plot_skeleton)

        facts_prompt = detective_cfg.prompts["fact_generator"]["system"].format(
            victim=scenario_data.get("victim"),
            cause=scenario_data.get("cause_of_death"),
            location=scenario_data.get("location_of_body"),
            solution=scenario_data.get("solution"),
            timeline=timeline_info,
            characters_list="\n".join(roles_desc)
        )

        facts_data_map = {}

        if logger: logger.log_event("GEN_STEP_2", "Generating Facts")

        for attempt in range(1, 3):
            try:
                print(f"🧠 Шаг 2: Улики (Попытка {attempt})...")
                response_facts = await llm_client.generate(
                    model_config=model,
                    messages=[{"role": "system", "content": facts_prompt}],
                    temperature=0.6,
                    json_mode=True
                )
                parsed_facts = llm_client.parse_json(response_facts)

                temp_map = {}
                for item in parsed_facts.get("facts_by_character", []):
                    c_name = item.get("character_name", "").strip()
                    if c_name:
                        temp_map[c_name] = item.get("facts", [])

                valid_count = 0
                for char in expected_chars:
                    if char in temp_map or any(char in k for k in temp_map.keys()):
                        valid_count += 1

                if valid_count >= len(expected_chars):
                    facts_data_map = temp_map
                    break
            except Exception as e:
                print(f"⚠️ Ошибка генерации фактов: {e}")

        return self._assemble_game_objects(scenario_data, facts_data_map, player_names, plot_skeleton)

    def _assemble_game_objects(self,
                               scen_data: Dict,
                               facts_map: Dict,
                               player_names: List[str],
                               timeline_truth: str) -> Tuple[DetectiveScenario, Dict[str, DetectivePlayerProfile]]:

        real_cause = scen_data.get("real_cause")
        if not real_cause:
            real_cause = scen_data.get("cause_of_death", "Неизвестно")

        apparent_cause = scen_data.get("apparent_cause")
        if not apparent_cause:
            apparent_cause = "Остановка сердца"

        scenario = DetectiveScenario(
            title=scen_data.get("title", "Дело без названия"),
            description=scen_data.get("description", "..."),
            victim_name=scen_data.get("victim", "Неизвестный"),
            time_of_death=scen_data.get("time_of_death", "Неизвестно"),

            real_cause=real_cause,
            apparent_cause=apparent_cause,
            cause_of_death=real_cause,

            location_of_body=scen_data.get("location_of_body", "Неизвестно"),
            murder_method=scen_data.get("method", "Unknown"),
            true_solution=scen_data.get("solution", "Unknown"),
            timeline_truth=timeline_truth
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
                secret_objective=role_json.get("secret", ""),
                is_finder=role_json.get("is_finder", False)  # <--- Парсим флаг Нашедшего
            )

            # Факты
            raw_facts = []
            if char_name in facts_map:
                raw_facts = facts_map[char_name]
            else:
                best_match = None
                for key in facts_map.keys():
                    if difflib.SequenceMatcher(None, char_name, key).ratio() > 0.8:
                        best_match = key
                        break
                if best_match:
                    raw_facts = facts_map[best_match]

            if len(raw_facts) < 5:
                # Fallback
                raw_facts.append({
                    "text": "Я был в своей комнате и ничего не слышал.",
                    "keyword": "Тишина",
                    "type": "ALIBI"
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
                if len(keyword) > 25: keyword = keyword[:25] + "."

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