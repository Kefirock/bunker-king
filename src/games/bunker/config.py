import os
import yaml
import sys
# Нам всё еще нужен core_cfg, чтобы получать доступ к моделям, но не к путям
from src.core.config import core_cfg

print("🛠 Loading module: src.games.bunker.config...")


class BunkerConfig:
    def __init__(self):
        # 1. Вычисляем путь ОТНОСИТЕЛЬНО ЭТОГО ФАЙЛА
        # Этот файл лежит в src/games/bunker/
        # Мы ищем папку src/games/bunker/configs/
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.base_dir = os.path.join(current_dir, "configs")

        print(f"📂 BunkerConfig looking for files in: {self.base_dir}")

        # Проверяем, существует ли папка
        if not os.path.exists(self.base_dir):
            print(f"🔥 CRITICAL ERROR: Game config dir missing at {self.base_dir}")
            sys.exit(1)

        self.gameplay = self._load("gameplay.yaml")
        self.scenarios = self._load("scenarios.yaml")
        self.prompts = self._load("prompts.yaml")

        # Проверка на корректность загрузки
        if not self.gameplay or "judge" not in self.gameplay:
            print(f"🔥 FATAL: gameplay.yaml is empty or failed to load from {self.base_dir}!")
            sys.exit(1)

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


# Создаем экземпляр
print("⚙️ Instantiating BunkerConfig...")
bunker_cfg = BunkerConfig()
print("✅ BunkerConfig instantiated successfully.")