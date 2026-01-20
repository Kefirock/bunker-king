import random
from typing import List, Dict, Any, Optional
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer
from src.games.detective.schemas import Fact, DetectivePlayerProfile, RoleType

BOT_SYSTEM_PROMPT = """
ТЫ — {name}. Твоя роль: {role}.
Твоя легенда: {bio}
Твоя скрытая цель: {objective}

СИТУАЦИЯ:
Сценарий: "{scenario_title}" - {scenario_desc}
Вскрытые факты (Общие): {public_facts}

ТВОЙ ИНВЕНТАРЬ (СКРЫТЫЕ ФАКТЫ):
{inventory}

ИСТОРИЯ ЧАТА:
{history}

ЗАДАЧА:
1. Ответь на сообщения или выскажи мнение о ситуации.
2. Реши, нужно ли ВСКРЫТЬ (Опубликовать) один из твоих фактов ПРЯМО СЕЙЧАС.
   - Если ты НЕВИНОВНЫЙ: Вскрывай факты, которые помогают найти убийцу или снимают подозрения с тебя.
   - Если ты УБИЙЦА: Вскрывай факты, если тебя прижали к стенке, или чтобы отвести подозрения (алиби). Не вскрывай улики против себя без причины.
   - ТЫ ОБЯЗАН вскрыть минимум 2 факта до конца игры. Сейчас вскрыто: {published_count}.

ОТВЕТЬ В JSON:
{{
  "thought": "Твои мысли (почему ты это делаешь)",
  "speech": "Твоя реплика в чат (от 1-го лица, коротко)",
  "reveal_fact_id": "ID факта из инвентаря или null"
}}
"""

VOTE_PROMPT = """
ФИНАЛ! НУЖНО НАЙТИ УБИЙЦУ.

ТЫ — {name} ({role}).
Сценарий: {scenario_title}
Вскрытые факты: {public_facts}
История: {history}

СПИСОК ПОДОЗРЕВАЕМЫХ:
{candidates}

ЗАДАЧА:
1. Если ты УБИЙЦА: Голосуй против любого невиновного, чтобы спастись.
2. Если ты НЕВИНОВНЫЙ: Проанализируй факты и найди настоящего убийцу.

ОТВЕТЬ JSON: {{"vote_target_name": "Имя игрока"}}
"""


class DetectiveBotAgent:
    async def make_turn(self,
                        bot: BasePlayer,
                        all_players: List[BasePlayer],
                        scenario_data: Dict,
                        history: List[str],
                        public_facts: List[Fact],
                        all_facts_map: Dict[str, Fact]) -> Dict[str, Any]:

        prof: DetectivePlayerProfile = bot.attributes.get("detective_profile")
        if not prof: return {}

        pub_str = "; ".join([f"[{f.type}] {f.text}" for f in public_facts]) or "Нет"

        inv_lines = []
        for fid in prof.inventory:
            fact = all_facts_map.get(fid)
            if fact and not fact.is_public:
                inv_lines.append(f"ID: {fid} | [{fact.type}] {fact.text}")

        inv_str = "\n".join(inv_lines) if inv_lines else "Пусто (или все вскрыто)"

        prompt = BOT_SYSTEM_PROMPT.format(
            name=bot.name,
            role=prof.role,
            bio=prof.bio,
            objective=prof.secret_objective,
            scenario_title=scenario_data.get("title", ""),
            scenario_desc=scenario_data.get("description", ""),
            public_facts=pub_str,
            inventory=inv_str,
            history="\n".join(history[-10:]),
            published_count=prof.published_facts_count
        )

        model = core_cfg.models["player_models"][0]

        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                json_mode=True
            )
            return llm_client.parse_json(response)
        except Exception as e:
            print(f"Bot Error {bot.name}: {e}")
            return {"speech": "...", "reveal_fact_id": None}

    async def make_vote(self,
                        bot: BasePlayer,
                        candidates: List[BasePlayer],
                        scenario_data: Dict,
                        history: List[str],
                        public_facts: List[Fact]) -> str:

        prof: DetectivePlayerProfile = bot.attributes.get("detective_profile")

        # Если я убийца, голосую против случайного не-себя (или врага)
        if prof.role == RoleType.KILLER:
            others = [p for p in candidates if p.id != bot.id]
            if others: return random.choice(others).name
            return candidates[0].name

        # Если мирный - думаю
        pub_str = "; ".join([f"{f.text}" for f in public_facts])
        cand_str = ", ".join([p.name for p in candidates])

        prompt = VOTE_PROMPT.format(
            name=bot.name,
            role=prof.role,
            scenario_title=scenario_data.get("title", ""),
            public_facts=pub_str,
            history="\n".join(history[-15:]),
            candidates=cand_str
        )

        model = core_cfg.models["player_models"][0]
        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                json_mode=True
            )
            data = llm_client.parse_json(response)
            target = data.get("vote_target_name", "")

            # Валидация
            if any(p.name == target for p in candidates):
                return target
            return random.choice(candidates).name

        except:
            return random.choice(candidates).name