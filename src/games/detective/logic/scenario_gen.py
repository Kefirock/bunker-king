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
        """
        Создает жесткий процедурный каркас сценария.
        Определяет физику мира, алиби и улики ДО того, как нейросеть начнет писать текст.
        """
        mod = detective_cfg.modules

        # 1. ФИЗИКА МИРА
        tech = random.choice(mod.get("tech_levels", [{"name": "1920s"}]))

        # Выбор сеттинга и локаций
        settings_keys = list(mod.get("locations", {}).keys())
        sett_key = random.choice(settings_keys)
        setting_data = mod["locations"][sett_key]
        setting_name = setting_data["name"]
        rooms = setting_data["rooms"]

        # Преступление
        victim = random.choice(mod.get("victims", ["Тиран"]))
        method = random.choice(mod.get("methods", ["Яд"]))

        # 2. РАСПРЕДЕЛЕНИЕ РОЛЕЙ (Абстрактное)
        # Нам нужно распределить роли для player_count персонажей
        # 0 = Убийца
        # 1 = "Вор" (Red Herring) - ложная цель
        # Остальные = Невиновные

        roles_logic = []
        for i in range(player_count):
            if i == 0:
                role_type = "KILLER"
                objective = "Скрыть преступление."
            elif i == 1:
                role_type = "INNOCENT"
                # Берем случайную побочную цель
                sec_obj = random.choice(mod.get("secondary_objectives", [{"name": "Вор"}]))
                objective = f"ВТОРИЧНАЯ ЦЕЛЬ: {sec_obj['name']} ({sec_obj['desc']}). Вести себя подозрительно, но не из-за убийства."
            else:
                role_type = "INNOCENT"
                objective = "Найти убийцу."

            roles_logic.append({
                "id": i,
                "type": role_type,
                "obj": objective,
                "room": random.choice(rooms)  # Предварительная локация
            })

        random.shuffle(roles_logic)  # Перемешиваем, чтобы убийца не всегда был первым

        # 3. АЛИБИ-МАТРИЦА (Кто с кем был)
        # Группируем персонажей по комнатам в момент убийства
        # Убийца должен быть в комнате с Жертвой (или иметь возможность)
        # Остальные могут быть парами (сильное алиби) или по одному (слабое)

        killer = next(r for r in roles_logic if r["type"] == "KILLER")
        crime_scene = random.choice(rooms)  # Место убийства

        alibi_report = []
        alibi_report.append(f"- МЕСТО УБИЙСТВА: {crime_scene}. ВРЕМЯ: 23:00.")
        alibi_report.append(f"- УБИЙЦА (Персонаж #{killer['id'] + 1}) был на месте преступления, но будет лгать.")

        # Распределяем остальных
        others = [r for r in roles_logic if r["type"] != "KILLER"]
        # Создаем хотя бы одну пару для "Железного алиби"
        if len(others) >= 2:
            pair_room = random.choice([r for r in rooms if r != crime_scene])
            p1 = others.pop()
            p2 = others.pop()
            alibi_report.append(
                f"- Персонаж #{p1['id'] + 1} и Персонаж #{p2['id'] + 1} были ВМЕСТЕ в локации '{pair_room}'. Они подтверждают алиби друг друга.")

        # Оставшиеся по одному
        for p in others:
            solo_room = random.choice([r for r in rooms if r != crime_scene])
            alibi_report.append(f"- Персонаж #{p['id'] + 1} был ОДИН в локации '{solo_room}'. Алиби слабое.")

        # 4. МАРКЕРЫ (Следы на одежде)
        markers_report = []
        # Маркер для убийцы (связан с методом или борьбой)
        k_marker = random.choice(mod.get("markers", [{"text": "Грязь"}]))
        markers_report.append(f"- УБИЙЦА имеет маркер: {k_marker['text']} ({k_marker['implication']}).")

        # Маркер для "Вора" (или любого другого для путаницы)
        innocent_suspect = next((r for r in roles_logic if "ВТОРИЧНАЯ ЦЕЛЬ" in r["obj"]), None)
        if innocent_suspect:
            i_marker = random.choice(mod.get("markers", [{"text": "Нервный вид"}]))
            markers_report.append(
                f"- ПОДОЗРЕВАЕМЫЙ (Персонаж #{innocent_suspect['id'] + 1}) имеет маркер: {i_marker['text']}.")

        # --- СБОРКА ИТОГОВОГО ТЗ ---
        skeleton = (
            f"=== ФИЗИКА МИРА ===\n"
            f"ЭПОХА: {tech['name']} ({tech['constraints']})\n"
            f"СЕТТИНГ: {setting_name}\n"
            f"ДОСТУПНЫЕ КОМНАТЫ: {', '.join(rooms)}\n\n"

            f"=== ПРЕСТУПЛЕНИЕ ===\n"
            f"ЖЕРТВА: {victim}\n"
            f"СПОСОБ: {method}\n"
            f"ЛОКАЦИЯ ТЕЛА: {crime_scene}\n\n"

            f"=== РОЛИ И ЦЕЛИ (ДЛЯ ГЕНЕРАЦИИ) ===\n"
        )

        for r in roles_logic:
            skeleton += f"Персонаж #{r['id'] + 1}: Роль {r['type']}. {r['obj']}\n"

        skeleton += f"\n=== АЛИБИ И ФАКТЫ (ОБЯЗАТЕЛЬНО ИСПОЛЬЗОВАТЬ) ===\n"
        skeleton += "\n".join(alibi_report)
        skeleton += "\n" + "\n".join(markers_report)

        return skeleton

    async def generate(self, player_names: List[str], logger=None) -> Tuple[
        DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        count = len(player_names)
        model = core_cfg.models["player_models"][0]
        max_attempts = 3

        # --- ШАГ 0: РЕЖИССЕРСКИЙ ПУЛЬТ ---
        # Генерируем жесткую логику до обращения к LLM
        plot_skeleton = self._build_advanced_skeleton(count)

        if logger:
            logger.log_event("DIRECTOR_MODE", "Advanced skeleton assembled", {"skeleton": plot_skeleton})
            # print(f"🎬 Режиссер собрал сюжет:\n{plot_skeleton}") # Debug print

        # --- ШАГ 1: ГЕНЕРАЦИЯ ЛИТЕРАТУРНОГО СЦЕНАРИЯ ---

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

        # Передаем в генератор фактов тот же скелет, чтобы он знал про маркеры и алиби
        facts_prompt = detective_cfg.prompts["fact_generator"]["system"].format(
            victim=scenario_data.get("victim"),
            cause=scenario_data.get("cause_of_death"),
            location=scenario_data.get("location_of_body"),
            solution=scenario_data.get("solution"),
            timeline=plot_skeleton,  # <--- ВАЖНО: Передаем рассчитанный скелет как "Истину"
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
                else:
                    print(f"⚠️ Шаг 2: Неполные факты ({valid_count}/{len(expected_chars)}). Retry.")

            except Exception as e:
                print(f"⚠️ Ошибка генерации фактов: {e}")
                if logger: logger.log_event("GEN_FACTS_ERROR", str(e))

        return self._assemble_game_objects(scenario_data, facts_data_map, player_names, plot_skeleton)

    def _assemble_game_objects(self,
                               scen_data: Dict,
                               facts_map: Dict,
                               player_names: List[str],
                               timeline_truth: str) -> Tuple[DetectiveScenario, Dict[str, DetectivePlayerProfile]]:

        scenario = DetectiveScenario(
            title=scen_data.get("title", "Дело без названия"),
            description=scen_data.get("description", "..."),
            victim_name=scen_data.get("victim", "Неизвестный"),
            time_of_death=scen_data.get("time_of_death", "Неизвестно"),
            cause_of_death=scen_data.get("cause_of_death", "Неизвестно"),
            location_of_body=scen_data.get("location_of_body", "Неизвестно"),
            murder_method=scen_data.get("method", "Unknown"),
            true_solution=scen_data.get("solution", "Unknown"),
            timeline_truth=timeline_truth  # Сохраняем скелет как истину
        )

        player_profiles: Dict[str, DetectivePlayerProfile] = {}
        roles_data = scen_data.get("roles", [])

        random.shuffle(roles_data)

        for i, real_name in enumerate(player_names):
            role_json = roles_data[i] if i < len(roles_data) else roles_data[0]

            char_name = role_json.get("character_name", f"Персонаж {i + 1}")
            r_str = str(role_json.get("role", "INNOCENT")).upper()
            role_enum = RoleType.KILLER if "KILLER" in r_str else RoleType.INNOCENT

            # Извлекаем маркеры и локации из скелета (сложно распарсить текст обратно,
            # поэтому пока просто сохраняем легенду, которая должна была быть сгенерирована на основе скелета)

            profile = DetectivePlayerProfile(
                character_name=char_name,
                tag=role_json.get("tag", "Гость"),
                legend=role_json.get("legend", ""),
                role=role_enum,
                secret_objective=role_json.get("secret", "")
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
                raise ScenarioGenerationError(f"Нейросеть не сгенерировала достаточно улик для {char_name}.")

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