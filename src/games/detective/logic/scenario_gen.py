import uuid
import random
import asyncio
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

    def _parse_skeleton_info(self, skeleton: str) -> Dict[str, Any]:
        """Извлекает структурированную информацию из текстового skeleton."""
        info = {
            "title": "Детективное дело",
            "description": "Загадочное убийство...",
            "victim": "Жертва",
            "cause": "Неизвестно",
            "real_cause": "Неизвестно",
            "apparent_cause": "Остановка сердца",
            "location": "Особняк",
            "method": "Неизвестно",
            "solution": "Убийца ещё не найден",
            "time_of_death": "23:00"
        }

        try:
            # Парсим victim
            if "ЖЕРТВА:" in skeleton:
                info["victim"] = skeleton.split("ЖЕРТВА:")[1].split("\n")[0].strip()

            # Парсим method
            if "СПОСОБ:" in skeleton:
                info["method"] = skeleton.split("СПОСОБ:")[1].split("\n")[0].strip()

            # Парсим location
            if "ЛОКАЦИЯ ТЕЛА:" in skeleton:
                info["location"] = skeleton.split("ЛОКАЦИЯ ТЕЛА:")[1].split("\n")[0].strip()

            # Парсим setting для title
            if "СЕТТИНГ:" in skeleton:
                setting = skeleton.split("СЕТТИНГ:")[1].split("\n")[0].strip()
                info["title"] = f"Дело в {setting}"
                info["description"] = f"Загадочное убийство произошло в {setting}."

            info["real_cause"] = info["method"]
            info["cause"] = info["method"]

        except Exception:
            pass  # Используем defaults

        return info

    def _extract_alibi_for_index(self, skeleton: str, index: int) -> str:
        """Извлекает алиби для персонажа по индексу из skeleton."""
        try:
            lines = skeleton.split("\n")
            for line in lines:
                if f"Персонаж #{index + 1}" in line and "был" in line.lower():
                    return line.split("в локации")[-1].strip("'").strip() if "в локации" in line else "Неизвестно"
        except Exception:
            pass
        return "Неизвестно"

    def _extract_markers(self, skeleton: str) -> List[str]:
        """Извлекает маркеры/следы из skeleton для каждого персонажа."""
        markers = ["Нервозность", "Пятно", "Царапина", "Запах", "Шрам"]
        try:
            lines = skeleton.split("\n")
            found_markers = []
            for line in lines:
                if "след:" in line.lower():
                    marker_text = line.split("след:")[-1].strip()
                    found_markers.append(marker_text)

            # Если нашли маркеры, используем их, иначе defaults
            if found_markers:
                # Заполняем до 5
                while len(found_markers) < 5:
                    found_markers.append(markers[len(found_markers) % len(markers)])
                return found_markers[:5]
        except Exception:
            pass
        return markers

    async def generate(self, player_names: List[str], logger=None) -> Tuple[
        DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        """
        Новый gather pipeline:
        1. Скелет сценария
        2. Параллельная генерация 5 легенд (gather)
        3. Распределение implicate_targets
        4. Параллельная генерация 5 улик (gather)
        5. Сборка без difflib
        """
        count = len(player_names)
        model = core_cfg.models["player_models"][0]

        # ШАГ 0: Скелет
        try:
            plot_skeleton = self._build_advanced_skeleton(count)
        except Exception as e:
            raise ScenarioGenerationError(f"Ошибка сборки скелета: {e}")

        if logger:
            logger.log_event("DIRECTOR_MODE", "Skeleton assembled", {"skeleton": plot_skeleton})

        # Парсим данные из skeleton для структурированной информации
        scenario_info = self._parse_skeleton_info(plot_skeleton)
        scenario_info["player_names"] = player_names

        # Подготавливаем role_data для каждого персонажа
        # По логике skeleton: 0=убийца, 1=вторичная цель, остальные=невиновные
        role_data_list = []
        for i, name in enumerate(player_names):
            role = "KILLER" if i == 0 else "INNOCENT"
            tag = "Подозреваемый" if i == 1 else "Гость"
            is_finder = (i == 1)  # Второй находит тело
            alibi_loc = self._extract_alibi_for_index(plot_skeleton, i)
            secret = "Убийство" if i == 0 else ("Вторичная цель" if i == 1 else "Личная тайна")

            role_data_list.append({
                "character_name": name,
                "role": role,
                "tag": tag,
                "is_finder": is_finder,
                "alibi_location": alibi_loc,
                "secret_objective": secret
            })

        all_names = ", ".join(player_names)

        # ШАГ 1: Параллельная генерация 5 легенд (gather)
        if logger:
            logger.log_event("GEN_STEP_1", "Generating Legends (gather)")

        print(f"🧠 Шаг 1: Генерация {count} легенд параллельно...")
        legend_tasks = [
            self._generate_legend(role_data, all_names, plot_skeleton, model)
            for role_data in role_data_list
        ]
        legends = await asyncio.gather(*legend_tasks, return_exceptions=True)

        # Обработка ошибок в легендах
        valid_legends = []
        for i, result in enumerate(legends):
            if isinstance(result, Exception):
                print(f"⚠️ Legend error for {player_names[i]}: {result}")
                valid_legends.append({
                    "character_name": player_names[i],
                    "tag": "Гость",
                    "legend": f"Я {player_names[i]}, оказался здесь случайно.",
                    "secret": "Личная тайна"
                })
            else:
                valid_legends.append(result)

        legends = valid_legends

        # ШАГ 2: Распределение implicate_targets (эвристика противоречий)
        implicate_targets = self._assign_implicate_targets(player_names, {"roles": role_data_list})

        # ШАГ 3: Параллельная генерация 5 улик (gather)
        if logger:
            logger.log_event("GEN_STEP_2", "Generating Facts (gather)")

        print(f"🧠 Шаг 2: Генерация {count}×5 улик параллельно...")

        # Извлекаем маркеры из skeleton
        markers = self._extract_markers(plot_skeleton)

        fact_tasks = []
        for i, char_name in enumerate(player_names):
            legend_data = legends[i]
            marker = markers[i] if i < len(markers) else "Неизвестный след"
            role = "KILLER" if i == 0 else "INNOCENT"

            fact_tasks.append(
                self._generate_facts_for_character(
                    char_name=char_name,
                    legend_data=legend_data,
                    skeleton=plot_skeleton,
                    implicate_target=implicate_targets[i],
                    role=role,
                    marker=marker,
                    scenario_info=scenario_info,
                    model=model
                )
            )

        facts_results = await asyncio.gather(*fact_tasks, return_exceptions=True)

        # Обработка ошибок в фактах
        valid_facts = []
        for i, result in enumerate(facts_results):
            if isinstance(result, Exception):
                print(f"⚠️ Facts error for {player_names[i]}: {result}")
                # Fallback факты
                valid_facts.append([
                    {"text": "Я заметил что-то странное...", "keyword": "Подозрение", "type": "TESTIMONY", "is_plot": True, "implicates": implicate_targets[i]},
                    {"text": "В комнате было что-то не так.", "keyword": "Деталь", "type": "PHYSICAL", "is_plot": True, "implicates": None},
                    {"text": "Я слышал странный звук.", "keyword": "Звук", "type": "TESTIMONY", "is_plot": False, "implicates": None},
                    {"text": "Воздух был тяжелым.", "keyword": "Атмосфера", "type": "TESTIMONY", "is_plot": False, "implicates": None},
                    {"text": "Я ничего не понимаю.", "keyword": "Смятение", "type": "TESTIMONY", "is_plot": False, "implicates": None}
                ])
            else:
                valid_facts.append(result)

        facts_results = valid_facts

        # ШАГ 4: Сборка объектов (без difflib)
        if logger:
            logger.log_event("GEN_STEP_3", "Assembling game objects")

        return self._assemble_from_gather(legends, facts_results, plot_skeleton, player_names, scenario_info)

    def _assign_implicate_targets(self, player_names: List[str], skeleton_data: Dict) -> List[str]:
        """
        Алгоритмическое распределение 'на кого указывает улика' до генерации.
        Возвращает список имен целей для каждого персонажа (индекс совпадает с player_names).
        """
        count = len(player_names)
        targets = [None] * count

        # Определяем роли из skeleton_data (если доступно) или используем эвристику
        roles = skeleton_data.get("roles", [])
        role_map = {}
        for i, name in enumerate(player_names):
            role_map[name] = "INNOCENT"

        # Первый игрок — убийца (по логике _build_advanced_skeleton)
        if count > 0:
            role_map[player_names[0]] = "KILLER"

        # Ищем невиновного с вторичной целью (обычно второй в списке)
        secondary_target = player_names[1] if count > 1 else None

        # Убийца должен иметь хотя бы 1 улику, указывающую на невиновного
        killer_idx = 0
        innocent_indices = [i for i in range(count) if i != killer_idx]

        if innocent_indices:
            # Убийца указывает на случайного невиновного
            targets[killer_idx] = player_names[random.choice(innocent_indices)]

        # Невиновный с вторичной целью (если есть) должен иметь улику на убийцу
        if secondary_target and count > 1:
            targets[1] = player_names[killer_idx]

        # Остальные — случайное распределение
        for i in range(count):
            if targets[i] is None:
                others = [n for j, n in enumerate(player_names) if j != i]
                targets[i] = random.choice(others) if others else player_names[0]

        return targets

    async def _generate_legend(self, role_data: Dict, all_names: str, skeleton: str, model: Dict) -> Dict:
        """
        Генерация легенды для одного персонажа.
        Вызывается параллельно через gather.
        """
        prompt = detective_cfg.prompts["character_legend"]["system"].format(
            setting=skeleton.split("СЕТТИНГ:")[1].split("\n")[0].strip() if "СЕТТИНГ:" in skeleton else "Особняк",
            tech_level=skeleton.split("ЭПОХА:")[1].split("\n")[0].strip() if "ЭПОХА:" in skeleton else "1920s",
            victim=skeleton.split("ЖЕРТВА:")[1].split("\n")[0].strip() if "ЖЕРТВА:" in skeleton else "Хозяин",
            method=skeleton.split("СПОСОБ:")[1].split("\n")[0].strip() if "СПОСОБ:" in skeleton else "Удар",
            time_of_death="23:00",
            location=skeleton.split("ЛОКАЦИЯ ТЕЛА:")[1].split("\n")[0].strip() if "ЛОКАЦИЯ ТЕЛА:" in skeleton else "Кабинет",
            all_characters_list=all_names,
            character_name=role_data.get("character_name", "Гость"),
            role=role_data.get("role", "INNOCENT"),
            tag=role_data.get("tag", "Гость"),
            is_finder=str(role_data.get("is_finder", False)).lower(),
            alibi_location=role_data.get("alibi_location", "Неизвестно"),
            secret_objective=role_data.get("secret_objective", "Скрыть тайну")
        )

        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "system", "content": prompt}],
                temperature=0.8,
                json_mode=True
            )
            data = llm_client.parse_json(response)

            # Валидация обязательных полей
            required = ["character_name", "tag", "legend", "secret"]
            if not data or any(f not in data for f in required):
                raise ScenarioGenerationError(f"Invalid legend JSON: {data}")

            return data
        except Exception as e:
            print(f"⚠️ Legend generation error: {e}")
            # Fallback
            return {
                "character_name": role_data.get("character_name", "Гость"),
                "tag": role_data.get("tag", "Гость"),
                "legend": f"Я {role_data.get('character_name', 'гость')}, оказался здесь по несчастливой случайности.",
                "secret": role_data.get("secret_objective", "У меня есть тайна.")
            }

    async def _generate_facts_for_character(
        self,
        char_name: str,
        legend_data: Dict,
        skeleton: str,
        implicate_target: str,
        role: str,
        marker: str,
        scenario_info: Dict,
        model: Dict
    ) -> List[Dict]:
        """
        Генерация 5 улик для одного персонажа.
        Вызывается параллельно через gather.
        """
        prompt = detective_cfg.prompts["character_facts"]["system"].format(
            victim=scenario_info.get("victim", "Хозяин"),
            cause=scenario_info.get("cause", "Неизвестно"),
            location=scenario_info.get("location", "Особняк"),
            solution=scenario_info.get("solution", "Unknown"),
            timeline=scenario_info.get("timeline", ""),
            character_name=char_name,
            role=role,
            legend=legend_data.get("legend", ""),
            marker=marker,
            implicate_target_1=implicate_target
        )

        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "system", "content": prompt}],
                temperature=0.6,
                json_mode=True
            )
            data = llm_client.parse_json(response)

            facts = data.get("facts", [])
            if len(facts) < 5:
                raise ScenarioGenerationError(f"Only {len(facts)} facts generated, need 5")

            return facts[:5]
        except Exception as e:
            print(f"⚠️ Facts generation error for {char_name}: {e}")
            # Fallback факты
            return [
                {"text": f"Я заметил кое-что странное...", "keyword": "Подозрение", "type": "TESTIMONY", "is_plot": True, "implicates": implicate_target},
                {"text": f"В комнате было что-то не так.", "keyword": "Деталь", "type": "PHYSICAL", "is_plot": True, "implicates": None},
                {"text": f"Я слышал странный звук.", "keyword": "Звук", "type": "TESTIMONY", "is_plot": False, "implicates": None},
                {"text": f"Воздух был тяжелым.", "keyword": "Атмосфера", "type": "TESTIMONY", "is_plot": False, "implicates": None},
                {"text": f"Я ничего не понимаю.", "keyword": "Смятение", "type": "TESTIMONY", "is_plot": False, "implicates": None}
            ]

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

    def _assemble_from_gather(
        self,
        legends: List[Dict],
        facts_results: List[List[Dict]],
        skeleton: str,
        player_names: List[str],
        scenario_info: Dict
    ) -> Tuple[DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        """
        Сборка объектов из результатов gather pipeline.
        НЕ использует difflib — соответствие по индексу.
        """
        # Создаём сценарий из skeleton и scenario_info
        scenario = DetectiveScenario(
            title=scenario_info.get("title", "Дело без названия"),
            description=scenario_info.get("description", "..."),
            victim_name=scenario_info.get("victim", "Неизвестный"),
            time_of_death=scenario_info.get("time_of_death", "Неизвестно"),
            real_cause=scenario_info.get("real_cause", "Неизвестно"),
            apparent_cause=scenario_info.get("apparent_cause", "Остановка сердца (причина неясна)"),
            location_of_body=scenario_info.get("location", "Неизвестно"),
            murder_method=scenario_info.get("method", "Unknown"),
            true_solution=scenario_info.get("solution", "Unknown"),
            timeline_truth=skeleton,
            fact_graph=[],  # Будет заполнено после генерации фактов
            red_herrings=[]
        )

        player_profiles: Dict[str, DetectivePlayerProfile] = {}

        # Собираем профили и факты по индексу (соответствие gather)
        for i, real_name in enumerate(player_names):
            legend_data = legends[i] if i < len(legends) else {}
            character_facts = facts_results[i] if i < len(facts_results) else []

            char_name = legend_data.get("character_name", f"Персонаж {i + 1}")
            tag = legend_data.get("tag", "Гость")

            # Определяем роль (первый — убийца по логике skeleton)
            role_enum = RoleType.KILLER if i == 0 else RoleType.INNOCENT

            profile = DetectivePlayerProfile(
                character_name=char_name,
                tag=tag,
                legend=legend_data.get("legend", ""),
                role=role_enum,
                secret_objective=legend_data.get("secret", ""),
                is_finder=legend_data.get("is_finder", False)
            )

            # Создаём факты из gather результатов
            for f_data in character_facts[:5]:  # Максимум 5 фактов
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
                if len(keyword) > 25:
                    keyword = keyword[:25] + "."

                fact = Fact(
                    id=fid,
                    text=f_data.get("text", "???"),
                    keyword=keyword,
                    type=ftype,
                    is_public=False,
                    implicates=f_data.get("implicates")  # ← НОВОЕ ПОЛЕ из JSON
                )

                scenario.all_facts[fid] = fact
                profile.inventory.append(fid)

            player_profiles[real_name] = profile

        # Генерация противоречий после создания всех фактов
        scenario.fact_graph = self._generate_fact_contractions(scenario.all_facts)

        return scenario, player_profiles