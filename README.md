# SWARM / O.N.Y.X.

```
███████╗██╗    ██╗ █████╗ ██████╗ ███╗   ███╗
██╔════╝██║    ██║██╔══██╗██╔══██╗████╗ ████║
███████╗██║ █╗ ██║███████║██████╔╝██╔████╔██║
╚════██║██║███╗██║██╔══██║██╔══██╗██║╚██╔╝██║
███████║╚███╔███╔╝██║  ██║██║  ██║██║ ╚═╝ ██║
╚══════╝ ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝
```

> **O**rchestrated **N**etwork **Y**ield e**X**tensible
> модульный инструмент для анализа сетей

**RU** | [EN](README.EN.md)

---

## О проекте

O.N.Y.X. — десктопное приложение, которое объединяет в одной оболочке инструменты, обычно существующие по отдельности: HTTP-клиент, сканер портов и директорий, менеджер прокси, JWT-мастерскую, кодировщик, diff, шифрованное хранилище секретов и систему запуска коллекций запросов. У всех модулей — общий UI, общая история запросов, общий vault и общая шина событий.

---

## Модули

| Модуль | Назначение |
|---|---|
| **HTTP Client** | HTTP-запросы: три движка (requests, curl_cffi, cloudscraper), cookie-jar, redirect chain, hex-дамп ответов, HTML-дерево, профили, curl import/export |
| **Port Scanner** | TCP/UDP-скан портов: SYN-режим, OS fingerprinting по TTL, баннер-граббинг, определение сервисов (OpenSSH, nginx, uvicorn, mysql и др.) |
| **DNS Lookup** | Резолв DNS: A, AAAA, CNAME, MX, NS, TXT, SOA, SRV, CAA, PTR. Свой nameserver, IPv4/IPv6, reverse lookup для IP, экспорт JSON/CSV/TXT |
| **Scanner** | Брутфорс директорий: wildcard-фильтр soft-404, extension-фаззинг, ротация User-Agent, поддержка прокси, экспорт результатов |
| **Collections** | Наборы запросов: ассерты (status, body, JSON path, time), extract в vault, retry/delay, conditional execution, параллельность, data-driven прогон из CSV/JSON, CLI runner, auto re-auth при 401 с TOTP |
| **Login Flow** | Профили логина: JSON / form-data / Basic / OAuth2 (client_credentials, password, refresh_token), двухшаговый flow с 2FA, автоопределение токена в ответе, TOTP, CAPTCHA-хук |
| **JWT Decoder** | Разбор JWT, проверка подписи HS256/384/512, brute-force секрета, генерация токенов, none-attack, live-таймер истечения |
| **Vault** | Шифрованное хранилище Fernet: TTL, namespaces, ротация ключа, bulk import/export, поиск |
| **Encoder / Decoder** | Pipeline из 31 операции: Base64/32/58, URL, Hex, HTML entities, ROT13, Unicode escape, Gzip, Zlib, XOR, MD5/SHA1/SHA256/SHA512, автоопределение входа |
| **Diff** | Сравнение текстов: unified, side-by-side, word-level; режимы JSON / XML / YAML; опции ignore whitespace / case / dynamic keys |
| **Proxy Manager** | Список прокси с параллельной проверкой, автоотключением мёртвых, статистикой latency, ротацией, import/export |
| **Reporter** | Отчёты Markdown / HTML / JSON / PDF с фильтрами по модулю и сессии, график статусов, snapshot-сравнение прогонов |
| **System Test** | Сквозная самопроверка: 100+ шагов со встроенным Flask-сервером и тестовым HTTP-прокси |

---

## Установка

```bash
pip install -r requirements.txt
python main.py
```

Python 3.10+.

**Windows.** Установить Npcap с https://npcap.com/#download, отметив **Install Npcap in WinPcap API-compatible Mode**.

---

## Структура

```
.
├── main.py
├── requirements.txt
├── config/                # runtime: vault, history.db, профили, коллекции
│
├── core/                  # ядро
│   ├── main_window.py     # меню и навигация
│   ├── module_api.py      # шина сервисов и событий
│   ├── module_loader.py   # загрузка модулей и разрешение зависимостей
│   ├── module_spec.py     # чтение metadata.json
│   ├── module_registry.py # реестр включённых модулей
│   ├── base_module.py     # базовый класс
│   ├── history.py         # SQLite-история запросов
│   ├── i18n.py            # локализация
│   ├── settings.py        # настройки
│   ├── splash.py          # заставка
│   ├── utils.py           # хелперы
│   └── *_dialog.py        # диалоги
│
├── modules/               # модули
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
├── styles/                # темы (matrix, onyx)
│   ├── loader.py
│   ├── matrix/
│   └── onyx/
│
└── langs/                 # общие переводы
    ├── en_US.json
    └── ru_RU.json
```

---

## Написание модуля

Модуль — папка внутри `modules/`:

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

`dependencies` — модуль не загрузится без них. `optional_dependencies` — не загрузится только он сам, но остальные модули не пострадают. Система выстраивает порядок загрузки автоматически, разрешает цепочки зависимостей и блокирует включение модуля, если его зависимость отключена.

### module.py

Класс — наследник `BaseModule`. Один экземпляр на всё время работы приложения.

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

| Метод | Когда вызывается |
|---|---|
| `on_load(api)` | при загрузке модуля — регистрация сервисов, подписки на события |
| `create_widget()` | при первом открытии модуля — возвращает QWidget |
| `retranslate()` | при смене языка |
| `on_unload(api)` | при выгрузке модуля |
| `on_shutdown()` | при закрытии приложения |

Виджет создаётся один раз и кэшируется. Открытие/закрытие модуля не сбрасывает его состояние.

### langs/*.json

```json
{
  "name": "My Module",
  "description": "What this module does.",
  "hello": "Hello, world!"
}
```

Обращение: `tr("hello", module="my_module")`. Если ключа нет в текущей локали — подставляется `en_US`.

### Взаимодействие с другими модулями

**Регистрация сервиса** — функция становится доступна другим:

```python
api.register_service(
    "my_module", "my_module.greet",
    lambda name: f"Hello, {name}!",
    "my_module.greet(name) -> str"
)
```

**Вызов чужого сервиса:**

```python
token = api.call("vault.get", "my_token")

resp = api.call("http.request", "GET", "https://api.example.com/me",
                headers={"Authorization": f"Bearer {token}"},
                body="", timeout=30)
```

**События** — публикация и подписка:

```python
api.emit_event("my_module.done", result="ok", count=42)

api.event.connect(self._on_event)

def _on_event(self, name, payload):
    if name == "vault.changed":
        ...
```

---

> Orchestrated · Network · Yield · eXtensible
> используйте ответственно