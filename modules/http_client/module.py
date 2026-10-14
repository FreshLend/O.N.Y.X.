import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

import requests
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QGuiApplication, QColor, QAction
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPlainTextEdit, QPushButton, QSplitter, QMessageBox,
    QInputDialog, QFileDialog, QTabWidget, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QMenu, QDialog
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.settings import get_settings
from core.history import get_history
from core.i18n import tr
from core.utils import (
    color as _c, parent_widget, parse_headers as _parse_headers,
    resolve_placeholders as _resolve_vault_placeholders,
)
from styles import loader as theme_loader


CONFIG_ROOT = Path(__file__).resolve().parent.parent.parent / "config"
AUTH_PRESETS = {
    "bearer": ("Bearer <YOUR_TOKEN>", "Authorization: Bearer {v}"),
    "basic": ("Basic <BASE64(user:pass)>", "Authorization: Basic {v}"),
    "apikey_header": ("X-API-Key: <YOUR_KEY>", "X-API-Key: {v}"),
    "apikey_query": ("?api_key=<YOUR_KEY>", ""),
}


def _log(msg):
    try:
        print(f"[http_client] {msg}")
    except Exception:
        pass


class SessionStore:
    MAX_SESSIONS = 64

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions = OrderedDict()
        self._kinds = {}

    def _key(self, engine, name):
        return f"{engine}:{name}"

    def get_requests(self, name):
        key = self._key("requests", name)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is None:
                sess = requests.Session()
                self._sessions[key] = sess
                self._kinds[key] = "requests"
                self._trim()
            else:
                self._sessions.move_to_end(key)
            return sess

    def get_curl_cffi(self, name, impersonate="chrome"):
        key = self._key("curl_cffi", name)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is None:
                try:
                    from curl_cffi import requests as creq
                except ImportError:
                    raise RuntimeError("curl_cffi not installed")
                sess = None
                try:
                    sess = creq.Session(impersonate=impersonate or "chrome")
                except TypeError:
                    try:
                        sess = creq.Session()
                    except Exception as e:
                        raise RuntimeError(f"curl_cffi Session failed: {e}")
                if sess is None:
                    raise RuntimeError("curl_cffi Session creation failed")
                self._sessions[key] = sess
                self._kinds[key] = "curl_cffi"
                self._trim()
            else:
                self._sessions.move_to_end(key)
            return sess

    def get_cloudscraper(self, name):
        key = self._key("cloudscraper", name)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is None:
                try:
                    import cloudscraper
                except ImportError:
                    raise RuntimeError("cloudscraper not installed")
                sess = cloudscraper.create_scraper(
                    browser={"browser": "chrome", "platform": "windows",
                             "mobile": False})
                self._sessions[key] = sess
                self._kinds[key] = "cloudscraper"
                self._trim()
            else:
                self._sessions.move_to_end(key)
            return sess

    def list(self):
        with self._lock:
            out = []
            for key, sess in self._sessions.items():
                try:
                    cookies = list(getattr(sess, "cookies", []) or [])
                except Exception:
                    cookies = []
                out.append({
                    "key": key,
                    "engine": self._kinds.get(key, "?"),
                    "cookies": len(cookies),
                })
            return out

    def clear_one(self, key):
        with self._lock:
            sess = self._sessions.pop(key, None)
            self._kinds.pop(key, None)
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass
            return True
        return False

    def clear_all(self):
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._kinds.clear()
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass

    def _trim(self):
        while len(self._sessions) > self.MAX_SESSIONS:
            key, sess = self._sessions.popitem(last=False)
            self._kinds.pop(key, None)
            try:
                sess.close()
            except Exception:
                pass


_session_store = SessionStore()


def _get_session(name="default"):
    return _session_store.get_requests(name)


def _validate_headers(headers):
    if not headers:
        return None
    for k, v in headers.items():
        try:
            f"{k}: {v}".encode("latin-1")
        except UnicodeEncodeError:
            return (k, v)
    return None


def _validate_url(url):
    if not url:
        return "empty"
    if "<" in url or ">" in url:
        return "placeholder"
    try:
        url.encode("ascii")
    except UnicodeEncodeError:
        return "non_ascii"
    return None


def _dispatch_request(*, engine, method, url, headers, body,
                      proxies, timeout, verify, allow_redirects, impersonate,
                      session_name=None, capture_redirects=False):
    bad_url = _validate_url(url)
    if bad_url == "placeholder":
        return {"ok": False, "error": "URL contains placeholder <...>",
                "engine": engine}
    if bad_url == "non_ascii":
        return {"ok": False, "error": "URL contains non-ASCII characters",
                "engine": engine}

    bad_hdr = _validate_headers(headers)
    if bad_hdr is not None:
        k, v = bad_hdr
        return {
            "ok": False,
            "error": (f"Header '{k}' contains non-latin-1 characters. "
                      f"HTTP headers must be ASCII/latin-1."),
            "engine": engine,
        }

    data = body.encode("utf-8") if body else None
    name = session_name or "default"

    if engine == "curl_cffi":
        try:
            adapter = _session_store.get_curl_cffi(name, impersonate)
        except RuntimeError as e:
            return {"ok": False, "error": str(e), "engine": engine}
        kwargs = dict(headers=headers, data=data, timeout=timeout,
                      verify=verify, allow_redirects=False,
                      impersonate=impersonate or "chrome")
        if proxies:
            kwargs["proxies"] = proxies
    elif engine == "cloudscraper":
        try:
            adapter = _session_store.get_cloudscraper(name)
        except RuntimeError as e:
            return {"ok": False, "error": str(e), "engine": engine}
        kwargs = dict(headers=headers, data=data, timeout=timeout,
                      allow_redirects=False)
        if proxies:
            kwargs["proxies"] = proxies
    else:
        adapter = _session_store.get_requests(name)
        kwargs = dict(headers=headers, data=data, timeout=timeout,
                      verify=verify, allow_redirects=False)
        if proxies:
            kwargs["proxies"] = proxies

    timings = {}

    def _resp_hook(resp, **kw):
        try:
            timings["elapsed"] = resp.elapsed.total_seconds()
        except Exception:
            pass

    chain = []
    current_url = url
    current_method = method
    current_data = data
    current_headers = dict(headers)
    final = None
    max_hops = 15

    t_start = time.time()
    for hop in range(max_hops):
        hop_start = time.time()

        req_kwargs = {k: v for k, v in kwargs.items()
                      if k not in ("headers", "data")}
        if engine in ("requests", "cloudscraper"):
            req_kwargs["hooks"] = {"response": _resp_hook}

        try:
            resp = adapter.request(
                current_method, current_url,
                headers=current_headers, data=current_data,
                **req_kwargs
            )
        except Exception as e:
            _log(f"request error on {current_method} {current_url}: {e}")
            raise

        hop_elapsed = time.time() - hop_start
        chain.append({
            "url": current_url,
            "method": current_method,
            "status": resp.status_code,
            "reason": getattr(resp, "reason", ""),
            "elapsed": hop_elapsed,
            "location": resp.headers.get("Location", ""),
        })
        if not allow_redirects or resp.status_code not in (301, 302, 303, 307, 308):
            final = resp
            break
        location = resp.headers.get("Location")
        if not location:
            final = resp
            break
        from urllib.parse import urljoin
        current_url = urljoin(current_url, location)
        if resp.status_code in (301, 302, 303) and current_method not in ("GET", "HEAD"):
            current_method = "GET"
            current_data = None
            current_headers.pop("Content-Type", None)
            current_headers.pop("Content-Length", None)
    else:
        final = resp

    total_elapsed = time.time() - t_start

    cookies = {}
    try:
        for c in adapter.cookies:
            cookies[c.name] = c.value
    except Exception as e:
        _log(f"cookie read failed: {e}")

    try:
        raw = final.content
    except Exception as e:
        _log(f"content read failed: {e}")
        raw = b""

    try:
        text = final.text
    except Exception:
        text = raw.decode("utf-8", errors="replace")

    ttfb = timings.get("elapsed")
    if ttfb is None:
        try:
            ttfb = getattr(final, "elapsed", None)
            if ttfb is not None and hasattr(ttfb, "total_seconds"):
                ttfb = ttfb.total_seconds()
        except Exception:
            ttfb = None

    return {
        "ok": True,
        "status": final.status_code,
        "reason": getattr(final, "reason", ""),
        "headers": dict(final.headers),
        "body": text,
        "raw": raw,
        "elapsed": total_elapsed,
        "ttfb": ttfb,
        "engine": engine,
        "redirect_chain": chain,
        "cookies": cookies,
    }


class RequestWorker(QThread):
    finished = Signal(dict)

    def __init__(self, **kwargs):
        super().__init__()
        self._kwargs = kwargs
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                self.finished.emit({"ok": False, "error": "cancelled",
                                    "engine": self._kwargs.get("engine")})
                return
            result = _dispatch_request(**self._kwargs)
            if self._cancelled:
                result = {"ok": False, "error": "cancelled",
                          "engine": self._kwargs.get("engine")}
        except Exception as e:
            _log(f"RequestWorker exception: {e}")
            result = {"ok": False, "error": str(e),
                      "engine": self._kwargs.get("engine")}
        self.finished.emit(result)


class CookieEditorDialog(QDialog):
    def __init__(self, parent, session_name, session):
        super().__init__(parent)
        self.setWindowTitle(tr("cookies.editor.title", module="http_client"))
        self.setMinimumSize(620, 420)
        self._session = session
        self._session_name = session_name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            tr("cookies.col.name", module="http_client"),
            tr("cookies.col.value", module="http_client"),
            tr("cookies.col.domain", module="http_client"),
            tr("cookies.col.path", module="http_client"),
            tr("cookies.col.expires", module="http_client"),
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked
                                    | QAbstractItemView.EditKeyPressed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        btns = QHBoxLayout()
        add_btn = QPushButton(tr("cookies.add", module="http_client"))
        add_btn.clicked.connect(self._add_row)
        btns.addWidget(add_btn)

        del_btn = QPushButton(tr("cookies.delete_row", module="http_client"))
        del_btn.clicked.connect(self._delete_row)
        btns.addWidget(del_btn)

        imp_btn = QPushButton(tr("cookies.import", module="http_client"))
        imp_btn.clicked.connect(self._import_json)
        btns.addWidget(imp_btn)

        exp_btn = QPushButton(tr("cookies.export", module="http_client"))
        exp_btn.clicked.connect(self._export_json)
        btns.addWidget(exp_btn)

        btns.addStretch()

        save_btn = QPushButton(tr("cookies.apply", module="http_client"))
        save_btn.clicked.connect(self._apply)
        btns.addWidget(save_btn)

        cancel_btn = QPushButton(tr("cookies.cancel", module="http_client"))
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)

        layout.addLayout(btns)

        self._load()

    def _load(self):
        try:
            items = list(self._session.cookies)
        except Exception as e:
            _log(f"cookie dialog load failed: {e}")
            items = []
        self.table.setRowCount(len(items))
        for i, c in enumerate(items):
            self.table.setItem(i, 0, QTableWidgetItem(c.name or ""))
            self.table.setItem(i, 1, QTableWidgetItem(c.value or ""))
            self.table.setItem(i, 2, QTableWidgetItem(c.domain or ""))
            self.table.setItem(i, 3, QTableWidgetItem(c.path or "/"))
            exp = ""
            try:
                if c.expires:
                    exp = str(c.expires)
            except Exception:
                exp = ""
            self.table.setItem(i, 4, QTableWidgetItem(exp))

    def _add_row(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(""))
        self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.setItem(row, 2, QTableWidgetItem(""))
        self.table.setItem(row, 3, QTableWidgetItem("/"))
        self.table.setItem(row, 4, QTableWidgetItem(""))

    def _delete_row(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        self.table.removeRow(sel[0].row())

    def _import_json(self):
        path, _ = QFileDialog.getOpenFileName(
            parent_widget(self), tr("cookies.import.title", module="http_client"),
            "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(parent_widget(self),
                                tr("msg.error.title", module="http_client"), str(e))
            return
        if not isinstance(data, dict):
            return
        for k, v in data.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(k)))
            self.table.setItem(row, 1, QTableWidgetItem(str(v)))
            self.table.setItem(row, 2, QTableWidgetItem(""))
            self.table.setItem(row, 3, QTableWidgetItem("/"))
            self.table.setItem(row, 4, QTableWidgetItem(""))

    def _export_json(self):
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self), tr("cookies.export.title", module="http_client"),
            "cookies.json", "JSON (*.json)")
        if not path:
            return
        out = {}
        for i in range(self.table.rowCount()):
            k = self.table.item(i, 0)
            v = self.table.item(i, 1)
            if k and v and k.text():
                out[k.text()] = v.text()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(parent_widget(self),
                                tr("msg.error.title", module="http_client"), str(e))

    def _apply(self):
        try:
            self._session.cookies.clear()
        except Exception:
            pass
        for i in range(self.table.rowCount()):
            name = self.table.item(i, 0)
            value = self.table.item(i, 1)
            if not name or not value:
                continue
            n = name.text().strip()
            v = value.text()
            if not n:
                continue
            try:
                self._session.cookies.set(n, v, domain=None, path="/")
            except Exception as e:
                _log(f"cookie set failed for {n}: {e}")
        self.accept()


class SessionsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(tr("sessions.title", module="http_client"))
        self.setMinimumSize(520, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        info = QLabel(tr("sessions.hint", module="http_client"))
        info.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([
            tr("sessions.col.key", module="http_client"),
            tr("sessions.col.engine", module="http_client"),
            tr("sessions.col.cookies", module="http_client"),
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        btns = QHBoxLayout()
        refresh_btn = QPushButton(tr("sessions.refresh", module="http_client"))
        refresh_btn.clicked.connect(self._refresh)
        btns.addWidget(refresh_btn)

        del_btn = QPushButton(tr("sessions.clear_one", module="http_client"))
        del_btn.clicked.connect(self._clear_one)
        btns.addWidget(del_btn)

        del_all_btn = QPushButton(tr("sessions.clear_all", module="http_client"))
        del_all_btn.clicked.connect(self._clear_all)
        btns.addWidget(del_all_btn)

        btns.addStretch()
        close_btn = QPushButton(tr("sessions.close", module="http_client"))
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        self._refresh()

    def _refresh(self):
        items = _session_store.list()
        self.table.setRowCount(len(items))
        for i, s in enumerate(items):
            self.table.setItem(i, 0, QTableWidgetItem(s["key"]))
            self.table.setItem(i, 1, QTableWidgetItem(s["engine"]))
            self.table.setItem(i, 2, QTableWidgetItem(str(s["cookies"])))

    def _selected_key(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return None
        return self.table.item(sel[0].row(), 0).text()

    def _clear_one(self):
        key = self._selected_key()
        if not key:
            return
        if _session_store.clear_one(key):
            self._refresh()

    def _clear_all(self):
        if QMessageBox.question(
            parent_widget(self),
            tr("sessions.clear_all.confirm.title", module="http_client"),
            tr("sessions.clear_all.confirm.text", module="http_client"),
        ) != QMessageBox.Yes:
            return
        _session_store.clear_all()
        self._refresh()


class HttpClientModule(BaseModule):
    TEMPLATE_HEADERS = (
        "# One header per line:  Key: Value\n"
        "Authorization: Bearer <YOUR_TOKEN>\n"
        "Content-Type: application/json\n"
        "Origin: https://<YOUR_DOMAIN>\n"
        "x-client-name: web"
    )
    TEMPLATE_BODY = '{\n  "id": 0,\n  "text": "example"\n}'
    TEMPLATE_URL = "https://example.com/api/v1/endpoint"

    PROFILES_DIR = CONFIG_ROOT / "profiles" / "http_client"
    SNIPPETS_FILE = CONFIG_ROOT / "snippets" / "http_client.json"
    COOKIES_FILE = CONFIG_ROOT / "profiles" / "http_client" / "_cookies.json"

    def __init__(self):
        super().__init__()
        self.settings = get_settings()
        self.history = get_history()
        self._widget = None
        self._snippets = []
        self.worker = None
        self._lbls = {}
        self._btns = {}
        self._last_response = None

    def on_shutdown(self):
        w = getattr(self, "worker", None)
        if w is not None:
            try:
                w.cancel()
                w.wait(3000)
            except Exception as e:
                _log(f"worker shutdown failed: {e}")
        self.worker = None
        _session_store.clear_all()

    def on_load(self, api):
        api.register_service(self.name, "http.request", self._svc_request,
                             "http.request(method, url, headers, body, timeout, "
                             "verify, allow_redirects, engine, proxies, "
                             "session_name, caller, session) -> dict")
        api.register_service(self.name, "http.curl.parse", self._svc_curl_parse,
                             "http.curl.parse(cmd) -> dict")
        api.register_service(self.name, "http.curl.build", self._svc_curl_build,
                             "http.curl.build(method, url, headers, body) -> str")
        api.register_service(self.name, "http.profile.save", self._svc_profile_save,
                             "http.profile.save(name, data) -> bool")
        api.register_service(self.name, "http.profile.load", self._svc_profile_load,
                             "http.profile.load(name) -> dict")
        api.register_service(self.name, "http.profile.list", self._svc_profile_list,
                             "http.profile.list() -> list[str]")
        api.register_service(self.name, "http.profile.delete", self._svc_profile_delete,
                             "http.profile.delete(name) -> bool")
        api.register_service(self.name, "http.snippet.save", self._svc_snippet_save,
                             "http.snippet.save(name, body) -> bool")
        api.register_service(self.name, "http.snippet.load", self._svc_snippet_load,
                             "http.snippet.load(name) -> str")
        api.register_service(self.name, "http.snippet.list", self._svc_snippet_list,
                             "http.snippet.list() -> list[str]")
        api.register_service(self.name, "http.snippet.delete", self._svc_snippet_delete,
                             "http.snippet.delete(name) -> bool")
        api.register_service(self.name, "http.engines", self._svc_engines,
                             "http.engines() -> list[str]")
        api.register_service(self.name, "http.sessions.list", self._svc_sessions_list,
                             "http.sessions.list() -> list[dict]")
        api.register_service(self.name, "http.sessions.clear_one",
                             self._svc_sessions_clear_one,
                             "http.sessions.clear_one(key) -> bool")
        api.register_service(self.name, "http.sessions.clear",
                             self._svc_sessions_clear,
                             "http.sessions.clear() -> bool")

    def _svc_request(self, method, url, headers=None, body=None,
                     timeout=30, verify=True, allow_redirects=True,
                     engine="requests", proxies=None,
                     session_name=None, caller=None, session=None):
        try:
            result = _dispatch_request(
                engine=engine, method=method, url=url,
                headers=headers or {}, body=body or "", proxies=proxies,
                timeout=timeout, verify=verify, allow_redirects=allow_redirects,
                impersonate=self.settings.get("http_client", "impersonate",
                                              default="chrome"),
                session_name=session_name,
            )
        except Exception as e:
            _log(f"svc_request exception: {e}")
            result = {"ok": False, "error": str(e), "engine": engine}

        result.pop("raw", None)
        try:
            mod_label = caller or self.name
            if result.get("ok"):
                self.history.add(
                    module=mod_label, session=session,
                    method=method, url=url,
                    headers=headers or {}, body=body or "",
                    status=result.get("status"),
                    response_body=result.get("body"),
                    elapsed=result.get("elapsed"),
                )
            else:
                self.history.add(
                    module=mod_label, session=session,
                    method=method, url=url,
                    headers=headers or {}, body=body or "",
                    error=result.get("error"),
                )
        except Exception as e:
            _log(f"history write failed: {e}")
        return result

    def _svc_curl_parse(self, cmd):
        return self._parse_curl(cmd)

    def _svc_curl_build(self, method, url, headers=None, body=None):
        return self._build_curl(method=method, url=url,
                                headers_raw=json.dumps(headers or {}, ensure_ascii=False),
                                body=body or "")

    def _svc_profile_save(self, name, data):
        self.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.PROFILES_DIR / f"{name}.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            _log(f"profile save failed: {e}")
            return False

    def _svc_profile_load(self, name):
        path = self.PROFILES_DIR / f"{name}.json"
        if not path.exists():
            return {"ok": False, "error": "not_found"}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return {"ok": True, "data": json.load(f)}
        except Exception as e:
            _log(f"profile load failed: {e}")
            return {"ok": False, "error": str(e)}

    def _svc_profile_list(self):
        if not self.PROFILES_DIR.is_dir():
            return []
        INTERNAL = {"_cookies"}
        return sorted(
            f[:-5] for f in os.listdir(self.PROFILES_DIR)
            if f.endswith(".json") and f[:-5] not in INTERNAL
        )

    def _svc_profile_delete(self, name):
        path = self.PROFILES_DIR / f"{name}.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except Exception as e:
            _log(f"profile delete failed: {e}")
            return False

    def _svc_snippet_save(self, name, body):
        snippets = self._load_snippets_raw()
        snippets = [s for s in snippets if s.get("name") != name]
        snippets.append({"name": name, "body": body})
        return self._write_snippets_raw(snippets)

    def _svc_snippet_load(self, name):
        for s in self._load_snippets_raw():
            if s.get("name") == name:
                return s.get("body", "")
        return None

    def _svc_snippet_list(self):
        return [s.get("name") for s in self._load_snippets_raw()]

    def _svc_snippet_delete(self, name):
        snippets = self._load_snippets_raw()
        new = [s for s in snippets if s.get("name") != name]
        if len(new) == len(snippets):
            return False
        self._write_snippets_raw(new)
        return True

    def _svc_engines(self):
        engines = ["requests"]
        try:
            import curl_cffi
            engines.append("curl_cffi")
        except ImportError:
            pass
        try:
            import cloudscraper
            engines.append("cloudscraper")
        except ImportError:
            pass
        return engines

    def _svc_sessions_list(self):
        return _session_store.list()

    def _svc_sessions_clear_one(self, key):
        return _session_store.clear_one(key)

    def _svc_sessions_clear(self):
        _session_store.clear_all()
        return True

    def _load_snippets_raw(self):
        if not self.SNIPPETS_FILE.exists():
            return []
        try:
            with open(self.SNIPPETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            _log(f"snippets read failed: {e}")
            return []

    def _write_snippets_raw(self, snippets):
        self.SNIPPETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.SNIPPETS_FILE, "w", encoding="utf-8") as f:
                json.dump(snippets, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            _log(f"snippets write failed: {e}")
            return False

    def create_widget(self):
        root = QWidget()
        self._widget = root

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.mono = QFont("Consolas", 10)
        self.settings = get_settings()
        self.history = get_history()

        layout.addLayout(self._build_profiles_bar())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_request_tab(), "")
        self.tabs.addTab(self._build_response_tab(), "")
        self.tabs.addTab(self._build_redirects_tab(), "")
        self.tabs.addTab(self._build_html_tab(), "")
        self.tabs.addTab(self._build_history_tab(), "")
        self.tabs.addTab(self._build_cookies_tab(), "")
        layout.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()

        self.copy_curl_btn = QPushButton()
        self.copy_curl_btn.setFixedWidth(180)
        self.copy_curl_btn.clicked.connect(self._copy_request_as_curl)
        bottom.addWidget(self.copy_curl_btn)

        self.copy_resp_btn = QPushButton()
        self.copy_resp_btn.setFixedWidth(180)
        self.copy_resp_btn.clicked.connect(self._copy_response)
        bottom.addWidget(self.copy_resp_btn)

        self.clear_btn = QPushButton()
        self.clear_btn.setFixedWidth(140)
        self.clear_btn.clicked.connect(self._clear_all)
        bottom.addWidget(self.clear_btn)

        self.send_btn = QPushButton()
        self.send_btn.setIcon(theme_loader.get_icon(icon_name="play", emoji=">", size=14))
        self.send_btn.setFixedWidth(220)
        self.send_btn.clicked.connect(self._send_or_cancel)
        bottom.addWidget(self.send_btn)

        layout.addLayout(bottom)

        self._refresh_profiles()
        self._refresh_snippets()
        self._refresh_history()
        self._refresh_cookies_view()
        self._refresh_sessions_label()

        self.retranslate()
        return root

    def retranslate(self):
        def t(k, **kw):
            return tr(k, module="http_client", **kw)
        if not hasattr(self, "tabs"):
            return
        self.tabs.setTabText(0, t("tab.request"))
        self.tabs.setTabText(1, t("tab.response"))
        self.tabs.setTabText(2, t("tab.redirects"))
        self.tabs.setTabText(3, t("tab.html"))
        self.tabs.setTabText(4, t("tab.history"))
        self.tabs.setTabText(5, t("tab.cookies"))
        self._lbls["profile"].setText(t("profile"))
        self._btns["profile_save"].setText(t("profile.save"))
        self._btns["profile_export"].setText(t("profile.export"))
        self._btns["profile_import"].setText(t("profile.import"))
        self._btns["profile_delete"].setText(t("profile.delete"))
        self._btns["profile_reset"].setText(t("profile.reset"))
        self._lbls["method"].setText(t("method"))
        self._lbls["url"].setText(t("url"))
        self._btns["import_curl"].setText(t("import_curl"))
        self._lbls["headers_lbl"].setText(t("headers"))
        self._lbls["body_lbl"].setText(t("body"))
        self._lbls["snippet_lbl"].setText(t("snippet"))
        self._btns["snippet_save"].setText(t("snippet.save"))
        self._btns["snippet_delete"].setText(t("snippet.delete"))
        self.options_group.setTitle(t("options.title"))
        self.engine_lbl.setText(t("options.engine"))
        self.verify_chk.setText(t("options.verify"))
        self.redirect_chk.setText(t("options.redirect"))
        self.timeout_lbl.setText(t("options.timeout"))
        self.use_proxy_chk.setText(t("options.proxy"))
        self.session_lbl.setText(t("options.session"))
        self.session_input.setPlaceholderText(t("options.session.placeholder"))
        self.status_label.setText(t("status.none"))
        self.html_title_lbl.setText(t("html.title"))
        self.history_title_lbl.setText(t("history.title"))
        self._btns["hist_refresh"].setText(t("history.refresh"))
        self._btns["hist_load"].setText(t("history.load"))
        self._btns["hist_copy"].setText(t("history.copy_curl"))
        self._btns["hist_clear"].setText(t("history.clear"))
        self.history_table.setHorizontalHeaderLabels([
            t("history.col.id"), t("history.col.method"), t("history.col.url"),
            t("history.col.status"), t("history.col.time"), t("history.col.created"),
        ])
        self.redirects_table.setHorizontalHeaderLabels([
            t("redirects.col.num"), t("redirects.col.status"),
            t("redirects.col.method"), t("redirects.col.url"),
            t("redirects.col.location"), t("redirects.col.time")])
        self.cookies_title.setText(t("cookies.title"))
        self.cookies_hint.setText(t("cookies.hint"))
        self._btns["cookies_refresh"].setText(t("cookies.refresh"))
        self._btns["cookies_clear"].setText(t("cookies.clear"))
        self._btns["cookies_save"].setText(t("cookies.save"))
        self._btns["cookies_edit"].setText(t("cookies.edit"))
        self._btns["cookies_sessions"].setText(t("cookies.sessions"))
        self.cookies_table.setHorizontalHeaderLabels([
            t("cookies.col.name"), t("cookies.col.value")])
        self.binary_label.setText(t("binary.hint"))
        self.copy_curl_btn.setText(t("bottom.copy_curl"))
        self.copy_resp_btn.setText(t("bottom.copy_response"))
        self.clear_btn.setText(t("bottom.clear"))
        if self.worker is not None and self.worker.isRunning():
            self.send_btn.setText(t("bottom.cancel"))
        else:
            self.send_btn.setText(t("bottom.send"))
        self.auth_lbl.setText(t("auth.label"))
        self._btns["auth_apply"].setText(t("auth.apply"))

    def _build_profiles_bar(self):
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._lbls["profile"] = QLabel()
        self._lbls["profile"].setStyleSheet(f"color: {_c('fg_dim')};")
        bar.addWidget(self._lbls["profile"])
        self.profile_box = QComboBox()
        self.profile_box.setFixedWidth(180)
        self.profile_box.currentTextChanged.connect(self._load_profile)
        bar.addWidget(self.profile_box)
        for key, text in (
            ("profile_save", self._save_profile_as),
            ("profile_export", self._export_profile),
            ("profile_import", self._import_profile),
            ("profile_delete", self._delete_profile),
        ):
            b = QPushButton()
            b.setFixedWidth(110)
            b.clicked.connect(text)
            self._btns[key] = b
            bar.addWidget(b)
        bar.addStretch()
        self._btns["profile_reset"] = QPushButton()
        self._btns["profile_reset"].setFixedWidth(130)
        self._btns["profile_reset"].clicked.connect(self._reset_template)
        bar.addWidget(self._btns["profile_reset"])
        return bar

    def _build_request_tab(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        row = QHBoxLayout()
        self._lbls["method"] = QLabel()
        self._lbls["method"].setStyleSheet(f"color: {_c('fg_dim')};")
        row.addWidget(self._lbls["method"])
        self.method_box = QComboBox()
        self.method_box.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_box.setCurrentText("GET")
        self.method_box.setFixedWidth(110)
        row.addWidget(self.method_box)
        self._lbls["url"] = QLabel()
        self._lbls["url"].setStyleSheet(f"color: {_c('fg_dim')};")
        row.addWidget(self._lbls["url"])
        self.url_input = QLineEdit(self.TEMPLATE_URL)
        self.url_input.setFont(self.mono)
        row.addWidget(self.url_input, 1)
        self._btns["import_curl"] = QPushButton()
        self._btns["import_curl"].setFixedWidth(150)
        self._btns["import_curl"].clicked.connect(self._import_curl_dialog)
        row.addWidget(self._btns["import_curl"])
        outer.addLayout(row)

        auth_row = QHBoxLayout()
        self.auth_lbl = QLabel()
        self.auth_lbl.setStyleSheet(f"color: {_c('fg_dim')};")
        auth_row.addWidget(self.auth_lbl)
        self.auth_box = QComboBox()
        self.auth_box.addItems(["none", "bearer", "basic", "apikey_header"])
        self.auth_box.setFixedWidth(160)
        auth_row.addWidget(self.auth_box)
        self.auth_value = QLineEdit()
        self.auth_value.setFont(self.mono)
        self.auth_value.setPlaceholderText("<token/credentials>")
        auth_row.addWidget(self.auth_value, 1)
        self._btns["auth_apply"] = QPushButton()
        self._btns["auth_apply"].setFixedWidth(140)
        self._btns["auth_apply"].clicked.connect(self._apply_auth)
        auth_row.addWidget(self._btns["auth_apply"])
        outer.addLayout(auth_row)

        split = QSplitter(Qt.Vertical)

        h_widget = QWidget()
        h_layout = QVBoxLayout(h_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(4)
        self._lbls["headers_lbl"] = QLabel()
        self._lbls["headers_lbl"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        h_layout.addWidget(self._lbls["headers_lbl"])
        self.headers_edit = QPlainTextEdit()
        self.headers_edit.setFont(self.mono)
        self.headers_edit.setPlainText(self.TEMPLATE_HEADERS)
        h_layout.addWidget(self.headers_edit)

        b_widget = QWidget()
        b_layout = QVBoxLayout(b_widget)
        b_layout.setContentsMargins(0, 0, 0, 0)
        b_layout.setSpacing(4)
        b_top = QHBoxLayout()
        self._lbls["body_lbl"] = QLabel()
        self._lbls["body_lbl"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        b_top.addWidget(self._lbls["body_lbl"])
        b_top.addStretch()
        self._lbls["snippet_lbl"] = QLabel()
        self._lbls["snippet_lbl"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        b_top.addWidget(self._lbls["snippet_lbl"])
        self.snippet_box = QComboBox()
        self.snippet_box.setFixedWidth(180)
        self.snippet_box.currentTextChanged.connect(self._load_snippet)
        b_top.addWidget(self.snippet_box)
        self._btns["snippet_save"] = QPushButton()
        self._btns["snippet_save"].setFixedWidth(160)
        self._btns["snippet_save"].clicked.connect(self._save_snippet)
        b_top.addWidget(self._btns["snippet_save"])
        self._btns["snippet_delete"] = QPushButton()
        self._btns["snippet_delete"].setFixedWidth(100)
        self._btns["snippet_delete"].clicked.connect(self._delete_snippet)
        b_top.addWidget(self._btns["snippet_delete"])
        b_layout.addLayout(b_top)
        self.body_edit = QPlainTextEdit()
        self.body_edit.setFont(self.mono)
        self.body_edit.setPlainText(self.TEMPLATE_BODY)
        b_layout.addWidget(self.body_edit)

        split.addWidget(h_widget)
        split.addWidget(b_widget)
        split.setSizes([220, 320])
        outer.addWidget(split, 1)
        outer.addWidget(self._build_options_group())
        return page

    def _build_options_group(self):
        self.options_group = QGroupBox()
        g = self.options_group
        form = QFormLayout(g)
        s = get_settings()

        def lr(key, default):
            return s.get("http_client", "last_request", key, default=default)

        self.engine_box = QComboBox()
        self.engine_box.addItems(["requests", "curl_cffi", "cloudscraper"])
        self.engine_box.setCurrentText(str(lr("engine", "requests")))
        self.engine_lbl = QLabel()
        form.addRow(self.engine_lbl, self.engine_box)

        self.verify_chk = QCheckBox()
        self.verify_chk.setChecked(bool(lr("verify_ssl", True)))
        form.addRow(self.verify_chk)

        self.redirect_chk = QCheckBox()
        self.redirect_chk.setChecked(bool(lr("follow_redirects", True)))
        form.addRow(self.redirect_chk)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 600)
        try:
            self.timeout_spin.setValue(int(lr("timeout", 30)))
        except Exception:
            self.timeout_spin.setValue(30)
        self.timeout_lbl = QLabel()
        form.addRow(self.timeout_lbl, self.timeout_spin)

        self.use_proxy_chk = QCheckBox()
        self.use_proxy_chk.setChecked(bool(lr("use_proxy", False)))
        form.addRow(self.use_proxy_chk)

        self.session_lbl = QLabel()
        self.session_input = QLineEdit("default")
        self.session_input.setFont(self.mono)
        form.addRow(self.session_lbl, self.session_input)

        return g

    def _build_response_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        self.status_label = QLabel()
        self.status_label.setStyleSheet(
            f"color: {_c('fg')}; font-size: 12px; font-weight: bold;")
        layout.addWidget(self.status_label)

        inner = QTabWidget()
        self.resp_headers_edit = QPlainTextEdit()
        self.resp_headers_edit.setFont(self.mono)
        self.resp_headers_edit.setReadOnly(True)
        inner.addTab(self.resp_headers_edit, "Headers")

        self.resp_body_edit = QPlainTextEdit()
        self.resp_body_edit.setFont(self.mono)
        self.resp_body_edit.setReadOnly(True)
        self.resp_body_edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.resp_body_edit.customContextMenuRequested.connect(
            self._body_context_menu)
        inner.addTab(self.resp_body_edit, "Body")

        self.resp_hex_edit = QPlainTextEdit()
        self.resp_hex_edit.setFont(self.mono)
        self.resp_hex_edit.setReadOnly(True)
        inner.addTab(self.resp_hex_edit, "Hex")

        self.binary_label = QLabel()
        self.binary_label.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bin_w = QWidget()
        bl = QVBoxLayout(bin_w)
        bl.addWidget(self.binary_label)
        bl.addWidget(self.resp_hex_edit)
        inner.removeTab(2)
        inner.addTab(bin_w, "Hex")

        layout.addWidget(inner, 1)
        return page

    def _build_redirects_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.redirects_table = QTableWidget()
        self.redirects_table.setColumnCount(6)
        self.redirects_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.redirects_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.redirects_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.redirects_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.redirects_table)
        return page

    def _build_html_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        bar = QHBoxLayout()
        self.html_title_lbl = QLabel()
        self.html_title_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self.html_title_lbl)
        bar.addStretch()
        self.html_info = QLabel("-")
        self.html_info.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self.html_info)
        layout.addLayout(bar)
        self.html_tree = QTreeWidget()
        self.html_tree.setHeaderLabels(["TAG / ATTRS", "VALUE"])
        self.html_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.html_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.html_tree, 1)
        return page

    def _build_history_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.history_title_lbl = QLabel()
        self.history_title_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self.history_title_lbl)
        bar.addStretch()
        for k, slot, w in (
            ("hist_refresh", self._refresh_history, 130),
            ("hist_load", self._load_history_selected, 120),
            ("hist_copy", self._copy_history_as_curl, 150),
            ("hist_clear", self._clear_history, 140),
        ):
            b = QPushButton()
            b.setFixedWidth(w)
            b.clicked.connect(slot)
            self._btns[k] = b
            bar.addWidget(b)
        layout.addLayout(bar)

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(6)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.history_table.doubleClicked.connect(self._load_history_selected)
        layout.addWidget(self.history_table, 1)
        return page

    def _build_cookies_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.cookies_title = QLabel()
        self.cookies_title.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.cookies_title)

        self.cookies_hint = QLabel()
        self.cookies_hint.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 10px;")
        self.cookies_hint.setWordWrap(True)
        layout.addWidget(self.cookies_hint)

        row = QHBoxLayout()
        for k, slot, w in (
            ("cookies_refresh", self._refresh_cookies_view, 130),
            ("cookies_save", self._save_cookies, 130),
            ("cookies_clear", self._clear_cookies, 130),
            ("cookies_edit", self._open_cookie_editor, 160),
            ("cookies_sessions", self._open_sessions_dialog, 180),
        ):
            b = QPushButton()
            b.setFixedWidth(w)
            b.clicked.connect(slot)
            self._btns[k] = b
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)

        self.cookies_table = QTableWidget()
        self.cookies_table.setColumnCount(2)
        self.cookies_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cookies_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.cookies_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.cookies_table, 1)
        return page

    def _body_context_menu(self, pos):
        menu = QMenu(self._widget)
        cursor = self.resp_body_edit.textCursor()
        selected = cursor.selectedText()
        act_copy = QAction(tr("ctx.copy", module="http_client"), menu)
        act_copy.triggered.connect(
            lambda: QGuiApplication.clipboard().setText(
                selected or self.resp_body_edit.toPlainText()))
        menu.addAction(act_copy)
        menu.addSeparator()
        text_to_send = selected or self.resp_body_edit.toPlainText()

        act_encoder = QAction(tr("ctx.to_encoder", module="http_client"), menu)
        act_encoder.triggered.connect(
            lambda: self._send_to("encoder", text_to_send))
        menu.addAction(act_encoder)

        act_diff = QAction(tr("ctx.to_diff", module="http_client"), menu)
        act_diff.triggered.connect(lambda: self._send_to("diff", text_to_send))
        menu.addAction(act_diff)

        act_jwt = QAction(tr("ctx.to_jwt", module="http_client"), menu)
        act_jwt.triggered.connect(lambda: self._send_to("jwt_decoder", text_to_send))
        menu.addAction(act_jwt)

        act_vault = QAction(tr("ctx.to_vault", module="http_client"), menu)
        act_vault.triggered.connect(
            lambda: self._send_to_vault(text_to_send))
        menu.addAction(act_vault)

        menu.addSeparator()
        act_save = QAction(tr("ctx.save_file", module="http_client"), menu)
        act_save.triggered.connect(self._save_response_to_file)
        menu.addAction(act_save)

        menu.exec_(self.resp_body_edit.mapToGlobal(pos))

    def _send_to(self, module_key, text):
        try:
            get_bus().emit_event("ui.send_text",
                                 target=module_key, text=text)
            QMessageBox.information(
                parent_widget(self._widget),
                tr("ctx.title", module="http_client"),
                tr("ctx.sent", module="http_client", target=module_key))
        except Exception as e:
            _log(f"send_to failed: {e}")
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"), str(e))

    def _send_to_vault(self, text):
        key, ok = QInputDialog.getText(
            parent_widget(self._widget),
            tr("ctx.vault.title", module="http_client"),
            tr("ctx.vault.key", module="http_client"))
        if not ok or not key.strip():
            return
        try:
            get_bus().call("vault.set", key.strip(), text)
            QMessageBox.information(
                parent_widget(self._widget),
                tr("ctx.title", module="http_client"),
                tr("ctx.vault.saved", module="http_client", key=key.strip()))
        except Exception as e:
            _log(f"vault set failed: {e}")
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"), str(e))

    def _save_response_to_file(self):
        if not self._last_response:
            return
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("ctx.save_file", module="http_client"),
            "response.bin", "All (*)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(self._last_response.get("raw") or b"")
        except Exception as e:
            _log(f"save response failed: {e}")
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"), str(e))

    def _apply_auth(self):
        kind = self.auth_box.currentText()
        if kind == "none":
            return
        val = self.auth_value.text()
        if not val:
            return
        line = ""
        if kind == "bearer":
            line = f"Authorization: Bearer {val}"
        elif kind == "basic":
            line = f"Authorization: Basic {val}"
        elif kind == "apikey_header":
            line = f"X-API-Key: {val}"
        current = self.headers_edit.toPlainText()
        lines = [l for l in current.splitlines()
                 if not l.lower().startswith("authorization:")
                 and not l.lower().startswith("x-api-key:")]
        lines.append(line)
        self.headers_edit.setPlainText("\n".join(lines))

    def _reset_template(self):
        self.url_input.setText(self.TEMPLATE_URL)
        self.headers_edit.setPlainText(self.TEMPLATE_HEADERS)
        self.body_edit.setPlainText(self.TEMPLATE_BODY)
        self.method_box.setCurrentText("GET")
        self.profile_box.setCurrentText("")

    def _clear_all(self):
        self.resp_headers_edit.clear()
        self.resp_body_edit.clear()
        self.resp_hex_edit.clear()
        self.redirects_table.setRowCount(0)
        self.html_tree.clear()
        self.html_info.setText("-")
        self.status_label.setText(tr("status.none", module="http_client"))
        self.status_label.setStyleSheet(
            f"color: {_c('fg')}; font-size: 12px; font-weight: bold;")

    def _parse_headers(self):
        raw = self.headers_edit.toPlainText()
        resolved = _resolve_vault_placeholders(raw)
        return _parse_headers(resolved)

    def _persist_request_options(self):
        try:
            self.settings.update(
                "http_client", "last_request",
                values={
                    "engine": self.engine_box.currentText(),
                    "timeout": self.timeout_spin.value(),
                    "verify_ssl": self.verify_chk.isChecked(),
                    "follow_redirects": self.redirect_chk.isChecked(),
                    "use_proxy": self.use_proxy_chk.isChecked(),
                },
            )
        except Exception as e:
            _log(f"persist options failed: {e}")

    def _send_or_cancel(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.send_btn.setEnabled(False)
            self.status_label.setText(tr("status.cancelling", module="http_client"))
            return
        self._send()

    def _send(self):
        url = _resolve_vault_placeholders(self.url_input.text().strip())
        if not url:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"),
                                tr("msg.url_empty", module="http_client"))
            return
        if "<" in url or ">" in url:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"),
                                tr("msg.url_placeholder", module="http_client"))
            return

        method = self.method_box.currentText()
        headers = self._parse_headers()
        body = _resolve_vault_placeholders(self.body_edit.toPlainText())
        engine = self.engine_box.currentText()

        bad_hdr = _validate_headers(headers)
        if bad_hdr is not None:
            k, v = bad_hdr
            QMessageBox.warning(
                parent_widget(self._widget),
                tr("msg.bad_header.title", module="http_client"),
                tr("msg.bad_header.text", module="http_client",
                   key=k, value=v[:80]))
            return

        if headers.get("Content-Type", "").lower().startswith("application/json") and body.strip():
            try:
                json.loads(body)
            except Exception as e:
                QMessageBox.warning(parent_widget(self._widget),
                                    tr("msg.bad_json.title", module="http_client"),
                                    str(e))
                return

        proxies = None
        if self.use_proxy_chk.isChecked():
            proxies = self.settings.get_requests_proxies()

        if self.worker is not None and self.worker.isRunning():
            return

        self._persist_request_options()
        self.send_btn.setEnabled(False)
        self.send_btn.setText(tr("bottom.sending", module="http_client"))
        self.status_label.setText(tr("status.sending", module="http_client"))
        self.status_label.setStyleSheet(
            f"color: {_c('warning')}; font-size: 12px; font-weight: bold;")
        self.resp_headers_edit.clear()
        self.resp_body_edit.clear()
        self.resp_hex_edit.clear()
        self.redirects_table.setRowCount(0)
        self.html_tree.clear()

        self.worker = RequestWorker(
            engine=engine, method=method, url=url, headers=headers, body=body,
            proxies=proxies,
            timeout=self.timeout_spin.value(),
            verify=self.verify_chk.isChecked(),
            allow_redirects=self.redirect_chk.isChecked(),
            impersonate=self.settings.get("http_client", "impersonate", default="chrome"),
            session_name=self.session_input.text().strip() or "default",
        )
        self.worker.finished.connect(self._on_response)
        self.worker.start()

    def _on_response(self, data):
        self.send_btn.setEnabled(True)
        self.send_btn.setText(tr("bottom.send", module="http_client"))
        self.worker = None

        if not data.get("ok"):
            err = data.get("error") or "unknown error"
            if err == "cancelled":
                self.status_label.setText(tr("status.cancelled", module="http_client"))
                self.status_label.setStyleSheet(
                    f"color: {_c('warning')}; font-size: 12px; font-weight: bold;")
                return
            self.status_label.setText(tr("status.error", module="http_client",
                                          engine=data.get("engine")))
            self.status_label.setStyleSheet(
                f"color: {_c('danger')}; font-size: 12px; font-weight: bold;")
            self.resp_body_edit.setPlainText(f"ERROR:\n{err}")
            try:
                self.history.add(
                    module=self.name, method=self.method_box.currentText(),
                    url=self.url_input.text(), headers=self._parse_headers(),
                    body=self.body_edit.toPlainText(), error=err)
            except Exception as e:
                _log(f"history write failed: {e}")
            self._refresh_history()
            return

        status = data["status"]
        color_ = (_c("success") if 200 <= status < 300
                  else _c("warning") if 300 <= status < 400
                  else _c("danger"))
        ttfb = data.get("ttfb")
        timing = f"{data['elapsed']*1000:.0f} ms"
        if ttfb is not None:
            timing += f" (TTFB {ttfb*1000:.0f} ms)"
        self.status_label.setText(tr(
            "status.ok", module="http_client",
            code=status, reason=data.get("reason", ""),
            ms=timing, engine=data.get("engine", "")))
        self.status_label.setStyleSheet(
            f"color: {color_}; font-size: 12px; font-weight: bold;")

        h_text = "".join(f"{k}: {v}\n" for k, v in data["headers"].items())
        self.resp_headers_edit.setPlainText(h_text)

        body = data["body"]
        try:
            parsed = json.loads(body)
            self.resp_body_edit.setPlainText(
                json.dumps(parsed, indent=2, ensure_ascii=False))
        except Exception:
            self.resp_body_edit.setPlainText(body)

        raw = data.get("raw") or b""
        self.resp_hex_edit.setPlainText(self._hex_dump(raw))
        self.binary_label.setText(
            tr("binary.info", module="http_client", size=len(raw)))

        chain = data.get("redirect_chain") or []
        self.redirects_table.setRowCount(len(chain))
        for i, hop in enumerate(chain):
            self.redirects_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            s_item = QTableWidgetItem(str(hop["status"]))
            sc = hop["status"]
            s_item.setForeground(QColor(
                _c("success") if 200 <= sc < 300
                else _c("warning") if 300 <= sc < 400
                else _c("danger")))
            self.redirects_table.setItem(i, 1, s_item)
            self.redirects_table.setItem(i, 2, QTableWidgetItem(hop["method"]))
            self.redirects_table.setItem(i, 3, QTableWidgetItem(hop["url"]))
            self.redirects_table.setItem(i, 4, QTableWidgetItem(hop["location"]))
            self.redirects_table.setItem(i, 5, QTableWidgetItem(
                f"{hop['elapsed']*1000:.0f} ms"))

        self._build_html_tree(body)
        self._last_response = data

        try:
            self.history.add(
                module=self.name,
                method=self.method_box.currentText(),
                url=self.url_input.text(),
                headers=self._parse_headers(),
                body=self.body_edit.toPlainText(),
                status=status,
                response_body=body,
                elapsed=data["elapsed"],
            )
        except Exception as e:
            _log(f"history write failed: {e}")
        self._refresh_history()
        self._refresh_cookies_view()
        self._refresh_sessions_label()

    def _hex_dump(self, raw, width=16):
        lines = []
        for i in range(0, len(raw), width):
            chunk = raw[i:i + width]
            hex_part = " ".join(f"{b:02x}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:08x}  {hex_part:<{width*3}}  {ascii_part}")
        return "\n".join(lines)

    def _build_html_tree(self, html):
        self.html_tree.clear()
        try:
            from bs4 import BeautifulSoup, NavigableString, Tag
        except ImportError:
            self.html_info.setText(tr("html.no_bs", module="http_client"))
            return
        if "<" not in html or ">" not in html:
            self.html_info.setText(tr("html.no_html", module="http_client"))
            return
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            self.html_info.setText(tr("html.parse_error", module="http_client", error=e))
            return

        text_color = QColor(_c("fg_dim"))

        def build(node, parent):
            if isinstance(node, NavigableString):
                txt = str(node).strip()
                if txt:
                    it = QTreeWidgetItem([f'"{txt[:120]}"', ""])
                    it.setForeground(0, text_color)
                    parent.addChild(it)
                return
            if isinstance(node, Tag):
                attrs = " ".join(f'{k}="{v}"' for k, v in node.attrs.items())
                label = f"<{node.name}>" + (f"  {attrs}" if attrs else "")
                it = QTreeWidgetItem([label, ""])
                parent.addChild(it)
                for child in node.children:
                    build(child, it)

        root = QTreeWidgetItem(["root", ""])
        for child in soup.children:
            build(child, root)
        self.html_tree.addTopLevelItem(root)
        root.setExpanded(True)
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        self.html_info.setText(tr("html.info", module="http_client",
                                   tags=len(soup.find_all()),
                                   title=title[:60] if title else "-"))

    def _refresh_history(self):
        rows = self.history.list(module=self.name, limit=300)
        self.history_table.setRowCount(len(rows))
        success = QColor(_c("success"))
        warning = QColor(_c("warning"))
        danger = QColor(_c("danger"))
        for i, row in enumerate(rows):
            vals = [
                str(row["id"]),
                row["method"] or "-",
                row["url"] or "-",
                str(row["status"]) if row["status"] is not None else (
                    "ERR" if row["error"] else "-"),
                f"{row['elapsed']*1000:.0f} ms" if row["elapsed"] else "-",
                (row["created_at"] or "")[:19].replace("T", " "),
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if j == 3:
                    if row["status"]:
                        if 200 <= row["status"] < 300:
                            item.setForeground(success)
                        elif 300 <= row["status"] < 400:
                            item.setForeground(warning)
                        else:
                            item.setForeground(danger)
                    elif row["error"]:
                        item.setForeground(danger)
                self.history_table.setItem(i, j, item)

    def _refresh_cookies_view(self):
        try:
            sess = _session_store.get_requests(
                self.session_input.text().strip() or "default")
            items = list(sess.cookies)
            self.cookies_table.setRowCount(len(items))
            for i, c in enumerate(items):
                self.cookies_table.setItem(i, 0, QTableWidgetItem(c.name))
                self.cookies_table.setItem(i, 1, QTableWidgetItem(c.value))
        except Exception as e:
            _log(f"cookies view failed: {e}")

    def _refresh_sessions_label(self):
        try:
            count = len(_session_store.list())
            self.cookies_title.setText(
                tr("cookies.title_count", module="http_client", count=count))
        except Exception:
            pass

    def _save_cookies(self):
        try:
            sess = _session_store.get_requests(
                self.session_input.text().strip() or "default")
            self.COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.COOKIES_FILE, "w", encoding="utf-8") as f:
                json.dump(requests.utils.dict_from_cookiejar(sess.cookies),
                          f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log(f"save cookies failed: {e}")
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"), str(e))

    def _clear_cookies(self):
        try:
            name = self.session_input.text().strip() or "default"
            sess = _session_store.get_requests(name)
            sess.cookies.clear()
            self._refresh_cookies_view()
        except Exception as e:
            _log(f"clear cookies failed: {e}")

    def _open_cookie_editor(self):
        try:
            name = self.session_input.text().strip() or "default"
            sess = _session_store.get_requests(name)
        except Exception as e:
            _log(f"cookie editor failed: {e}")
            return
        dlg = CookieEditorDialog(parent_widget(self._widget), name, sess)
        if dlg.exec() == QDialog.Accepted:
            self._refresh_cookies_view()

    def _open_sessions_dialog(self):
        dlg = SessionsDialog(parent_widget(self._widget))
        dlg.exec()
        self._refresh_sessions_label()
        self._refresh_cookies_view()

    def _selected_history_row_id(self):
        sel = self.history_table.selectionModel().selectedRows()
        if not sel:
            return None
        try:
            return int(self.history_table.item(sel[0].row(), 0).text())
        except Exception:
            return None

    def _load_history_selected(self):
        rid = self._selected_history_row_id()
        if not rid:
            return
        rec = self.history.get(rid)
        if not rec:
            return
        self.method_box.setCurrentText(rec["method"] or "GET")
        self.url_input.setText(rec["url"] or "")
        try:
            headers = json.loads(rec["headers"]) if rec["headers"] else {}
            self.headers_edit.setPlainText("\n".join(f"{k}: {v}" for k, v in headers.items()))
        except Exception:
            self.headers_edit.setPlainText(rec["headers"] or "")
        self.body_edit.setPlainText(rec["body"] or "")
        self.tabs.setCurrentIndex(0)

    def _copy_history_as_curl(self):
        rid = self._selected_history_row_id()
        if not rid:
            return
        rec = self.history.get(rid)
        if not rec:
            return
        try:
            headers_dict = json.loads(rec["headers"]) if rec["headers"] else {}
        except Exception:
            headers_dict = {}
        try:
            curl = get_bus().call("http.curl.build", rec["method"] or "GET",
                                    rec["url"] or "", headers_dict,
                                    rec["body"] or "")
            if curl:
                QGuiApplication.clipboard().setText(curl)
        except KeyError:
            _log("http.curl.build unavailable")

    def _clear_history(self):
        if QMessageBox.question(
            parent_widget(self._widget),
            tr("msg.confirm_clear_history.title", module="http_client"),
            tr("msg.confirm_clear_history", module="http_client",
               module_name=self.name),
        ) != QMessageBox.Yes:
            return
        self.history.clear(module=self.name)
        self._refresh_history()

    def _copy_request_as_curl(self):
        try:
            curl = get_bus().call(
                "http.curl.build",
                self.method_box.currentText(),
                _resolve_vault_placeholders(self.url_input.text()),
                self._parse_headers(),
                _resolve_vault_placeholders(self.body_edit.toPlainText()))
        except KeyError:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"),
                                "http.curl.build unavailable")
            return
        QGuiApplication.clipboard().setText(curl)
        QMessageBox.information(parent_widget(self._widget),
                                tr("msg.curl_done", module="http_client"),
                                tr("msg.curl_copied", module="http_client"))

    def _build_curl(self, *, method, url, headers_raw, body):
        parts = ["curl", "-X", method or "GET"]
        try:
            headers = json.loads(headers_raw) if headers_raw else {}
        except Exception:
            headers = {}
        for k, v in headers.items():
            parts.append("-H")
            parts.append(f'"{k}: {v}"')
        if body:
            safe = body.replace("'", "'\\''")
            parts.append("--data-raw")
            parts.append(f"'{safe}'")
        parts.append(f'"{url}"')
        return " ".join(parts)

    def _import_curl_dialog(self):
        text, ok = QInputDialog.getMultiLineText(
            parent_widget(self._widget),
            tr("msg.import_curl.title", module="http_client"),
            tr("msg.import_curl.text", module="http_client"))
        if not ok or not text.strip():
            return
        try:
            parsed = get_bus().call("http.curl.parse", text)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.parse.title", module="http_client"), str(e))
            return
        if parsed.get("method"):
            self.method_box.setCurrentText(parsed["method"].upper())
        if parsed.get("url"):
            self.url_input.setText(parsed["url"])
        if parsed.get("headers"):
            self.headers_edit.setPlainText("\n".join(parsed["headers"]))
        if parsed.get("body") is not None:
            self.body_edit.setPlainText(parsed["body"])

    def _parse_curl(self, s):
        import shlex
        s = s.strip()
        if s.lower().startswith("curl"):
            s = s[4:].strip()
        s = s.replace("\\\n", " ").replace("^\n", " ")
        try:
            tokens = shlex.split(s)
        except ValueError:
            tokens = s.split()
        method = None
        url = None
        headers = []
        body = None
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t in ("-X", "--request") and i + 1 < len(tokens):
                method = tokens[i + 1]; i += 2
            elif t in ("-H", "--header") and i + 1 < len(tokens):
                headers.append(tokens[i + 1]); i += 2
            elif t in ("-d", "--data", "--data-raw", "--data-binary") and i + 1 < len(tokens):
                body = tokens[i + 1]; i += 2
            elif t in ("-b", "--cookie") and i + 1 < len(tokens):
                headers.append(f"Cookie: {tokens[i + 1]}"); i += 2
            elif t in ("-u", "--user") and i + 1 < len(tokens):
                headers.append(f"Authorization: Basic {tokens[i + 1]}"); i += 2
            elif t.startswith("http://") or t.startswith("https://"):
                url = t; i += 1
            else:
                i += 1
        return {"method": method, "url": url, "headers": headers, "body": body}

    def _copy_response(self):
        text = self.resp_body_edit.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)

    def _refresh_snippets(self):
        self.SNIPPETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        snippets = []
        if self.SNIPPETS_FILE.exists():
            try:
                with open(self.SNIPPETS_FILE, "r", encoding="utf-8") as f:
                    snippets = json.load(f)
            except Exception as e:
                _log(f"snippets read failed: {e}")
                snippets = []
        self._snippets = snippets
        self.snippet_box.blockSignals(True)
        self.snippet_box.clear()
        self.snippet_box.addItem("")
        for s in snippets:
            self.snippet_box.addItem(s["name"])
        self.snippet_box.blockSignals(False)

    def _save_snippet(self):
        name, ok = QInputDialog.getText(
            parent_widget(self._widget),
            tr("msg.save_snippet.title", module="http_client"),
            tr("msg.save_snippet.text", module="http_client"))
        if not ok or not name.strip():
            return
        name = name.strip()
        body = self.body_edit.toPlainText()
        self._snippets = [s for s in self._snippets if s["name"] != name]
        self._snippets.append({"name": name, "body": body})
        self._write_snippets()
        self._refresh_snippets()
        self.snippet_box.setCurrentText(name)

    def _load_snippet(self, name):
        if not name:
            return
        for s in self._snippets:
            if s["name"] == name:
                self.body_edit.setPlainText(s["body"])
                return

    def _delete_snippet(self):
        name = self.snippet_box.currentText()
        if not name:
            return
        self._snippets = [s for s in self._snippets if s["name"] != name]
        self._write_snippets()
        self._refresh_snippets()

    def _write_snippets(self):
        self.SNIPPETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.SNIPPETS_FILE, "w", encoding="utf-8") as f:
            json.dump(self._snippets, f, ensure_ascii=False, indent=2)

    def _refresh_profiles(self):
        self.profile_box.blockSignals(True)
        cur = self.profile_box.currentText()
        self.profile_box.clear()
        self.profile_box.addItem("")
        self.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        for fname in sorted(os.listdir(self.PROFILES_DIR)):
            if fname.endswith(".json") and not fname.startswith("_"):
                self.profile_box.addItem(fname[:-5])
        if cur:
            idx = self.profile_box.findText(cur)
            if idx >= 0:
                self.profile_box.setCurrentIndex(idx)
        self.profile_box.blockSignals(False)

    def _current_profile_data(self):
        return {
            "method": self.method_box.currentText(),
            "url": self.url_input.text(),
            "headers": self.headers_edit.toPlainText(),
            "body": self.body_edit.toPlainText(),
            "engine": self.engine_box.currentText(),
            "verify_ssl": self.verify_chk.isChecked(),
            "follow_redirects": self.redirect_chk.isChecked(),
            "timeout": self.timeout_spin.value(),
            "use_proxy": self.use_proxy_chk.isChecked(),
            "session": self.session_input.text(),
        }

    def _apply_profile_data(self, data):
        self.method_box.setCurrentText(data.get("method", "GET"))
        self.url_input.setText(data.get("url", ""))
        self.headers_edit.setPlainText(data.get("headers", ""))
        self.body_edit.setPlainText(data.get("body", ""))
        if data.get("engine"):
            self.engine_box.setCurrentText(data["engine"])
        if "verify_ssl" in data:
            self.verify_chk.setChecked(bool(data["verify_ssl"]))
        if "follow_redirects" in data:
            self.redirect_chk.setChecked(bool(data["follow_redirects"]))
        if "timeout" in data:
            self.timeout_spin.setValue(int(data["timeout"]))
        if "use_proxy" in data:
            self.use_proxy_chk.setChecked(bool(data["use_proxy"]))
        if "session" in data:
            self.session_input.setText(data["session"])

    def _load_profile(self, name):
        if not name:
            return
        path = self.PROFILES_DIR / f"{name}.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"),
                                tr("msg.read_failed", module="http_client", error=e))
            return
        self._apply_profile_data(data)

    def _save_profile_as(self):
        name, ok = QInputDialog.getText(
            parent_widget(self._widget),
            tr("msg.save_profile.title", module="http_client"),
            tr("msg.save_profile.text", module="http_client"))
        if not ok or not name.strip():
            return
        name = name.strip()
        self.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        path = self.PROFILES_DIR / f"{name}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._current_profile_data(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"),
                                tr("msg.save_failed", module="http_client", error=e))
            return
        self._refresh_profiles()
        self.profile_box.setCurrentText(name)

    def _export_profile(self):
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("msg.export.title", module="http_client"),
            "profile.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._current_profile_data(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"), str(e))

    def _import_profile(self):
        path, _ = QFileDialog.getOpenFileName(
            parent_widget(self._widget),
            tr("msg.import.title", module="http_client"), "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"), str(e))
            return
        self._apply_profile_data(data)

    def _delete_profile(self):
        name = self.profile_box.currentText()
        if not name:
            return
        path = self.PROFILES_DIR / f"{name}.json"
        if not path.exists():
            return
        if QMessageBox.question(
            parent_widget(self._widget),
            tr("msg.delete_profile.title", module="http_client"),
            tr("msg.delete_profile.text", module="http_client", name=name),
        ) != QMessageBox.Yes:
            return
        try:
            path.unlink()
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="http_client"), str(e))
            return
        self._refresh_profiles()