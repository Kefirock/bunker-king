import datetime
import json
import os
import re
import logging
import sys


# Фильтр, чтобы "сырые" данные LLM не засоряли консоль
class ConsoleFilter(logging.Filter):
    def filter(self, record):
        return record.name != "LLM_RAW"


class GameLogger:
    def __init__(self):
        # Папка, куда будет смонтирован Volume (/app/Logs)
        self.base_log_dir = "Logs"
        self.current_session_dir = None

        self.chat_logger = None
        self.logic_logger = None
        self.raw_logger = None

        self.icons = {
            "DIRECTOR": "🎬", "JUDGE": "⚖️", "BOT_THOUGHT": "🧠",
            "BOT_SPEECH": "🗣", "VOTE": "🗳", "ERROR": "🔥",
            "SYSTEM": "⚙️", "INFO": "📝", "LLM_REQUEST": "⬆️",
            "LLM_RESPONSE": "⬇️", "GAME_OVER": "🏁",
            "JUDGE_DECISION": "🔨", "HUMAN_SPEECH": "👤",
            "HUMAN_TURN": "👉", "VOTE_RESULTS": "📊"
        }

        # Создаем базовую папку (если Volume не подключен, создастся локально)
        os.makedirs(self.base_log_dir, exist_ok=True)
        self._setup_console_logging()

    def _setup_console_logging(self):
        """Настраивает общий вывод в консоль (то, что видно в Koyeb)."""
        root = logging.getLogger()
        root.setLevel(logging.INFO)

        # Очистка старых хендлеров
        for h in root.handlers[:]: root.removeHandler(h)

        # Вывод в stdout (Консоль)
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        console.addFilter(ConsoleFilter())
        root.addHandler(console)

        # Глушим шум библиотек
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("aiogram").setLevel(logging.INFO)

    def _create_file_logger(self, name: str, filepath: str):
        """Создает отдельный логгер, который пишет ТОЛЬКО в файл."""
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # <--- ВАЖНО: Не дублировать в консоль

        if logger.hasHandlers():
            logger.handlers.clear()

        fh = logging.FileHandler(filepath, encoding='utf-8')
        fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(fh)
        return logger

    def new_session(self, username: str) -> None:
        """Создает папку сессии и файлы."""
        safe_name = re.sub(r'[\\/*?:"<>|]', "", username).strip() or "Unknown"
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        folder_name = f"Session_{timestamp}_{safe_name}"
        self.current_session_dir = os.path.join(self.base_log_dir, folder_name)
        os.makedirs(self.current_session_dir, exist_ok=True)

        # 1. Лог чата
        self.chat_logger = self._create_file_logger(f"chat_{timestamp}",
                                                    os.path.join(self.current_session_dir, "chat_history.log"))

        # 2. Лог логики игры
        self.logic_logger = self._create_file_logger(f"logic_{timestamp}",
                                                     os.path.join(self.current_session_dir, "game_logic.log"))

        # 3. Сырой лог (LLM JSONs)
        self.raw_logger = self._create_file_logger("LLM_RAW",
                                                   os.path.join(self.current_session_dir, "raw_debug.log"))

        start_msg = f"=== NEW SESSION STARTED: {username} ==="
        logging.info(start_msg)  # В консоль
        self.logic_logger.info(start_msg)  # В файл

    def log_chat_message(self, speaker: str, message: str) -> None:
        msg = f"[{speaker}]: {message}"
        # В файл
        if self.chat_logger: self.chat_logger.info(msg)
        # В консоль (кратко)
        logging.info(f"💬 {msg}")

    def log_game_event(self, event_type: str, message: str, details: dict = None) -> None:
        icon = self.icons.get(event_type.upper(), self.icons["INFO"])
        log_msg = f"{icon} [{event_type}] {message}"

        # В файл (подробно с JSON)
        if self.logic_logger:
            file_msg = log_msg
            if details:
                file_msg += f"\nDetails: {json.dumps(details, ensure_ascii=False, indent=2)}\n{'-' * 40}"
            self.logic_logger.info(file_msg)

        # В консоль (только заголовок, без простыни JSON)
        logging.info(log_msg)

    def log_llm_interaction(self, service_name: str, model_id: str, prompt: list, response: str,
                            is_json_mode: bool) -> None:
        """Пишет сырые данные ТОЛЬКО в файл raw_debug.log"""
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "service": service_name,
            "model": model_id,
            "prompt": prompt,
            "response": response,
            "json_mode": is_json_mode
        }
        if self.raw_logger:
            self.raw_logger.info(json.dumps(entry, ensure_ascii=False, indent=2))


game_logger = GameLogger()