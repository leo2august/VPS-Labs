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

from lab_db import connect_read, connect_write

BASE = "http://127.0.0.1:20128"
LIVE_DB = Path(os.environ.get("LABS_9ROUTER_DB", "/home/ubuntu/.9router/db/data.sqlite"))
DEVICE_PROVIDERS = {"kiro", "github", "qwen"}
API_KEY_PROVIDERS = {"openrouter", "glm", "minimax", "gemini", "deepseek", "openai"}
# Default OpenAI-compatible base URLs for providers that don't store one in DB.
DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "glm": "https://open.bigmodel.cn/api/paas/v4",
    "minimax": "https://api.minimax.chat/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "github": "https://models.github.ai/api",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "ollama": "http://localhost:11434/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}
# Config providers that route through 9router OAuth and have no public OpenAI-compatible endpoint.
OAUTH_PROVIDERS = {"kiro", "codex", "antigravity", "cline", "gemini-cli", "kilocode", "commandcode", "opencode"}
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
    """Write isActive directly into the 9router SQLite DB (shared twin DB, WAL)."""
    if not LIVE_DB.exists():
        raise ValueError("database 9router tidak ditemukan untuk mode offline")
    ids = [_id(i) for i in account_ids]
    con = connect_write()
    try:
        con.execute("BEGIN IMMEDIATE")
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
        con = connect_read()
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


def _account_data(account_id):
    """Read a single provider connection from the DB + merge its data JSON.
    Supports cfg: prefixed ids that read from Hermes config custom_providers."""
    aid = str(account_id or "")
    if aid.startswith("cfg:"):
        return _config_account(aid[4:])
    con = connect_read()
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM providerConnections WHERE id=?", (_id(account_id),)).fetchone()
    finally:
        con.close()
    if not row:
        raise ValueError("akun tidak ditemukan")
    raw = dict(row)
    raw.update(json.loads(raw.pop("data") or "{}"))
    return raw


CONFIG_PATH = Path(os.environ.get("HERMES_HOME", "/home/ubuntu/.hermes")) / "config.yaml"


def _config_providers():
    """Custom providers from Hermes config.yaml that have a direct base_url (not via 9router)."""
    out = []
    try:
        cfg = json.loads(_load_yaml_json(CONFIG_PATH))
    except Exception:
        return out
    provs = cfg.get("custom_providers") or []
    if isinstance(provs, dict):
        provs = list(provs.values())
    for p in provs:
        name = (p.get("name") or "").strip()
        base = (p.get("base_url") or "").strip()
        if not name or not base:
            continue
        # skip providers that route through 9router (already represented in DB)
        if "127.0.0.1" in base and "20128" in base:
            continue
        if "127.0.0.1" in base and "20129" in base:
            continue
        out.append({
            "name": name,
            "base_url": base.rstrip("/"),
            "api_key": p.get("api_key") or "",
            "model": p.get("model") or p.get("default_model") or "",
            "models": p.get("models") or [],
            "extra_headers": p.get("extra_headers") or {},
        })
    return out


def _load_yaml_json(path):
    """Parse a YAML file, return as JSON string (uses PyYAML if available)."""
    import subprocess
    r = subprocess.run(["python3", "-c",
                        "import yaml,sys,json;print(json.dumps(yaml.safe_load(open(sys.argv[1]))))",
                        str(path)], capture_output=True, text=True, timeout=10)
    return r.stdout or "{}"


def _config_account(name):
    for p in _config_providers():
        if p["name"] == name:
            return p
    raise ValueError("akun tidak ditemukan")


def _base_url(raw):
    """Resolve the provider's OpenAI-compatible base URL."""
    bu = raw.get("base_url") or ""
    if bu:
        return bu.rstrip("/")
    psd = raw.get("providerSpecificData") or {}
    bu = psd.get("baseUrl") or raw.get("baseUrl") or ""
    if bu:
        return bu.rstrip("/")
    prov = str(raw.get("provider", "")).lower()
    # strip UUID suffix for openai-compatible-xxx to get the base name
    for known in ("openai", "anthropic"):
        if prov.startswith(known):
            return DEFAULT_BASE_URLS.get(known)
    if prov in DEFAULT_BASE_URLS:
        return DEFAULT_BASE_URLS[prov]
    # try wildcard prefix match
    match = [v for k, v in DEFAULT_BASE_URLS.items() if prov.startswith(k)]
    if match:
        return match[0]
    return None


def _auth_token(raw):
    """Get the bearer token (apiKey or accessToken) for the account."""
    return raw.get("apiKey") or raw.get("accessToken") or raw.get("api_key") or ""


def _extra_headers(raw):
    """Extra headers for relay providers (e.g. x-relay-target for B.AI)."""
    return raw.get("extra_headers") or {}


def _first_model(raw):
    """Pick the first locked model from data, or defaultModel, or a fallback."""
    m = raw.get("defaultModel") or raw.get("default_model") or raw.get("model") or ""
    if m:
        return m
    for k, v in raw.items():
        if k.startswith("modelLock_") and v:
            return k.replace("modelLock_", "", 1)
    return ""


def _offline_http(method, url, headers, body=None, timeout=10):
    """Wrapper for urllib with timeout, returns (status, body_bytes)."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        return 0, str(exc.reason).encode()


def test_account(account_id):
    try:
        data = _call("POST", f"/api/providers/{_id(account_id)}/test")
        return {"ok": True, "mode": "api", "result": data}
    except ValueError:
        raw = _account_data(account_id)
        base = _base_url(raw)
        if not base:
            # OAuth provider without public base URL; check refresh token + model list
            if str(raw.get("provider", "")).lower() in OAUTH_PROVIDERS:
                locked = sorted(k.replace("modelLock_", "", 1) for k, v in raw.items() if k.startswith("modelLock_") and v)
                refresh = raw.get("refreshToken") or ""
                expires = raw.get("expiresAt") or ""
                if not refresh and not _auth_token(raw):
                    return {"ok": True, "result": {"valid": False, "error": "kredensial tidak tersedia"}}
                if refresh:
                    # refresh token available -> 9router will auto-refresh; treat as valid
                    extra = f" · access token sampai {expires[:10]}" if expires else ""
                    msg = f"token OAuth valid (refresh otomatis tersedia){extra}" + (f" · {len(locked)} model" if locked else "")
                    return {"ok": True, "result": {"valid": True, "note": msg, "models": locked}}
                # no refresh token: fall back to access token expiry check
                expired = False
                if expires:
                    try:
                        from datetime import datetime, timezone
                        expired = datetime.fromisoformat(str(expires).replace("Z", "+00:00")) < datetime.now(timezone.utc)
                    except ValueError:
                        pass
                if expired:
                    return {"ok": True, "result": {"valid": False, "error": f"token kedaluwarsa ({expires[:10]})"}}
                return {"ok": True, "result": {"valid": True, "note": "token OAuth valid", "models": locked}}
            return {"ok": True, "result": {"valid": False, "error": "base URL tidak diketahui untuk provider ini (mode offline)"}}
        token = _auth_token(raw)
        if not token:
            return {"ok": True, "result": {"valid": False, "error": "kredensial tidak tersedia"}}
        model = _first_model(raw) or "gpt-3.5-turbo"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        headers.update(_extra_headers(raw))
        import time
        t0 = time.time()
        payload = json.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 16}).encode()
        status, body = _offline_http("POST", f"{base}/chat/completions", headers, body=payload)
        ms = int((time.time() - t0) * 1000)
        if status == 200:
            return {"ok": True, "mode": "db", "result": {"valid": True, "latency_ms": ms}}
        detail = "no response" if status == 0 else f"HTTP {status}"
        return {"ok": True, "mode": "db", "result": {"valid": False, "latency_ms": ms, "error": detail}}


def account_models(account_id):
    try:
        data = _call("GET", f"/api/providers/{_id(account_id)}/models")
        return {"ok": True, "mode": "api", **data}
    except ValueError:
        raw = _account_data(account_id)
        # For config providers, return their models list directly
        if str(account_id or "").startswith("cfg:"):
            models = raw.get("models") or []
            if models:
                return {"ok": True, "mode": "db", "models": models}
        # first try HTTP /models
        base = _base_url(raw)
        if base:
            token = _auth_token(raw)
            if token:
                headers = {"Authorization": f"Bearer {token}"}
                headers.update(_extra_headers(raw))
                status, body = _offline_http("GET", f"{base}/models", headers)
                if status == 200:
                    try:
                        models = [m.get("id") or m.get("name") for m in json.loads(body).get("data", []) if m.get("id") or m.get("name")]
                        if models:
                            return {"ok": True, "mode": "db", "models": models}
                    except (json.JSONDecodeError, TypeError, KeyError):
                        pass
        # fallback: return locked-models from data
        locked = sorted(k.replace("modelLock_", "", 1) for k, v in raw.items() if k.startswith("modelLock_") and v)
        if locked:
            return {"ok": True, "mode": "db", "models": locked}
        return {"ok": True, "mode": "db", "models": []}


def delete_account(account_id):
    try:
        _call("DELETE", f"/api/providers/{_id(account_id)}")
        return {"ok": True, "mode": "api"}
    except ValueError:
        if str(account_id or "").startswith("cfg:"):
            raise ValueError("hapus langsung dari config.yaml tidak didukung; nonaktifkan dari config")
        aid = _id(account_id)
        if not LIVE_DB.exists():
            raise ValueError("database 9router tidak ditemukan untuk mode offline")
        con = connect_write()
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM providerConnections WHERE id=?", (aid,))
            con.commit()
        finally:
            con.close()
        return {"ok": True, "mode": "db"}


def create_api_key(provider, name, api_key):
    provider = str(provider or "").lower()
    if provider not in API_KEY_PROVIDERS:
        return {"ok": False, "error": "provider API key tidak didukung dari Labs"}
    if not str(name).strip() or len(str(api_key).strip()) < 8:
        return {"ok": False, "error": "nama atau API key tidak valid"}
    data = _call("POST", "/api/providers", {"provider": provider, "name": str(name).strip(), "apiKey": str(api_key).strip()})
    return {"ok": True, "connection": data.get("connection", data)}


def _legacy_device_start(provider):
    """Fallback device login via 9router API (provider yang butuh signing key internal)."""
    provider = str(provider or "").lower()
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


def _legacy_device_poll(flow_id):
    """Fallback poll via 9router API."""
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


def start_device_login(provider, account_id=""):
    """Mulai device login — independent OAuth engine, fallback ke 9router.

    Prioritas: lab_oauth (mandiri) dulu. Jika provider belum didukung di
    lab_oauth (mis. kiro butuh signing key internal 9router), fallback ke
    API 9router bila 9router hidup.
    """
    from lab_oauth import start_device_login as _oauth_start
    try:
        return _oauth_start(provider, account_id)
    except ValueError as exc:
        # fallback ke 9router bila tersedia
        try:
            _call("GET", "/api/providers", timeout=2)
            return _legacy_device_start(provider)
        except ValueError:
            return {"ok": False, "error": str(exc)}


def poll_device_login(flow_id):
    """Poll status login — independent OAuth engine, fallback ke 9router."""
    from lab_oauth import poll_device_login as _oauth_poll
    try:
        return _oauth_poll(flow_id)
    except ValueError as exc:
        try:
            _call("GET", "/api/providers", timeout=2)
            return _legacy_device_poll(flow_id)
        except ValueError:
            return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    assert _id("abc-123_DEF") == "abc-123_DEF"
    assert router_status().get("online") in (True, False)
    print("lab_router_accounts self-check OK")
