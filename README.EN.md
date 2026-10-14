# SWARM / O.N.Y.X.

> **O**rchestrated **N**etwork **Y**ield e**X**tensible  
> a modular toolkit for network analysis

[RU](README.md) | **EN**

---

## About

O.N.Y.X. is a desktop application that brings together tools that usually exist separately: an HTTP client, port and directory scanners, a proxy manager, a JWT workbench, an encoder, a diff tool, an encrypted secret vault, and a request-collection runner. Every module shares the same UI, request history, vault, and event bus.

---

## Modules

| Module | Purpose |
|---|---|
| **HTTP Client** | HTTP requests with three engines (requests, curl_cffi, cloudscraper), cookie jar, redirect chain, hex dump of responses, HTML tree, profiles, curl import/export |
| **Port Scanner** | TCP/UDP port scan: SYN mode, OS fingerprinting via TTL, banner grabbing, service detection (OpenSSH, nginx, uvicorn, mysql, and more) |
| **DNS Lookup** | DNS resolution: A, AAAA, CNAME, MX, NS, TXT, SOA, SRV, CAA, PTR. Custom nameserver, IPv4/IPv6, reverse lookup for IPs, export to JSON/CSV/TXT |
| **Scanner** | Directory brute-force: soft-404 wildcard filter, extension fuzzing, User-Agent rotation, proxy support, result export |
| **Collections** | Request sets with assertions (status, body, JSON path, time), extract to vault, retry/delay, conditional execution, parallel runs, data-driven runs from CSV/JSON, CLI runner, auto re-auth on 401 with TOTP |
| **Login Flow** | Login profiles: JSON / form-data / Basic / OAuth2 (client_credentials, password, refresh_token), two-step 2FA flow, token auto-detection in responses, TOTP, CAPTCHA hook |
| **JWT Decoder** | JWT parsing, HS256/384/512 signature verification, secret brute-force, token generation, none-attack, live expiry timer |
| **Vault** | Encrypted Fernet vault: TTL, namespaces, key rotation, bulk import/export, search |
| **Encoder / Decoder** | Pipeline of 31 operations: Base64/32/58, URL, Hex, HTML entities, ROT13, Unicode escape, Gzip, Zlib, XOR, MD5/SHA1/SHA256/SHA512, auto-detection of input |
| **Diff** | Text comparison: unified, side-by-side, word-level; JSON / XML / YAML modes; ignore whitespace / case / dynamic keys |
| **Proxy Manager** | Proxy list with parallel checks, dead-proxy auto-disable, latency stats, rotation, import/export |
| **Reporter** | Reports in Markdown / HTML / JSON / PDF with filters by module and session, status chart, snapshot comparison of runs |
| **System Test** | End-to-end self-check: 100+ steps with embedded Flask server and test HTTP proxy |

---

## Installation

```bash
pip install -r requirements.txt
python main.py
```

Python 3.10+.

**Windows.** Install Npcap from https://npcap.com/#download, checking **Install Npcap in WinPcap API-compatible Mode**.

---

## Structure

```
.
├── main.py
├── requirements.txt
├── config/                # runtime: vault, history.db, profiles, collections
│
├── core/                  # core
│   ├── main_window.py     # menu and navigation
│   ├── module_api.py      # service and event bus
│   ├── module_loader.py   # module loading and dependency resolution
│   ├── module_spec.py     # metadata.json reader
│   ├── module_registry.py # registry of enabled modules
│   ├── base_module.py     # base class
│   ├── history.py         # SQLite request history
│   ├── i18n.py            # localization
│   ├── settings.py        # settings
│   ├── splash.py          # splash screen
│   ├── utils.py           # helpers
│   └── *_dialog.py        # dialogs
│
├── modules/               # modules
│   ├── http_client/
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
├── styles/                # themes (matrix, onyx)
│   ├── loader.py
│   ├── matrix/
│   └── onyx/
│
└── langs/                 # shared translations
    ├── en_US.json
    └── ru_RU.json
```

---

## Writing a module

A module is a folder inside `modules/`:

```
modules/my_module/
├── metadata.json
├── module.py
├── __init__.py
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
  "icon": "🚀",
  "dependencies": { "vault": ">=1.0.0" },
  "optional_dependencies": { "http_client": ">=1.0.0" }
}
```

`dependencies` — hard requirements; the module will not load without them. `optional_dependencies` — only this module is affected, the rest keep working. The system builds the load order automatically, resolves dependency chains, and blocks a module from being enabled if any of its dependencies are disabled.

### module.py

A class inheriting from `BaseModule`. One instance per application lifetime.

```python
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from core.base_module import BaseModule
from core.i18n import tr


class MyModule(BaseModule):
    def on_load(self, api):
        pass

    def on_shutdown(self):
        pass

    def create_widget(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(QLabel(tr("hello", module="my_module")))
        return root

    def retranslate(self):
        pass
```

| Method | When it is called |
|---|---|
| `on_load(api)` | when the module loads — register services, subscribe to events |
| `create_widget()` | on first open of the module — returns a QWidget |
| `retranslate()` | on language change |
| `on_unload(api)` | when the module is unloaded |
| `on_shutdown()` | when the application closes |

The widget is created once and cached. Opening/closing the module does not reset its state.

### langs/*.json

```json
{
  "name": "My Module",
  "description": "What this module does.",
  "hello": "Hello, world!"
}
```

Access via `tr("hello", module="my_module")`. If a key is missing in the current locale, `en_US` is used.

### Interacting with other modules

**Registering a service** — the function becomes available to others:

```python
api.register_service(
    "my_module", "my_module.greet",
    lambda name: f"Hello, {name}!",
    "my_module.greet(name) -> str"
)
```

**Calling someone else's service:**

```python
token = api.call("vault.get", "my_token")

resp = api.call("http.request", "GET", "https://api.example.com/me",
                headers={"Authorization": f"Bearer {token}"},
                body="", timeout=30)
```

**Events** — publishing and subscribing:

```python
api.emit_event("my_module.done", result="ok", count=42)

api.event.connect(self._on_event)

def _on_event(self, name, payload):
    if name == "vault.changed":
        ...
```

---

> Orchestrated · Network · Yield · eXtensible
> use responsibly