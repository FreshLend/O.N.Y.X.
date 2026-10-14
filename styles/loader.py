import re
import json
import importlib
from pathlib import Path
from string import Template
from typing import Optional, Dict

from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter, QFont, QColor
from PySide6.QtSvg import QSvgRenderer


STYLES_DIR = Path(__file__).resolve().parent
STYLES_PACKAGE = __package__ or "styles"

_active: Dict = {
    "name": "matrix",
    "title": "Matrix",
    "path": None,
    "theme": {},
    "icons": {},
}


def list_styles() -> list:
    if not STYLES_DIR.exists():
        return []
    return sorted(
        p.name for p in STYLES_DIR.iterdir()
        if p.is_dir()
        and (p / "theme.json").exists()
        and not p.name.startswith("_")
    )


def load_style(name: str) -> Dict:
    path = STYLES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Theme '{name}' not found")

    with open(path / "theme.json", "r", encoding="utf-8") as f:
        theme = json.load(f)

    qss_template = (path / "style.qss").read_text(encoding="utf-8")
    flat = _flatten(theme)
    qss = Template(qss_template).safe_substitute(flat)

    unresolved = re.findall(r"\$[A-Za-z_][A-Za-z0-9_]*", qss)
    if unresolved:
        print(f"[theme:{name}] unresolved vars: {sorted(set(unresolved))}")

    icons: Dict[str, str] = {}
    icons_file = path / "icons.py"
    if icons_file.exists():
        mod = importlib.import_module(f"{STYLES_PACKAGE}.{name}.icons")
        icons = getattr(mod, "ICONS", {}) or {}

    _active.update({
        "name": name,
        "title": theme.get("name", name),
        "path": str(path),
        "theme": theme,
        "icons": icons,
        "qss": qss,
    })
    return _active


def _flatten(theme: dict) -> Dict[str, str]:
    out: Dict[str, str] = {}

    def walk(node):
        for key, value in node.items():
            if isinstance(value, dict):
                walk(value)
            elif isinstance(value, list):
                continue
            else:
                out[key] = str(value)

    walk(theme)
    return out


def active_style() -> Dict:
    return _active


def get_color(key: str, default: str = "#00ff41") -> str:
    colors = _active["theme"].get("colors", {})
    return colors.get(key, default)


def build_qss() -> str:
    return _active.get("qss", "")


def _svg_pixmap(svg: str, size: int, color: str) -> Optional[QPixmap]:
    svg = svg.replace("{color}", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return None
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return pix


def _emoji_pixmap(emoji: str, size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    font = QFont()
    font.setPointSize(int(size * 0.7))
    painter.setFont(font)
    painter.setPen(QColor(get_color("fg", "#00ff41")))
    painter.drawText(pix.rect(), Qt.AlignCenter, emoji)
    painter.end()
    return pix


def get_icon(*, icon_name: Optional[str] = None,
             emoji: Optional[str] = None,
             size: int = 32,
             color: Optional[str] = None) -> QIcon:
    color = color or get_color("fg", "#00ff41")

    if icon_name and icon_name in _active["icons"]:
        pix = _svg_pixmap(_active["icons"][icon_name], size, color)
        if pix:
            return QIcon(pix)

    if emoji:
        return QIcon(_emoji_pixmap(emoji, size))

    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    return QIcon(pix)


def get_pixmap(*, icon_name=None, emoji=None, size=32, color=None) -> QPixmap:
    return get_icon(icon_name=icon_name, emoji=emoji, size=size, color=color).pixmap(size, size)