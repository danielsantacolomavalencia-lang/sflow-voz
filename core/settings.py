"""Tiny JSON-backed user settings (TTS speed, etc.) persisted in APP_DATA_DIR."""
import json
import os
from config import APP_DATA_DIR

_PATH = os.path.join(APP_DATA_DIR, "settings.json")


def load_settings() -> dict:
    try:
        with open(_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data: dict):
    try:
        with open(_PATH, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[SFlow] No se pudo guardar settings: {e}")
