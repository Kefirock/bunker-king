import uuid
import random
from typing import List, Tuple, Dict
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.games.detective.config import detective_cfg
from src.games.detective.schemas import DetectiveScenario, Fact, FactType, RoleType, DetectivePlayerProfile


class ScenarioGenerator:
    async def generate(self, player_names: List[str]) -> Tuple[DetectiveScenario, Dict[str, DetectivePlayerProfile]]:
        count = len(player_names)
        total_facts = max(5, count * 2 + 1)

        system_prompt = detective_cfg.prompts["scenario_writer"]["system"].format(
            player_count=count,
            player_names=", ".join(player_names),
            total_facts=total_facts
        )

        model = core_cfg.models["player_models"][0]

        response = await llm_client.generate(
            model_config=model,
            messages=[{"role": "system", "content": system_prompt}],
            temperature=0.8,
            json_mode=True
        )

        data = llm_client.parse_json(response)

        # --- СТРОГАЯ ПРОВЕРКА ДАННЫХ ---
        if not data or "facts" not in data or "roles" not in data:
            raise RuntimeError("LLM failed to generate a valid scenario structure. Aborting game start.")

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

        # 1. Привязка Ролей к Именам (Строго по ответу LLM)
        for name in player_names:
            p_data = next((r for r in roles_data if r.get("player_name") == name), None)

            if not p_data:
                raise RuntimeError(f"LLM missed role for player: {name}")

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

            fact = Fact(
                id=fid,
                text=f_data.get("text", "???"),
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
                # Если LLM не указал владельца факта (случайная улика) - даем тому, у кого меньше карт
                target_profile = min(player_profiles.values(), key=lambda p: len(p.inventory))

            target_profile.inventory.append(fid)

        return scenario, player_profiles