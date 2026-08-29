"""Labs API Proxy — OpenAI-compatible endpoint using 9router DB tokens.

Routes /v1/chat/completions and /v1/models, reading credentials from the
shared 9router DB (providerConnections) or Hermes config custom providers.
Works without 9router running — true twin independence.
"""
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from lab_db import connect_read, connect_write

LIVE_DB = Path(os.environ.get("LABS_9ROUTER_DB", "/home/ubuntu/.9router/db/data.sqlite"))
CONFIG_PATH = Path(os.environ.get("HERMES_HOME", "/home/ubuntu/.hermes")) / "config.yaml"

# Known OpenAI-compatible base URLs (same as lab_router_accounts)
DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
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

OAUTH_PROVIDERS = {"kiro", "codex", "antigravity", "cline", "gemini-cli", "kilocode", "commandcode", "opencode"}


def _load_yaml_json(path):
    import subprocess
    r = subprocess.run(["python3", "-c",
                        "import yaml,sys,json;print(json.dumps(yaml.safe_load(open(sys.argv[1]))))",
                        str(path)], capture_output=True, text=True, timeout=10)
    return json.loads(r.stdout or "{}")


def _resolve(model, active_only=True):
    """Find which provider+account can serve a model.

    Returns (base_url, api_key, extra_headers) or None.
    """
    # 1. Check 9router DB accounts
    db = connect_read()
    try:
        rows = db.execute(
            "SELECT id, provider, data, isActive FROM providerConnections ORDER BY priority"
        ).fetchall()
    finally:
        db.close()
    for row in rows:
        rid, prov, data_str, is_active = row["id"], row["provider"], row["data"] or "{}", row["isActive"]
        if active_only and not is_active:
            continue
        data = json.loads(data_str)
        locked = [k.replace("modelLock_", "", 1) for k, v in data.items() if k.startswith("modelLock_") and v]
        if model not in locked:
            continue
        # Found! resolve base URL
        psd = data.get("providerSpecificData") or {}
        base = psd.get("baseUrl") or data.get("baseUrl") or DEFAULT_BASE_URLS.get(prov)
        if not base:
            base = next((v for k, v in DEFAULT_BASE_URLS.items() if prov.startswith(k)), None)
        if not base:
            continue
        token = data.get("apiKey") or data.get("accessToken")
        if not token:
            continue
        extra = {}
        if psd.get("connectionProxyUrl"):
            extra["x-9router-proxy"] = psd["connectionProxyUrl"]
        return (base.rstrip("/"), token, extra)

    # 2. Check Hermes custom providers (config.yaml)
    try:
        cfg = _load_yaml_json(CONFIG_PATH)
        for p in cfg.get("custom_providers") or []:
            if isinstance(p, dict):
                name = p.get("name", "")
                models = [str(m) for m in (p.get("models") or [])] + [str(p.get("model", ""))]
                if model in models:
                    base = p.get("base_url", "").rstrip("/")
                    if not base or "127.0.0.1" in base:
                        continue
                    token = p.get("api_key", "")
                    extra = {}
                    for hk in ("x-relay-target", "x-9router-proxy"):
                        if hk in p.get("extra_headers", {}):
                            extra[hk] = p["extra_headers"][hk]
                    return (base, token, extra)
    except Exception:
        pass

    return None


def list_models(active_only=True):
    """List all models available in the system."""
    models = set()
    db = connect_read()
    try:
        rows = db.execute(
            "SELECT data, isActive FROM providerConnections"
        ).fetchall()
    finally:
        db.close()
    for row in rows:
        if active_only and not row["isActive"]:
            continue
        data = json.loads(row["data"] or "{}")
        for k, v in data.items():
            if k.startswith("modelLock_") and v:
                models.add(k.replace("modelLock_", "", 1))
    # Add custom providers models
    try:
        cfg = _load_yaml_json(CONFIG_PATH)
        for p in cfg.get("custom_providers") or []:
            if isinstance(p, dict):
                for m in p.get("models") or []:
                    if isinstance(m, str):
                        models.add(m)
                if p.get("model"):
                    models.add(p["model"])
    except Exception:
        pass
    return sorted(models)


def chat_completions(body):
    """Forward an OpenAI-format chat completions request to the right provider."""
    model = body.get("model", "")
    if not model:
        return {"error": "model required"}, 400
    resolved = _resolve(model)
    if not resolved:
        return {"error": f"model '{model}' tidak ditemukan di akun 9router atau config"}, 404
    base_url, token, extra_headers = resolved
    endpoint = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    headers.update(extra_headers)
    try:
        payload = json.dumps(body).encode()
        req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            data = json.loads(raw.decode("utf-8", "replace"))
            return data, resp.status
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            detail = {"error": str(e)}
        return detail, e.code
    except urllib.error.URLError as e:
        return {"error": str(e.reason)}, 502
    except Exception as e:
        return {"error": str(e)}, 500


if __name__ == "__main__":
    print("models:", len(list_models()))
    for m in list_models()[:10]:
        r = _resolve(m)
        if r:
            print(f"  {m} -> {r[0]}")