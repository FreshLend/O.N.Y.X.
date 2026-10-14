import json
from pathlib import Path

from PySide6.QtCore import QObject, Signal

ROOT = Path(__file__).resolve().parent.parent
LANGS_DIR = ROOT / "langs"
DEFAULT_LOCALE = "en_US"


class I18n(QObject):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.locale = DEFAULT_LOCALE
        self._core = {}
        self._fallback = {}
        self._modules = {}

    def available_locales(self):
        result = []
        if LANGS_DIR.exists():
            for p in sorted(LANGS_DIR.iterdir()):
                if p.suffix == ".json" and not p.name.startswith("_"):
                    result.append(p.stem)
        if DEFAULT_LOCALE not in result:
            result.append(DEFAULT_LOCALE)
        return result

    def set_locale(self, locale: str):
        self.locale = locale or DEFAULT_LOCALE
        self._core = self._load_core(self.locale)
        self._fallback = self._load_core(DEFAULT_LOCALE)
        self._modules = {}
        self.changed.emit(self.locale)

    def _load_core(self, locale):
        path = LANGS_DIR / f"{locale}.json"
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_module(self, module_key, locale):
        cache_key = (module_key, locale)
        if cache_key in self._modules:
            return self._modules[cache_key]
        path = ROOT / "modules" / module_key / "langs" / f"{locale}.json"
        data = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        self._modules[cache_key] = data
        return data

    def has_module(self, module_key):
        return (ROOT / "modules" / module_key / "langs").is_dir()

    def tr(self, message_key, module=None, default=None, **kwargs):
        value = None
        if module:
            value = self._load_module(module, self.locale).get(message_key)
            if value is None:
                value = self._load_module(module, DEFAULT_LOCALE).get(message_key)
        else:
            value = self._core.get(message_key)
            if value is None:
                value = self._fallback.get(message_key)
        if value is None:
            value = default if default is not None else message_key
        if kwargs:
            try:
                return value.format(**kwargs)
            except Exception:
                return value
        return value


_i18n = None


def get_i18n() -> I18n:
    global _i18n
    if _i18n is None:
        _i18n = I18n()
    return _i18n


def tr(message_key, module=None, default=None, **kwargs):
    return get_i18n().tr(message_key, module=module, default=default, **kwargs)