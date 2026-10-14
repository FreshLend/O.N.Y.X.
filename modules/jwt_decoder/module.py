import base64
import json
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QLineEdit, QSplitter, QTabWidget, QComboBox,
    QFileDialog, QMessageBox
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.i18n import tr
from core.utils import color as _c, parent_widget


WEAK_SECRETS = [
    "secret", "password", "123456", "12345678", "qwerty", "admin", "admin123",
    "jwt", "jwtsecret", "jwt_secret", "jwt-secret", "mysecret", "my_secret",
    "secretkey", "secret_key", "secret-key", "supersecret", "super_secret",
    "changeme", "changeme123", "test", "test123", "dev", "development",
    "key", "key123", "private", "privatekey", "token", "tokenkey",
    "your-256-bit-secret", "your_jwt_secret", "my-256-bit-secret",
    "shhhhh", "shhhh", "topsecret", "top_secret", "default", "defaultkey",
    "example", "examplekey", "hackme", "letmein", "root", "toor",
    "jsonwebtoken", "node", "flask", "django", "laravel", "rails",
    "Spring", "springboot", "SpringBootSecret", "microservice",
]


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class JwtDecoderModule(BaseModule):
    def __init__(self):
        self._widget = None
        self._pending_token = None
        self._countdown_timer = None
        self._last_exp = None

    def on_load(self, api):
        api.register_service(self.name, "jwt.decode", self._svc_decode,
                             "jwt.decode(token) -> dict")
        api.register_service(self.name, "jwt.verify", self._svc_verify,
                             "jwt.verify(token, secret, algo='HS256') -> dict")
        api.register_service(self.name, "jwt.sign", self._svc_sign,
                             "jwt.sign(payload, secret, algo='HS256', headers=None) -> str")
        api.register_service(self.name, "jwt.brute", self._svc_brute,
                             "jwt.brute(token, wordlist=None, algo='HS256') -> dict")
        api.register_service(self.name, "jwt.none", self._svc_none,
                             "jwt.none(token) -> str")
        try:
            api.event.connect(self._on_bus_event)
        except Exception:
            pass

    def on_shutdown(self):
        try:
            if getattr(self, "_countdown_timer", None):
                self._countdown_timer.stop()
        except Exception:
            pass

    def _b64url_decode(self, s):
        try:
            result = get_bus().call("encoder.transform", "Base64URL decode", s)
        except KeyError:
            return b""
        if isinstance(result, str) and result.startswith("__ERROR__:"):
            raise ValueError(result)
        return result

    def _looks_like_jwt(self, s):
        if not isinstance(s, str) or not s:
            return False
        parts = s.split(".")
        if len(parts) != 3:
            return False
        try:
            h = json.loads(self._b64url_decode(parts[0]))
            p = json.loads(self._b64url_decode(parts[1]))
            return isinstance(h, dict) and isinstance(p, dict)
        except Exception:
            return False

    def _on_bus_event(self, name, payload):
        if name != "vault.changed":
            return
        key = payload.get("key")
        if not key:
            return
        try:
            val = get_bus().call("vault.get", key)
        except KeyError:
            return
        if self._looks_like_jwt(val):
            self._pending_token = val
            if hasattr(self, "token_input"):
                try:
                    self.token_input.setText(val)
                    self._decode()
                except RuntimeError:
                    pass

    def _set_info(self, text, color_key="fg_dim"):
        try:
            self.info_label.setText(text)
            self.info_label.setStyleSheet(
                f"color: {_c(color_key)}; font-size: 11px;")
        except RuntimeError:
            pass

    def _restore_token_from_vault(self):
        token = None
        src_key = None
        if self._pending_token and self._looks_like_jwt(self._pending_token):
            token = self._pending_token
            src_key = "(cached)"
        if not token:
            try:
                items = get_bus().call("vault.list") or []
            except Exception:
                return
            for key, _ in items:
                try:
                    val = get_bus().call("vault.get", key)
                except Exception:
                    continue
                if self._looks_like_jwt(val):
                    token = val
                    src_key = str(key)
                    break
        if not token:
            return
        self._pending_token = token
        try:
            self.token_input.setText(token)
            self._decode()
            self._set_info(tr("info.loaded", module="jwt_decoder",
                              key=src_key, length=len(token),
                              preview=token[:16] + "..."), "success")
        except RuntimeError:
            return

    def _svc_decode(self, token):
        if not token or token.count(".") != 2:
            return {"ok": False, "error": "invalid_format"}
        try:
            h_b64, p_b64, _ = token.split(".")
            header = json.loads(self._b64url_decode(h_b64))
            payload = json.loads(self._b64url_decode(p_b64))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        exp = payload.get("exp")
        now = int(time.time())
        return {
            "ok": True,
            "header": header,
            "payload": payload,
            "iat": payload.get("iat"),
            "exp": exp,
            "expired": bool(exp and exp < now),
            "expires_in": (exp - now) if exp else None,
        }

    def _svc_verify(self, token, secret, algo="HS256"):
        try:
            import jwt as pyjwt
        except ImportError:
            return {"ok": False, "error": "pyjwt not installed"}
        try:
            payload = pyjwt.decode(token, secret, algorithms=[algo])
            return {"ok": True, "valid": True, "payload": payload}
        except pyjwt.ExpiredSignatureError:
            return {"ok": True, "valid": False, "error": "expired"}
        except pyjwt.InvalidSignatureError:
            return {"ok": True, "valid": False, "error": "invalid_signature"}
        except Exception as e:
            return {"ok": True, "valid": False, "error": str(e)}

    def _svc_sign(self, payload, secret, algo="HS256", headers=None):
        try:
            import jwt as pyjwt
        except ImportError:
            return {"ok": False, "error": "pyjwt not installed"}
        try:
            if isinstance(payload, str):
                payload = json.loads(payload)
            token = pyjwt.encode(payload, secret, algorithm=algo,
                                 headers=headers or None)
            if isinstance(token, bytes):
                token = token.decode()
            return {"ok": True, "token": token}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _svc_brute(self, token, wordlist=None, algo="HS256"):
        try:
            import jwt as pyjwt
        except ImportError:
            return {"ok": False, "error": "pyjwt not installed"}
        candidates = list(WEAK_SECRETS)
        if wordlist:
            try:
                with open(wordlist, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        s = line.strip()
                        if s and s not in candidates:
                            candidates.append(s)
            except Exception:
                pass
        for s in candidates:
            try:
                pyjwt.decode(token, s, algorithms=[algo])
                return {"ok": True, "found": True, "secret": s,
                        "tried": candidates.index(s) + 1}
            except pyjwt.InvalidSignatureError:
                continue
            except pyjwt.ExpiredSignatureError:
                return {"ok": True, "found": True, "secret": s,
                        "tried": candidates.index(s) + 1, "expired": True}
            except Exception:
                continue
        return {"ok": True, "found": False, "tried": len(candidates)}

    def _svc_none(self, token):
        if not token or token.count(".") != 2:
            return {"ok": False, "error": "invalid_format"}
        try:
            h_b64, p_b64, _ = token.split(".")
            header = json.loads(self._b64url_decode(h_b64))
            header["alg"] = "none"
            new_h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
            return {"ok": True, "token": f"{new_h}.{p_b64}."}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def create_widget(self) -> QWidget:
        root = QWidget()
        self._widget = root

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        mono = QFont("Consolas", 10)

        row = QHBoxLayout()
        self.token_lbl = QLabel()
        self.token_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px; letter-spacing: 1px;")
        row.addWidget(self.token_lbl)
        self.token_input = QLineEdit()
        self.token_input.setFont(mono)
        self.token_input.textChanged.connect(self._decode)
        row.addWidget(self.token_input, 1)
        self.paste_btn = QPushButton()
        self.paste_btn.setFixedWidth(120)
        self.paste_btn.clicked.connect(self._paste)
        row.addWidget(self.paste_btn)
        self.load_btn = QPushButton()
        self.load_btn.setFixedWidth(170)
        self.load_btn.clicked.connect(self._restore_token_from_vault)
        row.addWidget(self.load_btn)
        layout.addLayout(row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_decode_tab(), "")
        self.tabs.addTab(self._build_verify_tab(), "")
        self.tabs.addTab(self._build_forge_tab(), "")
        self.tabs.addTab(self._build_brute_tab(), "")
        self.tabs.addTab(self._build_attack_tab(), "")
        layout.addWidget(self.tabs, 1)

        self.info_label = QLabel("-")
        self.info_label.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.info_label)

        self._countdown_timer = QTimer(root)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._update_countdown)
        self._countdown_timer.start()

        self.retranslate()
        self._restore_token_from_vault()
        return root

    def _build_decode_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        split = QSplitter(Qt.Vertical)
        h_w = QWidget()
        hl = QVBoxLayout(h_w)
        hl.setContentsMargins(0, 0, 0, 0)
        self.header_lbl = QLabel()
        self.header_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        hl.addWidget(self.header_lbl)
        self.header_out = QPlainTextEdit()
        self.header_out.setFont(QFont("Consolas", 10))
        self.header_out.setReadOnly(True)
        hl.addWidget(self.header_out)
        p_w = QWidget()
        pl = QVBoxLayout(p_w)
        pl.setContentsMargins(0, 0, 0, 0)
        self.payload_lbl = QLabel()
        self.payload_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        pl.addWidget(self.payload_lbl)
        self.payload_out = QPlainTextEdit()
        self.payload_out.setFont(QFont("Consolas", 10))
        self.payload_out.setReadOnly(True)
        pl.addWidget(self.payload_out)
        split.addWidget(h_w)
        split.addWidget(p_w)
        split.setSizes([200, 400])
        layout.addWidget(split)
        return page

    def _build_verify_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.secret_lbl = QLabel()
        self.secret_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self.secret_lbl)
        self.secret_input = QLineEdit()
        self.secret_input.setFont(QFont("Consolas", 10))
        row.addWidget(self.secret_input, 1)
        self.algo_box = QComboBox()
        self.algo_box.addItems(["HS256", "HS384", "HS512"])
        self.algo_box.setFixedWidth(120)
        row.addWidget(self.algo_box)
        self.verify_btn = QPushButton()
        self.verify_btn.setFixedWidth(140)
        self.verify_btn.clicked.connect(self._verify)
        row.addWidget(self.verify_btn)
        layout.addLayout(row)
        self.verify_out = QPlainTextEdit()
        self.verify_out.setFont(QFont("Consolas", 10))
        self.verify_out.setReadOnly(True)
        layout.addWidget(self.verify_out, 1)
        return page

    def _build_forge_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.forge_secret_lbl = QLabel()
        self.forge_secret_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self.forge_secret_lbl)
        self.forge_secret = QLineEdit("secret")
        self.forge_secret.setFont(QFont("Consolas", 10))
        row.addWidget(self.forge_secret, 1)
        self.forge_algo = QComboBox()
        self.forge_algo.addItems(["HS256", "HS384", "HS512", "none"])
        self.forge_algo.setFixedWidth(120)
        row.addWidget(self.forge_algo)
        self.forge_btn = QPushButton()
        self.forge_btn.setFixedWidth(140)
        self.forge_btn.clicked.connect(self._forge)
        row.addWidget(self.forge_btn)
        layout.addLayout(row)
        self.forge_payload_lbl = QLabel()
        self.forge_payload_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.forge_payload_lbl)
        self.forge_payload = QPlainTextEdit()
        self.forge_payload.setFont(QFont("Consolas", 10))
        self.forge_payload.setPlainText('{\n  "sub": "admin",\n  "role": "admin",\n  "iat": 1700000000,\n  "exp": 9999999999\n}')
        layout.addWidget(self.forge_payload, 1)
        self.forge_out_lbl = QLabel()
        self.forge_out_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.forge_out_lbl)
        self.forge_out = QPlainTextEdit()
        self.forge_out.setFont(QFont("Consolas", 10))
        self.forge_out.setReadOnly(True)
        self.forge_out.setFixedHeight(80)
        layout.addWidget(self.forge_out)
        self.forge_copy = QPushButton()
        self.forge_copy.setFixedWidth(200)
        self.forge_copy.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.forge_out.toPlainText()))
        layout.addWidget(self.forge_copy)
        return page

    def _build_brute_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.brute_algo_lbl = QLabel()
        self.brute_algo_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self.brute_algo_lbl)
        self.brute_algo = QComboBox()
        self.brute_algo.addItems(["HS256", "HS384", "HS512"])
        self.brute_algo.setFixedWidth(120)
        row.addWidget(self.brute_algo)
        self.brute_wl_lbl = QLabel()
        self.brute_wl_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self.brute_wl_lbl)
        self.brute_wl = QLineEdit()
        self.brute_wl.setFont(QFont("Consolas", 10))
        row.addWidget(self.brute_wl, 1)
        self.brute_wl_btn = QPushButton()
        self.brute_wl_btn.setFixedWidth(120)
        self.brute_wl_btn.clicked.connect(self._pick_wordlist)
        row.addWidget(self.brute_wl_btn)
        self.brute_btn = QPushButton()
        self.brute_btn.setFixedWidth(160)
        self.brute_btn.clicked.connect(self._brute)
        row.addWidget(self.brute_btn)
        layout.addLayout(row)
        self.brute_out = QPlainTextEdit()
        self.brute_out.setFont(QFont("Consolas", 10))
        self.brute_out.setReadOnly(True)
        layout.addWidget(self.brute_out, 1)
        return page

    def _build_attack_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.none_hint = QLabel()
        self.none_hint.setWordWrap(True)
        self.none_hint.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.none_hint)
        self.none_btn = QPushButton()
        self.none_btn.setFixedWidth(240)
        self.none_btn.clicked.connect(self._none_attack)
        layout.addWidget(self.none_btn)
        self.none_out = QPlainTextEdit()
        self.none_out.setFont(QFont("Consolas", 10))
        self.none_out.setReadOnly(True)
        layout.addWidget(self.none_out, 1)
        self.none_copy = QPushButton()
        self.none_copy.setFixedWidth(200)
        self.none_copy.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.none_out.toPlainText()))
        layout.addWidget(self.none_copy)
        return page

    def retranslate(self):
        def t(k, **kw):
            return tr(k, module="jwt_decoder", **kw)
        if not hasattr(self, "token_lbl"):
            return
        self.token_lbl.setText(t("token"))
        self.token_input.setPlaceholderText(t("placeholder.token"))
        self.paste_btn.setText(t("paste"))
        self.load_btn.setText(t("from_vault"))
        self.tabs.setTabText(0, t("tab.decode"))
        self.tabs.setTabText(1, t("tab.verify"))
        self.tabs.setTabText(2, t("tab.forge"))
        self.tabs.setTabText(3, t("tab.brute"))
        self.tabs.setTabText(4, t("tab.attack"))
        self.header_lbl.setText(t("header"))
        self.payload_lbl.setText(t("payload"))
        self.secret_lbl.setText(t("secret"))
        self.secret_input.setPlaceholderText(t("placeholder.secret"))
        self.verify_btn.setText(t("verify"))
        self.forge_secret_lbl.setText(t("forge.secret"))
        self.forge_btn.setText(t("forge.run"))
        self.forge_payload_lbl.setText(t("forge.payload"))
        self.forge_out_lbl.setText(t("forge.output"))
        self.forge_copy.setText(t("copy"))
        self.brute_algo_lbl.setText(t("brute.algo"))
        self.brute_wl_lbl.setText(t("brute.wordlist"))
        self.brute_wl_btn.setText(t("browse"))
        self.brute_btn.setText(t("brute.run"))
        self.none_hint.setText(t("attack.none.hint"))
        self.none_btn.setText(t("attack.none.run"))
        self.none_copy.setText(t("copy"))
        if self.token_input.text().strip():
            self._decode()

    def _paste(self):
        self.token_input.setText(QGuiApplication.clipboard().text().strip())

    def _decode(self):
        token = self.token_input.text().strip()
        try:
            self.header_out.clear()
            self.payload_out.clear()
        except RuntimeError:
            return
        if not token or token.count(".") != 2:
            self._last_exp = None
            return
        try:
            h_b64, p_b64, _ = token.split(".")
            header = json.loads(self._b64url_decode(h_b64))
            payload = json.loads(self._b64url_decode(p_b64))
        except Exception as e:
            self._set_info(tr("info.parse_error", module="jwt_decoder", error=e), "danger")
            return
        try:
            self.header_out.setPlainText(json.dumps(header, indent=2, ensure_ascii=False))
            self.payload_out.setPlainText(json.dumps(payload, indent=2, ensure_ascii=False))
            self._last_exp = payload.get("exp")
            self._update_countdown()
        except RuntimeError:
            return

    def _update_countdown(self):
        try:
            if not hasattr(self, "info_label") or self.info_label is None:
                return
            token = self.token_input.text().strip()
            if not token or token.count(".") != 2:
                return
            _, p_b64, _ = token.split(".")
            payload = json.loads(self._b64url_decode(p_b64))
            parts = []
            now = int(time.time())
            for k in ("iat", "nbf", "exp"):
                if k in payload:
                    parts.append(f"{k}: {self._fmt_ts(payload[k], now)}")
            if not parts:
                self._set_info(tr("info.no_time", module="jwt_decoder"))
                return
            if "exp" in payload and payload["exp"] < now:
                color = "danger"
            elif "exp" in payload and payload["exp"] - now < 300:
                color = "warning"
            else:
                color = "success"
            self._set_info("  ·  ".join(parts), color)
        except RuntimeError:
            return

    def _fmt_ts(self, ts, now):
        try:
            dt = datetime.fromtimestamp(ts)
        except Exception:
            return str(ts)
        delta = ts - now
        if delta > 0:
            h = delta // 3600
            m = (delta % 3600) // 60
            s = delta % 60
            return f"{dt:%Y-%m-%d %H:%M} (in {h}h {m}m {s}s)"
        return f"{dt:%Y-%m-%d %H:%M} (expired {-delta}s ago)"

    def _verify(self):
        token = self.token_input.text().strip()
        secret = self.secret_input.text()
        if not token or not secret:
            return
        algo = self.algo_box.currentText()
        res = self._svc_verify(token, secret, algo)
        try:
            if res.get("ok") and res.get("valid"):
                self.verify_out.setPlainText(
                    tr("info.verified_valid", module="jwt_decoder", algo=algo)
                    + "\n\n" + json.dumps(res.get("payload"), indent=2, ensure_ascii=False))
            else:
                self.verify_out.setPlainText(
                    tr("info.verified_invalid", module="jwt_decoder",
                       error=res.get("error") or "invalid"))
        except RuntimeError:
            return

    def _forge(self):
        try:
            payload = json.loads(self.forge_payload.toPlainText())
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget), "JSON", str(e))
            return
        algo = self.forge_algo.currentText()
        secret = self.forge_secret.text()
        if algo == "none":
            try:
                import jwt as pyjwt
                token = pyjwt.encode(payload, "", algorithm="none")
                if isinstance(token, bytes):
                    token = token.decode()
            except Exception:
                h = _b64url_encode(json.dumps({"alg": "none", "typ": "JWT"},
                                              separators=(",", ":")).encode())
                p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
                token = f"{h}.{p}."
        else:
            r = self._svc_sign(payload, secret, algo)
            if not r.get("ok"):
                QMessageBox.warning(parent_widget(self._widget), "JWT", r.get("error"))
                return
            token = r["token"]
        try:
            self.forge_out.setPlainText(token)
        except RuntimeError:
            return

    def _pick_wordlist(self):
        path, _ = QFileDialog.getOpenFileName(
            parent_widget(self._widget),
            tr("brute.wordlist", module="jwt_decoder"),
            "", "Text (*.txt);;All (*)")
        if path:
            self.brute_wl.setText(path)

    def _brute(self):
        token = self.token_input.text().strip()
        if not token or token.count(".") != 2:
            return
        algo = self.brute_algo.currentText()
        wl = self.brute_wl.text().strip() or None
        try:
            self.brute_out.setPlainText(tr("brute.running", module="jwt_decoder"))
        except RuntimeError:
            return
        res = self._svc_brute(token, wl, algo)
        try:
            if res.get("found"):
                self.brute_out.setPlainText(
                    tr("brute.found", module="jwt_decoder",
                       secret=res["secret"], tried=res["tried"]))
            else:
                self.brute_out.setPlainText(
                    tr("brute.not_found", module="jwt_decoder",
                       tried=res.get("tried", 0)))
        except RuntimeError:
            return

    def _none_attack(self):
        token = self.token_input.text().strip()
        if not token or token.count(".") != 2:
            return
        res = self._svc_none(token)
        try:
            if res.get("ok"):
                self.none_out.setPlainText(res["token"])
            else:
                self.none_out.setPlainText(str(res.get("error")))
        except RuntimeError:
            return