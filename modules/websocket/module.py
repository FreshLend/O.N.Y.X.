import json
import ssl
import threading
import time
import uuid
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QComboBox, QSpinBox, QCheckBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QFileDialog
)

try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    websocket = None
    HAS_WEBSOCKET = False

from core.base_module import BaseModule
from core.module_api import get_bus
from core.history import get_history
from core.i18n import tr
from core.utils import (
    color as _c, parent_widget, parse_headers, resolve_placeholders,
)
from styles import loader as theme_loader


def T(key, **kwargs):
    return tr(key, module="websocket", **kwargs)


def _hex_dump(raw, width=16):
    lines = []
    for i in range(0, len(raw), width):
        chunk = raw[i:i + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:08x}  {hex_part:<{width * 3}}  {ascii_part}")
    return "\n".join(lines)


def _parse_proxy_url(url):
    from urllib.parse import urlparse
    if not url:
        return None
    p = urlparse(url)
    if not p.hostname:
        return None
    scheme = (p.scheme or "http").lower()
    if scheme not in ("http", "https", "socks4", "socks5"):
        scheme = "http"
    return {"host": p.hostname, "port": p.port or 8080, "type": scheme}


class WsWorker(QThread):
    opened = Signal(str)
    message = Signal(str, dict)
    closed = Signal(str, int, str)
    error = Signal(str, str)
    ping_in = Signal(str, bytes)
    pong_in = Signal(str, bytes)

    def __init__(self, connection_id, url, headers, subprotocols, proxy,
                 ping_interval, ping_timeout, ssl_verify,
                 state_updater=None, message_recorder=None):
        super().__init__()
        self._conn_id = connection_id
        self._url = url
        self._headers = headers or {}
        self._subprotocols = subprotocols or []
        self._proxy = proxy
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._ssl_verify = ssl_verify
        self._state_updater = state_updater
        self._message_recorder = message_recorder
        self._app = None
        self._stop = False

    def app(self):
        return self._app

    def stop(self):
        self._stop = True
        app = self._app
        if app is not None:
            try:
                app.keep_running = False
            except Exception:
                pass
            try:
                app.close()
            except Exception:
                pass

    def run(self):
        if not HAS_WEBSOCKET:
            if self._state_updater:
                self._state_updater(self._conn_id, "closed")
            self.error.emit(self._conn_id, "websocket-client not installed")
            self.closed.emit(self._conn_id, 1006, "library missing")
            return

        self._app = websocket.WebSocketApp(
            self._url,
            header=self._headers,
            subprotocols=self._subprotocols,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_ping=self._on_ping,
            on_pong=self._on_pong,
        )

        sslopt = {}
        if not self._ssl_verify:
            sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

        kwargs = {}
        if self._proxy:
            kwargs["http_proxy_host"] = self._proxy["host"]
            kwargs["http_proxy_port"] = self._proxy["port"]
            kwargs["proxy_type"] = self._proxy["type"]

        try:
            self._app.run_forever(
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
                sslopt=sslopt,
                **kwargs
            )
        except Exception as e:
            if not self._stop:
                self.error.emit(self._conn_id, str(e))
        finally:
            self._app = None
            if self._state_updater:
                self._state_updater(self._conn_id, "closed")

    def _record(self, msg):
        if self._message_recorder:
            try:
                self._message_recorder(self._conn_id, msg)
            except Exception:
                pass

    def _on_open(self, ws):
        if self._state_updater:
            self._state_updater(self._conn_id, "connected")
        self.opened.emit(self._conn_id)

    def _on_message(self, ws, message):
        if isinstance(message, bytes):
            msg = {
                "direction": "in",
                "opcode": "binary",
                "data": message,
                "size": len(message),
            }
        else:
            text = str(message)
            msg = {
                "direction": "in",
                "opcode": "text",
                "text": text,
                "size": len(text.encode("utf-8", errors="replace")),
            }
        self._record(msg)
        self.message.emit(self._conn_id, msg)

    def _on_error(self, ws, error):
        self.error.emit(self._conn_id, str(error))

    def _on_close(self, ws, code, reason):
        if self._state_updater:
            self._state_updater(self._conn_id, "closed")
        self.closed.emit(self._conn_id,
                          int(code) if code else 1000,
                          reason or "")

    def _on_ping(self, ws, data):
        msg = {
            "direction": "in", "opcode": "ping",
            "text": data.hex() if data else "",
            "size": len(data) if data else 0,
        }
        self._record(msg)
        self.ping_in.emit(self._conn_id, data or b"")

    def _on_pong(self, ws, data):
        msg = {
            "direction": "in", "opcode": "pong",
            "text": data.hex() if data else "",
            "size": len(data) if data else 0,
        }
        self._record(msg)
        self.pong_in.emit(self._conn_id, data or b"")


class WebSocketModule(BaseModule):
    def __init__(self):
        self._widget = None
        self._connections = {}
        self._lock = threading.RLock()
        self._active_id = None
        self._labels = {}
        self._buttons = {}
        self._checks = {}
        self._history = get_history()
        self._log_widget = None
        self._mono = None

    def on_load(self, api):
        api.register_service(self.name, "ws.connect", self._svc_connect,
                             "ws.connect(url, headers=None, subprotocols=None, "
                             "proxy=None, ping_interval=30, ping_timeout=10, "
                             "ssl_verify=True, caller=None, session=None) -> dict")
        api.register_service(self.name, "ws.send", self._svc_send,
                             "ws.send(connection_id, data, binary=False) -> bool")
        api.register_service(self.name, "ws.close", self._svc_close,
                             "ws.close(connection_id, code=1000, reason='') -> bool")
        api.register_service(self.name, "ws.ping", self._svc_ping,
                             "ws.ping(connection_id) -> bool")
        api.register_service(self.name, "ws.list", self._svc_list,
                             "ws.list() -> list[dict]")
        api.register_service(self.name, "ws.available", self._svc_available,
                             "ws.available() -> bool")
        api.register_service(self.name, "ws.wait_connected", self._svc_wait_connected,
                             "ws.wait_connected(connection_id, timeout=5) -> bool")
        api.register_service(self.name, "ws.exchange", self._svc_exchange,
                             "ws.exchange(connection_id, data, binary=False, "
                             "timeout=5) -> dict")
        api.register_service(self.name, "ws.recent", self._svc_recent,
                             "ws.recent(connection_id, limit=100) -> list[dict]")

    def on_shutdown(self):
        with self._lock:
            workers = [meta["worker"] for meta in self._connections.values()]
            self._connections.clear()
        for w in workers:
            try:
                w.stop()
                w.wait(3000)
            except Exception:
                pass

    def _svc_available(self):
        return HAS_WEBSOCKET

    def _svc_list(self):
        with self._lock:
            return [
                {
                    "id": cid,
                    "url": meta["url"],
                    "state": meta["state"],
                    "opened_at": meta.get("opened_at"),
                    "sent": meta.get("sent", 0),
                    "received": meta.get("received", 0),
                }
                for cid, meta in self._connections.items()
            ]

    def _set_conn_state(self, conn_id, state):
        with self._lock:
            meta = self._connections.get(conn_id)
            if meta:
                meta["state"] = state

    def _record_message(self, conn_id, msg):
        with self._lock:
            meta = self._connections.get(conn_id)
            if not meta:
                return
            if msg.get("direction") == "in":
                meta["received"] = meta.get("received", 0) + 1
            meta["counter"] = meta.get("counter", 0) + 1
            msg["num"] = meta["counter"]
            msg["time"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            meta["messages"].append(msg)

    def _svc_connect(self, url, headers=None, subprotocols=None, proxy=None,
                     ping_interval=30, ping_timeout=10, ssl_verify=True,
                     caller=None, session=None):
        if not HAS_WEBSOCKET:
            return {"ok": False, "error": "websocket-client not installed"}
        if not url:
            return {"ok": False, "error": "url required"}

        if isinstance(headers, str):
            headers = parse_headers(headers)
        if isinstance(subprotocols, str):
            subprotocols = [s.strip() for s in subprotocols.split(",") if s.strip()]
        if proxy is None:
            proxy_url = get_bus().try_call("proxy.get_active")
            proxy = _parse_proxy_url(proxy_url)
        elif isinstance(proxy, str):
            proxy = _parse_proxy_url(proxy)

        conn_id = "ws-" + uuid.uuid4().hex[:8]
        worker = WsWorker(
            connection_id=conn_id,
            url=url,
            headers=headers or {},
            subprotocols=subprotocols or [],
            proxy=proxy,
            ping_interval=max(0, int(ping_interval)),
            ping_timeout=max(1, int(ping_timeout)),
            ssl_verify=bool(ssl_verify),
            state_updater=self._set_conn_state,
            message_recorder=self._record_message,
        )
        meta = {
            "id": conn_id,
            "url": url,
            "state": "connecting",
            "worker": worker,
            "opened_at": None,
            "sent": 0,
            "received": 0,
            "messages": [],
            "counter": 0,
            "caller": caller,
            "session": session,
        }
        with self._lock:
            self._connections[conn_id] = meta

        worker.opened.connect(self._on_opened)
        worker.message.connect(self._on_message)
        worker.closed.connect(self._on_closed)
        worker.error.connect(self._on_error)
        worker.ping_in.connect(self._on_ping)
        worker.pong_in.connect(self._on_pong)
        worker.start()

        return {"ok": True, "connection_id": conn_id}

    def _svc_send(self, connection_id, data, binary=False):
        if not HAS_WEBSOCKET:
            return False
        with self._lock:
            meta = self._connections.get(connection_id)
        if not meta or meta["state"] != "connected":
            return False
        app = meta["worker"].app()
        if app is None:
            return False
        try:
            opcode = (websocket.ABNF.OPCODE_BINARY if binary
                      else websocket.ABNF.OPCODE_TEXT)
            if binary and isinstance(data, str):
                data = data.encode("utf-8", errors="replace")
            app.send(data, opcode=opcode)
            with self._lock:
                meta["sent"] = meta.get("sent", 0) + 1
            self._record_message(connection_id, {
                "direction": "out",
                "opcode": "binary" if binary else "text",
                "data": data if binary else None,
                "text": None if binary else str(data),
                "size": len(data) if binary else len(str(data).encode("utf-8")),
            })
            return True
        except Exception:
            return False

    def _svc_close(self, connection_id, code=1000, reason=""):
        with self._lock:
            meta = self._connections.get(connection_id)
        if not meta:
            return False
        meta["worker"].stop()
        return True

    def _svc_ping(self, connection_id):
        if not HAS_WEBSOCKET:
            return False
        with self._lock:
            meta = self._connections.get(connection_id)
        if not meta or meta.get("state") != "connected":
            return False
        app = meta["worker"].app()
        if app is None:
            return False
        try:
            app.send("", opcode=websocket.ABNF.OPCODE_PING)
            return True
        except Exception:
            return False

    def _svc_wait_connected(self, connection_id, timeout=5):
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            with self._lock:
                meta = self._connections.get(connection_id)
            if not meta:
                return False
            state = meta.get("state")
            if state == "connected":
                return True
            if state == "closed":
                return False
            time.sleep(0.05)
        return False

    def _svc_recent(self, connection_id, limit=100):
        with self._lock:
            meta = self._connections.get(connection_id)
            if not meta:
                return []
            return list(meta["messages"][-int(limit):])

    def _svc_exchange(self, connection_id, data, binary=False, timeout=5):
        with self._lock:
            meta = self._connections.get(connection_id)
            if not meta or meta.get("state") != "connected":
                return {"ok": False, "error": "not connected"}
            start_count = len(meta["messages"])

        if not self._svc_send(connection_id, data, binary=binary):
            return {"ok": False, "error": "send failed"}

        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            with self._lock:
                meta = self._connections.get(connection_id)
                if not meta:
                    return {"ok": False, "error": "connection lost"}
                messages = meta["messages"][start_count:]
            for m in messages:
                if m.get("direction") != "in":
                    continue
                if m.get("opcode") in ("ping", "pong"):
                    continue
                if binary and m.get("opcode") != "binary":
                    continue
                if not binary and m.get("opcode") != "text":
                    continue
                return {
                    "ok": True,
                    "opcode": m.get("opcode"),
                    "text": m.get("text"),
                    "binary_data": m.get("data"),
                    "size": m.get("size", 0),
                }
            time.sleep(0.05)
        return {"ok": False, "error": "timeout"}

    def _on_opened(self, conn_id):
        with self._lock:
            meta = self._connections.get(conn_id)
            if meta:
                meta["opened_at"] = datetime.utcnow().isoformat()
                url = meta["url"]
            else:
                url = ""
        self._log_line(T("msg.opened", url=url))
        if conn_id == self._active_id:
            self._refresh_status()
            self._update_ui_state()
        try:
            get_bus().emit_event("ws.opened", connection_id=conn_id, url=url)
        except Exception:
            pass
        try:
            self._history.add(
                module=self.name, session=None, method="WS",
                url=url, headers={}, body="",
                status=101, response_body="opened", elapsed=0.0)
        except Exception:
            pass

    def _on_message(self, conn_id, msg):
        if conn_id == self._active_id:
            self._add_msg_row(msg)
            preview = (msg.get("text") or "")[:60]
            if msg.get("opcode") == "binary":
                preview = f"[binary {msg.get('size', 0)}B]"
            self._log_line(f"[in] {preview}")
        try:
            get_bus().emit_event(
                "ws.message",
                connection_id=conn_id,
                direction=msg.get("direction"),
                opcode=msg.get("opcode"),
                size=msg.get("size", 0),
            )
        except Exception:
            pass

    def _on_closed(self, conn_id, code, reason):
        with self._lock:
            meta = self._connections.get(conn_id)
            url = meta["url"] if meta else ""
        self._log_line(T("msg.closed", code=code, reason=reason or "-"))
        if conn_id == self._active_id:
            self._refresh_status()
            self._update_ui_state()
        try:
            get_bus().emit_event("ws.closed",
                                 connection_id=conn_id, code=code,
                                 reason=reason)
        except Exception:
            pass
        try:
            self._history.add(
                module=self.name, session=None, method="WS",
                url=url, headers={}, body="",
                status=code, response_body=reason or "closed", elapsed=0.0)
        except Exception:
            pass

    def _on_error(self, conn_id, error):
        self._log_line(T("msg.error", error=error))
        try:
            get_bus().emit_event("ws.error",
                                 connection_id=conn_id, error=error)
        except Exception:
            pass

    def _on_ping(self, conn_id, data):
        pass

    def _on_pong(self, conn_id, data):
        pass

    def _log_line(self, text):
        if self._log_widget is None:
            return
        try:
            self._log_widget.appendPlainText(text)
        except RuntimeError:
            return

    def create_widget(self) -> QWidget:
        root = QWidget()
        self._widget = root

        self._labels = {}
        self._buttons = {}
        self._checks = {}

        self._mono = QFont("Consolas", 10)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        if not HAS_WEBSOCKET:
            warn = QLabel(T("err.no_lib"))
            warn.setWordWrap(True)
            warn.setStyleSheet(
                f"color: {_c('warning')}; padding: 8px; "
                f"border: 1px solid {_c('warning')};")
            layout.addWidget(warn)

        layout.addLayout(self._build_top_bar())

        self.status_label = QLabel()
        self.status_label.setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.status_label)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_messages_tab(), "")
        self._tabs.addTab(self._build_connection_tab(), "")
        layout.addWidget(self._tabs, 1)

        layout.addLayout(self._build_send_bar())

        self._log_widget = QPlainTextEdit()
        self._log_widget.setFont(self._mono)
        self._log_widget.setReadOnly(True)
        self._log_widget.setFixedHeight(90)
        layout.addWidget(self._log_widget)

        self.retranslate()
        self._refresh_status()
        self._update_ui_state()
        return root

    def _build_top_bar(self):
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self._labels["url"] = QLabel()
        self._labels["url"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        bar.addWidget(self._labels["url"])

        self.url_input = QLineEdit("wss://echo.websocket.events")
        self.url_input.setFont(self._mono)
        bar.addWidget(self.url_input, 1)

        self.connect_btn = QPushButton()
        self.connect_btn.setFixedWidth(160)
        self.connect_btn.clicked.connect(self._ui_connect)
        bar.addWidget(self.connect_btn)

        return bar

    def _build_messages_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)

        self.msg_table = QTableWidget()
        self.msg_table.setColumnCount(6)
        self.msg_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.msg_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.msg_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        self.msg_table.itemSelectionChanged.connect(self._show_msg_detail)
        v.addWidget(self.msg_table, 3)

        self._labels["detail_title"] = QLabel()
        self._labels["detail_title"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        v.addWidget(self._labels["detail_title"])

        self.msg_detail = QPlainTextEdit()
        self.msg_detail.setFont(self._mono)
        self.msg_detail.setReadOnly(True)
        v.addWidget(self.msg_detail, 2)

        return page

    def _build_connection_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        self._labels["headers"] = QLabel()
        self._labels["headers"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        v.addWidget(self._labels["headers"])

        self.headers_edit = QPlainTextEdit()
        self.headers_edit.setFont(self._mono)
        self.headers_edit.setFixedHeight(100)
        v.addWidget(self.headers_edit)

        row1 = QHBoxLayout()
        self._labels["subproto"] = QLabel()
        self._labels["subproto"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row1.addWidget(self._labels["subproto"])
        self.subproto_input = QLineEdit()
        self.subproto_input.setFont(self._mono)
        row1.addWidget(self.subproto_input, 1)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        self._checks["proxy"] = QCheckBox()
        row2.addWidget(self._checks["proxy"])
        self._checks["ssl"] = QCheckBox()
        self._checks["ssl"].setChecked(True)
        row2.addWidget(self._checks["ssl"])
        row2.addStretch()
        v.addLayout(row2)

        row3 = QHBoxLayout()
        self._labels["ping_interval"] = QLabel()
        self._labels["ping_interval"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row3.addWidget(self._labels["ping_interval"])
        self.ping_interval_spin = QSpinBox()
        self.ping_interval_spin.setRange(0, 3600)
        self.ping_interval_spin.setValue(30)
        self.ping_interval_spin.setFixedWidth(90)
        row3.addWidget(self.ping_interval_spin)

        self._labels["ping_timeout"] = QLabel()
        self._labels["ping_timeout"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row3.addWidget(self._labels["ping_timeout"])
        self.ping_timeout_spin = QSpinBox()
        self.ping_timeout_spin.setRange(1, 600)
        self.ping_timeout_spin.setValue(10)
        self.ping_timeout_spin.setFixedWidth(90)
        row3.addWidget(self.ping_timeout_spin)

        row3.addStretch()
        v.addLayout(row3)

        v.addStretch()
        return page

    def _build_send_bar(self):
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self.send_type = QComboBox()
        self.send_type.addItem("text", "text")
        self.send_type.addItem("binary", "binary")
        self.send_type.setFixedWidth(100)
        bar.addWidget(self.send_type)

        self.send_input = QLineEdit()
        self.send_input.setFont(self._mono)
        self.send_input.returnPressed.connect(self._ui_send)
        bar.addWidget(self.send_input, 1)

        self.send_btn = QPushButton()
        self.send_btn.setIcon(theme_loader.get_icon(
            icon_name="play", emoji=">", size=14))
        self.send_btn.setFixedWidth(130)
        self.send_btn.clicked.connect(self._ui_send)
        bar.addWidget(self.send_btn)

        self.ping_btn = QPushButton()
        self.ping_btn.setFixedWidth(100)
        self.ping_btn.clicked.connect(self._ui_ping)
        bar.addWidget(self.ping_btn)

        self.clear_btn = QPushButton()
        self.clear_btn.setFixedWidth(110)
        self.clear_btn.clicked.connect(self._ui_clear)
        bar.addWidget(self.clear_btn)

        self.save_btn = QPushButton()
        self.save_btn.setFixedWidth(120)
        self.save_btn.clicked.connect(self._ui_save_log)
        bar.addWidget(self.save_btn)

        return bar

    def retranslate(self):
        if not hasattr(self, "url_input"):
            return

        self._labels["url"].setText(T("url"))
        self.url_input.setPlaceholderText(T("url.placeholder"))
        self._tabs.setTabText(0, T("tab.messages"))
        self._tabs.setTabText(1, T("tab.connection"))
        self._labels["detail_title"].setText(T("detail.title"))
        self._labels["headers"].setText(T("headers"))
        self.headers_edit.setPlaceholderText(T("headers.placeholder"))
        self._labels["subproto"].setText(T("subprotocols"))
        self.subproto_input.setPlaceholderText(T("subprotocols.placeholder"))
        self._checks["proxy"].setText(T("proxy"))
        self._checks["proxy"].setToolTip(T("proxy.tip"))
        self._checks["ssl"].setText(T("ssl_verify"))
        self._labels["ping_interval"].setText(T("ping_interval"))
        self._labels["ping_timeout"].setText(T("ping_timeout"))
        self.send_input.setPlaceholderText(T("send.placeholder"))
        self.send_btn.setText(T("send"))
        self.ping_btn.setText(T("ping"))
        self.clear_btn.setText(T("clear"))
        self.save_btn.setText(T("save"))
        self.msg_table.setHorizontalHeaderLabels([
            T("col.num"), T("col.time"), T("col.dir"),
            T("col.opcode"), T("col.size"), T("col.payload"),
        ])
        self._refresh_status()
        self._update_ui_state()

    def _ui_connect(self):
        if self._active_id:
            meta = self._connections.get(self._active_id)
            if meta and meta.get("state") in ("connected", "connecting"):
                self._svc_close(self._active_id)
                self._log_line(T("msg.closing"))
                return

        if not HAS_WEBSOCKET:
            QMessageBox.warning(parent_widget(self._widget),
                                T("err.title"), T("err.no_lib"))
            return

        url = resolve_placeholders(self.url_input.text().strip())
        if not url:
            QMessageBox.warning(parent_widget(self._widget),
                                T("err.title"), T("err.no_url"))
            return
        if not (url.startswith("ws://") or url.startswith("wss://")):
            QMessageBox.warning(parent_widget(self._widget),
                                T("err.title"), T("err.bad_scheme"))
            return

        headers_text = resolve_placeholders(self.headers_edit.toPlainText())
        headers = parse_headers(headers_text) if headers_text.strip() else {}
        subprotocols = [s.strip() for s in self.subproto_input.text().split(",")
                        if s.strip()]

        proxy = None
        if self._checks["proxy"].isChecked():
            proxy_url = get_bus().try_call("proxy.get_active")
            if proxy_url:
                proxy = _parse_proxy_url(proxy_url)

        result = self._svc_connect(
            url=url, headers=headers, subprotocols=subprotocols,
            proxy=proxy,
            ping_interval=self.ping_interval_spin.value(),
            ping_timeout=self.ping_timeout_spin.value(),
            ssl_verify=self._checks["ssl"].isChecked(),
        )
        if not result.get("ok"):
            QMessageBox.warning(parent_widget(self._widget),
                                T("err.title"), str(result.get("error")))
            return

        conn_id = result["connection_id"]
        self._active_id = conn_id
        self._clear_msg_table()
        self._log_line(T("msg.connecting", url=url))
        self._refresh_status()
        self._update_ui_state()

    def _ui_send(self):
        if not self._active_id:
            return
        meta = self._connections.get(self._active_id)
        if not meta or meta.get("state") != "connected":
            return
        data = self.send_input.text()
        binary = self.send_type.currentData() == "binary"
        if not data and not binary:
            return
        ok = self._svc_send(self._active_id, data, binary=binary)
        if ok:
            self.send_input.clear()
        else:
            self._log_line(T("msg.send_failed"))

    def _ui_ping(self):
        if not self._active_id:
            return
        if self._svc_ping(self._active_id):
            self._log_line(T("msg.ping_sent"))
        else:
            self._log_line(T("msg.error", error="ping failed"))

    def _ui_clear(self):
        if not self._active_id:
            return
        if QMessageBox.question(
            parent_widget(self._widget),
            T("confirm.clear.title"),
            T("confirm.clear.text"),
        ) != QMessageBox.Yes:
            return
        meta = self._connections.get(self._active_id)
        if meta:
            meta["messages"] = []
            meta["counter"] = 0
        self._clear_msg_table()

    def _ui_save_log(self):
        if not self._active_id:
            return
        meta = self._connections.get(self._active_id)
        if not meta or not meta.get("messages"):
            return
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            T("save.dialog"),
            "ws_log.json",
            "JSON (*.json);;Text (*.txt)")
        if not path:
            return
        try:
            if path.endswith(".txt"):
                with open(path, "w", encoding="utf-8") as f:
                    for m in meta["messages"]:
                        ts = m.get("time", "")
                        direction = m.get("direction", "?")
                        opcode = m.get("opcode", "?")
                        payload = m.get("text") or ""
                        if opcode == "binary" and m.get("data"):
                            payload = m["data"].hex()
                        f.write(f"{ts}  [{direction}] {opcode}  {payload}\n")
            else:
                export = []
                for m in meta["messages"]:
                    export.append({
                        "num": m.get("num"),
                        "time": m.get("time"),
                        "direction": m.get("direction"),
                        "opcode": m.get("opcode"),
                        "size": m.get("size"),
                        "text": m.get("text"),
                        "data_hex": m["data"].hex() if m.get("data") else None,
                    })
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(export, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                T("err.title"), str(e))

    def _clear_msg_table(self):
        if not hasattr(self, "msg_table"):
            return
        try:
            self.msg_table.setRowCount(0)
            self.msg_detail.clear()
        except RuntimeError:
            return

    def _add_msg_row(self, msg):
        if not hasattr(self, "msg_table"):
            return
        try:
            row = self.msg_table.rowCount()
            self.msg_table.insertRow(row)
            self.msg_table.setItem(row, 0,
                                   QTableWidgetItem(str(msg.get("num", ""))))
            self.msg_table.setItem(row, 1,
                                   QTableWidgetItem(msg.get("time", "")))

            direction = msg.get("direction", "?")
            dir_item = QTableWidgetItem(direction)
            if direction == "in":
                dir_item.setForeground(QColor(_c("fg_soft")))
            elif direction == "out":
                dir_item.setForeground(QColor(_c("fg")))
            else:
                dir_item.setForeground(QColor(_c("fg_dim")))
            self.msg_table.setItem(row, 2, dir_item)

            opcode = msg.get("opcode", "")
            op_item = QTableWidgetItem(opcode)
            if opcode in ("ping", "pong"):
                op_item.setForeground(QColor(_c("fg_dim")))
            elif opcode == "close":
                op_item.setForeground(QColor(_c("danger")))
            self.msg_table.setItem(row, 3, op_item)

            self.msg_table.setItem(row, 4,
                                   QTableWidgetItem(str(msg.get("size", 0))))

            preview = msg.get("text") or ""
            if msg.get("opcode") == "binary" and msg.get("data"):
                preview = msg["data"][:64].hex()
            preview = preview[:120]
            self.msg_table.setItem(row, 5, QTableWidgetItem(preview))

            num_item = self.msg_table.item(row, 0)
            if num_item:
                num_item.setData(Qt.UserRole, msg)
        except RuntimeError:
            return

    def _show_msg_detail(self):
        if not hasattr(self, "msg_table"):
            return
        sel = self.msg_table.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        item = self.msg_table.item(row, 0)
        if not item:
            return
        msg = item.data(Qt.UserRole)
        if not msg:
            return
        lines = [
            f"#: {msg.get('num')}",
            f"time: {msg.get('time')}",
            f"direction: {msg.get('direction')}",
            f"opcode: {msg.get('opcode')}",
            f"size: {msg.get('size')} bytes",
            "",
        ]
        if msg.get("opcode") == "binary" and msg.get("data"):
            lines.append("HEX:")
            lines.append(_hex_dump(msg["data"]))
        else:
            lines.append("PAYLOAD:")
            lines.append(msg.get("text") or "")
        try:
            self.msg_detail.setPlainText("\n".join(lines))
        except RuntimeError:
            return

    def _refresh_status(self):
        if not hasattr(self, "status_label"):
            return
        if not self._active_id:
            self.status_label.setText(T("status.idle"))
            self.status_label.setStyleSheet(
                f"color: {_c('fg_dim')}; font-size: 11px;")
            return
        meta = self._connections.get(self._active_id)
        if not meta:
            self.status_label.setText(T("status.idle"))
            return
        state = meta.get("state")
        url = meta.get("url", "")
        sent = meta.get("sent", 0)
        recv = meta.get("received", 0)
        if state == "connected":
            self.status_label.setText(
                T("status.connected", url=url, sent=sent, received=recv))
            self.status_label.setStyleSheet(
                f"color: {_c('success')}; font-size: 11px; font-weight: bold;")
        elif state == "connecting":
            self.status_label.setText(T("status.connecting", url=url))
            self.status_label.setStyleSheet(
                f"color: {_c('warning')}; font-size: 11px; font-weight: bold;")
        else:
            self.status_label.setText(
                T("status.closed", url=url, sent=sent, received=recv))
            self.status_label.setStyleSheet(
                f"color: {_c('fg_dim')}; font-size: 11px;")

    def _update_ui_state(self):
        if not hasattr(self, "url_input"):
            return
        state = None
        if self._active_id:
            meta = self._connections.get(self._active_id)
            if meta:
                state = meta.get("state")

        if state == "connected":
            self.connect_btn.setText(T("disconnect"))
            self.connect_btn.setEnabled(True)
            self.url_input.setEnabled(False)
            self.send_input.setEnabled(True)
            self.send_btn.setEnabled(True)
            self.ping_btn.setEnabled(True)
        elif state == "connecting":
            self.connect_btn.setText(T("connecting"))
            self.connect_btn.setEnabled(True)
            self.url_input.setEnabled(False)
            self.send_input.setEnabled(False)
            self.send_btn.setEnabled(False)
            self.ping_btn.setEnabled(False)
        else:
            self.connect_btn.setText(T("connect"))
            self.connect_btn.setEnabled(HAS_WEBSOCKET)
            self.url_input.setEnabled(True)
            self.send_input.setEnabled(False)
            self.send_btn.setEnabled(False)
            self.ping_btn.setEnabled(False)