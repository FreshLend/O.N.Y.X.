import base64
import gzip
import hashlib
import html
import json
import re
import zlib
import urllib.parse

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QComboBox, QSplitter, QMessageBox, QInputDialog
)

from core.base_module import BaseModule
from core.i18n import tr
from core.utils import color as _c, parent_widget


BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


OPERATIONS = [
    "Base64 encode", "Base64 decode",
    "Base64URL encode", "Base64URL decode",
    "Base32 encode", "Base32 decode",
    "Base58 encode", "Base58 decode",
    "URL encode", "URL decode",
    "Hex encode", "Hex decode",
    "HTML entities", "HTML unescape",
    "ROT13",
    "JSON pretty", "JSON minify",
    "Unicode escape", "Unicode unescape",
    "UTF-16 LE hex",
    "Binary encode", "Binary decode",
    "Gzip (b64)", "Gunzip (b64)",
    "Zlib (b64)", "Unzlib (b64)",
    "XOR hex",
    "MD5", "SHA1", "SHA256", "SHA512",
]


class EncoderModule(BaseModule):
    ALIASES = {
        "Base64": "Base64 encode",
        "Base64URL": "Base64URL encode",
        "Base32": "Base32 encode",
        "Base58": "Base58 encode",
        "URL": "URL encode",
        "Hex": "Hex encode",
        "HTML": "HTML entities",
        "JSON": "JSON pretty",
    }

    def on_load(self, api):
        api.register_service(self.name, "encoder.transform", self._svc_transform,
                             "encoder.transform(mode, text, xor_key=None) -> str")
        api.register_service(self.name, "encoder.pipeline", self._svc_pipeline,
                             "encoder.pipeline(ops, text, xor_key=None) -> str")

    def _svc_transform(self, mode, text, xor_key=None):
        try:
            return self._transform(mode, text, xor_key)
        except Exception as e:
            return f"__ERROR__: {e}"

    def _svc_pipeline(self, ops, text, xor_key=None):
        cur = text
        for op in ops:
            try:
                cur = self._transform(op, cur, xor_key)
            except Exception as e:
                return f"__ERROR__ at '{op}': {e}"
        return cur

    def _transform(self, mode, text, xor_key=None):
        mode = self.ALIASES.get(mode, mode)
        if mode == "Base64 encode":
            return base64.b64encode(text.encode()).decode()
        if mode == "Base64 decode":
            pad = "=" * (-len(text) % 4)
            return base64.b64decode((text + pad).encode()).decode("utf-8", errors="replace")
        if mode == "Base64URL encode":
            return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
        if mode == "Base64URL decode":
            pad = "=" * (-len(text) % 4)
            return base64.urlsafe_b64decode((text + pad).encode()).decode("utf-8", errors="replace")
        if mode == "Base32 encode":
            return base64.b32encode(text.encode()).decode()
        if mode == "Base32 decode":
            pad = "=" * (-len(text) % 8)
            return base64.b32decode((text + pad).upper().encode()).decode("utf-8", errors="replace")
        if mode == "Base58 encode":
            return self._b58_encode(text.encode())
        if mode == "Base58 decode":
            return self._b58_decode(text).decode("utf-8", errors="replace")
        if mode == "URL encode":
            return urllib.parse.quote(text, safe="")
        if mode == "URL decode":
            return urllib.parse.unquote(text)
        if mode == "Hex encode":
            return text.encode().hex()
        if mode == "Hex decode":
            return bytes.fromhex(text).decode("utf-8", errors="replace")
        if mode == "HTML entities":
            return html.escape(text)
        if mode == "HTML unescape":
            return html.unescape(text)
        if mode == "ROT13":
            return self._rot13(text)
        if mode == "JSON pretty":
            return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        if mode == "JSON minify":
            return json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)
        if mode == "Unicode escape":
            return "".join(f"\\u{ord(c):04x}" if ord(c) < 0x10000
                           else f"\\U{ord(c):08x}" for c in text)
        if mode == "Unicode unescape":
            def r(m):
                code = m.group(1) or m.group(2)
                return chr(int(code, 16))
            return re.sub(r"\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})", r, text)
        if mode == "UTF-16 LE hex":
            return text.encode("utf-16-le").hex()
        if mode == "Binary encode":
            return " ".join(f"{b:08b}" for b in text.encode())
        if mode == "Binary decode":
            bits = text.replace(" ", "").replace("\n", "")
            if len(bits) % 8 != 0:
                raise ValueError("bit count not multiple of 8")
            return bytes(int(bits[i:i + 8], 2)
                         for i in range(0, len(bits), 8)).decode("utf-8", errors="replace")
        if mode == "Gzip (b64)":
            return base64.b64encode(gzip.compress(text.encode())).decode()
        if mode == "Gunzip (b64)":
            return gzip.decompress(base64.b64decode(text)).decode("utf-8", errors="replace")
        if mode == "Zlib (b64)":
            return base64.b64encode(zlib.compress(text.encode())).decode()
        if mode == "Unzlib (b64)":
            return zlib.decompress(base64.b64decode(text)).decode("utf-8", errors="replace")
        if mode == "XOR hex":
            if not xor_key:
                raise ValueError("XOR key required")
            key = xor_key.encode()
            data = text.encode()
            out = bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))
            return out.hex()
        if mode == "MD5":
            return hashlib.md5(text.encode()).hexdigest()
        if mode == "SHA1":
            return hashlib.sha1(text.encode()).hexdigest()
        if mode == "SHA256":
            return hashlib.sha256(text.encode()).hexdigest()
        if mode == "SHA512":
            return hashlib.sha512(text.encode()).hexdigest()
        raise ValueError(f"unknown mode: {mode}")

    def _b58_encode(self, data):
        n = int.from_bytes(data, "big")
        out = ""
        while n > 0:
            n, r = divmod(n, 58)
            out = BASE58_ALPHABET[r] + out
        pad = 0
        for b in data:
            if b == 0:
                pad += 1
            else:
                break
        return BASE58_ALPHABET[0] * pad + (out or "")

    def _b58_decode(self, s):
        n = 0
        for ch in s:
            n = n * 58 + BASE58_ALPHABET.index(ch)
        pad = 0
        for ch in s:
            if ch == BASE58_ALPHABET[0]:
                pad += 1
            else:
                break
        length = (n.bit_length() + 7) // 8
        return b"\x00" * pad + n.to_bytes(length, "big")

    def _rot13(self, s):
        res = []
        for ch in s:
            if "a" <= ch <= "z":
                res.append(chr((ord(ch) - ord("a") + 13) % 26 + ord("a")))
            elif "A" <= ch <= "Z":
                res.append(chr((ord(ch) - ord("A") + 13) % 26 + ord("A")))
            else:
                res.append(ch)
        return "".join(res)

    def _auto_detect(self, s):
        s = s.strip()
        if not s:
            return None
        if re.fullmatch(r"[0-9a-fA-F]+", s) and len(s) % 2 == 0 and len(s) >= 4:
            return "Hex decode"
        if re.fullmatch(r"[A-Z2-7]+=*", s) and len(s) >= 8 and "=" in s:
            return "Base32 decode"
        if re.fullmatch(r"[A-Za-z0-9+/]+=*", s) and len(s) % 4 == 0 and len(s) >= 8:
            return "Base64 decode"
        if s.startswith("%") or re.search(r"%[0-9a-fA-F]{2}", s):
            return "URL decode"
        if "&lt;" in s or "&gt;" in s or "&amp;" in s:
            return "HTML unescape"
        if re.search(r"\\u[0-9a-fA-F]{4}", s):
            return "Unicode unescape"
        if s.startswith("{") or s.startswith("["):
            try:
                json.loads(s)
                return "JSON pretty"
            except Exception:
                pass
        if s.count(".") == 2 and len(s) > 40:
            try:
                parts = s.split(".")
                base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4))
                return "Base64URL decode"
            except Exception:
                pass
        if re.fullmatch(r"[01\s]+", s):
            return "Binary decode"
        return None

    def __init__(self):
        self._widget = None
        self._rows = []

    def create_widget(self) -> QWidget:
        root = QWidget()
        self._widget = root
        self._rows = []

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        mono = QFont("Consolas", 10)

        header = QHBoxLayout()
        self.pipe_lbl = QLabel()
        self.pipe_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        header.addWidget(self.pipe_lbl)

        header.addStretch()

        self.detect_btn = QPushButton()
        self.detect_btn.setFixedWidth(180)
        self.detect_btn.clicked.connect(self._auto_detect_ui)
        header.addWidget(self.detect_btn)

        self.add_btn = QPushButton()
        self.add_btn.setFixedWidth(120)
        self.add_btn.clicked.connect(lambda: self._add_op())
        header.addWidget(self.add_btn)

        self.clear_pipe_btn = QPushButton()
        self.clear_pipe_btn.setFixedWidth(140)
        self.clear_pipe_btn.clicked.connect(self._clear_pipeline)
        header.addWidget(self.clear_pipe_btn)

        self.swap_btn = QPushButton()
        self.swap_btn.setFixedWidth(120)
        self.swap_btn.clicked.connect(self._swap)
        header.addWidget(self.swap_btn)

        self.copy_btn = QPushButton()
        self.copy_btn.setFixedWidth(140)
        self.copy_btn.clicked.connect(self._copy_out)
        header.addWidget(self.copy_btn)

        layout.addLayout(header)

        self.pipe_container = QVBoxLayout()
        self.pipe_container.setSpacing(4)
        self._pipe_widget = QWidget()
        self._pipe_widget.setLayout(self.pipe_container)
        layout.addWidget(self._pipe_widget)

        split = QSplitter(Qt.Vertical)

        in_w = QWidget()
        in_l = QVBoxLayout(in_w)
        in_l.setContentsMargins(0, 0, 0, 0)
        self.in_lbl = QLabel()
        self.in_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        in_l.addWidget(self.in_lbl)
        self.inp = QPlainTextEdit()
        self.inp.setFont(mono)
        self.inp.textChanged.connect(self._process)
        in_l.addWidget(self.inp)

        out_w = QWidget()
        out_l = QVBoxLayout(out_w)
        out_l.setContentsMargins(0, 0, 0, 0)
        self.out_lbl = QLabel()
        self.out_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        out_l.addWidget(self.out_lbl)
        self.out = QPlainTextEdit()
        self.out.setFont(mono)
        self.out.setReadOnly(True)
        out_l.addWidget(self.out)

        split.addWidget(in_w)
        split.addWidget(out_w)
        split.setSizes([280, 280])
        layout.addWidget(split, 1)

        self.status = QLabel("-")
        self.status.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.status)

        self.retranslate()
        self._add_op("Base64 encode")
        return root

    def _add_op(self, mode=None):
        if mode is None or not isinstance(mode, str):
            mode = "Base64 encode"

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)

        num = QLabel(f"{len(self._rows) + 1}.")
        num.setFixedWidth(28)
        num.setStyleSheet(f"color: {_c('fg_dim')};")
        rl.addWidget(num)

        box = QComboBox()
        box.addItems(OPERATIONS)
        if mode in OPERATIONS:
            box.setCurrentText(mode)
        else:
            box.setCurrentIndex(0)
        box.setMinimumWidth(180)
        box.currentTextChanged.connect(lambda _: self._process())
        rl.addWidget(box)

        xor_key = QLineEdit()
        xor_key.setFont(QFont("Consolas", 10))
        xor_key.setPlaceholderText("XOR key")
        xor_key.setFixedWidth(120)
        xor_key.setVisible(box.currentText() == "XOR hex")
        xor_key.textChanged.connect(lambda _: self._process())
        rl.addWidget(xor_key)

        up_btn = QPushButton("↑")
        up_btn.setFixedWidth(36)
        up_btn.clicked.connect(lambda: self._move_op(row, -1))
        rl.addWidget(up_btn)

        down_btn = QPushButton("↓")
        down_btn.setFixedWidth(36)
        down_btn.clicked.connect(lambda: self._move_op(row, 1))
        rl.addWidget(down_btn)

        del_btn = QPushButton("×")
        del_btn.setFixedWidth(36)
        del_btn.clicked.connect(lambda: self._remove_op(row))
        rl.addWidget(del_btn)

        rl.addStretch()

        def _on_op_change(_):
            xor_key.setVisible(box.currentText() == "XOR hex")
            self._process()

        box.currentTextChanged.connect(_on_op_change)

        self.pipe_container.addWidget(row)
        self._rows.append({"widget": row, "box": box, "num": num, "xor_key": xor_key})
        self._renumber()
        self._process()

    def _remove_op(self, row):
        entry = None
        for r in self._rows:
            if r["widget"] is row:
                entry = r
                break
        if entry is None:
            return
        self._rows.remove(entry)
        row.setParent(None)
        row.deleteLater()
        self._renumber()
        self._process()

    def _move_op(self, row, direction):
        idx = None
        for i, r in enumerate(self._rows):
            if r["widget"] is row:
                idx = i
                break
        if idx is None:
            return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self._rows):
            return
        self._rows[idx], self._rows[new_idx] = self._rows[new_idx], self._rows[idx]
        for r in self._rows:
            self.pipe_container.removeWidget(r["widget"])
        for r in self._rows:
            self.pipe_container.addWidget(r["widget"])
        self._renumber()
        self._process()

    def _renumber(self):
        for i, r in enumerate(self._rows):
            r["num"].setText(f"{i + 1}.")

    def _clear_pipeline(self):
        for r in list(self._rows):
            r["widget"].setParent(None)
            r["widget"].deleteLater()
        self._rows = []
        self._process()

    def _current_ops(self):
        return [r["box"].currentText() for r in self._rows]

    def retranslate(self):
        def t(k, **kw):
            return tr(k, module="encoder", **kw)
        if not hasattr(self, "inp"):
            return
        self.pipe_lbl.setText(t("pipeline"))
        self.detect_btn.setText(t("detect"))
        self.add_btn.setText(t("add_op"))
        self.clear_pipe_btn.setText(t("clear_pipe"))
        self.swap_btn.setText(t("swap"))
        self.copy_btn.setText(t("copy"))
        self.in_lbl.setText(t("input"))
        self.out_lbl.setText(t("output"))
        if self.out.toPlainText():
            self._process()

    def _process(self):
        src = self.inp.toPlainText()
        rows = self._rows
        if not rows:
            self.out.setPlainText(src)
            self.status.setText(tr("status.no_ops", module="encoder"))
            return

        cur = src
        applied = []
        for i, r in enumerate(rows):
            op = r["box"].currentText()
            try:
                xor_key = r["xor_key"].text() if op == "XOR hex" else None
                if op == "XOR hex" and not xor_key:
                    self.out.setPlainText("")
                    self.status.setText(tr("status.error", module="encoder",
                                            step=i + 1, op=op,
                                            error="XOR key required"))
                    return
                cur = self._transform(op, cur, xor_key)
                applied.append(op)
            except Exception as e:
                self.out.setPlainText("")
                self.status.setText(
                    tr("status.error", module="encoder", step=i + 1, op=op, error=e))
                return
        self.out.setPlainText(cur)
        self.status.setText(tr("status.ok", module="encoder",
                                steps=len(applied)))

    def _auto_detect_ui(self):
        text = self.inp.toPlainText().strip()
        if not text:
            return
        detected = self._auto_detect(text)
        if not detected:
            QMessageBox.information(parent_widget(self._widget),
                                    tr("detect.title", module="encoder"),
                                    tr("detect.not_found", module="encoder"))
            return
        QMessageBox.information(parent_widget(self._widget),
                                tr("detect.title", module="encoder"),
                                tr("detect.found", module="encoder", op=detected))
        self._clear_pipeline()
        self._add_op(detected)

    def _swap(self):
        a = self.inp.toPlainText()
        b = self.out.toPlainText()
        self.inp.blockSignals(True)
        self.inp.setPlainText(b)
        self.inp.blockSignals(False)
        self.out.setPlainText(a)

    def _copy_out(self):
        QGuiApplication.clipboard().setText(self.out.toPlainText())