import os
import json
import logging
import asyncio
import random
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from groq import AsyncGroq

try:
    from cerebras.cloud.sdk import Cerebras
except ImportError:
    Cerebras = None

from src.core.config import core_cfg

load_dotenv(os.path.join("Configs", ".env"))


class LLMService:
    def __init__(self):
        self.groq_key = os.getenv("GROQ_API_KEY")
        self.cerebras_key = os.getenv("CEREBRAS_API_KEY")

        self.groq_client = AsyncGroq(api_key=self.groq_key) if self.groq_key else None
        self.cerebras_client = Cerebras(api_key=self.cerebras_key) if (self.cerebras_key and Cerebras) else None

    async def generate(self,
                       model_config: Dict,
                       messages: List[Dict],
                       temperature: float = 0.7,
                       json_mode: bool = False,
                       logger=None) -> str:
        """
        Умная генерация с Fallback. Если основная модель не отвечает, пробуем другие.
        """
        # 1. Подготовка сообщений (копия, чтобы не испортить оригинал при ретраях)
        current_messages = [m.copy() for m in messages]

        if json_mode:
            # Добавляем инструкцию JSON, если её нет
            if not any("json" in (m.get("content") or "").lower() for m in current_messages):
                sys_msg = {"role": "system", "content": "ОТВЕТЬ СТРОГО В JSON."}
                if current_messages and current_messages[0].get("role") == "system":
                    current_messages[0]["content"] += " ОТВЕТЬ СТРОГО В JSON."
                else:
                    current_messages.insert(0, sys_msg)

        # 2. Список моделей для попыток
        # Сначала основная, потом 2 случайные запасные
        candidates = [model_config]
        all_models = core_cfg.models.get("player_models", [])

        # Добавляем запасные (исключая основную)
        backups = [m for m in all_models if m != model_config]
        random.shuffle(backups)
        candidates.extend(backups[:2])

        # 3. Цикл попыток
        for i, config in enumerate(candidates):
            provider = config.get("provider")
            model_id = config.get("model_id")

            try:
                # Жесткий тайм-аут 15 секунд на генерацию
                response = await asyncio.wait_for(
                    self._call_provider(provider, model_id, current_messages, temperature, json_mode),
                    timeout=15.0
                )

                if response:
                    if logger:
                        logger.log_llm(model_id, current_messages, response)
                    return response

            except (asyncio.TimeoutError, Exception) as e:
                err_msg = f"LLM Error ({provider}/{model_id}): {e}"
                if logger: logger.log_event("ERROR", err_msg)
                print(f"⚠️ {err_msg} -> Switching to backup...")
                # Идем к следующему кандидату в цикле

        # Если все попытки провалились
        print("🔥 ALL LLM ATTEMPTS FAILED.")
        return "{}" if json_mode else "..."

    async def _call_provider(self, provider: str, model_id: str, messages: List[Dict], temp: float,
                             json_mode: bool) -> str:
        """Низкоуровневый вызов API"""
        kwargs = {
            "model": model_id,
            "messages": messages,
            "temperature": temp,
            "max_tokens": 1024
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        if provider == "groq":
            if not self.groq_client: raise ValueError("Groq Client missing")
            completion = await self.groq_client.chat.completions.create(**kwargs)
            return completion.choices[0].message.content

        elif provider == "cerebras":
            if not self.cerebras_client: raise ValueError("Cerebras Client missing")
            completion = self.cerebras_client.chat.completions.create(**kwargs)
            return completion.choices[0].message.content

        else:
            raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def parse_json(text: Optional[str]) -> Dict[str, Any]:
        if not text: return {}
        clean_text = text.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(clean_text)
        except:
            return {}


llm_client = LLMService()