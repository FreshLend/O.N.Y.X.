import argparse
import json
import sys
from pathlib import Path


CONFIG_ROOT = Path(__file__).resolve().parent.parent.parent / "config"
COLLECTIONS_DIR = CONFIG_ROOT / "collections"


def _ensure_qt():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def cmd_list(args):
    if not COLLECTIONS_DIR.exists():
        print("(no collections)")
        return 0
    for p in sorted(COLLECTIONS_DIR.iterdir()):
        if p.suffix == ".json" and not p.name.startswith("_"):
            print(p.stem)
    return 0


def cmd_run(args):
    _ensure_qt()
    from core.module_loader import discover_modules
    from core.module_api import get_bus

    discover_modules("modules")

    from .module import CollectionsModule
    m = CollectionsModule()
    bus = get_bus()
    bus.register_module(m)

    if args.name not in m._collections:
        print(f"collection not found: {args.name}", file=sys.stderr)
        return 2

    coll = m._collections[args.name]

    if args.vars:
        try:
            extra = json.loads(args.vars)
            if isinstance(extra, dict):
                coll.setdefault("vars", {}).update(extra)
        except Exception as e:
            print(f"invalid --vars JSON: {e}", file=sys.stderr)
            return 2

    if args.env:
        coll["active_env"] = args.env

    if args.workers is not None:
        coll["parallel_workers"] = int(args.workers)

    if args.parallel:
        coll["parallel"] = True

    if args.stop_on_error:
        coll["stop_on_error"] = True

    results, session = m._execute_collection(
        args.name, coll, caller="cli", session=args.session,
        stop_on_error=coll.get("stop_on_error", False),
        parallel=coll.get("parallel", False), on_progress=None)

    passed = sum(1 for r in results
                 if r.get("ok") and r.get("assert_ok", True))
    failed = len(results) - passed

    if args.format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        for i, r in enumerate(results, 1):
            status = r.get("status", "ERR")
            tag = "OK" if r.get("assert_ok", True) and r.get("ok") else "FAIL"
            name = r.get("name") or r.get("url") or "?"
            proto = (r.get("protocol") or "http").upper()
            reauth = " (reauth)" if r.get("reauthed") else ""
            skipped = " [skip]" if r.get("skipped") else ""
            print(f"[{i}/{len(results)}] [{proto}] {name}  {status}  {tag}{reauth}{skipped}")
        print(f"\nsession={session}  passed={passed}  failed={failed}")

    return 0 if failed == 0 else 1


def cmd_datadriven(args):
    _ensure_qt()
    import csv
    from core.module_loader import discover_modules
    from core.module_api import get_bus

    discover_modules("modules")

    from .module import CollectionsModule
    m = CollectionsModule()
    bus = get_bus()
    bus.register_module(m)

    if args.name not in m._collections:
        print(f"collection not found: {args.name}", file=sys.stderr)
        return 2

    coll = m._collections[args.name]
    reqs = coll.get("requests") or []
    if not reqs:
        print("collection has no requests", file=sys.stderr)
        return 2

    rows = []
    try:
        if args.data.endswith(".json"):
            with open(args.data, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = [data]
            rows = [d for d in data if isinstance(d, dict)]
        else:
            with open(args.data, "r", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
    except Exception as e:
        print(f"cannot read data file: {e}", file=sys.stderr)
        return 2

    session = args.session or "cli-dd"
    session_name = m._session_name(args.name)
    auth_config = coll.get("auth") or {}
    results = []

    try:
        for row in rows:
            vars_map = dict(m._resolve_vars(args.name))
            for k, v in row.items():
                vars_map[str(k)] = v
            for req in reqs:
                try:
                    res = m._execute_one(
                        req, None, vars_map, "cli", session,
                        auth_config, session_name)
                except Exception as e:
                    res = {"name": req.get("name"), "url": req.get("url"),
                           "ok": False, "assert_ok": False, "error": str(e)}
                res["_row"] = row
                results.append(res)
    finally:
        m._close_all_ws()

    passed = sum(1 for r in results
                 if r.get("ok") and r.get("assert_ok", True))
    failed = len(results) - passed

    if args.format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        for i, r in enumerate(results, 1):
            status = r.get("status", "ERR")
            tag = "OK" if r.get("assert_ok", True) and r.get("ok") else "FAIL"
            name = r.get("name") or r.get("url") or "?"
            row_str = " ".join(f"{k}={v}" for k, v in (r.get("_row") or {}).items())
            proto = (r.get("protocol") or "http").upper()
            print(f"[{i}/{len(results)}] [{proto}] {name}  {status}  {tag}  [{row_str}]")
        print(f"\nsession={session}  passed={passed}  failed={failed}")

    return 0 if failed == 0 else 1


def main():
    parser = argparse.ArgumentParser(prog="onyx-collections")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list collections")
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="run a collection")
    p_run.add_argument("name")
    p_run.add_argument("--vars", help="JSON object with overrides")
    p_run.add_argument("--env", help="environment name")
    p_run.add_argument("--session", default=None)
    p_run.add_argument("--workers", type=int, default=None,
                       help="override parallel_workers")
    p_run.add_argument("--parallel", action="store_true",
                       help="force parallel execution")
    p_run.add_argument("--stop-on-error", action="store_true",
                       help="stop on first failure")
    p_run.add_argument("--format", choices=("text", "json"), default="text")
    p_run.set_defaults(func=cmd_run)

    p_dd = sub.add_parser("run-dd", help="data-driven run from CSV/JSON")
    p_dd.add_argument("name")
    p_dd.add_argument("data", help="CSV or JSON file with rows")
    p_dd.add_argument("--session", default=None)
    p_dd.add_argument("--format", choices=("text", "json"), default="text")
    p_dd.set_defaults(func=cmd_datadriven)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())