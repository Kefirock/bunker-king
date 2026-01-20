import os
import json
import logging
import asyncio
import random
import re
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

        current_messages = [m.copy() for m in messages]

        if json_mode:
            sys_msg = " RETURN JSON OBJECT ONLY. NO MARKDOWN. NO COMMENTS."
            if current_messages and current_messages[0]["role"] == "system":
                current_messages[0]["content"] += sys_msg
            else:
                current_messages.insert(0, {"role": "system", "content": sys_msg})

        candidates = [model_config]
        all_models = core_cfg.models.get("player_models", [])
        backups = [m for m in all_models if m != model_config]
        random.shuffle(backups)
        candidates.extend(backups[:2])

        for config in candidates:
            provider = config.get("provider")
            model_id = config.get("model_id")

            try:
                response = await asyncio.wait_for(
                    self._call_provider(provider, model_id, current_messages, temperature, json_mode),
                    timeout=20.0
                )
                if response:
                    if logger: logger.log_llm(model_id, current_messages, response)
                    return response

            except Exception as e:
                print(f"⚠️ LLM Error ({model_id}): {e}")
                continue

        print("🔥 ALL LLM ATTEMPTS FAILED.")
        return "{}" if json_mode else "..."

    async def _call_provider(self, provider: str, model_id: str, messages: List[Dict], temp: float,
                             json_mode: bool) -> str:
        kwargs = {
            "model": model_id,
            "messages": messages,
            "temperature": temp,
            "max_tokens": 2048
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

        return "{}"

    @staticmethod
    def parse_json(text: Optional[str]) -> Dict[str, Any]:
        if not text: return {}

        # Очистка Markdown
        pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            clean_text = match.group(1)
        else:
            clean_text = text.strip()

        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            print(f"❌ JSON Parse Error. Raw: {clean_text[:100]}...")
            return {}


llm_client = LLMService()