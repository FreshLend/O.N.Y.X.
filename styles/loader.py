import json
import importlib
import os
from pathlib import Path
from string import Template
from typing import Optional, Dict

from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter, QFont, QColor
from PySide6.QtSvg import QSvgRenderer

STYLES_DIR = Path(__file__).resolve().parent
STYLES_PACKAGE = __package__ or "styles"

_DEFAULT_THEME = {
    "name": "SWARM",
    "title": "SWARM",
    "colors": {
        "bg":        "#0d0c12",
        "bg2":       "#14131c",
        "bg3":       "#1c1b26",
        "bg4":       "#262533",
        "bg5":       "#2e2c3d",
        "fg":        "#e0dcf5",
        "fg_dim":    "#7a759c",
        "fg_soft":   "#b3abd6",
        "fg_dark":   "#4a4661",
        "border":    "#2a283a",
        "border_hi": "#88bfff",
        "sel_bg":    "#88bfff",
        "sel_fg":    "#0d0c12",
        "danger":    "#ff5252",
        "warning":   "#ffd740",
        "success":   "#69f0ae",
        "shadow":    "rgba(0, 0, 0, 0.9)"
    },
    "fonts": {
        "family": "'Consolas', 'Courier New', monospace",
        "size": 12,
        "size_small": 11,
        "size_title": 22,
        "size_h1": 16,
        "size_icon": 28
    },
    "radius": 6,
    "spacing": 8
}

_DEFAULT_QSS = """
QWidget { background-color: $bg; color: $fg; font-family: $family; font-size: ${size}px; }
QMainWindow { background-color: $bg2; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background-color: $bg2; color: $fg; border: 1px solid $border; padding: $spacing; border-radius: ${radius}px;
    selection-background-color: $sel_bg; selection-color: $sel_fg;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid $border_hi; }
QPushButton {
    background-color: $bg3; color: $fg; border: 1px solid $border;
    padding: 8px 16px; border-radius: ${radius}px; font-weight: bold; letter-spacing: 1px;
}
QPushButton:hover { background-color: $bg4; border-color: $border_hi; color: $border_hi; }
QPushButton:pressed { background-color: $bg5; }
QPushButton:disabled { color: $fg_dark; border-color: $border; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background-color: $bg; color: $fg; border: 1px solid $border_hi; border-radius: ${radius}px;
    selection-background-color: $sel_bg; selection-color: $sel_fg;
}
QLabel { background-color: transparent; }
QFrame#card {
    background-color: $bg3; border: 1px solid $border; border-radius: ${radius}px;
}
QFrame#card:hover { border: 1px solid $border_hi; background-color: $bg4; }

QFrame#header { background-color: $bg2; border-bottom: 1px solid $border; }
QFrame#header QWidget { background-color: transparent; }
QFrame#header QPushButton { background-color: $bg3; border: 1px solid $border; }
QFrame#header QPushButton:hover { background-color: $bg4; border-color: $border_hi; }

QTabWidget::pane { border: 1px solid $border; background-color: $bg2; border-radius: ${radius}px; top: -1px; }
QTabBar { background-color: $bg2; qproperty-drawBase: 0; }
QTabBar::tab {
    background-color: $bg3; color: $fg_dim; border: 1px solid $border; border-bottom: none;
    padding: 8px 18px; margin-right: 2px; border-radius: ${radius}px ${radius}px 0 0; font-weight: bold; letter-spacing: 1px;
}
QTabBar::tab:selected { background-color: $bg4; color: $border_hi; border: 1px solid $border_hi; border-bottom: 1px solid $bg4; }
QTabBar::tab:hover:!selected { background-color: $bg5; color: $fg_soft; }
QHeaderView { background-color: $bg3; border: none; }
QHeaderView::section {
    background-color: $bg3; color: $fg; border: 1px solid $border; border-top: none;
    padding: 6px 10px; font-weight: bold; letter-spacing: 1px;
}
QHeaderView::section:hover { background-color: $bg4; }
QTreeWidget, QTableWidget, QTreeView, QTableView, QListView {
    background-color: $bg2; color: $fg; border: 1px solid $border; border-radius: ${radius}px;
    alternate-background-color: $bg3; gridline-color: $border;
    selection-background-color: $sel_bg; selection-color: $sel_fg; outline: none;
}
QTreeWidget::item, QTableWidget::item, QTreeView::item, QTableView::item, QListView::item { padding: 3px 4px; border: none; }
QTreeWidget::item:selected, QTableWidget::item:selected, QTreeView::item:selected, QTableView::item:selected, QListView::item:selected { background-color: $sel_bg; color: $sel_fg; }
QTreeWidget::item:hover, QTableWidget::item:hover, QTreeView::item:hover, QTableView::item:hover { background-color: $bg4; }

/* Страховка от черных квадратов в таблицах */
QTableWidget QWidget { background: transparent; }

QGroupBox {
    border: 1px solid $border; border-radius: ${radius}px; margin-top: 12px; padding-top: 8px; color: $fg_soft; font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 6px; background-color: $bg; color: $border_hi; letter-spacing: 1px; }
QScrollBar:vertical { background: $bg2; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background: $bg5; min-height: 20px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: $fg_dim; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: none; }
QScrollBar:horizontal { background: $bg2; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background: $bg5; min-width: 20px; border-radius: 5px; }
QScrollBar::handle:horizontal:hover { background: $fg_dim; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; background: none; }
QSplitter::handle { background-color: $border; }
QMenu { background-color: $bg3; border: 1px solid $border_hi; color: $fg; border-radius: ${radius}px; }
QMenu::item:selected { background-color: $border_hi; color: $sel_fg; }
QToolTip { background-color: $bg3; color: $fg; border: 1px solid $border_hi; padding: 4px; border-radius: 4px; }
QDialog, QMessageBox { background-color: $bg; }
QMessageBox QLabel { color: $fg; }
QMessageBox QPushButton { min-width: 90px; }
QTableCornerButton::section { background-color: $bg3; border: 1px solid $border; border-top: none; border-left: none; }
"""

_DEFAULT_ICONS = {
    "settings": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>''',
    "info": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>''',
    "database": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>''',
    "back": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>''',
    "code": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>''',
    "play": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>''',
    "globe": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>''',
    "module": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>''',
}

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

_active: Dict = {
    "name": "SWARM",
    "title": _DEFAULT_THEME["title"],
    "path": None,
    "theme": _DEFAULT_THEME,
    "icons": _DEFAULT_ICONS,
    "qss": Template(_DEFAULT_QSS).safe_substitute(_flatten(_DEFAULT_THEME))
}

def list_styles() -> list:
    styles = ["SWARM"]
    if STYLES_DIR.exists():
        for p in STYLES_DIR.iterdir():
            if p.is_dir() and (p / "theme.json").exists() and not p.name.startswith("_") and p.name != "SWARM":
                styles.append(p.name)
    return sorted(styles)

def load_style(name: str) -> Dict:
    if name.lower() == "swarm" or not STYLES_DIR.exists():
        _active.update({
            "name": "SWARM",
            "title": _DEFAULT_THEME["title"],
            "path": None,
            "theme": _DEFAULT_THEME,
            "icons": _DEFAULT_ICONS,
            "qss": Template(_DEFAULT_QSS).safe_substitute(_flatten(_DEFAULT_THEME))
        })
        return _active

    path = STYLES_DIR / name
    if not path.exists():
        print(f"[theme] Theme '{name}' not found, falling back to SWARM.")
        return load_style("SWARM")

    try:
        with open(path / "theme.json", "r", encoding="utf-8") as f:
            theme = json.load(f)
        qss_template = (path / "style.qss").read_text(encoding="utf-8")
    except Exception as e:
        print(f"[theme] Failed to load theme '{name}': {e}. Using SWARM.")
        return load_style("SWARM")

    flat = _flatten(theme)
    qss = Template(qss_template).safe_substitute(flat)

    icons: Dict[str, str] = dict(_DEFAULT_ICONS)
    icons_file = path / "icons.py"
    if icons_file.exists():
        try:
            mod = importlib.import_module(f"{STYLES_PACKAGE}.{name}.icons")
            custom_icons = getattr(mod, "ICONS", {}) or {}
            icons.update(custom_icons)
        except Exception as e:
            print(f"[theme] Failed to load icons for '{name}': {e}")

    _active.update({
        "name": name,
        "title": theme.get("name", name),
        "path": str(path),
        "theme": theme,
        "icons": icons,
        "qss": qss,
    })
    return _active

def active_style() -> Dict:
    return _active

def get_color(key: str, default: str = "#e0dcf5") -> str:
    colors = _active.get("theme", {}).get("colors", {})
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
    painter.setPen(QColor(get_color("fg", "#e0dcf5")))
    painter.drawText(pix.rect(), Qt.AlignCenter, emoji)
    painter.end()
    return pix

def get_icon(*, icon_name: Optional[str] = None,
             icon_path: Optional[str] = None,
             emoji: Optional[str] = None,
             size: int = 32,
             color: Optional[str] = None) -> QIcon:
    
    color = color or get_color("fg", "#e0dcf5")

    if icon_path and os.path.exists(icon_path):
        if icon_path.lower().endswith(".svg"):
            try:
                with open(icon_path, "r", encoding="utf-8") as f:
                    svg_data = f.read()
                pix = _svg_pixmap(svg_data, size, color)
                if pix:
                    return QIcon(pix)
            except Exception:
                pass
        else:
            pix = QPixmap(icon_path)
            if not pix.isNull():
                pix = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                return QIcon(pix)

    if icon_name and icon_name in _active.get("icons", {}):
        pix = _svg_pixmap(_active["icons"][icon_name], size, color)
        if pix:
            return QIcon(pix)

    if emoji:
        return QIcon(_emoji_pixmap(emoji, size))

    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    return QIcon(pix)

def get_pixmap(*, icon_name=None, icon_path=None, emoji=None, size=32, color=None) -> QPixmap:
    return get_icon(icon_name=icon_name, icon_path=icon_path, emoji=emoji, size=size, color=color).pixmap(size, size)