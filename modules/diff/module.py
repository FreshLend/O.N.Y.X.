import json
import re
from difflib import SequenceMatcher, HtmlDiff, unified_diff

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QSyntaxHighlighter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSplitter, QComboBox, QMessageBox, QCheckBox,
    QTextBrowser, QFileDialog
)

from core.base_module import BaseModule
from core.history import get_history
from core.i18n import tr
from core.utils import color as _c, parent_widget
from styles import loader as theme_loader


DYNAMIC_KEYS = {"timestamp", "date", "iat", "exp", "random", "uptime",
                "request_id", "session_id", "created_at", "server_time",
                "ts", "nbf"}


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, doc, add_color, del_color):
        super().__init__(doc)
        self.add_color = add_color
        self.del_color = del_color

    def highlightBlock(self, text):
        if text.startswith("+") and not text.startswith("+++"):
            fmt = QTextCharFormat()
            fmt.setForeground(self.add_color)
            self.setFormat(0, len(text), fmt)
        elif text.startswith("-") and not text.startswith("---"):
            fmt = QTextCharFormat()
            fmt.setForeground(self.del_color)
            self.setFormat(0, len(text), fmt)


class DiffModule(BaseModule):
    def on_load(self, api):
        api.register_service(self.name, "diff.compare", self._svc_compare,
                             "diff.compare(text_a, text_b, mode='Line diff', "
                             "ignore_dynamic=False, ignore_whitespace=False, "
                             "ignore_case=False) -> list[str]")

    def _svc_compare(self, a, b, mode="Line diff", ignore_dynamic=False,
                     ignore_whitespace=False, ignore_case=False):
        if mode in ("JSON keys", "XML", "YAML"):
            try:
                ja, jb = self._parse_structured(a, b, mode)
            except Exception as e:
                return [f"parse error: {e}"]
            if ignore_dynamic:
                ja = self._strip_dynamic(ja)
                jb = self._strip_dynamic(jb)
            out = []
            self._walk_json(ja, jb, "", out)
            return out
        a_lines = a.splitlines(keepends=True)
        b_lines = b.splitlines(keepends=True)
        if ignore_whitespace:
            a_lines = [self._norm_ws(l) for l in a_lines]
            b_lines = [self._norm_ws(l) for l in b_lines]
        if ignore_case:
            a_lines = [l.lower() for l in a_lines]
            b_lines = [l.lower() for l in b_lines]
        return list(unified_diff(a_lines, b_lines, fromfile="A", tofile="B", n=2))

    def _norm_ws(self, s):
        return re.sub(r"\s+", " ", s).strip() + "\n"

    def _strip_dynamic(self, obj):
        if isinstance(obj, dict):
            return {k: self._strip_dynamic(v) for k, v in obj.items()
                    if k not in DYNAMIC_KEYS}
        if isinstance(obj, list):
            return [self._strip_dynamic(x) for x in obj]
        return obj

    def _parse_structured(self, a, b, mode):
        if mode == "JSON keys":
            return json.loads(a), json.loads(b)
        if mode == "XML":
            import xml.etree.ElementTree as ET
            return self._etree_to_dict(ET.fromstring(a)), self._etree_to_dict(ET.fromstring(b))
        if mode == "YAML":
            try:
                import yaml
            except ImportError:
                raise RuntimeError("pyyaml not installed")
            return yaml.safe_load(a), yaml.safe_load(b)
        raise ValueError(f"unknown mode {mode}")

    def _etree_to_dict(self, elem):
        out = {"tag": elem.tag}
        if elem.attrib:
            out["@attrs"] = dict(elem.attrib)
        children = list(elem)
        if children:
            grouped = {}
            for c in children:
                cd = self._etree_to_dict(c)
                grouped.setdefault(c.tag, []).append(cd)
            out["children"] = grouped
        else:
            txt = (elem.text or "").strip()
            if txt:
                out["text"] = txt
        return out

    def create_widget(self) -> QWidget:
        root = QWidget()
        self._widget = root

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        mono = QFont("Consolas", 10)

        bar1 = QHBoxLayout()
        self.mode_lbl = QLabel()
        self.mode_lbl.setStyleSheet(f"color: {_c('fg_dim')};")
        bar1.addWidget(self.mode_lbl)

        self.mode = QComboBox()
        self.mode.addItem("Line diff", "line")
        self.mode.addItem("JSON keys", "json")
        self.mode.addItem("XML", "xml")
        self.mode.addItem("YAML", "yaml")
        self.mode.setFixedWidth(140)
        bar1.addWidget(self.mode)

        self.view_lbl = QLabel()
        self.view_lbl.setStyleSheet(f"color: {_c('fg_dim')};")
        bar1.addWidget(self.view_lbl)

        self.view_box = QComboBox()
        self.view_box.addItem("Unified", "unified")
        self.view_box.addItem("Side-by-side", "side")
        self.view_box.addItem("Word-level", "word")
        self.view_box.setFixedWidth(150)
        bar1.addWidget(self.view_box)

        self.ignore_dynamic = QCheckBox()
        bar1.addWidget(self.ignore_dynamic)

        self.ignore_ws = QCheckBox()
        bar1.addWidget(self.ignore_ws)

        self.ignore_case = QCheckBox()
        bar1.addWidget(self.ignore_case)

        bar1.addStretch()
        layout.addLayout(bar1)

        bar2 = QHBoxLayout()
        bar2.addStretch()

        self.hist_btn = QPushButton()
        self.hist_btn.setFixedWidth(230)
        self.hist_btn.clicked.connect(self._load_from_history)
        bar2.addWidget(self.hist_btn)

        self.save_btn = QPushButton()
        self.save_btn.setFixedWidth(140)
        self.save_btn.clicked.connect(self._save_diff)
        bar2.addWidget(self.save_btn)

        self.run_btn = QPushButton()
        self.run_btn.setIcon(theme_loader.get_icon(icon_name="play", emoji=">", size=14))
        self.run_btn.setFixedWidth(170)
        self.run_btn.clicked.connect(self._run)
        bar2.addWidget(self.run_btn)

        layout.addLayout(bar2)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.a_lbl = QLabel()
        self.a_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        ll.addWidget(self.a_lbl)
        self.a_edit = QPlainTextEdit()
        self.a_edit.setFont(mono)
        ll.addWidget(self.a_edit)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.b_lbl = QLabel()
        self.b_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        rl.addWidget(self.b_lbl)
        self.b_edit = QPlainTextEdit()
        self.b_edit.setFont(mono)
        rl.addWidget(self.b_edit)

        split.addWidget(left)
        split.addWidget(right)
        layout.addWidget(split, 1)

        self.result_lbl = QLabel()
        self.result_lbl.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.result_lbl)

        self.result = QPlainTextEdit()
        self.result.setFont(mono)
        self.result.setReadOnly(True)
        layout.addWidget(self.result, 1)

        self.html_view = QTextBrowser()
        self.html_view.setOpenExternalLinks(False)
        self.html_view.hide()
        layout.addWidget(self.html_view, 1)

        self._hl = DiffHighlighter(
            self.result.document(),
            add_color=QColor(_c("success", "#66d9a0")),
            del_color=QColor(_c("danger", "#ff4d6d")))

        self.view_box.currentIndexChanged.connect(self._switch_view)
        self.retranslate()
        return root

    def retranslate(self):
        def t(k, **kw):
            return tr(k, module="diff", **kw)
        if not hasattr(self, "mode"):
            return
        cur_mode = self.mode.currentIndex()
        cur_view = self.view_box.currentIndex()

        self.mode.blockSignals(True)
        self.view_box.blockSignals(True)

        self.mode.clear()
        self.mode.addItem(t("mode.line"), "line")
        self.mode.addItem(t("mode.json"), "json")
        self.mode.addItem(t("mode.xml"), "xml")
        self.mode.addItem(t("mode.yaml"), "yaml")

        self.view_box.clear()
        self.view_box.addItem(t("view.unified"), "unified")
        self.view_box.addItem(t("view.side"), "side")
        self.view_box.addItem(t("view.word"), "word")

        if cur_mode >= 0:
            self.mode.setCurrentIndex(cur_mode)
        if cur_view >= 0:
            self.view_box.setCurrentIndex(cur_view)

        self.mode.blockSignals(False)
        self.view_box.blockSignals(False)

        self.mode_lbl.setText(t("mode"))
        self.view_lbl.setText(t("view"))
        self.ignore_dynamic.setText(t("ignore_dynamic"))
        self.ignore_dynamic.setToolTip(t("ignore_dynamic.tip"))
        self.ignore_ws.setText(t("ignore_ws"))
        self.ignore_case.setText(t("ignore_case"))
        self.hist_btn.setText(t("load_history"))
        self.save_btn.setText(t("save"))
        self.run_btn.setText(t("run"))
        self.a_lbl.setText(t("text_a"))
        self.b_lbl.setText(t("text_b"))
        self.result_lbl.setText(t("result"))

    def _current_mode_key(self):
        data = self.mode.currentData()
        return data if data else "line"

    def _current_view_key(self):
        data = self.view_box.currentData()
        return data if data else "unified"

    def _switch_view(self, *_):
        mode = self._current_view_key()
        if mode == "side":
            self.result.hide()
            self.html_view.show()
        else:
            self.html_view.hide()
            self.result.show()

    def _load_from_history(self):
        rows = get_history().list(limit=2)
        if len(rows) < 2:
            QMessageBox.information(
                parent_widget(self._widget),
                tr("history.title", module="diff"),
                tr("history.need_two", module="diff"))
            return
        r1 = get_history().get(rows[0]["id"])
        r2 = get_history().get(rows[1]["id"])
        self.a_edit.setPlainText(r2.get("response_body") or "")
        self.b_edit.setPlainText(r1.get("response_body") or "")

    def _run(self):
        a = self.a_edit.toPlainText()
        b = self.b_edit.toPlainText()
        mode_key = self._current_mode_key()
        view_key = self._current_view_key()
        ignore = self.ignore_dynamic.isChecked()
        ignore_ws = self.ignore_ws.isChecked()
        ignore_case = self.ignore_case.isChecked()

        if view_key == "side":
            self._render_side(a, b, mode_key, ignore, ignore_ws, ignore_case)
            return

        if view_key == "word":
            self._render_word_level(a, b)
            return

        if mode_key in ("json", "xml", "yaml"):
            self._diff_structured(a, b, mode_key, ignore)
        else:
            self._diff_lines(a, b, ignore_ws, ignore_case)

    def _mode_to_api(self, mode_key):
        return {"json": "JSON keys", "xml": "XML", "yaml": "YAML"}.get(mode_key, "Line diff")

    def _diff_lines(self, a, b, ignore_ws=False, ignore_case=False):
        a_lines = a.splitlines(keepends=True)
        b_lines = b.splitlines(keepends=True)
        if ignore_ws:
            a_lines = [self._norm_ws(l) for l in a_lines]
            b_lines = [self._norm_ws(l) for l in b_lines]
        if ignore_case:
            a_lines = [l.lower() for l in a_lines]
            b_lines = [l.lower() for l in b_lines]
        diff = unified_diff(a_lines, b_lines, fromfile="A", tofile="B", n=2)
        self.result.setPlainText("".join(diff))

    def _diff_structured(self, a, b, mode_key, ignore_dynamic):
        api_mode = self._mode_to_api(mode_key)
        try:
            ja, jb = self._parse_structured(a, b, api_mode)
        except Exception as e:
            self.result.setPlainText(
                tr("err.parse", module="diff", error=e))
            return
        if ignore_dynamic:
            ja = self._strip_dynamic(ja)
            jb = self._strip_dynamic(jb)
        lines = []
        self._walk_json(ja, jb, "", lines)
        self.result.setPlainText("\n".join(lines) if lines
                                 else tr("empty_changes", module="diff"))

    def _render_side(self, a, b, mode_key, ignore_dynamic, ignore_ws, ignore_case):
        api_mode = self._mode_to_api(mode_key)
        if api_mode in ("JSON keys", "XML", "YAML"):
            try:
                ja, jb = self._parse_structured(a, b, api_mode)
                if ignore_dynamic:
                    ja = self._strip_dynamic(ja)
                    jb = self._strip_dynamic(jb)
                a = json.dumps(ja, indent=2, ensure_ascii=False, default=str)
                b = json.dumps(jb, indent=2, ensure_ascii=False, default=str)
            except Exception as e:
                self.html_view.setHtml(f"<pre>parse error: {e}</pre>")
                return

        a_lines = a.splitlines()
        b_lines = b.splitlines()

        if ignore_ws:
            a_lines = [self._norm_ws(l).rstrip("\n") for l in a_lines]
            b_lines = [self._norm_ws(l).rstrip("\n") for l in b_lines]
        if ignore_case:
            a_lines = [l.lower() for l in a_lines]
            b_lines = [l.lower() for l in b_lines]

        hd = HtmlDiff(tabsize=4, wrapcolumn=80)
        html = hd.make_table(a_lines, b_lines,
                              fromdesc="A (old)", todesc="B (new)",
                              context=False, numlines=2)

        styled = f"""
        <html><head><style>
        body {{ background: {_c('bg2')}; color: {_c('fg')};
                font-family: Consolas, monospace; font-size: 11px; }}
        table.diff {{ border-collapse: collapse; width: 100%; }}
        table.diff td {{ padding: 2px 6px; vertical-align: top;
                          white-space: pre-wrap; word-break: break-all; }}
        .diff_header {{ background: {_c('bg3')}; color: {_c('fg_soft')};
                         text-align: right; padding-right: 8px; }}
        td.diff_header {{ width: 40px; }}
        .diff_next {{ background: {_c('bg3')}; }}
        .diff_add {{ background: rgba(102,217,160,0.12); }}
        .diff_chg {{ background: rgba(255,184,77,0.15); }}
        .diff_sub {{ background: rgba(255,77,109,0.12); }}
        </style></head><body>{html}</body></html>
        """
        self.html_view.setHtml(styled)

    def _render_word_level(self, a, b):
        a_lines = a.splitlines()
        b_lines = b.splitlines()
        out = []
        sm = SequenceMatcher(None, a_lines, b_lines)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for line in a_lines[i1:i2]:
                    out.append("  " + line)
            elif tag == "delete":
                for line in a_lines[i1:i2]:
                    out.append("- " + line)
            elif tag == "insert":
                for line in b_lines[j1:j2]:
                    out.append("+ " + line)
            elif tag == "replace":
                old = a_lines[i1:i2]
                new = b_lines[j1:j2]
                for k in range(max(len(old), len(new))):
                    o = old[k] if k < len(old) else ""
                    n = new[k] if k < len(new) else ""
                    if o and n:
                        word_diff = self._inline_word_diff(o, n)
                        out.append("~ " + word_diff)
                    elif o:
                        out.append("- " + o)
                    elif n:
                        out.append("+ " + n)
        self.result.setPlainText("\n".join(out)
                                 if out else tr("empty_changes", module="diff"))

    def _inline_word_diff(self, o, n):
        sm = SequenceMatcher(None, o.split(), n.split())
        parts = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                parts.append(" ".join(o.split()[i1:i2]))
            elif tag == "delete":
                parts.append("[-" + " ".join(o.split()[i1:i2]) + "-]")
            elif tag == "insert":
                parts.append("{+" + " ".join(n.split()[j1:j2]) + "+}")
            elif tag == "replace":
                parts.append("[-" + " ".join(o.split()[i1:i2]) + "-]")
                parts.append("{+" + " ".join(n.split()[j1:j2]) + "+}")
        return " ".join(parts)

    def _walk_json(self, a, b, prefix, out):
        if type(a) != type(b):
            out.append(f"~ {prefix}  type: {type(a).__name__} -> {type(b).__name__}")
            return
        if isinstance(a, dict):
            for k in sorted(set(a.keys()) | set(b.keys())):
                p = f"{prefix}.{k}" if prefix else k
                if k not in a:
                    out.append(f"+ {p} = {b[k]!r}")
                elif k not in b:
                    out.append(f"- {p} = {a[k]!r}")
                else:
                    self._walk_json(a[k], b[k], p, out)
        elif isinstance(a, list):
            if len(a) != len(b):
                out.append(f"~ {prefix}  len: {len(a)} -> {len(b)}")
            for i in range(min(len(a), len(b))):
                self._walk_json(a[i], b[i], f"{prefix}[{i}]", out)
        else:
            if a != b:
                out.append(f"~ {prefix}  {a!r} -> {b!r}")

    def _save_diff(self):
        text = self.result.toPlainText()
        html = self.html_view.toHtml() if self.html_view.isVisible() else ""
        if not text and not html:
            return
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("save.title", module="diff"),
            "diff.txt",
            "Text (*.txt);;Unified diff (*.diff);;HTML (*.html)")
        if not path:
            return
        try:
            if path.endswith(".html") and html:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text or html)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="diff"), str(e))