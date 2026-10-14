import json
import threading
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "modules.json"


class ModuleRegistry:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._lock = threading.RLock()
            cls._instance._data = {"enabled": {}}
            cls._instance._load()
        return cls._instance

    def _load(self):
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        if CONFIG.exists():
            try:
                with open(CONFIG, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                self._data.setdefault("enabled", {})
            except Exception:
                self._data = {"enabled": {}}

    def _save(self):
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def is_enabled(self, key) -> bool:
        with self._lock:
            return bool(self._data["enabled"].get(key, True))

    def set_enabled(self, key, enabled):
        with self._lock:
            self._data["enabled"][key] = bool(enabled)
            self._save()

    def set_many(self, mapping):
        with self._lock:
            for k, v in mapping.items():
                self._data["enabled"][k] = bool(v)
            self._save()

    def all_states(self):
        with self._lock:
            return dict(self._data["enabled"])


def get_registry():
    return ModuleRegistry()