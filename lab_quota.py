"""9router account health: live API first, newest backup as fallback."""
import json
import os
import sqlite3
import tempfile
import time
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKUPS = Path(__file__).resolve().parent / "backups"
LIVE_DB = Path(os.environ.get("LABS_9ROUTER_DB", "/home/ubuntu/.9router/db/data.sqlite"))
LIVE_API = "http://127.0.0.1:20128/api/providers"


def _latest_backup():
    files = sorted(BACKUPS.glob("9router-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _usage(db):
    totals = defaultdict(lambda: {"requests": 0, "prompt": 0, "output": 0})
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        for r in con.execute("SELECT provider,connectionId,COUNT(*) requests,SUM(promptTokens) prompt,SUM(completionTokens) output FROM usageHistory GROUP BY provider,connectionId"):
            key = r["connectionId"] or r["provider"] or ""
            totals[key] = {"requests": r["requests"] or 0, "prompt": r["prompt"] or 0, "output": r["output"] or 0}
    finally:
        con.close()
    return totals


def _account(row, used):
    expires = row.get("expiresAt") or row.get("expires_at")
    expired = False
    if expires:
        try: expired = datetime.fromisoformat(str(expires).replace("Z", "+00:00")) < datetime.now(timezone.utc)
        except ValueError: pass
    enabled = bool(row.get("isActive", row.get("enabled", False)))
    status = row.get("testStatus") or ("active" if enabled else "disabled")
    if expired: status = "expired"
    locks = [v for k, v in row.items() if str(k).startswith("modelLock_") and v]
    return {"id": str(row.get("id", "")), "provider": str(row.get("provider", "unknown")),
            "name": row.get("name") or row.get("email") or row.get("provider") or "Akun",
            "email": row.get("email") or "", "enabled": enabled, "status": status,
            "error_code": row.get("errorCode"), "last_error": str(row.get("lastError") or "")[:180],
            "expires_at": expires, "locked_models": len(locks), "plan": row.get("plan"),
            "requests": used["requests"], "tokens": used["prompt"] + used["output"],
            "prompt_tokens": used["prompt"], "output_tokens": used["output"]}


def _result(accounts, source, stamp, live, note=None, db_mode=False):
    counts = defaultdict(int)
    for account in accounts: counts[account["status"]] += 1
    if note is None:
        note = ("Data live 9router. Kontrol akun dan login tersedia." if live else
                "9router offline. Data fallback dari backup; kontrol akun dinonaktifkan.")
    return {"ok": True, "live": live, "db_mode": db_mode, "source": source, "snapshot_at": stamp, "accounts": accounts,
            "summary": {"total": len(accounts), "active": counts["active"],
                        "unavailable": counts["unavailable"] + counts["error"],
                        "expired": counts["expired"], "disabled": counts["disabled"]},
            "note": note}


def _live_status():
    with urllib.request.urlopen(LIVE_API, timeout=5) as res:
        rows = json.loads(res.read()).get("connections", [])
    totals = _usage(LIVE_DB)
    return _result([_account(r, totals[str(r.get("id", ""))]) for r in rows], "9router LIVE", time.time(), True)


def _db_read_accounts(db_path=LIVE_DB):
    """Read provider accounts straight from the 9router SQLite DB."""
    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(str(db))
    totals = _usage(db)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = list(con.execute("SELECT id,provider,name,email,isActive,data FROM providerConnections ORDER BY provider,name"))
    finally:
        con.close()
    accounts = []
    for row in rows:
        raw = dict(row)
        raw.update(json.loads(raw.pop("data") or "{}"))
        accounts.append(_account(raw, totals[str(raw.get("id", ""))]))
    return accounts


def _db_status():
    accounts = _db_read_accounts()
    return _result(accounts, "9router DB", time.time(), True,
                   note="9router API mati; data langsung dari database. Toggle akun memakai mode DB (tanpa API).",
                   db_mode=True)


def _config_provider_accounts():
    """Custom providers from Hermes config.yaml surfaced as quota accounts."""
    from lab_router_accounts import _config_providers
    out = []
    for p in _config_providers():
        name = p["name"]
        out.append({
            "id": f"cfg:{name}", "provider": "config",
            "name": name, "email": p["base_url"].replace("https://", "").split("/")[0],
            "enabled": True, "status": "active", "error_code": None, "last_error": "",
            "expires_at": None, "locked_models": len(p.get("models") or []),
            "plan": "", "requests": 0, "tokens": 0, "prompt_tokens": 0, "output_tokens": 0,
            "model": p.get("model") or "", "models": p.get("models") or [],
            "config": True,
        })
    return out


def quota_status():
    try:
        result = _live_status()
    except Exception:
        try:
            result = _db_status()
        except Exception:
            try:
                result = _backup_status()
            except Exception as exc:
                return {"ok": False, "error": str(exc)[:240], "accounts": []}
    # always append config providers so they show up regardless of 9router state
    cfg = _config_provider_accounts()
    if cfg:
        result["accounts"] = result.get("accounts", []) + cfg
        result["summary"]["total"] = len(result["accounts"])
    return result


def _backup_status():
    backup = _latest_backup()
    if not backup:
        return {"ok": False, "error": "backup 9router tidak ditemukan", "accounts": []}
    with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(backup) as z:
        z.extract("db/data.sqlite", td)
        db = Path(td) / "db/data.sqlite"
        totals = _usage(db)
        con = sqlite3.connect(db); con.row_factory = sqlite3.Row
        try: rows = list(con.execute("SELECT id,provider,name,email,isActive,data FROM providerConnections ORDER BY provider,name"))
        finally: con.close()
        accounts = []
        for row in rows:
            raw = dict(row); raw.update(json.loads(raw.pop("data") or "{}"))
            accounts.append(_account(raw, totals[str(raw.get("id", ""))]))
    return _result(accounts, backup.name, backup.stat().st_mtime, False)


if __name__ == "__main__":
    result = quota_status()
    assert "accounts" in result
    print("lab_quota self-check OK", len(result["accounts"]), "live=" + str(result.get("live")))
