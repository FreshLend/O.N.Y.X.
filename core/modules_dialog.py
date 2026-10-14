from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QAbstractItemView,
    QMessageBox, QCheckBox, QWidget
)

from .module_spec import scan_specs
from .module_registry import get_registry
from .i18n import tr


class ModulesDialog(QDialog):
    def __init__(self, parent=None, modules_dir="modules"):
        super().__init__(parent)
        self.setWindowTitle(tr("modules.title"))
        self.setMinimumSize(880, 580)
        self.modules_dir = modules_dir
        self.registry = get_registry()
        self.specs = scan_specs(Path(modules_dir).resolve())
        self._state = {key: self.registry.is_enabled(key) for key in self.specs}
        self._row_keys = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            tr("modules.col.on"),
            tr("modules.col.module"),
            tr("modules.col.version"),
            tr("modules.col.author"),
            tr("modules.col.dependencies"),
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        enable_all = QPushButton(tr("modules.enable_all"))
        enable_all.setFixedWidth(180)
        enable_all.clicked.connect(lambda: self._set_all(True))
        bottom.addWidget(enable_all)
        disable_all = QPushButton(tr("modules.disable_all"))
        disable_all.setFixedWidth(180)
        disable_all.clicked.connect(lambda: self._set_all(False))
        bottom.addWidget(disable_all)
        bottom.addStretch()
        cancel = QPushButton(tr("modules.cancel"))
        cancel.setFixedWidth(150)
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        apply_btn = QPushButton(tr("modules.apply"))
        apply_btn.setFixedWidth(160)
        apply_btn.clicked.connect(self._apply)
        bottom.addWidget(apply_btn)
        layout.addLayout(bottom)

        self._render()

    def _deps_label(self, spec):
        parts = []
        for k, c in spec.dependencies.items():
            parts.append(f"{k} {c}".strip())
        for k, c in spec.optional_dependencies.items():
            parts.append(f"[opt] {k} {c}".strip())
        return "; ".join(parts) if parts else "-"

    def _render(self):
        self.table.setRowCount(0)
        self._row_keys = []
        for key in sorted(self.specs):
            spec = self.specs[key]
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._row_keys.append(key)

            cb = QCheckBox()
            cb.setChecked(self._state.get(key, True))
            cb.stateChanged.connect(
                lambda s, k=key: self._on_toggle(k, s == Qt.Checked.value))
            wrap = QWidget()
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.addWidget(cb)
            wl.setAlignment(Qt.AlignCenter)
            self.table.setCellWidget(row, 0, wrap)

            self.table.setItem(row, 1, QTableWidgetItem(spec.name))
            self.table.setItem(row, 2, QTableWidgetItem(spec.version))
            self.table.setItem(row, 3, QTableWidgetItem(spec.author))
            self.table.setItem(row, 4, QTableWidgetItem(self._deps_label(spec)))

    def _on_toggle(self, key, on):
        if on:
            self._enable_with_deps(key, set())
        else:
            self._disable_with_dependents(key, set())
        self._sync_checkboxes()

    def _enable_with_deps(self, key, seen):
        if key in seen:
            return
        seen.add(key)
        self._state[key] = True
        spec = self.specs.get(key)
        if spec is None:
            return
        for dep_key in spec.dependencies:
            self._enable_with_deps(dep_key, seen)

    def _disable_with_dependents(self, key, seen):
        if key in seen:
            return
        seen.add(key)
        self._state[key] = False
        for other_key, spec in self.specs.items():
            if key in spec.dependencies:
                self._disable_with_dependents(other_key, seen)

    def _sync_checkboxes(self):
        for row, key in enumerate(self._row_keys):
            wrap = self.table.cellWidget(row, 0)
            if not wrap:
                continue
            cb = wrap.findChild(QCheckBox)
            if cb is None:
                continue
            cb.blockSignals(True)
            cb.setChecked(self._state.get(key, True))
            cb.blockSignals(False)

    def _set_all(self, value):
        for key in self.specs:
            self._state[key] = value
        self._sync_checkboxes()

    def _apply(self):
        for key, spec in self.specs.items():
            if not self._state.get(key):
                continue
            for dep_key in spec.dependencies:
                if not self._state.get(dep_key):
                    dep_name = self.specs.get(dep_key)
                    dep_name = dep_name.name if dep_name else dep_key
                    QMessageBox.warning(
                        self, tr("modules.invalid_title"),
                        tr("modules.invalid_message",
                           module=spec.name, dependency=dep_name))
                    return
        self.registry.set_many(self._state)
        self.accept()