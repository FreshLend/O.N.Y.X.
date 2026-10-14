import json
import time
import threading
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QSyntaxHighlighter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QComboBox, QListWidget, QListWidgetItem, QInputDialog,
    QMessageBox, QSplitter, QFileDialog, QCheckBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QFormLayout, QTabWidget, QDialog,
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.i18n import tr
from core.utils import (
    color as _c, parent_widget, parse_headers, extract_by_path,
    parse_int_list, coerce_value,
)
from styles import loader as theme_loader


CONFIG_ROOT = Path(__file__).resolve().parent.parent.parent / "config"


def T(key, **kwargs):
    return tr(key, module="collections", **kwargs)


class JsonHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.key_fmt = QTextCharFormat()
        self.key_fmt.setForeground(QColor(_c("fg_soft", "#66a8d8")))
        self.str_fmt = QTextCharFormat()
        self.str_fmt.setForeground(QColor(_c("success", "#66d9a0")))
        self.num_fmt = QTextCharFormat()
        self.num_fmt.setForeground(QColor(_c("warning", "#ffb84d")))
        self.kw_fmt = QTextCharFormat()
        self.kw_fmt.setForeground(QColor(_c("danger", "#ff4d6d")))

    def highlightBlock(self, text):
        import re
        s = text
        for m in re.finditer(r'"[^"\\]*(?:\\.[^"\\]*)*"\s*:', s):
            self.setFormat(m.start(), m.end() - m.start(), self.key_fmt)
        for m in re.finditer(r':\s*"([^"\\]|\\.)*"', s):
            self.setFormat(m.start() + 1, m.end() - m.start() - 1, self.str_fmt)
        for m in re.finditer(r'\b(-?\d+\.?\d*([eE][+-]?\d+)?)\b', s):
            self.setFormat(m.start(), m.end() - m.start(), self.num_fmt)
        for m in re.finditer(r'\b(true|false|null)\b', s):
            self.setFormat(m.start(), m.end() - m.start(), self.kw_fmt)


class CollectionRunWorker(QThread):
    step = Signal(int, int, dict)
    done = Signal(list, str)

    def __init__(self, exec_fn, name, coll, caller, session,
                 stop_on_error, parallel):
        super().__init__()
        self._exec_fn = exec_fn
        self._name = name
        self._coll = coll
        self._caller = caller
        self._session = session
        self._stop_on_error = stop_on_error
        self._parallel = parallel

    def run(self):
        def on_progress(idx, total, result):
            try:
                self.step.emit(idx, total, result)
            except RuntimeError:
                pass

        try:
            results, session = self._exec_fn(
                self._name, self._coll, self._caller, self._session,
                self._stop_on_error, self._parallel, on_progress,
            )
        except Exception as e:
            import traceback
            results = [{
                "name": "(runner)", "url": "", "method": "",
                "ok": False, "assert_ok": False,
                "error": f"{e}\n{traceback.format_exc()}",
            }]
            session = self._session
        self.done.emit(results, session)


class SingleRunWorker(QThread):
    done = Signal(dict)

    def __init__(self, exec_fn, req, vars_map, caller, session, auth_config):
        super().__init__()
        self._exec_fn = exec_fn
        self._req = req
        self._vars_map = vars_map
        self._caller = caller
        self._session = session
        self._auth_config = auth_config

    def run(self):
        try:
            result = self._exec_fn(self._req, None, self._vars_map,
                                    self._caller, self._session,
                                    self._auth_config)
        except Exception as e:
            result = {
                "name": self._req.get("name"),
                "url": self._req.get("url"),
                "ok": False, "assert_ok": False, "error": str(e),
            }
        self.done.emit(result)


class DataDrivenRunWorker(QThread):
    step = Signal(int, int, dict)
    done = Signal(list, str)

    def __init__(self, owner, name, coll, rows, caller, session):
        super().__init__()
        self._owner = owner
        self._name = name
        self._coll = coll
        self._rows = rows
        self._caller = caller
        self._session = session

    def run(self):
        results = []
        reqs = self._coll.get("requests") or []
        session_name = self._owner._session_name(self._name)
        total = len(self._rows) * max(1, len(reqs))
        idx = 0
        try:
            for row in self._rows:
                vars_map = dict(self._owner._resolve_vars(self._name))
                for k, v in row.items():
                    vars_map[str(k)] = v
                for req in reqs:
                    idx += 1
                    try:
                        res = self._owner._execute_one(
                            req, None, vars_map, self._caller,
                            self._session, self._coll.get("auth") or {},
                            session_name)
                    except Exception as e:
                        res = {"name": req.get("name"), "url": req.get("url"),
                               "ok": False, "assert_ok": False, "error": str(e)}
                    res["_row"] = row
                    results.append(res)
                    try:
                        self.step.emit(idx - 1, total, res)
                    except RuntimeError:
                        pass
        finally:
            try:
                self._owner._close_all_ws()
            except Exception:
                pass
        self.done.emit(results, self._session)


class CollectionsModule(BaseModule):
    STORE = CONFIG_ROOT / "collections"
    RUNS_FILE = CONFIG_ROOT / "collections" / "_runs.json"
    TEMPLATES_FILE = CONFIG_ROOT / "collections" / "_templates.json"

    def __init__(self):
        self._widget = None
        self._collections = {}
        self._active_name = None
        self._active_idx = -1
        self._last_run = {}
        self._last_results = []
        self._runner = None
        self._single = None
        self._datadriven = None
        self._running_name = None
        self._labels = {}
        self._buttons = {}
        self._checks = {}
        self._tabs = None
        self._table = None
        self._list = None
        self._log = None
        self._reauth_lock = threading.RLock()
        self._reauth_in_progress = False
        self._ws_lock = threading.RLock()
        self._ws_connections = {}

    def on_load(self, api):
        self._load_all()
        self._ensure_templates()
        api.register_service(self.name, "collections.run", self._svc_run,
                             "collections.run(name, caller=None, session=None, "
                             "stop_on_error=None) -> list")
        api.register_service(self.name, "collections.save", self._svc_save,
                             "collections.save(name, requests, vars=None, "
                             "parallel=False, stop_on_error=False, "
                             "environments=None, active_env=None, auth=None) -> bool")
        api.register_service(self.name, "collections.delete", self._svc_delete,
                             "collections.delete(name) -> bool")
        api.register_service(self.name, "collections.list", self._svc_list,
                             "collections.list() -> list[str]")

    def on_shutdown(self):
        for w in (getattr(self, "_runner", None),
                  getattr(self, "_single", None),
                  getattr(self, "_datadriven", None)):
            if w is not None:
                try:
                    w.wait(5000)
                except Exception:
                    pass
        self._runner = None
        self._single = None
        self._datadriven = None
        self._close_all_ws()

    def _close_all_ws(self):
        with self._ws_lock:
            ids = list(self._ws_connections.values())
            self._ws_connections.clear()
        bus = get_bus()
        for cid in ids:
            try:
                bus.call("ws.close", cid)
            except Exception:
                pass

    def _svc_list(self):
        return sorted(self._collections.keys())

    def _svc_save(self, name, requests, vars=None,
                  parallel=False, stop_on_error=False,
                  environments=None, active_env=None, auth=None):
        existing = self._collections.get(name) or {}
        existing["requests"] = requests
        if vars is not None:
            existing["vars"] = vars
        if environments is not None:
            existing["environments"] = environments
        if active_env is not None:
            existing["active_env"] = active_env
        if auth is not None:
            existing["auth"] = auth
        existing["parallel"] = bool(parallel)
        existing["stop_on_error"] = bool(stop_on_error)
        existing.setdefault("environments", {})
        existing.setdefault("active_env", "")
        existing.setdefault("vars", {})
        existing.setdefault("auth", {})
        self._collections[name] = existing
        self._save_collection(name)
        if hasattr(self, "collection_box"):
            self._refresh_collections()
        return True

    def _svc_delete(self, name):
        existed = name in self._collections
        self._collections.pop(name, None)
        path = self.STORE / f"{name}.json"
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
        if self._active_name == name:
            self._active_name = None
            self._active_idx = -1
        if hasattr(self, "collection_box"):
            self._refresh_collections()
        return existed

    def _svc_run(self, name, caller=None, session=None, stop_on_error=None):
        if name not in self._collections:
            return [{"ok": False, "error": f"collection '{name}' not found"}]
        coll = self._collections[name]
        if stop_on_error is None:
            stop_on_error = coll.get("stop_on_error", False)
        results, _session = self._execute_collection(
            name, coll, caller=caller, session=session,
            stop_on_error=stop_on_error,
            parallel=coll.get("parallel", False),
            on_progress=None,
        )
        return results

    def _load_all(self):
        self.STORE.mkdir(parents=True, exist_ok=True)
        self._collections = {}
        for fname in sorted(self.STORE.iterdir()):
            if fname.suffix == ".json" and not fname.name.startswith("_"):
                try:
                    with open(fname, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data.setdefault("requests", [])
                    data.setdefault("vars", {})
                    data.setdefault("environments", {})
                    data.setdefault("active_env", "")
                    data.setdefault("stop_on_error", False)
                    data.setdefault("parallel", False)
                    data.setdefault("parallel_workers", 5)
                    data.setdefault("session_name", "")
                    data.setdefault("auth", {})
                    self._collections[fname.stem] = data
                except Exception:
                    continue

    def _save_collection(self, name):
        if name not in self._collections:
            return
        self.STORE.mkdir(parents=True, exist_ok=True)
        with open(self.STORE / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(self._collections[name], f, ensure_ascii=False, indent=2)

    def _append_run(self, name, info):
        runs = []
        if self.RUNS_FILE.exists():
            try:
                with open(self.RUNS_FILE, "r", encoding="utf-8") as f:
                    runs = json.load(f)
            except Exception:
                runs = []
        runs.insert(0, info)
        runs = runs[:200]
        self.RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.RUNS_FILE, "w", encoding="utf-8") as f:
            json.dump(runs, f, ensure_ascii=False, indent=2)

    def _ensure_templates(self):
        if self.TEMPLATES_FILE.exists():
            return
        templates = {
            "auth-flow": {
                "vars": {"base_url": "http://127.0.0.1:5000"},
                "requests": [
                    {
                        "name": "login", "protocol": "http",
                        "method": "POST",
                        "url": "{{base_url}}/api/login",
                        "headers": "Content-Type: application/json",
                        "body": '{"username":"admin","password":"admin123"}',
                        "extract": {"path": "token", "vault_key": "tpl.jwt"},
                        "assert": {"status": 200, "max_ms": 2000},
                        "delay_ms": 0, "retry": 0, "retry_on": [],
                        "skip_if_prev_failed": False, "run_if_prev_status": [],
                    },
                    {
                        "name": "me", "protocol": "http",
                        "method": "GET",
                        "url": "{{base_url}}/api/me",
                        "headers": "Authorization: Bearer {{vault.tpl.jwt}}",
                        "body": "",
                        "extract": {"path": "", "vault_key": ""},
                        "assert": {"status": 200, "max_ms": 2000},
                        "delay_ms": 0, "retry": 0, "retry_on": [],
                        "skip_if_prev_failed": True, "run_if_prev_status": [200],
                    },
                ],
            },
            "auth-flow-2fa": {
                "vars": {"base_url": "http://127.0.0.1:5000"},
                "auth": {
                    "enabled": True,
                    "login_url": "http://127.0.0.1:5000/api/login",
                    "user": "admin",
                    "password": "admin123",
                    "auth_type": "JSON body",
                    "totp_secret": "",
                    "verify_url": "",
                    "challenge_path": "challenge_id",
                    "code_field": "code",
                    "token_path": "token",
                    "vault_key": "collections.auth.token",
                    "max_reauth": 2,
                },
                "requests": [
                    {
                        "name": "me", "protocol": "http",
                        "method": "GET",
                        "url": "{{base_url}}/api/me",
                        "headers": "Authorization: Bearer {{vault.collections.auth.token}}",
                        "body": "",
                        "extract": {"path": "", "vault_key": ""},
                        "assert": {"status": 200, "max_ms": 2000},
                        "delay_ms": 0, "retry": 0, "retry_on": [],
                        "skip_if_prev_failed": False, "run_if_prev_status": [],
                    },
                ],
            },
            "crud-smoke": {
                "vars": {"base_url": "http://127.0.0.1:5000"},
                "requests": [
                    {
                        "name": "get", "protocol": "http",
                        "method": "GET",
                        "url": "{{base_url}}/api/users",
                        "headers": "", "body": "",
                        "extract": {"path": "", "vault_key": ""},
                        "assert": {"status": 200, "body_contains": '"count"',
                                   "max_ms": 2000},
                        "delay_ms": 0, "retry": 0, "retry_on": [],
                        "skip_if_prev_failed": False, "run_if_prev_status": [],
                    },
                    {
                        "name": "post", "protocol": "http",
                        "method": "POST",
                        "url": "{{base_url}}/api/echo",
                        "headers": "Content-Type: application/json",
                        "body": '{"x":1}',
                        "extract": {"path": "", "vault_key": ""},
                        "assert": {"status": 200, "json_path": "json.x",
                                   "json_equals": 1, "max_ms": 2000},
                        "delay_ms": 0, "retry": 0, "retry_on": [],
                        "skip_if_prev_failed": False, "run_if_prev_status": [],
                    },
                ],
            },
            "health-retry": {
                "vars": {"base_url": "http://127.0.0.1:5000"},
                "requests": [
                    {
                        "name": "health", "protocol": "http",
                        "method": "GET",
                        "url": "{{base_url}}/api/health",
                        "headers": "", "body": "",
                        "extract": {"path": "", "vault_key": ""},
                        "assert": {"status": 200, "max_ms": 1000},
                        "delay_ms": 500, "retry": 3, "retry_on": [500, 502, 503],
                        "skip_if_prev_failed": False, "run_if_prev_status": [],
                    },
                ],
            },
            "ws-echo": {
                "vars": {"ws_url": "wss://echo.websocket.events"},
                "requests": [
                    {
                        "name": "ws connect+send", "protocol": "ws",
                        "method": "WS",
                        "url": "{{ws_url}}",
                        "headers": "",
                        "body": "hello-onyx",
                        "extract": {"path": "", "vault_key": ""},
                        "assert": {"status": 101, "body_contains": "hello-onyx",
                                   "max_ms": 0},
                        "ws_binary": False, "ws_timeout": 5,
                        "ws_close": True, "ws_reuse_key": "",
                        "delay_ms": 0, "retry": 0, "retry_on": [],
                        "skip_if_prev_failed": False, "run_if_prev_status": [],
                    },
                ],
            },
        }
        self.TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.TEMPLATES_FILE, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)

    def _load_templates(self):
        try:
            with open(self.TEMPLATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def create_widget(self) -> QWidget:
        root = QWidget()
        self._widget = root

        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        mono = QFont("Consolas", 10)
        self.mono = mono

        layout.addLayout(self._build_top_bar())
        layout.addLayout(self._build_env_bar())

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        self.search_input = QLineEdit()
        self.search_input.textChanged.connect(self._refresh_list)
        ll.addWidget(self.search_input)

        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.setDefaultDropAction(Qt.MoveAction)
        self._list.currentRowChanged.connect(self._on_select)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        self._list.itemDoubleClicked.connect(lambda _: self._run_single())
        ll.addWidget(self._list, 1)

        list_btns = QHBoxLayout()
        add_btn = QPushButton()
        self._buttons["add"] = add_btn
        add_btn.clicked.connect(self._add_request)
        list_btns.addWidget(add_btn)

        dup_btn = QPushButton()
        self._buttons["dup"] = dup_btn
        dup_btn.setFixedWidth(70)
        dup_btn.clicked.connect(self._duplicate_request)
        list_btns.addWidget(dup_btn)

        del_r_btn = QPushButton()
        self._buttons["del"] = del_r_btn
        del_r_btn.clicked.connect(self._delete_request)
        list_btns.addWidget(del_r_btn)

        ll.addLayout(list_btns)

        run_one_btn = QPushButton()
        self._buttons["run_this"] = run_one_btn
        run_one_btn.setIcon(theme_loader.get_icon(icon_name="play", emoji=">", size=14))
        run_one_btn.clicked.connect(self._run_single)
        ll.addWidget(run_one_btn)

        self.run_all_btn = QPushButton()
        self._buttons["run_all"] = self.run_all_btn
        self.run_all_btn.setIcon(theme_loader.get_icon(icon_name="play", emoji=">", size=14))
        self.run_all_btn.clicked.connect(self._run_all)
        ll.addWidget(self.run_all_btn)

        self.run_dd_btn = QPushButton()
        self._buttons["run_datadriven"] = self.run_dd_btn
        self.run_dd_btn.setIcon(theme_loader.get_icon(icon_name="play", emoji=">", size=14))
        self.run_dd_btn.clicked.connect(self._run_datadriven)
        ll.addWidget(self.run_dd_btn)

        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_request_tab(), "")
        self._tabs.addTab(self._build_assert_tab(), "")
        self._tabs.addTab(self._build_options_tab(), "")
        rl.addWidget(self._tabs, 1)

        save_btn = QPushButton()
        self._buttons["save_req"] = save_btn
        save_btn.clicked.connect(self._save_current)
        rl.addWidget(save_btn)

        split.addWidget(right)
        split.setSizes([300, 900])
        layout.addWidget(split, 2)

        results_lbl = QLabel()
        self._labels["results_title"] = results_lbl
        results_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(results_lbl)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self._table.doubleClicked.connect(self._show_result_detail)
        layout.addWidget(self._table, 1)

        self._log = QPlainTextEdit()
        self._log.setFont(mono)
        self._log.setReadOnly(True)
        self._log.setFixedHeight(80)
        layout.addWidget(self._log)

        self.retranslate()
        self._refresh_collections()
        return root

    def _build_top_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self._labels["collection"] = QLabel()
        self._labels["collection"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self._labels["collection"])

        self.collection_box = QComboBox()
        self.collection_box.setFixedWidth(200)
        self.collection_box.currentTextChanged.connect(self._switch_collection)
        bar.addWidget(self.collection_box)

        new_btn = QPushButton()
        self._buttons["new_coll"] = new_btn
        new_btn.setFixedWidth(90)
        new_btn.clicked.connect(self._new_collection)
        bar.addWidget(new_btn)

        tpl_btn = QPushButton()
        self._buttons["from_tpl"] = tpl_btn
        tpl_btn.setFixedWidth(160)
        tpl_btn.clicked.connect(self._new_from_template)
        bar.addWidget(tpl_btn)

        del_btn = QPushButton()
        self._buttons["del_coll"] = del_btn
        del_btn.setFixedWidth(100)
        del_btn.clicked.connect(self._delete_collection)
        bar.addWidget(del_btn)

        exp_btn = QPushButton()
        self._buttons["export"] = exp_btn
        exp_btn.setFixedWidth(100)
        exp_btn.clicked.connect(self._export)
        bar.addWidget(exp_btn)

        imp_btn = QPushButton()
        self._buttons["import"] = imp_btn
        imp_btn.setFixedWidth(100)
        imp_btn.clicked.connect(self._import)
        bar.addWidget(imp_btn)

        bar.addStretch()
        return bar

    def _build_env_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self._labels["env"] = QLabel()
        self._labels["env"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self._labels["env"])

        self.env_box = QComboBox()
        self.env_box.setFixedWidth(150)
        self.env_box.currentTextChanged.connect(self._on_env_changed)
        bar.addWidget(self.env_box)

        env_edit = QPushButton()
        self._buttons["edit_vars"] = env_edit
        env_edit.setFixedWidth(150)
        env_edit.clicked.connect(self._edit_vars)
        bar.addWidget(env_edit)

        auth_btn = QPushButton()
        self._buttons["auth"] = auth_btn
        auth_btn.setFixedWidth(170)
        auth_btn.clicked.connect(self._edit_auth)
        bar.addWidget(auth_btn)

        self._checks["stop_on_error"] = QCheckBox()
        self._checks["stop_on_error"].stateChanged.connect(self._on_options_changed)
        bar.addWidget(self._checks["stop_on_error"])

        self._checks["parallel"] = QCheckBox()
        self._checks["parallel"].stateChanged.connect(self._on_options_changed)
        bar.addWidget(self._checks["parallel"])

        self._labels["workers"] = QLabel()
        self._labels["workers"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self._labels["workers"])

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 64)
        self.workers_spin.setValue(5)
        self.workers_spin.setFixedWidth(70)
        self.workers_spin.valueChanged.connect(self._on_workers_changed)
        bar.addWidget(self.workers_spin)

        bar.addStretch()

        runs_btn = QPushButton()
        self._buttons["runs_history"] = runs_btn
        runs_btn.setFixedWidth(160)
        runs_btn.clicked.connect(self._show_runs_history)
        bar.addWidget(runs_btn)

        return bar

    def _build_request_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        row = QHBoxLayout()
        self._labels["name"] = QLabel()
        self._labels["name"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self._labels["name"])
        self.name_input = QLineEdit()
        self.name_input.setFont(self.mono)
        row.addWidget(self.name_input, 2)

        self._labels["protocol"] = QLabel()
        self._labels["protocol"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self._labels["protocol"])
        self.protocol_box = QComboBox()
        self.protocol_box.addItem("http", "http")
        self.protocol_box.addItem("ws", "ws")
        self.protocol_box.setFixedWidth(80)
        self.protocol_box.currentIndexChanged.connect(self._on_protocol_changed)
        row.addWidget(self.protocol_box)

        self._labels["method"] = QLabel()
        self._labels["method"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self._labels["method"])
        self.method_box = QComboBox()
        self.method_box.addItems(
            ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "WS"])
        self.method_box.setFixedWidth(110)
        row.addWidget(self.method_box)

        self._labels["from_curl"] = QLabel()
        self._labels["from_curl"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self._labels["from_curl"])
        
        curl_btn = QPushButton()
        self._buttons["import_curl"] = curl_btn
        curl_btn.setFixedWidth(110)
        curl_btn.clicked.connect(self._import_curl_into_request)
        row.addWidget(curl_btn)

        outer.addLayout(row)

        self._labels["url"] = QLabel()
        self._labels["url"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        outer.addWidget(self._labels["url"])
        self.url_input = QLineEdit()
        self.url_input.setFont(self.mono)
        outer.addWidget(self.url_input)

        self._labels["headers"] = QLabel()
        self._labels["headers"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        outer.addWidget(self._labels["headers"])
        self.headers_edit = QPlainTextEdit()
        self.headers_edit.setFont(self.mono)
        self.headers_edit.setFixedHeight(70)
        outer.addWidget(self.headers_edit)

        self._labels["body"] = QLabel()
        self._labels["body"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        outer.addWidget(self._labels["body"])
        self.body_edit = QPlainTextEdit()
        self.body_edit.setFont(self.mono)
        self.body_edit.setFixedHeight(120)
        outer.addWidget(self.body_edit)

        self._json_hl = JsonHighlighter(self.body_edit.document())

        return page

    def _on_protocol_changed(self):
        is_ws = self.protocol_box.currentData() == "ws"
        try:
            if is_ws:
                self.method_box.setCurrentText("WS")
                self.method_box.setEnabled(False)
                self.body_edit.setPlaceholderText(T("field.ws_body.placeholder"))
            else:
                if self.method_box.currentText() == "WS":
                    self.method_box.setCurrentText("GET")
                self.method_box.setEnabled(True)
                self.body_edit.setPlaceholderText("")
        except RuntimeError:
            return

    def _build_assert_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        self._groups = getattr(self, "_groups", {})
        g = QGroupBox()
        self._groups["assert"] = g
        form = QFormLayout(g)

        self.assert_status = QLineEdit()
        self.assert_status_label = QLabel()
        form.addRow(self.assert_status_label, self.assert_status)

        self.assert_body_contains = QLineEdit()
        self.assert_body_label = QLabel()
        form.addRow(self.assert_body_label, self.assert_body_contains)

        self.assert_json_path = QLineEdit()
        self.assert_json_path_label = QLabel()
        form.addRow(self.assert_json_path_label, self.assert_json_path)

        self.assert_json_equals = QLineEdit()
        self.assert_json_equals_label = QLabel()
        form.addRow(self.assert_json_equals_label, self.assert_json_equals)

        self.assert_max_ms = QSpinBox()
        self.assert_max_ms.setRange(0, 600000)
        self.assert_max_ms.setValue(0)
        self.assert_max_ms_label = QLabel()
        form.addRow(self.assert_max_ms_label, self.assert_max_ms)

        outer.addWidget(g)

        self.assert_hint = QLabel()
        self.assert_hint.setWordWrap(True)
        self.assert_hint.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        outer.addWidget(self.assert_hint)

        outer.addStretch()
        return page

    def _build_options_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        g = QGroupBox()
        self._groups["vault"] = g
        form = QFormLayout(g)
        self.extract_path = QLineEdit()
        self.extract_path_label = QLabel()
        form.addRow(self.extract_path_label, self.extract_path)

        self.extract_key = QLineEdit()
        self.extract_key_label = QLabel()
        form.addRow(self.extract_key_label, self.extract_key)
        outer.addWidget(g)

        g2 = QGroupBox()
        self._groups["retry"] = g2
        form2 = QFormLayout(g2)

        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(0, 60000)
        self.delay_ms_label = QLabel()
        form2.addRow(self.delay_ms_label, self.delay_ms)

        self.retry_count = QSpinBox()
        self.retry_count.setRange(0, 10)
        self.retry_count_label = QLabel()
        form2.addRow(self.retry_count_label, self.retry_count)

        self.retry_on = QLineEdit()
        self.retry_on_label = QLabel()
        form2.addRow(self.retry_on_label, self.retry_on)

        outer.addWidget(g2)

        g3 = QGroupBox()
        self._groups["cond"] = g3
        form3 = QFormLayout(g3)

        self.skip_if_prev_failed = QCheckBox()
        self._checks["skip_prev"] = self.skip_if_prev_failed
        form3.addRow(self.skip_if_prev_failed)

        self.run_if_prev_status = QLineEdit()
        self.run_if_prev_label = QLabel()
        form3.addRow(self.run_if_prev_label, self.run_if_prev_status)

        outer.addWidget(g3)

        g4 = QGroupBox()
        self._groups["ws"] = g4
        form4 = QFormLayout(g4)

        self.ws_binary = QCheckBox()
        form4.addRow(self.ws_binary)

        self.ws_close = QCheckBox()
        self.ws_close.setChecked(True)
        form4.addRow(self.ws_close)

        self.ws_timeout = QSpinBox()
        self.ws_timeout.setRange(1, 600)
        self.ws_timeout.setValue(5)
        self.ws_timeout_label = QLabel()
        form4.addRow(self.ws_timeout_label, self.ws_timeout)

        self.ws_reuse_key = QLineEdit()
        self.ws_reuse_key_label = QLabel()
        form4.addRow(self.ws_reuse_key_label, self.ws_reuse_key)

        outer.addWidget(g4)

        outer.addStretch()
        return page

    def retranslate(self):
        if not hasattr(self, "collection_box"):
            return

        self._labels["collection"].setText(T("collection"))
        self._labels["env"].setText(T("env"))
        self._labels["results_title"].setText(T("results.title"))

        self._buttons["add"].setText(T("add"))
        self._buttons["dup"].setText(T("dup"))
        self._buttons["del"].setText(T("del"))
        self._buttons["run_this"].setText(T("run_this"))
        self._buttons["run_all"].setText(T("run_all"))
        self._buttons["run_datadriven"].setText(T("run_datadriven"))
        self._buttons["new_coll"].setText(T("new"))
        self._buttons["from_tpl"].setText(T("from_template"))
        self._buttons["del_coll"].setText(T("delete"))
        self._buttons["export"].setText(T("export"))
        self._buttons["import"].setText(T("import"))
        self._buttons["edit_vars"].setText(T("edit_vars"))
        self._buttons["auth"].setText(T("auth_2fa"))
        self._buttons["auth"].setToolTip(T("auth_2fa.tip"))
        self._buttons["runs_history"].setText(T("runs_history"))
        self._buttons["save_req"].setText(T("save_request"))
        self._buttons["import_curl"].setText(T("field.import"))

        self._checks["stop_on_error"].setText(T("stop_on_error"))
        self._checks["parallel"].setText(T("parallel"))
        self._checks["skip_prev"].setText(T("opt.skip_prev"))

        self._labels["workers"].setText(T("workers"))

        self.search_input.setPlaceholderText(T("search.placeholder"))

        self._tabs.setTabText(0, T("tab.request"))
        self._tabs.setTabText(1, T("tab.assert"))
        self._tabs.setTabText(2, T("tab.options"))

        self._table.setHorizontalHeaderLabels([
            T("results.col.num"),
            T("results.col.name"),
            T("results.col.status"),
            T("results.col.time"),
            T("results.col.assert"),
        ])

        self._labels["name"].setText(T("field.name"))
        self._labels["protocol"].setText(T("field.protocol"))
        self._labels["method"].setText(T("field.method"))
        self._labels["from_curl"].setText(T("field.from_curl"))
        self._labels["url"].setText(T("field.url"))
        self._labels["headers"].setText(T("field.headers"))
        self._labels["body"].setText(T("field.body"))

        self._groups["assert"].setTitle(T("assert.title"))
        self.assert_status_label.setText(T("assert.status"))
        self.assert_status.setPlaceholderText(T("assert.status.placeholder"))
        self.assert_body_label.setText(T("assert.body"))
        self.assert_body_contains.setPlaceholderText(T("assert.body.placeholder"))
        self.assert_json_path_label.setText(T("assert.json_path"))
        self.assert_json_path.setPlaceholderText(T("assert.json_path.placeholder"))
        self.assert_json_equals_label.setText(T("assert.json_equals"))
        self.assert_json_equals.setPlaceholderText(T("assert.json_equals.placeholder"))
        self.assert_max_ms_label.setText(T("assert.max_ms"))
        self.assert_max_ms.setSpecialValueText(T("assert.max_ms.no_limit"))
        self.assert_hint.setText(T("assert.hint"))

        self._groups["vault"].setTitle(T("opt.vault_group"))
        self.extract_path_label.setText(T("opt.vault_path"))
        self.extract_path.setPlaceholderText(T("opt.vault_path.placeholder"))
        self.extract_key_label.setText(T("opt.vault_key"))
        self.extract_key.setPlaceholderText(T("opt.vault_key.placeholder"))

        self._groups["retry"].setTitle(T("opt.retry_group"))
        self.delay_ms_label.setText(T("opt.delay"))
        self.retry_count_label.setText(T("opt.retry"))
        self.retry_on_label.setText(T("opt.retry_on"))
        self.retry_on.setPlaceholderText(T("opt.retry_on.placeholder"))

        self._groups["cond"].setTitle(T("opt.cond_group"))
        self.run_if_prev_label.setText(T("opt.run_if_prev"))
        self.run_if_prev_status.setPlaceholderText(T("opt.run_if_prev.placeholder"))

        self._groups["ws"].setTitle(T("opt.ws_group"))
        self.ws_binary.setText(T("opt.ws_binary"))
        self.ws_close.setText(T("opt.ws_close"))
        self.ws_timeout_label.setText(T("opt.ws_timeout"))
        self.ws_reuse_key_label.setText(T("opt.ws_reuse_key"))
        self.ws_reuse_key.setPlaceholderText(T("opt.ws_reuse_key.placeholder"))

    def _refresh_collections(self):
        if not hasattr(self, "collection_box"):
            return
        try:
            self.collection_box.blockSignals(True)
            cur = self.collection_box.currentText()
            self.collection_box.clear()
            for name in sorted(self._collections.keys()):
                self.collection_box.addItem(name)
            if cur and cur in self._collections:
                self.collection_box.setCurrentText(cur)
            self.collection_box.blockSignals(False)
        except RuntimeError:
            return

        if self.collection_box.count():
            self._switch_collection(self.collection_box.currentText())
        else:
            self._switch_collection(None)

    def _switch_collection(self, name):
        if not name or name not in self._collections:
            self._active_name = None
            self._active_idx = -1
            self._refresh_env_box()
            self._refresh_list()
            self._clear_editor()
            return

        self._active_name = name
        self._active_idx = -1
        self._refresh_env_box()
        self._refresh_list()
        self._load_collection_options()

    def _active_coll(self):
        if not self._active_name:
            return None
        return self._collections.get(self._active_name)

    def _load_collection_options(self):
        coll = self._active_coll()
        if coll is None:
            return
        try:
            self._checks["stop_on_error"].blockSignals(True)
            self._checks["parallel"].blockSignals(True)
            self.workers_spin.blockSignals(True)
            self._checks["stop_on_error"].setChecked(coll.get("stop_on_error", False))
            self._checks["parallel"].setChecked(coll.get("parallel", False))
            self.workers_spin.setValue(int(coll.get("parallel_workers") or 5))
            self._checks["stop_on_error"].blockSignals(False)
            self._checks["parallel"].blockSignals(False)
            self.workers_spin.blockSignals(False)
        except RuntimeError:
            return

    def _on_options_changed(self):
        coll = self._active_coll()
        if coll is None:
            return
        try:
            coll["stop_on_error"] = self._checks["stop_on_error"].isChecked()
            coll["parallel"] = self._checks["parallel"].isChecked()
            self._save_collection(self._active_name)
        except RuntimeError:
            return

    def _on_workers_changed(self, value):
        coll = self._active_coll()
        if coll is None:
            return
        try:
            coll["parallel_workers"] = int(value)
            self._save_collection(self._active_name)
        except RuntimeError:
            return

    def _new_collection(self):
        name, ok = QInputDialog.getText(parent_widget(self._widget),
                                        T("dialog.new_collection.title"),
                                        T("dialog.new_collection.field"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._collections:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), T("msg.exists"))
            return
        self._collections[name] = {
            "requests": [], "vars": {}, "environments": {},
            "active_env": "", "stop_on_error": False, "parallel": False,
            "parallel_workers": 5, "session_name": "",
            "auth": {},
        }
        self._save_collection(name)
        self._refresh_collections()
        self.collection_box.setCurrentText(name)

    def _new_from_template(self):
        templates = self._load_templates()
        if not templates:
            QMessageBox.information(parent_widget(self._widget),
                                    T("dialog.template.title"),
                                    T("msg.no_templates"))
            return
        names = list(templates.keys())
        name, ok = QInputDialog.getItem(
            parent_widget(self._widget), T("dialog.template.title"),
            T("dialog.template.field"), names, 0, False)
        if not ok or not name:
            return
        coll_name, ok2 = QInputDialog.getText(
            parent_widget(self._widget), T("dialog.new_collection.title"),
            T("dialog.new_collection.field"), text=name)
        if not ok2 or not coll_name.strip():
            return
        coll_name = coll_name.strip()
        if coll_name in self._collections:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), T("msg.exists"))
            return
        data = json.loads(json.dumps(templates[name]))
        data.setdefault("environments", {})
        data.setdefault("active_env", "")
        data.setdefault("stop_on_error", False)
        data.setdefault("parallel", False)
        data.setdefault("parallel_workers", 5)
        data.setdefault("session_name", "")
        data.setdefault("auth", {})
        self._collections[coll_name] = data
        self._save_collection(coll_name)
        self._refresh_collections()
        self.collection_box.setCurrentText(coll_name)

    def _delete_collection(self):
        name = self.collection_box.currentText()
        if not name:
            return
        if QMessageBox.question(
            parent_widget(self._widget), T("msg.confirm_delete.title"),
            T("msg.confirm_delete", name=name),
        ) != QMessageBox.Yes:
            return
        path = self.STORE / f"{name}.json"
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
        self._collections.pop(name, None)
        if self._active_name == name:
            self._active_name = None
            self._active_idx = -1
        self._refresh_collections()

    def _refresh_env_box(self):
        if not hasattr(self, "env_box"):
            return
        try:
            self.env_box.blockSignals(True)
            self.env_box.clear()
            self.env_box.addItem("(none)")
            coll = self._active_coll()
            if coll is not None:
                envs = coll.get("environments", {}) or {}
                for name in sorted(envs.keys()):
                    self.env_box.addItem(name)
                active = coll.get("active_env", "") or "(none)"
                idx = self.env_box.findText(active)
                if idx >= 0:
                    self.env_box.setCurrentIndex(idx)
            self.env_box.blockSignals(False)
        except RuntimeError:
            return

    def _on_env_changed(self, text):
        coll = self._active_coll()
        if coll is None:
            return
        coll["active_env"] = "" if text == "(none)" else text
        self._save_collection(self._active_name)

    def _edit_vars(self):
        coll = self._active_coll()
        if coll is None:
            return

        text, ok = QInputDialog.getMultiLineText(
            parent_widget(self._widget), T("dialog.vars.title"),
            T("dialog.vars.hint"),
            json.dumps({
                "vars": coll.get("vars", {}),
                "environments": coll.get("environments", {}),
                "session_name": coll.get("session_name", ""),
            }, ensure_ascii=False, indent=2)
        )
        if not ok:
            return
        try:
            parsed = json.loads(text)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), T("dialog.vars.error", error=e))
            return
        coll["vars"] = parsed.get("vars", {}) or {}
        coll["environments"] = parsed.get("environments", {}) or {}
        if "session_name" in parsed:
            coll["session_name"] = parsed.get("session_name") or ""
        self._save_collection(self._active_name)
        self._refresh_env_box()

    def _edit_auth(self):
        coll = self._active_coll()
        if coll is None:
            return
        current = coll.get("auth") or {
            "enabled": False,
            "profile": "",
            "login_url": "",
            "user": "",
            "password": "",
            "auth_type": "JSON body",
            "totp_secret": "",
            "verify_url": "",
            "challenge_path": "challenge_id",
            "code_field": "code",
            "token_path": "token",
            "vault_key": "collections.auth.token",
            "method": "",
            "code": "",
            "max_reauth": 2,
        }
        text, ok = QInputDialog.getMultiLineText(
            parent_widget(self._widget),
            T("dialog.auth.title"),
            T("dialog.auth.hint"),
            json.dumps(current, ensure_ascii=False, indent=2)
        )
        if not ok:
            return
        try:
            parsed = json.loads(text)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), T("dialog.vars.error", error=e))
            return
        coll["auth"] = parsed or {}
        self._save_collection(self._active_name)

    def _filtered_requests(self):
        coll = self._active_coll()
        if coll is None:
            return []
        all_reqs = coll.get("requests") or []
        needle = self.search_input.text().strip().lower() if hasattr(self, "search_input") else ""
        if not needle:
            return list(enumerate(all_reqs))
        out = []
        for i, r in enumerate(all_reqs):
            hay = " ".join(str(r.get(k, "")) for k in
                           ("name", "url", "method", "protocol")).lower()
            if needle in hay:
                out.append((i, r))
        return out

    def _refresh_list(self):
        if not hasattr(self, "_list"):
            return
        try:
            self._list.clear()
            for i, req in self._filtered_requests():
                status_marker = self._status_marker(req)
                proto = (req.get("protocol") or "http").upper()
                text = (f"{status_marker} [{proto}] "
                        f"{req.get('name') or req.get('url', '')}")
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, i)
                color = self._status_color(req)
                if color:
                    item.setForeground(QColor(color))
                self._list.addItem(item)
        except RuntimeError:
            return

    def _status_marker(self, req):
        key = req.get("name") or req.get("url")
        if key in self._last_run:
            r = self._last_run[key]
            if r.get("ok") and r.get("assert_ok", True):
                return "[v]"
            return "[x]"
        return "[ ]"

    def _status_color(self, req):
        key = req.get("name") or req.get("url")
        if key in self._last_run:
            r = self._last_run[key]
            if r.get("ok") and r.get("assert_ok", True):
                return _c("success")
            return _c("danger")
        return None

    def _on_rows_moved(self, *args):
        coll = self._active_coll()
        if coll is None:
            return
        new_order = []
        all_reqs = coll.get("requests") or []
        for i in range(self._list.count()):
            item = self._list.item(i)
            orig_idx = item.data(Qt.UserRole)
            if orig_idx is not None and 0 <= orig_idx < len(all_reqs):
                new_order.append(all_reqs[orig_idx])
        if len(new_order) == len(all_reqs):
            coll["requests"] = new_order
            self._save_collection(self._active_name)
            self._refresh_list()

    def _add_request(self):
        if not self._active_name:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), T("msg.select_first"))
            return
        coll = self._active_coll()
        if coll is None:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), T("msg.select_first"))
            return
        req = self._empty_request()
        coll.setdefault("requests", []).append(req)
        self._save_collection(self._active_name)
        self._refresh_list()
        self._list.setCurrentRow(len(coll["requests"]) - 1)

    def _empty_request(self):
        return {
            "name": "new request", "protocol": "http", "method": "GET",
            "url": "", "headers": "", "body": "",
            "extract": {"path": "", "vault_key": ""},
            "assert": {"status": None, "body_contains": "",
                       "json_path": "", "json_equals": "", "max_ms": 0},
            "delay_ms": 0, "retry": 0, "retry_on": [],
            "skip_if_prev_failed": False, "run_if_prev_status": [],
            "ws_binary": False, "ws_close": True, "ws_timeout": 5,
            "ws_reuse_key": "",
        }

    def _duplicate_request(self):
        if self._active_idx < 0:
            return
        coll = self._active_coll()
        if coll is None:
            self._active_idx = -1
            self._clear_editor()
            return
        reqs = coll.get("requests") or []
        if not (0 <= self._active_idx < len(reqs)):
            self._active_idx = -1
            self._clear_editor()
            return
        copy = json.loads(json.dumps(reqs[self._active_idx]))
        copy["name"] = (copy.get("name") or "") + "_copy"
        reqs.insert(self._active_idx + 1, copy)
        self._save_collection(self._active_name)
        self._refresh_list()
        self._list.setCurrentRow(self._active_idx + 1)

    def _delete_request(self):
        if self._active_idx < 0:
            return
        coll = self._active_coll()
        if coll is None:
            self._active_idx = -1
            self._clear_editor()
            return
        reqs = coll.get("requests") or []
        if not (0 <= self._active_idx < len(reqs)):
            self._active_idx = -1
            self._clear_editor()
            return
        del reqs[self._active_idx]
        self._save_collection(self._active_name)
        self._active_idx = -1
        self._refresh_list()
        self._clear_editor()

    def _on_select(self, row):
        if row < 0:
            self._active_idx = -1
            self._clear_editor()
            return
        coll = self._active_coll()
        if coll is None:
            self._active_idx = -1
            self._clear_editor()
            return
        item = self._list.item(row)
        orig_idx = item.data(Qt.UserRole) if item else None
        reqs = coll.get("requests") or []
        if orig_idx is None or not (0 <= orig_idx < len(reqs)):
            self._active_idx = -1
            self._clear_editor()
            return
        self._active_idx = orig_idx
        req = reqs[orig_idx]
        self._load_request_into_editor(req)

    def _load_request_into_editor(self, req):
        self.name_input.setText(req.get("name", ""))
        proto = (req.get("protocol") or "http").lower()
        idx = self.protocol_box.findData(proto)
        if idx >= 0:
            self.protocol_box.setCurrentIndex(idx)
        self.method_box.setCurrentText(req.get("method", "GET"))
        self.url_input.setText(req.get("url", ""))
        self.headers_edit.setPlainText(req.get("headers", ""))
        self.body_edit.setPlainText(req.get("body", ""))
        ex = req.get("extract") or {}
        self.extract_path.setText(ex.get("path", ""))
        self.extract_key.setText(ex.get("vault_key", ""))
        a = req.get("assert") or {}
        self.assert_status.setText(
            "" if a.get("status") is None else str(a.get("status")))
        self.assert_body_contains.setText(a.get("body_contains", ""))
        self.assert_json_path.setText(a.get("json_path", ""))
        self.assert_json_equals.setText(str(a.get("json_equals", "")))
        self.assert_max_ms.setValue(int(a.get("max_ms") or 0))
        self.delay_ms.setValue(int(req.get("delay_ms") or 0))
        self.retry_count.setValue(int(req.get("retry") or 0))
        self.retry_on.setText(
            ", ".join(str(x) for x in (req.get("retry_on") or [])))
        self.skip_if_prev_failed.setChecked(bool(req.get("skip_if_prev_failed")))
        self.run_if_prev_status.setText(
            ", ".join(str(x) for x in (req.get("run_if_prev_status") or [])))
        self.ws_binary.setChecked(bool(req.get("ws_binary")))
        self.ws_close.setChecked(bool(req.get("ws_close", True)))
        self.ws_timeout.setValue(int(req.get("ws_timeout") or 5))
        self.ws_reuse_key.setText(req.get("ws_reuse_key", ""))
        self._on_protocol_changed()

    def _clear_editor(self):
        try:
            self.name_input.clear()
            idx = self.protocol_box.findData("http")
            if idx >= 0:
                self.protocol_box.setCurrentIndex(idx)
            self.method_box.setCurrentText("GET")
            self.url_input.clear()
            self.headers_edit.clear()
            self.body_edit.clear()
            self.extract_path.clear()
            self.extract_key.clear()
            self.assert_status.clear()
            self.assert_body_contains.clear()
            self.assert_json_path.clear()
            self.assert_json_equals.clear()
            self.assert_max_ms.setValue(0)
            self.delay_ms.setValue(0)
            self.retry_count.setValue(0)
            self.retry_on.clear()
            self.skip_if_prev_failed.setChecked(False)
            self.run_if_prev_status.clear()
            self.ws_binary.setChecked(False)
            self.ws_close.setChecked(True)
            self.ws_timeout.setValue(5)
            self.ws_reuse_key.clear()
        except RuntimeError:
            return
        self._on_protocol_changed()

    def _save_current(self):
        if self._active_idx < 0:
            return
        coll = self._active_coll()
        if coll is None:
            self._active_idx = -1
            self._clear_editor()
            return
        reqs = coll.get("requests") or []
        if not (0 <= self._active_idx < len(reqs)):
            self._active_idx = -1
            self._clear_editor()
            return
        req = reqs[self._active_idx]
        req["name"] = self.name_input.text()
        req["protocol"] = self.protocol_box.currentData() or "http"
        req["method"] = self.method_box.currentText()
        req["url"] = self.url_input.text()
        req["headers"] = self.headers_edit.toPlainText()
        req["body"] = self.body_edit.toPlainText()
        req["extract"] = {
            "path": self.extract_path.text().strip(),
            "vault_key": self.extract_key.text().strip(),
        }
        status_text = self.assert_status.text().strip()
        req["assert"] = {
            "status": int(status_text) if status_text.isdigit() else None,
            "body_contains": self.assert_body_contains.text(),
            "json_path": self.assert_json_path.text().strip(),
            "json_equals": coerce_value(self.assert_json_equals.text().strip()),
            "max_ms": self.assert_max_ms.value(),
        }
        req["delay_ms"] = self.delay_ms.value()
        req["retry"] = self.retry_count.value()
        req["retry_on"] = parse_int_list(self.retry_on.text())
        req["skip_if_prev_failed"] = self.skip_if_prev_failed.isChecked()
        req["run_if_prev_status"] = parse_int_list(self.run_if_prev_status.text())
        req["ws_binary"] = self.ws_binary.isChecked()
        req["ws_close"] = self.ws_close.isChecked()
        req["ws_timeout"] = self.ws_timeout.value()
        req["ws_reuse_key"] = self.ws_reuse_key.text().strip()

        self._save_collection(self._active_name)
        self._refresh_list()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) == self._active_idx:
                self._list.setCurrentRow(i)
                break

    def _import_curl_into_request(self):
        text, ok = QInputDialog.getMultiLineText(
            parent_widget(self._widget), T("dialog.import_curl.title"),
            T("dialog.import_curl.hint"))
        if not ok or not text.strip():
            return
        try:
            parsed = get_bus().call("http.curl.parse", text)
        except KeyError:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), "http.curl.parse unavailable")
            return
        idx = self.protocol_box.findData("http")
        if idx >= 0:
            self.protocol_box.setCurrentIndex(idx)
        if parsed.get("method"):
            self.method_box.setCurrentText(parsed["method"].upper())
        if parsed.get("url"):
            self.url_input.setText(parsed["url"])
        if parsed.get("headers"):
            self.headers_edit.setPlainText("\n".join(parsed["headers"]))
        if parsed.get("body") is not None:
            self.body_edit.setPlainText(parsed["body"])

    def _run_all(self):
        coll = self._active_coll()
        if coll is None:
            return
        if not coll.get("requests"):
            return
        if self._runner is not None and self._runner.isRunning():
            return

        self._table.setRowCount(0)
        self._log.clear()
        self._last_run = {}
        self._last_results = []

        session = "cl-" + uuid.uuid4().hex[:8]
        parallel = coll.get("parallel", False)
        stop_on_error = coll.get("stop_on_error", False)
        auth_on = bool(coll.get("auth", {}).get("enabled"))

        self._running_name = self._active_name
        self.run_all_btn.setEnabled(False)
        self._log.appendPlainText(
            T("run.start",
              name=self._active_name, session=session,
              parallel=parallel, stop=stop_on_error,
              auth="on" if auth_on else "off"))

        self._runner = CollectionRunWorker(
            self._execute_collection, self._active_name, coll,
            "Collections", session, stop_on_error, parallel,
        )
        self._runner.step.connect(self._on_step_done)
        self._runner.done.connect(self._on_run_done)
        self._runner.start()

    def _run_datadriven(self):
        coll = self._active_coll()
        if coll is None:
            return
        reqs = coll.get("requests") or []
        if not reqs:
            return
        if self._datadriven is not None and self._datadriven.isRunning():
            return

        path, _ = QFileDialog.getOpenFileName(
            parent_widget(self._widget),
            T("datadriven.title"),
            "", "CSV (*.csv);;JSON (*.json);;All (*)")
        if not path:
            return

        rows = []
        try:
            if path.endswith(".json"):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data = [data]
                rows = [d for d in data if isinstance(d, dict)]
            else:
                import csv
                with open(path, "r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), str(e))
            return

        if not rows:
            QMessageBox.information(parent_widget(self._widget),
                                    T("datadriven.title"),
                                    T("datadriven.empty"))
            return

        session = "dd-" + uuid.uuid4().hex[:8]
        self._table.setRowCount(0)
        self._log.clear()
        self._last_run = {}
        self._last_results = []
        self._running_name = self._active_name
        self.run_dd_btn.setEnabled(False)
        self.run_all_btn.setEnabled(False)

        self._log.appendPlainText(T(
            "datadriven.start", name=self._active_name,
            session=session, rows=len(rows)))

        self._datadriven = DataDrivenRunWorker(
            self, self._active_name, coll, rows,
            "Collections", session,
        )
        self._datadriven.step.connect(self._on_step_done)
        self._datadriven.done.connect(self._on_run_done_dd)
        self._datadriven.start()

    def _on_step_done(self, idx, total, result):
        self._add_result_row(idx, result)
        self._last_run[result.get("name") or result.get("url")] = result
        self._refresh_list()
        tag = T("tag.ok") if result.get("assert_ok", True) else T("tag.fail")
        if result.get("reauthed"):
            tag += T("tag.reauth")
        row_info = ""
        if result.get("_row"):
            row_info = "  " + ", ".join(f"{k}={v}" for k, v in result["_row"].items())
        self._log.appendPlainText(T(
            "run.step",
            idx=idx + 1, total=total,
            name=result.get("name"),
            status=result.get("status", "ERR"),
            ms=f"{(result.get('elapsed') or 0)*1000:.0f}",
            tag=tag) + row_info)

    def _on_run_done(self, results, session):
        self._last_results = results
        self.run_all_btn.setEnabled(True)

        passed = sum(1 for r in results
                     if r.get("ok") and r.get("assert_ok", True))
        failed = len(results) - passed
        self._log.appendPlainText(T(
            "run.done", passed=passed, failed=failed, session=session))

        name = self._running_name or self._active_name
        if name:
            coll = self._collections.get(name, {})
            self._append_run(name, {
                "session": session,
                "started_at": datetime.utcnow().isoformat(),
                "total": len(results),
                "passed": passed,
                "failed": failed,
                "parallel": coll.get("parallel", False),
                "stop_on_error": coll.get("stop_on_error", False),
            })
        self._running_name = None
        self._runner = None

    def _on_run_done_dd(self, results, session):
        self._last_results = results
        self.run_dd_btn.setEnabled(True)
        self.run_all_btn.setEnabled(True)

        passed = sum(1 for r in results
                     if r.get("ok") and r.get("assert_ok", True))
        failed = len(results) - passed
        self._log.appendPlainText(T(
            "run.done", passed=passed, failed=failed, session=session))

        name = self._running_name or self._active_name
        if name:
            self._append_run(name, {
                "session": session,
                "started_at": datetime.utcnow().isoformat(),
                "total": len(results),
                "passed": passed,
                "failed": failed,
                "parallel": False,
                "stop_on_error": False,
                "datadriven": True,
            })
        self._running_name = None
        self._datadriven = None

    def _run_single(self):
        if self._active_idx < 0 or not self._active_name:
            return
        if self._single is not None and self._single.isRunning():
            return
        coll = self._active_coll()
        if coll is None:
            return
        reqs = coll.get("requests") or []
        if not (0 <= self._active_idx < len(reqs)):
            return
        req = reqs[self._active_idx]
        self._log.clear()
        self._log.appendPlainText(T("run.single", name=req.get("name")))
        vars_map = self._resolve_vars(self._active_name)
        auth_config = coll.get("auth") or {}
        session_name = self._session_name(self._active_name)
        session = "single-" + uuid.uuid4().hex[:6]

        self._single = SingleRunWorker(
            self._execute_one, req, vars_map, "Collections",
            session, auth_config,
        )
        self._single._exec_fn = lambda r, p, v, c, s, a: self._execute_one(
            r, p, v, c, s, a, session_name)
        self._single.done.connect(self._on_single_done)
        self._single.start()

    def _on_single_done(self, result):
        self._last_run[result.get("name") or result.get("url")] = result
        self._table.setRowCount(0)
        self._add_result_row(0, result)
        self._refresh_list()
        tag = T("tag.ok") if result.get("assert_ok", True) else T("tag.fail")
        if result.get("reauthed"):
            tag += T("tag.reauth")
        self._log.appendPlainText(T(
            "run.single_ok", status=result.get("status", "ERR"), tag=tag))
        self._single = None

    def _resolve_vars(self, collection_name):
        coll = self._collections.get(collection_name) or {}
        out = dict(coll.get("vars", {}) or {})
        active_env = coll.get("active_env") or ""
        if active_env:
            env_vars = (coll.get("environments", {}) or {}).get(active_env, {}) or {}
            out.update(env_vars)
        return out

    def _session_name(self, collection_name):
        coll = self._collections.get(collection_name) or {}
        name = (coll.get("session_name") or "").strip()
        if name:
            return name
        return f"coll:{collection_name}"

    def _substitute_all(self, text, vars_map):
        from core.utils import resolve_placeholders
        return resolve_placeholders(text, vars_map=vars_map)

    def _try_reauth(self, auth_config, caller, session):
        profile = auth_config.get("profile")
        if profile:
            try:
                r = get_bus().call(
                    "login.run_profile", profile,
                    auth_config.get("user", ""),
                    auth_config.get("password", ""),
                    code=auth_config.get("code"),
                    method=auth_config.get("method"),
                    totp_secret=auth_config.get("totp_secret"),
                    vault_key=auth_config.get("vault_key"),
                    caller=caller, session=session,
                )
            except KeyError:
                return False
            except Exception:
                return False
            return bool(r and r.get("ok"))

        login_url = auth_config.get("login_url")
        if not login_url:
            return False
        user = auth_config.get("user", "")
        password = auth_config.get("password", "")
        auth_type = auth_config.get("auth_type", "JSON body")
        token_path = auth_config.get("token_path", "token")
        vault_key = auth_config.get("vault_key", "collections.auth.token")
        totp_secret = auth_config.get("totp_secret") or None
        verify_url = auth_config.get("verify_url") or None
        challenge_path = auth_config.get("challenge_path", "challenge_id")
        code_field = auth_config.get("code_field", "code")
        code = auth_config.get("code") or None
        try:
            r = get_bus().call(
                "login.run", login_url, user, password,
                auth_type, token_path, vault_key,
                totp_secret=totp_secret,
                verify_url=verify_url,
                challenge_path=challenge_path,
                code_field=code_field,
                code=code,
                caller=caller, session=session,
            )
        except KeyError:
            return False
        except Exception:
            return False
        return bool(r and r.get("ok"))

    def _execute_collection(self, name, coll, caller, session,
                            stop_on_error, parallel, on_progress):
        requests = coll.get("requests", [])
        if not requests:
            return [], session

        if not session:
            session = "cl-" + uuid.uuid4().hex[:8]

        vars_map = self._resolve_vars(name)
        auth_config = coll.get("auth") or {}
        session_name = self._session_name(name)
        results = []

        try:
            if parallel:
                idx_map = list(enumerate(requests))
                workers = max(1, int(coll.get("parallel_workers") or 5))
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futures = {
                        ex.submit(self._execute_one, req, None, vars_map,
                                  caller, session, auth_config, session_name): idx
                        for idx, req in idx_map
                    }
                    for fut in as_completed(futures):
                        idx = futures[fut]
                        try:
                            res = fut.result()
                        except Exception as e:
                            res = {"ok": False, "error": str(e),
                                   "name": requests[idx].get("name"),
                                   "url": requests[idx].get("url"),
                                   "assert_ok": False}
                        results.append((idx, res))
                        if on_progress:
                            on_progress(idx, len(requests), res)
                results.sort(key=lambda x: x[0])
                ordered = [r for _, r in results]
            else:
                ordered = []
                prev = None
                for idx, req in enumerate(requests):
                    if stop_on_error and prev and (
                            not prev.get("ok")
                            or not prev.get("assert_ok", True)):
                        res = {
                            "name": req.get("name"),
                            "url": req.get("url"),
                            "skipped": True,
                            "ok": False,
                            "assert_ok": False,
                            "error": "skipped (stop on error)",
                        }
                        ordered.append(res)
                        if on_progress:
                            on_progress(idx, len(requests), res)
                        continue

                    res = self._execute_one(req, prev, vars_map, caller,
                                             session, auth_config, session_name)
                    ordered.append(res)
                    prev = res
                    if on_progress:
                        on_progress(idx, len(requests), res)
        finally:
            self._close_all_ws()

        return ordered, session

    def _execute_one(self, req, prev_result, vars_map, caller, session,
                     auth_config=None, session_name=None):
        if (req.get("protocol") or "http") == "ws":
            return self._execute_ws(req, vars_map, caller, session)

        name = req.get("name")
        method = req.get("method", "GET")
        url_raw = req.get("url", "")
        headers_raw = req.get("headers", "")
        body_raw = req.get("body", "")

        prev_failed = bool(prev_result and (
            not prev_result.get("ok")
            or not prev_result.get("assert_ok", True)))

        if req.get("skip_if_prev_failed") and prev_failed:
            return {
                "name": name, "url": url_raw, "method": method,
                "skipped": True, "ok": False, "assert_ok": False,
                "error": "skipped (previous failed)",
            }

        allowed = req.get("run_if_prev_status") or []
        if allowed and prev_result is not None:
            prev_status = prev_result.get("status")
            if prev_status not in allowed:
                return {
                    "name": name, "url": url_raw, "method": method,
                    "skipped": True, "ok": False, "assert_ok": False,
                    "error": f"skipped (prev status {prev_status} not in {allowed})",
                }

        delay_ms = int(req.get("delay_ms") or 0)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

        retry_max = int(req.get("retry") or 0)
        retry_on = req.get("retry_on") or []
        auth_enabled = bool(auth_config and auth_config.get("enabled"))
        max_reauth = int(auth_config.get("max_reauth", 2)) if auth_config else 2

        attempt = 0
        reauth_count = 0
        reauth_done = False
        resp = None
        url = url_raw

        while True:
            url = self._substitute_all(url_raw, vars_map)
            headers_text = self._substitute_all(headers_raw, vars_map)
            body = self._substitute_all(body_raw, vars_map)
            headers = parse_headers(headers_text)

            attempt += 1
            try:
                kwargs = dict(headers=headers, body=body, timeout=30)
                if caller:
                    kwargs["caller"] = caller
                if session:
                    kwargs["session"] = session
                if session_name:
                    kwargs["session_name"] = session_name
                resp = get_bus().call("http.request", method, url, **kwargs)
            except KeyError:
                return {
                    "name": name, "url": url, "method": method,
                    "ok": False, "assert_ok": False,
                    "error": "http.request unavailable",
                }
            except Exception as e:
                resp = {"ok": False, "error": str(e)}

            if (auth_enabled and resp.get("ok")
                    and resp.get("status") == 401
                    and reauth_count < max_reauth):
                should_reauth = False
                with self._reauth_lock:
                    if self._reauth_in_progress:
                        time.sleep(0.4)
                        continue
                    self._reauth_in_progress = True
                    should_reauth = True
                try:
                    if should_reauth and self._try_reauth(auth_config, caller, session):
                        reauth_done = True
                        reauth_count += 1
                        attempt = 0
                        continue
                finally:
                    with self._reauth_lock:
                        self._reauth_in_progress = False

            if (resp.get("ok") and resp.get("status") in retry_on
                    and attempt <= retry_max):
                time.sleep(0.3)
                continue
            break

        result = {
            "name": name,
            "url": url,
            "method": method,
            "ok": bool(resp.get("ok")),
            "status": resp.get("status"),
            "elapsed": resp.get("elapsed"),
            "error": resp.get("error"),
            "body": resp.get("body"),
            "attempt": attempt,
            "protocol": "http",
        }
        if reauth_done:
            result["reauthed"] = True
            result["reauth_count"] = reauth_count

        extract = req.get("extract") or {}
        if (extract.get("path") and extract.get("vault_key")
                and resp.get("ok")
                and 200 <= (resp.get("status") or 0) < 300):
            try:
                parsed = json.loads(resp["body"])
                val = extract_by_path(parsed, extract["path"])
                if val is not None:
                    get_bus().call("vault.set", extract["vault_key"], str(val))
                    result["extracted"] = extract["vault_key"]
            except Exception:
                pass

        assert_ok, assert_msg = self._check_asserts(
            req.get("assert") or {}, resp)
        result["assert_ok"] = assert_ok
        result["assert_msg"] = assert_msg

        return result

    def _execute_ws(self, req, vars_map, caller, session):
        name = req.get("name")
        url = self._substitute_all(req.get("url", ""), vars_map)
        headers_text = self._substitute_all(req.get("headers", ""), vars_map)
        body = self._substitute_all(req.get("body", ""), vars_map)
        headers = parse_headers(headers_text) if headers_text.strip() else {}
        binary = bool(req.get("ws_binary"))
        timeout = float(req.get("ws_timeout") or 5)
        close_after = bool(req.get("ws_close", True))
        reuse_key = (req.get("ws_reuse_key") or "").strip()

        bus = get_bus()

        if not bus.has_service("ws.connect"):
            return {
                "name": name, "url": url, "method": "WS",
                "protocol": "ws",
                "ok": False, "assert_ok": False,
                "error": "ws.connect unavailable (WebSocket module disabled)",
            }

        start_time = time.time()
        conn_id = None
        if reuse_key:
            with self._ws_lock:
                conn_id = self._ws_connections.get(reuse_key)

        if conn_id:
            conns = bus.call("ws.list")
            match = [c for c in conns if c["id"] == conn_id]
            if not match or match[0]["state"] != "connected":
                with self._ws_lock:
                    self._ws_connections.pop(reuse_key, None)
                conn_id = None

        if not conn_id:
            try:
                r = bus.call("ws.connect", url, headers=headers,
                             caller=caller, session=session)
            except KeyError:
                return {
                    "name": name, "url": url, "method": "WS",
                    "protocol": "ws", "ok": False, "assert_ok": False,
                    "error": "ws.connect unavailable",
                }
            except Exception as e:
                return {
                    "name": name, "url": url, "method": "WS",
                    "protocol": "ws", "ok": False, "assert_ok": False,
                    "error": str(e),
                }
            if not r.get("ok"):
                return {
                    "name": name, "url": url, "method": "WS",
                    "protocol": "ws", "ok": False, "assert_ok": False,
                    "error": r.get("error") or "ws connect failed",
                }
            conn_id = r["connection_id"]
            if reuse_key:
                with self._ws_lock:
                    self._ws_connections[reuse_key] = conn_id

        try:
            if not bus.call("ws.wait_connected", conn_id, max(2, int(timeout))):
                if close_after:
                    try:
                        bus.call("ws.close", conn_id)
                    except Exception:
                        pass
                if reuse_key:
                    with self._ws_lock:
                        self._ws_connections.pop(reuse_key, None)
                return {
                    "name": name, "url": url, "method": "WS",
                    "protocol": "ws", "ok": False, "assert_ok": False,
                    "error": "ws not connected",
                }
        except KeyError:
            return {
                "name": name, "url": url, "method": "WS",
                "protocol": "ws", "ok": False, "assert_ok": False,
                "error": "ws.wait_connected unavailable",
            }

        result = {
            "name": name, "url": url, "method": "WS",
            "protocol": "ws",
            "ok": True, "status": 101,
        }

        if body:
            try:
                ex = bus.call("ws.exchange", conn_id, body,
                              binary=binary, timeout=timeout)
            except KeyError:
                result["ok"] = False
                result["error"] = "ws.exchange unavailable"
            except Exception as e:
                result["ok"] = False
                result["error"] = str(e)
            else:
                if not ex.get("ok"):
                    result["ok"] = False
                    result["error"] = ex.get("error")
                else:
                    if binary:
                        data = ex.get("binary_data") or b""
                        try:
                            text_val = (data.decode("utf-8", errors="replace")
                                        if isinstance(data, bytes) else str(data))
                        except Exception:
                            text_val = ""
                        result["body"] = text_val
                    else:
                        result["body"] = ex.get("text") or ""
                    result["ws_opcode"] = ex.get("opcode")
                    result["ws_size"] = ex.get("size", 0)
        else:
            result["body"] = ""

        result["elapsed"] = time.time() - start_time

        if close_after:
            try:
                bus.call("ws.close", conn_id)
            except Exception:
                pass
            if reuse_key:
                with self._ws_lock:
                    self._ws_connections.pop(reuse_key, None)

        if result.get("ok"):
            assert_ok, assert_msg = self._check_asserts(
                req.get("assert") or {},
                {"ok": True, "status": result.get("status"),
                 "body": result.get("body", ""),
                 "elapsed": result.get("elapsed")})
            result["assert_ok"] = assert_ok
            result["assert_msg"] = assert_msg
        else:
            result["assert_ok"] = False
            result["assert_msg"] = result.get("error") or "ws failed"

        extract = req.get("extract") or {}
        if (extract.get("path") and extract.get("vault_key")
                and result.get("ok")):
            try:
                parsed = json.loads(result.get("body") or "")
                val = extract_by_path(parsed, extract["path"])
                if val is not None:
                    bus.call("vault.set", extract["vault_key"], str(val))
                    result["extracted"] = extract["vault_key"]
            except Exception:
                pass

        return result

    def _check_asserts(self, a, resp):
        msgs = []

        expected_status = a.get("status")
        if expected_status is not None:
            actual = resp.get("status")
            if actual != expected_status:
                msgs.append(f"status {actual} != {expected_status}")

        contains = a.get("body_contains") or ""
        if contains:
            body = resp.get("body") or ""
            if contains not in body:
                msgs.append(f"body missing '{contains[:40]}'")

        json_path = a.get("json_path") or ""
        json_equals = a.get("json_equals", "")
        if json_path:
            try:
                parsed = json.loads(resp.get("body") or "")
                val = extract_by_path(parsed, json_path)
                if val is None:
                    msgs.append(f"json path '{json_path}' not found")
                elif str(val) != str(json_equals):
                    msgs.append(f"json {json_path}={val!r} != {json_equals!r}")
            except Exception as e:
                msgs.append(f"json parse error: {e}")

        max_ms = int(a.get("max_ms") or 0)
        if max_ms > 0:
            elapsed = (resp.get("elapsed") or 0) * 1000
            if elapsed > max_ms:
                msgs.append(f"slow {elapsed:.0f} ms > {max_ms} ms")

        if not resp.get("ok"):
            msgs.append(resp.get("error") or "request failed")

        return (len(msgs) == 0), "; ".join(msgs)

    def _add_result_row(self, idx, r):
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(str(idx + 1)))
        self._table.setItem(row, 1, QTableWidgetItem(r.get("name") or "-"))
        status = r.get("status")
        status_text = str(status) if status is not None else (
            "SKIP" if r.get("skipped") else "ERR")
        if r.get("reauthed"):
            status_text += T("tag.reauth").strip()
        s_item = QTableWidgetItem(status_text)
        s_item.setForeground(QColor(_c("success") if r.get("ok") else _c("danger")))
        self._table.setItem(row, 2, s_item)
        time_s = f"{(r.get('elapsed') or 0)*1000:.0f} ms" if r.get("elapsed") else "-"
        self._table.setItem(row, 3, QTableWidgetItem(time_s))
        msg = r.get("assert_msg") or r.get("error") or ""
        if r.get("extracted"):
            msg = (msg + f"  [vault: {r['extracted']}]").strip()
        if r.get("_row"):
            row_str = "  ".join(f"{k}={v}" for k, v in r["_row"].items())
            msg = (msg + f"  [{row_str}]").strip()
        a_item = QTableWidgetItem(msg)
        if not r.get("assert_ok", True):
            a_item.setForeground(QColor(_c("danger")))
        self._table.setItem(row, 4, a_item)
        item = self._table.item(row, 0)
        if item:
            item.setData(Qt.UserRole, r)

    def _show_result_detail(self):
        sel = self._table.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        item = self._table.item(row, 0)
        if not item:
            return
        r = item.data(Qt.UserRole)
        if not r:
            return
        text = []
        if r.get("_row"):
            text.append("row:")
            for k, v in r["_row"].items():
                text.append(f"  {k} = {v}")
            text.append("")
        text.append(f"name: {r.get('name')}")
        text.append(f"protocol: {r.get('protocol', 'http')}")
        text.append(f"url: {r.get('url')}")
        text.append(f"method: {r.get('method')}")
        text.append(f"status: {r.get('status')}")
        text.append(f"elapsed: {(r.get('elapsed') or 0)*1000:.0f} ms")
        text.append(f"attempt: {r.get('attempt', 1)}")
        text.append(f"ok: {r.get('ok')}")
        text.append(f"assert_ok: {r.get('assert_ok')}")
        if r.get("reauthed"):
            text.append(f"reauthed: yes (count={r.get('reauth_count', 1)})")
        if r.get("ws_opcode"):
            text.append(f"ws_opcode: {r.get('ws_opcode')}")
        if r.get("assert_msg"):
            text.append(f"assert_msg: {r.get('assert_msg')}")
        if r.get("error"):
            text.append(f"error: {r.get('error')}")
        if r.get("extracted"):
            text.append(f"extracted: {r['extracted']}")
        text.append("")
        text.append("BODY:")
        text.append((r.get("body") or "")[:3000])
        QMessageBox.information(parent_widget(self._widget),
                                T("result_detail"), "\n".join(text))

    def _show_runs_history(self):
        if not self.RUNS_FILE.exists():
            QMessageBox.information(parent_widget(self._widget),
                                    T("dialog.runs.title"),
                                    T("msg.history_empty"))
            return
        try:
            with open(self.RUNS_FILE, "r", encoding="utf-8") as f:
                runs = json.load(f)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), str(e))
            return

        if not runs:
            QMessageBox.information(parent_widget(self._widget),
                                    T("dialog.runs.title"),
                                    T("msg.history_empty"))
            return

        dlg = QDialog(parent_widget(self._widget))
        dlg.setWindowTitle(T("dialog.runs.title"))
        dlg.setMinimumSize(760, 460)
        dlg.resize(860, 540)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QLabel(T("dialog.runs.total", count=len(runs)))
        header.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(header)

        lines = []
        for r in runs:
            started = (r.get("started_at") or "")[:19].replace("T", " ")
            session = r.get("session") or "-"
            total = r.get("total", 0)
            passed = r.get("passed", 0)
            failed = r.get("failed", 0)
            parallel = "P" if r.get("parallel") else "-"
            stop = "S" if r.get("stop_on_error") else "-"
            dd = "D" if r.get("datadriven") else "-"
            lines.append(
                f"{started}  "
                f"session={session:<14}  "
                f"total={total:>3}  passed={passed:>3}  failed={failed:>3}  "
                f"{parallel} {stop} {dd}"
            )

        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont("Consolas", 10))
        txt.setPlainText("\n".join(lines))
        txt.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(txt, 1)

        btns = QHBoxLayout()
        btns.addStretch()

        clear_btn = QPushButton(T("dialog.runs.clear"))
        clear_btn.setFixedWidth(200)
        clear_btn.clicked.connect(lambda: self._clear_runs_history(dlg))
        btns.addWidget(clear_btn)

        close_btn = QPushButton(T("dialog.runs.close"))
        close_btn.setFixedWidth(140)
        close_btn.clicked.connect(dlg.accept)
        btns.addWidget(close_btn)

        layout.addLayout(btns)

        dlg.exec()

    def _clear_runs_history(self, dlg):
        if QMessageBox.question(
            parent_widget(self._widget), T("dialog.runs.clear"),
            T("dialog.runs.confirm"),
        ) != QMessageBox.Yes:
            return
        try:
            if self.RUNS_FILE.exists():
                self.RUNS_FILE.unlink()
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), str(e))
            return
        dlg.accept()
        QMessageBox.information(parent_widget(self._widget),
                                T("dialog.runs.title"),
                                T("dialog.runs.cleared"))

    def _export(self):
        name = self.collection_box.currentText()
        if not name:
            return
        if name not in self._collections:
            return
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget), T("dialog.export.title"),
            f"{name}.json", "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._collections[name], f, ensure_ascii=False, indent=2)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            parent_widget(self._widget), T("dialog.import.title"), "",
            "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                T("msg.error"), str(e))
            return
        data.setdefault("requests", [])
        data.setdefault("vars", {})
        data.setdefault("environments", {})
        data.setdefault("active_env", "")
        data.setdefault("stop_on_error", False)
        data.setdefault("parallel", False)
        data.setdefault("parallel_workers", 5)
        data.setdefault("session_name", "")
        data.setdefault("auth", {})
        base = Path(path).stem or "imported"
        name = base
        i = 1
        while name in self._collections:
            name = f"{base}_{i}"
            i += 1
        self._collections[name] = data
        self._save_collection(name)
        self._refresh_collections()
        self.collection_box.setCurrentText(name)