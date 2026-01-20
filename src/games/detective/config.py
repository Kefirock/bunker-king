import os
import yaml
import sys

class DetectiveConfig:
    def __init__(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.base_dir = os.path.join(current_dir, "configs")

        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir, exist_ok=True)

        self.prompts = self._load("prompts.yaml")

    def _load(self, filename: str):
        path = os.path.join(self.base_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            print(f"⚠️ Detective config not found: {path}")
            return {}
        except Exception as e:
            print(f"❌ Error loading {path}: {e}")
            return {}

detective_cfg = DetectiveConfig()