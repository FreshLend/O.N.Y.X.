import threading
from collections import deque
from typing import Callable, Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal


class ModuleAPI(QObject):
    event = Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.RLock()
        self._services: Dict[str, dict] = {}
        self._modules: Dict[str, Any] = {}
        self._event_buffer: deque = deque(maxlen=500)

    def register_module(self, module) -> None:
        with self._lock:
            mod_name = module.name
            self._modules[mod_name] = module

        if hasattr(module, "on_load"):
            try:
                module.on_load(self)
            except Exception as e:
                print(f"[bus] on_load('{mod_name}') failed: {e}")

        self.emit_event("module.loaded", name=mod_name)

    def unregister_module(self, name: str) -> None:
        with self._lock:
            mod = self._modules.pop(name, None)
            self._services = {
                k: v for k, v in self._services.items() if v["provider"] != name
            }

        if mod and hasattr(mod, "on_unload"):
            try:
                mod.on_unload(self)
            except Exception as e:
                print(f"[bus] on_unload('{name}') failed: {e}")

        self.emit_event("module.unloaded", name=name)

    def get_module(self, name: str):
        return self._modules.get(name)

    def list_modules(self) -> List[str]:
        return list(self._modules.keys())

    def register_service(self, provider: str, service_name: str,
                         fn: Callable, description: str = "") -> None:
        if not callable(fn):
            raise TypeError(f"Service '{service_name}' must be callable")
        with self._lock:
            self._services[service_name] = {
                "provider": provider,
                "fn": fn,
                "description": description,
            }
        self.emit_event("service.registered",
                        name=service_name, provider=provider)

    def unregister_service(self, service_name: str) -> None:
        with self._lock:
            existed = self._services.pop(service_name, None)
        if existed:
            self.emit_event("service.unregistered", name=service_name)

    def has_service(self, name: str) -> bool:
        return name in self._services

    def list_services(self) -> Dict[str, dict]:
        return dict(self._services)

    def call(self, service_name: str, *args, **kwargs) -> Any:
        svc = self._services.get(service_name)
        if not svc:
            raise KeyError(f"Service '{service_name}' not registered")
        return svc["fn"](*args, **kwargs)

    def try_call(self, service_name: str, *args, default=None, **kwargs) -> Any:
        try:
            return self.call(service_name, *args, **kwargs)
        except KeyError:
            return default

    def emit_event(self, event_name: str, **payload) -> None:
        with self._lock:
            self._event_buffer.append((event_name, dict(payload)))
        self.event.emit(event_name, payload)

    def recent_events(self, limit: int = 500) -> List[Tuple[str, dict]]:
        with self._lock:
            items = list(self._event_buffer)
        return items[-limit:]

    def clear_event_buffer(self) -> None:
        with self._lock:
            self._event_buffer.clear()


_bus: Optional[ModuleAPI] = None


def get_bus() -> ModuleAPI:
    global _bus
    if _bus is None:
        _bus = ModuleAPI()
    return _bus