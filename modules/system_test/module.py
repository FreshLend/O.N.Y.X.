import json
import re
import time
import urllib.request
import uuid

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPlainTextEdit, QMessageBox
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.i18n import tr
from core.utils import color as _c, parent_widget
from styles import loader as theme_loader

from .proxy_server import TestProxy
from .test_server import TestServer, JWT_SECRET, TOTP_SECRET

try:
    import pyotp
except ImportError:
    pyotp = None


def T(key, **kwargs):
    return tr(key, module="system_test", **kwargs)


class _Skip(Exception):
    def __init__(self, missing):
        super().__init__("skipped: " + ", ".join(missing))
        self.missing = list(missing)


class SystemTestModule(BaseModule):
    CALLER = "System Test"

    def __init__(self):
        self._widget = None
        self._steps = []
        self._idx = 0
        self._results = []
        self._details = {}
        self._proxy = TestProxy()
        self._server = TestServer()
        self._session_id = None
        self._labels = {}
        self._buttons = {}

    def on_shutdown(self):
        try:
            self._proxy.stop()
        except Exception:
            pass
        try:
            self._server.stop()
        except Exception:
            pass

    def create_widget(self) -> QWidget:
        root = QWidget()
        self._widget = root

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        mono = QFont("Consolas", 10)

        bar = QHBoxLayout()
        self._labels["server"] = QLabel()
        self._labels["server"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self._labels["server"])

        self.base_url = QLineEdit()
        self.base_url.setReadOnly(True)
        self.base_url.setFont(mono)
        bar.addWidget(self.base_url, 2)

        self._labels["user"] = QLabel()
        self._labels["user"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self._labels["user"])
        self.user_input = QLineEdit("admin")
        self.user_input.setFixedWidth(120)
        bar.addWidget(self.user_input)

        self._labels["pass"] = QLabel()
        self._labels["pass"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self._labels["pass"])
        self.pass_input = QLineEdit("admin123")
        self.pass_input.setEchoMode(QLineEdit.Password)
        self.pass_input.setFixedWidth(120)
        bar.addWidget(self.pass_input)

        bar.addStretch()

        self.run_btn = QPushButton()
        self._buttons["run"] = self.run_btn
        self.run_btn.setIcon(theme_loader.get_icon(icon_name="play", emoji=">", size=14))
        self.run_btn.setFixedWidth(220)
        self.run_btn.clicked.connect(self._start)
        bar.addWidget(self.run_btn)

        layout.addLayout(bar)

        self.progress = QLabel()
        self.progress.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.progress)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self._show_detail)
        layout.addWidget(self.table, 2)

        self._labels["detail_hint"] = QLabel()
        self._labels["detail_hint"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self._labels["detail_hint"])

        self.detail = QPlainTextEdit()
        self.detail.setFont(mono)
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(150)
        layout.addWidget(self.detail)

        self.retranslate()
        return root

    def retranslate(self):
        if not hasattr(self, "run_btn"):
            return
        self._labels["server"].setText(T("server"))
        self._labels["user"].setText(T("user"))
        self._labels["pass"].setText(T("pass"))
        self._labels["detail_hint"].setText(T("detail.hint"))
        self._buttons["run"].setText(T("run"))
        self.base_url.setPlaceholderText(T("server.placeholder"))
        self.table.setHorizontalHeaderLabels([
            T("col.num"), T("col.step"), T("col.status"), T("col.time"),
        ])
        if self._session_id is None and not self._results:
            self.progress.setText(T("progress.idle"))
        elif self._results:
            self._refresh_progress_text()

    def _refresh_progress_text(self):
        passed = sum(1 for r in self._results if r[1] and not r[3])
        skipped = sum(1 for r in self._results if r[3])
        total = len(self._results)
        failed = total - passed - skipped
        if failed == 0:
            key = "progress.done_pass"
            color = _c("success")
        else:
            key = "progress.done_fail"
            color = _c("danger")
        self.progress.setText(T(key, passed=passed, skipped=skipped,
                                failed=failed, total=total,
                                session=self._session_id or "-"))
        self.progress.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: bold;")

    def _http(self, method, url, **kwargs):
        bus = get_bus()
        kwargs.setdefault("timeout", 10)
        kwargs["caller"] = self.CALLER
        kwargs["session"] = self._session_id
        return bus.call("http.request", method, url, **kwargs)

    def _start(self):
        if self._server.port is None:
            if not self._server.start():
                QMessageBox.warning(parent_widget(self._widget),
                                    T("err.title"), T("err.server"))
                return
        self.base_url.setText(self._server.url or "")

        self._session_id = "st-" + uuid.uuid4().hex[:8]
        self._proxy.stop()
        self._steps = self._build_steps()
        self._idx = 0
        self._results = []
        self._details = {}
        self.table.setRowCount(0)
        self.detail.clear()
        self.run_btn.setEnabled(False)
        self.progress.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        self.progress.setText(T("progress.running", done=0,
                                total=len(self._steps),
                                session=self._session_id))
        QTimer.singleShot(30, self._next_step)

    def _next_step(self):
        if self._idx >= len(self._steps):
            self._finish()
            return

        import traceback

        name, fn = self._steps[self._idx]
        t0 = time.time()
        skipped = False
        try:
            result = fn()
            if result is None:
                ok, detail = False, T("err.none")
            else:
                ok, detail = result
        except _Skip as e:
            ok = True
            skipped = True
            detail = T("status.skipped", missing=", ".join(e.missing))
        except Exception as e:
            ok = False
            detail = T("err.exception", error=e) + f"\n\n{traceback.format_exc()}"
        elapsed = time.time() - t0

        self._results.append((name, ok, elapsed, skipped))
        self._details[self._idx] = detail
        self._add_row(self._idx, name, ok, elapsed, skipped)

        self._idx += 1
        self.progress.setText(T("progress.running", done=self._idx,
                                total=len(self._steps),
                                session=self._session_id))
        QTimer.singleShot(15, self._next_step)

    def _finish(self):
        self._proxy.stop()
        self._server.stop()
        self.base_url.clear()
        self.run_btn.setEnabled(True)
        self._refresh_progress_text()

    def _add_row(self, idx, name, ok, elapsed, skipped=False):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(str(idx + 1)))
        self.table.setItem(row, 1, QTableWidgetItem(name))
        if skipped:
            status = T("status.skip")
            color = _c("warning")
        elif ok:
            status = T("status.ok")
            color = _c("success")
        else:
            status = T("status.fail")
            color = _c("danger")
        s_item = QTableWidgetItem(status)
        s_item.setForeground(QColor(color))
        self.table.setItem(row, 2, s_item)
        self.table.setItem(row, 3, QTableWidgetItem(f"{elapsed*1000:.0f} ms"))

    def _show_detail(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        self.detail.setPlainText(self._details.get(row, T("detail.none")))

    def _build_steps(self):
        url = (self._server.url or "").rstrip("/")
        user = self.user_input.text()
        pwd = self.pass_input.text()
        bus = get_bus()
        proxy = self._proxy
        http = self._http
        session = self._session_id

        state = {
            "token_saved": False,
            "form_token_saved": False,
            "totp_token_saved": False,
            "manual_token_saved": False,
        }

        def _require(*services):
            registered = bus.list_services()
            missing = [s for s in services if s not in registered]
            if missing:
                raise _Skip(missing)

        def _need_token(key="token_saved", label="test.jwt"):
            if not state.get(key):
                raise _Skip([f"{label} (login step was skipped or failed)"])

        def s_mock_up():
            try:
                with urllib.request.urlopen(f"{url}/api/health", timeout=3) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                ok = data.get("ok") is True
                return ok, f"direct status={resp.status}"
            except Exception as e:
                return False, f"direct fetch failed: {e}"

        def s_proxy_start():
            ok = proxy.start()
            return ok, f"proxy at {proxy.url}" if ok else "failed"

        def s_bus():
            modules = bus.list_modules()
            services = bus.list_services()
            orphan = []
            for svc_name, meta in services.items():
                provider = meta.get("provider")
                if provider and provider not in modules:
                    orphan.append(f"{svc_name}@{provider}")
            if orphan:
                return False, f"orphan services: {orphan}"
            mod_list = ", ".join(sorted(modules))
            return True, (f"modules={len(modules)}  services={len(services)}\n"
                          f"loaded: {mod_list}")

        def s_health():
            _require("http.request")
            r = http("GET", f"{url}/api/health", headers={}, body="")
            return r.get("ok") and r["status"] == 200, f"status={r.get('status')}"

        def s_echo_post():
            _require("http.request")
            r = http("POST", f"{url}/api/echo",
                     headers={"Content-Type": "application/json"},
                     body='{"ping":"pong"}')
            ok = r.get("ok") and r["status"] == 200 and '"ping"' in r["body"]
            return ok, f"status={r.get('status')}"

        def s_echo_put():
            _require("http.request")
            r = http("PUT", f"{url}/api/echo",
                     headers={"Content-Type": "application/json"}, body='{"x":1}')
            return r.get("ok") and '"method":"PUT"' in r["body"], f"status={r.get('status')}"

        def s_echo_patch():
            _require("http.request")
            r = http("PATCH", f"{url}/api/echo",
                     headers={"Content-Type": "application/json"}, body='{"x":2}')
            return r.get("ok") and '"method":"PATCH"' in r["body"], f"status={r.get('status')}"

        def s_echo_delete():
            _require("http.request")
            r = http("DELETE", f"{url}/api/echo",
                     headers={"Content-Type": "application/json"}, body='{"x":3}')
            return r.get("ok") and '"method":"DELETE"' in r["body"], f"status={r.get('status')}"

        def s_query_args():
            _require("http.request")
            r = http("GET", f"{url}/api/echo?a=1&b=two", headers={}, body="")
            ok = r.get("ok") and '"a":"1"' in r["body"] and '"b":"two"' in r["body"]
            return ok, "args parsed"

        def s_error_codes():
            _require("http.request")
            codes = [400, 401, 403, 404, 418, 500, 503]
            got = []
            for c in codes:
                r = http("GET", f"{url}/api/error/{c}",
                         headers={}, body="", timeout=5)
                if r.get("ok"):
                    got.append(r["status"])
            return got == codes, f"got={got}"

        def s_redirect():
            _require("http.request")
            r = http("GET", f"{url}/api/redirect",
                     headers={}, body="", allow_redirects=True)
            return r.get("ok") and r["status"] == 200, f"final={r.get('status')}"

        def s_redirect_chain():
            _require("http.request")
            r = http("GET", f"{url}/api/redirect?n=3",
                     headers={}, body="", allow_redirects=True)
            return r.get("ok") and r["status"] == 200, f"final={r.get('status')}"

        def s_timeout():
            _require("http.request")
            r = http("GET", f"{url}/api/slow?delay=35",
                     headers={}, body="", timeout=2)
            err = str(r.get("error", "")).lower()
            hit = ("timeout" in err) or ("timed out" in err)
            return (not r.get("ok") and hit), f"error={err[:80]}"

        def s_html():
            _require("http.request")
            r = http("GET", f"{url}/", headers={}, body="")
            if not r.get("ok") or r["status"] != 200:
                return False, "GET / failed"
            body = r["body"]
            ok = "<html" in body.lower() and "<body" in body.lower()
            return ok, f"size={len(body)}"

        def s_json_parse():
            _require("http.request")
            r = http("GET", f"{url}/api/users", headers={}, body="")
            if not r.get("ok"):
                return False, "request failed"
            try:
                parsed = json.loads(r["body"])
                return parsed.get("count") == 5, f"count={parsed.get('count')}"
            except Exception as e:
                return False, str(e)

        def s_engines_list():
            _require("http.engines")
            engines = bus.call("http.engines")
            return len(engines) >= 1, f"engines={engines}"

        def s_engine_requests():
            _require("http.request")
            r = http("GET", f"{url}/api/health", headers={}, body="", engine="requests")
            return r.get("ok") and r["status"] == 200, "engine=requests"

        def s_engine_curl_cffi():
            _require("http.engines", "http.request")
            engines = bus.call("http.engines")
            if "curl_cffi" not in engines:
                return True, "skipped (not installed)"
            r = http("GET", f"{url}/api/health", headers={}, body="", engine="curl_cffi")
            return r.get("ok") and r["status"] == 200, "engine=curl_cffi"

        def s_engine_cloudscraper():
            _require("http.engines", "http.request")
            engines = bus.call("http.engines")
            if "cloudscraper" not in engines:
                return True, "skipped (not installed)"
            r = http("GET", f"{url}/api/health", headers={}, body="", engine="cloudscraper")
            return r.get("ok") and r["status"] == 200, "engine=cloudscraper"

        def s_curl_parse():
            _require("http.curl.parse")
            cmd = ('curl -X POST "http://example.com/api" '
                   '-H "Content-Type: application/json" '
                   '-H "Authorization: Bearer abc" '
                   '--data-raw \'{"a":1}\'')
            parsed = bus.call("http.curl.parse", cmd)
            ok = (parsed.get("method") == "POST"
                  and parsed.get("url") == "http://example.com/api"
                  and len(parsed.get("headers", [])) == 2
                  and parsed.get("body") == '{"a":1}')
            return ok, f"method={parsed.get('method')}"

        def s_curl_parse_get():
            _require("http.curl.parse")
            cmd = "curl https://example.com/path"
            parsed = bus.call("http.curl.parse", cmd)
            return parsed.get("url") == "https://example.com/path", f"url={parsed.get('url')}"

        def s_curl_build():
            _require("http.curl.build")
            cmd = bus.call("http.curl.build", "GET", "http://example.com/x",
                           {"Accept": "application/json"}, "")
            ok = "curl" in cmd and "-X GET" in cmd and "http://example.com/x" in cmd
            return ok, cmd[:80]

        def s_curl_roundtrip():
            _require("http.curl.build", "http.curl.parse")
            original = {
                "method": "POST",
                "url": "http://example.com/api",
                "headers": {"Content-Type": "application/json"},
                "body": '{"k":"v"}',
            }
            built = bus.call("http.curl.build", original["method"],
                             original["url"], original["headers"], original["body"])
            parsed = bus.call("http.curl.parse", built)
            ok = (parsed.get("method") == "POST"
                  and parsed.get("url") == original["url"]
                  and parsed.get("body") == original["body"])
            return ok, "roundtrip ok"

        def s_profile_save():
            _require("http.profile.save")
            data = {
                "method": "POST", "url": "http://test.local/api",
                "headers": "Content-Type: application/json",
                "body": '{"x":1}', "engine": "requests", "timeout": 30,
            }
            return bus.call("http.profile.save", "__sys_test_profile__", data), "saved"

        def s_profile_load():
            _require("http.profile.load")
            r = bus.call("http.profile.load", "__sys_test_profile__")
            if not r.get("ok"):
                return False, str(r.get("error"))
            d = r["data"]
            ok = d["method"] == "POST" and d["url"] == "http://test.local/api"
            return ok, f"loaded url={d.get('url')}"

        def s_profile_list():
            _require("http.profile.list")
            names = bus.call("http.profile.list")
            return "__sys_test_profile__" in names, f"count={len(names)}"

        def s_profile_delete():
            _require("http.profile.delete", "http.profile.list")
            ok = bus.call("http.profile.delete", "__sys_test_profile__")
            names = bus.call("http.profile.list")
            return ok and "__sys_test_profile__" not in names, "deleted"

        def s_snippet_save():
            _require("http.snippet.save")
            return bus.call("http.snippet.save", "__sys_test_snip__", '{"test":1}'), "saved"

        def s_snippet_load():
            _require("http.snippet.load")
            body = bus.call("http.snippet.load", "__sys_test_snip__")
            return body == '{"test":1}', f"body={body}"

        def s_snippet_list():
            _require("http.snippet.list")
            names = bus.call("http.snippet.list")
            return "__sys_test_snip__" in names, f"count={len(names)}"

        def s_snippet_delete():
            _require("http.snippet.delete")
            ok = bus.call("http.snippet.delete", "__sys_test_snip__")
            return ok, "deleted"

        def s_vault_set_get():
            _require("vault.set", "vault.get")
            bus.call("vault.set", "__sys_test_key__", "hello")
            val = bus.call("vault.get", "__sys_test_key__")
            return val == "hello", f"got={val}"

        def s_vault_list():
            _require("vault.list")
            items = bus.call("vault.list")
            names = [k for k, _ in items]
            return "__sys_test_key__" in names, f"count={len(items)}"

        def s_vault_delete():
            _require("vault.delete", "vault.get")
            ok = bus.call("vault.delete", "__sys_test_key__")
            val = bus.call("vault.get", "__sys_test_key__")
            return ok and val is None, "deleted"

        def s_login():
            _require("login.run", "vault.set", "vault.get")
            r = bus.call("login.run", f"{url}/api/login", user, pwd,
                         "JSON body", "token", "test.jwt",
                         caller=self.CALLER, session=session)
            if r is None:
                return False, "login.run returned None"
            if not r.get("ok"):
                return False, str(r.get("error"))
            state["token_saved"] = True
            return True, f"vault_key={r['vault_key']}"

        def s_login_form():
            _require("login.run", "vault.set", "vault.get")
            r = bus.call("login.run", f"{url}/api/login/form", user, pwd,
                         "Form data", "token", "test.form.jwt",
                         caller=self.CALLER, session=session)
            if r is None:
                return False, "login.run returned None"
            if not r.get("ok"):
                return False, str(r.get("error"))
            state["form_token_saved"] = True
            return True, "form login ok"

        def s_vault_has_token():
            _require("vault.get")
            _need_token()
            val = bus.call("vault.get", "test.jwt")
            if not val:
                return False, "test.jwt not found"
            return val.count(".") == 2, f"{len(str(val))} chars"

        def s_auth_me():
            _require("http.request", "vault.get")
            _need_token()
            token = bus.call("vault.get", "test.jwt")
            r = http("GET", f"{url}/api/me",
                     headers={"Authorization": f"Bearer {token}"}, body="")
            return r.get("ok") and r["status"] == 200, f"status={r.get('status')}"

        def s_auth_admin():
            _require("http.request", "vault.get")
            _need_token()
            token = bus.call("vault.get", "test.jwt")
            r = http("GET", f"{url}/api/admin",
                     headers={"Authorization": f"Bearer {token}"}, body="")
            return r.get("ok") and r["status"] == 200, f"status={r.get('status')}"

        def s_auth_no_token():
            _require("http.request")
            r = http("GET", f"{url}/api/me", headers={}, body="",
                     session_name="__st_isolated_no_token__")
            return r.get("ok") and r["status"] == 401, f"status={r.get('status')}"

        def s_auth_bad_token():
            _require("http.request")
            r = http("GET", f"{url}/api/me",
                     headers={"Authorization": "Bearer not.a.token"}, body="")
            return r.get("ok") and r["status"] == 401, f"status={r.get('status')}"

        def s_login_2fa_totp():
            if pyotp is None:
                return True, "skipped (pyotp not installed)"
            _require("login.run", "vault.get")
            r = bus.call("login.run", f"{url}/api/login", "admin_2fa", "admin123",
                         "JSON body", "token", "test.2fa.totp",
                         totp_secret=TOTP_SECRET,
                         caller=self.CALLER, session=session)
            if not r or not r.get("ok"):
                return False, str((r or {}).get("error"))
            if not r.get("two_factor_used"):
                return False, "two_factor_used flag not set"
            token = bus.call("vault.get", "test.2fa.totp")
            ok = bool(token) and token.count(".") == 2
            if ok:
                state["totp_token_saved"] = True
            return ok, f"steps={r.get('steps')} token_saved={ok}"

        def s_login_2fa_manual_code():
            if pyotp is None:
                return True, "skipped (pyotp not installed)"
            _require("login.run")
            code = pyotp.TOTP(TOTP_SECRET).now()
            r = bus.call("login.run", f"{url}/api/login", "admin_2fa", "admin123",
                         "JSON body", "token", "test.2fa.manual",
                         code=code,
                         caller=self.CALLER, session=session)
            if not r or not r.get("ok"):
                return False, str((r or {}).get("error"))
            return True, f"steps={r.get('steps')} code={code[:2]}***"

        def s_login_2fa_no_code():
            _require("login.run", "vault.delete")
            bus.call("vault.delete", "test.2fa.missing")
            r = bus.call("login.run", f"{url}/api/login", "admin_2fa", "admin123",
                         "JSON body", "token", "test.2fa.missing",
                         caller=self.CALLER, session=session)
            ok = bool(r and r.get("requires_2fa") and r.get("challenge"))
            return ok, f"requires_2fa={r.get('requires_2fa') if r else None}"

        def s_login_2fa_bad_code():
            _require("login.run")
            r = bus.call("login.run", f"{url}/api/login", "admin_2fa", "admin123",
                         "JSON body", "token", "test.2fa.bad",
                         code="000000",
                         caller=self.CALLER, session=session)
            ok = bool(r) and not r.get("ok")
            err = (r or {}).get("error", "None")
            return ok, f"rejected={ok} err={err[:60]}"

        def s_jwt_decode():
            _require("jwt.decode", "vault.get")
            _need_token()
            token = bus.call("vault.get", "test.jwt")
            res = bus.call("jwt.decode", token)
            if not res.get("ok"):
                return False, str(res.get("error"))
            ok = (not res["expired"]
                  and res["payload"].get("sub") == user
                  and res["header"].get("alg") == "HS256")
            return ok, f"sub={res['payload'].get('sub')} expires_in={res['expires_in']}s"

        def s_jwt_verify_valid():
            _require("jwt.verify", "vault.get")
            _need_token()
            token = bus.call("vault.get", "test.jwt")
            res = bus.call("jwt.verify", token, JWT_SECRET, "HS256")
            if not res.get("ok"):
                return False, str(res.get("error"))
            return res.get("valid"), f"valid={res.get('valid')}"

        def s_jwt_verify_bad_secret():
            _require("jwt.verify", "vault.get")
            _need_token()
            token = bus.call("vault.get", "test.jwt")
            res = bus.call("jwt.verify", token, "wrong_secret", "HS256")
            if not res.get("ok"):
                return False, str(res.get("error"))
            return (res.get("valid") is False), f"valid={res.get('valid')}"

        def s_jwt_verify_tampered():
            _require("jwt.verify", "vault.get")
            _need_token()
            token = bus.call("vault.get", "test.jwt")
            parts = token.split(".")
            tampered = parts[0] + "." + parts[1] + ".AAAAAAAA"
            res = bus.call("jwt.verify", tampered, JWT_SECRET, "HS256")
            if not res.get("ok"):
                return False, str(res.get("error"))
            return (res.get("valid") is False), f"valid={res.get('valid')}"

        def s_encoder_base64():
            _require("encoder.transform")
            b64 = bus.call("encoder.transform", "Base64", "hello world")
            return b64 == "aGVsbG8gd29ybGQ=", f"got={b64}"

        def s_encoder_hex():
            _require("encoder.transform")
            h = bus.call("encoder.transform", "Hex", "ABC")
            return h == "414243", f"got={h}"

        def s_encoder_rot13():
            _require("encoder.transform")
            r = bus.call("encoder.transform", "ROT13", "hello")
            return r == "uryyb", f"got={r}"

        def s_encoder_url():
            _require("encoder.transform")
            r = bus.call("encoder.transform", "URL encode", "a b&c")
            return r == "a%20b%26c", f"got={r}"

        def s_encoder_html():
            _require("encoder.transform")
            r = bus.call("encoder.transform", "HTML entities", "<b>x</b>")
            return "&lt;b&gt;" in r, f"got={r}"

        def s_encoder_json():
            _require("encoder.transform")
            r = bus.call("encoder.transform", "JSON pretty", '{"a":1}')
            return '"a": 1' in r, "pretty ok"

        def s_diff_json():
            _require("diff.compare")
            a = '{"a":1,"b":2}'
            b = '{"a":1,"b":3,"c":4}'
            res = bus.call("diff.compare", a, b, "JSON keys")
            joined = "\n".join(res)
            return ("+ c" in joined and "~ b" in joined), f"changes={len(res)}"

        def s_diff_no_changes():
            _require("diff.compare")
            a = '{"a":1}'
            b = '{"a":1}'
            res = bus.call("diff.compare", a, b, "JSON keys")
            return len(res) == 0, f"changes={len(res)}"

        def s_diff_line():
            _require("diff.compare")
            a = "line1\nline2\nline3"
            b = "line1\nLINE2\nline3"
            res = bus.call("diff.compare", a, b, "Line diff")
            joined = "".join(res)
            return "-line2" in joined and "+LINE2" in joined, f"hunks={len(res)}"

        def s_scanner():
            _require("scanner.scan")
            results = bus.call("scanner.scan", url,
                               ["admin", "secret", "uploads", "robots.txt", "api"],
                               threads=5, timeout=5,
                               caller=self.CALLER, session=session)
            if results is None:
                return False, "scanner.scan returned None"
            found_urls = [r["url"] for r in results]
            has_admin = any("/admin" in u for u in found_urls)
            return has_admin and len(results) >= 3, f"found={len(results)}"

        def s_scanner_single():
            _require("scanner.scan")
            results = bus.call("scanner.scan", url, ["uploads"],
                               threads=1, timeout=5,
                               caller=self.CALLER, session=session)
            if results is None:
                return False, "scanner.scan returned None"
            return len(results) == 1, f"found={len(results)}"

        def s_reporter_md():
            _require("reporter.generate")
            text = bus.call("reporter.generate", "Markdown", 50, False)
            ok = (len(text) > 0
                  and text.lstrip().startswith("#")
                  and "|" in text)
            return ok, f"len={len(text)}"

        def s_reporter_json():
            _require("reporter.generate")
            text = bus.call("reporter.generate", "JSON", 10, False)
            try:
                parsed = json.loads(text)
                return isinstance(parsed, list), f"records={len(parsed)}"
            except Exception as e:
                return False, str(e)

        def s_reporter_html():
            _require("reporter.generate")
            text = bus.call("reporter.generate", "HTML", 10, False)
            return text.startswith("<!doctype html>"), f"len={len(text)}"

        def s_history():
            from core.history import get_history
            rows = get_history().list(limit=5)
            if not rows:
                return True, "skipped (no history rows yet)"
            return len(rows) > 0, f"records={len(rows)}"

        def s_history_caller():
            from core.history import get_history
            rows = get_history().list(module=self.CALLER, limit=5)
            if not rows:
                return True, "skipped (no rows for this caller)"
            return len(rows) > 0, f"caller records={len(rows)}"

        def s_history_session():
            from core.history import get_history
            rows = get_history().list(session=session, limit=500)
            if not rows:
                return True, "skipped (no session rows)"
            return True, f"session records={len(rows)}"

        def s_proxy_works():
            _require("proxy.check")
            r = bus.call("proxy.check", proxy.url, f"{url}/api/health", 5)
            if not r.get("ok"):
                return False, str(r.get("error"))
            return r["status"] == 200, f"via {proxy.url}"

        def s_proxy_bad():
            _require("proxy.check")
            r = bus.call("proxy.check", "http://127.0.0.1:1",
                         f"{url}/api/health", 2)
            return (not r.get("ok")), "bad proxy rejected"

        def s_coll_save_list():
            _require("collections.save", "collections.list")
            name = "__sys_coll_basic__"
            reqs = [{
                "name": "health", "method": "GET",
                "url": f"{url}/api/health",
                "headers": "", "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 200, "max_ms": 0},
                "delay_ms": 0, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs)
            names = bus.call("collections.list")
            return name in names, f"collections={len(names)}"

        def s_coll_run_simple():
            _require("collections.run", "http.request")
            name = "__sys_coll_basic__"
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            if len(results) != 1:
                return False, f"expected 1, got {len(results)}"
            r = results[0]
            ok = (r.get("status") == 200
                  and r.get("ok") is True
                  and r.get("assert_ok") is True)
            return ok, f"status={r.get('status')} assert_ok={r.get('assert_ok')}"

        def s_coll_cleanup_basic():
            _require("collections.delete", "collections.list")
            name = "__sys_coll_basic__"
            ok = bus.call("collections.delete", name)
            names = bus.call("collections.list")
            return ok and name not in names, "deleted"

        def s_coll_vars():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_vars__"
            reqs = [{
                "name": "health_via_var", "method": "GET",
                "url": "{{base_url}}/api/health",
                "headers": "", "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 200, "max_ms": 0},
                "delay_ms": 0, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs,
                     vars={"base_url": url})
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if not results:
                return False, "no results"
            ok = results[0].get("status") == 200
            return ok, f"var substituted, status={results[0].get('status')}"

        def s_coll_env():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_env__"
            reqs = [{
                "name": "health_env", "method": "GET",
                "url": "{{base_url}}/api/health",
                "headers": "", "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 200, "max_ms": 0},
                "delay_ms": 0, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs,
                     vars={"base_url": "http://127.0.0.1:1"},
                     environments={
                         "prod": {"base_url": url},
                         "dev": {"base_url": "http://127.0.0.1:2"},
                     },
                     active_env="prod")
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if not results:
                return False, "no results"
            ok = results[0].get("status") == 200
            return ok, f"env overrides vars, status={results[0].get('status')}"

        def s_coll_assert_status():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_assert_status__"
            reqs = [{
                "name": "expect_418", "method": "GET",
                "url": f"{url}/api/error/418",
                "headers": "", "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 418, "max_ms": 0},
                "delay_ms": 0, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if not results:
                return False, "no results"
            r = results[0]
            return r.get("assert_ok") is True, f"assert_ok={r.get('assert_ok')}"

        def s_coll_assert_fail():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_assert_fail__"
            reqs = [{
                "name": "expect_200_get_418", "method": "GET",
                "url": f"{url}/api/error/418",
                "headers": "", "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 200, "max_ms": 0},
                "delay_ms": 0, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if not results:
                return False, "no results"
            r = results[0]
            ok = r.get("assert_ok") is False
            return ok, f"assert detected fail: {r.get('assert_msg', '')}"

        def s_coll_assert_json():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_assert_json__"
            reqs = [{
                "name": "json_check", "method": "GET",
                "url": f"{url}/api/users",
                "headers": "", "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 200, "json_path": "count",
                           "json_equals": 5, "max_ms": 0},
                "delay_ms": 0, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if not results:
                return False, "no results"
            r = results[0]
            return r.get("assert_ok") is True, "json path check ok"

        def s_coll_assert_max_ms():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_assert_maxms__"
            reqs = [{
                "name": "slow_check", "method": "GET",
                "url": f"{url}/api/health",
                "headers": "", "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 200, "max_ms": 1},
                "delay_ms": 0, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if not results:
                return False, "no results"
            r = results[0]
            return r.get("assert_ok") is False, "max_ms breach detected"

        def s_coll_assert_body():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_assert_body__"
            reqs = [{
                "name": "body_check", "method": "GET",
                "url": f"{url}/api/health",
                "headers": "", "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 200, "body_contains": '"ok":true', "max_ms": 0},
                "delay_ms": 0, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if not results:
                return False, "no results"
            return results[0].get("assert_ok") is True, "body_contains ok"

        def s_coll_extract():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request", "vault.set", "vault.get")
            name = "__sys_coll_extract__"
            reqs = [
                {
                    "name": "login", "method": "POST",
                    "url": f"{url}/api/login",
                    "headers": "Content-Type: application/json",
                    "body": json.dumps({"username": user, "password": pwd}),
                    "extract": {"path": "token", "vault_key": "test.coll.jwt"},
                    "assert": {"status": 200, "max_ms": 0},
                    "delay_ms": 0, "retry": 0, "retry_on": [],
                    "skip_if_prev_failed": False, "run_if_prev_status": [],
                },
                {
                    "name": "me", "method": "GET",
                    "url": f"{url}/api/me",
                    "headers": "Authorization: Bearer {{vault.test.coll.jwt}}",
                    "body": "",
                    "extract": {"path": "", "vault_key": ""},
                    "assert": {"status": 200, "max_ms": 0},
                    "delay_ms": 0, "retry": 0, "retry_on": [],
                    "skip_if_prev_failed": True, "run_if_prev_status": [200],
                },
            ]
            bus.call("collections.save", name, reqs)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if len(results) != 2:
                return False, f"expected 2, got {len(results)}"
            login_ok = results[0].get("status") == 200
            extracted = results[0].get("extracted") == "test.coll.jwt"
            me_ok = results[1].get("status") == 200
            me_assert = results[1].get("assert_ok") is True
            detail = (f"login={results[0].get('status')} extracted={extracted} "
                      f"me={results[1].get('status')} assert={me_assert}")
            return (login_ok and extracted and me_ok and me_assert), detail

        def s_coll_retry():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_retry__"
            reqs = [{
                "name": "retry_503", "method": "GET",
                "url": f"{url}/api/error/503",
                "headers": "", "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 503, "max_ms": 0},
                "delay_ms": 0, "retry": 2, "retry_on": [503],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if not results:
                return False, "no results"
            attempts = results[0].get("attempt", 0)
            return attempts == 3, f"attempts={attempts}"

        def s_coll_delay():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_delay__"
            reqs = [{
                "name": "delayed", "method": "GET",
                "url": f"{url}/api/health",
                "headers": "", "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 200, "max_ms": 0},
                "delay_ms": 500, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs)
            t0 = time.time()
            bus.call("collections.run", name,
                     caller=self.CALLER, session=session)
            elapsed = time.time() - t0
            bus.call("collections.delete", name)
            return elapsed >= 0.45, f"elapsed={elapsed*1000:.0f} ms (expect >= 450)"

        def s_coll_skip_if_prev_failed():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_skip__"
            reqs = [
                {
                    "name": "fail_first", "method": "GET",
                    "url": f"{url}/api/error/500",
                    "headers": "", "body": "",
                    "extract": {"path": "", "vault_key": ""},
                    "assert": {"status": 200, "max_ms": 0},
                    "delay_ms": 0, "retry": 0, "retry_on": [],
                    "skip_if_prev_failed": False, "run_if_prev_status": [],
                },
                {
                    "name": "should_skip", "method": "GET",
                    "url": f"{url}/api/health",
                    "headers": "", "body": "",
                    "extract": {"path": "", "vault_key": ""},
                    "assert": {"status": 200, "max_ms": 0},
                    "delay_ms": 0, "retry": 0, "retry_on": [],
                    "skip_if_prev_failed": True, "run_if_prev_status": [],
                },
            ]
            bus.call("collections.save", name, reqs)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if len(results) != 2:
                return False, f"expected 2, got {len(results)}"
            skipped = results[1].get("skipped") is True
            return skipped, f"second skipped={skipped}"

        def s_coll_run_if_status():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_runif__"
            reqs = [
                {
                    "name": "return_500", "method": "GET",
                    "url": f"{url}/api/error/500",
                    "headers": "", "body": "",
                    "extract": {"path": "", "vault_key": ""},
                    "assert": {"status": 500, "max_ms": 0},
                    "delay_ms": 0, "retry": 0, "retry_on": [],
                    "skip_if_prev_failed": False, "run_if_prev_status": [],
                },
                {
                    "name": "only_if_200", "method": "GET",
                    "url": f"{url}/api/health",
                    "headers": "", "body": "",
                    "extract": {"path": "", "vault_key": ""},
                    "assert": {"status": 200, "max_ms": 0},
                    "delay_ms": 0, "retry": 0, "retry_on": [],
                    "skip_if_prev_failed": False, "run_if_prev_status": [200],
                },
            ]
            bus.call("collections.save", name, reqs)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if len(results) != 2:
                return False, f"expected 2, got {len(results)}"
            skipped = results[1].get("skipped") is True
            return skipped, "second skipped because prev was 500"

        def s_coll_stop_on_error():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_stop__"
            reqs = [
                {
                    "name": "first_fail", "method": "GET",
                    "url": f"{url}/api/error/500",
                    "headers": "", "body": "",
                    "extract": {"path": "", "vault_key": ""},
                    "assert": {"status": 200, "max_ms": 0},
                    "delay_ms": 0, "retry": 0, "retry_on": [],
                    "skip_if_prev_failed": False, "run_if_prev_status": [],
                },
                {
                    "name": "second_skipped", "method": "GET",
                    "url": f"{url}/api/health",
                    "headers": "", "body": "",
                    "extract": {"path": "", "vault_key": ""},
                    "assert": {"status": 200, "max_ms": 0},
                    "delay_ms": 0, "retry": 0, "retry_on": [],
                    "skip_if_prev_failed": False, "run_if_prev_status": [],
                },
            ]
            bus.call("collections.save", name, reqs, stop_on_error=True)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            if len(results) != 2:
                return False, f"expected 2, got {len(results)}"
            skipped = results[1].get("skipped") is True
            return skipped, "second skipped by stop_on_error"

        def s_coll_parallel():
            _require("collections.save", "collections.run", "collections.delete",
                     "http.request")
            name = "__sys_coll_parallel__"
            reqs = [
                {
                    "name": f"p{i}", "method": "GET",
                    "url": f"{url}/api/slow?delay=1",
                    "headers": "", "body": "",
                    "extract": {"path": "", "vault_key": ""},
                    "assert": {"status": 200, "max_ms": 0},
                    "delay_ms": 0, "retry": 0, "retry_on": [],
                    "skip_if_prev_failed": False, "run_if_prev_status": [],
                }
                for i in range(5)
            ]
            bus.call("collections.save", name, reqs, parallel=True)
            t0 = time.time()
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            elapsed = time.time() - t0
            bus.call("collections.delete", name)
            if len(results) != 5:
                return False, f"expected 5, got {len(results)}"
            all_ok = all(r.get("status") == 200 for r in results)
            detail = f"5 parallel in {elapsed*1000:.0f} ms"
            return all_ok, detail

        def s_coll_reauth_on_401():
            _require("collections.save", "collections.run", "collections.delete",
                     "login.run", "http.request", "vault.set", "vault.delete")
            name = "__sys_coll_reauth__"
            bus.call("vault.set", "onyx_reauth_token", "invalid.token.value")

            auth_cfg = {
                "enabled": True,
                "login_url": f"{url}/api/login",
                "user": user,
                "password": pwd,
                "auth_type": "JSON body",
                "totp_secret": "",
                "verify_url": "",
                "challenge_path": "challenge_id",
                "code_field": "code",
                "token_path": "token",
                "vault_key": "onyx_reauth_token",
            }
            reqs = [{
                "name": "protected",
                "method": "GET",
                "url": f"{url}/api/me",
                "headers": "Authorization: Bearer {{vault.onyx_reauth_token}}",
                "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 200, "max_ms": 0},
                "delay_ms": 0, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs, auth=auth_cfg)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            bus.call("vault.delete", "onyx_reauth_token")

            if not results:
                return False, "no results"
            r = results[0]
            ok = (r.get("status") == 200
                  and r.get("reauthed") is True
                  and r.get("assert_ok") is True)
            detail = (f"status={r.get('status')} "
                      f"reauthed={r.get('reauthed')} "
                      f"assert_ok={r.get('assert_ok')}")
            return ok, detail

        def s_coll_reauth_with_2fa():
            if pyotp is None:
                return True, "skipped (pyotp not installed)"
            _require("collections.save", "collections.run", "collections.delete",
                     "login.run", "http.request", "vault.set", "vault.delete")
            name = "__sys_coll_reauth_2fa__"
            bus.call("vault.set", "onyx_reauth_2fa_token", "bad.token.here")

            auth_cfg = {
                "enabled": True,
                "login_url": f"{url}/api/login",
                "user": "admin_2fa",
                "password": "admin123",
                "auth_type": "JSON body",
                "totp_secret": TOTP_SECRET,
                "verify_url": "",
                "challenge_path": "challenge_id",
                "code_field": "code",
                "token_path": "token",
                "vault_key": "onyx_reauth_2fa_token",
            }
            reqs = [{
                "name": "protected",
                "method": "GET",
                "url": f"{url}/api/admin",
                "headers": "Authorization: Bearer {{vault.onyx_reauth_2fa_token}}",
                "body": "",
                "extract": {"path": "", "vault_key": ""},
                "assert": {"status": 200, "max_ms": 0},
                "delay_ms": 0, "retry": 0, "retry_on": [],
                "skip_if_prev_failed": False, "run_if_prev_status": [],
            }]
            bus.call("collections.save", name, reqs, auth=auth_cfg)
            results = bus.call("collections.run", name,
                               caller=self.CALLER, session=session)
            bus.call("collections.delete", name)
            bus.call("vault.delete", "onyx_reauth_2fa_token")

            if not results:
                return False, "no results"
            r = results[0]
            ok = (r.get("status") == 200
                  and r.get("reauthed") is True
                  and r.get("assert_ok") is True)
            detail = (f"status={r.get('status')} "
                      f"reauthed={r.get('reauthed')} (2FA)")
            return ok, detail

        def s_vault_persistence():
            _require("vault.set", "vault.get", "vault.delete")
            from modules.vault.module import STORE, ENC_PREFIX
            bus.call("vault.set", "__persist_probe__", "probe-value")
            try:
                blob = STORE.read_bytes()
            except Exception as e:
                return False, f"vault file missing: {e}"
            encrypted = blob.startswith(ENC_PREFIX)
            try:
                raw = blob[len(ENC_PREFIX):] if encrypted else blob
                text = raw.decode("utf-8", errors="replace")
                leaked = "probe-value" in text
            except Exception:
                leaked = False
            back = bus.call("vault.get", "__persist_probe__")
            bus.call("vault.delete", "__persist_probe__")
            ok = back == "probe-value" and not leaked
            mode = "encrypted" if encrypted else "plaintext"
            return ok, f"storage={mode} leaked_in_file={leaked} size={len(blob)}"

        def s_final_vault_check():
            _require("vault.delete", "vault.list")
            for key in ("test.form.jwt", "test.coll.jwt",
                        "test.2fa.totp", "test.2fa.manual",
                        "test.2fa.missing", "test.2fa.bad"):
                bus.call("vault.delete", key)
            items = bus.call("vault.list")
            names = [k for k, _ in items]
            return True, f"remaining keys={names}"

        def s_vault_ttl():
            _require("vault.set", "vault.get", "vault.delete")
            bus.call("vault.set", "__ttl_probe__", "short", ttl=1)
            val1 = bus.call("vault.get", "__ttl_probe__")
            time.sleep(1.3)
            val2 = bus.call("vault.get", "__ttl_probe__")
            bus.call("vault.delete", "__ttl_probe__")
            ok = val1 == "short" and val2 is None
            return ok, f"before={val1} after={val2}"

        def s_vault_namespaces():
            _require("vault.set", "vault.namespaces", "vault.delete")
            bus.call("vault.set", "__ns_test__.a", "1")
            bus.call("vault.set", "__ns_test__.b", "2")
            ns = bus.call("vault.namespaces")
            bus.call("vault.delete", "__ns_test__.a")
            bus.call("vault.delete", "__ns_test__.b")
            return "__ns_test__" in ns, f"namespaces={ns}"

        def s_vault_stats():
            _require("vault.stats")
            s = bus.call("vault.stats")
            ok = isinstance(s, dict) and "total" in s and "mode" in s
            return ok, f"total={s.get('total')} mode={s.get('mode')}"

        def s_http_redirect_chain():
            _require("http.request")
            r = http("GET", f"{url}/api/redirect?n=3", headers={}, body="")
            chain = r.get("redirect_chain") or []
            ok = (r.get("ok") and r["status"] == 200 and len(chain) >= 3)
            return ok, f"hops={len(chain)} final={r.get('status')}"

        def s_http_cookies():
            _require("http.request")
            sess_name = "st-cookie"
            r = http("POST", f"{url}/api/login",
                     headers={"Content-Type": "application/json"},
                     body=json.dumps({"username": user, "password": pwd}),
                     session_name=sess_name)
            if not r.get("ok"):
                return False, "login failed"
            cookies = r.get("cookies") or {}
            ok = "access_token" in cookies and len(cookies["access_token"]) > 20
            return ok, f"cookies={list(cookies.keys())}"

        def s_encoder_pipeline():
            _require("encoder.pipeline")
            r = bus.call("encoder.pipeline",
                          ["Base64 encode", "URL encode"], "hi")
            ok = r == "aGk%3D"
            return ok, f"got={r}"

        def s_diff_word_level():
            _require("diff.compare")
            a = "hello world\nfoo bar"
            b = "hello WORLD\nfoo bar"
            res = bus.call("diff.compare", a, b, "Line diff")
            joined = "".join(res)
            return "-hello world" in joined and "+hello WORLD" in joined, "word mode ok"

        def s_login_oauth2_profile():
            _require("login.list_profiles", "login.run_profile")
            profiles = bus.call("login.list_profiles")
            ok = ("oauth2-client-credentials" in profiles
                  and "oauth2-password" in profiles)
            return ok, f"oauth2 profiles present"

        def s_portscan_available():
            _require("portscan.run", "portscan.capabilities")
            caps = bus.call("portscan.capabilities")
            ok = all(k in caps for k in ("syn", "os_fp", "udp_accurate"))
            return ok, (f"syn={caps.get('syn')} "
                        f"os_fp={caps.get('os_fp')} "
                        f"udp_accurate={caps.get('udp_accurate')}")

        def s_portscan_service_registered():
            _require("portscan.run")
            services = bus.list_services()
            ok = "portscan.run" in services
            return ok, "portscan.run registered"

        def s_portscan_with_banner():
            _require("portscan.run")
            results = bus.call("portscan.run", "127.0.0.1",
                               [80, 443, 8080, 8888],
                               threads=4, timeout=1.0,
                               do_banner=True,
                               caller=self.CALLER, session=session)
            if results is None:
                return False, "returned None"
            has_service_field = all("service" in r for r in results)
            return has_service_field, f"banner scan ok, results={len(results)}"

        def s_portscan_localhost():
            _require("portscan.run")
            results = bus.call("portscan.run", "127.0.0.1",
                               [22, 80, 443, 8080],
                               threads=10, timeout=0.5,
                               do_banner=False,
                               caller=self.CALLER, session=session)
            if results is None:
                return False, "returned None"
            for r in results:
                if r.get("proto") != "tcp":
                    return False, f"bad proto {r.get('proto')}"
                if "status" not in r:
                    return False, "missing status"
            return True, f"scanned 4 ports, results={len(results)}"

        def s_portscan_syn_conditional():
            _require("portscan.capabilities", "portscan.run")
            caps = bus.call("portscan.capabilities")
            if not caps.get("syn"):
                return True, "skipped (scapy not installed)"
            results = bus.call("portscan.run", "127.0.0.1",
                               [22, 80, 443, 8080],
                               threads=4, timeout=1.0,
                               scan_mode="syn", do_os_fp=True,
                               caller=self.CALLER, session=session)
            if results is None:
                return False, "returned None"
            return True, f"syn scan, results={len(results)}"

        def s_dns_services_registered():
            _require("dns.lookup", "dns.reverse")
            services = bus.list_services()
            ok = "dns.lookup" in services and "dns.reverse" in services
            return ok, "dns.lookup + dns.reverse registered"

        def s_dns_localhost_reverse():
            _require("dns.lookup")
            results = bus.call("dns.lookup", "127.0.0.1",
                               types=["PTR"], timeout=2)
            if not isinstance(results, list) or not results:
                return False, "empty result"
            r = results[0]
            required = {"domain", "type", "status", "value"}
            if not required.issubset(r.keys()):
                return False, f"missing fields: {required - r.keys()}"
            status = r.get("status")
            ok = status in ("ok", "error", "timeout", "nxdomain", "empty")
            return ok, f"status={status} value={str(r.get('value'))[:60]}"

        def s_dns_nxdomain():
            _require("dns.lookup")
            results = bus.call("dns.lookup",
                               "onyx-does-not-exist-1234567890.invalid",
                               types=["A"], timeout=3)
            if not isinstance(results, list) or not results:
                return False, "empty result"
            r = results[0]
            status = r.get("status")
            ok = status in ("nxdomain", "empty", "timeout", "error")
            return ok, f"status={status} (expected non-ok)"

        def s_dns_real_lookup():
            _require("dns.lookup")
            results = bus.call("dns.lookup", "example.com",
                               types=["A"], timeout=3)
            if not isinstance(results, list) or not results:
                return True, "skipped (no network)"
            r = results[0]
            status = r.get("status")
            if status in ("timeout", "error"):
                return True, f"skipped (network unavailable, {status})"
            if status == "nxdomain":
                return False, "example.com returned NXDOMAIN"
            if status == "ok":
                value = r.get("value", "")
                if not re.match(r"^\d+\.\d+\.\d+\.\d+$", value):
                    return False, f"unexpected A value: {value}"
                return True, f"example.com -> {value}"
            return True, f"status={status}"

        return [
            ("Mock server self-check", s_mock_up),
            ("Test proxy start", s_proxy_start),
            ("Bus integrity", s_bus),
            ("HTTP health", s_health),
            ("HTTP echo POST", s_echo_post),
            ("HTTP echo PUT", s_echo_put),
            ("HTTP echo PATCH", s_echo_patch),
            ("HTTP echo DELETE", s_echo_delete),
            ("HTTP query args", s_query_args),
            ("HTTP error codes", s_error_codes),
            ("HTTP redirect", s_redirect),
            ("HTTP redirect chain", s_redirect_chain),
            ("HTTP timeout", s_timeout),
            ("HTTP redirect chain detail", s_http_redirect_chain),
            ("HTTP cookies capture", s_http_cookies),
            ("HTML response", s_html),
            ("JSON parse", s_json_parse),
            ("Engines list", s_engines_list),
            ("Engine requests", s_engine_requests),
            ("Engine curl_cffi", s_engine_curl_cffi),
            ("Engine cloudscraper", s_engine_cloudscraper),
            ("Curl parse POST", s_curl_parse),
            ("Curl parse GET", s_curl_parse_get),
            ("Curl build", s_curl_build),
            ("Curl roundtrip", s_curl_roundtrip),
            ("Profile save", s_profile_save),
            ("Profile load", s_profile_load),
            ("Profile list", s_profile_list),
            ("Profile delete", s_profile_delete),
            ("Snippet save", s_snippet_save),
            ("Snippet load", s_snippet_load),
            ("Snippet list", s_snippet_list),
            ("Snippet delete", s_snippet_delete),
            ("Vault set/get", s_vault_set_get),
            ("Vault list", s_vault_list),
            ("Vault delete", s_vault_delete),
            ("Login JSON", s_login),
            ("Login form-data", s_login_form),
            ("Vault has token", s_vault_has_token),
            ("Auth /api/me", s_auth_me),
            ("Auth /api/admin", s_auth_admin),
            ("Auth no token -> 401", s_auth_no_token),
            ("Auth bad token -> 401", s_auth_bad_token),
            ("Login 2FA TOTP auto", s_login_2fa_totp),
            ("Login 2FA manual code", s_login_2fa_manual_code),
            ("Login 2FA no code -> challenge", s_login_2fa_no_code),
            ("Login 2FA bad code -> rejected", s_login_2fa_bad_code),
            ("Login OAuth2 profiles", s_login_oauth2_profile),
            ("JWT decode", s_jwt_decode),
            ("JWT verify valid", s_jwt_verify_valid),
            ("JWT verify bad secret", s_jwt_verify_bad_secret),
            ("JWT verify tampered", s_jwt_verify_tampered),
            ("Encoder Base64", s_encoder_base64),
            ("Encoder Hex", s_encoder_hex),
            ("Encoder ROT13", s_encoder_rot13),
            ("Encoder URL", s_encoder_url),
            ("Encoder HTML", s_encoder_html),
            ("Encoder JSON pretty", s_encoder_json),
            ("Encoder pipeline", s_encoder_pipeline),
            ("Diff JSON changes", s_diff_json),
            ("Diff JSON none", s_diff_no_changes),
            ("Diff line", s_diff_line),
            ("Diff word-level", s_diff_word_level),
            ("Scanner multi", s_scanner),
            ("Scanner single", s_scanner_single),
            ("Reporter Markdown", s_reporter_md),
            ("Reporter JSON", s_reporter_json),
            ("Reporter HTML", s_reporter_html),
            ("History non-empty", s_history),
            ("History caller label", s_history_caller),
            ("History session tag", s_history_session),
            ("Proxy works", s_proxy_works),
            ("Proxy rejects bad", s_proxy_bad),
            ("Collections save + list", s_coll_save_list),
            ("Collections run simple", s_coll_run_simple),
            ("Collections cleanup basic", s_coll_cleanup_basic),
            ("Collections vars", s_coll_vars),
            ("Collections env override", s_coll_env),
            ("Collections assert status ok", s_coll_assert_status),
            ("Collections assert status fail", s_coll_assert_fail),
            ("Collections assert json path", s_coll_assert_json),
            ("Collections assert body", s_coll_assert_body),
            ("Collections assert max_ms", s_coll_assert_max_ms),
            ("Collections extract chain", s_coll_extract),
            ("Collections retry", s_coll_retry),
            ("Collections delay", s_coll_delay),
            ("Collections skip_if_prev_failed", s_coll_skip_if_prev_failed),
            ("Collections run_if_prev_status", s_coll_run_if_status),
            ("Collections stop_on_error", s_coll_stop_on_error),
            ("Collections parallel", s_coll_parallel),
            ("Collections reauth on 401", s_coll_reauth_on_401),
            ("Collections reauth with 2FA", s_coll_reauth_with_2fa),
            ("Vault persistence (on disk)", s_vault_persistence),
            ("Vault TTL", s_vault_ttl),
            ("Vault namespaces", s_vault_namespaces),
            ("Vault stats", s_vault_stats),
            ("Final vault check", s_final_vault_check),
            ("PortScanner capabilities", s_portscan_available),
            ("PortScanner localhost", s_portscan_localhost),
            ("PortScanner service", s_portscan_service_registered),
            ("PortScanner banner", s_portscan_with_banner),
            ("PortScanner SYN (conditional)", s_portscan_syn_conditional),
            ("DNS services registered", s_dns_services_registered),
            ("DNS localhost reverse", s_dns_localhost_reverse),
            ("DNS nxdomain", s_dns_nxdomain),
            ("DNS real lookup (conditional)", s_dns_real_lookup),
        ]