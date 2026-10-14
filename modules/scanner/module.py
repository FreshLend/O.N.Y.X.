import json
import os
import random
import string
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSpinBox, QProgressBar, QMessageBox, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QCheckBox, QComboBox, QGroupBox, QFormLayout
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.i18n import tr
from core.utils import color as _c, parent_widget
from styles import loader as theme_loader


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
]

EXTENSION_PRESETS = {
    "none": [""],
    "web": ["", ".php", ".html", ".htm", ".asp", ".aspx", ".jsp", ".js"],
    "backup": [".bak", ".old", ".orig", ".save", ".swp", "~", ".backup", ".tmp"],
    "config": [".env", ".git/config", ".git/HEAD", ".svn/entries", ".htaccess", ".htpasswd", ".yml", ".yaml", ".ini", ".conf"],
    "archives": [".zip", ".tar", ".tar.gz", ".tgz", ".rar", ".7z", ".gz"],
    "full": ["", ".php", ".html", ".htm", ".asp", ".aspx", ".jsp", ".bak", ".old", ".save", ".tmp", ".swp",
             ".env", ".git/config", ".htaccess", ".zip", ".tar.gz", ".txt", ".json", ".xml", ".yaml"],
}

DEFAULT_STATUS_CODES = "200,201,204,301,302,307,308,401,403,405"


def _parse_codes(text):
    out = []
    for p in text.replace(";", ",").split(","):
        p = p.strip()
        if p.isdigit():
            out.append(int(p))
    return out or [200]


class ScanWorker(QThread):
    progress = Signal(int, int)
    found = Signal(dict)
    baseline_ready = Signal(list)
    finished_scan = Signal(list)
    log_msg = Signal(str)

    def __init__(self, base_url, words, threads, timeout, extensions,
                 custom_codes, wildcard_filter, wildcard_tolerance,
                 proxies_list, delay_ms, ua_rotate, follow_redirects):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.words = words
        self.threads = max(1, threads)
        self.timeout = timeout
        self.extensions = extensions or [""]
        self.custom_codes = custom_codes
        self.wildcard_filter = wildcard_filter
        self.wildcard_tolerance = wildcard_tolerance
        self.proxies_list = proxies_list or []
        self.delay_ms = delay_ms
        self.ua_rotate = ua_rotate
        self.follow_redirects = follow_redirects
        self._stop = False
        self._baselines = []

    def stop(self):
        self._stop = True

    def _pick_proxy(self):
        if not self.proxies_list:
            return None
        return random.choice(self.proxies_list)

    def _pick_ua(self):
        return random.choice(USER_AGENTS) if self.ua_rotate else USER_AGENTS[0]

    def _request(self, url):
        headers = {"User-Agent": self._pick_ua()}
        proxy = self._pick_proxy()
        try:
            kwargs = dict(headers=headers, body="", timeout=self.timeout,
                          allow_redirects=self.follow_redirects)
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}
            return get_bus().call("http.request", "GET", url, **kwargs)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _build_baseline(self):
        self.log_msg.emit("[baseline] probing random paths...")
        probes = ["".join(random.choices(string.ascii_lowercase + string.digits, k=18)) for _ in range(3)]
        for p in probes:
            if self._stop:
                break
            r = self._request(f"{self.base_url}/{p}")
            if r and r.get("ok"):
                self._baselines.append({
                    "status": r.get("status"),
                    "length": len(r.get("body") or ""),
                })
        self.baseline_ready.emit(self._baselines)
        for b in self._baselines:
            self.log_msg.emit(f"[baseline] status={b['status']} len={b['length']}")

    def _matches_baseline(self, status, length):
        for b in self._baselines:
            if b["status"] != status:
                continue
            base_len = b["length"]
            if base_len <= 0:
                continue
            diff = abs(length - base_len) / base_len
            if diff <= self.wildcard_tolerance:
                return True
        return False

    def _scan_one(self, word):
        if self._stop:
            return []
        out = []
        for ext in self.extensions:
            if self._stop:
                break
            url = f"{self.base_url}/{word.lstrip('/')}{ext}"
            if self.delay_ms > 0:
                time.sleep(self.delay_ms / 1000.0)
            r = self._request(url)
            if not r or not r.get("ok"):
                continue
            status = r.get("status")
            if status == 429:
                self.log_msg.emit(f"[!] 429 on {url} — backoff 2s")
                time.sleep(2.0)
                continue
            if status not in self.custom_codes:
                continue
            length = len(r.get("body") or "")
            if self.wildcard_filter and self._matches_baseline(status, length):
                continue
            out.append({
                "status": status,
                "url": url,
                "size": length,
                "ms": int((r.get("elapsed") or 0) * 1000),
                "word": word,
                "ext": ext,
            })
        return out

    def run(self):
        if self.wildcard_filter:
            self._build_baseline()

        total = len(self.words)
        results = []
        done = 0

        with ThreadPoolExecutor(max_workers=self.threads) as ex:
            futures = {ex.submit(self._scan_one, w): w for w in self.words}
            for fut in as_completed(futures):
                if self._stop:
                    for f in futures:
                        f.cancel()
                    break
                done += 1
                self.progress.emit(done, total)
                try:
                    res = fut.result()
                except Exception:
                    res = []
                for r in res:
                    results.append(r)
                    self.found.emit(r)

        results.sort(key=lambda x: (x["status"], x["url"]))
        self.finished_scan.emit(results)


class ScannerModule(BaseModule):
    def on_load(self, api):
        api.register_service(self.name, "scanner.scan", self._svc_scan,
                             "scanner.scan(base_url, words, threads=10, timeout=10, "
                             "extensions=None, status_codes=None, wildcard_filter=True, "
                             "proxies=None, caller=None, session=None) -> list")

    def _svc_scan(self, base_url, words, threads=10, timeout=10,
                  extensions=None, status_codes=None, wildcard_filter=True,
                  proxies=None, caller=None, session=None):
        if isinstance(words, str):
            if os.path.exists(words):
                with open(words, "r", encoding="utf-8", errors="ignore") as f:
                    words = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            else:
                words = [w.strip() for w in words.splitlines() if w.strip()]
        if not words:
            return []

        ext_list = extensions or [""]
        codes = status_codes or [200, 201, 204, 301, 302, 307, 308, 401, 403, 405]
        base_url = base_url.rstrip("/")
        bus = get_bus()

        def probe(url):
            try:
                kwargs = dict(headers={"User-Agent": USER_AGENTS[0]}, body="",
                              timeout=timeout, allow_redirects=False)
                if caller:
                    kwargs["caller"] = caller
                if session:
                    kwargs["session"] = session
                return bus.call("http.request", "GET", url, **kwargs)
            except Exception:
                return {"ok": False}

        baselines = []
        if wildcard_filter:
            for _ in range(3):
                rp = "".join(random.choices(string.ascii_lowercase + string.digits, k=18))
                r = probe(f"{base_url}/{rp}")
                if r.get("ok"):
                    baselines.append({"status": r.get("status"),
                                      "length": len(r.get("body") or "")})

        def matches(status, length):
            for b in baselines:
                if b["status"] != status or b["length"] <= 0:
                    continue
                if abs(length - b["length"]) / b["length"] <= 0.05:
                    return True
            return False

        def one(word):
            hits = []
            for ext in ext_list:
                url = f"{base_url}/{word.lstrip('/')}{ext}"
                try:
                    kwargs = dict(headers={"User-Agent": USER_AGENTS[0]}, body="",
                                  timeout=timeout, allow_redirects=False)
                    if proxies:
                        kwargs["proxies"] = proxies
                    if caller:
                        kwargs["caller"] = caller
                    if session:
                        kwargs["session"] = session
                    r = bus.call("http.request", "GET", url, **kwargs)
                except Exception:
                    continue
                if not r.get("ok"):
                    continue
                st = r["status"]
                if st not in codes:
                    continue
                length = len(r.get("body") or "")
                if wildcard_filter and matches(st, length):
                    continue
                hits.append({"status": st, "url": url, "size": length,
                             "ms": int((r.get("elapsed") or 0) * 1000)})
            return hits

        out = []
        with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
            for fut in as_completed([ex.submit(one, w) for w in words]):
                try:
                    out.extend(fut.result())
                except Exception:
                    continue
        return out

    def __init__(self):
        self._widget = None
        self._results = []
        self.worker = None
        self._lbls = {}
        self._btns = {}
        self._active_wordlist_words = []

    def on_shutdown(self):
        if self.worker is not None:
            try:
                self.worker.stop()
                self.worker.wait(3000)
            except Exception:
                pass
            self.worker = None

    def create_widget(self) -> QWidget:
        root = QWidget()
        self._widget = root
        self._results = []

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        mono = QFont("Consolas", 10)

        row = QHBoxLayout()
        self._lbls["base_url"] = QLabel()
        self._lbls["base_url"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self._lbls["base_url"])
        self.url_input = QLineEdit("https://example.com")
        self.url_input.setFont(mono)
        row.addWidget(self.url_input, 2)

        self._lbls["wordlist"] = QLabel()
        self._lbls["wordlist"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self._lbls["wordlist"])
        self.wordlist_input = QLineEdit()
        self.wordlist_input.setFont(mono)
        row.addWidget(self.wordlist_input, 2)

        self._btns["browse"] = QPushButton()
        self._btns["browse"].setFixedWidth(120)
        self._btns["browse"].clicked.connect(self._pick_wordlist)
        row.addWidget(self._btns["browse"])
        layout.addLayout(row)

        opts = QGroupBox()
        self._opts_group = opts
        form = QFormLayout(opts)

        row2 = QHBoxLayout()
        self._lbls["threads"] = QLabel()
        self._lbls["threads"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row2.addWidget(self._lbls["threads"])
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 100)
        self.threads_spin.setValue(15)
        self.threads_spin.setFixedWidth(70)
        row2.addWidget(self.threads_spin)

        self._lbls["timeout"] = QLabel()
        self._lbls["timeout"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row2.addWidget(self._lbls["timeout"])
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 60)
        self.timeout_spin.setValue(10)
        self.timeout_spin.setFixedWidth(70)
        row2.addWidget(self.timeout_spin)

        self._lbls["delay"] = QLabel()
        self._lbls["delay"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row2.addWidget(self._lbls["delay"])
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 5000)
        self.delay_spin.setValue(0)
        self.delay_spin.setFixedWidth(80)
        row2.addWidget(self.delay_spin)

        self._lbls["extensions"] = QLabel()
        self._lbls["extensions"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row2.addWidget(self._lbls["extensions"])
        self.ext_box = QComboBox()
        for k in EXTENSION_PRESETS:
            self.ext_box.addItem(k)
        self.ext_box.setCurrentText("web")
        self.ext_box.setFixedWidth(120)
        row2.addWidget(self.ext_box)
        row2.addStretch()
        form.addRow(row2)

        row3 = QHBoxLayout()
        self._lbls["codes"] = QLabel()
        self._lbls["codes"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row3.addWidget(self._lbls["codes"])
        self.codes_input = QLineEdit(DEFAULT_STATUS_CODES)
        self.codes_input.setFont(mono)
        row3.addWidget(self.codes_input, 1)

        self.wildcard_chk = QCheckBox()
        self.wildcard_chk.setChecked(True)
        row3.addWidget(self.wildcard_chk)

        self.ua_rotate_chk = QCheckBox()
        self.ua_rotate_chk.setChecked(True)
        row3.addWidget(self.ua_rotate_chk)

        self.redirect_chk = QCheckBox()
        self.redirect_chk.setChecked(False)
        row3.addWidget(self.redirect_chk)

        self.use_proxy_chk = QCheckBox()
        self.use_proxy_chk.setChecked(False)
        row3.addWidget(self.use_proxy_chk)
        row3.addStretch()
        form.addRow(row3)

        layout.addWidget(opts)

        row4 = QHBoxLayout()
        self.run_btn = QPushButton()
        self.run_btn.setIcon(theme_loader.get_icon(icon_name="play", emoji=">", size=14))
        self.run_btn.setFixedWidth(180)
        self.run_btn.clicked.connect(self._start)
        row4.addWidget(self.run_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setFixedWidth(140)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        row4.addWidget(self.stop_btn)

        self.save_btn = QPushButton()
        self.save_btn.setFixedWidth(180)
        self.save_btn.clicked.connect(self._save_results)
        row4.addWidget(self.save_btn)

        row4.addStretch()
        layout.addLayout(row4)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table, 1)

        self.log = QPlainTextEdit()
        self.log.setFont(mono)
        self.log.setReadOnly(True)
        self.log.setFixedHeight(110)
        layout.addWidget(self.log)

        self.retranslate()
        return root

    def retranslate(self):
        def t(k, **kw):
            return tr(k, module="scanner", **kw)
        if not hasattr(self, "url_input"):
            return
        self._lbls["base_url"].setText(t("base_url"))
        self._lbls["wordlist"].setText(t("wordlist"))
        self.wordlist_input.setPlaceholderText(t("wordlist.placeholder"))
        self._btns["browse"].setText(t("browse"))
        self._lbls["threads"].setText(t("threads"))
        self._lbls["timeout"].setText(t("timeout"))
        self._lbls["delay"].setText(t("delay"))
        self._lbls["extensions"].setText(t("extensions"))
        self._lbls["codes"].setText(t("codes"))
        self.wildcard_chk.setText(t("wildcard"))
        self.wildcard_chk.setToolTip(t("wildcard.tip"))
        self.ua_rotate_chk.setText(t("ua_rotate"))
        self.redirect_chk.setText(t("follow_redirects"))
        self.use_proxy_chk.setText(t("use_proxy"))
        self.run_btn.setText(t("start"))
        self.stop_btn.setText(t("stop"))
        self.save_btn.setText(t("save_results"))
        self.table.setHorizontalHeaderLabels(
            [t("col.status"), t("col.url"), t("col.size"), t("col.time")])

    def _pick_wordlist(self):
        path, _ = QFileDialog.getOpenFileName(
            parent_widget(self._widget),
            tr("dialog.title", module="scanner"), "",
            tr("dialog.filter", module="scanner"))
        if path:
            self.wordlist_input.setText(path)

    def _load_proxies(self):
        if not self.use_proxy_chk.isChecked():
            return []
        try:
            from core.settings import get_settings
            s = get_settings()
            p = s.get("proxy", "http", default="") or s.get("proxy", "socks5", default="")
            return [p] if p else []
        except Exception:
            return []

    def _start(self):
        url = self.url_input.text().strip()
        wl = self.wordlist_input.text().strip()
        if not url:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="scanner"),
                                tr("err.no_url", module="scanner"))
            return
        if not wl or not os.path.exists(wl):
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="scanner"),
                                tr("err.no_wordlist", module="scanner"))
            return

        with open(wl, "r", encoding="utf-8", errors="ignore") as f:
            words = [line.strip() for line in f
                     if line.strip() and not line.startswith("#")]
        if not words:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="scanner"),
                                tr("err.empty_wordlist", module="scanner"))
            return

        if self.worker is not None and self.worker.isRunning():
            return

        self.table.setRowCount(0)
        self._results = []
        self.log.clear()
        self._active_wordlist_words = words
        self.log.appendPlainText(tr("log.loaded", module="scanner", count=len(words)))

        extensions = EXTENSION_PRESETS.get(self.ext_box.currentText(), [""])
        codes = _parse_codes(self.codes_input.text())
        proxies = self._load_proxies()

        self.progress.setRange(0, len(words))
        self.progress.setValue(0)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self.worker = ScanWorker(
            base_url=url,
            words=words,
            threads=self.threads_spin.value(),
            timeout=self.timeout_spin.value(),
            extensions=extensions,
            custom_codes=codes,
            wildcard_filter=self.wildcard_chk.isChecked(),
            wildcard_tolerance=0.05,
            proxies_list=proxies,
            delay_ms=self.delay_spin.value(),
            ua_rotate=self.ua_rotate_chk.isChecked(),
            follow_redirects=self.redirect_chk.isChecked(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.found.connect(self._on_found)
        self.worker.baseline_ready.connect(self._on_baseline)
        self.worker.finished_scan.connect(self._on_done)
        self.worker.log_msg.connect(self._on_log)
        self.worker.start()

    def _stop(self):
        if self.worker:
            self.worker.stop()
            self.log.appendPlainText(tr("log.stopping", module="scanner"))

    def _on_progress(self, done, total):
        try:
            self.progress.setValue(done)
            self.progress.setFormat(f"{done} / {total}")
        except RuntimeError:
            return

    def _on_baseline(self, baseline):
        if baseline:
            self.log.appendPlainText(tr("log.baseline", module="scanner",
                                        count=len(baseline)))

    def _on_log(self, msg):
        try:
            self.log.appendPlainText(msg)
        except RuntimeError:
            return

    def _on_found(self, item):
        try:
            row = self.table.rowCount()
            self.table.insertRow(row)
            status_item = QTableWidgetItem(str(item["status"]))
            s = item["status"]
            if 200 <= s < 300:
                status_item.setForeground(QColor(_c("success")))
            elif 300 <= s < 400:
                status_item.setForeground(QColor(_c("warning")))
            else:
                status_item.setForeground(QColor(_c("danger")))
            self.table.setItem(row, 0, status_item)
            self.table.setItem(row, 1, QTableWidgetItem(item["url"]))
            self.table.setItem(row, 2, QTableWidgetItem(str(item["size"])))
            self.table.setItem(row, 3, QTableWidgetItem(f"{item['ms']} ms"))
            self._results.append(item)
        except RuntimeError:
            return

    def _on_done(self, results):
        try:
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.log.appendPlainText(
                tr("log.found", module="scanner", count=len(results)))
        except RuntimeError:
            pass
        try:
            get_bus().call("vault.set", "scanner.results",
                           json.dumps(results, ensure_ascii=False))
        except Exception:
            pass

    def _save_results(self):
        if not self._results:
            return
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("save.title", module="scanner"),
            "scan_results.json", "JSON (*.json);;CSV (*.csv);;Text (*.txt)")
        if not path:
            return
        try:
            if path.endswith(".csv"):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("status,url,size,ms\n")
                    for r in self._results:
                        f.write(f"{r['status']},{r['url']},{r['size']},{r['ms']}\n")
            elif path.endswith(".txt"):
                with open(path, "w", encoding="utf-8") as f:
                    for r in self._results:
                        f.write(f"{r['status']}  {r['url']}  {r['size']}  {r['ms']}ms\n")
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self._results, f, ensure_ascii=False, indent=2)
            self.log.appendPlainText(tr("log.saved", module="scanner", path=path))
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="scanner"), str(e))