"""Safe localhost bridge for 9router provider/account management."""
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:20128"
LIVE_DB = Path(os.environ.get("LABS_9ROUTER_DB", "/home/ubuntu/.9router/db/data.sqlite"))
DEVICE_PROVIDERS = {"kiro", "github", "qwen"}
API_KEY_PROVIDERS = {"openrouter", "glm", "minimax", "gemini", "deepseek", "openai"}
_flows = {}
_lock = threading.Lock()


def _call(method, path, payload=None, timeout=25):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {"ok": True}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try: detail = json.loads(raw).get("error", raw)
        except json.JSONDecodeError: detail = raw
        raise ValueError(str(detail)[:240]) from exc
    except urllib.error.URLError as exc:
        raise ValueError("9router tidak aktif atau belum siap") from exc


def _id(value):
    value = str(value or "")
    if not value or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in value):
        raise ValueError("ID akun tidak valid")
    return value


def router_status():
    try:
        data = _call("GET", "/api/providers", timeout=5)
        connections = data.get("connections", [])
        return {"online": True, "connections": len(connections) if isinstance(connections, list) else 0}
    except ValueError:
        return {"online": False, "connections": 0}


def _db_set_active(account_ids, enabled):
    """Write isActive directly into the 9router SQLite DB (offline mode)."""
    if not LIVE_DB.exists():
        raise ValueError("database 9router tidak ditemukan untuk mode offline")
    ids = [_id(i) for i in account_ids]
    con = sqlite3.connect(str(LIVE_DB))
    try:
        cur = con.cursor()
        for aid in ids:
            cur.execute("UPDATE providerConnections SET isActive=?, updatedAt=? WHERE id=?",
                        (1 if enabled else 0, time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), aid))
        con.commit()
        updated = cur.rowcount if len(ids) == 1 else len(ids)
    finally:
        con.close()
    return {"ok": True, "mode": "db", "updated": max(updated, 0)}


def update_account(account_id, enabled):
    try:
        data = _call("PUT", f"/api/providers/{_id(account_id)}", {"isActive": bool(enabled)})
        return {"ok": True, "mode": "api", "connection": data.get("connection", data)}
    except ValueError:
        return _db_set_active([account_id], enabled)


def update_provider_accounts(provider, enabled):
    provider = str(provider or "").strip()
    try:
        rows = _call("GET", "/api/providers", timeout=5).get("connections", [])
        matches = [row for row in rows if str(row.get("provider", "")).lower() == provider.lower()]
        if not provider or not matches:
            raise ValueError("provider tidak ditemukan")
        failed = []
        updated = 0
        for row in matches:
            account_id = _id(row.get("id"))
            try:
                _call("PUT", f"/api/providers/{account_id}", {"isActive": bool(enabled)})
                updated += 1
            except ValueError as exc:
                failed.append({"id": account_id, "error": str(exc)})
        return {"ok": not failed, "mode": "api", "provider": provider, "enabled": bool(enabled),
                "updated": updated, "failed": failed}
    except ValueError:
        # offline: toggle straight on the DB
        if not provider:
            raise ValueError("provider tidak ditemukan")
        if not LIVE_DB.exists():
            raise ValueError("database 9router tidak ditemukan untuk mode offline")
        con = sqlite3.connect(str(LIVE_DB))
        try:
            rows = con.execute("SELECT id FROM providerConnections WHERE lower(provider)=?",
                               (provider.lower(),)).fetchall()
        finally:
            con.close()
        if not rows:
            raise ValueError("provider tidak ditemukan")
        ids = [r[0] for r in rows]
        result = _db_set_active(ids, enabled)
        result["provider"] = provider
        result["enabled"] = bool(enabled)
        return result


def test_account(account_id):
    data = _call("POST", f"/api/providers/{_id(account_id)}/test")
    return {"ok": True, "result": data}


def account_models(account_id):
    data = _call("GET", f"/api/providers/{_id(account_id)}/models")
    return {"ok": True, **data}


def delete_account(account_id):
    _call("DELETE", f"/api/providers/{_id(account_id)}")
    return {"ok": True}


def create_api_key(provider, name, api_key):
    provider = str(provider or "").lower()
    if provider not in API_KEY_PROVIDERS:
        return {"ok": False, "error": "provider API key tidak didukung dari Labs"}
    if not str(name).strip() or len(str(api_key).strip()) < 8:
        return {"ok": False, "error": "nama atau API key tidak valid"}
    data = _call("POST", "/api/providers", {"provider": provider, "name": str(name).strip(), "apiKey": str(api_key).strip()})
    return {"ok": True, "connection": data.get("connection", data)}


def start_device_login(provider):
    provider = str(provider or "").lower()
    if provider not in DEVICE_PROVIDERS:
        return {"ok": False, "error": "provider login tidak didukung"}
    data = _call("GET", f"/api/oauth/{provider}/device-code")
    device_code = data.get("device_code")
    if not device_code:
        return {"ok": False, "error": "device code tidak diterima"}
    flow_id = uuid.uuid4().hex
    with _lock:
        _flows[flow_id] = {"provider": provider, "deviceCode": device_code,
                           "codeVerifier": data.get("codeVerifier"),
                           "extraData": data.get("extraData") or data,
                           "expires": time.time() + min(int(data.get("expires_in", 600)), 900)}
    return {"ok": True, "flow_id": flow_id, "provider": provider,
            "user_code": data.get("user_code"),
            "verification_uri": data.get("verification_uri"),
            "verification_uri_complete": data.get("verification_uri_complete"),
            "expires_in": data.get("expires_in", 600), "interval": max(3, int(data.get("interval", 5)))}


def poll_device_login(flow_id):
    with _lock:
        flow = _flows.get(str(flow_id))
    if not flow:
        return {"ok": False, "error": "sesi login tidak ditemukan"}
    if flow["expires"] < time.time():
        with _lock: _flows.pop(str(flow_id), None)
        return {"ok": False, "expired": True, "error": "sesi login kedaluwarsa"}
    try:
        data = _call("POST", f"/api/oauth/{flow['provider']}/poll",
                     {"deviceCode": flow["deviceCode"], "codeVerifier": flow["codeVerifier"], "extraData": flow["extraData"]})
    except ValueError as exc:
        err = str(exc)
        if err in ("authorization_pending", "slow_down"):
            return {"ok": True, "pending": True}
        return {"ok": False, "error": err}
    if data.get("pending"):
        return {"ok": True, "pending": True}
    with _lock: _flows.pop(str(flow_id), None)
    return {"ok": True, "pending": False, "connection": data.get("connection", data)}


if __name__ == "__main__":
    assert _id("abc-123_DEF") == "abc-123_DEF"
    assert router_status().get("online") in (True, False)
    print("lab_router_accounts self-check OK")
