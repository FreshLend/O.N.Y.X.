import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QListWidget, QListWidgetItem, QSplitter, QAbstractItemView,
    QComboBox, QMessageBox, QFileDialog, QInputDialog, QTableWidget,
    QTableWidgetItem, QHeaderView
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.settings import get_settings
from core.i18n import tr
from core.utils import color as _c, parent_widget
from styles import loader as theme_loader


STORE = Path(__file__).resolve().parent.parent.parent / "config" / "proxies.json"
MAX_FAILS = 3


class ProxyCheckWorker(QThread):
    one_done = Signal(str, bool, int, str)
    all_done = Signal()

    def __init__(self, proxies, test_url, timeout, workers):
        super().__init__()
        self.proxies = proxies
        self.test_url = test_url
        self.timeout = timeout
        self.workers = max(1, workers)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        bus = get_bus()

        def probe(p):
            if self._stop:
                return p, False, 0, "stopped"
            t0 = time.time()
            try:
                r = bus.call(
                    "http.request", "GET", self.test_url,
                    headers={}, body="", timeout=self.timeout,
                    proxies={"http": p, "https": p},
                    caller="Proxy Manager",
                )
                ms = int((time.time() - t0) * 1000)
                if r.get("ok"):
                    return p, True, ms, f"HTTP {r.get('status')}"
                return p, False, 0, str(r.get("error", ""))[:100]
            except KeyError:
                return p, False, 0, "http.request unavailable"
            except Exception as e:
                return p, False, 0, str(e)[:100]

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = [ex.submit(probe, p) for p in self.proxies]
            for fut in as_completed(futures):
                if self._stop:
                    for f in futures:
                        f.cancel()
                    break
                try:
                    p, ok, ms, note = fut.result()
                    self.one_done.emit(p, ok, ms, note)
                except Exception:
                    continue
        self.all_done.emit()


class ProxyManagerModule(BaseModule):
    STORE = STORE

    def on_load(self, api):
        api.register_service(self.name, "proxy.check", self._svc_check,
                             "proxy.check(proxy_url, test_url='https://api.ipify.org', "
                             "timeout=10) -> dict")
        api.register_service(self.name, "proxy.get_active", self._svc_get_active,
                             "proxy.get_active() -> str|None")
        api.register_service(self.name, "proxy.rotate", self._svc_rotate,
                             "proxy.rotate() -> str|None")

    def _svc_check(self, proxy_url, test_url="https://api.ipify.org", timeout=10):
        bus = get_bus()
        try:
            r = bus.call("http.request", "GET", test_url,
                         headers={}, body="", timeout=timeout,
                         proxies={"http": proxy_url, "https": proxy_url},
                         caller=self.name)
        except KeyError:
            return {"ok": False, "error": "http.request unavailable"}
        if not r.get("ok"):
            return {"ok": False, "error": str(r.get("error", ""))[:200]}
        return {"ok": True, "status": r.get("status"),
                "body": (r.get("body") or "")[:200]}

    def _svc_get_active(self):
        s = get_settings()
        if not s.proxy_enabled():
            return None
        return (s.get("proxy", "http", default="")
                or s.get("proxy", "socks5", default="") or None)

    def _svc_rotate(self):
        alive = [p for p in self._proxies
                 if p.get("alive") and not p.get("disabled")]
        if not alive:
            return None
        p = random.choice(alive)
        self._activate_url(p["url"])
        return p["url"]

    def __init__(self):
        self._widget = None
        self._proxies = []
        self.worker = None
        self._lbls = {}
        self._btns = {}

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
        self._proxies = []
        self.worker = None

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        mono = QFont("Consolas", 10)

        row = QHBoxLayout()
        self._lbls["new"] = QLabel()
        self._lbls["new"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row.addWidget(self._lbls["new"])
        self.input = QLineEdit()
        self.input.setFont(mono)
        row.addWidget(self.input, 1)

        self._btns["add"] = QPushButton()
        self._btns["add"].setFixedWidth(120)
        self._btns["add"].clicked.connect(self._add)
        row.addWidget(self._btns["add"])

        self._btns["bulk"] = QPushButton()
        self._btns["bulk"].setFixedWidth(140)
        self._btns["bulk"].clicked.connect(self._bulk_add)
        row.addWidget(self._btns["bulk"])

        self._btns["import"] = QPushButton()
        self._btns["import"].setFixedWidth(120)
        self._btns["import"].clicked.connect(self._import_file)
        row.addWidget(self._btns["import"])

        self._btns["export"] = QPushButton()
        self._btns["export"].setFixedWidth(120)
        self._btns["export"].clicked.connect(self._export_file)
        row.addWidget(self._btns["export"])
        layout.addLayout(row)

        row2 = QHBoxLayout()
        self._lbls["test_url"] = QLabel()
        self._lbls["test_url"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row2.addWidget(self._lbls["test_url"])
        self.test_url = QLineEdit("https://api.ipify.org?format=json")
        self.test_url.setFont(mono)
        row2.addWidget(self.test_url, 1)

        self._lbls["workers"] = QLabel()
        self._lbls["workers"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        row2.addWidget(self._lbls["workers"])
        from PySide6.QtWidgets import QSpinBox
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 50)
        self.workers_spin.setValue(10)
        self.workers_spin.setFixedWidth(70)
        row2.addWidget(self.workers_spin)

        self.check_btn = QPushButton()
        self.check_btn.setIcon(theme_loader.get_icon(icon_name="play", emoji=">", size=14))
        self.check_btn.setFixedWidth(180)
        self.check_btn.clicked.connect(self._check_all)
        row2.addWidget(self.check_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setFixedWidth(120)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        row2.addWidget(self.stop_btn)
        layout.addLayout(row2)

        split = QSplitter()

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)

        self._lbls["proxies"] = QLabel()
        self._lbls["proxies"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        ll.addWidget(self._lbls["proxies"])

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        ll.addWidget(self.table, 1)

        list_btns = QHBoxLayout()
        self._btns["use"] = QPushButton()
        self._btns["use"].clicked.connect(self._use_selected)
        list_btns.addWidget(self._btns["use"])
        self._btns["disable"] = QPushButton()
        self._btns["disable"].clicked.connect(self._disable)
        list_btns.addWidget(self._btns["disable"])
        self._btns["toggle"] = QPushButton()
        self._btns["toggle"].clicked.connect(self._toggle_disabled)
        list_btns.addWidget(self._btns["toggle"])
        self._btns["delete"] = QPushButton()
        self._btns["delete"].clicked.connect(self._delete)
        list_btns.addWidget(self._btns["delete"])
        ll.addLayout(list_btns)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self._lbls["log"] = QLabel()
        self._lbls["log"].setStyleSheet(f"color: {_c('fg_dim')}; font-size: 11px;")
        rl.addWidget(self._lbls["log"])
        self.log = QPlainTextEdit()
        self.log.setFont(mono)
        self.log.setReadOnly(True)
        rl.addWidget(self.log)

        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([600, 400])
        layout.addWidget(split, 1)

        self._load()
        self._refresh()
        self.retranslate()
        return root

    def retranslate(self):
        def t(k, **kw):
            return tr(k, module="proxy_manager", **kw)
        if not hasattr(self, "input"):
            return
        self._lbls["new"].setText(t("new"))
        self.input.setPlaceholderText(t("placeholder"))
        self._btns["add"].setText(t("add"))
        self._btns["bulk"].setText(t("bulk_add"))
        self._btns["import"].setText(t("import"))
        self._btns["export"].setText(t("export"))
        self._lbls["test_url"].setText(t("test_url"))
        self._lbls["workers"].setText(t("workers"))
        self.check_btn.setText(t("check"))
        self.stop_btn.setText(t("stop"))
        self._lbls["proxies"].setText(t("proxies"))
        self._btns["use"].setText(t("use_selected"))
        self._btns["disable"].setText(t("disable"))
        self._btns["toggle"].setText(t("toggle_disabled"))
        self._btns["delete"].setText(t("delete"))
        self._lbls["log"].setText(t("log"))
        self.table.setHorizontalHeaderLabels([
            t("col.url"), t("col.status"), t("col.latency"),
            t("col.fails"), t("col.active")])

    def _load(self):
        if self.STORE.exists():
            try:
                with open(self.STORE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._proxies = []
                for p in data:
                    if isinstance(p, str):
                        p = {"url": p}
                    p.setdefault("note", "")
                    p.setdefault("alive", None)
                    p.setdefault("latency", None)
                    p.setdefault("fails", 0)
                    p.setdefault("disabled", False)
                    self._proxies.append(p)
            except Exception:
                self._proxies = []
        else:
            self._proxies = []

    def _save(self):
        self.STORE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.STORE, "w", encoding="utf-8") as f:
            json.dump(self._proxies, f, ensure_ascii=False, indent=2)

    def _refresh(self):
        try:
            self.table.setRowCount(len(self._proxies))
            for i, p in enumerate(self._proxies):
                url_item = QTableWidgetItem(p["url"])
                if p.get("disabled"):
                    url_item.setForeground(QColor(_c("fg_dark")))
                self.table.setItem(i, 0, url_item)

                if p.get("disabled"):
                    status = tr("status.disabled", module="proxy_manager")
                    color = _c("fg_dark")
                elif p.get("alive") is True:
                    status = tr("status.alive", module="proxy_manager")
                    color = _c("success")
                elif p.get("alive") is False:
                    status = tr("status.dead", module="proxy_manager")
                    color = _c("danger")
                else:
                    status = tr("status.unknown", module="proxy_manager")
                    color = _c("fg_dim")
                s_item = QTableWidgetItem(status)
                s_item.setForeground(QColor(color))
                self.table.setItem(i, 1, s_item)

                lat = p.get("latency")
                self.table.setItem(i, 2, QTableWidgetItem(
                    f"{lat} ms" if lat else "-"))
                self.table.setItem(i, 3, QTableWidgetItem(str(p.get("fails", 0))))

                active = self._is_active(p["url"])
                a_item = QTableWidgetItem("+" if active else "")
                if active:
                    a_item.setForeground(QColor(_c("success")))
                self.table.setItem(i, 4, a_item)
        except RuntimeError:
            return

    def _is_active(self, url):
        s = get_settings()
        if not s.proxy_enabled():
            return False
        active = (s.get("proxy", "http", default="")
                  or s.get("proxy", "socks5", default=""))
        return active == url

    def _selected_row(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return -1
        return sel[0].row()

    def _add(self):
        url = self.input.text().strip()
        if not url:
            return
        self._add_proxy(url)
        self.input.clear()
        self._save()
        self._refresh()

    def _bulk_add(self):
        text, ok = QInputDialog.getMultiLineText(
            parent_widget(self._widget),
            tr("bulk_add", module="proxy_manager"),
            tr("bulk.hint", module="proxy_manager"))
        if not ok or not text.strip():
            return
        added = 0
        for line in text.splitlines():
            u = line.strip()
            if u:
                self._add_proxy(u)
                added += 1
        self._save()
        self._refresh()
        self.log.appendPlainText(
            tr("log.bulk", module="proxy_manager", count=added))

    def _add_proxy(self, url):
        if not (url.startswith("http://") or url.startswith("https://")
                or url.startswith("socks5://") or url.startswith("socks4://")):
            url = "http://" + url
        if any(p["url"] == url for p in self._proxies):
            return
        self._proxies.append({
            "url": url, "note": "", "alive": None,
            "latency": None, "fails": 0, "disabled": False,
        })

    def _import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            parent_widget(self._widget),
            tr("import", module="proxy_manager"), "",
            "Text (*.txt);;JSON (*.json);;All (*)")
        if not path:
            return
        added = 0
        try:
            if path.endswith(".json"):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for p in data:
                    u = p if isinstance(p, str) else p.get("url", "")
                    if u:
                        before = len(self._proxies)
                        self._add_proxy(u)
                        if len(self._proxies) > before:
                            added += 1
            else:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        u = line.strip()
                        if u and not u.startswith("#"):
                            before = len(self._proxies)
                            self._add_proxy(u)
                            if len(self._proxies) > before:
                                added += 1
            self._save()
            self._refresh()
            self.log.appendPlainText(
                tr("log.imported", module="proxy_manager", count=added))
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="proxy_manager"), str(e))

    def _export_file(self):
        if not self._proxies:
            return
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("export", module="proxy_manager"),
            "proxies.txt", "Text (*.txt);;JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                if path.endswith(".json"):
                    json.dump(self._proxies, f, ensure_ascii=False, indent=2)
                else:
                    for p in self._proxies:
                        f.write(p["url"] + "\n")
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="proxy_manager"), str(e))

    def _delete(self):
        row = self._selected_row()
        if row < 0:
            return
        del self._proxies[row]
        self._save()
        self._refresh()

    def _toggle_disabled(self):
        row = self._selected_row()
        if row < 0:
            return
        self._proxies[row]["disabled"] = not self._proxies[row].get("disabled")
        self._save()
        self._refresh()

    def _activate_url(self, url):
        s = get_settings()
        s.set("proxy", "enabled", value=True)
        if url.startswith("socks5://") or url.startswith("socks4://"):
            s.set("proxy", "socks5", value=url)
            s.set("proxy", "http", value="")
            s.set("proxy", "https", value="")
        else:
            s.set("proxy", "http", value=url)
            s.set("proxy", "https", value=url)
            s.set("proxy", "socks5", value="")

    def _use_selected(self):
        row = self._selected_row()
        if row < 0:
            return
        p = self._proxies[row]
        if p.get("disabled"):
            return
        self._activate_url(p["url"])
        self._refresh()
        try:
            self.log.appendPlainText(
                tr("log.active", module="proxy_manager", url=p["url"]))
        except RuntimeError:
            pass

    def _disable(self):
        s = get_settings()
        s.set("proxy", "enabled", value=False)
        self._refresh()
        try:
            self.log.appendPlainText(tr("log.disabled", module="proxy_manager"))
        except RuntimeError:
            pass

    def _check_all(self):
        if not self._proxies:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        try:
            self.log.clear()
            self.check_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        except RuntimeError:
            return

        urls = [p["url"] for p in self._proxies if not p.get("disabled")]
        self.worker = ProxyCheckWorker(
            proxies=urls,
            test_url=self.test_url.text().strip() or "https://api.ipify.org?format=json",
            timeout=10,
            workers=self.workers_spin.value(),
        )
        self.worker.one_done.connect(self._on_one)
        self.worker.all_done.connect(self._on_all)
        self.worker.start()

    def _stop(self):
        if self.worker:
            self.worker.stop()

    def _on_one(self, proxy, ok, ms, note):
        for p in self._proxies:
            if p["url"] != proxy:
                continue
            if ok:
                p["alive"] = True
                p["latency"] = ms
                p["fails"] = 0
            else:
                p["alive"] = False
                p["fails"] = p.get("fails", 0) + 1
                if p["fails"] >= MAX_FAILS:
                    p["disabled"] = True
            break
        self._save()
        self._refresh()
        try:
            if ok:
                self.log.appendPlainText(
                    tr("log.good_proxy", module="proxy_manager",
                       url=proxy, ms=ms, note=note))
            else:
                self.log.appendPlainText(
                    tr("log.bad_proxy", module="proxy_manager",
                       url=proxy, error=note))
        except RuntimeError:
            return

    def _on_all(self):
        try:
            self.check_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.log.appendPlainText(tr("log.check_done", module="proxy_manager"))
        except RuntimeError:
            return