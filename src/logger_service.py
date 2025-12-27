import datetime
import json
import os
import re
import logging
import sys


# Хендлер для файла (пишет всё подряд)
class CustomFileHandler(logging.FileHandler):
    def __init__(self, filename, mode='a', encoding=None, delay=False):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        super().__init__(filename, mode, encoding, delay)


# Фильтр для консоли (отсекает лишний шум)
class ConsoleFilter(logging.Filter):
    def filter(self, record):
        # Блокируем логи от логгера "LLM_RAW", так как там лежат огромные JSON с текстами
        if record.name == "LLM_RAW":
            return False
        return True


class GameLogger:
    def __init__(self):
        self.base_log_dir = "Logs"
        self.session_dir = None

        self.chat_history_path = None
        self.game_logic_path = None
        self.raw_debug_path = None

        self.icons = {
            "DIRECTOR": "🎬",
            "JUDGE": "⚖️",
            "BOT_THOUGHT": "🧠",
            "BOT_SPEECH": "🗣",
            "VOTE": "🗳",
            "ERROR": "🔥",
            "SYSTEM": "⚙️",
            "INFO": "📝",
            "LLM_REQUEST": "⬆️",
            "LLM_RESPONSE": "⬇️",
            "GAME_OVER": "🏁",
            "JUDGE_DECISION": "🔨",
            "HUMAN_SPEECH": "👤",
            "HUMAN_TURN": "👉",
            "VOTE_RESULTS": "📊"
        }

        os.makedirs(self.base_log_dir, exist_ok=True)

    def new_session(self, username: str) -> None:
        safe_username = re.sub(r'[\\/*?:"<>|]', "", username).strip()
        if not safe_username:
            safe_username = "Unknown"

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.session_dir = os.path.join(self.base_log_dir, f"Session_{timestamp}_{safe_username}")
        os.makedirs(self.session_dir, exist_ok=True)

        self.chat_history_path = os.path.join(self.session_dir, "chat_history.log")
        self.game_logic_path = os.path.join(self.session_dir, "game_logic.log")
        self.raw_debug_path = os.path.join(self.session_dir, "raw_debug.log")

        self._setup_raw_logging()

        readable_time = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        header = f"=== GAME SESSION: {readable_time} ===\nUser: {username}\n\n"
        self._write_to_file(self.chat_history_path, header, mode="w")
        self._write_to_file(self.game_logic_path, header, mode="w")

    def _setup_raw_logging(self):
        """Настройка системного логирования."""
        root_logger = logging.getLogger()

        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

        # 1. ФАЙЛ: Пишет ВСЁ (включая LLM_RAW)
        file_handler = CustomFileHandler(self.raw_debug_path, encoding='utf-8')
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # 2. КОНСОЛЬ: Пишет только технические логи (HTTP, Errors), фильтрует LLM_RAW
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(ConsoleFilter())  # <--- Применяем фильтр
        root_logger.addHandler(console_handler)

        root_logger.setLevel(logging.INFO)

        # httpx пишет в консоль "HTTP Request: POST ...", этого достаточно для отслеживания стабильности
        logging.getLogger("httpx").setLevel(logging.INFO)
        logging.getLogger("aiogram").setLevel(logging.WARNING)

    def _write_to_file(self, filepath: str, text: str, mode: str = "a") -> None:
        if not filepath: return
        try:
            with open(filepath, mode, encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            # Ошибки записи логов всё равно выводим в консоль
            sys.stderr.write(f"Log Error: {e}\n")

    def log_chat_message(self, speaker: str, message: str) -> None:
        time_str = datetime.datetime.now().strftime("%H:%M:%S")
        entry = f"[{time_str}] {speaker}: {message}\n"
        self._write_to_file(self.chat_history_path, entry)

    def log_game_event(self, event_type: str, message: str, details: dict = None) -> None:
        time_str = datetime.datetime.now().strftime("%H:%M:%S")
        icon = self.icons.get(event_type.upper(), self.icons["INFO"])

        entry = f"[{time_str}] {icon} [{event_type.upper()}]: {message}\n"
        if details:
            entry += f"  Details: {json.dumps(details, ensure_ascii=False, indent=2)}\n"
        entry += "-" * 40 + "\n"
        self._write_to_file(self.game_logic_path, entry)

    def log_llm_interaction(self,
                            service_name: str,
                            model_id: str,
                            prompt: list,
                            response: str,
                            is_json_mode: bool) -> None:
        """
        Логирует полные данные.
        Благодаря ConsoleFilter, в консоль это НЕ попадет, но в raw_debug.log сохранится.
        """
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "service": service_name,
            "model_id": model_id,
            "prompt": prompt,
            "response": response,
            "json_mode": is_json_mode,
        }
        logging.getLogger("LLM_RAW").info(json.dumps(entry, ensure_ascii=False, indent=2))


game_logger = GameLogger()