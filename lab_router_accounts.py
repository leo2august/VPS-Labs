"""Safe localhost bridge for 9router provider/account management."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "http://127.0.0.1:20128"
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


def update_account(account_id, enabled):
    data = _call("PUT", f"/api/providers/{_id(account_id)}", {"isActive": bool(enabled)})
    return {"ok": True, "connection": data.get("connection", data)}


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
