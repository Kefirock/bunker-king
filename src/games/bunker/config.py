import yaml
import os
import sys
from typing import Dict, Any


class CoreConfig:
    def __init__(self):
        # --- DEBUG БЛОК ---
        print("🔍 DEBUG: FILE SYSTEM CHECK")
        try:
            cwd = os.getcwd()
            print(f"📂 Current Working Dir: {cwd}")
            print(f"📄 Files in {cwd}: {os.listdir(cwd)}")

            # Если есть папка src, глянем внутрь
            if os.path.exists("src"):
                print(f"📄 Files in src: {os.listdir('src')}")

            # Проверка регистра (Linux чувствителен!)
            configs_candidates = [f for f in os.listdir(cwd) if f.lower() == "configs"]
            if configs_candidates:
                print(f"👀 Found similar folders: {configs_candidates}")
        except Exception as e:
            print(f"⚠️ Debug error: {e}")
        # ------------------

        self.config_dir = self._find_config_path()
        if not os.path.exists(self.config_dir):
            # Если конфигов нет, нет смысла продолжать - выходим.
            print("🔥 CRITICAL: Configs not found. Exiting.")
            sys.exit(1)

        self.models = self.load_yaml("models.yaml")

    def _find_config_path(self) -> str:
        candidates = [
            os.path.join(os.getcwd(), "Configs"),
            "/app/Configs",
            "Configs"
        ]
        for path in candidates:
            if os.path.exists(path) and os.path.isdir(path):
                print(f"✅ Configs found at: {path}")
                return path
        return ""

    def load_yaml(self, filename: str) -> Dict[str, Any]:
        path = os.path.join(self.config_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"❌ Error loading {filename}: {e}")
            return {}


core_cfg = CoreConfig()