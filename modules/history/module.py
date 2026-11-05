import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import (
    QObject, QThread, Signal, Slot, Qt, QAbstractTableModel, QModelIndex,
)
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableView, QHeaderView, QAbstractItemView, QMessageBox,
)

from core.base_module import BaseModule
from core.i18n import tr
from core.utils import color as _c, parent_widget


HISTORY_DB = Path(__file__).resolve().parent.parent.parent / "config" / "history.db"


def T(key, **kw):
    return tr(key, module="history", **kw)


class HistoryStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = None

    def _ensure_db(self):
        if self._conn is not None:
            return
        with self._lock:
            if self._conn is not None:
                return
            HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(HISTORY_DB), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS requests ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "module TEXT NOT NULL,"
                "session TEXT,"
                "method TEXT,"
                "url TEXT,"
                "headers TEXT,"
                "body TEXT,"
                "status INTEGER,"
                "response_body TEXT,"
                "elapsed REAL,"
                "error TEXT,"
                "created_at TEXT)"
            )
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(requests)")
            if "session" not in [r[1] for r in cur.fetchall()]:
                conn.execute("ALTER TABLE requests ADD COLUMN session TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_module ON requests(module)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_at)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_module_session "
                "ON requests(module, session)")
            conn.commit()
            self._conn = conn

    def close(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def add(self, *, module, method, url, headers, body,
            session=None, status=None, response_body=None,
            elapsed=None, error=None):
        self._ensure_db()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO requests "
                "(module, session, method, url, headers, body, "
                "status, response_body, elapsed, error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    module, session, method, url,
                    json.dumps(headers, ensure_ascii=False)
                    if isinstance(headers, dict) else headers,
                    body, status, response_body, elapsed, error,
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    def list(self, module=None, session=None, limit=300, since=None):
        self._ensure_db()
        with self._lock:
            cur = self._conn.cursor()
            sql = ("SELECT id, module, session, method, url, status, "
                   "elapsed, error, created_at FROM requests WHERE 1=1")
            params = []
            if module:
                sql += " AND module=?"
                params.append(module)
            if session:
                sql += " AND session=?"
                params.append(session)
            if since:
                sql += " AND created_at>=?"
                params.append(since)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            keys = ["id", "module", "session", "method", "url",
                    "status", "elapsed", "error", "created_at"]
            return [dict(zip(keys, r)) for r in cur.fetchall()]

    def fetch(self, module=None, session=None, limit=300, since=None):
        self._ensure_db()
        with self._lock:
            cur = self._conn.cursor()
            sql = "SELECT * FROM requests WHERE 1=1"
            params = []
            if module:
                sql += " AND module=?"
                params.append(module)
            if session:
                sql += " AND session=?"
                params.append(session)
            if since:
                sql += " AND created_at>=?"
                params.append(since)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def get(self, row_id):
        self._ensure_db()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM requests WHERE id=?", (row_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    def count(self, module=None, session=None, since=None):
        self._ensure_db()
        with self._lock:
            cur = self._conn.cursor()
            sql = "SELECT COUNT(*) FROM requests WHERE 1=1"
            params = []
            if module:
                sql += " AND module=?"
                params.append(module)
            if session:
                sql += " AND session=?"
                params.append(session)
            if since:
                sql += " AND created_at>=?"
                params.append(since)
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def clear(self, module=None, session=None):
        self._ensure_db()
        with self._lock:
            if module and session:
                self._conn.execute(
                    "DELETE FROM requests WHERE module=? AND session=?",
                    (module, session))
            elif module:
                self._conn.execute(
                    "DELETE FROM requests WHERE module=?", (module,))
            elif session:
                self._conn.execute(
                    "DELETE FROM requests WHERE session=?", (session,))
            else:
                self._conn.execute("DELETE FROM requests")
            self._conn.commit()

    def delete_ids(self, ids):
        ids = list(ids)
        if not ids:
            return 0
        self._ensure_db()
        with self._lock:
            self._conn.executemany(
                "DELETE FROM requests WHERE id=?", [(i,) for i in ids])
            self._conn.commit()
        return len(ids)

    def modules(self):
        self._ensure_db()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT DISTINCT module FROM requests ORDER BY module")
            return [r[0] for r in cur.fetchall()]

    def sessions(self, module=None, limit=50):
        self._ensure_db()
        with self._lock:
            cur = self._conn.cursor()
            if module:
                cur.execute(
                    "SELECT session, MAX(id) AS mx FROM requests "
                    "WHERE module=? AND session IS NOT NULL AND session!='' "
                    "GROUP BY session ORDER BY mx DESC LIMIT ?",
                    (module, limit))
            else:
                cur.execute(
                    "SELECT session, MAX(id) AS mx FROM requests "
                    "WHERE session IS NOT NULL AND session!='' "
                    "GROUP BY session ORDER BY mx DESC LIMIT ?",
                    (limit,))
            return [r[0] for r in cur.fetchall()]


class HistoryTableModel(QAbstractTableModel):
    COLUMNS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._headers = [""] * self.COLUMNS

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def set_headers(self, headers):
        self._headers = list(headers)
        self.headerDataChanged.emit(Qt.Horizontal, 0, self.COLUMNS - 1)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.COLUMNS

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        try:
            r = self._rows[index.row()]
        except IndexError:
            return None
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return str(r.get("id", ""))
            if col == 1:
                return (r.get("created_at") or "")[:19].replace("T", " ")
            if col == 2:
                return r.get("module") or ""
            if col == 3:
                return r.get("session") or ""
            if col == 4:
                return r.get("method") or ""
            if col == 5:
                return r.get("url") or ""
            if col == 6:
                s = r.get("status")
                return str(s) if s is not None else (
                    "ERR" if r.get("error") else "-")
            if col == 7:
                ms = r.get("elapsed")
                return f"{ms*1000:.0f}" if ms else "-"

        if role == Qt.ForegroundRole and col == 6:
            s = r.get("status")
            if s is not None:
                if 200 <= s < 300:
                    return QColor(_c("success"))
                if 300 <= s < 400:
                    return QColor(_c("warning"))
                return QColor(_c("danger"))
            if r.get("error"):
                return QColor(_c("danger"))

        if role == Qt.UserRole:
            return r

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            if 0 <= section < len(self._headers):
                return self._headers[section]
        return None

    def row_at(self, row):
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def row_id(self, row):
        r = self.row_at(row)
        return r["id"] if r else None


class HistoryReceiver(QObject):
    def __init__(self, module):
        super().__init__()
        self._module = module

    @Slot(int, list, list, list)
    def on_loaded(self, seq, rows, modules, sessions):
        self._module._on_loaded(seq, rows, modules, sessions)

    @Slot(int, str)
    def on_error(self, seq, error):
        self._module._on_load_error(seq, error)


class HistoryLoadWorker(QThread):
    finished_rows = Signal(int, list, list, list)
    error = Signal(int, str)

    def __init__(self, seq, store, module, session, limit, parent=None):
        super().__init__(parent)
        self._seq = seq
        self._store = store
        self._module = module
        self._session = session
        self._limit = limit

    def run(self):
        try:
            modules = self._store.modules()
            sessions = self._store.sessions(module=self._module)
            rows = self._store.list(
                module=self._module, session=self._session,
                limit=self._limit)
        except Exception as e:
            self.error.emit(self._seq, str(e))
            return
        self.finished_rows.emit(self._seq, rows, modules, sessions)


class HistoryModule(BaseModule):
    ROW_LIMIT = 1000

    def __init__(self):
        self._store = None
        self._store_lock = threading.Lock()
        self._widget = None
        self._lbls = {}
        self._btns = {}
        self._worker = None
        self._receiver = HistoryReceiver(self)
        self._loading = False
        self._reload_queued = False
        self._load_seq = 0
        self._last_rows = []
        self._shutting_down = False
        self.model = None
        self.table = None

    def _get_store(self):
        if self._store is None:
            with self._store_lock:
                if self._store is None:
                    self._store = HistoryStore()
        return self._store

    def on_load(self, api):
        api.register_service(self.name, "history.available", lambda: True,
                             "history.available() -> bool")
        api.register_service(self.name, "history.add", self._svc_add,
                             "history.add(module, method, url, headers, body, "
                             "session=None, status=None, response_body=None, "
                             "elapsed=None, error=None) -> int|None")
        api.register_service(self.name, "history.list", self._svc_list,
                             "history.list(module=None, session=None, "
                             "limit=300, since=None) -> list[dict]")
        api.register_service(self.name, "history.fetch", self._svc_fetch,
                             "history.fetch(module=None, session=None, "
                             "limit=300, since=None) -> list[dict]")
        api.register_service(self.name, "history.get", self._svc_get,
                             "history.get(id) -> dict|None")
        api.register_service(self.name, "history.clear", self._svc_clear,
                             "history.clear(module=None, session=None)")
        api.register_service(self.name, "history.delete_ids",
                             self._svc_delete_ids,
                             "history.delete_ids(ids) -> int")
        api.register_service(self.name, "history.count", self._svc_count,
                             "history.count(module=None, session=None, "
                             "since=None) -> int")
        api.register_service(self.name, "history.modules", self._svc_modules,
                             "history.modules() -> list[str]")
        api.register_service(self.name, "history.sessions", self._svc_sessions,
                             "history.sessions(module=None, limit=50) -> list[str]")

    def on_unload(self, api):
        self._shutdown()
        if self._store is not None:
            self._store.close()
            self._store = None

    def on_shutdown(self):
        self._shutdown()
        if self._store is not None:
            self._store.close()
            self._store = None

    def _shutdown(self):
        self._shutting_down = True
        w = self._worker
        self._worker = None
        if w is not None:
            if w.isRunning():
                try:
                    w.wait(3000)
                except Exception:
                    pass
            try:
                w.deleteLater()
            except Exception:
                pass

    def _svc_add(self, **kwargs):
        try:
            return self._get_store().add(**kwargs)
        except Exception as e:
            print(f"[history] add failed: {e}")
            return None

    def _svc_list(self, **kwargs):
        try:
            return self._get_store().list(**kwargs)
        except Exception as e:
            print(f"[history] list failed: {e}")
            return []

    def _svc_fetch(self, **kwargs):
        try:
            return self._get_store().fetch(**kwargs)
        except Exception as e:
            print(f"[history] fetch failed: {e}")
            return []

    def _svc_get(self, row_id):
        try:
            return self._get_store().get(row_id)
        except Exception as e:
            print(f"[history] get failed: {e}")
            return None

    def _svc_clear(self, module=None, session=None):
        try:
            self._get_store().clear(module=module, session=session)
        except Exception as e:
            print(f"[history] clear failed: {e}")

    def _svc_delete_ids(self, ids):
        try:
            return self._get_store().delete_ids(ids)
        except Exception as e:
            print(f"[history] delete_ids failed: {e}")
            return 0

    def _svc_count(self, **kwargs):
        try:
            return self._get_store().count(**kwargs)
        except Exception as e:
            print(f"[history] count failed: {e}")
            return 0

    def _svc_modules(self):
        try:
            return self._get_store().modules()
        except Exception:
            return []

    def _svc_sessions(self, module=None, limit=50):
        try:
            return self._get_store().sessions(module=module, limit=limit)
        except Exception:
            return []

    def create_widget(self) -> QWidget:
        root = QWidget()
        self._widget = root

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel(T("title"))
        title.setStyleSheet(
            f"color: {_c('fg')}; font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        hint = QLabel(T("hint"))
        hint.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(hint)

        row = QHBoxLayout()

        self._lbls["module"] = QLabel(T("filter.module"))
        self._lbls["module"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self._lbls["module"])

        self.module_box = QComboBox()
        self.module_box.setFixedWidth(200)
        self.module_box.currentTextChanged.connect(
            lambda _: self._start_load())
        row.addWidget(self.module_box)

        self._lbls["session"] = QLabel(T("filter.session"))
        self._lbls["session"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self._lbls["session"])

        self.session_box = QComboBox()
        self.session_box.setFixedWidth(220)
        self.session_box.currentTextChanged.connect(
            lambda _: self._start_load())
        row.addWidget(self.session_box)

        self._lbls["search"] = QLabel(T("filter.search"))
        self._lbls["search"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self._lbls["search"])

        self.search_input = QLineEdit()
        self.search_input.setFont(QFont("Consolas", 10))
        self.search_input.setPlaceholderText(T("filter.search.placeholder"))
        self.search_input.textChanged.connect(
            lambda _: self._apply_rows(self._last_rows))
        row.addWidget(self.search_input, 1)

        self._btns["refresh"] = QPushButton(T("refresh"))
        self._btns["refresh"].setFixedWidth(140)
        self._btns["refresh"].clicked.connect(self._start_load)
        row.addWidget(self._btns["refresh"])

        layout.addLayout(row)

        self.model = HistoryTableModel(root)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)

        h = self.table.horizontalHeader()
        for col in (0, 1, 2, 3, 4, 6, 7):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 160)
        self.table.setColumnWidth(4, 80)
        self.table.setColumnWidth(6, 80)
        self.table.setColumnWidth(7, 70)

        self.table.doubleClicked.connect(self._show_detail)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()

        self.status = QLabel(T("status.total", count=0))
        self.status.setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        bottom.addWidget(self.status)
        bottom.addStretch()

        self._btns["clear_filtered"] = QPushButton(T("clear_filtered"))
        self._btns["clear_filtered"].setFixedWidth(240)
        self._btns["clear_filtered"].clicked.connect(self._clear_filtered)
        bottom.addWidget(self._btns["clear_filtered"])

        self._btns["delete_selected"] = QPushButton(T("delete_selected"))
        self._btns["delete_selected"].setFixedWidth(220)
        self._btns["delete_selected"].clicked.connect(self._delete_selected)
        bottom.addWidget(self._btns["delete_selected"])

        self._btns["clear_all"] = QPushButton(T("clear_all"))
        self._btns["clear_all"].setFixedWidth(180)
        self._btns["clear_all"].clicked.connect(self._clear_all)
        bottom.addWidget(self._btns["clear_all"])

        layout.addLayout(bottom)

        self.retranslate()
        return root

    def retranslate(self):
        if self.model is None:
            return
        self._lbls["module"].setText(T("filter.module"))
        self._lbls["session"].setText(T("filter.session"))
        self._lbls["search"].setText(T("filter.search"))
        self.search_input.setPlaceholderText(T("filter.search.placeholder"))
        self._btns["refresh"].setText(T("refresh"))
        self._btns["clear_filtered"].setText(T("clear_filtered"))
        self._btns["delete_selected"].setText(T("delete_selected"))
        self._btns["clear_all"].setText(T("clear_all"))
        self.model.set_headers([
            T("col.id"), T("col.time"), T("col.module"), T("col.session"),
            T("col.method"), T("col.url"), T("col.status"), T("col.elapsed"),
        ])
        self._start_load()

    def _selected_module(self):
        v = self.module_box.currentText()
        return None if v in ("", T("filter.all")) else v

    def _selected_session(self):
        v = self.session_box.currentText()
        return None if v in ("", T("filter.all")) else v

    def _set_loading(self, loading: bool):
        self._loading = loading
        try:
            self._btns["refresh"].setEnabled(not loading)
            if loading:
                self.status.setText(T("status.loading"))
                self.status.setStyleSheet(
                    f"color: {_c('warning')}; font-size: 11px;")
        except RuntimeError:
            return

    def _start_load(self):
        if self._widget is None or self._shutting_down:
            return
        if self._loading:
            self._reload_queued = True
            return

        self._load_seq += 1
        seq = self._load_seq
        module = self._selected_module()
        session = self._selected_session()
        store = self._get_store()

        self._set_loading(True)

        w = HistoryLoadWorker(seq, store, module, session, self.ROW_LIMIT)
        w.finished_rows.connect(self._receiver.on_loaded)
        w.error.connect(self._receiver.on_error)
        self._worker = w
        w.start()

    def _on_loaded(self, seq, rows, modules, sessions):
        w = self._worker
        self._worker = None
        if w is not None:
            try:
                w.deleteLater()
            except Exception:
                pass

        if self._shutting_down or self._widget is None:
            return
        if seq != self._load_seq:
            return

        self._set_loading(False)
        self._apply_filters(modules, sessions)
        self._last_rows = rows
        self._apply_rows(rows)

        if self._reload_queued:
            self._reload_queued = False
            self._start_load()

    def _on_load_error(self, seq, error):
        w = self._worker
        self._worker = None
        if w is not None:
            try:
                w.deleteLater()
            except Exception:
                pass

        if self._shutting_down or self._widget is None:
            return
        if seq != self._load_seq:
            return

        self._set_loading(False)
        try:
            self.status.setText(T("status.unavailable") + f": {error}")
            self.status.setStyleSheet(
                f"color: {_c('danger')}; font-size: 11px;")
        except RuntimeError:
            return

        if self._reload_queued:
            self._reload_queued = False
            self._start_load()

    def _apply_filters(self, modules, sessions):
        try:
            cur_m = self.module_box.currentText()
            self.module_box.blockSignals(True)
            self.module_box.clear()
            self.module_box.addItem(T("filter.all"))
            for m in modules:
                self.module_box.addItem(m)
            idx = self.module_box.findText(cur_m)
            if idx >= 0:
                self.module_box.setCurrentIndex(idx)
            self.module_box.blockSignals(False)

            cur_s = self.session_box.currentText()
            self.session_box.blockSignals(True)
            self.session_box.clear()
            self.session_box.addItem(T("filter.all"))
            for s in sessions:
                self.session_box.addItem(s)
            idx = self.session_box.findText(cur_s)
            if idx >= 0:
                self.session_box.setCurrentIndex(idx)
            self.session_box.blockSignals(False)
        except RuntimeError:
            return

    def _apply_rows(self, rows):
        if self.model is None:
            return
        try:
            needle = self.search_input.text().strip().lower()
            if needle:
                visible = []
                for r in rows:
                    haystack = " ".join(str(r.get(k) or "") for k in
                                        ("url", "method", "error")).lower()
                    if needle in haystack:
                        visible.append(r)
            else:
                visible = rows

            self.model.set_rows(visible)
            self.status.setText(T("status.total", count=len(visible)))
            self.status.setStyleSheet(
                f"color: {_c('fg_dim')}; font-size: 11px;")
        except RuntimeError:
            return

    def _selected_ids(self):
        ids = []
        try:
            sel = self.table.selectionModel().selectedRows()
        except Exception:
            return ids
        for index in sel:
            rid = self.model.row_id(index.row())
            if rid is not None:
                ids.append(rid)
        return ids

    def _show_detail(self, *_):
        ids = self._selected_ids()
        if not ids:
            return
        rec = self._svc_get(ids[0])
        if not rec:
            return
        lines = [
            f"id: {rec['id']}",
            f"time: {rec['created_at']}",
            f"module: {rec.get('module')}",
            f"session: {rec.get('session') or '-'}",
            f"method: {rec.get('method')}",
            f"url: {rec.get('url')}",
            f"status: {rec.get('status')}",
            f"elapsed: {(rec.get('elapsed') or 0)*1000:.0f} ms",
            f"error: {rec.get('error') or '-'}",
            "",
            "HEADERS:",
            rec.get("headers") or "",
            "",
            "BODY:",
            (rec.get("body") or "")[:2000],
            "",
            "RESPONSE:",
            (rec.get("response_body") or "")[:4000],
        ]
        QMessageBox.information(parent_widget(self._widget),
                                T("dialog.detail.title"), "\n".join(lines))

    def _delete_selected(self):
        ids = self._selected_ids()
        if not ids:
            return
        if QMessageBox.question(
            parent_widget(self._widget),
            T("confirm.delete_selected.title"),
            T("confirm.delete_selected.text", count=len(ids)),
        ) != QMessageBox.Yes:
            return
        self._svc_delete_ids(ids)
        self._start_load()

    def _clear_filtered(self):
        module = self._selected_module()
        session = self._selected_session()
        if not module and not session:
            QMessageBox.information(
                parent_widget(self._widget),
                T("confirm.clear_filtered.none.title"),
                T("confirm.clear_filtered.none.text"))
            return
        n = self._svc_count(module=module, session=session)
        if n == 0:
            return
        if QMessageBox.question(
            parent_widget(self._widget),
            T("confirm.clear_filtered.title"),
            T("confirm.clear_filtered.text", count=n),
        ) != QMessageBox.Yes:
            return
        self._svc_clear(module=module, session=session)
        self._start_load()

    def _clear_all(self):
        n = self._svc_count()
        if n == 0:
            return
        if QMessageBox.question(
            parent_widget(self._widget),
            T("confirm.clear_all.title"),
            T("confirm.clear_all.text"),
        ) != QMessageBox.Yes:
            return
        self._svc_clear()
        self._start_load()