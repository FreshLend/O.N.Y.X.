# Security Policy

O.N.Y.X. is a **dual-use security toolkit** for authorized testing,
API analysis and security research. It includes port scanning, directory
brute-forcing, JWT attacks, credential flows and other modules that are
indistinguishable from attack tooling. Read this before you use it.

---

## Authorized use only

Use O.N.Y.X. **only** against systems you own or have explicit, written
permission to test (signed scope, bug bounty program, client contract).
Using it against third-party infrastructure without authorization is
illegal in most jurisdictions and is not a supported use case.

The maintainers provide no legal advice. Obtain it yourself before
using this tool professionally.

---

## No warranty, no liability

O.N.Y.X. is provided **"as is"**, without warranty of any kind. The
authors are not responsible for how the software is used, for the
targets chosen by the user, or for any damage, data loss, service
disruption, legal consequences or other harm caused by use or misuse
of this software. Responsibility for every action performed with
O.N.Y.X. lies entirely with the person running it.

This applies to all modules, including the offensive ones (scanners,
brute-forcers, JWT attack helpers). Their presence in the project does
not constitute an invitation, endorsement or instruction to use them
against any particular system.

---

## Built-in test server is intentionally insecure

The bundled test server, WebSocket test server and test proxy exist
only so the modules have something to test against. They contain
deliberate weaknesses: IDOR on note endpoints, a hard-coded weak JWT
secret, a fixed TOTP secret, plaintext passwords, no rate limiting,
and proxy behavior that forwards arbitrary URLs. These are **features
of the test harness, not bugs**, and must never be exposed on a public
interface. They bind to `127.0.0.1` by design — do not change that.

Any local process on the same machine can talk to these test services,
so stop them when you are not actively testing (the application does
this automatically on shutdown; keep that behavior in forks).

---

## What O.N.Y.X. does not protect you from

- **Vault** encrypts secrets with Fernet and keeps the key in the OS
  keyring or a local key file. If `cryptography` is not installed, the
  vault falls back to **plaintext**. It is a convenience store for test
  tokens, **not** a production secrets manager.
- **HTTP history** stores request and response bodies (including
  `Authorization` headers) in an **unencrypted** SQLite file under
  `config/`. Clear history after testing sensitive targets.
- **Proxies** configured through Proxy Manager see all traffic from the
  HTTP client, WebSocket client, scanner and Collections runner. Only
  trust proxies you control.
- **Third-party dependencies** are used as-is. Supply-chain risk is not
  mitigated by O.N.Y.X.

If you need a reduced-risk setup, skip `scapy` (disables SYN/UDP
scans), keep the proxy disabled unless needed, and verify
`vault.stats()` reports `mode: "encrypted"` before storing anything
sensitive.