import csv
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import dns.resolver
import dns.reversename
import dns.exception

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QDoubleSpinBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFileDialog, QMessageBox,
    QCheckBox, QMenu
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.history import get_history
from core.i18n import tr
from core.utils import color as _c, parent_widget
from styles import loader as theme_loader


RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "SRV", "CAA", "PTR"]
DEFAULT_TYPES = {"A", "AAAA", "CNAME", "MX", "NS", "TXT"}


def _is_ip(s):
    try:
        socket.inet_pton(socket.AF_INET, s)
        return True
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, s)
        return True
    except OSError:
        return False


def _clean_nameservers(servers):
    import ipaddress
    cleaned = []
    fallback = []
    for s in servers:
        try:
            ip = ipaddress.ip_address(str(s))
        except Exception:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_multicast:
            continue
        if ip.is_private:
            fallback.append(str(ip))
        else:
            cleaned.append(str(ip))
    result = cleaned[:2] if cleaned else fallback[:2]
    if not result:
        result = ["1.1.1.1", "8.8.8.8"]
    return result


def _make_resolver(nameserver, timeout):
    r = dns.resolver.Resolver()
    if nameserver:
        parts = [p.strip() for p in nameserver.replace(",", " ").split() if p.strip()]
        if parts:
            r.nameservers = parts
            r.timeout = timeout
            r.lifetime = timeout * 2.0
            return r

    system_ns = []
    try:
        system_ns = list(r.nameservers)
    except Exception:
        pass

    r.nameservers = _clean_nameservers(system_ns)
    r.timeout = timeout
    r.lifetime = timeout * 2.0
    return r


def _resolve_one(resolver, domain, rtype):
    out = []
    try:
        answer = resolver.resolve(domain, rtype, raise_on_no_answer=False)
    except dns.resolver.NXDOMAIN:
        return [{
            "domain": domain, "type": rtype, "value": "",
            "ttl": None, "status": "nxdomain", "error": "",
        }]
    except dns.resolver.NoNameservers as e:
        return [{
            "domain": domain, "type": rtype, "value": "",
            "ttl": None, "status": "error", "error": f"no-nameservers: {e}",
        }]
    except dns.resolver.NoResolverConfiguration:
        return [{
            "domain": domain, "type": rtype, "value": "",
            "ttl": None, "status": "error", "error": "no-resolver-config",
        }]
    except dns.exception.Timeout:
        return [{
            "domain": domain, "type": rtype, "value": "",
            "ttl": None, "status": "timeout", "error": "timeout",
        }]
    except Exception as e:
        return [{
            "domain": domain, "type": rtype, "value": "",
            "ttl": None, "status": "error", "error": str(e)[:120],
        }]

    if answer is None or answer.rrset is None:
        return [{
            "domain": domain, "type": rtype, "value": "",
            "ttl": None, "status": "empty", "error": "",
        }]

    ttl = int(answer.rrset.ttl)
    for rdata in answer:
        try:
            val = str(rdata)
        except Exception:
            val = repr(rdata)
        out.append({
            "domain": domain, "type": rtype, "value": val,
            "ttl": ttl, "status": "ok", "error": "",
        })
    if not out:
        out.append({
            "domain": domain, "type": rtype, "value": "",
            "ttl": ttl, "status": "empty", "error": "",
        })
    return out


def _reverse_one(resolver, ip):
    try:
        rev = dns.reversename.from_address(ip)
    except Exception as e:
        return [{
            "domain": ip, "type": "PTR", "value": "",
            "ttl": None, "status": "error", "error": str(e)[:120],
        }]
    return _resolve_one(resolver, str(rev), "PTR")


def _query_all(domains, types, nameserver, timeout):
    resolver = _make_resolver(nameserver, timeout)

    jobs = []
    for d in domains:
        if _is_ip(d):
            jobs.append(("ptr", d, None))
        else:
            for t in types:
                if t == "PTR":
                    continue
                jobs.append(("fwd", d, t))

    results = []

    def one(job):
        kind, domain, rtype = job
        if kind == "ptr":
            return _reverse_one(resolver, domain)
        return _resolve_one(resolver, domain, rtype)

    with ThreadPoolExecutor(max_workers=8) as ex:
        for fut in as_completed([ex.submit(one, j) for j in jobs]):
            try:
                rows = fut.result()
            except Exception:
                continue
            results.extend(rows)

    results.sort(key=lambda r: (r["domain"], r["type"], r["value"]))
    return results


class DnsWorker(QThread):
    progress = Signal(int, int)
    found = Signal(list)
    log_msg = Signal(str)
    finished_lookup = Signal(list, float)

    def __init__(self, domains, types, nameserver, timeout):
        super().__init__()
        self.domains = domains
        self.types = list(types)
        self.nameserver = nameserver
        self.timeout = timeout
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        t0 = time.time()

        try:
            resolver = _make_resolver(self.nameserver, self.timeout)
        except Exception as e:
            try:
                self.log_msg.emit(f"[-] resolver init failed: {e}")
            except RuntimeError:
                pass
            self.finished_lookup.emit([], time.time() - t0)
            return

        try:
            servers = ", ".join(str(s) for s in resolver.nameservers)
            self.log_msg.emit(f"[i] nameservers: {servers}")
        except Exception:
            pass

        jobs = []
        for d in self.domains:
            if self._stop:
                break
            if _is_ip(d):
                jobs.append(("ptr", d, "PTR"))
            else:
                for t in self.types:
                    if t == "PTR":
                        continue
                    jobs.append(("fwd", d, t))

        total = len(jobs)
        done = 0
        all_rows = []
        batch = []

        def one(job):
            kind, domain, rtype = job
            if kind == "ptr":
                return _reverse_one(resolver, domain)
            return _resolve_one(resolver, domain, rtype)

        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(one, j): j for j in jobs}
            for fut in as_completed(futures):
                if self._stop:
                    for f in futures:
                        f.cancel()
                    break
                done += 1
                try:
                    rows = fut.result()
                except Exception as e:
                    kind, domain, rtype = futures[fut]
                    rows = [{
                        "domain": domain, "type": rtype or "PTR",
                        "value": "", "ttl": None,
                        "status": "error", "error": str(e)[:120],
                    }]
                batch.extend(rows)
                all_rows.extend(rows)

                if len(batch) >= 20 or done == total:
                    try:
                        self.found.emit(list(batch))
                    except RuntimeError:
                        pass
                    batch = []
                    try:
                        self.progress.emit(done, total)
                    except RuntimeError:
                        pass

        if batch:
            try:
                self.found.emit(list(batch))
            except RuntimeError:
                pass

        all_rows.sort(key=lambda r: (r["domain"], r["type"], r["value"]))
        self.finished_lookup.emit(all_rows, time.time() - t0)


class DnsLookupModule(BaseModule):
    def on_load(self, api):
        api.register_service(self.name, "dns.lookup", self._svc_lookup,
                             "dns.lookup(domain, types=None, nameserver=None, "
                             "timeout=5) -> list[dict]")
        api.register_service(self.name, "dns.reverse", self._svc_reverse,
                             "dns.reverse(ip, nameserver=None, timeout=5) -> list[dict]")

    def _svc_lookup(self, domain, types=None, nameserver=None, timeout=5):
        if not types:
            types = sorted(DEFAULT_TYPES)
        domains = [d.strip() for d in str(domain).replace(",", " ").split() if d.strip()]
        if not domains:
            return []
        return _query_all(domains, list(types), nameserver, timeout)

    def _svc_reverse(self, ip, nameserver=None, timeout=5):
        ips = [x.strip() for x in str(ip).replace(",", " ").split() if x.strip()]
        results = []
        for x in ips:
            results.extend(_query_all([x], ["PTR"], nameserver, timeout))
        return results

    def __init__(self):
        self._widget = None
        self._results = []
        self.worker = None
        self._lbls = {}
        self._checks = {}
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
        self._results = []

        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        mono = QFont("Consolas", 10)

        row1 = QHBoxLayout()
        self._lbls["domains"] = QLabel()
        self._lbls["domains"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row1.addWidget(self._lbls["domains"])
        self.domains_input = QLineEdit("example.com")
        self.domains_input.setFont(mono)
        row1.addWidget(self.domains_input, 1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self._lbls["types"] = QLabel()
        self._lbls["types"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row2.addWidget(self._lbls["types"])
        for t in RECORD_TYPES:
            cb = QCheckBox(t)
            cb.setChecked(t in DEFAULT_TYPES)
            self._checks[t] = cb
            row2.addWidget(cb)
        row2.addStretch()
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        self._lbls["nameserver"] = QLabel()
        self._lbls["nameserver"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row3.addWidget(self._lbls["nameserver"])
        self.nameserver_input = QLineEdit("")
        self.nameserver_input.setFont(mono)
        row3.addWidget(self.nameserver_input, 1)

        self._lbls["timeout"] = QLabel()
        self._lbls["timeout"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row3.addWidget(self._lbls["timeout"])
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.5, 30.0)
        self.timeout_spin.setValue(5.0)
        self.timeout_spin.setSingleStep(0.5)
        self.timeout_spin.setFixedWidth(90)
        row3.addWidget(self.timeout_spin)

        row3.addStretch()
        layout.addLayout(row3)

        row4 = QHBoxLayout()
        self.run_btn = QPushButton()
        self.run_btn.setIcon(theme_loader.get_icon(
            icon_name="play", emoji=">", size=14))
        self.run_btn.setFixedWidth(180)
        self.run_btn.clicked.connect(self._start)
        row4.addWidget(self.run_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setFixedWidth(120)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        row4.addWidget(self.stop_btn)

        self.save_btn = QPushButton()
        self.save_btn.setFixedWidth(140)
        self.save_btn.clicked.connect(self._save)
        row4.addWidget(self.save_btn)

        self.clear_btn = QPushButton()
        self.clear_btn.setFixedWidth(120)
        self.clear_btn.clicked.connect(self._clear)
        row4.addWidget(self.clear_btn)

        row4.addStretch()

        self.status = QLabel("-")
        self.status.setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row4.addWidget(self.status)

        layout.addLayout(row4)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.table, 1)

        self.log = QPlainTextEdit()
        self.log.setFont(mono)
        self.log.setReadOnly(True)
        self.log.setFixedHeight(80)
        layout.addWidget(self.log)

        self.retranslate()
        return root

    def retranslate(self):
        def t(k, **kw):
            return tr(k, module="dns_lookup", **kw)
        if not hasattr(self, "domains_input"):
            return
        self._lbls["domains"].setText(t("domains"))
        self.domains_input.setPlaceholderText(t("domains.placeholder"))
        self._lbls["types"].setText(t("types"))
        self._lbls["nameserver"].setText(t("nameserver"))
        self.nameserver_input.setPlaceholderText(t("nameserver.placeholder"))
        self._lbls["timeout"].setText(t("timeout"))
        self.run_btn.setText(t("run"))
        self.stop_btn.setText(t("stop"))
        self.save_btn.setText(t("save"))
        self.clear_btn.setText(t("clear"))
        self.table.setHorizontalHeaderLabels([
            t("col.num"), t("col.domain"), t("col.type"),
            t("col.ttl"), t("col.value"),
        ])
        if not self._results and not (self.worker and self.worker.isRunning()):
            self.status.setText(t("status.idle"))

    def _selected_types(self):
        return [k for k, cb in self._checks.items() if cb.isChecked()]

    def _start(self):
        raw = self.domains_input.text()
        domains = [d.strip() for d in raw.replace(",", " ").split() if d.strip()]
        if not domains:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="dns_lookup"),
                                tr("err.no_domains", module="dns_lookup"))
            return

        types = self._selected_types()
        if not types:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="dns_lookup"),
                                tr("err.no_types", module="dns_lookup"))
            return

        if self.worker is not None and self.worker.isRunning():
            return

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self._results = []
        self.log.clear()

        ns_display = self.nameserver_input.text().strip() or "(system)"
        self.log.appendPlainText(tr(
            "log.start", module="dns_lookup",
            domains=len(domains),
            types=",".join(types),
            server=ns_display))

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        self.status.setText(tr("status.running", module="dns_lookup",
                                done=0, total=len(domains) * len(types)))

        self.worker = DnsWorker(
            domains=domains,
            types=types,
            nameserver=self.nameserver_input.text().strip() or None,
            timeout=self.timeout_spin.value(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.found.connect(self._on_found)
        self.worker.log_msg.connect(self._on_log)
        self.worker.finished_lookup.connect(self._on_done)
        self.worker.start()

    def _stop(self):
        if self.worker:
            self.worker.stop()
            try:
                self.log.appendPlainText(
                    tr("log.stopping", module="dns_lookup"))
            except RuntimeError:
                pass

    def _on_progress(self, done, total):
        try:
            self.status.setText(tr("status.running", module="dns_lookup",
                                    done=done, total=total))
        except RuntimeError:
            return

    def _on_log(self, msg):
        try:
            self.log.appendPlainText(msg)
        except RuntimeError:
            return

    def _on_found(self, rows):
        try:
            for r in rows:
                self._results.append(r)
                self._insert_row(r)
        except RuntimeError:
            return

    def _insert_row(self, r):
        row = self.table.rowCount()
        self.table.insertRow(row)

        num = QTableWidgetItem(str(row + 1))
        num.setData(Qt.UserRole, r)
        self.table.setItem(row, 0, num)

        self.table.setItem(row, 1, QTableWidgetItem(r.get("domain", "")))

        type_item = QTableWidgetItem(r.get("type", ""))
        type_item.setForeground(QColor(_c("fg_soft")))
        self.table.setItem(row, 2, type_item)

        ttl = r.get("ttl")
        self.table.setItem(row, 3, QTableWidgetItem(
            str(ttl) if ttl is not None else "-"))

        status = r.get("status", "")
        value = r.get("value", "")
        if status == "error":
            value = r.get("error") or "error"
        elif status == "timeout":
            value = "(timeout)"
        elif status == "nxdomain":
            value = "(NXDOMAIN)"
        elif status == "empty":
            value = "(no records)"

        val_item = QTableWidgetItem(value[:500])
        val_item.setToolTip(value)

        if status == "ok":
            val_item.setForeground(QColor(_c("fg")))
        elif status == "error":
            val_item.setForeground(QColor(_c("danger")))
        elif status == "timeout":
            val_item.setForeground(QColor(_c("warning")))
        else:
            val_item.setForeground(QColor(_c("fg_dim")))
        self.table.setItem(row, 4, val_item)

    def _on_done(self, results, duration):
        self._results = results
        try:
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.table.setSortingEnabled(True)
        except RuntimeError:
            pass

        ok = sum(1 for r in results if r.get("status") == "ok")
        empty = sum(1 for r in results
                    if r.get("status") in ("empty", "nxdomain"))
        errors = sum(1 for r in results
                     if r.get("status") in ("error", "timeout"))

        try:
            self.status.setText(
                tr("status.done", module="dns_lookup",
                   ok=ok, empty=empty, errors=errors,
                   total=len(results), duration=duration))
            self.log.appendPlainText(
                tr("log.done", module="dns_lookup",
                   ok=ok, total=len(results)) + f"  ({duration:.2f}s)")
        except RuntimeError:
            pass

        try:
            get_history().add(
                module=self.name, session=None, method="DNS",
                url=self.domains_input.text()[:200],
                headers={}, body="",
                status=ok,
                response_body=json.dumps(results[:30], ensure_ascii=False)[:4000],
                elapsed=duration)
        except Exception:
            pass

    def _clear(self):
        try:
            self.table.setRowCount(0)
            self._results = []
            self.log.clear()
            self.status.setText(tr("status.idle", module="dns_lookup"))
        except RuntimeError:
            return

    def _context_menu(self, pos):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        menu = QMenu(self._widget)

        copy_val = menu.addAction(
            tr("ctx.copy_value", module="dns_lookup"))
        copy_val.triggered.connect(lambda: self._copy_col(row, 4))

        copy_row = menu.addAction(
            tr("ctx.copy_row", module="dns_lookup"))
        copy_row.triggered.connect(lambda: self._copy_row(row))

        copy_dom = menu.addAction(
            tr("ctx.copy_domain", module="dns_lookup"))
        copy_dom.triggered.connect(lambda: self._copy_col(row, 1))

        menu.exec_(self.table.mapToGlobal(pos))

    def _copy_col(self, row, col):
        item = self.table.item(row, col)
        if item:
            QGuiApplication.clipboard().setText(item.text())

    def _copy_row(self, row):
        parts = []
        for c in range(self.table.columnCount()):
            it = self.table.item(row, c)
            parts.append(it.text() if it else "")
        QGuiApplication.clipboard().setText("\t".join(parts))

    def _save(self):
        if not self._results:
            return
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("save.title", module="dns_lookup"),
            "dns_results.json",
            "JSON (*.json);;CSV (*.csv);;Text (*.txt)")
        if not path:
            return
        try:
            if path.endswith(".csv"):
                with open(path, "w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["domain", "type", "ttl", "status", "value", "error"])
                    for r in self._results:
                        w.writerow([
                            r.get("domain", ""), r.get("type", ""),
                            r.get("ttl") if r.get("ttl") is not None else "",
                            r.get("status", ""),
                            r.get("value", ""), r.get("error", "")])
            elif path.endswith(".txt"):
                with open(path, "w", encoding="utf-8") as f:
                    for r in self._results:
                        line = (f"{r.get('domain','')}  {r.get('type',''):<6}  "
                                f"ttl={r.get('ttl') if r.get('ttl') is not None else '-':<6}  ")
                        if r.get("status") == "ok":
                            line += r.get("value", "")
                        else:
                            line += f"[{r.get('status')}] {r.get('error') or r.get('value') or ''}"
                        f.write(line.rstrip() + "\n")
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self._results, f, ensure_ascii=False, indent=2)
            self.log.appendPlainText(tr(
                "log.saved", module="dns_lookup", path=path))
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="dns_lookup"),
                                str(e))