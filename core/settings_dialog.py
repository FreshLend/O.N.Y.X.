from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QGroupBox, QFormLayout,
    QComboBox
)

from .settings import get_settings
from .i18n import tr, get_i18n
from styles import loader as theme_loader


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings.title"))
        self.setMinimumWidth(520)
        s = get_settings()
        i18n = get_i18n()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.theme_group = QGroupBox(tr("settings.theme"))
        tl = QFormLayout(self.theme_group)
        self.theme_box = QComboBox()
        for name in theme_loader.list_styles():
            self.theme_box.addItem(name)
        self.theme_box.setCurrentText(
            s.get("theme", "current",
                  default=theme_loader.active_style().get("name", "matrix"))
        )
        self.theme_label = tl.addRow(tr("settings.theme") + ":", self.theme_box) or None
        layout.addWidget(self.theme_group)

        self.lang_group = QGroupBox(tr("settings.language"))
        ll = QFormLayout(self.lang_group)
        self.lang_box = QComboBox()
        for name in i18n.available_locales():
            self.lang_box.addItem(name)
        self.lang_box.setCurrentText(i18n.locale)
        ll.addRow(tr("settings.language") + ":", self.lang_box)
        layout.addWidget(self.lang_group)

        layout.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        save = QPushButton(tr("settings.save"))
        save.setFixedWidth(170)
        save.clicked.connect(self._save)
        btns.addWidget(save)
        cancel = QPushButton(tr("settings.cancel"))
        cancel.setFixedWidth(150)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        layout.addLayout(btns)

    def _save(self):
        s = get_settings()
        s.set("theme", "current", value=self.theme_box.currentText())
        s.set("app", "language", value=self.lang_box.currentText())
        self.accept()