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
        """
        mod = detective_cfg.modules

        # Fallback
        if not mod or "locations" not in mod:
            print("⚠️ WARNING: 'modules.yaml' issue. Using defaults.")
            mod = {
                "tech_levels": [{"name": "1920s", "constraints": "Классика."}],
                "locations": {"mansion": {"name": "Особняк", "rooms": ["Холл", "Сад", "Кухня"]}},
                "victims": ["Хозяин"], "methods": ["Яд"], "motives": ["Деньги"],
                "twists": ["Нет"], "markers": [{"text": "Пятно", "implication": "Грязь"}],
                "secondary_objectives": [{"name": "Вор", "desc": "Украл"}]
            }

        # 1. ФИЗИКА МИРА
        tech = random.choice(mod.get("tech_levels", [{"name": "1920s"}]))

        settings_keys = list(mod.get("locations", {}).keys())
        if not settings_keys: settings_keys = ["mansion"]
        sett_key = random.choice(settings_keys)
        setting_data = mod["locations"].get(sett_key, {"name": "Дом", "rooms": ["Холл"]})
        setting_name = setting_data["name"]
        rooms = setting_data["rooms"]

        victim = random.choice(mod.get("victims", ["Жертва"]))
        method = random.choice(mod.get("methods", ["Удар"]))

        # 2. РАСПРЕДЕЛЕНИЕ РОЛЕЙ
        roles_logic = []
        for i in range(player_count):
            if i == 0:
                role_type = "KILLER"
                # Явно указываем, что нужна маскировка
                objective = "Внутренняя роль: УБИЙЦА. Публичная маска: Придумай мирную профессию (Врач, Гость, и т.д.). Цель: Скрыть преступление."
            elif i == 1:
                role_type = "INNOCENT"
                sec_obj = random.choice(
                    mod.get("secondary_objectives", [{"name": "Свидетель", "desc": "Видел лишнее"}]))
                objective = f"ВТОРИЧНАЯ ЦЕЛЬ: {sec_obj['name']} ({sec_obj['desc']}). Вести себя подозрительно, но не из-за убийства."
            else:
                role_type = "INNOCENT"
                objective = "Найти убийцу, но скрыть свои мелкие тайны."

            roles_logic.append({
                "id": i,
                "type": role_type,
                "obj": objective
            })

        random.shuffle(roles_logic)

        # Назначаем НАШЕДШЕГО (Finder)
        # Ищем первого невиновного для этой роли
        finder_found = False
        for r in roles_logic:
            if r["type"] == "INNOCENT":
                r["is_finder"] = True
                r["obj"] += " ВАЖНО: ТЫ НАШЕЛ ТЕЛО. Опиши этот момент в своей легенде."
                finder_found = True
                break

        # Если вдруг все киллеры (невозможно, но для safety), назначаем первого
        if not finder_found:
            roles_logic[0]["is_finder"] = True

        # 3. АЛИБИ-МАТРИЦА
        killer = next(r for r in roles_logic if r["type"] == "KILLER")
        crime_scene = random.choice(rooms) if rooms else "Кабинет"

        alibi_report = []
        alibi_report.append(f"- МЕСТО УБИЙСТВА: {crime_scene}. ВРЕМЯ: ~23:00.")
        alibi_report.append(
            f"- УБИЙЦА (Персонаж #{killer['id'] + 1}) был на месте преступления. Должен придумать ложное алиби.")

        others = [r for r in roles_logic if r["type"] != "KILLER"]

        for p in others:
            safe_rooms = [r for r in rooms if r != crime_scene]
            solo_room = random.choice(safe_rooms) if safe_rooms else "Коридор"
            alibi_report.append(
                f"- Персонаж #{p['id'] + 1} был ОДИН в локации '{solo_room}'. Алиби слабое/неподтвержденное.")

        # 4. МАРКЕРЫ
        markers_report = []
        k_marker = random.choice(mod.get("markers", [{"text": "Нервозность", "implication": "Страх"}]))
        markers_report.append(f"- УБИЙЦА имеет след: {k_marker['text']} ({k_marker['implication']}).")

        innocent_suspect = next((r for r in roles_logic if "ВТОРИЧНАЯ ЦЕЛЬ" in r["obj"]), None)
        if innocent_suspect:
            i_marker = random.choice(mod.get("markers", [{"text": "Пятно", "implication": "Грязь"}]))
            markers_report.append(
                f"- ПОДОЗРЕВАЕМЫЙ (Персонаж #{innocent_suspect['id'] + 1}) имеет след: {i_marker['text']} (Ложный след).")

        # СБОРКА
        skeleton = (
            f"=== ФИЗИКА МИРА ===\n"
            f"ЭПОХА: {tech.get('name', '20s')}\n"
            f"СЕТТИНГ: {setting_name}\n"
            f"ДОСТУПНЫЕ КОМНАТЫ: {', '.join(rooms)}\n\n"

            f"=== ПРЕСТУПЛЕНИЕ ===\n"
            f"ЖЕРТВА: {victim}\n"
            f"СПОСОБ: {method}\n"
            f"ЛОКАЦИЯ ТЕЛА: {crime_scene}\n\n"

            f"=== РОЛИ И ЦЕЛИ (ДЛЯ ГЕНЕРАЦИИ) ===\n"
        )

        for r in roles_logic:
            role_desc = f"Персонаж #{r['id'] + 1}: Роль {r['type']}."
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
                    print("⚠️ Шаг 1: Ошибка структуры.")
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
            tag = r.get('tag', 'Гость')

            # SAFETY FILTER: Убираем спойлеры из тегов
            forbidden_tags = ["KILLER", "MURDERER", "УБИЙЦА", "LOVER", "ЛЮБОВНИК", "THIEF", "ВОР"]
            if any(bad in tag.upper() for bad in forbidden_tags):
                tag = "Гость"
                r["tag"] = tag  # Обновляем в данных

            expected_chars.append(char_name)
            roles_desc.append(
                f"- Имя: {char_name} ({tag})\n"
                f"  Роль: {r.get('role')}\n"
                f"  Легенда: {r.get('legend')}\n"
                f"  Секрет: {r.get('secret')}"
            )

        timeline_info = scenario_data.get("timeline_truth", plot_skeleton)

        facts_prompt = detective_cfg.prompts["fact_generator"]["system"].format(
            victim=scenario_data.get("victim"),
            # Берем истинную причину для генератора фактов, чтобы он мог создавать улики
            cause=scenario_data.get("cause_of_death", "Неизвестно"),
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

        # Извлекаем причины смерти
        real_cause = scen_data.get("real_cause")
        if not real_cause:
            # Если LLM забыла real_cause, берем cause_of_death
            real_cause = scen_data.get("cause_of_death", "Неизвестно")

        apparent_cause = scen_data.get("apparent_cause")
        if not apparent_cause:
            # Если нет видимой причины, генерируем заглушку на основе реальной
            apparent_cause = "Остановка сердца (причина неясна)"

        scenario = DetectiveScenario(
            title=scen_data.get("title", "Дело без названия"),
            description=scen_data.get("description", "..."),
            victim_name=scen_data.get("victim", "Неизвестный"),
            time_of_death=scen_data.get("time_of_death", "Неизвестно"),

            real_cause=real_cause,
            apparent_cause=apparent_cause,

            # Для обратной совместимости
            cause_of_death=real_cause,

            location_of_body=scen_data.get("location_of_body", "Неизвестно"),
            murder_method=scen_data.get("method", "Unknown"),
            true_solution=scen_data.get("solution", "Unknown"),
            timeline_truth=timeline_truth,
            fact_graph=[]  # Будет заполнено после генерации противоречий
        )
        
        # Генерация противоречий после создания фактов
        scenario.fact_graph = self._generate_fact_contractions(scenario.all_facts)
        
        # Сохранение ложных следов
        red_herrings_data = scen_data.get("red_herrings", [])
        for rh in red_herrings_data:
            from src.games.detective.schemas import RedHerring
            scenario.red_herrings.append(RedHerring(
                character_name=rh.get("character_name", ""),
                suspicious_secret=rh.get("suspicious_secret", ""),
                actual_truth=rh.get("actual_truth", "")
            ))

        player_profiles: Dict[str, DetectivePlayerProfile] = {}
        roles_data = scen_data.get("roles", [])

        random.shuffle(roles_data)

        for i, real_name in enumerate(player_names):
            role_json = roles_data[i] if i < len(roles_data) else roles_data[0]

            char_name = role_json.get("character_name", f"Персонаж {i + 1}")
            r_str = str(role_json.get("role", "INNOCENT")).upper()
            role_enum = RoleType.KILLER if "KILLER" in r_str else RoleType.INNOCENT

            # Используем очищенный тег из JSON или дефолтный
            tag = role_json.get("tag", "Гость")

            profile = DetectivePlayerProfile(
                character_name=char_name,
                tag=tag,
                legend=role_json.get("legend", ""),
                role=role_enum,
                secret_objective=role_json.get("secret", ""),
                is_finder=role_json.get("is_finder", False)
            )

            # --- ПОИСК ФАКТОВ ---
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

    def _generate_fact_contractions(self, all_facts: Dict[str, Fact]) -> List:
        """Генерация противоречий между фактами на основе анализа текста"""
        from src.games.detective.schemas import FactConnection
        
        facts_list = list(all_facts.values())
        if len(facts_list) < 4:
            return []
        
        # Простая эвристика: ищем факты с противоположными утверждениями
        # Ключевые слова для поиска противоречий
        contradiction_pairs = []
        
        # Словари для поиска противоречий
        time_mentions = {}  # время -> [fact_ids]
        location_mentions = {}  # место -> [fact_ids]
        
        time_words = ["23:00", "22:00", "21:00", "полночь", "вечер", "ночь", "утро"]
        location_words = ["кухня", "сад", "кабинет", "спальня", "холл", "гостиная"]
        
        for fact in facts_list:
            text_lower = fact.text.lower()
            
            # Ищем упоминания времени
            for tw in time_words:
                if tw in text_lower:
                    time_mentions.setdefault(tw, []).append(fact.id)
            
            # Ищем упоминания мест
            for lw in location_words:
                if lw in text_lower:
                    location_mentions.setdefault(lw, []).append(fact.id)
        
        # Создаём противоречия для мест/времени с несколькими упоминаниями
        for time_val, fact_ids in time_mentions.items():
            if len(fact_ids) >= 2:
                # Берём первые два факта о этом времени
                contradiction_pairs.append((
                    fact_ids[0],
                    fact_ids[1],
                    f"Оба факта о {time_val}, но могут противоречить в деталях"
                ))
        
        for loc_val, fact_ids in location_mentions.items():
            if len(fact_ids) >= 2 and len(contradiction_pairs) < 3:
                contradiction_pairs.append((
                    fact_ids[0],
                    fact_ids[1],
                    f"Оба факта о '{loc_val}', но могут противоречить в деталях"
                ))
        
        # Если не нашли достаточно противоречий, добавляем случайные пары
        import random
        while len(contradiction_pairs) < 2 and len(facts_list) >= 4:
            f1, f2 = random.sample(facts_list, 2)
            if f1.id != f2.id and (f1.id, f2.id) not in [(p[0], p[1]) for p in contradiction_pairs]:
                contradiction_pairs.append((
                    f1.id,
                    f2.id,
                    "Факты могут противоречить в контексте расследования"
                ))
        
        # Преобразуем в FactConnection
        contradictions = []
        for f1_id, f2_id, reason in contradiction_pairs[:4]:  # Максимум 4 противоречия
            conn = FactConnection(
                from_fact=f1_id,
                to_fact=f2_id,
                connection_type="CONTRADICTS",
                reason=reason
            )
            contradictions.append(conn)
        
        return contradictions