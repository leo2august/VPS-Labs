"""9router auto-failover + keepalive — hosted inside Labs (vps-audit).

Menggantikan cron Hermes: kiro-quota-autofailover + kiro-ping-keepalive.
- Auto switch OFF akun Kiro ACC yang quota-nya abis (errorCode 402/413,
  'credits required'/'paid model') supaya failover lompat ke ACC lain.
- Auto switch ON kembali saat quota pulih, dan semua akun hari-1 tiap bulan.
- Keepalive: ping semua ACC aktif tiap 12 jam biar gak idle / token segar.
- SILENT kalau tidak ada perubahan. Robust: kalau 9router offline, skip tanpa spam.

State disimpan di data/lab-settings.json (key: failover_enabled, failover_last_run,
failover_last_result, failover_keepalive_last).
"""
import datetime
import hashlib
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "data" / "lab-settings.json"
MID_FILE = Path(os.path.expanduser("~/.9router/machine-id"))
API = "http://127.0.0.1:20128"
TARGET_PROVIDERS = ("kiro",)  # khusus Kiro ACC
KEEPALIVE_INTERVAL = 12 * 3600  # detik
_lock = threading.Lock()

# ---- settings helpers (shared dengan lab_operations) ----
def _read_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except Exception:
        return {}


def _write_settings(data: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, SETTINGS)


def failover_setting(key, default=None):
    return _read_settings().get(key, default)


def failover_set_enabled(enabled: bool) -> dict:
    data = _read_settings()
    data["failover_enabled"] = bool(enabled)
    _write_settings(data)
    return {"ok": True, "enabled": bool(enabled)}


def failover_status() -> dict:
    s = _read_settings()
    return {
        "ok": True,
        "enabled": bool(s.get("failover_enabled", False)),
        "last_run": s.get("failover_last_run"),
        "last_result": s.get("failover_last_result"),
        "keepalive_last": s.get("failover_keepalive_last"),
        "router_online": _router_online(),
        "note": "Auto switch off Kiro saat quota habis, switch on saat pulih/hari-1 bulan. "
                "Keepalive ping tiap 12 jam. Mati otomatis kalau 9router offline."
    }


# ---- 9router API ----
def _get_token():
    if not MID_FILE.exists():
        return ""
    try:
        mid = MID_FILE.read_text().strip()
    except Exception:
        return ""
    return hashlib.sha256((mid + "9r-cli-auth").encode()).hexdigest()[:16]


def _api(method, path, body=None, timeout=8):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        API + path, data=data,
        headers={"Content-Type": "application/json", "x-9r-cli-token": _get_token()},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _router_online() -> bool:
    try:
        _api("GET", "/api/providers", timeout=4)
        return True
    except Exception:
        return False


def _parse_data(c):
    data = c.get("data", {}) or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {}
    merged = dict(data)
    for k in ("errorCode", "lastError", "testStatus"):
        if c.get(k) is not None:
            merged[k] = c.get(k)
    return merged


def _active_accs():
    """Daftar akun Kiro ACC (nama mulai 'ACC'), urut nomor."""
    conns = _api("GET", "/api/providers").get("connections", [])
    accs = [c for c in conns
            if c.get("provider") in TARGET_PROVIDERS
            and str(c.get("name", "")).startswith("ACC")]
    accs.sort(key=lambda c: int("".join(filter(str.isdigit, str(c.get("name", "")))) or 0))
    return accs


# ---- auto quota failover ----
def run_quota_failover() -> dict:
    """Satu siklus auto switch off/on. Silent jika tidak ada perubahan."""
    if not failover_setting("failover_enabled", False):
        return {"ok": True, "enabled": False, "changes": [], "note": "disabled"}
    if not _router_online():
        return {"ok": True, "enabled": True, "changes": [], "note": "9router offline - skip"}

    accs = _active_accs()
    force_reenable = datetime.date.today().day == 1
    changes = []

    for c in accs:
        cid, name = c.get("id"), c.get("name")
        data = _parse_data(c)
        err_code = data.get("errorCode")
        err_msg = str(data.get("lastError", "")).lower()
        active = bool(c.get("isActive"))

        quota_out = (err_code in (402, 413)) or ("credits required" in err_msg or "paid model" in err_msg)

        if force_reenable:
            if not active:
                _api("PUT", f"/api/providers/{cid}", {"isActive": True})
                changes.append(f"{name}: AKTIFKAN (reset bulan)")
        else:
            if quota_out and active:
                _api("PUT", f"/api/providers/{cid}", {"isActive": False})
                changes.append(f"{name}: OFF (quota {err_code})")
            elif not quota_out and not active:
                _api("PUT", f"/api/providers/{cid}", {"isActive": True})
                changes.append(f"{name}: AKTIFKAN (quota pulih)")

    return {"ok": True, "enabled": True, "changes": changes, "count": len(changes)}


# ---- keepalive ping ----
def run_keepalive() -> dict:
    """Ping semua ACC aktif — 9router distribute otomatis. Output hanya kalau error."""
    if not failover_setting("failover_enabled", False):
        return {"ok": True, "note": "disabled"}
    if not _router_online():
        return {"ok": True, "note": "9router offline - skip"}

    conns = _api("GET", "/api/providers").get("connections", [])
    active = [c for c in conns
              if c.get("provider") in TARGET_PROVIDERS
              and str(c.get("name", "")).startswith("ACC")
              and c.get("isActive")]
    if not active:
        return {"ok": True, "note": "no active ACC"}

    errors = []
    ok = 0
    for _ in range(len(active)):
        payload = {
            "model": "kr/claude-sonnet-4.5",
            "messages": [{"role": "system", "content": ""},
                         {"role": "user", "content": "ok"}],
            "max_tokens": 3,
            "temperature": 0,
        }
        try:
            req = urllib.request.Request(
                API + "/v1/chat/completions", data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=8) as r:
                code = r.status
            if code == 200 or code == 429:
                ok += 1
            else:
                errors.append(f"HTTP {code}")
        except Exception as exc:
            errors.append(str(exc)[:80])

    return {"ok": True, "pinged": ok, "errors": errors, "note": "keepalive done"}


# ---- one-shot (dipanggil scheduler thread) ----
def failover_tick(force_keepalive=False) -> dict:
    with _lock:
        result = run_quota_failover()
        now = datetime.datetime.now().isoformat(timespec="seconds")
        data = _read_settings()
        data["failover_last_run"] = now
        if result.get("changes"):
            data["failover_last_result"] = "; ".join(result["changes"])
        elif result.get("note"):
            data["failover_last_result"] = result["note"]
        else:
            data["failover_last_result"] = "tidak ada perubahan"
        # keepalive tiap 12 jam
        last_ka = data.get("failover_keepalive_last")
        due = (not last_ka) or force_keepalive or \
              (time.time() - _ts(last_ka)) >= KEEPALIVE_INTERVAL
        if due:
            ka = run_keepalive()
            data["failover_keepalive_last"] = now
            result["keepalive"] = ka
        _write_settings(data)
        return result


def _ts(iso):
    try:
        return datetime.datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0


def scheduler_loop(interval=300):
    """Thread daemon: cek tiap `interval` detik (default 5 menit)."""
    while True:
        try:
            failover_tick()
        except Exception:
            pass  # jangan pernah crash thread
        time.sleep(interval)
