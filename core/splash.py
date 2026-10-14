from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QProgressBar, QPlainTextEdit
)

from core.brand import APP_NAME, APP_EXPANSION, APP_REPO, ASCII_LOGO
from core.utils import color as _c


ASCII_SWARM = ASCII_LOGO


class SplashScreen(QWidget):
    finished = Signal()

    def __init__(self, min_duration_ms: int = 1000):
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setFixedSize(820, 580)
        self.setCursor(Qt.PointingHandCursor)

        self.setObjectName("splashRoot")
        self.setStyleSheet(
            f"#splashRoot {{"
            f"  background-color: {_c('bg2')};"
            f"  border: 1px solid {_c('border_hi')};"
            f"}}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 24)
        layout.setSpacing(8)

        self.ascii_label = QLabel("")
        self.ascii_label.setAlignment(Qt.AlignCenter)
        self.ascii_label.setFont(QFont("Consolas", 10))
        self.ascii_label.setStyleSheet(
            f"color: {_c('fg')}; background: transparent;"
        )
        layout.addWidget(self.ascii_label)

        self.title_label = QLabel("")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet(
            f"color: {_c('fg')}; background: transparent; "
            f"font-family: Consolas; font-size: 26px; font-weight: bold; "
            f"letter-spacing: 8px;"
        )
        layout.addWidget(self.title_label)

        self.exp_label = QLabel("")
        self.exp_label.setAlignment(Qt.AlignCenter)
        self.exp_label.setStyleSheet(
            f"color: {_c('fg_dim')}; background: transparent; "
            f"font-family: Consolas; font-size: 11px; letter-spacing: 2px;"
        )
        layout.addWidget(self.exp_label)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {_c('border')};")
        layout.addWidget(sep)

        self.log_widget = QPlainTextEdit()
        self.log_widget.setFont(QFont("Consolas", 9))
        self.log_widget.setReadOnly(True)
        self.log_widget.setFrameShape(QFrame.NoFrame)
        self.log_widget.setStyleSheet(
            f"background: {_c('bg')}; "
            f"color: {_c('fg_dim')}; "
            f"border: none; "
            f"padding: 6px;"
        )
        layout.addWidget(self.log_widget, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        repo_lbl = QLabel(APP_REPO)
        repo_lbl.setStyleSheet(
            f"color: {_c('fg_dark')}; background: transparent; "
            f"font-family: Consolas; font-size: 10px;"
        )
        footer.addWidget(repo_lbl)
        footer.addStretch()
        layout.addLayout(footer)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.setStyleSheet(
            f"QProgressBar {{ border: none; background: {_c('bg3')}; }}"
            f"QProgressBar::chunk {{ background: {_c('fg')}; }}"
        )
        layout.addWidget(self.progress)

        self._ascii_full = ASCII_SWARM
        self._ascii_pos = 0
        self._phase = "ascii"
        self._elapsed = 0
        self._title_shown_at = 0
        self._hold_duration = min_duration_ms
        self._hold_complete = False
        self._loading_done = False
        self._ascii_step = 4

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(12)

        self._center_on_screen()

    def log_line(self, text: str):
        self.log_widget.appendPlainText(text)
        sb = self.log_widget.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_loading_done(self):
        self._loading_done = True

    def _center_on_screen(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2,
        )

    def _revealed_ascii(self):
        out = []
        for i, ch in enumerate(self._ascii_full):
            if ch == "\n" or i < self._ascii_pos:
                out.append(ch)
            else:
                out.append(" ")
        return "".join(out)

    def _tick(self):
        self._elapsed += 12

        if self._phase == "ascii":
            self._ascii_pos += self._ascii_step
            if self._ascii_pos >= len(self._ascii_full):
                self._ascii_pos = len(self._ascii_full)
                self.ascii_label.setText(self._ascii_full)
                self.title_label.setText(APP_NAME)
                self.exp_label.setText(APP_EXPANSION)
                self._title_shown_at = self._elapsed
                self._phase = "wait"
            else:
                self.ascii_label.setText(self._revealed_ascii())

            self.progress.setValue(int(30 * self._ascii_pos / len(self._ascii_full)))

        elif self._phase == "wait":
            waited = self._elapsed - self._title_shown_at

            if waited >= self._hold_duration:
                self._hold_complete = True

            if not self._hold_complete:
                p = min(1.0, waited / max(1, self._hold_duration))
                self.progress.setValue(30 + int(60 * p))
            else:
                self.progress.setValue(90)

            if self._hold_complete and self._loading_done:
                self._phase = "done"

        elif self._phase == "done":
            self._timer.stop()
            self.progress.setValue(100)
            QTimer.singleShot(200, self._finish)

    def _finish(self):
        self.finished.emit()
        self.close()

    def mousePressEvent(self, event):
        self._timer.stop()
        self.ascii_label.setText(self._ascii_full)
        self.title_label.setText(APP_NAME)
        self.exp_label.setText(APP_EXPANSION)
        self.progress.setValue(100)
        if self._loading_done:
            QTimer.singleShot(80, self._finish)
        else:
            self._hold_complete = True
            self._phase = "wait"