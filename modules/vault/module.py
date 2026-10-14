import json
import threading
import time
from pathlib import Path


from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QPlainTextEdit, QSpinBox, QComboBox,
    QFileDialog
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.i18n import tr
from core.utils import color as _c, parent_widget, make_label


CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
STORE = CONFIG_DIR / "vault.json"
KEY_FILE = CONFIG_DIR / ".vault_key"
KEYRING_SERVICE = "onyx.vault"
KEYRING_USER = "default"
ENC_PREFIX = b"ONYX1:"
SAVE_DEBOUNCE_MS = 500


def _get_or_create_key():
    try:
        import keyring
        from cryptography.fernet import Fernet
        existing = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if existing:
            return existing.encode()
        k = Fernet.generate_key()
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, k.decode())
        return k
    except Exception:
        pass

    if KEY_FILE.exists():
        try:
            return KEY_FILE.read_bytes()
        except Exception:
            pass

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return b""

    k = Fernet.generate_key()
    try:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_bytes(k)
        try:
            KEY_FILE.chmod(0o600)
        except Exception:
            pass
    except Exception:
        return b""
    return k


def _rotate_keyring_key(new_key: bytes):
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, new_key.decode())
        return True
    except Exception:
        pass
    try:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_bytes(new_key)
        try:
            KEY_FILE.chmod(0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


class _Crypto:
    def __init__(self):
        self.fernet = None
        self.available = False
        self.mode = "plaintext"
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            return
        key = _get_or_create_key()
        if not key:
            return
        try:
            from cryptography.fernet import Fernet
            self.fernet = Fernet(key)
            self.available = True
            self.mode = "encrypted"
        except Exception:
            self.fernet = None
            self.available = False
            self.mode = "plaintext"

    def encrypt(self, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if not self.available:
            return raw
        return ENC_PREFIX + self.fernet.encrypt(raw)

    def decrypt(self, blob):
        if blob.startswith(ENC_PREFIX):
            if not self.available:
                raise RuntimeError(
                    "vault file is encrypted but cryptography/keyring unavailable")
            raw = self.fernet.decrypt(blob[len(ENC_PREFIX):])
        else:
            raw = blob
        return json.loads(raw.decode("utf-8"))

    def rotate(self):
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            return False
        new_key = Fernet.generate_key()
        if not _rotate_keyring_key(new_key):
            return False
        try:
            self.fernet = Fernet(new_key)
            self.available = True
            self.mode = "encrypted"
            return True
        except Exception:
            return False


class VaultModule(BaseModule):
    def __init__(self):
        self._data = {}
        self._ttl = {}
        self._widget = None
        self._lock = threading.RLock()
        self._crypto = _Crypto()
        self._lbls = {}
        self._dirty = False
        self._save_timer = None

    def on_load(self, api):
        self._load_from_disk()
        api.register_service(self.name, "vault.set", self._svc_set,
                             "vault.set(key, value, ttl=None)")
        api.register_service(self.name, "vault.get", self._svc_get,
                             "vault.get(key, default=None)")
        api.register_service(self.name, "vault.delete", self._svc_delete,
                             "vault.delete(key)")
        api.register_service(self.name, "vault.list", self._svc_list,
                             "vault.list()")
        api.register_service(self.name, "vault.clear", self._svc_clear,
                             "vault.clear()")
        api.register_service(self.name, "vault.namespaces", self._svc_namespaces,
                             "vault.namespaces() -> list[str]")
        api.register_service(self.name, "vault.rotate", self._svc_rotate,
                             "vault.rotate() -> bool")
        api.register_service(self.name, "vault.stats", self._svc_stats,
                             "vault.stats() -> dict")
        api.event.connect(self._on_bus_event)

    def on_unload(self, api):
        try:
            api.event.disconnect(self._on_bus_event)
        except Exception:
            pass
        self._flush_save()

    def on_shutdown(self):
        self._flush_save()

    def _load_from_disk(self):
        if not STORE.exists():
            return
        try:
            blob = STORE.read_bytes()
            data = self._crypto.decrypt(blob)
            if isinstance(data, dict) and data.get("_version") == 2:
                with self._lock:
                    self._data = data.get("_data") or {}
                    self._ttl = data.get("_ttl") or {}
            elif isinstance(data, dict):
                with self._lock:
                    self._data = data
                    self._ttl = {}
        except Exception as e:
            print(f"[vault] load failed: {e}")

    def _expire_keys(self):
        now = time.time()
        expired = [k for k, ts in self._ttl.items() if ts and ts < now]
        for k in expired:
            self._data.pop(k, None)
            self._ttl.pop(k, None)
        return expired

    def _schedule_save(self):
        self._dirty = True
        if self._save_timer is None:
            self._save_timer = threading.Timer(
                SAVE_DEBOUNCE_MS / 1000.0, self._flush_save)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _flush_save(self):
        with self._lock:
            if not self._dirty:
                self._save_timer = None
                return
            self._dirty = False
            self._save_timer = None
            self._expire_keys()
            snapshot = dict(self._data)
            ttl_snapshot = dict(self._ttl)

        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            safe = {}
            for k, v in snapshot.items():
                try:
                    json.dumps(v)
                    safe[k] = v
                except (TypeError, ValueError):
                    safe[k] = str(v)
            payload = {"_version": 2, "_data": safe, "_ttl": ttl_snapshot}
            blob = self._crypto.encrypt(payload)
            STORE.write_bytes(blob)
            try:
                STORE.chmod(0o600)
            except Exception:
                pass
        except Exception as e:
            print(f"[vault] save failed: {e}")

    def _svc_set(self, key, value, ttl=None):
        with self._lock:
            self._data[key] = value
            if ttl:
                self._ttl[key] = time.time() + float(ttl)
            else:
                self._ttl.pop(key, None)
        self._schedule_save()
        self._refresh()
        try:
            get_bus().emit_event("vault.changed",
                                 key=key, value_preview=str(value)[:50])
        except Exception:
            pass

    def _svc_get(self, key, default=None):
        with self._lock:
            exp = self._ttl.get(key)
            if exp and exp < time.time():
                self._data.pop(key, None)
                self._ttl.pop(key, None)
                self._schedule_save()
                return default
            return self._data.get(key, default)

    def _svc_delete(self, key):
        with self._lock:
            existed = self._data.pop(key, None) is not None
            self._ttl.pop(key, None)
        if existed:
            self._schedule_save()
            self._refresh()
            try:
                get_bus().emit_event("vault.deleted", key=key)
            except Exception:
                pass
        return existed

    def _svc_list(self):
        with self._lock:
            self._expire_keys()
            return [(k, type(v).__name__) for k, v in self._data.items()]

    def _svc_clear(self):
        with self._lock:
            self._data.clear()
            self._ttl.clear()
        self._schedule_save()
        self._refresh()
        try:
            get_bus().emit_event("vault.cleared")
        except Exception:
            pass

    def _svc_namespaces(self):
        with self._lock:
            self._expire_keys()
            ns = set()
            for k in self._data:
                if "." in k:
                    ns.add(k.split(".", 1)[0])
            return sorted(ns)

    def _svc_rotate(self):
        with self._lock:
            snapshot = dict(self._data)
            ttl_snapshot = dict(self._ttl)
        if not self._crypto.rotate():
            return False
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            payload = {"_version": 2, "_data": snapshot, "_ttl": ttl_snapshot}
            blob = self._crypto.encrypt(payload)
            STORE.write_bytes(blob)
            try:
                STORE.chmod(0o600)
            except Exception:
                pass
            self._dirty = False
            return True
        except Exception as e:
            print(f"[vault] rotate failed: {e}")
            return False

    def _svc_stats(self):
        with self._lock:
            self._expire_keys()
            namespaces = {}
            ttls = 0
            for k in self._data:
                ns = k.split(".", 1)[0] if "." in k else "(root)"
                namespaces[ns] = namespaces.get(ns, 0) + 1
            for v in self._ttl.values():
                if v:
                    ttls += 1
            return {
                "total": len(self._data),
                "namespaces": namespaces,
                "with_ttl": ttls,
                "mode": self._crypto.mode,
                "file_size": STORE.stat().st_size if STORE.exists() else 0,
            }

    def create_widget(self) -> QWidget:
        root = QWidget()
        self._widget = root

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self._lbls["title"] = QLabel()
        self._lbls["title"].setStyleSheet(
            f"color: {_c('fg')}; font-size: 14px; font-weight: bold;")
        layout.addWidget(self._lbls["title"])

        self._lbls["hint"] = QLabel()
        self._lbls["hint"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self._lbls["hint"])

        row = QHBoxLayout()
        self._lbls["key"] = make_label("")
        row.addWidget(self._lbls["key"])
        self.key_input = QLineEdit()
        row.addWidget(self.key_input, 1)

        self._lbls["value"] = make_label("")
        row.addWidget(self._lbls["value"])
        self.val_input = QLineEdit()
        row.addWidget(self.val_input, 2)

        self._lbls["ttl"] = make_label("")
        row.addWidget(self._lbls["ttl"])
        self.ttl_spin = QSpinBox()
        self.ttl_spin.setRange(0, 86400 * 365)
        self.ttl_spin.setValue(0)
        self.ttl_spin.setSpecialValueText("∞")
        self.ttl_spin.setFixedWidth(90)
        row.addWidget(self.ttl_spin)

        self.set_btn = QPushButton()
        self.set_btn.setFixedWidth(100)
        self.set_btn.clicked.connect(self._ui_set)
        row.addWidget(self.set_btn)

        layout.addLayout(row)

        tools = QHBoxLayout()
        self._lbls["search"] = make_label("")
        tools.addWidget(self._lbls["search"])
        self.search_input = QLineEdit()
        self.search_input.textChanged.connect(self._refresh)
        tools.addWidget(self.search_input, 1)

        self.ns_filter = QComboBox()
        self.ns_filter.setFixedWidth(180)
        self.ns_filter.currentTextChanged.connect(self._refresh)
        tools.addWidget(self.ns_filter)

        self.refresh_ns_btn = QPushButton()
        self.refresh_ns_btn.setFixedWidth(120)
        self.refresh_ns_btn.clicked.connect(self._refresh_namespaces)
        tools.addWidget(self.refresh_ns_btn)

        layout.addLayout(tools)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._ui_copy_value)
        layout.addWidget(self.table, 2)

        btns = QHBoxLayout()
        btns.addStretch()

        self.copy_btn = QPushButton()
        self.copy_btn.setFixedWidth(150)
        self.copy_btn.clicked.connect(self._ui_copy_value)
        btns.addWidget(self.copy_btn)

        self.del_btn = QPushButton()
        self.del_btn.setFixedWidth(120)
        self.del_btn.clicked.connect(self._ui_delete)
        btns.addWidget(self.del_btn)

        self.export_btn = QPushButton()
        self.export_btn.setFixedWidth(150)
        self.export_btn.clicked.connect(self._ui_export)
        btns.addWidget(self.export_btn)

        self.import_btn = QPushButton()
        self.import_btn.setFixedWidth(150)
        self.import_btn.clicked.connect(self._ui_import)
        btns.addWidget(self.import_btn)

        self.rotate_btn = QPushButton()
        self.rotate_btn.setFixedWidth(150)
        self.rotate_btn.clicked.connect(self._ui_rotate)
        btns.addWidget(self.rotate_btn)

        self.clear_btn = QPushButton()
        self.clear_btn.setFixedWidth(130)
        self.clear_btn.clicked.connect(self._ui_clear)
        btns.addWidget(self.clear_btn)

        layout.addLayout(btns)

        self._lbls["events"] = QLabel()
        self._lbls["events"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self._lbls["events"])

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(110)
        self.log.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log)

        self.retranslate()
        self._refresh_namespaces()
        self._refresh()
        return root

    def retranslate(self):
        def t(k, **kw):
            return tr(k, module="vault", **kw)

        mode = self._crypto.mode
        mode_key = "storage.encrypted" if mode == "encrypted" else "storage.plaintext"

        if "title" in self._lbls:
            self._lbls["title"].setText(t("title"))
            self._lbls["hint"].setText(t("storage.hint", mode=t(mode_key)))
            self._lbls["key"].setText(t("key.label"))
            self._lbls["value"].setText(t("value.label"))
            self._lbls["ttl"].setText(t("ttl.label"))
            self._lbls["search"].setText(t("search.label"))
            self._lbls["events"].setText(t("events"))
        if hasattr(self, "key_input"):
            self.key_input.setPlaceholderText(t("key.placeholder"))
            self.val_input.setPlaceholderText(t("value.placeholder"))
            self.search_input.setPlaceholderText(t("search.placeholder"))
            self.set_btn.setText(t("set"))
            self.copy_btn.setText(t("copy"))
            self.del_btn.setText(t("delete"))
            self.export_btn.setText(t("export"))
            self.import_btn.setText(t("import"))
            self.rotate_btn.setText(t("rotate"))
            self.refresh_ns_btn.setText(t("refresh_ns"))
            self.clear_btn.setText(t("clear"))
            self.table.setHorizontalHeaderLabels(
                [t("col.key"), t("col.type"), t("col.value"), t("col.ttl")])

    def _refresh_namespaces(self):
        try:
            cur = self.ns_filter.currentText()
            self.ns_filter.blockSignals(True)
            self.ns_filter.clear()
            self.ns_filter.addItem("(all)")
            for ns in self._svc_namespaces():
                self.ns_filter.addItem(ns)
            idx = self.ns_filter.findText(cur)
            if idx >= 0:
                self.ns_filter.setCurrentIndex(idx)
            self.ns_filter.blockSignals(False)
        except RuntimeError:
            return

    def _ui_set(self):
        key = self.key_input.text().strip()
        val = self.val_input.text()
        if not key:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="vault"),
                                tr("err.empty_key", module="vault"))
            return
        ttl = self.ttl_spin.value() or None
        with self._lock:
            self._data[key] = val
            if ttl:
                self._ttl[key] = time.time() + ttl
            else:
                self._ttl.pop(key, None)
        self._schedule_save()
        self.key_input.clear()
        self.val_input.clear()
        self.ttl_spin.setValue(0)
        self._refresh()
        self._refresh_namespaces()
        try:
            get_bus().emit_event("vault.changed",
                                 key=key, value_preview=str(val)[:50])
        except Exception:
            pass

    def _ui_delete(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        key = self.table.item(sel[0].row(), 0).text()
        with self._lock:
            self._data.pop(key, None)
            self._ttl.pop(key, None)
        self._schedule_save()
        self._refresh()
        self._refresh_namespaces()

    def _ui_clear(self):
        if QMessageBox.question(
            parent_widget(self._widget),
            tr("confirm.clear.title", module="vault"),
            tr("confirm.clear.text", module="vault"),
        ) != QMessageBox.Yes:
            return
        with self._lock:
            self._data.clear()
            self._ttl.clear()
        self._schedule_save()
        self._refresh()
        self._refresh_namespaces()

    def _ui_copy_value(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        key = self.table.item(sel[0].row(), 0).text()
        with self._lock:
            val = self._data.get(key, "")
        QGuiApplication.clipboard().setText(str(val))

    def _ui_export(self):
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("export.title", module="vault"),
            "vault_export.json", "JSON (*.json);;Text (*.txt)")
        if not path:
            return
        with self._lock:
            snapshot = dict(self._data)
        try:
            if path.endswith(".txt"):
                with open(path, "w", encoding="utf-8") as f:
                    for k, v in snapshot.items():
                        f.write(f"{k}={v}\n")
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, ensure_ascii=False, indent=2)
            self.log.appendPlainText(
                tr("log.exported", module="vault", count=len(snapshot), path=path))
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="vault"), str(e))

    def _ui_import(self):
        path, _ = QFileDialog.getOpenFileName(
            parent_widget(self._widget),
            tr("import.title", module="vault"),
            "", "JSON (*.json);;Text (*.txt);;All (*)")
        if not path:
            return
        loaded = {}
        try:
            if path.endswith(".json"):
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError("JSON must be an object")
            else:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        loaded[k.strip()] = v.strip()
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="vault"), str(e))
            return

        if not loaded:
            return
        merge = QMessageBox.question(
            parent_widget(self._widget),
            tr("import.confirm.title", module="vault"),
            tr("import.confirm.text", module="vault", count=len(loaded)),
        ) == QMessageBox.Yes
        with self._lock:
            if not merge:
                self._data.clear()
                self._ttl.clear()
            self._data.update(loaded)
        self._schedule_save()
        self._refresh()
        self._refresh_namespaces()
        self.log.appendPlainText(
            tr("log.imported", module="vault", count=len(loaded)))

    def _ui_rotate(self):
        if QMessageBox.question(
            parent_widget(self._widget),
            tr("rotate.confirm.title", module="vault"),
            tr("rotate.confirm.text", module="vault"),
        ) != QMessageBox.Yes:
            return
        ok = self._svc_rotate()
        if ok:
            QMessageBox.information(parent_widget(self._widget),
                                    tr("rotate.title", module="vault"),
                                    tr("rotate.ok", module="vault"))
        else:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("rotate.title", module="vault"),
                                tr("rotate.fail", module="vault"))

    def _refresh(self):
        if not hasattr(self, "table"):
            return
        try:
            with self._lock:
                self._expire_keys()
                items = list(self._data.items())
                ttls = dict(self._ttl)
            needle = self.search_input.text().strip().lower()
            ns = self.ns_filter.currentText()
            ns_active = ns and ns != "(all)"

            filtered = []
            now = time.time()
            for k, v in items:
                if ns_active:
                    if not (k == ns or k.startswith(ns + ".")):
                        continue
                if needle and needle not in k.lower() and needle not in str(v).lower():
                    continue
                filtered.append((k, v))

            self.table.setRowCount(len(filtered))
            for i, (k, v) in enumerate(filtered):
                self.table.setItem(i, 0, QTableWidgetItem(k))
                self.table.setItem(i, 1, QTableWidgetItem(type(v).__name__))
                self.table.setItem(i, 2, QTableWidgetItem(str(v)[:200]))
                ttl = ttls.get(k)
                if ttl:
                    left = int(ttl - now)
                    ttl_text = f"{left}s" if left < 3600 else f"{left // 60}m"
                else:
                    ttl_text = "∞"
                self.table.setItem(i, 3, QTableWidgetItem(ttl_text))
        except RuntimeError:
            return

    def _on_bus_event(self, name: str, payload: dict):
        if not hasattr(self, "log"):
            return
        try:
            short = ", ".join(f"{k}={v!r}" for k, v in payload.items())
            line = f"[bus] {name}"
            if short:
                line += f"   {short}"
            self.log.appendPlainText(line)
        except RuntimeError:
            return