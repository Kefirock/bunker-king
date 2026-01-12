import os
import yaml
from src.core.config import core_cfg


class BunkerConfig:
    def __init__(self):
        # Мы предполагаем, что YAML файлы пока лежат в старой папке Configs.
        # В идеальной плагинной системе они должны лежать внутри папки игры (games/bunker/assets),
        # но для упрощения миграции пока читаем оттуда.
        self.base_dir = "Configs"

        self.gameplay = self._load("gameplay.yaml")
        self.scenarios = self._load("scenarios.yaml")
        self.prompts = self._load("prompts.yaml")

        # Упрощенный доступ к весам судьи
        self.judge_weights = self.gameplay["judge"]["weights"]

    def _load(self, filename: str):
        path = os.path.join(self.base_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"❌ BunkerConfig Error loading {filename}: {e}")
            return {}

    def get_visibility(self, round_num: int):
        """Правила тумана войны для текущего раунда"""
        r_key = f"round_{min(round_num, 3)}"
        return self.gameplay["visibility"].get(r_key, {})


bunker_cfg = BunkerConfig()