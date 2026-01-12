import datetime
import json
import os
import logging
import re


class SessionLogger:
    def __init__(self, game_name: str, session_id: str):
        """
        game_name: "Bunker", "Mafia"
        session_id: уникальный ID лобби или чата
        """
        self.base_log_dir = "Logs"
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        # Logs/Bunker/Lobby_ABCD_Time/
        self.session_dir = os.path.join(self.base_log_dir, game_name, f"{session_id}_{timestamp}")
        os.makedirs(self.session_dir, exist_ok=True)

        self.main_logger = self._create_file_logger("game", "game_events.log")

        start_msg = f"=== SESSION START: {game_name} | {session_id} ==="
        self.log_event("SYSTEM", start_msg)

    def _create_file_logger(self, name_suffix: str, filename: str):
        logger = logging.getLogger(f"{name_suffix}_{id(self)}")
        logger.setLevel(logging.INFO)
        fh = logging.FileHandler(os.path.join(self.session_dir, filename), encoding='utf-8')
        fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(fh)
        return logger

    def log_event(self, event_type: str, message: str, details: dict = None):
        """Логирование события игры"""
        msg = f"[{event_type}] {message}"
        if details:
            msg += f"\nDETAILS: {json.dumps(details, ensure_ascii=False)}"
        self.main_logger.info(msg)
        # Дублируем в консоль для дебага
        print(f"📝 {msg[:100]}...")

    def log_llm(self, model: str, prompt: list, response: str):
        """Логирование запросов к нейросети"""
        entry = {
            "model": model,
            "prompt": prompt,
            "response": response
        }
        self.main_logger.info(f"[LLM] {json.dumps(entry, ensure_ascii=False)}")

    def get_session_path(self) -> str:
        return self.session_dir