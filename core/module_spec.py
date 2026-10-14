import json
from pathlib import Path


class ModuleSpec:
    def __init__(self, key, path, data):
        self.key = key
        self.path = path
        self.name = data.get("name", key)
        self.version = str(data.get("version", "0.0.0"))
        self.author = data.get("author", "unknown")
        self.description = data.get("description", "")
        self.icon = data.get("icon", "?")
        self.icon_name = data.get("icon_name")
        self.dependencies = dict(data.get("dependencies") or {})
        self.optional_dependencies = dict(data.get("optional_dependencies") or {})
        self.raw = data

    def all_dependencies(self, include_optional=True):
        out = dict(self.dependencies)
        if include_optional:
            out.update(self.optional_dependencies)
        return out


def load_spec(module_dir: Path):
    meta = module_dir / "metadata.json"
    if not meta.exists():
        return None
    try:
        with open(meta, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return ModuleSpec(module_dir.name, module_dir, data)


def scan_specs(modules_dir: Path):
    specs = {}
    if not modules_dir.exists():
        return specs
    for entry in sorted(modules_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        if not (entry / "module.py").exists():
            continue
        spec = load_spec(entry)
        if spec is not None:
            specs[spec.key] = spec
    return specs