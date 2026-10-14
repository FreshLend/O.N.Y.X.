from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QAbstractItemView,
    QTabWidget, QWidget, QPlainTextEdit
)

from core.module_api import get_bus
from core.i18n import tr


FLUSH_INTERVAL_MS = 120
MAX_PENDING = 5000


class BusDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("bus.title"))
        self.setMinimumSize(820, 520)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self.bus = get_bus()
        self._pending = []
        self._needs_refresh = False

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_services_tab(), tr("bus.tab.services"))
        self.tabs.addTab(self._build_modules_tab(), tr("bus.tab.modules"))
        self.tabs.addTab(self._build_events_tab(), tr("bus.tab.events"))
        layout.addWidget(self.tabs)

        bottom = QHBoxLayout()
        bottom.addStretch()

        clear_btn = QPushButton(tr("bus.clear"))
        clear_btn.setFixedWidth(140)
        clear_btn.clicked.connect(self._clear_all)
        bottom.addWidget(clear_btn)

        refresh = QPushButton(tr("bus.refresh"))
        refresh.setFixedWidth(150)
        refresh.clicked.connect(self.refresh)
        bottom.addWidget(refresh)

        close = QPushButton(tr("bus.close"))
        close.setFixedWidth(140)
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        layout.addLayout(bottom)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(FLUSH_INTERVAL_MS)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_events)

        self.bus.event.connect(self._on_event)
        self.refresh()

    def _build_services_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self.svc_table = QTableWidget()
        self.svc_table.setColumnCount(3)
        self.svc_table.setHorizontalHeaderLabels([
            tr("bus.col.service"),
            tr("bus.col.provider"),
            tr("bus.col.description"),
        ])
        self.svc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.svc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.svc_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.svc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.svc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        v.addWidget(self.svc_table)
        return w

    def _build_modules_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self.mod_table = QTableWidget()
        self.mod_table.setColumnCount(3)
        self.mod_table.setHorizontalHeaderLabels([
            tr("bus.col.module"),
            tr("bus.col.icon"),
            tr("bus.col.description"),
        ])
        self.mod_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.mod_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.mod_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.mod_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mod_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        v.addWidget(self.mod_table)
        return w

    def _build_events_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        top = QHBoxLayout()
        top.addWidget(QLabel(tr("bus.live_events")))
        top.addStretch()
        v.addLayout(top)

        self.event_log = QPlainTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(2000)
        v.addWidget(self.event_log)
        return w

    def refresh(self):
        services = self.bus.list_services()
        self.svc_table.setRowCount(len(services))
        for i, (name, meta) in enumerate(sorted(services.items())):
            self.svc_table.setItem(i, 0, QTableWidgetItem(name))
            self.svc_table.setItem(i, 1, QTableWidgetItem(meta["provider"]))
            self.svc_table.setItem(i, 2, QTableWidgetItem(meta.get("description", "")))

        names = self.bus.list_modules()
        self.mod_table.setRowCount(len(names))
        for i, n in enumerate(names):
            m = self.bus.get_module(n)
            self.mod_table.setItem(i, 0, QTableWidgetItem(m.name))
            self.mod_table.setItem(i, 1, QTableWidgetItem(str(getattr(m, "icon", "?"))))
            self.mod_table.setItem(i, 2, QTableWidgetItem(getattr(m, "description", "")))

    def showEvent(self, event):
        super().showEvent(event)
        self._render_buffer_full()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._pending.clear()
        self._flush_timer.stop()

    def _render_buffer_full(self):
        self.event_log.clear()
        events = self.bus.recent_events()
        if not events:
            return
        lines = [self._format_event(n, p) for n, p in events]
        self.event_log.setPlainText("\n".join(lines))
        sb = self.event_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _format_event(self, name: str, payload: dict) -> str:
        short = ", ".join(f"{k}={v!r}" for k, v in payload.items())
        line = f"[bus] {name}"
        if short:
            line += f"   {short}"
        return line

    def _on_event(self, name: str, payload: dict):
        if not self.isVisible():
            return

        if name in ("service.registered", "service.unregistered",
                    "module.loaded", "module.unloaded"):
            self._needs_refresh = True

        self._pending.append((name, payload))
        if len(self._pending) > MAX_PENDING:
            self._pending = self._pending[-MAX_PENDING:]

        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_events(self):
        if not self._pending:
            return

        batch = self._pending
        self._pending = []

        if self._needs_refresh:
            self._needs_refresh = False
            self.refresh()

        try:
            sb = self.event_log.verticalScrollBar()
            was_at_bottom = sb.value() >= sb.maximum() - 4

            lines = [self._format_event(n, p) for n, p in batch]
            self.event_log.appendPlainText("\n".join(lines))

            if was_at_bottom:
                sb.setValue(sb.maximum())
        except RuntimeError:
            return

    def _clear_all(self):
        self.bus.clear_event_buffer()
        self.event_log.clear()
        self._pending.clear()