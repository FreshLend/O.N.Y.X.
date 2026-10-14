import sys
from PySide6.QtCore import QTimer, QByteArray, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

from core.brand import (
    APP_BRAND, APP_NAME, APP_VERSION, APP_EXPANSION, APP_REPO, APP_AUTHOR,
    APP_ICON_SVG, ASCII_LOGO,
)
from core.module_loader import discover_modules
from core.settings import get_settings
from core.splash import SplashScreen
from core.main_window import MainWindow
from core.i18n import get_i18n
from styles import loader as theme_loader


def _build_icon() -> QIcon:
    renderer = QSvgRenderer(QByteArray(APP_ICON_SVG.encode()))
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)


class _AppState:
    window = None


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_AUTHOR)
    app.setWindowIcon(_build_icon())

    s = get_settings()

    lang = s.get("app", "language", default="en_US")
    get_i18n().set_locale(lang)

    theme_name = s.get("theme", "current", default="onyx")
    try:
        theme_loader.load_style(theme_name)
    except Exception:
        theme_loader.load_style("onyx")
    app.setStyleSheet(theme_loader.build_qss())

    print()
    print(ASCII_LOGO)
    print()
    print(f"  {APP_BRAND}  //  v{APP_VERSION}")
    print(f"  {APP_EXPANSION}")
    print(f"  {APP_REPO}")
    print()

    splash = SplashScreen(min_duration_ms=1000)
    splash.show()
    app.processEvents()

    def load_modules():
        def on_progress(msg: str):
            splash.log_line(msg)
            app.processEvents()

        modules = discover_modules("modules", on_progress=on_progress)
        splash.log_line("")
        splash.log_line("[+] init main window...")
        app.processEvents()

        _AppState.window = MainWindow(modules=modules)

        splash.log_line("[+] ready")
        app.processEvents()
        splash.set_loading_done()

    def show_main():
        if _AppState.window is not None:
            _AppState.window.show()

    splash.finished.connect(show_main)
    QTimer.singleShot(100, load_modules)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()