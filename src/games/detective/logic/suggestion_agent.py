from typing import List
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer
from src.games.detective.schemas import SuggestionData, Fact

SUGGESTION_PROMPT = """
Ты — внутренний голос персонажа в детективе.
Твоя роль: {role} ({bio}).
Твоя цель: {objective}.

ТЕКУЩАЯ СИТУАЦИЯ:
Вскрытые факты: {public_facts}
Твои скрытые факты (Инвентарь): {private_facts}

ПОСЛЕДНИЕ СООБЩЕНИЯ В ЧАТЕ:
{history}

ТВОЯ ЗАДАЧА:
Сгенерируй 3 варианта реплики, которую игрок может отправить в чат ПРЯМО СЕЙЧАС.
1. Logic: Умный, опирающийся на факты (твои или общие).
2. Defense: Эмоциональный, защитный или агрессивный.
3. Bluff: Увод темы, ложь или нейтральный вопрос.

Ответ должен быть СТРОГО JSON:
{{
  "logic_text": "Текст...",
  "defense_text": "Текст...",
  "bluff_text": "Текст..."
}}
"""


class SuggestionAgent:
    async def generate(self,
                       player: BasePlayer,
                       history: List[str],
                       public_facts: List[Fact],
                       all_facts_map: dict) -> SuggestionData:

        prof = player.attributes.get("detective_profile")
        if not prof: return SuggestionData(logic_text="...", defense_text="...", bluff_text="...")

        # Собираем тексты фактов
        pub_txt = "; ".join([f.text for f in public_facts]) or "Нет"

        # Инвентарь: берем тексты фактов по ID
        my_facts_txt = []
        for fid in prof.inventory:
            fact = all_facts_map.get(fid)
            if fact: my_facts_txt.append(f"[{fact.type}] {fact.text}")
        priv_txt = "; ".join(my_facts_txt) or "Пусто"

        prompt = SUGGESTION_PROMPT.format(
            role=prof.role,
            bio=prof.bio,
            objective=prof.secret_objective,
            public_facts=pub_txt,
            private_facts=priv_txt,
            history="\n".join(history[-8:])  # Последние 8 сообщений
        )

        model = core_cfg.models["player_models"][0]

        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6,
                json_mode=True
            )
            data = llm_client.parse_json(response)
            return SuggestionData(
                logic_text=data.get("logic_text", ""),
                defense_text=data.get("defense_text", ""),
                bluff_text=data.get("bluff_text", "")
            )
        except Exception as e:
            print(f"Suggestion Error: {e}")
            return SuggestionData(logic_text="Ошибка AI", defense_text="...", bluff_text="...")