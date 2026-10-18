from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
)

from core.brand import (
    APP_NAME, APP_VERSION, APP_EXPANSION,
    APP_AUTHOR, ASCII_LOGO
)
from core.utils import color as _c
from core.i18n import tr


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{tr('about.title')} — {APP_NAME}")
        self.setFixedSize(500, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(10)

        if ASCII_LOGO:
            ascii_logo = QLabel(ASCII_LOGO)
            ascii_logo.setAlignment(Qt.AlignCenter)
            ascii_logo.setFont(QFont("Consolas", 10))
            ascii_logo.setStyleSheet(f"color: {_c('fg')}; background: transparent;")
            layout.addWidget(ascii_logo)

        title = QLabel(APP_NAME)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {_c('fg')}; font-family: Consolas; "
            f"font-size: 22px; font-weight: bold; letter-spacing: 4px;"
        )
        layout.addWidget(title)

        tagline = QLabel(tr("about.tagline"))
        tagline.setAlignment(Qt.AlignCenter)
        tagline.setStyleSheet(
            f"color: {_c('fg_soft')}; font-family: Consolas; font-size: 11px; "
            f"font-style: italic;"
        )
        layout.addWidget(tagline)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {_c('border')};")
        layout.addWidget(sep)

        expansion_title = QLabel(APP_EXPANSION)
        expansion_title.setAlignment(Qt.AlignCenter)
        expansion_title.setStyleSheet(
            f"color: {_c('fg_soft')}; font-family: Consolas; "
            f"font-size: 13px; letter-spacing: 2px;"
        )
        layout.addWidget(expansion_title)

        letters = [
            ("O", "Orchestrated", "modular, working as one swarm"),
            ("N", "Network",      "HTTP, proxies, bypass engines"),
            ("Y", "Yield",        "responses, data, metrics"),
            ("X", "eXtensible",   "plugins and external modules"),
        ]
        letters_box = QVBoxLayout()
        letters_box.setSpacing(4)
        for letter, word, hint in letters:
            row = QHBoxLayout()
            row.setSpacing(10)

            l_lbl = QLabel(letter)
            l_lbl.setFixedWidth(20)
            l_lbl.setStyleSheet(
                f"color: {_c('fg')}; font-family: Consolas; "
                f"font-size: 16px; font-weight: bold;"
            )
            row.addWidget(l_lbl)

            w_lbl = QLabel(word)
            w_lbl.setFixedWidth(140)
            w_lbl.setStyleSheet(
                f"color: {_c('fg')}; font-family: Consolas; font-size: 12px;"
            )
            row.addWidget(w_lbl)

            h_lbl = QLabel(hint)
            h_lbl.setStyleSheet(
                f"color: {_c('fg_dim')}; font-family: Consolas; font-size: 11px;"
            )
            h_lbl.setWordWrap(True)
            row.addWidget(h_lbl, 1)

            letters_box.addLayout(row)

        layout.addLayout(letters_box)

        layout.addStretch()

        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background-color: {_c('border')};")
        layout.addWidget(sep2)

        meta = QLabel(tr("about.meta", version=APP_VERSION, author=APP_AUTHOR))
        meta.setAlignment(Qt.AlignCenter)
        meta.setStyleSheet(
            f"color: {_c('fg_dim')}; font-family: Consolas; font-size: 11px;"
        )
        layout.addWidget(meta)

        btns = QHBoxLayout()
        btns.addStretch()
        close_btn = QPushButton(tr("about.close"))
        close_btn.setFixedWidth(150)
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        btns.addStretch()
        layout.addLayout(btns)
