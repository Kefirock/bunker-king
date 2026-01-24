import datetime
import json
import os
import logging
import re


class SessionLogger:
    def __init__(self, game_name: str, lobby_id: str, host_name: str):
        """
        game_name: Название игры (Bunker, Detective) - будет папкой верхнего уровня.
        host_name: Имя создателя - будет подпапкой.
        """
        self.base_log_dir = "Logs"

        # 1. Санитизация (Очистка от смайликов и пробелов)
        safe_game = self._sanitize_name(game_name)
        safe_host = self._sanitize_name(host_name)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        session_folder_name = f"{timestamp}_{lobby_id}"

        # 2. Локальный путь: Logs / Detective / Alexey / 2026-01-01_LobbyID
        self.session_dir = os.path.join(self.base_log_dir, safe_game, safe_host, session_folder_name)

        # 3. Путь для S3: Detective/Alexey/2026-01-01_LobbyID
        # (Всегда используем прямые слеши для облака)
        self.s3_path = f"{safe_game}/{safe_host}/{session_folder_name}"

        # Создаем полную структуру папок
        os.makedirs(self.session_dir, exist_ok=True)

        self.main_logger = self._create_file_logger("game", "game_events.log")

        start_msg = f"=== SESSION START: {game_name} | Lobby: {lobby_id} | Host: {host_name} ==="
        self.log_event("SYSTEM", start_msg)

    def _sanitize_name(self, text: str) -> str:
        """Убирает все кроме букв, цифр и нижнего подчеркивания"""
        # Заменяем пробелы на _
        text = text.replace(" ", "_")
        # Оставляем только латиницу, кириллицу и цифры
        clean = re.sub(r'[^\w\-_]', '', text)
        return clean if clean else "Unknown"

    def _create_file_logger(self, name_suffix: str, filename: str):
        logger = logging.getLogger(f"{name_suffix}_{id(self)}")
        logger.setLevel(logging.INFO)
        # Удаляем старые хендлеры
        if logger.hasHandlers():
            logger.handlers.clear()

        fh = logging.FileHandler(os.path.join(self.session_dir, filename), encoding='utf-8')
        fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(fh)
        return logger

    def log_event(self, event_type: str, message: str, details: dict = None):
        msg = f"[{event_type}] {message}"
        if details:
            msg += f"\nDETAILS: {json.dumps(details, ensure_ascii=False)}"
        self.main_logger.info(msg)
        print(f"📝 {msg[:100]}...")

    def log_llm(self, model: str, prompt: list, response: str):
        entry = {
            "model": model,
            "prompt": prompt,
            "response": response
        }
        self.main_logger.info(f"[LLM] {json.dumps(entry, ensure_ascii=False)}")

    def get_session_path(self) -> str:
        return self.session_dir

    def get_s3_target_path(self) -> str:
        return self.s3_path