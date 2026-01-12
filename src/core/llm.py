import os
import json
import logging
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from groq import AsyncGroq

try:
    from cerebras.cloud.sdk import Cerebras
except ImportError:
    Cerebras = None

# Используем новый конфиг ядра
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

        provider = model_config.get("provider")
        model_id = model_config.get("model_id")

        kwargs = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1000
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
            # Авто-добавление инструкции JSON, если её нет
            if not any("json" in (m.get("content") or "").lower() for m in messages):
                sys_msg = {"role": "system", "content": "ОТВЕТЬ СТРОГО В JSON."}
                if messages and messages[0].get("role") == "system":
                    messages[0]["content"] += " ОТВЕТЬ СТРОГО В JSON."
                else:
                    messages.insert(0, sys_msg)

        response_content = ""
        try:
            if provider == "groq":
                if not self.groq_client: raise ValueError("Groq API Key missing")
                completion = await self.groq_client.chat.completions.create(**kwargs)
                response_content = completion.choices[0].message.content

            elif provider == "cerebras":
                if not self.cerebras_client: raise ValueError("Cerebras API Key missing")
                completion = self.cerebras_client.chat.completions.create(**kwargs)
                response_content = completion.choices[0].message.content
            else:
                response_content = "Error: Unknown provider"

        except Exception as e:
            logging.error(f"LLM Error ({provider}): {e}")
            response_content = "{}" if json_mode else "Error generating response."

        if logger:
            logger.log_llm(model_id, messages, response_content)

        return response_content if response_content else ("{}" if json_mode else "")

    @staticmethod
    def parse_json(text: Optional[str]) -> Dict[str, Any]:
        if not text: return {}
        clean_text = text.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(clean_text)
        except:
            return {}


# Инстанс сервиса
llm_client = LLMService()