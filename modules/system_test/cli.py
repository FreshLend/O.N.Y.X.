import argparse
import sys


def _register_all():
    from core.module_loader import discover_modules
    from core.module_api import get_bus

    bus = get_bus()
    result = discover_modules("modules")

    mods = None
    if result is None:
        try:
            from core.module_loader import get_loader
            loader = get_loader()
            for attr in ("modules", "_modules", "loaded_modules"):
                cand = getattr(loader, attr, None)
                if cand:
                    mods = list(cand.values()) if isinstance(cand, dict) else list(cand)
                    break
        except Exception:
            pass
    elif isinstance(result, dict):
        mods = list(result.values())
    else:
        mods = list(result)

    if not mods:
        print("[cli] WARNING: discover_modules returned nothing; "
              "bus will be empty", file=sys.stderr)
        return bus

    existing = set(bus.list_modules())
    for m in mods:
        name = getattr(m, "name", None)
        if not name or name in existing:
            continue
        try:
            bus.register_module(m)
            existing.add(name)
        except Exception as e:
            print(f"[cli] register failed for {name}: {e}", file=sys.stderr)
    return bus


def main():
    parser = argparse.ArgumentParser(prog="onyx-system-test")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    _register_all()

    from .module import SystemTestModule
    m = SystemTestModule()
    return m.run_headless(args.format)


if __name__ == "__main__":
    sys.exit(main())