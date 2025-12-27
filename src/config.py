# src/config.py
import yaml
import os
from typing import Dict, Any

class ConfigLoader:
    def __init__(self, config_dir: str = "Configs"):
        self.config_dir = config_dir
        self.gameplay = self._load("gameplay.yaml")
        self.models = self._load("models.yaml")
        self.scenarios = self._load("scenarios.yaml")
        self.prompts = self._load("prompts.yaml")

    def _load(self, filename: str) -> Dict[str, Any]:
        path = os.path.join(self.config_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"❌ Critical Error loading {filename}: {e}")
            raise e

    # --- Хелперы для удобного доступа ---

    def get_visibility(self, round_num: int) -> Dict[str, bool]:
        """Возвращает правила видимости для конкретного раунда"""
        # Если раунд > 3, берем настройки 3-го
        r_key = f"round_{min(round_num, 3)}"
        return self.gameplay["visibility"].get(r_key, {})

    def get_judge_weights(self) -> Dict[str, int]:
        return self.gameplay["judge"]["weights"]

    def get_bot_thresholds(self) -> Dict[str, int]:
        return self.gameplay["bots"]["thresholds"]

# Глобальный инстанс конфига
cfg = ConfigLoader()