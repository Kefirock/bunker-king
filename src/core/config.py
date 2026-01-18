import yaml
import os
import sys
from typing import Dict, Any


class CoreConfig:
    def __init__(self):
        # --- ПОИСК ГЛОБАЛЬНОЙ ПАПКИ CONFIGS ---
        # Она лежит в корне проекта (рядом с main.py)
        print("🔍 DEBUG: CoreConfig initializing...")
        self.config_dir = self._find_global_config_path()

        if not os.path.exists(self.config_dir):
            print("🔥 CRITICAL: Global 'Configs' directory not found.")
            sys.exit(1)

        # Загружаем только модели (общие для всех игр)
        self.models = self.load_yaml("models.yaml")
        print(f"✅ CoreConfig loaded. Models available: {len(self.models.get('player_models', []))}")

    def _find_global_config_path(self) -> str:
        candidates = [
            os.path.join(os.getcwd(), "Configs"),  # Для Docker (/app/Configs)
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "Configs")),
            # Для локального запуска из src/core
            "Configs"
        ]
        for path in candidates:
            if os.path.exists(path) and os.path.isdir(path):
                return path
        return ""

    def load_yaml(self, filename: str) -> Dict[str, Any]:
        path = os.path.join(self.config_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            print(f"⚠️ Global config file not found: {path}")
            return {}
        except Exception as e:
            print(f"❌ Error loading {filename}: {e}")
            return {}


# Глобальный инстанс
core_cfg = CoreConfig()