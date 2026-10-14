import base64
import json
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPlainTextEdit, QPushButton, QGroupBox, QFormLayout, QMessageBox,
    QInputDialog, QFileDialog, QDialog
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.i18n import tr
from core.utils import color as _c, parent_widget, extract_by_path
from styles import loader as theme_loader

try:
    import pyotp
except ImportError:
    pyotp = None


CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "login_profiles"


TOKEN_HINTS = ("token", "access_token", "accessToken", "id_token",
               "idToken", "jwt", "auth_token", "authToken",
               "bearer", "session", "sessionToken", "authToken")


def _auto_find_token(data):
    if not isinstance(data, dict):
        return None
    for k, v in data.items():
        if isinstance(v, str) and v.count(".") == 2 and len(v) > 40:
            if k.lower() in ("token", "jwt", "accesstoken", "idtoken",
                             "auth_token", "access_token"):
                return k
            parts = v.split(".")
            try:
                base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4))
                return k
            except Exception:
                pass
    for k, v in data.items():
        if isinstance(v, str) and any(h in k.lower() for h in
                                       ("token", "jwt", "bearer", "session")):
            if len(v) > 20:
                return k
    return None


SYSTEM_PROFILES = {
    "generic-json": {
        "name": "generic-json",
        "display_name": "Generic JSON (username/password -> token)",
        "system": True,
        "login_url": "",
        "headers": {"Content-Type": "application/json"},
        "vault_key_default": "login.token",
        "inputs": [],
        "steps": [
            {
                "name": "login",
                "body": {"username": "{user}", "password": "{password}"},
                "body_type": "json",
                "extract": {"token": "token"},
                "auto_detect_token": True,
            }
        ],
    },
    "generic-form": {
        "name": "generic-form",
        "display_name": "Generic Form data",
        "system": True,
        "login_url": "",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "vault_key_default": "login.token",
        "inputs": [],
        "steps": [
            {
                "name": "login",
                "body": "username={user}&password={password}",
                "body_type": "raw",
                "extract": {"token": "token"},
                "auto_detect_token": True,
            }
        ],
    },
    "generic-basic": {
        "name": "generic-basic",
        "display_name": "HTTP Basic Auth",
        "system": True,
        "login_url": "",
        "headers": {},
        "vault_key_default": "login.token",
        "inputs": [],
        "steps": [
            {
                "name": "login",
                "body": "",
                "body_type": "raw",
                "basic_auth": "{user}:{password}",
                "extract": {"token": "token"},
                "auto_detect_token": True,
            }
        ],
    },
    "generic-totp": {
        "name": "generic-totp",
        "display_name": "Generic TOTP (email/SMS fallback prompt)",
        "system": True,
        "login_url": "",
        "headers": {"Content-Type": "application/json"},
        "vault_key_default": "login.token",
        "inputs": [],
        "steps": [
            {
                "name": "login",
                "body": {"username": "{user}", "password": "{password}"},
                "body_type": "json",
                "detect_2fa_path": "two_factor_auth",
            },
            {
                "name": "submit_code",
                "body": {
                    "username": "{user}",
                    "password": "{password}",
                    "code": "{code}",
                },
                "body_type": "json",
                "requires_code": True,
                "use_totp": True,
                "extract": {"token": "token"},
                "auto_detect_token": True,
            },
        ],
    },
    "generic-register": {
        "name": "generic-register",
        "display_name": "Generic registration (username/email/password)",
        "system": True,
        "login_url": "",
        "headers": {"Content-Type": "application/json"},
        "vault_key_default": "login.token",
        "inputs": [
            {"key": "email", "label": "Email",
             "source": "user", "required": True},
            {"key": "password_confirm", "label": "Confirm password",
             "source": "password", "required": True},
        ],
        "steps": [
            {
                "name": "register",
                "body": {
                    "username": "{user}",
                    "email": "{email}",
                    "password": "{password}",
                    "password_confirm": "{password_confirm}",
                },
                "body_type": "json",
                "extract": {"token": "token"},
                "auto_detect_token": True,
            }
        ],
    },
    "oauth2-client-credentials": {
        "name": "oauth2-client-credentials",
        "display_name": "OAuth2 Client Credentials",
        "system": True,
        "login_url": "",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "vault_key_default": "oauth2.token",
        "inputs": [
            {"key": "client_id", "label": "Client ID",
             "required": True, "placeholder": "client_id"},
            {"key": "client_secret", "label": "Client Secret",
             "required": True, "password": True},
            {"key": "scope", "label": "Scope",
             "required": False, "placeholder": "read write"},
        ],
        "steps": [
            {
                "name": "token_request",
                "body": "grant_type=client_credentials&client_id={client_id}"
                        "&client_secret={client_secret}&scope={scope}",
                "body_type": "raw",
                "auto_detect_token": True,
                "extract": {"token": "access_token"},
            }
        ],
    },
    "oauth2-password": {
        "name": "oauth2-password",
        "display_name": "OAuth2 Password Grant",
        "system": True,
        "login_url": "",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "vault_key_default": "oauth2.token",
        "inputs": [
            {"key": "client_id", "label": "Client ID",
             "required": True, "placeholder": "client_id"},
            {"key": "client_secret", "label": "Client Secret",
             "required": False, "password": True},
            {"key": "scope", "label": "Scope", "required": False},
        ],
        "steps": [
            {
                "name": "token_request",
                "body": "grant_type=password&username={user}&password={password}"
                        "&client_id={client_id}&client_secret={client_secret}"
                        "&scope={scope}",
                "body_type": "raw",
                "auto_detect_token": True,
                "extract": {"token": "access_token"},
            }
        ],
    },
    "oauth2-refresh": {
        "name": "oauth2-refresh",
        "display_name": "OAuth2 Refresh Token",
        "system": True,
        "login_url": "",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "vault_key_default": "oauth2.token",
        "inputs": [
            {"key": "client_id", "label": "Client ID", "required": True},
            {"key": "client_secret", "label": "Client Secret",
             "required": False, "password": True},
            {"key": "refresh_token", "label": "Refresh token",
             "required": True, "password": True},
        ],
        "steps": [
            {
                "name": "refresh",
                "body": "grant_type=refresh_token&refresh_token={refresh_token}"
                        "&client_id={client_id}&client_secret={client_secret}",
                "body_type": "raw",
                "auto_detect_token": True,
                "extract": {"token": "access_token"},
            }
        ],
    },
}


NEW_PROFILE_TEMPLATE = {
    "name": "new-profile",
    "display_name": "My custom profile",
    "login_url": "https://example.com/api/login/",
    "headers": {
        "Content-Type": "application/json",
        "accept": "*/*",
        "user-agent": "Mozilla/5.0",
    },
    "vault_key_default": "login.token",
    "inputs": [],
    "steps": [
        {
            "name": "login",
            "body": {"username": "{user}", "password": "{password}"},
            "body_type": "json",
            "extract": {"token": "token"},
            "auto_detect_token": True,
        }
    ],
}


class JsonEditorDialog(QDialog):
    def __init__(self, parent, title, initial_text):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(720, 560)
        self.resize(900, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.hint = QLabel(tr("dialog.json.hint", module="login_flow"))
        self.hint.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.hint)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 10))
        self.editor.setPlainText(initial_text)
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setTabStopDistance(32)
        layout.addWidget(self.editor, 1)

        self.status = QLabel("")
        self.status.setFont(QFont("Consolas", 9))
        layout.addWidget(self.status)

        self.editor.textChanged.connect(self._validate)

        btns = QHBoxLayout()
        btns.addStretch()

        self.pretty_btn = QPushButton(tr("dialog.json.pretty", module="login_flow"))
        self.pretty_btn.setFixedWidth(140)
        self.pretty_btn.clicked.connect(self._pretty)
        btns.addWidget(self.pretty_btn)

        self.copy_btn = QPushButton(tr("dialog.json.copy", module="login_flow"))
        self.copy_btn.setFixedWidth(120)
        self.copy_btn.clicked.connect(self._copy)
        btns.addWidget(self.copy_btn)

        self.paste_btn = QPushButton(tr("dialog.json.paste", module="login_flow"))
        self.paste_btn.setFixedWidth(140)
        self.paste_btn.clicked.connect(self._paste)
        btns.addWidget(self.paste_btn)

        self.cancel_btn = QPushButton(tr("dialog.json.cancel", module="login_flow"))
        self.cancel_btn.setFixedWidth(140)
        self.cancel_btn.clicked.connect(self.reject)
        btns.addWidget(self.cancel_btn)

        self.ok_btn = QPushButton(tr("dialog.json.ok", module="login_flow"))
        self.ok_btn.setFixedWidth(120)
        self.ok_btn.clicked.connect(self._try_accept)
        btns.addWidget(self.ok_btn)

        layout.addLayout(btns)

        self._validate()

    def _validate(self):
        text = self.editor.toPlainText().strip()
        if not text:
            self.status.setText(tr("dialog.json.empty", module="login_flow"))
            self.status.setStyleSheet("color: #ffb84d;")
            return
        try:
            json.loads(text)
            self.status.setText(tr("dialog.json.valid", module="login_flow"))
            self.status.setStyleSheet("color: #66d9a0;")
        except Exception as e:
            self.status.setText(tr("dialog.json.invalid", module="login_flow", error=e))
            self.status.setStyleSheet("color: #ff4d6d;")

    def _pretty(self):
        try:
            data = json.loads(self.editor.toPlainText())
        except Exception as e:
            QMessageBox.warning(self, "Pretty failed", str(e))
            return
        self.editor.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))

    def _copy(self):
        QGuiApplication.clipboard().setText(self.editor.toPlainText())

    def _paste(self):
        self.editor.setPlainText(QGuiApplication.clipboard().text())

    def _try_accept(self):
        try:
            json.loads(self.editor.toPlainText())
        except Exception as e:
            QMessageBox.warning(self, "Invalid JSON", str(e))
            return
        self.accept()

    def text(self):
        return self.editor.toPlainText()


def _is_system(name: str) -> bool:
    return name in SYSTEM_PROFILES


def _list_profiles() -> list:
    names = set(SYSTEM_PROFILES.keys())
    if CONFIG_DIR.exists():
        for p in CONFIG_DIR.iterdir():
            if p.suffix == ".json" and not p.name.startswith("_"):
                names.add(p.stem)
    return sorted(names)


def _load_profile(name: str) -> dict:
    user_path = CONFIG_DIR / f"{name}.json"
    if user_path.exists():
        try:
            with open(user_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("name", name)
            data["system"] = False
            data.setdefault("inputs", [])
            return data
        except Exception as e:
            raise ValueError(f"cannot read profile {name}: {e}")
    if name in SYSTEM_PROFILES:
        return json.loads(json.dumps(SYSTEM_PROFILES[name]))
    raise FileNotFoundError(f"profile '{name}' not found")


def _save_profile(profile: dict, name: str) -> bool:
    if _is_system(name):
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    profile = dict(profile)
    profile["name"] = name
    profile["system"] = False
    try:
        with open(CONFIG_DIR / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _delete_profile(name: str) -> bool:
    if _is_system(name):
        return False
    path = CONFIG_DIR / f"{name}.json"
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except Exception:
        return False


def _render_str(template, ctx):
    if not isinstance(template, str):
        return template
    out = template
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _render_body(body, ctx):
    if isinstance(body, str):
        return _render_str(body, ctx)
    if isinstance(body, dict):
        return {k: _render_body(v, ctx) for k, v in body.items()}
    if isinstance(body, list):
        return [_render_body(v, ctx) for v in body]
    return body


def _extract(data, path):
    if not path:
        return None
    return extract_by_path(data, path)


def _check_condition(cond, parsed, ctx):
    if cond is None:
        return True
    if cond == "always":
        return True
    if cond == "method_required":
        return bool(ctx.get("method"))
    if cond == "method_required_without_code":
        return bool(ctx.get("method")) and not ctx.get("code")
    if cond == "code_required":
        return bool(ctx.get("code"))
    if cond == "challenge_present":
        return bool(ctx.get("challenge"))
    if cond == "no_token_yet":
        return not ctx.get("_token")
    if isinstance(cond, dict):
        actual = _extract(parsed, cond.get("path", ""))
        if "equals" in cond:
            return actual == cond["equals"]
        if "in" in cond:
            return actual in cond["in"]
        if "truthy" in cond:
            return bool(actual) == bool(cond["truthy"])
    return False


def _execute_profile(profile, user, password, code=None, method=None,
                     totp_secret=None, vault_key=None, extra=None,
                     caller=None, session=None):
    if not profile:
        return {"ok": False, "error": "empty profile"}

    login_url = profile.get("login_url") or ""
    if not login_url:
        return {"ok": False, "error": "profile has no login_url"}

    headers = profile.get("headers") or {}
    vault_key = vault_key or profile.get("vault_key_default") or "login.token"

    ctx = {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            if isinstance(k, str) and k:
                ctx[k] = v
    ctx["user"] = user or ""
    ctx["password"] = password or ""
    ctx["code"] = code or ""
    ctx["method"] = method or ""
    ctx["challenge"] = ""

    bus = get_bus()
    last_parsed = None
    two_factor_used = False

    steps = profile.get("steps") or []
    if not steps:
        return {"ok": False, "error": "profile has no steps"}

    for idx, step in enumerate(steps):
        step_name = step.get("name") or f"step {idx + 1}"

        if not _check_condition(step.get("condition"), last_parsed, ctx):
            continue

        if step.get("use_totp") and totp_secret:
            if pyotp is None:
                return {"ok": False,
                        "error": "pyotp not installed for TOTP step",
                        "step": step_name}
            try:
                ctx["code"] = pyotp.TOTP(totp_secret).now()
            except Exception as e:
                return {"ok": False, "error": f"totp failed: {e}",
                        "step": step_name}

        if step.get("requires_code") and not ctx["code"]:
            if totp_secret:
                if pyotp is None:
                    return {"ok": False, "error": "pyotp not installed",
                            "step": step_name}
                try:
                    ctx["code"] = pyotp.TOTP(totp_secret).now()
                except Exception as e:
                    return {"ok": False, "error": f"totp failed: {e}",
                            "step": step_name}
            else:
                return {
                    "ok": False,
                    "requires_code": True,
                    "requires_2fa": True,
                    "challenge": ctx.get("challenge"),
                    "challenge_path": step.get("challenge_capture_path")
                                      or "challenge_id",
                    "message": step.get("code_prompt")
                               or "Enter verification code",
                    "step": step_name,
                }

        body_template = step.get("body", {})
        body_type = step.get("body_type", "json")
        body_rendered = _render_body(body_template, ctx)

        if body_type == "json":
            if isinstance(body_rendered, (dict, list)):
                body_str = json.dumps(body_rendered, ensure_ascii=False)
            else:
                body_str = str(body_rendered)
            req_headers = dict(headers)
            req_headers.setdefault("Content-Type", "application/json")
        elif body_type == "form":
            if isinstance(body_rendered, dict):
                body_str = "&".join(f"{k}={v}" for k, v in body_rendered.items())
            else:
                body_str = str(body_rendered)
            req_headers = dict(headers)
            req_headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body_str = str(body_rendered) if body_rendered is not None else ""
            req_headers = dict(headers)

        if step.get("basic_auth"):
            creds_str = _render_str(step["basic_auth"], ctx)
            creds = base64.b64encode(creds_str.encode()).decode()
            req_headers["Authorization"] = f"Basic {creds}"

        url = _render_str(step.get("url") or login_url, ctx)
        method_http = (step.get("method") or "POST").upper()

        try:
            kwargs = dict(headers=req_headers, body=body_str, timeout=30)
            if caller:
                kwargs["caller"] = caller
            if session:
                kwargs["session"] = session
            if step.get("use_session"):
                kwargs["session_name"] = step["use_session"]
            resp = bus.call("http.request", method_http, url, **kwargs)
        except KeyError:
            return {"ok": False, "error": "http.request unavailable",
                    "step": step_name}
        except Exception as e:
            return {"ok": False, "error": str(e), "step": step_name}

        if not resp.get("ok"):
            return {"ok": False, "error": resp.get("error"),
                    "step": step_name}

        status = resp.get("status")
        try:
            parsed = json.loads(resp.get("body") or "")
        except Exception:
            parsed = {}
        last_parsed = parsed

        if not (200 <= (status or 0) < 300):
            err_msg = None
            if isinstance(parsed, dict):
                err_msg = (parsed.get("msg")
                           or parsed.get("error")
                           or parsed.get("detail"))
            return {
                "ok": False,
                "error": err_msg or f"status {status}",
                "status": status,
                "step": step_name,
                "body_preview": (resp.get("body") or "")[:300],
            }

        if step.get("challenge_capture_path"):
            cv = _extract(parsed, step["challenge_capture_path"])
            if cv is not None:
                ctx["challenge"] = cv

        if step.get("detect_2fa_path"):
            tf = _extract(parsed, step["detect_2fa_path"])
            if "detect_2fa_value" in step:
                triggered = (tf == step["detect_2fa_value"])
            else:
                triggered = (tf is not None and tf is not False and tf != "")

            if triggered:
                two_factor_used = True
                methods_raw = _extract(parsed, step.get("methods_path") or "") or []
                method_field = step.get("method_name_path") or "type"
                available_methods = []
                if isinstance(methods_raw, list):
                    for m in methods_raw:
                        if isinstance(m, dict):
                            mname = m.get(method_field)
                            if mname:
                                available_methods.append({
                                    "value": mname,
                                    "label": m.get("name") or mname,
                                })

                if not ctx["method"] and available_methods:
                    if len(available_methods) == 1:
                        ctx["method"] = available_methods[0]["value"]
                    else:
                        return {
                            "ok": False,
                            "requires_method": True,
                            "methods": available_methods,
                            "message": (step.get("method_prompt")
                                        or "Choose 2FA method"),
                            "step": step_name,
                        }

        if step.get("detect_captcha_path"):
            cap = _extract(parsed, step["detect_captcha_path"])
            if cap:
                return {
                    "ok": False,
                    "requires_captcha": True,
                    "captcha_url": _render_str(
                        step.get("captcha_url") or url, ctx),
                    "message": step.get("captcha_message")
                               or "CAPTCHA required — solve manually and paste value",
                    "step": step_name,
                }

        extract_map = step.get("extract") or {}
        token_path = extract_map.get("token")
        if not token_path and step.get("auto_detect_token"):
            detected = _auto_find_token(parsed)
            if detected:
                token_path = detected
        if token_path:
            token = _extract(parsed, token_path)
            if token:
                extras = {}
                for k, p in extract_map.items():
                    if k == "token" or not p:
                        continue
                    v = _extract(parsed, p)
                    if v is not None:
                        extras[k] = v
                try:
                    bus.call("vault.set", vault_key, str(token))
                except KeyError:
                    return {"ok": False, "error": "vault unavailable",
                            "token": str(token)}
                return {
                    "ok": True,
                    "token": str(token),
                    "vault_key": vault_key,
                    "extras": extras,
                    "steps_executed": idx + 1,
                    "two_factor_used": two_factor_used,
                    "token_path_used": token_path,
                    "auto_detected": bool(step.get("auto_detect_token")
                                          and not extract_map.get("token")),
                }

    return {"ok": False, "error": "no token extracted"}


class LoginWorker(QThread):
    done = Signal(dict)

    def __init__(self, profile, user, password, code=None, method=None,
                 totp_secret=None, vault_key=None, extra=None,
                 caller=None, session=None):
        super().__init__()
        self._profile = profile
        self._user = user
        self._password = password
        self._code = code
        self._method = method
        self._totp_secret = totp_secret
        self._vault_key = vault_key
        self._extra = extra
        self._caller = caller
        self._session = session

    def run(self):
        try:
            r = _execute_profile(
                self._profile, self._user, self._password,
                code=self._code, method=self._method,
                totp_secret=self._totp_secret,
                vault_key=self._vault_key,
                extra=self._extra,
                caller=self._caller, session=self._session,
            )
        except Exception as e:
            r = {"ok": False, "error": str(e)}
        self.done.emit(r or {"ok": False, "error": "empty"})


class LoginFlowModule(BaseModule):
    def __init__(self):
        self._widget = None
        self._worker = None
        self._last_token = None
        self._pending_code = None
        self._pending_method = None
        self._inputs_container_layout = None
        self._input_widgets = {}
        self._input_sources = {}
        self._input_cache = {}
        self._lbls = {}
        self._btns = {}
        self._cred_labels = {}
        self._prev_profile = None

    def on_load(self, api):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        api.register_service(self.name, "login.run", self._svc_login_legacy,
                             "login.run(url, user, password, auth_type='JSON body', "
                             "token_path='token', vault_key='login.token', "
                             "totp_secret=None, verify_url=None, "
                             "challenge_path='challenge_id', code_field='code', "
                             "code=None, extra=None, caller=None, session=None) -> dict")
        api.register_service(self.name, "login.run_2fa", self._svc_login_legacy,
                             "login.run_2fa(...) — alias of login.run with 2FA support")
        api.register_service(self.name, "login.run_profile", self._svc_run_profile,
                             "login.run_profile(profile_name, user, password, "
                             "code=None, method=None, totp_secret=None, "
                             "vault_key=None, extra=None, "
                             "caller=None, session=None) -> dict")
        api.register_service(self.name, "login.list_profiles", _list_profiles,
                             "login.list_profiles() -> list[str]")

    def on_shutdown(self):
        w = getattr(self, "_worker", None)
        if w is not None:
            try:
                w.wait(5000)
            except Exception:
                pass
        self._worker = None

    def _svc_run_profile(self, profile_name, user, password,
                         code=None, method=None, totp_secret=None,
                         vault_key=None, extra=None, caller=None, session=None):
        try:
            profile = _load_profile(profile_name)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return _execute_profile(
            profile, user, password,
            code=code, method=method,
            totp_secret=totp_secret, vault_key=vault_key,
            extra=extra,
            caller=caller, session=session,
        )

    def _svc_login_legacy(self, url, user, password,
                          auth_type="JSON body",
                          token_path="token",
                          vault_key="login.token",
                          totp_secret=None, verify_url=None,
                          challenge_path="challenge_id",
                          code_field="code", code=None, extra=None,
                          caller=None, session=None):
        if not url:
            return {"ok": False, "error": "url empty"}

        if not verify_url:
            base = url.rsplit("/", 1)[0]
            verify_url = base + "/2fa/verify"

        profile = {
            "name": "adhoc",
            "login_url": url,
            "headers": {},
            "vault_key_default": vault_key,
            "steps": [],
        }

        if auth_type == "JSON body":
            profile["headers"] = {"Content-Type": "application/json"}
            step1 = {
                "name": "login",
                "body": {"username": "{user}", "password": "{password}"},
                "body_type": "json",
            }
        elif auth_type == "Form data":
            profile["headers"] = {
                "Content-Type": "application/x-www-form-urlencoded",
            }
            step1 = {
                "name": "login",
                "body": "username={user}&password={password}",
                "body_type": "form",
            }
        elif auth_type == "Basic Auth":
            step1 = {
                "name": "login",
                "body": "",
                "body_type": "raw",
                "basic_auth": "{user}:{password}",
            }
        elif auth_type == "Custom":
            step1 = {
                "name": "login",
                "body": password,
                "body_type": "raw",
            }
            profile["headers"] = {"Content-Type": "application/json"}
        else:
            return {"ok": False, "error": f"unknown auth_type {auth_type}"}

        step1["extract"] = {"token": token_path}
        step1["detect_2fa_path"] = challenge_path
        step1["challenge_capture_path"] = challenge_path
        step1["auto_detect_token"] = True

        step2 = {
            "name": "verify_2fa",
            "url": verify_url,
            "body": {challenge_path: "{challenge}", code_field: "{code}"},
            "body_type": "json",
            "condition": "challenge_present",
            "requires_code": True,
            "challenge_capture_path": challenge_path,
            "extract": {"token": token_path},
            "auto_detect_token": True,
        }

        profile["steps"] = [step1, step2]

        return _execute_profile(
            profile, user, password,
            code=code, method=None,
            totp_secret=totp_secret, vault_key=vault_key,
            extra=extra,
            caller=caller, session=session,
        )

    def create_widget(self) -> QWidget:
        self._input_widgets = {}
        self._input_sources = {}
        self._inputs_box = None
        self._prev_profile = None

        root = QWidget()
        self._widget = root

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        mono = QFont("Consolas", 10)

        self._lbls["title"] = QLabel()
        self._lbls["title"].setStyleSheet(
            f"color: {_c('fg')}; font-size: 13px; font-weight: bold;")
        layout.addWidget(self._lbls["title"])

        self._lbls["hint"] = QLabel()
        self._lbls["hint"].setWordWrap(True)
        self._lbls["hint"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self._lbls["hint"])

        bar = QHBoxLayout()
        self._lbls["profile"] = QLabel()
        self._lbls["profile"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self._lbls["profile"])
        self.profile_box = QComboBox()
        self.profile_box.setFont(mono)
        self.profile_box.setMinimumWidth(260)
        bar.addWidget(self.profile_box, 1)

        self._btns["refresh"] = QPushButton()
        self._btns["refresh"].setFixedWidth(130)
        self._btns["refresh"].clicked.connect(self._refresh_profiles)
        bar.addWidget(self._btns["refresh"])

        self._btns["import"] = QPushButton()
        self._btns["import"].setFixedWidth(120)
        self._btns["import"].clicked.connect(self._import_profile)
        bar.addWidget(self._btns["import"])

        self._btns["export"] = QPushButton()
        self._btns["export"].setFixedWidth(120)
        self._btns["export"].clicked.connect(self._export_profile)
        bar.addWidget(self._btns["export"])

        layout.addLayout(bar)

        bar2 = QHBoxLayout()

        self._btns["new"] = QPushButton()
        self._btns["new"].setFixedWidth(120)
        self._btns["new"].clicked.connect(self._new_profile)
        bar2.addWidget(self._btns["new"])

        self._btns["view"] = QPushButton()
        self._btns["view"].setFixedWidth(190)
        self._btns["view"].clicked.connect(self._view_or_edit_profile)
        bar2.addWidget(self._btns["view"])

        self._btns["delete"] = QPushButton()
        self._btns["delete"].setFixedWidth(130)
        self._btns["delete"].clicked.connect(self._delete_profile_ui)
        bar2.addWidget(self._btns["delete"])

        bar2.addStretch()
        layout.addLayout(bar2)

        url_row = QHBoxLayout()
        self._lbls["url"] = QLabel()
        self._lbls["url"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        url_row.addWidget(self._lbls["url"])
        self.url_input = QLineEdit()
        self.url_input.setFont(mono)
        url_row.addWidget(self.url_input, 1)
        layout.addLayout(url_row)

        self.cred_group = QGroupBox()
        form = QFormLayout(self.cred_group)

        self.user_input = QLineEdit()
        self.user_input.setFont(mono)
        self._cred_labels["login"] = QLabel()
        form.addRow(self._cred_labels["login"], self.user_input)

        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.Password)
        self._cred_labels["password"] = QLabel()
        form.addRow(self._cred_labels["password"], self.pass_input)

        self.vault_key_input = QLineEdit()
        self.vault_key_input.setFont(mono)
        self._cred_labels["vault"] = QLabel()
        form.addRow(self._cred_labels["vault"], self.vault_key_input)

        self.totp_input = QLineEdit()
        self.totp_input.setFont(mono)
        self._cred_labels["totp"] = QLabel()
        form.addRow(self._cred_labels["totp"], self.totp_input)

        self.cred_form = form
        layout.addWidget(self.cred_group)

        self._inputs_container = QWidget()
        self._inputs_container_layout = QVBoxLayout(self._inputs_container)
        self._inputs_container_layout.setContentsMargins(0, 0, 0, 0)
        self._inputs_container_layout.setSpacing(8)
        layout.addWidget(self._inputs_container)

        self.log = QPlainTextEdit()
        self.log.setFont(mono)
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

        btns = QHBoxLayout()
        btns.addStretch()

        self._btns["copy_token"] = QPushButton()
        self._btns["copy_token"].setFixedWidth(170)
        self._btns["copy_token"].clicked.connect(self._copy_token)
        btns.addWidget(self._btns["copy_token"])

        self.login_btn = QPushButton()
        self.login_btn.setIcon(theme_loader.get_icon(icon_name="play", emoji=">", size=14))
        self.login_btn.setFixedWidth(190)
        self.login_btn.clicked.connect(self._do_login)
        btns.addWidget(self.login_btn)

        layout.addLayout(btns)

        self.profile_box.currentTextChanged.connect(self._on_profile_changed)

        self.retranslate()
        self._refresh_profiles()
        return root

    def retranslate(self):
        try:
            if not self.profile_box:
                return
        except (RuntimeError, AttributeError):
            return

        def t(k, **kw):
            return tr(k, module="login_flow", **kw)

        self._lbls["title"].setText(t("title"))
        self._lbls["hint"].setText(t("hint"))
        self._lbls["profile"].setText(t("profile"))
        self._btns["refresh"].setText(t("refresh"))
        self._btns["import"].setText(t("import"))
        self._btns["export"].setText(t("export"))
        self._btns["new"].setText(t("new"))
        self._btns["delete"].setText(t("delete"))
        self._lbls["url"].setText(t("url"))
        self.url_input.setPlaceholderText(t("url.placeholder"))

        self.cred_group.setTitle(t("cred.title"))
        self._cred_labels["login"].setText(t("cred.login"))
        self._cred_labels["password"].setText(t("cred.password"))
        self._cred_labels["vault"].setText(t("cred.vault"))
        self._cred_labels["totp"].setText(t("cred.totp"))
        self.user_input.setPlaceholderText(t("cred.login.placeholder"))
        self.pass_input.setPlaceholderText(t("cred.password.placeholder"))
        self.vault_key_input.setPlaceholderText(t("url.placeholder"))
        self.totp_input.setPlaceholderText(t("cred.totp.placeholder"))

        self._btns["copy_token"].setText(t("copy_token"))
        self.login_btn.setText(t("login"))

        self._update_view_btn_label()
        if self._prev_profile:
            self._on_profile_changed(self._prev_profile, log=False)

    def _update_view_btn_label(self):
        name = self.profile_box.currentText()
        is_sys = _is_system(name) if name else False
        key = "view_json" if is_sys else "edit_json"
        self._btns["view"].setText(tr(key, module="login_flow"))

    def _clear_inputs_ui(self):
        if self._inputs_container_layout is None:
            return
        while self._inputs_container_layout.count():
            item = self._inputs_container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._input_widgets = {}
        self._input_sources = {}

    def _cache_current_inputs(self, profile_name):
        for key, edit in self._input_widgets.items():
            try:
                self._input_cache[(profile_name, key)] = edit.text()
            except RuntimeError:
                pass

    def _rebuild_inputs(self, profile):
        self._clear_inputs_ui()
        inputs = profile.get("inputs") or []
        if not inputs:
            return

        profile_name = profile.get("name") or ""

        box = QGroupBox(tr("inputs.title", module="login_flow"))
        form = QFormLayout(box)

        source_labels = {
            "user": tr("source.user", module="login_flow"),
            "password": tr("source.password", module="login_flow"),
            "vault_key": tr("source.vault_key", module="login_flow"),
        }

        for spec in inputs:
            key = spec.get("key")
            if not key:
                continue
            label = spec.get("label") or key
            if spec.get("required"):
                label = label + " *"

            source = spec.get("source")
            if source:
                hint = QLabel(tr("inputs.same_as", module="login_flow",
                                  source=source_labels.get(source, source)))
                hint.setStyleSheet(
                    f"color: {_c('fg_dim')}; font-style: italic; padding: 4px 6px;")
                form.addRow(label + ":", hint)
                self._input_sources[key] = source
                continue

            edit = QLineEdit()
            edit.setFont(QFont("Consolas", 10))
            if spec.get("password"):
                edit.setEchoMode(QLineEdit.Password)
            if spec.get("placeholder"):
                edit.setPlaceholderText(spec["placeholder"])

            cached = self._input_cache.get((profile_name, key), "")
            if cached:
                edit.setText(cached)

            form.addRow(label + ":", edit)
            self._input_widgets[key] = edit

        self._inputs_container_layout.addWidget(box)

    def _collect_inputs(self, user="", password="", vault_key=""):
        out = {}
        for key, edit in self._input_widgets.items():
            try:
                out[key] = edit.text()
            except RuntimeError:
                continue
        for key, src in self._input_sources.items():
            if src == "user":
                out[key] = user
            elif src == "password":
                out[key] = password
            elif src == "vault_key":
                out[key] = vault_key
        return out

    def _refresh_profiles(self):
        try:
            current = self.profile_box.currentText()
            self.profile_box.blockSignals(True)
            self.profile_box.clear()
            for name in _list_profiles():
                self.profile_box.addItem(name)
            idx = self.profile_box.findText(current)
            if idx >= 0:
                self.profile_box.setCurrentIndex(idx)
            self.profile_box.blockSignals(False)
        except RuntimeError:
            return
        self._on_profile_changed(self.profile_box.currentText())

    def _on_profile_changed(self, name, log=True):
        if not name:
            self._update_delete_btn(False)
            self._clear_inputs_ui()
            return
        try:
            profile = _load_profile(name)
        except Exception as e:
            self.log.appendPlainText(
                tr("log.profile_error", module="login_flow", error=e))
            return

        if self._prev_profile:
            self._cache_current_inputs(self._prev_profile)
        self._prev_profile = name

        url = profile.get("login_url") or ""
        vk = profile.get("vault_key_default") or ""
        self.url_input.setText(url)
        self.vault_key_input.setText(vk)

        self._rebuild_inputs(profile)

        steps = profile.get("steps") or []
        is_sys = _is_system(name)
        tag = tr("log.builtin", module="login_flow") if is_sys else ""
        n_inputs = len(profile.get("inputs") or [])
        if log:
            self.log.appendPlainText(tr(
                "log.profile", module="login_flow",
                name=name, tag=tag, url=url or "-",
                steps=len(steps), inputs=n_inputs))
        self._update_delete_btn(is_sys)
        self._update_view_btn_label()

    def _update_delete_btn(self, is_system: bool):
        try:
            self._btns["delete"].setEnabled(not is_system)
        except (RuntimeError, KeyError):
            return

    def _view_or_edit_profile(self):
        name = self.profile_box.currentText()
        if not name:
            return
        try:
            profile = _load_profile(name)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"), str(e))
            return

        is_sys = _is_system(name)
        if is_sys:
            title = tr("dialog.view.title", module="login_flow", name=name)
        else:
            title = tr("dialog.edit.title", module="login_flow", name=name)

        dlg = JsonEditorDialog(parent_widget(self._widget), title,
                                json.dumps(profile, ensure_ascii=False, indent=2))

        if is_sys:
            dlg.editor.setReadOnly(True)
            if dlg.ok_btn:
                dlg.ok_btn.setEnabled(False)

        if dlg.exec() != QDialog.Accepted:
            return

        if is_sys:
            return

        try:
            parsed = json.loads(dlg.text())
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.invalid_json", module="login_flow", error=e))
            return

        if _save_profile(parsed, name):
            self.log.appendPlainText(
                tr("log.saved", module="login_flow", name=name))
            self._on_profile_changed(name)
        else:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.save_profile", module="login_flow"))

    def _new_profile(self):
        template = json.loads(json.dumps(NEW_PROFILE_TEMPLATE))
        dlg = JsonEditorDialog(
            parent_widget(self._widget),
            tr("dialog.new.title", module="login_flow"),
            json.dumps(template, ensure_ascii=False, indent=2))
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            parsed = json.loads(dlg.text())
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.invalid_json", module="login_flow", error=e))
            return

        name = (parsed.get("name") or "").strip()
        if not name:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.no_name", module="login_flow"))
            return
        if _is_system(name):
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.reserved_name", module="login_flow", name=name))
            return

        target = CONFIG_DIR / f"{name}.json"
        if target.exists():
            if QMessageBox.question(
                parent_widget(self._widget),
                tr("confirm.overwrite.title", module="login_flow"),
                tr("confirm.overwrite.text", module="login_flow", name=name),
            ) != QMessageBox.Yes:
                return

        if _save_profile(parsed, name):
            self.log.appendPlainText(
                tr("log.created", module="login_flow", name=name))
            self._refresh_profiles()
            idx = self.profile_box.findText(name)
            if idx >= 0:
                self.profile_box.setCurrentIndex(idx)
        else:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.save_profile", module="login_flow"))

    def _import_profile(self):
        path, _ = QFileDialog.getOpenFileName(
            parent_widget(self._widget),
            tr("dialog.import.title", module="login_flow"), "",
            "JSON (*.json);;All (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.cannot_read", module="login_flow", error=e))
            return
        if not isinstance(data, dict):
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.profile_obj", module="login_flow"))
            return

        suggested = data.get("name") or Path(path).stem
        name, ok = QInputDialog.getText(
            parent_widget(self._widget),
            tr("dialog.import.title", module="login_flow"),
            tr("dialog.import.field", module="login_flow"),
            text=suggested)
        if not ok or not name.strip():
            return
        name = name.strip()
        if _is_system(name):
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.reserved_name_choose", module="login_flow",
                                   name=name))
            return

        target = CONFIG_DIR / f"{name}.json"
        if target.exists():
            if QMessageBox.question(
                parent_widget(self._widget),
                tr("confirm.overwrite.title", module="login_flow"),
                tr("confirm.overwrite.text", module="login_flow", name=name),
            ) != QMessageBox.Yes:
                return

        if _save_profile(data, name):
            self.log.appendPlainText(
                tr("log.imported", module="login_flow", name=name))
            self._refresh_profiles()
            idx = self.profile_box.findText(name)
            if idx >= 0:
                self.profile_box.setCurrentIndex(idx)
        else:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.save_profile", module="login_flow"))

    def _export_profile(self):
        name = self.profile_box.currentText()
        if not name:
            return
        try:
            profile = _load_profile(name)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"), str(e))
            return
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("dialog.export.title", module="login_flow"),
            f"{name}.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
            self.log.appendPlainText(
                tr("log.exported", module="login_flow", name=name, path=path))
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"), str(e))

    def _delete_profile_ui(self):
        name = self.profile_box.currentText()
        if not name or _is_system(name):
            return
        if QMessageBox.question(
            parent_widget(self._widget),
            tr("confirm.delete.title", module="login_flow"),
            tr("confirm.delete.text", module="login_flow", name=name),
        ) != QMessageBox.Yes:
            return
        if _delete_profile(name):
            self.log.appendPlainText(
                tr("log.deleted", module="login_flow", name=name))
            self._refresh_profiles()
        else:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.delete_profile", module="login_flow"))

    def _do_login(self, code=None, method=None):
        if self._worker is not None and self._worker.isRunning():
            return
        name = self.profile_box.currentText()
        if not name:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"),
                                tr("err.select_profile", module="login_flow"))
            return
        try:
            profile = _load_profile(name)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="login_flow"), str(e))
            return

        url_override = self.url_input.text().strip()
        if url_override:
            profile["login_url"] = url_override

        user = self.user_input.text()
        pwd = self.pass_input.text()
        vault_key = self.vault_key_input.text().strip() or None
        totp = self.totp_input.text().strip() or None

        extra = self._collect_inputs(
            user=user, password=pwd, vault_key=vault_key or "")

        required_missing = []
        for spec in profile.get("inputs") or []:
            if not spec.get("required"):
                continue
            key = spec.get("key")
            value = (extra.get(key) or "").strip()
            if not value:
                required_missing.append(spec.get("label") or key)
        if required_missing:
            if QMessageBox.question(
                parent_widget(self._widget),
                tr("err.missing_fields.title", module="login_flow"),
                tr("err.missing_fields.text", module="login_flow",
                   fields="\n - ".join(required_missing)),
            ) != QMessageBox.Yes:
                return

        self._pending_code = code
        self._pending_method = method

        self.login_btn.setEnabled(False)
        extra_info = f"  extra_keys={list(extra.keys())}" if extra else ""
        self.log.appendPlainText(tr(
            "log.start", module="login_flow",
            profile=name, user=user or "-",
            method=method or "-", code="yes" if code else "-",
            extra=extra_info))

        self._worker = LoginWorker(
            profile, user, pwd,
            code=code, method=method,
            totp_secret=totp, vault_key=vault_key,
            extra=extra,
            caller=self.name, session=None,
        )
        self._worker.done.connect(self._on_login_done)
        self._worker.start()

    def _on_login_done(self, resp):
        self.login_btn.setEnabled(True)
        self._worker = None

        if resp.get("ok"):
            token = resp.get("token", "")
            self._last_token = token
            steps = resp.get("steps_executed", "?")
            extras = resp.get("extras") or {}
            tf = tr("log.tf", module="login_flow") if resp.get("two_factor_used") else ""
            auto = tr("log.auto_detected", module="login_flow",
                      path=resp.get("token_path_used", "")) if resp.get("auto_detected") else ""
            self.log.appendPlainText(tr(
                "log.ok", module="login_flow",
                vault_key=resp.get("vault_key"), steps=steps, tf=tf) + auto)
            if extras:
                extras_str = ", ".join(f"{k}={v}" for k, v in extras.items())
                self.log.appendPlainText(
                    tr("log.extras", module="login_flow", extras=extras_str))
            self.log.appendPlainText(
                f"    {token[:60]}{'...' if len(token) > 60 else ''}")
            return

        if resp.get("requires_method"):
            methods = resp.get("methods") or []
            labels = [f"{m.get('label')} ({m.get('value')})" for m in methods]
            if not labels:
                self.log.appendPlainText(
                    tr("log.missing_method", module="login_flow"))
                return
            label, ok = QInputDialog.getItem(
                parent_widget(self._widget),
                tr("2fa.choose.title", module="login_flow"),
                resp.get("message") or tr("2fa.choose.hint", module="login_flow"),
                labels, 0, False)
            if not ok or not label:
                self.log.appendPlainText(
                    tr("log.cancelled", module="login_flow"))
                return
            idx = labels.index(label)
            chosen = methods[idx]["value"]
            self._do_login(code=self._pending_code, method=chosen)
            return

        if resp.get("requires_code"):
            message = resp.get("message") or tr("2fa.code.hint", module="login_flow")
            code, ok = QInputDialog.getText(
                parent_widget(self._widget),
                tr("2fa.code.title", module="login_flow"), message)
            if not ok or not code.strip():
                self.log.appendPlainText(
                    tr("log.cancelled", module="login_flow"))
                return
            self._do_login(code=code.strip(), method=self._pending_method)
            return

        if resp.get("requires_captcha"):
            url = resp.get("captcha_url") or ""
            QGuiApplication.clipboard().setText(url)
            value, ok = QInputDialog.getText(
                parent_widget(self._widget),
                tr("captcha.title", module="login_flow"),
                tr("captcha.hint", module="login_flow", url=url))
            if not ok or not value.strip():
                self.log.appendPlainText(
                    tr("log.cancelled", module="login_flow"))
                return
            self._do_login(code=value.strip(), method=self._pending_method)
            return

        err = resp.get("error") or "unknown error"
        step = resp.get("step")
        prefix = tr("log.step", module="login_flow", step=step) if step else ""
        self.log.appendPlainText(
            tr("log.error", module="login_flow", prefix=prefix, error=err))
        if resp.get("body_preview"):
            self.log.appendPlainText(f"    {resp['body_preview'][:200]}")

    def _copy_token(self):
        if self._last_token:
            QGuiApplication.clipboard().setText(self._last_token)