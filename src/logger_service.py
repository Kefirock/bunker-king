import datetime
import json
import os
import re
import logging
import sys


# Фильтр для консоли
class ConsoleFilter(logging.Filter):
    def filter(self, record):
        return record.name != "LLM_RAW"


class GameLogger:
    def __init__(self, mode: str, username: str):
        """
        mode: "Solo" или "Multiplayer"
        username: Имя игрока (соло) или лидера лобби (мульти)
        """
        self.base_log_dir = "Logs"

        # 1. Очистка имени от спецсимволов
        safe_name = re.sub(r'[\\/*?:"<>| ]', "_", username).strip() or "Unknown"

        # 2. Формирование пути: Logs / Mode / Username / Timestamp
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.session_dir = os.path.join(self.base_log_dir, mode, safe_name, timestamp)

        os.makedirs(self.session_dir, exist_ok=True)

        # 3. Создаем отдельные логгеры для этой сессии
        self.chat_logger = self._create_file_logger(f"chat_{timestamp}_{id(self)}", "chat_history.log")
        self.logic_logger = self._create_file_logger(f"logic_{timestamp}_{id(self)}", "game_logic.log")
        self.raw_logger = self._create_file_logger(f"raw_{timestamp}_{id(self)}", "raw_debug.log")

        self.icons = {
            "DIRECTOR": "🎬", "JUDGE": "⚖️", "BOT_THOUGHT": "🧠",
            "BOT_SPEECH": "🗣", "VOTE": "🗳", "ERROR": "🔥",
            "SYSTEM": "⚙️", "INFO": "📝", "LLM_REQUEST": "⬆️",
            "LLM_RESPONSE": "⬇️", "GAME_OVER": "🏁",
            "JUDGE_DECISION": "🔨", "HUMAN_SPEECH": "👤",
            "HUMAN_TURN": "👉", "VOTE_RESULTS": "📊"
        }

        start_msg = f"=== NEW {mode.upper()} SESSION: {username} | {timestamp} ==="
        logging.info(start_msg)  # В общую консоль
        if self.logic_logger: self.logic_logger.info(start_msg)  # В файл

    def _create_file_logger(self, name: str, filename: str):
        """Создает логгер, пишущий только в конкретный файл сессии"""
        filepath = os.path.join(self.session_dir, filename)
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # Не дублировать в root

        if logger.hasHandlers():
            logger.handlers.clear()

        fh = logging.FileHandler(filepath, encoding='utf-8')
        fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(fh)
        return logger

    def log_chat_message(self, speaker: str, message: str) -> None:
        msg = f"[{speaker}]: {message}"
        if self.chat_logger: self.chat_logger.info(msg)
        # В консоль дублируем кратко
        logging.info(f"💬 {msg}")

    def log_game_event(self, event_type: str, message: str, details: dict = None) -> None:
        icon = self.icons.get(event_type.upper(), self.icons["INFO"])
        log_msg = f"{icon} [{event_type}] {message}"

        if self.logic_logger:
            file_msg = log_msg
            if details:
                file_msg += f"\nDetails: {json.dumps(details, ensure_ascii=False, indent=2)}\n{'-' * 40}"
            self.logic_logger.info(file_msg)

        logging.info(log_msg)

    def log_llm_interaction(self, service_name: str, model_id: str, prompt: list, response: str,
                            is_json_mode: bool) -> None:
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

    def get_session_path(self) -> str:
        """Возвращает путь к папке для S3 uploader"""
        return self.session_dir


# Настройка общей консоли (запускается при импорте)
def setup_console():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in root.handlers[:]: root.removeHandler(h)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    console.addFilter(ConsoleFilter())
    root.addHandler(console)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)


setup_console()