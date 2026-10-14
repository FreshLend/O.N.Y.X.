import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPainter, QColor, QBrush, QTextDocument
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox,
    QPushButton, QPlainTextEdit, QFileDialog, QCheckBox, QMessageBox,
    QLineEdit, QDialog, QListWidget, QListWidgetItem, QInputDialog
)

from core.base_module import BaseModule
from core.history import get_history
from core.i18n import tr
from core.utils import color as _c, parent_widget
from styles import loader as theme_loader


SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "report_snapshots"


class StatusChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self._data = {}

    def set_data(self, buckets):
        self._data = dict(buckets)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        p.fillRect(0, 0, w, h, QColor(_c("bg2")))

        total = sum(self._data.values())
        if total == 0:
            p.setPen(QColor(_c("fg_dim")))
            p.setFont(QFont("Consolas", 10))
            p.drawText(self.rect(), Qt.AlignCenter, "(no data)")
            return

        colors = {
            "2xx": QColor(_c("success", "#66d9a0")),
            "3xx": QColor(_c("warning", "#ffb84d")),
            "4xx": QColor(_c("danger", "#ff4d6d")),
            "5xx": QColor("#ff2288"),
            "ERR": QColor(_c("fg_dark", "#444444")),
        }

        bar_x = 10
        bar_y = 20
        bar_w = w - 20
        bar_h = 34
        p.drawRect(bar_x, bar_y, bar_w, bar_h)

        cur_x = bar_x
        for key in ("2xx", "3xx", "4xx", "5xx", "ERR"):
            count = self._data.get(key, 0)
            if count == 0:
                continue
            seg_w = int(bar_w * count / total)
            p.fillRect(cur_x, bar_y, seg_w, bar_h, colors[key])
            cur_x += seg_w

        p.setFont(QFont("Consolas", 9))
        legend_y = bar_y + bar_h + 12
        lx = bar_x
        for key in ("2xx", "3xx", "4xx", "5xx", "ERR"):
            count = self._data.get(key, 0)
            if count == 0:
                continue
            p.setBrush(QBrush(colors[key]))
            p.setPen(Qt.NoPen)
            p.drawRect(lx, legend_y, 12, 12)
            p.setPen(QColor(_c("fg")))
            p.drawText(lx + 16, legend_y + 11, f"{key}: {count}")
            lx += 90

        p.end()


class ReporterModule(BaseModule):
    TIME_RANGES = [
        ("times.all", None),
        ("times.5m", 5),
        ("times.15m", 15),
        ("times.hour", 60),
        ("times.6h", 360),
        ("times.24h", 1440),
    ]

    SLOW_THRESHOLD_MS = 1000

    def on_load(self, api):
        api.register_service(self.name, "reporter.generate", self._svc_generate,
                             "reporter.generate(fmt='Markdown', limit=100, "
                             "only_errors=False, module=None, session=None, "
                             "format=None) -> str")
        api.register_service(self.name, "reporter.snapshot", self._svc_snapshot,
                             "reporter.snapshot(name, filters=None) -> dict")
        api.register_service(self.name, "reporter.compare", self._svc_compare,
                             "reporter.compare(snap_a, snap_b) -> dict")

    def _svc_generate(self, fmt="Markdown", limit=100,
                      only_errors=False, module=None, session=None,
                      format=None, **kwargs) -> str:
        if format is not None:
            fmt = format
        rows = get_history().list(module=module, session=session, limit=limit)
        records = []
        for r in rows:
            rec = get_history().get(r["id"])
            if rec:
                records.append(rec)
        if only_errors:
            records = [r for r in records if self._is_problem(r)]
        return self._render_full(records, fmt)

    def _svc_snapshot(self, name, filters=None):
        snap = {
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "filters": filters or {},
            "records": [],
        }
        records = self._fetch_raw(filters or {})
        for r in records:
            snap["records"].append({
                "id": r.get("id"),
                "module": r.get("module"),
                "method": r.get("method"),
                "url": r.get("url"),
                "status": r.get("status"),
                "elapsed_ms": int((r.get("elapsed") or 0) * 1000),
                "error": r.get("error"),
            })
        return snap

    def _svc_compare(self, snap_a, snap_b):
        key = lambda r: (r.get("method"), r.get("url"), r.get("status"))
        a_keys = {key(r): r for r in snap_a.get("records", [])}
        b_keys = {key(r): r for r in snap_b.get("records", [])}
        only_a = [a_keys[k] for k in a_keys if k not in b_keys]
        only_b = [b_keys[k] for k in b_keys if k not in a_keys]
        common = [k for k in a_keys if k in b_keys]
        slower = []
        faster = []
        for k in common:
            a_ms = a_keys[k].get("elapsed_ms") or 0
            b_ms = b_keys[k].get("elapsed_ms") or 0
            if b_ms > a_ms * 1.2:
                slower.append({"key": k, "before": a_ms, "after": b_ms})
            elif b_ms < a_ms * 0.8:
                faster.append({"key": k, "before": a_ms, "after": b_ms})
        return {
            "only_in_a": only_a,
            "only_in_b": only_b,
            "slower": slower,
            "faster": faster,
            "common": len(common),
        }

    def _fetch_raw(self, filters):
        return get_history().list(
            module=filters.get("module"),
            session=filters.get("session"),
            limit=filters.get("limit", 500),
            since=filters.get("since"),
        )

    def __init__(self):
        self._widget = None
        self._lbls = {}
        self._btns = {}

    def create_widget(self) -> QWidget:
        root = QWidget()
        self._widget = root

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        mono = QFont("Consolas", 10)

        row1 = QHBoxLayout()
        self._lbls = {}
        self._btns = {}
        self._lbls["format"] = QLabel()
        self._lbls["format"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row1.addWidget(self._lbls["format"])
        self.fmt = QComboBox()
        self.fmt.addItems(["Markdown", "HTML", "JSON", "Plain text"])
        self.fmt.setFixedWidth(150)
        row1.addWidget(self.fmt)

        self._lbls["module"] = QLabel()
        self._lbls["module"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row1.addWidget(self._lbls["module"])
        self.module_filter = QComboBox()
        self.module_filter.setFixedWidth(190)
        self.module_filter.currentTextChanged.connect(lambda _: self._on_module_changed())
        row1.addWidget(self.module_filter)

        self._lbls["session"] = QLabel()
        self._lbls["session"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row1.addWidget(self._lbls["session"])
        self.session_filter = QComboBox()
        self.session_filter.setFixedWidth(200)
        self.session_filter.currentTextChanged.connect(lambda _: self._update_count())
        row1.addWidget(self.session_filter)

        self._lbls["time"] = QLabel()
        self._lbls["time"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row1.addWidget(self._lbls["time"])
        self.time_filter = QComboBox()
        self.time_filter.setFixedWidth(150)
        self.time_filter.currentTextChanged.connect(lambda _: self._update_count())
        row1.addWidget(self.time_filter)

        self._lbls["limit"] = QLabel()
        self._lbls["limit"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row1.addWidget(self._lbls["limit"])
        self.limit = QSpinBox()
        self.limit.setRange(10, 100000)
        self.limit.setValue(500)
        self.limit.setFixedWidth(90)
        row1.addWidget(self.limit)

        row1.addStretch()
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self._lbls["search"] = QLabel()
        self._lbls["search"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row2.addWidget(self._lbls["search"])
        self.search_input = QLineEdit()
        self.search_input.textChanged.connect(lambda _: self._update_count())
        row2.addWidget(self.search_input, 1)

        self.problems_only = QCheckBox()
        self.problems_only.stateChanged.connect(lambda _: self._update_count())
        row2.addWidget(self.problems_only)

        self.group_by_url = QCheckBox()
        self.group_by_url.stateChanged.connect(lambda _: self._generate())
        row2.addWidget(self.group_by_url)

        layout.addLayout(row2)

        row3 = QHBoxLayout()
        self.count_label = QLabel("-")
        self.count_label.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row3.addWidget(self.count_label)
        row3.addStretch()

        self._btns["refresh"] = QPushButton()
        self._btns["refresh"].setFixedWidth(120)
        self._btns["refresh"].clicked.connect(self._refresh_all)
        row3.addWidget(self._btns["refresh"])

        self._btns["generate"] = QPushButton()
        self._btns["generate"].setIcon(
            theme_loader.get_icon(icon_name="play", emoji=">", size=14))
        self._btns["generate"].setFixedWidth(160)
        self._btns["generate"].clicked.connect(self._generate)
        row3.addWidget(self._btns["generate"])

        self._btns["snapshot"] = QPushButton()
        self._btns["snapshot"].setFixedWidth(170)
        self._btns["snapshot"].clicked.connect(self._save_snapshot)
        row3.addWidget(self._btns["snapshot"])

        self._btns["compare"] = QPushButton()
        self._btns["compare"].setFixedWidth(160)
        self._btns["compare"].clicked.connect(self._compare_snapshots)
        row3.addWidget(self._btns["compare"])

        layout.addLayout(row3)

        row4 = QHBoxLayout()
        self._btns["save"] = QPushButton()
        self._btns["save"].setFixedWidth(150)
        self._btns["save"].clicked.connect(self._save)
        row4.addWidget(self._btns["save"])

        self._btns["save_pdf"] = QPushButton()
        self._btns["save_pdf"].setFixedWidth(150)
        self._btns["save_pdf"].clicked.connect(self._save_pdf)
        row4.addWidget(self._btns["save_pdf"])

        self._btns["print"] = QPushButton()
        self._btns["print"].setFixedWidth(130)
        self._btns["print"].clicked.connect(self._print)
        row4.addWidget(self._btns["print"])

        row4.addStretch()

        self._btns["clear_filtered"] = QPushButton()
        self._btns["clear_filtered"].setFixedWidth(190)
        self._btns["clear_filtered"].clicked.connect(self._clear_filtered)
        row4.addWidget(self._btns["clear_filtered"])

        self._btns["clear_all"] = QPushButton()
        self._btns["clear_all"].setFixedWidth(160)
        self._btns["clear_all"].clicked.connect(self._clear_all)
        row4.addWidget(self._btns["clear_all"])

        layout.addLayout(row4)

        self.chart = StatusChart()
        layout.addWidget(self.chart)

        self.out = QPlainTextEdit()
        self.out.setFont(mono)
        self.out.setReadOnly(True)
        layout.addWidget(self.out, 1)

        self.status = QLabel("-")
        self.status.setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.status)

        self._rebuild_time_options()
        self.retranslate()
        self._refresh_all()
        return root

    def _rebuild_time_options(self):
        cur = self.time_filter.currentIndex()
        self.time_filter.blockSignals(True)
        self.time_filter.clear()
        for key, _m in self.TIME_RANGES:
            self.time_filter.addItem(tr(key, module="reporter"))
        if 0 <= cur < self.time_filter.count():
            self.time_filter.setCurrentIndex(cur)
        self.time_filter.blockSignals(False)

    def retranslate(self):
        def t(k, **kw):
            return tr(k, module="reporter", **kw)
        if not hasattr(self, "fmt"):
            return
        self._lbls["format"].setText(t("format"))
        self._lbls["module"].setText(t("module"))
        self._lbls["session"].setText(t("session"))
        self._lbls["time"].setText(t("time"))
        self._lbls["limit"].setText(t("limit"))
        self._lbls["search"].setText(t("search"))
        self.search_input.setPlaceholderText(t("search.placeholder"))
        self.problems_only.setText(t("problems_only"))
        self.problems_only.setToolTip(t("problems_only.tip", ms=self.SLOW_THRESHOLD_MS))
        self.group_by_url.setText(t("group_by_url"))
        self._btns["refresh"].setText(t("refresh"))
        self._btns["generate"].setText(t("generate"))
        self._btns["save"].setText(t("save"))
        self._btns["save_pdf"].setText(t("save_pdf"))
        self._btns["print"].setText(t("print"))
        self._btns["snapshot"].setText(t("snapshot"))
        self._btns["compare"].setText(t("compare"))
        self._btns["clear_filtered"].setText(t("clear_filtered"))
        self._btns["clear_filtered"].setToolTip(t("clear_filtered.tip"))
        self._btns["clear_all"].setText(t("clear_all"))
        self._btns["clear_all"].setToolTip(t("clear_all.tip"))

        self._rebuild_time_options()

        for i in range(self.module_filter.count()):
            if self.module_filter.itemText(i) == "All":
                self.module_filter.setItemText(i, t("all"))
                break
        for i in range(self.session_filter.count()):
            if self.session_filter.itemText(i) == "All":
                self.session_filter.setItemText(i, t("all"))
                break

    def _on_module_changed(self):
        self._refresh_sessions()
        self._update_count()

    def _selected_module(self):
        m = self.module_filter.currentText()
        if m in ("", "All", tr("all", module="reporter")):
            return None
        return m

    def _selected_session(self):
        s = self.session_filter.currentText()
        if s in ("", "All", tr("all", module="reporter")):
            return None
        return s

    def _selected_since(self):
        idx = self.time_filter.currentIndex()
        if 0 <= idx < len(self.TIME_RANGES):
            key, minutes = self.TIME_RANGES[idx]
            if minutes is not None:
                dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)
                return dt.replace(tzinfo=None).isoformat()
        return None

    def _refresh_all(self):
        self._refresh_modules()
        self._refresh_sessions()
        self._update_count()

    def _refresh_modules(self):
        current = self.module_filter.currentText()
        self.module_filter.blockSignals(True)
        self.module_filter.clear()
        self.module_filter.addItem(tr("all", module="reporter"))
        try:
            for name in get_history().modules():
                self.module_filter.addItem(name)
        except Exception:
            pass
        idx = self.module_filter.findText(current)
        if idx >= 0:
            self.module_filter.setCurrentIndex(idx)
        self.module_filter.blockSignals(False)

    def _refresh_sessions(self):
        current = self.session_filter.currentText()
        self.session_filter.blockSignals(True)
        self.session_filter.clear()
        self.session_filter.addItem(tr("all", module="reporter"))
        try:
            module = self._selected_module()
            for sid in get_history().sessions(module=module):
                self.session_filter.addItem(sid)
        except Exception:
            pass
        idx = self.session_filter.findText(current)
        if idx >= 0:
            self.session_filter.setCurrentIndex(idx)
        self.session_filter.blockSignals(False)

    def _fetch(self):
        module = self._selected_module()
        session = self._selected_session()
        since = self._selected_since()
        limit = self.limit.value()

        rows = get_history().list(
            module=module, session=session, limit=limit, since=since)
        records = []
        for r in rows:
            rec = get_history().get(r["id"])
            if rec:
                records.append(rec)

        if self.problems_only.isChecked():
            records = [r for r in records if self._is_problem(r)]

        search = self.search_input.text().strip().lower()
        if search:
            records = [r for r in records if self._matches_search(r, search)]

        return records

    def _is_problem(self, rec):
        if rec.get("error"):
            return True
        status = rec.get("status")
        if status is not None and status >= 400:
            return True
        elapsed = rec.get("elapsed") or 0
        if elapsed * 1000 > self.SLOW_THRESHOLD_MS:
            return True
        return False

    def _matches_search(self, rec, needle):
        haystack = " ".join(str(rec.get(k) or "") for k in
                            ("url", "response_body", "error", "body")).lower()
        return needle in haystack

    def _update_count(self):
        if not hasattr(self, "count_label"):
            return
        try:
            records = self._fetch()
            self.count_label.setText(
                tr("count", module="reporter", count=len(records)))
            self.chart.set_data(self._buckets(records))
        except Exception as e:
            self.count_label.setText(f"records: error ({e})")

    def _buckets(self, records):
        out = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "ERR": 0}
        for r in records:
            s = r.get("status")
            if s is None:
                out["ERR"] += 1
            elif 200 <= s < 300:
                out["2xx"] += 1
            elif 300 <= s < 400:
                out["3xx"] += 1
            elif 400 <= s < 500:
                out["4xx"] += 1
            elif s >= 500:
                out["5xx"] += 1
        return out

    def _generate(self):
        records = self._fetch()
        fmt = self.fmt.currentText()

        self.chart.set_data(self._buckets(records))

        if not records:
            self.out.setPlainText("(empty)")
            self.status.setText(tr("status.empty", module="reporter"))
            return

        if self.group_by_url.isChecked():
            text = self._render_grouped(records, fmt)
        else:
            text = self._render_full(records, fmt)

        self.out.setPlainText(text)
        suffix = tr("status.grouped", module="reporter") if self.group_by_url.isChecked() else ""
        self.status.setText(tr("status.done", module="reporter",
                               count=len(records), format=fmt, suffix=suffix))

    def _render_full(self, records, fmt):
        if fmt == "Markdown":
            return self._render_md(records)
        if fmt == "HTML":
            return self._render_html(records)
        if fmt == "JSON":
            return self._render_json(records)
        return self._render_plain(records)

    def _render_grouped(self, records, fmt):
        groups = {}
        for r in records:
            key = (r.get("method") or "?", r.get("url") or "?")
            groups.setdefault(key, []).append(r)
        if fmt == "Markdown":
            return self._render_grouped_md(groups)
        if fmt == "HTML":
            return self._render_grouped_html(groups)
        if fmt == "JSON":
            return self._render_grouped_json(groups)
        return self._render_grouped_plain(groups)

    def _render_md(self, records):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "# " + tr("title", module="reporter"),
            "",
            tr("generated", module="reporter", datetime=now_str),
            tr("records", module="reporter", count=len(records)),
            "",
        ]
        lines.extend(self._summary_md(records))
        lines.extend([
            "",
            "| # | Module | Session | Method | URL | Status | Time |",
            "|---|---|---|---|---|---|---|",
        ])
        for i, r in enumerate(records, 1):
            status = r.get("status") if r.get("status") is not None else (
                "ERR" if r.get("error") else "-")
            time_s = f"{r['elapsed']*1000:.0f} ms" if r.get("elapsed") else "-"
            url = (r.get("url") or "").replace("|", "\\|")
            lines.append(
                f"| {i} | {r.get('module') or '-'} | {r.get('session') or '-'} "
                f"| {r.get('method') or '-'} | {url} | {status} | {time_s} |"
            )
        lines.append(f"\n## {tr('details', module='reporter')}\n")
        for i, r in enumerate(records, 1):
            lines.append(
                f"### {i}. [{r.get('module')}] {r.get('method')} {r.get('url')}")
            if r.get("session"):
                lines.append(f"- **Session:** `{r['session']}`")
            if r.get("error"):
                lines.append(f"- **Error:** {r['error']}")
            body = r.get("response_body") or ""
            if body:
                lines.append("```")
                lines.append(body[:800])
                lines.append("```")
            lines.append("")
        return "\n".join(lines)

    def _render_plain(self, records):
        lines = [f"REPORT  ({len(records)} records)"]
        lines.extend(self._summary_md(records))
        lines.append("")
        for i, r in enumerate(records, 1):
            status = r.get("status") if r.get("status") is not None else (
                "ERR" if r.get("error") else "-")
            time_s = f"{r['elapsed']*1000:.0f}ms" if r.get("elapsed") else "-"
            sid = f"  [{r['session']}]" if r.get("session") else ""
            lines.append(
                f"{i:>4}. [{r.get('module') or '-'}]{sid}  "
                f"{r.get('method'):<7} {status:<5} {time_s:>8}  {r.get('url')}"
            )
        return "\n".join(lines)

    def _render_json(self, records):
        compact = []
        for r in records:
            compact.append({
                "id": r.get("id"),
                "module": r.get("module"),
                "session": r.get("session"),
                "method": r.get("method"),
                "url": r.get("url"),
                "status": r.get("status"),
                "elapsed_ms": int(r["elapsed"] * 1000) if r.get("elapsed") else None,
                "error": r.get("error"),
                "created_at": r.get("created_at"),
                "response_preview": (r.get("response_body") or "")[:300],
            })
        return json.dumps(compact, ensure_ascii=False, indent=2)

    def _render_html(self, records):
        bg = _c("bg2")
        fg = _c("fg")
        border = _c("border")
        th_bg = _c("bg3")
        code_bg = _c("bg")
        accent = _c("fg_soft")
        parts = [
            "<!doctype html><html><head><meta charset='utf-8'>",
            "<title>Report</title>",
            f"<style>body{{font-family:Consolas,monospace;background:{bg};"
            f"color:{fg};padding:20px;}}"
            f"table{{border-collapse:collapse;width:100%}}"
            f"td,th{{border:1px solid {border};padding:6px}}"
            f"th{{background:{th_bg};color:{accent};}}"
            f"code{{background:{code_bg};padding:2px 4px;display:block;"
            f"white-space:pre-wrap;border:1px solid {border};}}"
            f"a{{color:{accent}}}</style>",
            "</head><body>",
            f"<h1>{tr('title', module='reporter')}</h1>",
            f"<p>{tr('generated', module='reporter', datetime=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}"
            f" · {tr('records', module='reporter', count=len(records))}</p>",
        ]
        summary = self._summary_md(records)
        parts.append("<ul>")
        for line in summary[1:]:
            parts.append(f"<li>{line.lstrip('- ')}</li>")
        parts.append("</ul>")
        parts.append(
            "<table><tr><th>#</th><th>Module</th><th>Session</th>"
            "<th>Method</th><th>URL</th><th>Status</th><th>Time</th></tr>"
        )
        for i, r in enumerate(records, 1):
            status = r.get("status") if r.get("status") is not None else (
                "ERR" if r.get("error") else "-")
            time_s = f"{r['elapsed']*1000:.0f} ms" if r.get("elapsed") else "-"
            parts.append(
                f"<tr><td>{i}</td><td>{r.get('module') or '-'}</td>"
                f"<td>{r.get('session') or '-'}</td>"
                f"<td>{r.get('method') or '-'}</td>"
                f"<td>{r.get('url') or ''}</td>"
                f"<td>{status}</td><td>{time_s}</td></tr>"
            )
        parts.append("</table>")
        for i, r in enumerate(records, 1):
            parts.append(
                f"<h3>{i}. [{r.get('module')}] {r.get('method')} {r.get('url')}</h3>")
            if r.get("error"):
                parts.append(f"<p><b>Error:</b> {r['error']}</p>")
            if r.get("response_body"):
                parts.append(f"<code>{r['response_body'][:1000]}</code>")
        parts.append("</body></html>")
        return "\n".join(parts)

    def _render_grouped_md(self, groups):
        lines = [
            "# " + tr("title.grouped", module="reporter"),
            "",
            tr("generated", module="reporter", datetime=datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            tr("unique", module="reporter", count=len(groups)),
            "",
            "| Count | Method | URL | Statuses | Avg ms | Max ms |",
            "|---|---|---|---|---|---|",
        ]
        for (method, url), items in sorted(groups.items(), key=lambda x: -len(x[1])):
            statuses = {}
            times = []
            for r in items:
                s = str(r.get("status") if r.get("status") is not None else "ERR")
                statuses[s] = statuses.get(s, 0) + 1
                if r.get("elapsed"):
                    times.append(r["elapsed"] * 1000)
            avg = f"{sum(times)/len(times):.0f}" if times else "-"
            mx = f"{max(times):.0f}" if times else "-"
            status_str = ", ".join(f"{k}×{v}" for k, v in sorted(statuses.items()))
            safe_url = url.replace("|", "\\|")
            lines.append(
                f"| {len(items)} | {method} | {safe_url} | {status_str} | {avg} | {mx} |"
            )
        return "\n".join(lines)

    def _render_grouped_plain(self, groups):
        lines = [f"GROUPED BY URL  ({len(groups)} unique)"]
        for (method, url), items in sorted(groups.items(), key=lambda x: -len(x[1])):
            lines.append(f"  {len(items):>4}  {method:<7} {url}")
        return "\n".join(lines)

    def _render_grouped_json(self, groups):
        out = []
        for (method, url), items in sorted(groups.items(), key=lambda x: -len(x[1])):
            statuses = {}
            times = []
            for r in items:
                s = r.get("status") if r.get("status") is not None else "ERR"
                statuses[str(s)] = statuses.get(str(s), 0) + 1
                if r.get("elapsed"):
                    times.append(r["elapsed"] * 1000)
            out.append({
                "method": method,
                "url": url,
                "count": len(items),
                "statuses": statuses,
                "avg_ms": int(sum(times) / len(times)) if times else None,
                "max_ms": int(max(times)) if times else None,
            })
        return json.dumps(out, ensure_ascii=False, indent=2)

    def _render_grouped_html(self, groups):
        bg = _c("bg2")
        fg = _c("fg")
        border = _c("border")
        th_bg = _c("bg3")
        accent = _c("fg_soft")
        parts = [
            "<!doctype html><html><head><meta charset='utf-8'>",
            "<title>Report</title>",
            f"<style>body{{font-family:Consolas,monospace;background:{bg};"
            f"color:{fg};padding:20px;}}"
            f"table{{border-collapse:collapse;width:100%}}"
            f"td,th{{border:1px solid {border};padding:6px}}"
            f"th{{background:{th_bg};color:{accent};}}</style>",
            "</head><body>",
            f"<h1>{tr('title.grouped', module='reporter')}</h1>",
            f"<p>{tr('generated', module='reporter', datetime=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}"
            f" · {tr('unique', module='reporter', count=len(groups))}</p>",
            "<table><tr><th>Count</th><th>Method</th><th>URL</th>"
            "<th>Statuses</th><th>Avg ms</th><th>Max ms</th></tr>",
        ]
        for (method, url), items in sorted(groups.items(), key=lambda x: -len(x[1])):
            statuses = {}
            times = []
            for r in items:
                s = str(r.get("status") if r.get("status") is not None else "ERR")
                statuses[s] = statuses.get(s, 0) + 1
                if r.get("elapsed"):
                    times.append(r["elapsed"] * 1000)
            avg = f"{sum(times)/len(times):.0f}" if times else "-"
            mx = f"{max(times):.0f}" if times else "-"
            status_str = ", ".join(f"{k}×{v}" for k, v in sorted(statuses.items()))
            parts.append(
                f"<tr><td>{len(items)}</td><td>{method}</td>"
                f"<td>{url}</td><td>{status_str}</td><td>{avg}</td><td>{mx}</td></tr>"
            )
        parts.append("</table></body></html>")
        return "\n".join(parts)

    def _summary_md(self, records):
        b = self._buckets(records)
        times = [r["elapsed"] * 1000 for r in records if r.get("elapsed")]
        lines = ["## " + tr("summary", module="reporter")]
        lines.append(f"- 2xx: {b['2xx']}")
        lines.append(f"- 3xx: {b['3xx']}")
        lines.append(f"- 4xx: {b['4xx']}")
        lines.append(f"- 5xx: {b['5xx']}")
        lines.append(f"- ERR: {b['ERR']}")
        if times:
            lines.append(f"- avg: {sum(times)/len(times):.0f} ms")
            lines.append(f"- max: {max(times):.0f} ms")
        return lines

    def _save(self):
        text = self.out.toPlainText()
        if not text:
            return
        fmt = self.fmt.currentText()
        ext = {"Markdown": "md", "HTML": "html",
               "JSON": "json", "Plain text": "txt"}.get(fmt, "txt")
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("save.dialog", module="reporter"),
            f"report.{ext}", f"{fmt} (*.{ext})")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.status.setText(tr("status.saved", module="reporter", path=path))

    def _save_pdf(self):
        text = self.out.toPlainText()
        if not text:
            return
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("save.pdf.title", module="reporter"),
            "report.pdf", "PDF (*.pdf)")
        if not path:
            return
        try:
            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(path)
            doc = QTextDocument()
            fmt = self.fmt.currentText()
            if fmt == "HTML":
                doc.setHtml(text)
            else:
                doc.setPlainText(text)
            doc.setDefaultFont(QFont("Consolas", 9))
            doc.print_(printer)
            self.status.setText(tr("status.pdf_saved", module="reporter", path=path))
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="reporter"), str(e))

    def _print(self):
        text = self.out.toPlainText()
        if not text:
            return
        try:
            printer = QPrinter(QPrinter.HighResolution)
            dlg = QPrintDialog(printer, self._widget)
            if dlg.exec() != QPrintDialog.Accepted:
                return
            doc = QTextDocument()
            fmt = self.fmt.currentText()
            if fmt == "HTML":
                doc.setHtml(text)
            else:
                doc.setPlainText(text)
            doc.setDefaultFont(QFont("Consolas", 9))
            doc.print_(printer)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="reporter"), str(e))

    def _current_filters(self):
        return {
            "module": self._selected_module(),
            "session": self._selected_session(),
            "since": self._selected_since(),
            "limit": self.limit.value(),
        }

    def _save_snapshot(self):
        name, ok = QInputDialog.getText(
            parent_widget(self._widget),
            tr("snapshot.title", module="reporter"),
            tr("snapshot.prompt", module="reporter"))
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            snap = self._svc_snapshot(name, self._current_filters())
            path = SNAPSHOTS_DIR / f"{name}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=2)
            self.status.setText(
                tr("snapshot.saved", module="reporter",
                   count=len(snap["records"]), path=str(path)))
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="reporter"), str(e))

    def _compare_snapshots(self):
        if not SNAPSHOTS_DIR.exists():
            QMessageBox.information(parent_widget(self._widget),
                                    tr("compare.title", module="reporter"),
                                    tr("compare.no_snapshots", module="reporter"))
            return
        names = sorted(p.stem for p in SNAPSHOTS_DIR.iterdir()
                       if p.suffix == ".json")
        if len(names) < 2:
            QMessageBox.information(parent_widget(self._widget),
                                    tr("compare.title", module="reporter"),
                                    tr("compare.need_two", module="reporter"))
            return

        dlg = QDialog(parent_widget(self._widget))
        dlg.setWindowTitle(tr("compare.title", module="reporter"))
        dlg.setMinimumSize(560, 340)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(tr("compare.pick_a", module="reporter")))
        a_list = QListWidget()
        for n in names:
            a_list.addItem(QListWidgetItem(n))
        a_list.setCurrentRow(len(names) - 2)
        lay.addWidget(a_list)
        lay.addWidget(QLabel(tr("compare.pick_b", module="reporter")))
        b_list = QListWidget()
        for n in names:
            b_list.addItem(QListWidgetItem(n))
        b_list.setCurrentRow(len(names) - 1)
        lay.addWidget(b_list)
        btns = QHBoxLayout()
        btns.addStretch()
        ok_btn = QPushButton(tr("compare.run", module="reporter"))
        ok_btn.setFixedWidth(140)
        ok_btn.clicked.connect(dlg.accept)
        btns.addWidget(ok_btn)
        cancel = QPushButton(tr("compare.cancel", module="reporter"))
        cancel.setFixedWidth(120)
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        if dlg.exec() != QDialog.Accepted:
            return

        a_name = a_list.currentItem().text() if a_list.currentItem() else None
        b_name = b_list.currentItem().text() if b_list.currentItem() else None
        if not a_name or not b_name:
            return
        try:
            with open(SNAPSHOTS_DIR / f"{a_name}.json", "r", encoding="utf-8") as f:
                a = json.load(f)
            with open(SNAPSHOTS_DIR / f"{b_name}.json", "r", encoding="utf-8") as f:
                b = json.load(f)
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="reporter"), str(e))
            return

        diff = self._svc_compare(a, b)
        lines = [
            f"A: {a_name}  ({a['created_at']})  records={len(a['records'])}",
            f"B: {b_name}  ({b['created_at']})  records={len(b['records'])}",
            f"common: {diff['common']}",
            "",
            f"--- only in A ({len(diff['only_in_a'])}) ---",
        ]
        for r in diff["only_in_a"][:50]:
            lines.append(f"  {r.get('method')} {r.get('url')} -> {r.get('status')}")
        lines.append("")
        lines.append(f"+++ only in B ({len(diff['only_in_b'])}) +++")
        for r in diff["only_in_b"][:50]:
            lines.append(f"  {r.get('method')} {r.get('url')} -> {r.get('status')}")
        lines.append("")
        lines.append(f"~~~ slower in B ({len(diff['slower'])}) ~~~")
        for s in diff["slower"][:30]:
            lines.append(f"  {s['key'][0]} {s['key'][1]}  {s['before']}ms -> {s['after']}ms")
        lines.append("")
        lines.append(f">>> faster in B ({len(diff['faster'])}) <<<")
        for s in diff["faster"][:30]:
            lines.append(f"  {s['key'][0]} {s['key'][1]}  {s['before']}ms -> {s['after']}ms")

        self.out.setPlainText("\n".join(lines))
        self.status.setText(tr("compare.done", module="reporter",
                                a=a_name, b=b_name))

    def _clear_filtered(self):
        module = self._selected_module()
        session = self._selected_session()
        since = self._selected_since()

        if not module and not session and not since:
            QMessageBox.information(
                parent_widget(self._widget),
                tr("msg.filter_empty.title", module="reporter"),
                tr("msg.filter_empty.text", module="reporter"))
            return

        try:
            rows = get_history().list(module=module, session=session,
                                      limit=1000000, since=since)
            ids = [r["id"] for r in rows]
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("msg.error.title", module="reporter"), str(e))
            return

        if not ids:
            QMessageBox.information(parent_widget(self._widget),
                                    tr("msg.empty.title", module="reporter"),
                                    tr("msg.empty.text", module="reporter"))
            return

        if QMessageBox.question(
            parent_widget(self._widget),
            tr("msg.confirm_filtered.title", module="reporter"),
            tr("msg.confirm_filtered.text", module="reporter", count=len(ids))
        ) != QMessageBox.Yes:
            return

        get_history().delete_ids(ids)
        self.out.clear()
        self.status.setText(
            tr("status.cleared_filtered", module="reporter", count=len(ids)))
        self._refresh_all()

    def _clear_all(self):
        n = get_history().count()
        if n == 0:
            QMessageBox.information(parent_widget(self._widget),
                                    tr("msg.empty.title", module="reporter"),
                                    tr("msg.empty.text", module="reporter"))
            return
        if QMessageBox.question(
            parent_widget(self._widget),
            tr("msg.confirm_all.title", module="reporter"),
            tr("msg.confirm_all.text", module="reporter", count=n)
        ) != QMessageBox.Yes:
            return
        get_history().clear()
        self.out.clear()
        self.status.setText(
            tr("status.cleared_all", module="reporter", count=n))
        self._refresh_all()