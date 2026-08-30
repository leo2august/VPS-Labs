# VPS Sentinel Labs

Private **VPS observatory / admin dashboard** — a single dark, Japanese-modern web UI to watch system health, manage services, audit security, control model/provider routes, back up and restore, and more.

Clean, self-contained template: no personal data, no secrets, no branding of any previous owner. Everything is configurable through environment variables.

---

## Features

- **Overview** — live CPU/RAM/disk/swap, network, I/O, service map, security score, active model/provider (realtime from runtime state).
- **Performance / Services & process / System info** — telemetry, top processes, hardware details.
- **Security audit** — run the bundled `vps-audit.sh`, filter AMAN/WARNING/KRITIS findings.
- **Network & ports** — interfaces, listening ports, exposure, allowlist close with confirmation.
- **Storage** — safe cache & RAM cleanup.
- **System logs / Notification center** — journal viewer + alert log with badges.
- **Task manager** — start/stop/restart services with confirmation.
- **Router / Provider & gateway** — live per-gateway runtime routes, change provider+model, custom provider CRUD (add/ping/edit/delete), model picker.
- **Provider quota & accounts** — 9router account management, quotas, OAuth device-login wizard.
- **9router twin** — Labs dapat berbagi DB yang sama dengan 9router, login OAuth via 9router on-demand (kiro/github/qwen/kilocode), import token, custom provider, proxy API, dan auto-off watchdog. Lihat [`MIGRATION-9ROUTER-LABS.md`](MIGRATION-9ROUTER-LABS.md) untuk panduan lengkap.
- **Usage** — token usage per provider, PDF report.
- **Chat / Skills / Memory / Sessions** — Hermes integration (optional; needs Hermes home).
- **Settings (Control Center)** — theme/language/density, Hermes WebUI settings, VPS access (password/SSH key/mode), website status.
- **Backup & restore** — per-target (labs / webui / 9router) zip backup with manifest validation and safe restore.
- **Accounts** — login with username or email, optional SMTP password recovery.
- **Hardened** — CSRF origin guard, SSRF-safe provider ping, rate-limited login, secure cookies, clickjacking/HSTS headers, path-traversal & zip-slip protection.


## Requirements

- Python 3.10+
- Linux with systemd, `psutil`-readable /proc
- Optional integrations: Hermes Agent home (`~/.hermes`), 9router DB, task-router, GateKey-compatible API

## Installation

```bash
# 1. Clone / copy this template
git clone <this-repo> /opt/vps-sentinel
cd /opt/vps-sentinel

# 2. Python deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure credentials (username / password / session secret)
mkdir -p data
cp data/labs.env.example data/labs.env
#  -> edit data/labs.env and set a strong LABS_PASSWORD + LABS_SECRET
#     (generate secret: openssl rand -hex 32)

# 4. Optional app config
cp .env.example .env          # and edit to taste (paths, domains, SMTP, GateKey key)

# 5. Smoke test
python app.py                 # listens on 127.0.0.1:9118, open http://127.0.0.1:9118
```

### Run as a systemd service (recommended)

```bash
sudo cp deploy/vps-audit.service.example /etc/systemd/system/vps-sentinel.service
#  -> edit paths inside to your install dir
sudo systemctl daemon-reload
sudo systemctl enable --now vps-sentinel
```

### Put it behind a reverse proxy (Caddy example)

```
labs.example.com {
    reverse_proxy 127.0.0.1:9118
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy strict-origin-when-cross-origin
    }
}
```

## Configuration reference

| Variable | Purpose | Default |
|----------|---------|---------|
| `LABS_USER` | login username | `admin` |
| `LABS_PASSWORD` | login password (required) | — |
| `LABS_SECRET` | session signing secret (hex) | — |
| `LABS_SECURE_COOKIE` | `1` = Secure cookie (use behind HTTPS) | `0` |
| `LABS_HERMES_DIR` | Hermes home for config/state/webui integration | `~/.hermes` |
| `LABS_TASK_ROUTER_PY` | task-router router.py path (optional) | — |
| `LABS_9ROUTER_DB` | 9router sqlite path (optional) | — |
| `LABS_GATEKEY_URL` | OpenAI-compatible chat endpoint | GateKey default |
| `LABS_GATEKEY_KEY` | API key for built-in Chat (optional) | — |
| `LABS_WEB_DOMAINS` | comma-separated sites for Website tab | — |
| `LABS_SERVICES` | comma-separated systemd units to manage | `caddy,fail2ban,ssh,nginx,postgresql` |
| `LABS_SMTP_HOST` / `PORT` / `USER` / `PASSWORD` / `FROM` | SMTP for email password recovery | — |

Environment files are read from `data/labs.env` and `.env` (both optional; OS env wins).

## Backups

- Per-target backups (labs / webui / 9router) are written to `backups/`.
- Restore requires an audited upload (manifest + target + required files + size caps) — zip-slip and cross-target injection are rejected.

## Security notes

- Login is rate-limited (15/IP/h, 10/username/15min).
- Provider ping only allows `http(s)` and blocks cloud-metadata IPs.
- State-changing API calls verify the `Origin` header matches the host (CSRF defense-in-depth).
- Never commit `data/labs.env`, `data/*.json`, `backups/`, or `vps-audit-report*.txt` — see `.gitignore`.

## License

MIT — see `LICENSE`. The bundled `vps-audit.sh` is a community VPS audit script (its original attribution is retained in its header).
