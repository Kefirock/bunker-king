import yaml
import os
from typing import Dict, Any


class CoreConfig:
    def __init__(self):
        # 1. Вычисляем абсолютный путь к папке Configs
        # Сейчас мы в src/core/config.py
        # Нам нужно подняться на 2 уровня вверх (core -> src -> root)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
        self.config_dir = os.path.join(root_dir, "Configs")

        # Проверка на всякий случай
        if not os.path.exists(self.config_dir):
            print(f"🔥 CRITICAL: Config dir not found at {self.config_dir}")

        self.models = self.load_yaml("models.yaml")

    def load_yaml(self, filename: str) -> Dict[str, Any]:
        path = os.path.join(self.config_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            print(f"⚠️ Config not found: {path}")
            return {}
        except Exception as e:
            print(f"❌ Error loading {filename}: {e}")
            return {}


# Глобальный инстанс для Ядра
core_cfg = CoreConfig()