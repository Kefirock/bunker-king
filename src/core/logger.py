import datetime
import json
import os
import logging
import re


class SessionLogger:
    def __init__(self, game_name: str, lobby_id: str, host_name: str):
        """
        host_name: Имя создателя (для папки в S3)
        """
        self.base_log_dir = "Logs"

        # Очистка имени от смайликов и спецсимволов для путей
        safe_host = re.sub(r'[^\w\-_]', '', host_name.replace(' ', '_'))
        if not safe_host: safe_host = "UnknownUser"

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        # Локально храним просто по ID сессии, чтобы не плодить папки
        self.session_dir = os.path.join(self.base_log_dir, f"{lobby_id}_{timestamp}")

        # А вот для S3 готовим красивый путь: Alexey/2026-01-01_12-00_LOBBYID
        self.s3_path = f"{safe_host}/{timestamp}_{lobby_id}"

        os.makedirs(self.session_dir, exist_ok=True)

        self.main_logger = self._create_file_logger("game", "game_events.log")

        start_msg = f"=== SESSION START: {game_name} | Lobby: {lobby_id} | Host: {host_name} ==="
        self.log_event("SYSTEM", start_msg)

    def _create_file_logger(self, name_suffix: str, filename: str):
        logger = logging.getLogger(f"{name_suffix}_{id(self)}")
        logger.setLevel(logging.INFO)
        # Удаляем старые хендлеры, если есть
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