import yaml
import os
import sys
from typing import Dict, Any


class CoreConfig:
    def __init__(self):
        self.config_dir = self._find_config_path()
        self.models = self.load_yaml("models.yaml")

    def _find_config_path(self) -> str:
        """
        Ищет папку Configs в разных стандартных местах.
        """
        # 1. Отталкиваемся от расположения этого файла (src/core/config.py)
        # Поднимаемся: core -> src -> ROOT
        current_file = os.path.abspath(__file__)
        src_core = os.path.dirname(current_file)
        src = os.path.dirname(src_core)
        project_root = os.path.dirname(src)

        # Возможные пути
        candidates = [
            os.path.join(project_root, "Configs"),  # Рядом с src/
            os.path.join(os.getcwd(), "Configs"),  # В текущей рабочей папке
            "/app/Configs",  # Стандарт для Docker
            "Configs",  # Относительный путь
        ]

        for path in candidates:
            if os.path.exists(path) and os.path.isdir(path):
                print(f"✅ Configs found at: {path}")
                return path

        # Если ничего не нашли - выводим отладку для логов Koyeb
        print("🔥 CRITICAL: Configs directory NOT FOUND.")
        print(f"   Searched in: {candidates}")
        print(f"   Current Work Dir (cwd): {os.getcwd()}")
        try:
            print(f"   Files in project root ({project_root}): {os.listdir(project_root)}")
            print(f"   Files in cwd ({os.getcwd()}): {os.listdir(os.getcwd())}")
        except Exception:
            pass

        # Если конфигов нет, работать нельзя — падаем, чтобы было видно в логах
        sys.exit(1)

    def load_yaml(self, filename: str) -> Dict[str, Any]:
        path = os.path.join(self.config_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            print(f"⚠️ Config file not found: {path}")
            return {}
        except Exception as e:
            print(f"❌ Error loading {filename}: {e}")
            return {}


# Глобальный инстанс
core_cfg = CoreConfig()