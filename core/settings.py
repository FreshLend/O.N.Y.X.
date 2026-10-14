import json
from pathlib import Path
from typing import Optional, Dict

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "config" / "settings.json"

_DEFAULT = {
    "app": {
        "language": "en_US",
    },
    "theme": {
        "current": "onyx",
    },
    "proxy": {
        "enabled": False,
        "http": "",
        "https": "",
        "socks5": "",
    },
    "http_client": {
        "impersonate": "chrome",
        "last_request": {
            "engine": "requests",
            "timeout": 30,
            "verify_ssl": True,
            "follow_redirects": True,
            "use_proxy": False,
        },
    },
}


class Settings:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}
            cls._instance.load()
        return cls._instance

    def load(self):
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        else:
            self._data = {}
        for key, val in _DEFAULT.items():
            if key not in self._data:
                self._data[key] = val
            elif isinstance(val, dict):
                for k2, v2 in val.items():
                    if k2 not in self._data[key]:
                        self._data[key][k2] = v2
                    elif isinstance(v2, dict):
                        self._data[key].setdefault(k2, {})
                        for k3, v3 in v2.items():
                            self._data[key][k2].setdefault(k3, v3)

    def save(self):
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, *path, default=None):
        cur = self._data
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur

    def set(self, *path, value):
        if not path:
            raise ValueError("path is empty")
        cur = self._data
        for key in path[:-1]:
            cur = cur.setdefault(key, {})
        cur[path[-1]] = value
        self.save()

    def update(self, *path, values):
        if not path:
            raise ValueError("path is empty")
        cur = self._data
        for key in path:
            cur = cur.setdefault(key, {})
        if not isinstance(cur, dict):
            return
        for k, v in values.items():
            cur[k] = v
        self.save()

    def proxy_enabled(self) -> bool:
        return bool(self.get("proxy", "enabled", default=False))

    def get_requests_proxies(self) -> Optional[Dict[str, str]]:
        if not self.proxy_enabled():
            return None
        http = (self.get("proxy", "http", default="") or "").strip()
        https = (self.get("proxy", "https", default="") or "").strip()
        socks5 = (self.get("proxy", "socks5", default="") or "").strip()

        if socks5:
            return {"http": socks5, "https": socks5}
        if http or https:
            return {"http": http or https, "https": https or http}
        return None


def get_settings() -> Settings:
    return Settings()