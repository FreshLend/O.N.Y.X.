import importlib
import time
import traceback
from pathlib import Path
from typing import Callable, List, Optional

from .base_module import BaseModule
from .module_spec import scan_specs
from .module_registry import get_registry
from .version import satisfies
from .i18n import tr


def _resolve_load_order(specs, registry):
    enabled = {key: registry.is_enabled(key) for key in specs}
    blocked = {}

    for key, spec in specs.items():
        if not enabled[key]:
            continue
        for dep_key, constraint in spec.dependencies.items():
            if dep_key not in specs:
                blocked[key] = f"missing dependency: {dep_key}"
                enabled[key] = False
                break
            dep_spec = specs[dep_key]
            if not satisfies(dep_spec.version, constraint):
                blocked[key] = (
                    f"{dep_key} {dep_spec.version} !~ {constraint}"
                )
                enabled[key] = False
                break

    changed = True
    while changed:
        changed = False
        for key, spec in specs.items():
            if not enabled.get(key):
                continue
            for dep_key in spec.dependencies:
                if not enabled.get(dep_key):
                    enabled[key] = False
                    blocked[key] = f"required {dep_key} disabled"
                    changed = True
                    break

    order = []
    visited = set()
    temp = set()

    def visit(k):
        if k in visited or k in temp:
            return
        temp.add(k)
        spec = specs[k]
        for dep in spec.dependencies:
            if dep in specs and enabled.get(dep):
                visit(dep)
        temp.discard(k)
        visited.add(k)
        order.append(k)

    for key in sorted(specs.keys()):
        if enabled.get(key):
            visit(key)

    return order, enabled, blocked


def discover_modules(
    modules_dir: str = "modules",
    on_progress: Optional[Callable[[str], None]] = None,
    registry=None,
):
    def emit(msg: str):
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass
        print(f"[loader] {msg}")

    start_time = time.time()

    registry = registry or get_registry()
    base_path = Path(modules_dir).resolve()
    if not base_path.exists():
        emit(f"папка '{modules_dir}' не найдена")
        return []

    specs = scan_specs(base_path)
    emit(f"найдено модулей: {len(specs)}")

    order, enabled, blocked = _resolve_load_order(specs, registry)

    for key in sorted(specs.keys()):
        if not enabled.get(key):
            emit(f"[ ] {key}: {blocked.get(key, 'disabled')}")

    modules: List[BaseModule] = []
    loaded_keys = set()
    total_to_load = len(order)

    for idx, key in enumerate(order, 1):
        spec = specs[key]
        t0 = time.time()
        emit(f"[{idx:>2}/{total_to_load}] {key} v{spec.version}")

        missing = [d for d in spec.dependencies if d not in loaded_keys]
        if missing:
            emit(f"[x] {key}: missing {missing}")
            continue

        import_path = f"{modules_dir}.{key}.module"
        try:
            mod = importlib.import_module(import_path)
        except Exception as e:
            emit(f"[x] {key}: import failed: {e}")
            traceback.print_exc()
            continue

        exported = getattr(mod, "MODULE", None)
        candidates = []
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (isinstance(attr, type)
                    and issubclass(attr, BaseModule)
                    and attr is not BaseModule
                    and attr.__module__ == mod.__name__):
                candidates.append(attr)

        chosen = None
        if isinstance(exported, type) and issubclass(exported, BaseModule):
            chosen = exported
        elif candidates:
            chosen = candidates[0]
            if len(candidates) > 1:
                emit(f"[!] {key}: {len(candidates)} classes, using {chosen.__name__}")

        if chosen is None:
            emit(f"[!] {key}: no BaseModule subclass")
            continue

        try:
            instance = chosen()
            instance.key = key
            instance.version = spec.version
            instance.spec = spec
            instance.name = tr("name", module=key, default=spec.name)
            instance.description = tr("description", module=key,
                                      default=spec.description)
            instance.icon = spec.icon
            instance.icon_name = spec.icon_name
            modules.append(instance)
            loaded_keys.add(key)
            elapsed_ms = int((time.time() - t0) * 1000)
            emit(f"[+] {instance.name}  ({elapsed_ms} ms)")
        except Exception as e:
            emit(f"[x] {key}: instantiate failed: {e}")
            traceback.print_exc()

    t_total = time.time() - start_time
    emit(f"загружено: {len(modules)}  ({int(t_total * 1000)} ms)")
    return modules


def scan_all(modules_dir: str = "modules"):
    return scan_specs(Path(modules_dir).resolve())