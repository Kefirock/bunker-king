import os
import yaml
import sys
from src.core.config import core_cfg


class BunkerConfig:
    def __init__(self):
        # Берем путь, который уже нашло ядро
        self.base_dir = core_cfg.config_dir

        self.gameplay = self._load("gameplay.yaml")
        self.scenarios = self._load("scenarios.yaml")
        self.prompts = self._load("prompts.yaml")

        # Проверка, что файл загрузился корректно
        if not self.gameplay or "judge" not in self.gameplay:
            print(f"🔥 CRITICAL ERROR: 'gameplay.yaml' failed to load correctly from {self.base_dir}")
            # Пытаемся вывести содержимое для отладки
            print(f"   Content: {self.gameplay}")
            sys.exit(1)

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
        if not self.gameplay: return {}
        r_key = f"round_{min(round_num, 3)}"
        return self.gameplay.get("visibility", {}).get(r_key, {})


bunker_cfg = BunkerConfig()