from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QFrame, QGridLayout, QScrollArea,
    QSizePolicy, QDialog,
)

from core.module_loader import discover_modules
from core.settings_dialog import SettingsDialog
from core.settings import get_settings
from core.module_api import get_bus
from core.bus_dialog import BusDialog
from core.about_dialog import AboutDialog
from core.modules_dialog import ModulesDialog
from core.brand import APP_NAME, APP_VERSION, APP_BRAND
from core.utils import color
from core.i18n import tr, get_i18n
from styles import loader as theme_loader


class ModuleCard(QFrame):
    def __init__(self, module, on_click):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumSize(240, 170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.module = module
        self._on_click = on_click

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        icon_label = QLabel()
        icon_label.setPixmap(theme_loader.get_pixmap(
            icon_name=getattr(module, "icon_name", None),
            emoji=getattr(module, "icon", "?"),
            size=36,
        ))
        icon_label.setFixedHeight(40)
        layout.addWidget(icon_label)

        title = QLabel(module.name.upper())
        title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; "
            f"color: {color('fg')};"
        )
        layout.addWidget(title)

        ver = getattr(module, "version", None)
        if ver:
            v_lbl = QLabel(f"v{ver}")
            v_lbl.setStyleSheet(f"color: {color('fg_dim')}; font-size: 10px;")
            layout.addWidget(v_lbl)

        desc = QLabel(module.description)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {color('fg_dim')}; font-size: 11px;")
        layout.addWidget(desc)

        layout.addStretch()
        hint = QLabel("> " + tr("app.menu.click_to_open"))
        hint.setStyleSheet(
            f"color: {color('fg_soft')}; font-size: 10px; letter-spacing: 1px;"
        )
        layout.addWidget(hint)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click(self.module)


class MainWindow(QMainWindow):
    def __init__(self, modules=None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  //  v{APP_VERSION}")
        self.resize(880, 680)

        self.settings = get_settings()
        self.bus = get_bus()
        self.i18n = get_i18n()

        if modules is None:
            self.modules = discover_modules("modules")
        else:
            self.modules = modules

        for m in self.modules:
            if m.name not in self.bus.list_modules():
                self.bus.register_module(m)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self._widget_cache = {}

        self.stack.addWidget(self._build_menu_screen())
        self._module_host = QWidget()
        self.stack.addWidget(self._module_host)

        self._bus_dialog = None
        self._current_module = None
        self.i18n.changed.connect(self._on_locale_changed)

    def _theme_title(self) -> str:
        style = theme_loader.active_style()
        return style.get("title") or style.get("name") or "?"

    def _build_menu_screen(self) -> QWidget:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        banner = QFrame()
        banner.setObjectName("header")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(24, 20, 24, 20)

        brand_box = QWidget()
        brand_box.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        brand_layout = QVBoxLayout(brand_box)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(2)

        title = QLabel(f"╔══ {APP_BRAND} ══╗")
        title.setAlignment(Qt.AlignHCenter)
        title.setStyleSheet(
            f"font-size: 22px; font-weight: bold; "
            f"color: {color('fg')}; letter-spacing: 3px;"
        )
        brand_layout.addWidget(title)

        subtitle = QLabel(tr(
            "app.menu.stats",
            modules=len(self.modules),
            services=len(self.bus.list_services()),
            theme=self._theme_title(),
        ))
        subtitle.setAlignment(Qt.AlignHCenter)
        subtitle.setStyleSheet(f"color: {color('fg_dim')}; font-size: 12px;")
        brand_layout.addWidget(subtitle)

        banner_layout.addWidget(brand_box)
        banner_layout.addStretch()

        modules_btn = QPushButton(" " + tr("app.menu.modules"))
        modules_btn.setIcon(theme_loader.get_icon(icon_name="code", emoji="*", size=14))
        modules_btn.setFixedWidth(180)
        modules_btn.clicked.connect(self._open_modules)
        banner_layout.addWidget(modules_btn)

        about_btn = QPushButton(" " + tr("app.menu.about"))
        about_btn.setIcon(theme_loader.get_icon(icon_name="info", emoji="i", size=14))
        about_btn.setFixedWidth(160)
        about_btn.clicked.connect(self._open_about)
        banner_layout.addWidget(about_btn)

        bus_btn = QPushButton(" " + tr("app.menu.bus"))
        bus_btn.setIcon(theme_loader.get_icon(icon_name="database", emoji="*", size=14))
        bus_btn.setFixedWidth(150)
        bus_btn.clicked.connect(self._open_bus_monitor)
        banner_layout.addWidget(bus_btn)

        settings_btn = QPushButton(" " + tr("app.menu.settings"))
        settings_btn.setIcon(theme_loader.get_icon(icon_name="settings", emoji="*", size=14))
        settings_btn.setFixedWidth(180)
        settings_btn.clicked.connect(self._open_settings)
        banner_layout.addWidget(settings_btn)

        outer.addWidget(banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(24, 24, 24, 24)
        grid.setSpacing(16)

        if not self.modules:
            empty = QLabel(tr("app.menu.empty"))
            empty.setStyleSheet(f"color: {color('danger')}; font-size: 13px;")
            grid.addWidget(empty, 0, 0)

        for i, module in enumerate(self.modules):
            card = ModuleCard(module, self._open_module)
            grid.addWidget(card, i // 3, i % 3)

        for col in range(3):
            grid.setColumnStretch(col, 1)
        grid.setRowStretch(grid.rowCount(), 1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        footer = QLabel(tr("app.menu.footer"))
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet(
            f"color: {color('fg_dim')}; font-size: 12px; "
            f"padding: 12px; border-top: 1px solid {color('border')};"
        )
        outer.addWidget(footer)

        return root

    def _open_about(self):
        AboutDialog(self).exec()

    def _open_bus_monitor(self):
        if self._bus_dialog is None:
            self._bus_dialog = BusDialog(self)
            self._bus_dialog.setModal(False)
            self._bus_dialog.finished.connect(self._on_bus_closed)
        self._bus_dialog.show()
        self._bus_dialog.raise_()
        self._bus_dialog.activateWindow()

    def _on_bus_closed(self, *_):
        self._bus_dialog = None

    def _open_modules(self):
        dlg = ModulesDialog(self, modules_dir="modules")
        if dlg.exec() != QDialog.Accepted:
            return
        self._reload_modules()

    def _reload_modules(self):
        self._widget_cache.clear()
        for name in list(self.bus.list_modules()):
            mod = self.bus.get_module(name)
            if mod and hasattr(mod, "on_shutdown"):
                try:
                    mod.on_shutdown()
                except Exception:
                    pass
            self.bus.unregister_module(name)

        self.modules = discover_modules("modules")
        for m in self.modules:
            if m.name not in self.bus.list_modules():
                self.bus.register_module(m)

        self._current_module = None
        self._rebuild_ui()

    def _rebuild_ui(self):
        self._widget_cache.clear()
        new_menu = self._build_menu_screen()
        old_menu = self.stack.widget(0)
        self.stack.insertWidget(0, new_menu)
        self.stack.removeWidget(old_menu)
        old_menu.deleteLater()
        self.stack.setCurrentIndex(0)

        old_layout = self._module_host.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()

    def _on_locale_changed(self, *_):
        self._refresh_module_meta()

        if self._current_module is None:
            new_menu = self._build_menu_screen()
            old_menu = self.stack.widget(0)
            self.stack.insertWidget(0, new_menu)
            self.stack.removeWidget(old_menu)
            old_menu.deleteLater()
            self.stack.setCurrentIndex(0)
            return

        self._rebuild_menu()
        for name in self.bus.list_modules():
            mod = self.bus.get_module(name)
            if mod and hasattr(mod, "retranslate"):
                try:
                    mod.retranslate()
                except Exception as e:
                    print(f"[i18n] retranslate {name}: {e}")

    def _refresh_module_meta(self):
        for m in self.modules:
            key = getattr(m, "key", None)
            spec = getattr(m, "spec", None)
            if not key or spec is None:
                continue
            try:
                m.name = tr("name", module=key, default=spec.name)
                m.description = tr("description", module=key,
                                   default=spec.description)
            except Exception:
                continue

    def _rebuild_menu(self):
        new_menu = self._build_menu_screen()
        old_menu = self.stack.widget(0)
        self.stack.insertWidget(0, new_menu)
        self.stack.removeWidget(old_menu)
        old_menu.deleteLater()
        if self._current_module is not None:
            self.stack.setCurrentIndex(1)

    def _open_module(self, module):
        key = getattr(module, "key", None) or module.name

        layout = self._module_host.layout()
        if layout is None:
            self._module_host.setLayout(QVBoxLayout())
            self._module_host.layout().setContentsMargins(0, 0, 0, 0)
            self._module_host.layout().setSpacing(0)
            layout = self._module_host.layout()
        else:
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w:
                    w.setParent(None)

        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 12, 16, 12)

        back = QPushButton(" " + tr("app.menu.back"))
        back.setIcon(theme_loader.get_icon(icon_name="back", emoji="<", size=14))
        back.setFixedWidth(140)
        back.clicked.connect(lambda: self._close_module())
        h.addWidget(back)

        icon_label = QLabel()
        icon_label.setPixmap(theme_loader.get_pixmap(
            icon_name=getattr(module, "icon_name", None),
            emoji=getattr(module, "icon", "?"),
            size=22,
        ))
        h.addWidget(icon_label)

        title = QLabel(module.name.upper())
        title.setStyleSheet(
            f"font-size: 16px; font-weight: bold; "
            f"color: {color('fg')}; letter-spacing: 2px;")
        h.addWidget(title)

        ver = getattr(module, "version", None)
        if ver:
            ver_lbl = QLabel(f"v{ver}")
            ver_lbl.setStyleSheet(f"color: {color('fg_dim')}; font-size: 11px;")
            h.addWidget(ver_lbl)

        h.addStretch()
        layout.addWidget(header)

        widget = self._widget_cache.get(key)
        if widget is None:
            try:
                widget = module.create_widget()
            except Exception as e:
                widget = QLabel(tr("app.module.error", error=e))
                widget.setStyleSheet(f"color: {color('danger')}; padding: 20px;")
            self._widget_cache[key] = widget

        layout.addWidget(widget, 1)
        self._current_module = module
        self.stack.setCurrentIndex(1)

    def _close_module(self):
        self._current_module = None
        self.stack.setCurrentIndex(0)

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return

        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()

        new_theme = self.settings.get(
            "theme", "current",
            default=theme_loader.active_style().get("name", "matrix"))
        new_lang = self.settings.get("app", "language", default="en_US")

        theme_changed = new_theme != theme_loader.active_style().get("name")
        lang_changed = new_lang != self.i18n.locale

        if theme_changed:
            try:
                theme_loader.load_style(new_theme)
                if app:
                    app.setStyleSheet(theme_loader.build_qss())
            except Exception as e:
                print(f"[theme] load '{new_theme}' failed: {e}")

        if lang_changed:
            self.i18n.set_locale(new_lang)
        elif theme_changed:
            self._rebuild_ui()

    def closeEvent(self, event):
        if self._bus_dialog is not None:
            self._bus_dialog.close()
            self._bus_dialog = None

        for name in list(self.bus.list_modules()):
            mod = self.bus.get_module(name)
            if mod and hasattr(mod, "on_shutdown"):
                try:
                    mod.on_shutdown()
                except Exception as e:
                    print(f"[shutdown] {name}: {e}")
            self.bus.unregister_module(name)

        super().closeEvent(event)