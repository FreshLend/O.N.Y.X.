import csv
import errno
import ipaddress
import json
import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QFont, QColor, QGuiApplication, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QDoubleSpinBox, QProgressBar, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog,
    QMessageBox, QCheckBox, QComboBox, QMenu
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.history import get_history
from core.i18n import tr
from core.utils import color as _c, parent_widget
from styles import loader as theme_loader


try:
    import scapy.all as scapy
    try:
        scapy.conf.verb = 0
    except Exception:
        pass
    HAS_SCAPY = True
except Exception:
    HAS_SCAPY = False

CAN_SYN = HAS_SCAPY
CAN_OS_FP = HAS_SCAPY


SERVICES = {
    20: "ftp-data", 21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp",
    53: "dns", 69: "tftp", 79: "finger", 80: "http", 88: "kerberos",
    110: "pop3", 111: "rpcbind", 119: "nntp", 123: "ntp", 135: "msrpc",
    137: "netbios-ns", 138: "netbios-dgm", 139: "netbios-ssn",
    143: "imap", 161: "snmp", 162: "snmp-trap", 179: "bgp",
    389: "ldap", 443: "https", 445: "microsoft-ds", 465: "smtps",
    500: "isakmp", 512: "exec", 513: "login", 514: "syslog",
    515: "lpd", 548: "afp", 554: "rtsp", 587: "submission",
    631: "ipp", 636: "ldaps", 873: "rsync", 990: "ftps",
    993: "imaps", 995: "pop3s", 1080: "socks", 1194: "openvpn",
    1433: "mssql", 1521: "oracle", 1723: "pptp", 1883: "mqtt",
    2049: "nfs", 2082: "cpanel", 2083: "cpanel-ssl",
    2181: "zookeeper", 2375: "docker", 2376: "docker-tls",
    3000: "http-alt", 3128: "squid", 3260: "iscsi",
    3306: "mysql", 3389: "rdp", 4444: "metasploit",
    5000: "http-alt", 5060: "sip", 5222: "xmpp",
    5432: "postgres", 5601: "kibana", 5672: "amqp",
    5900: "vnc", 5901: "vnc-1", 5984: "couchdb",
    6000: "x11", 6379: "redis", 6443: "k8s-api",
    7001: "weblogic", 7070: "realserver", 7474: "neo4j",
    7687: "neo4j-bolt", 8000: "http-alt", 8008: "http-alt",
    8080: "http-proxy", 8081: "http-alt", 8086: "influxdb",
    8161: "activemq", 8443: "https-alt", 8888: "http-alt",
    9000: "http-alt", 9042: "cassandra", 9090: "http-alt",
    9092: "kafka", 9200: "elasticsearch", 9300: "elasticsearch-cluster",
    9418: "git", 9443: "https-alt", 10000: "webmin",
    11211: "memcached", 15672: "rabbitmq-mgmt",
    27017: "mongodb", 27018: "mongodb-shard",
    50000: "sap", 50070: "hadoop-namenode",
}

TOP_100 = [
    7, 9, 13, 21, 22, 23, 25, 26, 37, 53, 79, 80, 81, 82, 88, 106,
    110, 111, 113, 119, 135, 139, 143, 144, 179, 199, 389, 427, 443,
    444, 445, 465, 513, 514, 515, 543, 544, 548, 554, 587, 631, 636,
    646, 873, 990, 993, 995, 1025, 1026, 1027, 1028, 1029, 1110,
    1433, 1720, 1723, 1755, 1900, 2000, 2001, 2049, 2121, 2717,
    3000, 3128, 3306, 3389, 3986, 4899, 5000, 5009, 5051, 5060,
    5101, 5190, 5357, 5432, 5631, 5666, 5800, 5900, 6000, 6001,
    6379, 6646, 7070, 8000, 8008, 8009, 8080, 8081, 8443, 8888,
    9100, 9999, 10000, 32768, 49152, 49153, 49154, 49155, 49156, 49157,
]

WEB_PORTS = [
    80, 81, 443, 591, 2082, 2087, 2095, 2096, 3000, 4243, 5000, 7000,
    8000, 8008, 8009, 8080, 8081, 8082, 8083, 8088, 8090, 8181, 8443,
    8888, 9000, 9090, 9443, 10000,
]

DATABASES = [
    1433, 1521, 1830, 2483, 2484, 3050, 3306, 3351, 5432, 5984, 6379,
    7474, 7687, 8086, 8529, 9042, 9200, 9300, 11211, 27017, 27018, 28017,
]

MAIL_PORTS = [25, 110, 143, 465, 587, 993, 995]

COMMON = sorted(set(TOP_100 + WEB_PORTS + DATABASES + MAIL_PORTS))

PRESETS = {
    "top-100": TOP_100,
    "web": WEB_PORTS,
    "databases": DATABASES,
    "mail": MAIL_PORTS,
    "common": COMMON,
    "1-1024": list(range(1, 1025)),
}

WEB_BANNER_PROBE_PORTS = {
    80, 81, 88, 443, 591, 3000, 4243, 5000, 7000, 8000, 8008, 8080,
    8081, 8082, 8083, 8088, 8090, 8181, 8443, 8888, 9000, 9090, 9443, 10000,
}


BANNER_PATTERNS = [
    (r"SSH-[\d.]+-OpenSSH[_-]([\d.p]+)", "ssh", "OpenSSH"),
    (r"SSH-[\d.]+-([A-Za-z][A-Za-z0-9._-]*?)[_-]([\d.p]+)", "ssh", None),
    (r"Server:\s*nginx/?([\d.]*)", "http", "nginx"),
    (r"Server:\s*Apache/?([\d.]*)", "http", "Apache"),
    (r"Server:\s*Microsoft-IIS/?([\d.]*)", "http", "IIS"),
    (r"Server:\s*uvicorn", "http", "uvicorn"),
    (r"Server:\s*Werkzeug/?([\d.]*)", "http", "Werkzeug"),
    (r"Server:\s*SimpleHTTP/?([\d.]*)", "http", "SimpleHTTP"),
    (r"Server:\s*cloudflare", "http", "Cloudflare"),
    (r"Server:\s*gunicorn/?([\d.]*)", "http", "gunicorn"),
    (r"Server:\s*Caddy", "http", "Caddy"),
    (r"Open WebUI", "http", "Open WebUI"),
    (r"^HTTP/[\d.]+", "http", None),
    (r"220[ -].*ProFTPD\s*([\d.]*)", "ftp", "ProFTPD"),
    (r"220[ -].*vsFTPd\s*([\d.]*)", "ftp", "vsFTPd"),
    (r"220[ -].*FileZilla", "ftp", "FileZilla"),
    (r"220[ -]Microsoft ESMTP", "smtp", "Microsoft SMTP"),
    (r"220[ -]Postfix", "smtp", "Postfix"),
    (r"220[ -]Exim", "smtp", "Exim"),
    (r"\+OK.*Dovecot", "pop3", "Dovecot"),
    (r"^Redis", "redis", "Redis"),
    (r"^-ERR unknown command", "redis", "Redis"),
    (r"^MongoDB", "mongodb", "MongoDB"),
    (r"^MySQL", "mysql", "MySQL"),
    (r"^MariaDB", "mysql", "MariaDB"),
    (r"elasticsearch", "elasticsearch", "Elasticsearch"),
    (r"AMQP", "amqp", "RabbitMQ"),
    (r"RDP", "rdp", "RDP"),
]


def _detect_service(port, banner):
    known = SERVICES.get(port) or ""
    if not banner:
        return known, "", ""
    for pattern, svc, product in BANNER_PATTERNS:
        m = re.search(pattern, banner, re.IGNORECASE | re.MULTILINE)
        if not m:
            continue
        version = ""
        try:
            for g in m.groups():
                if g:
                    version = str(g).strip(".p ")
                    break
        except Exception:
            pass
        return (svc or known), (product or ""), version
    return known, "", ""


TTL_TABLE = [
    (64, "Linux/Unix/macOS"),
    (128, "Windows"),
    (255, "Network device (Cisco/BSD)"),
    (60, "Solaris/AIX"),
    (30, "Embedded/IoT"),
    (32, "Embedded/IoT"),
    (252, "Cisco IOS"),
]


def _os_from_ttl(ttl):
    if ttl is None:
        return "", ""
    best = None
    best_hops = None
    for initial, name in TTL_TABLE:
        if initial >= ttl:
            hops = initial - ttl
            if best_hops is None or hops < best_hops:
                best = name
                best_hops = hops
    if best is None:
        return f"unknown (ttl={ttl})", ""
    if best_hops <= 1:
        conf = "high"
    elif best_hops <= 5:
        conf = "medium"
    else:
        conf = "low"
    return f"{best} (hops≈{best_hops})", conf


def _parse_port_spec(spec):
    spec = (spec or "").strip()
    if not spec:
        return []
    if spec in PRESETS:
        return list(PRESETS[spec])
    if spec == "all":
        return list(range(1, 65536))
    out = set()
    for chunk in spec.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            try:
                a, b = chunk.split("-", 1)
                lo = max(1, int(a.strip()))
                hi = min(65535, int(b.strip()))
                if lo <= hi:
                    out.update(range(lo, hi + 1))
            except Exception:
                continue
        elif chunk.isdigit():
            n = int(chunk)
            if 1 <= n <= 65535:
                out.add(n)
    return sorted(out)


def _parse_targets(spec):
    out = []
    for chunk in (spec or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "/" in chunk:
            try:
                net = ipaddress.ip_network(chunk, strict=False)
                if net.num_addresses > 4096:
                    continue
                for ip in net.hosts():
                    out.append(str(ip))
            except Exception:
                continue
        else:
            out.append(chunk)
    return out


def _resolve(host):
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None


def _tcp_connect(ip, port, timeout, do_banner):
    t0 = time.time()
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        r = s.connect_ex((ip, port))
        elapsed = (time.time() - t0) * 1000
        if r == 0:
            banner = _grab_banner(s, port, timeout) if do_banner else ""
            return "open", elapsed, banner, None, ""
        if r in (errno.ECONNREFUSED, 10061):
            return "closed", elapsed, "", None, ""
        return "filtered", elapsed, "", None, f"errno-{r}"
    except socket.timeout:
        return "filtered", timeout * 1000, "", None, ""
    except Exception as e:
        return "filtered", timeout * 1000, "", None, str(e)[:80]
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


_scapy_lock = threading.Lock()


def _tcp_syn_batch(ip, ports, timeout, stop_check=None):
    if not HAS_SCAPY or not ports:
        return {}
    results = {}
    CHUNK = 256
    for i in range(0, len(ports), CHUNK):
        if stop_check and stop_check():
            break
        chunk = list(ports[i:i + CHUNK])
        answers = []
        with _scapy_lock:
            try:
                pkt = scapy.IP(dst=ip) / scapy.TCP(dport=chunk, flags="S")
                ans, _unans = scapy.sr(
                    pkt, timeout=timeout, verbose=0,
                    retry=0, inter=0.001,
                )
                answers = list(ans)
            except OSError:
                for p in chunk:
                    results.setdefault(p, {
                        "status": "filtered", "ttl": None,
                        "error": "scapy-pipe-error",
                    })
                continue
            except Exception as e:
                for p in chunk:
                    results.setdefault(p, {
                        "status": "filtered", "ttl": None,
                        "error": str(e)[:80],
                    })
                continue

        for sent, recv in answers:
            try:
                port = int(sent[scapy.TCP].dport)
            except Exception:
                continue
            ttl = getattr(recv, "ttl", None)
            try:
                if recv.haslayer(scapy.TCP):
                    tcp = recv.getlayer(scapy.TCP)
                    flags = int(tcp.flags)
                    if (flags & 0x12) == 0x12:
                        results[port] = {"status": "open",
                                          "ttl": ttl, "error": ""}
                    elif flags & 0x04:
                        results[port] = {"status": "closed",
                                          "ttl": ttl, "error": ""}
                    else:
                        results[port] = {"status": "filtered",
                                          "ttl": ttl, "error": ""}
                elif recv.haslayer(scapy.ICMP):
                    icmp = recv.getlayer(scapy.ICMP)
                    results[port] = {
                        "status": "filtered", "ttl": ttl,
                        "error": f"icmp-{icmp.type}-{icmp.code}",
                    }
                else:
                    results[port] = {"status": "filtered",
                                      "ttl": ttl, "error": ""}
            except Exception:
                continue

        for p in chunk:
            if p not in results:
                results[p] = {"status": "filtered",
                              "ttl": None, "error": ""}
    return results


def _tcp_syn_scapy(ip, port, timeout):
    res = _tcp_syn_batch(ip, [port], timeout)
    r = res.get(port) or {"status": "filtered", "ttl": None, "error": ""}
    return r["status"], timeout * 1000, "", r.get("ttl"), r.get("error", "")


def _grab_banner(sock, port, timeout):
    try:
        sock.settimeout(min(timeout, 2.0))
        if port in WEB_BANNER_PROBE_PORTS:
            try:
                sock.sendall(b"HEAD / HTTP/1.0\r\nHost: target\r\n\r\n")
            except Exception:
                pass
        data = sock.recv(2048)
        if not data:
            return ""
        text = data.decode("utf-8", errors="replace").strip()
        return text[:400].replace("\r", "").replace("\n", " | ")
    except Exception:
        return ""


def _udp_probe(ip, port, timeout):
    t0 = time.time()
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        try:
            s.send(b"")
        except Exception as e:
            return "filtered", (time.time() - t0) * 1000, "", str(e)[:80]
        try:
            data = s.recv(1024)
            elapsed = (time.time() - t0) * 1000
            return "open", elapsed, data[:100].hex() if data else "", ""
        except socket.timeout:
            return "open|filtered", timeout * 1000, "", ""
        except ConnectionRefusedError:
            return "closed", (time.time() - t0) * 1000, "", ""
        except OSError as e:
            if e.errno in (errno.ECONNREFUSED, 61, 111):
                return "closed", (time.time() - t0) * 1000, "", ""
            return "open|filtered", timeout * 1000, "", str(e)[:80]
    except Exception as e:
        return "filtered", timeout * 1000, "", str(e)[:80]
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def _udp_probe_scapy(ip, port, timeout):
    if not HAS_SCAPY:
        return _udp_probe(ip, port, timeout)
    t0 = time.time()
    try:
        pkt = scapy.IP(dst=ip) / scapy.UDP(dport=port)
        resp = scapy.sr1(pkt, timeout=timeout, verbose=0)
    except Exception as e:
        return "filtered", (time.time() - t0) * 1000, "", str(e)[:80]
    elapsed = (time.time() - t0) * 1000
    if resp is None:
        return "open|filtered", elapsed, "", ""
    try:
        if resp.haslayer(scapy.UDP):
            payload = bytes(resp.getlayer(scapy.UDP).payload)
            return "open", elapsed, payload[:100].hex(), ""
        if resp.haslayer(scapy.ICMP):
            icmp = resp.getlayer(scapy.ICMP)
            if int(icmp.type) == 3 and int(icmp.code) == 3:
                return "closed", elapsed, "", ""
            return "filtered", elapsed, "", f"icmp-{icmp.type}-{icmp.code}"
    except Exception:
        pass
    return "open|filtered", elapsed, "", ""


class NumericItem(QTableWidgetItem):
    def __init__(self, text, value):
        super().__init__(text)
        self._value = value

    def __lt__(self, other):
        try:
            return self._value < other._value
        except (AttributeError, TypeError):
            return super().__lt__(other)


class PortScanWorker(QThread):
    progress = Signal(int, int, float)
    found = Signal(dict)
    log_msg = Signal(str)
    finished_scan = Signal(list, float)

    def __init__(self, targets, ports, threads, timeout,
                 do_udp, do_banner, scan_mode, do_os_fp):
        super().__init__()
        self.targets = targets
        self.ports = ports
        self.threads = max(1, threads)
        self.timeout = timeout
        self.do_udp = do_udp
        self.do_banner = do_banner
        self.scan_mode = scan_mode
        self.do_os_fp = do_os_fp
        self._stop = False

    def stop(self):
        self._stop = True

    def _probe_tcp(self, ip, port):
        if self.scan_mode == "syn" and CAN_SYN:
            status, elapsed, banner, ttl, err = _tcp_syn_scapy(
                ip, port, self.timeout)
            return {"status": status, "elapsed": elapsed,
                    "banner": banner, "ttl": ttl, "error": err}
        status, elapsed, banner, ttl, err = _tcp_connect(
            ip, port, self.timeout, self.do_banner)
        return {"status": status, "elapsed": elapsed,
                "banner": banner, "ttl": ttl, "error": err}

    def _probe_udp(self, ip, port):
        if HAS_SCAPY:
            status, elapsed, banner, err = _udp_probe_scapy(
                ip, port, self.timeout)
        else:
            status, elapsed, banner, err = _udp_probe(
                ip, port, self.timeout)
        return {"status": status, "elapsed": elapsed,
                "banner": banner, "ttl": None, "error": err}

    def run(self):
        t_start = time.time()
        results = []
        resolved = []
        for t in self.targets:
            ip = _resolve(t)
            if ip:
                resolved.append((t, ip))
            else:
                try:
                    self.log_msg.emit(f"[-] cannot resolve: {t}")
                except RuntimeError:
                    pass

        if not resolved:
            self.finished_scan.emit([], 0.0)
            return

        use_syn = self.scan_mode == "syn" and CAN_SYN

        if use_syn:
            self._run_syn_batch(resolved, t_start, results)
        else:
            self._run_pool(resolved, t_start, results)

    def _make_result(self, target, ip, port, proto,
                     status, elapsed, banner, ttl, err):
        svc, product, version = _detect_service(port, banner or "")
        os_text, os_conf = ("", "")
        if self.do_os_fp and ttl is not None:
            os_text, os_conf = _os_from_ttl(ttl)
        return {
            "target": target, "ip": ip, "port": port, "proto": proto,
            "status": status, "elapsed": elapsed,
            "banner": banner or "",
            "service": svc, "product": product, "version": version,
            "ttl": ttl, "os_guess": os_text, "os_confidence": os_conf,
            "error": err or "", "scan_mode": self.scan_mode,
        }

    def _run_syn_batch(self, resolved, t_start, results):
        total = len(resolved) * len(self.ports)
        done = 0

        for target, ip in resolved:
            if self._stop:
                break

            try:
                self.log_msg.emit(
                    f"[>] SYN batch to {ip} ({len(self.ports)} ports)")
            except RuntimeError:
                pass

            t_batch = time.time()
            syn_map = _tcp_syn_batch(
                ip, self.ports, self.timeout,
                stop_check=lambda: self._stop,
            )
            batch_elapsed = (time.time() - t_batch) * 1000

            for port in self.ports:
                if self._stop:
                    break
                info = syn_map.get(port) or {
                    "status": "filtered", "ttl": None, "error": ""}
                r = self._make_result(
                    target, ip, port, "tcp",
                    info["status"], batch_elapsed / max(1, len(self.ports)),
                    "", info.get("ttl"), info.get("error", ""))
                results.append(r)
                if r["status"] != "closed":
                    try:
                        self.found.emit(r)
                    except RuntimeError:
                        pass
                done += 1
                if done % 20 == 0 or done == total:
                    try:
                        self.progress.emit(
                            done, total, time.time() - t_start)
                    except RuntimeError:
                        pass

            if self.do_udp:
                for port in self.ports:
                    if self._stop:
                        break
                    if HAS_SCAPY:
                        status, elapsed, banner, err = _udp_probe_scapy(
                            ip, port, self.timeout)
                    else:
                        status, elapsed, banner, err = _udp_probe(
                            ip, port, self.timeout)
                    r = self._make_result(
                        target, ip, port, "udp",
                        status, elapsed, banner, None, err)
                    results.append(r)
                    if r["status"] != "closed":
                        try:
                            self.found.emit(r)
                        except RuntimeError:
                            pass
                    done += 1
                    if done % 20 == 0 or done == total:
                        try:
                            self.progress.emit(
                                done, total, time.time() - t_start)
                        except RuntimeError:
                            pass

        self.finished_scan.emit(results, time.time() - t_start)

    def _run_pool(self, resolved, t_start, results):
        jobs = []
        for t, ip in resolved:
            for p in self.ports:
                jobs.append((t, ip, p, "tcp"))
                if self.do_udp:
                    jobs.append((t, ip, p, "udp"))

        total = len(jobs)
        done = 0

        def one(target, ip, port, proto):
            if self._stop:
                return None
            if proto == "tcp":
                status, elapsed, banner, ttl, err = _tcp_connect(
                    ip, port, self.timeout, self.do_banner)
            else:
                if HAS_SCAPY:
                    status, elapsed, banner, err = _udp_probe_scapy(
                        ip, port, self.timeout)
                else:
                    status, elapsed, banner, err = _udp_probe(
                        ip, port, self.timeout)
                ttl = None
            return self._make_result(
                target, ip, port, proto,
                status, elapsed, banner, ttl, err)

        with ThreadPoolExecutor(max_workers=self.threads) as ex:
            futures = [ex.submit(one, *j) for j in jobs]
            for fut in as_completed(futures):
                if self._stop:
                    for f in futures:
                        f.cancel()
                    break
                done += 1
                if done % 20 == 0 or done == total:
                    try:
                        self.progress.emit(
                            done, total, time.time() - t_start)
                    except RuntimeError:
                        pass
                try:
                    res = fut.result()
                except Exception:
                    res = None
                if res:
                    results.append(res)
                    if res["status"] != "closed":
                        try:
                            self.found.emit(res)
                        except RuntimeError:
                            pass

        self.finished_scan.emit(results, time.time() - t_start)


class PortScannerModule(BaseModule):
    def on_load(self, api):
        api.register_service(self.name, "portscan.run", self._svc_run,
                             "portscan.run(host, ports, threads=200, timeout=1.0, "
                             "do_udp=False, do_banner=True, scan_mode='connect', "
                             "do_os_fp=False, caller=None, session=None) -> list")
        api.register_service(self.name, "portscan.capabilities",
                             self._svc_capabilities,
                             "portscan.capabilities() -> dict")

    def _svc_capabilities(self):
        return {
            "syn": CAN_SYN,
            "os_fp": CAN_OS_FP,
            "udp_accurate": True,
        }

    def _svc_run(self, host, ports, threads=200, timeout=1.0,
                 do_udp=False, do_banner=True, scan_mode="connect",
                 do_os_fp=False, caller=None, session=None):
        if isinstance(ports, str):
            ports = _parse_port_spec(ports)
        if not ports:
            return []
        if isinstance(host, str):
            targets = _parse_targets(host)
        else:
            targets = list(host)

        resolved = []
        for t in targets:
            ip = _resolve(t)
            if ip:
                resolved.append((t, ip))
        if not resolved:
            return []

        if scan_mode == "syn" and not CAN_SYN:
            scan_mode = "connect"

        results = []

        if scan_mode == "syn":
            for target, ip in resolved:
                syn_map = _tcp_syn_batch(ip, ports, timeout)
                for port in ports:
                    info = syn_map.get(port) or {
                        "status": "filtered", "ttl": None, "error": ""}
                    ttl = info.get("ttl")
                    os_text, os_conf = ("", "")
                    if do_os_fp and ttl is not None:
                        os_text, os_conf = _os_from_ttl(ttl)
                    svc, product, version = _detect_service(port, "")
                    r = {
                        "target": target, "ip": ip, "port": port,
                        "proto": "tcp", "status": info["status"],
                        "elapsed": timeout * 1000,
                        "banner": "",
                        "service": svc, "product": product,
                        "version": version,
                        "ttl": ttl, "os_guess": os_text,
                        "os_confidence": os_conf,
                        "error": info.get("error", ""),
                        "scan_mode": "syn",
                    }
                    if r["status"] != "closed":
                        results.append(r)
                if do_udp:
                    for port in ports:
                        if HAS_SCAPY:
                            status, elapsed, banner, err = _udp_probe_scapy(
                                ip, port, timeout)
                        else:
                            status, elapsed, banner, err = _udp_probe(
                                ip, port, timeout)
                        svc, product, version = _detect_service(port, banner)
                        r = {
                            "target": target, "ip": ip, "port": port,
                            "proto": "udp", "status": status,
                            "elapsed": elapsed, "banner": banner,
                            "service": svc, "product": product,
                            "version": version,
                            "ttl": None, "os_guess": "", "os_confidence": "",
                            "error": err, "scan_mode": "syn",
                        }
                        if r["status"] != "closed":
                            results.append(r)
        else:
            def one(target, ip, port, proto):
                if proto == "tcp":
                    status, elapsed, banner, ttl, err = _tcp_connect(
                        ip, port, timeout, do_banner)
                else:
                    if HAS_SCAPY:
                        status, elapsed, banner, err = _udp_probe_scapy(
                            ip, port, timeout)
                    else:
                        status, elapsed, banner, err = _udp_probe(
                            ip, port, timeout)
                    ttl = None
                svc, product, version = _detect_service(port, banner)
                os_text, os_conf = ("", "")
                if do_os_fp and ttl is not None:
                    os_text, os_conf = _os_from_ttl(ttl)
                return {
                    "target": target, "ip": ip, "port": port,
                    "proto": proto, "status": status,
                    "elapsed": elapsed, "banner": banner,
                    "service": svc, "product": product,
                    "version": version, "ttl": ttl,
                    "os_guess": os_text, "os_confidence": os_conf,
                    "error": err, "scan_mode": scan_mode,
                }

            jobs = []
            for t, ip in resolved:
                for p in ports:
                    jobs.append((t, ip, p, "tcp"))
                    if do_udp:
                        jobs.append((t, ip, p, "udp"))

            with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
                for fut in as_completed([ex.submit(one, *j) for j in jobs]):
                    try:
                        r = fut.result()
                    except Exception:
                        continue
                    if r["status"] != "closed":
                        results.append(r)

        try:
            mod_label = caller or self.name
            host_label = host if isinstance(host, str) else ",".join(host)
            get_history().add(
                module=mod_label, session=session, method="SCAN",
                url=f"{host_label}  ({len(ports)} ports, {scan_mode})",
                headers={}, body="", status=len(results),
                response_body=json.dumps(results[:50], default=str)[:4000],
                elapsed=0.0)
        except Exception:
            pass

        return results

    def __init__(self):
        self._widget = None
        self._results = []
        self.worker = None
        self._lbls = {}
        self._btns = {}
        self._filter_key = "all"

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
        self._lbls["targets"] = QLabel()
        self._lbls["targets"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row1.addWidget(self._lbls["targets"])
        self.targets_input = QLineEdit("127.0.0.1")
        self.targets_input.setFont(mono)
        row1.addWidget(self.targets_input, 1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self._lbls["ports"] = QLabel()
        self._lbls["ports"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row2.addWidget(self._lbls["ports"])
        self.ports_input = QLineEdit("")
        self.ports_input.setFont(mono)
        row2.addWidget(self.ports_input, 1)

        self._lbls["preset"] = QLabel()
        self._lbls["preset"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row2.addWidget(self._lbls["preset"])
        self.preset_box = QComboBox()
        self.preset_box.setFixedWidth(160)
        self.preset_box.currentIndexChanged.connect(self._on_preset_changed)
        row2.addWidget(self.preset_box)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        self._lbls["threads"] = QLabel()
        self._lbls["threads"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row3.addWidget(self._lbls["threads"])
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 1000)
        self.threads_spin.setValue(200)
        self.threads_spin.setFixedWidth(80)
        row3.addWidget(self.threads_spin)

        self._lbls["timeout"] = QLabel()
        self._lbls["timeout"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row3.addWidget(self._lbls["timeout"])
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 30.0)
        self.timeout_spin.setValue(1.0)
        self.timeout_spin.setSingleStep(0.1)
        self.timeout_spin.setFixedWidth(90)
        row3.addWidget(self.timeout_spin)

        self._lbls["mode"] = QLabel()
        self._lbls["mode"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row3.addWidget(self._lbls["mode"])
        self.mode_box = QComboBox()
        self.mode_box.setFixedWidth(150)
        self.mode_box.addItem("TCP connect", "connect")
        if CAN_SYN:
            self.mode_box.addItem("TCP SYN", "syn")
        row3.addWidget(self.mode_box)

        row3.addStretch()
        layout.addLayout(row3)

        row4 = QHBoxLayout()
        self.udp_chk = QCheckBox()
        row4.addWidget(self.udp_chk)

        self.banner_chk = QCheckBox()
        self.banner_chk.setChecked(True)
        row4.addWidget(self.banner_chk)

        self.osfp_chk = QCheckBox()
        self.osfp_chk.setEnabled(CAN_OS_FP)
        row4.addWidget(self.osfp_chk)

        self._lbls["filter"] = QLabel()
        self._lbls["filter"].setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        row4.addWidget(self._lbls["filter"])
        self.filter_box = QComboBox()
        self.filter_box.setFixedWidth(160)
        self.filter_box.currentIndexChanged.connect(self._on_filter_changed)
        row4.addWidget(self.filter_box)

        row4.addStretch()

        self.run_btn = QPushButton()
        self.run_btn.setIcon(theme_loader.get_icon(
            icon_name="play", emoji=">", size=14))
        self.run_btn.setFixedWidth(150)
        self.run_btn.clicked.connect(self._start)
        row4.addWidget(self.run_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setFixedWidth(110)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        row4.addWidget(self.stop_btn)

        self.save_btn = QPushButton()
        self.save_btn.setFixedWidth(130)
        self.save_btn.clicked.connect(self._save)
        row4.addWidget(self.save_btn)

        layout.addLayout(row4)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.summary = QLabel("-")
        self.summary.setStyleSheet(
            f"color: {_c('fg_dim')}; font-size: 11px;")
        layout.addWidget(self.summary)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        h = self.table.horizontalHeader()
        for i in range(7):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        h.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.table, 1)

        self.log = QPlainTextEdit()
        self.log.setFont(mono)
        self.log.setReadOnly(True)
        self.log.setFixedHeight(80)
        layout.addWidget(self.log)

        self._rebuild_presets()
        self._rebuild_filter()
        self.retranslate()
        return root

    def _rebuild_presets(self):
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self.preset_box.addItem("—", "")
        for key, label in (
            ("top-100", "top-100"),
            ("web", "web"),
            ("databases", "databases"),
            ("mail", "mail"),
            ("common", "common"),
            ("1-1024", "1-1024"),
            ("all", "1-65535"),
        ):
            self.preset_box.addItem(label, key)
        self.preset_box.blockSignals(False)
        self.preset_box.setCurrentIndex(1)
        self._on_preset_changed()

    def _rebuild_filter(self):
        self.filter_box.blockSignals(True)
        self.filter_box.clear()
        self.filter_box.addItem("", "all")
        self.filter_box.addItem("", "open")
        self.filter_box.addItem("", "open_filtered")
        self.filter_box.addItem("", "problems")
        self.filter_box.blockSignals(False)
        self.filter_box.setCurrentIndex(0)

    def _on_preset_changed(self):
        key = self.preset_box.currentData()
        if not key:
            return
        if key == "all":
            self.ports_input.setText("1-65535")
            return
        ports = PRESETS.get(key, [])
        if ports:
            self.ports_input.setText(",".join(str(p) for p in ports))

    def _on_filter_changed(self):
        self._filter_key = self.filter_box.currentData() or "all"
        self._repopulate_table()

    def retranslate(self):
        def t(k, **kw):
            return tr(k, module="port_scanner", **kw)
        if not hasattr(self, "targets_input"):
            return
        self._lbls["targets"].setText(t("targets"))
        self.targets_input.setPlaceholderText(t("targets.placeholder"))
        self._lbls["ports"].setText(t("ports"))
        self.ports_input.setPlaceholderText(t("ports.placeholder"))
        self._lbls["preset"].setText(t("preset"))
        self._lbls["threads"].setText(t("threads"))
        self._lbls["timeout"].setText(t("timeout"))
        self._lbls["mode"].setText(t("mode"))
        self._lbls["filter"].setText(t("filter"))
        self.udp_chk.setText(t("udp"))
        self.banner_chk.setText(t("banner"))
        self.osfp_chk.setText(t("os_fp"))
        self.run_btn.setText(t("run"))
        self.stop_btn.setText(t("stop"))
        self.save_btn.setText(t("save"))

        cur_filter = self.filter_box.currentIndex()
        self.filter_box.blockSignals(True)
        self.filter_box.setItemText(0, t("filter.all"))
        self.filter_box.setItemText(1, t("filter.open"))
        self.filter_box.setItemText(2, t("filter.open_filtered"))
        self.filter_box.setItemText(3, t("filter.problems"))
        self.filter_box.setCurrentIndex(cur_filter)
        self.filter_box.blockSignals(False)

        self.table.setHorizontalHeaderLabels([
            t("col.host"), t("col.port"), t("col.proto"),
            t("col.status"), t("col.service"),
            t("col.banner"), t("col.os"), t("col.time"),
        ])
        if not self._results and not (self.worker and self.worker.isRunning()):
            self.summary.setText(t("summary.idle"))

    def _current_scan_mode(self):
        key = self.mode_box.currentData()
        if key in ("connect", "syn") and (key != "syn" or CAN_SYN):
            return key
        return "connect"

    def _start(self):
        targets = _parse_targets(self.targets_input.text())
        ports = _parse_port_spec(self.ports_input.text())

        if not targets:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="port_scanner"),
                                tr("err.no_targets", module="port_scanner"))
            return
        if not ports:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="port_scanner"),
                                tr("err.no_ports", module="port_scanner"))
            return
        if self.worker is not None and self.worker.isRunning():
            return

        scan_mode = self._current_scan_mode()
        do_os_fp = self.osfp_chk.isChecked() and CAN_OS_FP

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self._results = []
        self.log.clear()

        est_jobs = len(targets) * len(ports) * (
            2 if self.udp_chk.isChecked() else 1)
        self.log.appendPlainText(tr(
            "log.start", module="port_scanner",
            targets=len(targets), ports=len(ports),
            jobs=est_jobs,
            threads=self.threads_spin.value()) + f"  mode={scan_mode}")

        self.progress.setRange(0, est_jobs)
        self.progress.setValue(0)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self.worker = PortScanWorker(
            targets=targets,
            ports=ports,
            threads=self.threads_spin.value(),
            timeout=self.timeout_spin.value(),
            do_udp=self.udp_chk.isChecked(),
            do_banner=self.banner_chk.isChecked(),
            scan_mode=scan_mode,
            do_os_fp=do_os_fp,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.found.connect(self._on_found)
        self.worker.log_msg.connect(self._on_log)
        self.worker.finished_scan.connect(self._on_done)
        self.worker.start()

    def _stop(self):
        if self.worker:
            self.worker.stop()
            try:
                self.log.appendPlainText(
                    tr("log.stopping", module="port_scanner"))
            except RuntimeError:
                pass

    def _on_progress(self, done, total, elapsed):
        try:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            if done > 0 and elapsed > 0:
                rate = done / elapsed
                remaining = (total - done) / rate if rate > 0 else 0
                self.progress.setFormat(
                    f"{done}/{total}  ·  {rate:.0f} p/s  ·  ETA {remaining:.0f}s")
            else:
                self.progress.setFormat(f"{done}/{total}")
        except RuntimeError:
            return

    def _on_log(self, msg):
        try:
            self.log.appendPlainText(msg)
        except RuntimeError:
            return

    def _matches_filter(self, r):
        key = self._filter_key
        if key == "all":
            return True
        status = r.get("status")
        if key == "open":
            return status == "open"
        if key == "open_filtered":
            return status in ("open", "open|filtered")
        if key == "problems":
            return bool(r.get("error")) or status not in ("open", "open|filtered")
        return True

    def _on_found(self, r):
        try:
            self._results.append(r)
            if not self._matches_filter(r):
                return
            self._insert_row(r)
        except RuntimeError:
            return

    def _insert_row(self, r):
        row = self.table.rowCount()
        self.table.insertRow(row)

        host_disp = r["target"]
        if r["ip"] and r["ip"] != r["target"]:
            host_disp = f"{r['target']} ({r['ip']})"
        self.table.setItem(row, 0, QTableWidgetItem(host_disp))

        port_item = NumericItem(str(r["port"]), int(r["port"]))
        self.table.setItem(row, 1, port_item)
        self.table.setItem(row, 2, QTableWidgetItem(r["proto"].upper()))

        status_item = QTableWidgetItem(r["status"])
        s = r["status"]
        if s == "open":
            status_item.setForeground(QColor(_c("success")))
        elif s == "open|filtered":
            status_item.setForeground(QColor(_c("warning")))
        elif s == "closed":
            status_item.setForeground(QColor(_c("fg_dark")))
        else:
            status_item.setForeground(QColor(_c("fg_dim")))
        self.table.setItem(row, 3, status_item)

        svc_text = "-"
        if r.get("product"):
            svc_text = r["product"]
            if r.get("version"):
                svc_text += f" {r['version']}"
            elif r.get("service") and r["service"] != r["product"].lower():
                svc_text += f" ({r['service']})"
        elif r.get("service"):
            svc_text = r["service"]
        self.table.setItem(row, 4, QTableWidgetItem(svc_text))

        banner = r.get("banner") or ""
        banner_item = QTableWidgetItem(banner[:200])
        if banner:
            banner_item.setToolTip(banner)
        self.table.setItem(row, 5, banner_item)

        os_text = r.get("os_guess") or ""
        if not os_text and r.get("ttl") is not None:
            os_text = f"ttl={r['ttl']}"
        os_item = QTableWidgetItem(os_text or "-")
        conf = r.get("os_confidence")
        if conf == "high":
            os_item.setForeground(QColor(_c("success")))
        elif conf == "medium":
            os_item.setForeground(QColor(_c("warning")))
        elif conf == "low":
            os_item.setForeground(QColor(_c("fg_dim")))
        if r.get("ttl") is not None:
            os_item.setToolTip(f"ttl={r['ttl']}  confidence={conf or 'n/a'}")
        self.table.setItem(row, 6, os_item)

        ms = float(r.get("elapsed") or 0)
        time_item = NumericItem(f"{ms:.0f} ms", ms)
        self.table.setItem(row, 7, time_item)

    def _repopulate_table(self):
        try:
            header = self.table.horizontalHeader()
            sort_col = header.sortIndicatorSection()
            sort_order = header.sortIndicatorOrder()

            self.table.setSortingEnabled(False)
            self.table.setRowCount(0)
            for r in self._results:
                if not self._matches_filter(r):
                    continue
                self._insert_row(r)
            self.table.setSortingEnabled(True)
            if sort_col >= 0:
                self.table.sortItems(sort_col, sort_order)
        except RuntimeError:
            return

    def _on_done(self, results, duration):
        self._results = results
        try:
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.table.setSortingEnabled(True)
            self._repopulate_table()
        except RuntimeError:
            pass

        open_count = sum(1 for r in results if r["status"] == "open")
        filtered_count = sum(1 for r in results
                             if r["status"] in ("filtered", "open|filtered"))
        closed_count = sum(1 for r in results if r["status"] == "closed")

        os_guesses = [r.get("os_guess") for r in results
                      if r.get("os_guess") and r.get("os_confidence") in ("high", "medium")]
        os_summary = ""
        if os_guesses:
            from collections import Counter
            os_summary = "   OS: " + ", ".join(
                f"{k}×{v}" for k, v in Counter(os_guesses).most_common(3))

        try:
            self.summary.setText(
                tr("summary.done", module="port_scanner",
                   open=open_count, filtered=filtered_count,
                   closed=closed_count, total=len(results))
                + f"   ·   {duration:.2f}s" + os_summary)
            self.log.appendPlainText(
                tr("log.done", module="port_scanner",
                   open=open_count, total=len(results))
                + f"  ({duration:.2f}s)" + os_summary)
        except RuntimeError:
            pass

        try:
            get_bus().call("vault.set", "portscan.last",
                           json.dumps([r for r in results
                                       if r["status"] == "open"],
                                      ensure_ascii=False))
        except Exception:
            pass

    def _selected_result(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return None
        row = sel[0].row()
        host_item = self.table.item(row, 0)
        port_item = self.table.item(row, 1)
        proto_item = self.table.item(row, 2)
        if not host_item or not port_item:
            return None
        host_text = host_item.text()
        if " (" in host_text:
            host_text = host_text.split(" (", 1)[0]
        try:
            port = int(port_item.text())
        except Exception:
            return None
        proto = proto_item.text().lower() if proto_item else "tcp"
        return {"host": host_text, "port": port, "proto": proto}

    def _context_menu(self, pos):
        sel = self._selected_result()
        if not sel:
            return
        menu = QMenu(self._widget)

        copy_hp = menu.addAction(
            tr("ctx.copy_hostport", module="port_scanner"))
        copy_hp.triggered.connect(
            lambda: QGuiApplication.clipboard().setText(
                f"{sel['host']}:{sel['port']}"))

        copy_nmap = menu.addAction(
            tr("ctx.copy_nmap", module="port_scanner"))
        copy_nmap.triggered.connect(
            lambda: QGuiApplication.clipboard().setText(
                f"nmap -p {sel['port']} -sV {sel['host']}"))

        open_browser = None
        if sel["port"] in WEB_PORTS:
            open_browser = menu.addAction(
                tr("ctx.open_browser", module="port_scanner"))

        menu.addSeparator()

        rescan = menu.addAction(
            tr("ctx.rescan", module="port_scanner"))
        rescan.triggered.connect(lambda: self._rescan(sel["port"]))

        menu.addSeparator()

        copy_row = menu.addAction(
            tr("ctx.copy_row", module="port_scanner"))
        copy_row.triggered.connect(lambda: self._copy_selected_row())

        copy_banner = menu.addAction(
            tr("ctx.copy_banner", module="port_scanner"))
        copy_banner.triggered.connect(lambda: self._copy_selected_banner())

        action = menu.exec_(self.table.mapToGlobal(pos))

        if action is open_browser and open_browser is not None:
            scheme = "https" if sel["port"] in (443, 8443, 9443) else "http"
            QDesktopServices.openUrl(
                QUrl(f"{scheme}://{sel['host']}:{sel['port']}/"))

    def _rescan(self, port):
        self.ports_input.setText(str(port))
        self.preset_box.setCurrentIndex(0)
        self._start()

    def _copy_selected_row(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        values = [self.table.item(row, c).text() if self.table.item(row, c)
                  else "" for c in range(self.table.columnCount())]
        QGuiApplication.clipboard().setText("  ".join(values))

    def _copy_selected_banner(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        item = self.table.item(sel[0].row(), 5)
        if item:
            QGuiApplication.clipboard().setText(item.text())

    def _save(self):
        if not self._results:
            return
        path, _ = QFileDialog.getSaveFileName(
            parent_widget(self._widget),
            tr("save.title", module="port_scanner"),
            "portscan_results.json",
            "JSON (*.json);;CSV (*.csv);;Text (*.txt)")
        if not path:
            return
        try:
            if path.endswith(".csv"):
                with open(path, "w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["target", "ip", "port", "proto",
                                "status", "service", "product", "version",
                                "banner", "ttl", "os_guess",
                                "os_confidence", "ms"])
                    for r in self._results:
                        w.writerow([
                            r["target"], r["ip"], r["port"], r["proto"],
                            r["status"], r.get("service", ""),
                            r.get("product", ""), r.get("version", ""),
                            r["banner"], r.get("ttl") or "",
                            r.get("os_guess") or "",
                            r.get("os_confidence") or "",
                            f"{r.get('elapsed') or 0:.0f}"])
            elif path.endswith(".txt"):
                with open(path, "w", encoding="utf-8") as f:
                    for r in self._results:
                        line = (f"{r['target']}:{r['port']}/{r['proto']}  "
                                f"{r['status']}  ")
                        if r.get("product"):
                            line += r["product"]
                            if r.get("version"):
                                line += f" {r['version']}"
                        else:
                            line += r.get("service") or "-"
                        if r.get("banner"):
                            line += f"  | {r['banner'][:80]}"
                        if r.get("os_guess"):
                            line += f"  [{r['os_guess']}]"
                        f.write(line + "\n")
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self._results, f, ensure_ascii=False, indent=2)
            self.log.appendPlainText(tr(
                "log.saved", module="port_scanner", path=path))
        except Exception as e:
            QMessageBox.warning(parent_widget(self._widget),
                                tr("err.title", module="port_scanner"),
                                str(e))