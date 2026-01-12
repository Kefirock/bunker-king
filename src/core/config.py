import yaml
import os
from typing import Dict, Any

class CoreConfig:
    def __init__(self, config_dir: str = "Configs"):
        self.config_dir = config_dir
        # Загружаем ТОЛЬКО модели, так как они общие для всех игр
        self.models = self.load_yaml("models.yaml")

    def load_yaml(self, filename: str) -> Dict[str, Any]:
        """Универсальный загрузчик YAML"""
        path = os.path.join(self.config_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            # Если файла нет (например, конфиг конкретной игры), вернем пустоту или ошибку
            print(f"⚠️ Config not found: {path}")
            return {}
        except Exception as e:
            print(f"❌ Error loading {filename}: {e}")
            return {}

# Глобальный инстанс для Ядра
core_cfg = CoreConfig()