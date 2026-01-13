import os
import yaml
import sys
from src.core.config import core_cfg


class BunkerConfig:
    def __init__(self):
        # 1. Вычисляем абсолютный путь к папке Configs
        # Файл лежит в src/games/bunker/config.py
        # Поднимаемся: bunker -> games -> src -> root
        current_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
        self.base_dir = os.path.join(root_dir, "Configs")

        self.gameplay = self._load("gameplay.yaml")
        self.scenarios = self._load("scenarios.yaml")
        self.prompts = self._load("prompts.yaml")

        # Защита: Если конфиг не загрузился, не даем упасть с KeyError,
        # а выводим понятную ошибку и выходим.
        if not self.gameplay or "judge" not in self.gameplay:
            print(f"🔥 CRITICAL ERROR: 'gameplay.yaml' failed to load from {self.base_dir}")
            # Создаем заглушку, чтобы IDE не ругалась, но по факту это конец
            self.judge_weights = {}
            # Можно выбросить исключение, чтобы остановить запуск
            return

        self.judge_weights = self.gameplay["judge"]["weights"]

    def _load(self, filename: str):
        path = os.path.join(self.base_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"❌ BunkerConfig Error loading {path}: {e}")
            return {}

    def get_visibility(self, round_num: int):
        """Правила тумана войны для текущего раунда"""
        # Защита от отсутствия ключей
        if not self.gameplay: return {}

        r_key = f"round_{min(round_num, 3)}"
        return self.gameplay.get("visibility", {}).get(r_key, {})


bunker_cfg = BunkerConfig()