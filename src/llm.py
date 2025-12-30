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

from src.config import cfg
from src.logger_service import game_logger

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
                       json_mode: bool = False) -> str:

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
            # Безопасная проверка контента сообщений (защита от None в messages)
            if not any("json" in (m.get("content") or "").lower() for m in messages):
                if messages and messages[0].get("role") == "system":
                    messages[0]["content"] = (messages[0].get("content") or "") + " ОТВЕТЬ СТРОГО В JSON."
                else:
                    messages.insert(0, {"role": "system", "content": "ОТВЕТЬ СТРОГО В JSON."})

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

        # --- FIX: Защита от None, если API вернуло пустой объект ---
        if response_content is None:
            response_content = "{}" if json_mode else ""
        # -----------------------------------------------------------

        # --- ЛОГИРОВАНИЕ LLM ВЗАИМОДЕЙСТВИЯ ---
        game_logger.log_llm_interaction(
            service_name="LLMService",
            model_id=model_id,
            prompt=messages,
            response=response_content,
            is_json_mode=json_mode
        )

        return response_content

    @staticmethod
    def parse_json(text: Optional[str]) -> Dict[str, Any]:
        """
        Парсит JSON из ответа LLM.
        Добавлена защита от None (ошибка AttributeError).
        """
        # --- FIX: Проверка на пустоту ---
        if not text:
            logging.warning("⚠️ LLM returned empty response/None during JSON parsing")
            return {}
        # --------------------------------

        clean_text = text.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            logging.warning(f"Failed to parse JSON: {text[:200]}...")
            return {}


llm = LLMService()