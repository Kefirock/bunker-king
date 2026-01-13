import os
import yaml
import sys
from src.core.config import core_cfg

print("🛠 Loading module: src.games.bunker.config...")


class BunkerConfig:
    def __init__(self):
        # Используем путь, который уже нашло Ядро
        self.base_dir = core_cfg.config_dir

        print(f"📂 BunkerConfig base_dir: {self.base_dir}")

        self.gameplay = self._load("gameplay.yaml")
        self.scenarios = self._load("scenarios.yaml")
        self.prompts = self._load("prompts.yaml")

        # Проверка на пустоту
        if not self.gameplay:
            print("🔥 FATAL: gameplay.yaml is empty or failed to load!")
            sys.exit(1)

        # Безопасное получение весов (get вместо [])
        self.judge_weights = self.gameplay.get("judge", {}).get("weights", {})

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


# Создаем экземпляр БЕЗ try-except.
# Если тут ошибка - пусть бот упадет и покажет Traceback.
print("⚙️ Instantiating BunkerConfig...")
bunker_cfg = BunkerConfig()
print("✅ BunkerConfig instantiated successfully.")