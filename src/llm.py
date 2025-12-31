import os
import json
import logging
import asyncio
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
                       json_mode: bool = False,
                       max_retries: int = 3) -> str:
        """
        Генерирует ответ от LLM с повторными попытками при ошибках.
        """
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
            # Гарантируем наличие инструкции про JSON
            if not any("json" in (m.get("content") or "").lower() for m in messages):
                system_found = False
                for m in messages:
                    if m.get("role") == "system":
                        m["content"] = (m.get("content") or "") + " ОТВЕТЬ СТРОГО В JSON."
                        system_found = True
                        break
                if not system_found:
                    messages.insert(0, {"role": "system", "content": "ОТВЕТЬ СТРОГО В JSON."})

        # --- ЦИКЛ RETRY ---
        for attempt in range(max_retries):
            try:
                response_content = ""

                if provider == "groq":
                    if not self.groq_client: raise ValueError("Groq API Key missing")
                    completion = await self.groq_client.chat.completions.create(**kwargs)
                    response_content = completion.choices[0].message.content

                elif provider == "cerebras":
                    if not self.cerebras_client: raise ValueError("Cerebras API Key missing")
                    completion = self.cerebras_client.chat.completions.create(**kwargs)
                    response_content = completion.choices[0].message.content
                else:
                    raise ValueError(f"Unknown provider: {provider}")

                # Проверка на пустой ответ
                if not response_content or not response_content.strip():
                    raise ValueError("Empty response received")

                # Проверка валидности JSON (если нужен)
                if json_mode:
                    json.loads(response_content)

                # Успех - логируем и возвращаем
                game_logger.log_llm_interaction(
                    service_name="LLMService",
                    model_id=model_id,
                    prompt=messages,
                    response=response_content,
                    is_json_mode=json_mode
                )
                return response_content

            except Exception as e:
                logging.warning(f"⚠️ LLM Attempt {attempt + 1}/{max_retries} failed ({model_id}): {e}")
                await asyncio.sleep(1.5)  # Пауза перед повтором

        # --- FALLBACK (Если все попытки провалились) ---
        logging.error(f"❌ LLM failed after {max_retries} attempts. Returning safe fallback.")

        if json_mode:
            # Возвращаем заглушку JSON, чтобы игра не упала
            return json.dumps({
                "thought": "Кажется, я потерял мысль... (Сбой нейросети)",
                "intent": "DEFEND",
                "speech": "...",
                "vote": "",
                "violation_type": "none",
                "argument_quality": "weak"
            })
        else:
            return "..."

    @staticmethod
    def parse_json(text: Optional[str]) -> Dict[str, Any]:
        """Безопасный парсинг JSON."""
        if not text:
            return {}

        clean_text = text.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            logging.warning(f"Failed to parse JSON: {text[:100]}...")
            return {}


llm = LLMService()