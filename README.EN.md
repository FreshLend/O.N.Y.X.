# [SWARM](https://github.com/FreshLend/SWARM) / O.N.Y.X. [RU](README.md) | **EN**

<p align="center">
  <img src="icon.png" alt="O.N.Y.X." width="256">
</p>

<h1 align="center">O.N.Y.X.</h1>

<p align="center">
  <em>Orchestrated · Network · Yield · eXtensible</em><br>
  a modular toolkit for network analysis
</p>

---

## About

O.N.Y.X. is a desktop application that brings together tools that usually exist separately: an HTTP client, port and directory scanners, a proxy manager, a JWT workbench, an encoder, a diff tool, an encrypted secret vault, and a request-collection runner.

---

## Modules

| Module | Purpose |
|---|---|
| **HTTP Client** | HTTP requests: three engines (requests, curl_cffi, cloudscraper), session manager with cookie editor and separate cookie-jar per engine, redirect chain, hex dump of responses, HTML tree, profiles, curl import/export, request cancel, TTFB |
| **WebSocket Client** | WebSocket sessions over ws:// and wss://, custom headers, subprotocols, text and binary frames, hex dump for binary, per-message details, ping/pong, history timeline, proxy support, `{{vault.key}}` in URL and headers |
| **Port Scanner** | TCP/UDP port scan: SYN mode, OS fingerprinting via TTL, banner grabbing, service detection (OpenSSH, nginx, uvicorn, mysql, etc.) |
| **DNS Lookup** | DNS resolution: A, AAAA, CNAME, MX, NS, TXT, SOA, SRV, CAA, PTR. Custom nameserver, IPv4/IPv6, reverse lookup for IPs, export to JSON/CSV/TXT |
| **Scanner** | Directory brute-force: soft-404 wildcard filter, extension fuzzing, User-Agent rotation, proxy support, result export |
| **Collections** | Request sets over HTTP and WebSocket: assertions (status, body, JSON path, time), extract to vault, retry/delay, conditional execution, bounded re-auth on 401 with TOTP, configurable parallel workers, per-collection cookie isolation, data-driven runs from CSV/JSON, CLI runner |
| **Login Flow** | Login profiles: JSON / form-data / Basic / OAuth2 (client_credentials, password, refresh_token), two-step 2FA flow, token auto-detection in responses, TOTP, CAPTCHA hook |
| **JWT Decoder** | JWT parsing, HS256/384/512 signature verification, secret brute-force, token generation, none-attack, live expiry timer |
| **Vault** | Encrypted Fernet vault: TTL, namespaces, key rotation, bulk import/export, search |
| **Encoder / Decoder** | Pipeline of 31 operations: Base64/32/58, URL, Hex, HTML entities, ROT13, Unicode escape, Gzip, Zlib, XOR, MD5/SHA1/SHA256/SHA512, auto-detection of input |
| **Diff** | Text comparison: unified, side-by-side, word-level; JSON / XML / YAML modes; ignore whitespace / case / dynamic keys |
| **Proxy Manager** | Proxy list with parallel checks, dead-proxy auto-disable, latency stats, rotation, import/export |
| **Reporter** | Reports in Markdown / HTML / JSON / PDF with filters by module and session, status chart, snapshot comparison of runs |
| **System Test** | End-to-end self-check: 100+ steps with embedded Flask server, test HTTP proxy and test WebSocket server |

---

## Installation

```bash
pip install -r requirements.txt
python main.py
```

Python 3.10+.

**Windows.** Install Npcap from https://npcap.com/#download, checking **Install Npcap in WinPCAP API-compatible Mode**.

---

## Structure

```
.
├── main.py
├── requirements.txt
├── config/                # runtime: vault, history.db, profiles, collections
│
├── core/                  # SWARM core
│   ├── brand.py           # branding (O.N.Y.X.)
│   ├── main_window.py     # menu and navigation
│   ├── module_api.py      # service and event bus
│   ├── module_loader.py   # module loading, Hot Reload and dependencies
│   ├── module_spec.py     # metadata.json reader
│   ├── module_registry.py # registry of enabled modules
│   ├── base_module.py     # base class
│   ├── history.py         # SQLite request history
│   ├── i18n.py            # localization
│   ├── settings.py        # settings
│   ├── splash.py          # splash screen
│   └── utils.py           # helpers
│
├── modules/               # modules
│   ├── http_client/
│   ├── websocket/
│   ├── port_scanner/
│   ├── dns_lookup/
│   ├── scanner/
│   ├── collections/
│   ├── login_flow/
│   ├── jwt_decoder/
│   ├── vault/
│   ├── encoder/
│   ├── diff/
│   ├── proxy_manager/
│   ├── reporter/
│   └── system_test/
│
├── styles/                # custom themes
│   ├── loader.py          # loader (built-in SWARM theme)
│   ├── matrix/
│   └── onyx/
│
└── langs/                 # shared core translations
    ├── en_US.json
    └── ru_RU.json
```

---

## Writing a module

A module is a folder inside `modules/`. Thanks to **Hot Reload**, you don't need to restart the whole app to see changes - just click "Modules" -> "Apply".

```
modules/my_module/
├── metadata.json
├── module.py
├── icon.svg               # (optional) custom icon
└── langs/
    ├── en_US.json
    └── ru_RU.json
```

### metadata.json

```json
{
  "name": "My Module",
  "version": "1.0.0",
  "author": "YourName",
  "description": "What this module does.",
  "icon_name": "module",
  "dependencies": { "vault": ">=1.0.0" }
}
```
*If you don't want to look for an icon, use the built-in ones (`module`, `play`, `globe`, `settings`, `code`). Or just drop an `icon.svg` file into the module folder - the core will pick it up automatically.*

### module.py

A class inheriting from `BaseModule`. One instance per application lifetime.

```python
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from core.base_module import BaseModule
from core.i18n import tr


class MyModule(BaseModule):
    def create_widget(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(QLabel(tr("hello", module="my_module")))
        return root
```

| Method | When it is called |
|---|---|
| `on_load(api)` | when the module loads - register services, subscribe to events |
| `create_widget()` | on first open of the module - returns a QWidget |
| `retranslate()` | on language change |
| `on_unload(api)` | when the module is unloaded |
| `on_shutdown()` | when the application closes |

### Interacting with other modules

**Registering a service:**
```python
api.register_service("my_module", "my_module.greet", lambda name: f"Hello, {name}!")
```

**Calling a service and events:**
```python
token = api.call("vault.get", "my_token")
api.emit_event("my_module.done", result="ok")
api.event.connect(self._on_event)
```

---
> Orchestrated · Network · Yield · eXtensible  
> use responsibly · [SECURITY.md](SECURITY.md)