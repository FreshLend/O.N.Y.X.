import re
from typing import Optional, Any

from PySide6.QtWidgets import QApplication, QLabel

from styles import loader as theme_loader


def color(key: str, default: str = "#8fc9ff") -> str:
    return theme_loader.get_color(key, default)


def parent_widget(widget_attr) -> Any:
    if widget_attr is not None:
        return widget_attr
    return QApplication.activeWindow()


def make_label(text: str, *, key: str = "fg_dim",
               size: int = 11, spacing: int = 0,
               bold: bool = False) -> QLabel:
    lbl = QLabel(text)
    style = f"color: {color(key)}; font-size: {size}px;"
    if spacing:
        style += f" letter-spacing: {spacing}px;"
    if bold:
        style += " font-weight: bold;"
    lbl.setStyleSheet(style)
    return lbl


def parse_headers(text: str) -> dict:
    headers: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers


def extract_by_path(data: Any, path: str) -> Optional[Any]:
    if not path:
        return None
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def parse_int_list(text: str) -> list:
    out: list = []
    for part in text.replace(";", ",").split(","):
        p = part.strip()
        if p.isdigit():
            out.append(int(p))
    return out


def coerce_value(value: str) -> Any:
    if value == "":
        return ""
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def resolve_placeholders(text, vars_map=None, bus=None) -> str:
    if not text or "{{" not in text:
        return text
    if bus is None:
        from .module_api import get_bus
        bus = get_bus()
    if vars_map is None:
        vars_map = {}

    def repl(m):
        inner = m.group(1).strip()
        default = None
        if ":" in inner:
            k, default = inner.split(":", 1)
            inner = k.strip()
            default = default.strip()
        if inner in vars_map:
            return str(vars_map[inner])
        key = inner
        if key.startswith("vault."):
            key = key[len("vault."):]
        try:
            val = bus.call("vault.get", key, default)
        except Exception:
            return m.group(0)
        if val is None:
            return default if default is not None else m.group(0)
        return str(val)

    return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", repl, text)