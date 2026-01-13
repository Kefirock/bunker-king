import os
import yaml
import sys
from src.core.config import core_cfg


class BunkerConfig:
    def __init__(self):
        # Используем путь, который уже нашло Ядро (core_cfg)
        # Это гарантирует, что мы смотрим в ту же папку /app/Configs
        self.base_dir = core_cfg.config_dir

        print(f"🤖 BunkerConfig loading from: {self.base_dir}")

        self.gameplay = self._load("gameplay.yaml")
        self.scenarios = self._load("scenarios.yaml")
        self.prompts = self._load("prompts.yaml")

        # Защита: Если конфиг не загрузился - останавливаемся
        if not self.gameplay or "judge" not in self.gameplay:
            print(f"🔥 CRITICAL ERROR: 'gameplay.yaml' failed to load or is empty.")
            # Пытаемся показать, что не так
            print(f"   Contents of gameplay: {self.gameplay}")
            sys.exit(1)

        self.judge_weights = self.gameplay["judge"]["weights"]

    def _load(self, filename: str):
        path = os.path.join(self.base_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            print(f"❌ File not found: {path}")
            return {}
        except Exception as e:
            print(f"❌ Error loading {path}: {e}")
            return {}

    def get_visibility(self, round_num: int):
        if not self.gameplay: return {}
        r_key = f"round_{min(round_num, 3)}"
        return self.gameplay.get("visibility", {}).get(r_key, {})


# === ВОТ ЭТА СТРОЧКА САМАЯ ВАЖНАЯ ===
# Без нее другие файлы не могут импортировать 'bunker_cfg'
try:
    bunker_cfg = BunkerConfig()
except Exception as e:
    print(f"🔥 FATAL ERROR initializing BunkerConfig: {e}")
    sys.exit(1)