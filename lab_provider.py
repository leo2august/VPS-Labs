"""Labs — ping test model, provider rename, full config viewer."""
import os
import json
import re
import subprocess
import time
from pathlib import Path

import urllib.error
import urllib.request
import yaml

HERMES_DIR = Path(os.environ.get('LABS_HERMES_DIR', '/home/USER/.hermes'))
CONFIG = HERMES_DIR / "config.yaml"


def _load_cfg():
    try:
        return yaml.safe_load(CONFIG.read_text()) or {}
    except Exception as e:
        raise ValueError(f"config.yaml rusak: {e}")


def _chattr(op, path):
    try:
        subprocess.run(["chattr", op, str(path)], capture_output=True, text=True, timeout=5)
    except Exception:
        pass


def _save_cfg(data):
    _chattr("-i", CONFIG)
    try:
        tmp = CONFIG.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False))
        import os
        os.replace(tmp, CONFIG)
    finally:
        _chattr("+i", CONFIG)


def _ssrf_safe_url(raw: str) -> str:
    """Allow only http/https and block cloud-metadata targets (SSRF guard)."""
    import ipaddress
    import urllib.parse
    raw = str(raw or "").strip()
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("hanya http/https yang diizinkan")
    host = parsed.hostname or ""
    blocked = ("169.254.169.254", "100.100.100.200", "100.100.102.200", "0.0.0.0",
               "metadata.google.internal", "metadata.tencentyun.com", "metadata")
    if host.lower() in blocked:
        raise ValueError("target metadata cloud diblokir")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_link_local or (ip.version == 4 and (ip == ipaddress.ip_address("0.0.0.0"))):
            raise ValueError("target metadata cloud diblokir")
    except ValueError:
        if host in blocked or not host:
            raise ValueError("target metadata cloud diblokir")
    return raw


def ping_model(base_url: str, api_key: str, model: str, timeout: int = 25,
              provider_name: str = "") -> dict:
    """POST a tiny chat completion to test if the model/provider is reachable.
    If provider_name is given, look up the real api_key + a real model from config."""
    if provider_name:
        try:
            cfg = _load_cfg()
            for p in cfg.get("custom_providers", []) or []:
                if isinstance(p, dict) and p.get("name", "") == provider_name:
                    if not api_key:
                        api_key = p.get("api_key") or p.get("apiKey") or ""
                    if (not model or model == "unknown"):
                        models = p.get("models") or []
                        if isinstance(models, list) and models:
                            model = models[0]
                        elif p.get("model"):
                            model = p.get("model")
                        else:
                            model = "unknown"
                    if not base_url:
                        base_url = p.get("base_url", "")
                    break
        except Exception:
            pass
    try:
        base_url = _ssrf_safe_url(base_url)
    except ValueError as exc:
        return {"ok": False, "model": model, "code": 0, "ms": 0, "detail": str(exc)}
    base_url = base_url.rstrip("/")
    if not base_url:
        return {"ok": False, "model": model, "code": 0, "ms": 0, "detail": "base_url kosong (provider mungkin pakai model field)"}
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    url = base_url + "/chat/completions"
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}],
                       "max_tokens": 4, "stream": False}).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + str(api_key)
    start = time.time()
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = r.status
            raw = r.read().decode("utf-8", "replace")[:300]
        ms = int((time.time() - start) * 1000)
        ok = code == 200
        detail = raw[:150] if not ok else "OK"
        return {"ok": ok, "model": model, "code": code, "ms": ms, "detail": detail}
    except urllib.error.HTTPError as e:
        ms = int((time.time() - start) * 1000)
        return {"ok": False, "model": model, "code": e.code, "ms": ms,
                "detail": e.read()[:150].decode("utf-8", "replace")}
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        return {"ok": False, "model": model, "code": 0, "ms": ms, "detail": str(e)[:150]}


def rename_provider(old_name: str, new_name: str) -> dict:
    try:
        data = _load_cfg()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    new_name = str(new_name).strip()
    if not new_name:
        return {"ok": False, "error": "nama baru kosong"}
    provs = data.get("custom_providers", [])
    for p in provs:
        if p.get("name", "") == old_name:
            # check duplicate
            for q in provs:
                if q.get("name", "").lower() == new_name.lower() and q is not p:
                    return {"ok": False, "error": "nama sudah dipakai provider lain"}
            p["name"] = new_name
            try:
                _save_cfg(data)
            except Exception as e:
                return {"ok": False, "error": str(e)[:200]}
            return {"ok": True, "old": old_name, "new": new_name}
    return {"ok": False, "error": "provider tidak ditemukan"}


def full_config() -> dict:
    """Return sanitized full config structure (no api_key values)."""
    try:
        data = _load_cfg()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    providers = []
    for p in data.get("custom_providers", []) or []:
        if not isinstance(p, dict):
            continue
        models = p.get("models", [])
        if not isinstance(models, list):
            models = [str(models)] if models else []
        single_model = p.get("model") or p.get("default_model")
        if single_model and single_model not in models:
            models.insert(0, str(single_model))
        providers.append({
            "name": p.get("name", "?"),
            "base_url": p.get("base_url", ""),
            "api_key": bool(p.get("api_key") or p.get("apiKey")),
            "models": models,
            "default_model": str(single_model or (models[0] if models else "")),
            "enabled": p.get("enabled", True),
            "extra_headers": {k: "***" for k in (p.get("extra_headers") or {})},
        })
    safe = {}
    for k, v in data.items():
        if k == "custom_providers":
            continue
        safe[k] = v
    return {"ok": True, "providers": providers,
            "other": {k: v for k, v in safe.items() if not isinstance(v, dict) or k in ("model", "providers")},
            "model": data.get("model"),
            "raw_preview": CONFIG.read_text(errors="replace")[:6000]}


def edit_provider(name: str, new_name=None, base_url=None, api_key=None,
                  models=None, model_add=None, model_remove=None, enabled=None) -> dict:
    """Full provider editing: rename, change base_url, replace/set api_key,
    add/remove models, toggle enabled."""
    try:
        data = _load_cfg()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    provs = data.get("custom_providers", [])
    for p in provs:
        if p.get("name", "") == name:
            if new_name:
                for q in provs:
                    if q.get("name", "").lower() == str(new_name).lower() and q is not p:
                        return {"ok": False, "error": "nama sudah dipakai"}
                p["name"] = str(new_name)
            if base_url:
                p["base_url"] = str(base_url)
            if api_key:
                p["api_key"] = str(api_key)
            if models is not None:
                p["models"] = models if isinstance(models, list) else [str(models)]
                if p["models"]:
                    p["model"] = str(p["models"][0])
            else:
                cur = p.get("models", [])
                if not isinstance(cur, list):
                    cur = [str(cur)] if cur else []
                if model_add:
                    for m in str(model_add).split(","):
                        m = m.strip()
                        if m and m not in cur:
                            cur.append(m)
                    p["models"] = cur
                if model_remove:
                    cur = [m for m in cur if m != str(model_remove)]
                    p["models"] = cur
            if enabled is not None:
                p["enabled"] = bool(enabled)
            try:
                _save_cfg(data)
            except Exception as e:
                return {"ok": False, "error": str(e)[:200]}
            return {"ok": True, "updated": p.get("name")}
    return {"ok": False, "error": "provider tidak ditemukan"}

def get_core_config() -> dict:
    """Return editable core routing config: model, fallback, providers section."""
    try:
        data = _load_cfg()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    core = {
        "model": data.get("model", {}),
        "fallback_providers": data.get("fallback_providers", []),
        "providers": data.get("providers", {}),
        "max_tokens": data.get("max_tokens"),
    }
    return {"ok": True, "core": core}


def save_core_config(model_default=None, model_provider=None,
                     fallback_providers=None, max_tokens=None) -> dict:
    try:
        data = _load_cfg()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if model_default is not None or model_provider is not None:
        data.setdefault("model", {})
        if model_default:
            data["model"]["default"] = str(model_default)
        if model_provider:
            data["model"]["provider"] = str(model_provider)
    if fallback_providers is not None:
        data["fallback_providers"] = fallback_providers if isinstance(fallback_providers, list) else []
    if max_tokens is not None:
        try:
            data["max_tokens"] = int(max_tokens)
        except (TypeError, ValueError):
            pass
    try:
        _save_cfg(data)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "model": data.get("model"), "fallback": data.get("fallback_providers")}
