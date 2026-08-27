"""Labs — router & webui integration endpoints (read + safe actions)."""
import ast
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from flask import jsonify

ROOT = Path(__file__).resolve().parent
HERMES_HOME = Path(os.environ.get('LABS_HERMES_DIR', '/home/USER/.hermes'))
ROUTER_PY = Path(os.environ.get('LABS_TASK_ROUTER_PY', '/home/USER/task-router/router.py'))
HERMES_CONFIG = HERMES_HOME / "config.yaml"
WEBUI_SETTINGS = HERMES_HOME / "webui" / "settings.json"
WEBUI_SETTINGS_BAK = ROOT / "settings.backup.json"
ROUTER_BAK = ROOT / "router-config.backup.json"

# --- safe service allowlist for control actions ---
SERVICE_ALLOWLIST = {
    "hermes-webui", "hermes-dashboard", "hermes-gateway", "hermes-task-router",
    "vps-audit", "caddy", "fail2ban", "9router", "ufw", "wms", "pos", "kanji-api",
}
USER_SVMAP = {"hermes-gateway": ["hermes_cli", "main"], "hermes-task-router": ["task-router", "router.py"]}

# --- webui settings we allow editing from the lab (key -> friendly label) ---
WEBUI_EDITABLE = {
    "theme": "Tema", "skin": "Skin", "font_size": "Ukuran font",
    "bot_name": "Nama bot", "language": "Bahasa", "send_key": "Tombol kirim",
    "sidebar_density": "Densitas sidebar", "auto_title_refresh_every": "Auto refresh judul",
    "default_message_mode": "Mode pesan default", "show_thinking": "Tampilkan thinking",
    "simplified_tool_calling": "Tool calling sederhana", "check_for_updates": "Cek update",
    "update_channel": "Kanal update", "default_model_provider": "Provider default",
    "notifications_enabled": "Notifikasi", "sound_enabled": "Suara",
    "tts_pitch": "Pitch TTS", "show_token_usage": "Tampilkan token usage",
}


def _safe_read(path: Path, max_bytes: int = 4_000_000) -> str:
    try:
        if path.exists() and path.stat().st_size <= max_bytes:
            return path.read_text(errors="replace")
    except OSError:
        pass
    return ""


# ---------------- router config ----------------
def parse_router_static(router_py: Path = ROUTER_PY) -> dict:
    """Extract routing configuration constants from task-router/router.py."""
    src = _safe_read(router_py)
    result = {"available": False, "error": None}
    if not src:
        result["error"] = "router.py tidak terbaca"
        return result
    result["available"] = True
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        result["error"] = f"parse error: {e}"
        return result

    def _extract_assign(name: str):
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        try:
                            return ast.literal_eval(node.value)
                        except Exception:
                            return None
        return None

    result["light_chain"] = _extract_assign("LIGHT_CHAIN")
    result["triggers"] = _extract_assign("TRIGGERS")
    result["pinned"] = _extract_assign("PINNED")
    result["heavy_hints"] = _extract_assign("HEAVY_HINTS")
    result["trigger_verbs"] = _extract_assign("TRIGGER_VERBS")
    # signature map
    sig = _extract_assign("SIG")
    result["signatures"] = sig
    return result


def read_hermes_config() -> dict:
    """Read key model/provider settings from ~/.hermes/config.yaml (no secrets)."""
    text = _safe_read(HERMES_CONFIG)
    data = {"available": False, "error": None, "model": None, "providers": []}
    if not text:
        data["error"] = "config.yaml tidak terbaca"
        return data
    data["available"] = True
    try:
        import yaml
        cfg = yaml.safe_load(text) or {}
    except Exception as e:
        data["error"] = f"yaml parse error: {e}"
        return data
    m = cfg.get("model") or {}
    data["model"] = {"default": m.get("default"), "provider": m.get("provider")}
    for p in cfg.get("custom_providers", []):
        data["providers"].append({
            "name": p.get("name"),
            "model": p.get("model"),
            "default_model": p.get("default_model"),
            "discover_models": bool(p.get("discover_models")),
            "has_key": bool(p.get("api_key")),
            "models": p.get("models") or [],
        })
    return data


def router_status() -> dict:
    """Probe 9router (20128) and task-router (20129) health + models."""
    out = {"task_router": {}, "g9router": {}}
    # task-router /health
    try:
        import urllib.request
        r = urllib.request.urlopen("http://127.0.0.1:20129/health", timeout=3)
        out["task_router"] = {"ok": r.status == 200, "http": r.status}
    except Exception as e:
        out["task_router"] = {"ok": False, "error": str(e)[:120]}
    # 9router /api/health + /v1/models
    try:
        import urllib.request
        r = urllib.request.urlopen("http://127.0.0.1:20128/api/health", timeout=3)
        out["g9router"] = {"ok": r.status == 200, "http": r.status}
    except Exception as e:
        out["g9router"] = {"ok": False, "error": str(e)[:120]}
    # models list from 9router
    try:
        import urllib.request
        r = urllib.request.urlopen("http://127.0.0.1:20128/v1/models", timeout=4)
        body = json.loads(r.read().decode())
        models = body.get("data", []) if isinstance(body, dict) else []
        out["g9router"]["models"] = [m.get("id") for m in models if isinstance(m, dict)][:60]
        out["g9router"]["model_count"] = len(out["g9router"]["models"])
    except Exception as e:
        out["g9router"]["model_error"] = str(e)[:120]
    return out


# ---------------- webui settings ----------------
def read_webui_settings() -> dict:
    text = _safe_read(WEBUI_SETTINGS)
    if not text:
        return {"available": False, "error": "settings.json tidak terbaca"}
    try:
        data = json.loads(text)
    except Exception as e:
        return {"available": False, "error": f"json error: {e}"}
    editable = {}
    for k, label in WEBUI_EDITABLE.items():
        if k in data:
            editable[k] = {"value": data[k], "label": label, "type": type(data[k]).__name__}
    return {"available": True, "editable": editable, "total_keys": len(data)}


def update_webui_setting(key: str, value) -> dict:
    if key not in WEBUI_EDITABLE:
        return {"ok": False, "error": f"Key '{key}' tidak diizinkan"}
    if not WEBUI_SETTINGS.exists():
        return {"ok": False, "error": "settings.json tidak ada"}
    # backup once per day
    if not WEBUI_SETTINGS_BAK.exists():
        shutil.copy2(WEBUI_SETTINGS, WEBUI_SETTINGS_BAK)
    try:
        data = json.loads(WEBUI_SETTINGS.read_text())
    except Exception as e:
        return {"ok": False, "error": f"json error: {e}"}
    # type coercion based on current value type
    cur = data.get(key)
    if isinstance(cur, bool):
        value = str(value).lower() in ("1", "true", "yes", "on")
    elif isinstance(cur, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = cur
    elif isinstance(cur, float):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = cur
    else:
        value = str(value)
    data[key] = value
    tmp = WEBUI_SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, WEBUI_SETTINGS)
    # try to reload webui settings? webui reads from disk on demand for most keys
    return {"ok": True, "key": key, "value": value}


# ---------------- service control ----------------
def list_services() -> list:
    rows = []
    for name in sorted(SERVICE_ALLOWLIST):
        if name in USER_SVMAP:
            state = ""
            try:
                r = subprocess.run(["sudo", "su", "-", "ubuntu", "-c",
                                    "XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active " + name],
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8, check=False)
                state = r.stdout.strip()
            except Exception:
                pass
            if state not in ("active", "inactive", "failed", "activating", "deactivating"):
                pats = USER_SVMAP.get(name, [name])
                try:
                    r = subprocess.run(["pgrep", "-f", "|".join(pats)], text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False)
                    state = "active" if r.stdout.strip() else "inactive"
                except Exception:
                    state = "inactive"
            en = subprocess.run(["systemctl", "is-enabled", name], capture_output=True, text=True, timeout=4)
            rows.append({"name": name, "state": state, "enabled": en.stdout.strip(), "pid": None, "memory_mb": 0})
        else:
            st = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=4)
            state = st.stdout.strip()
            if state not in ("active", "inactive", "failed", "activating", "deactivating"):
                state = "missing"
            en = subprocess.run(["systemctl", "is-enabled", name], capture_output=True, text=True, timeout=4)
            props = subprocess.run(["systemctl", "show", name, "--property=MainPID,MemoryCurrent,Description"], capture_output=True, text=True, timeout=4)
            info = dict(line.split("=", 1) for line in props.stdout.splitlines() if "=" in line)
            memory = info.get("MemoryCurrent", "0")
            rows.append({"name": name, "state": state, "enabled": en.stdout.strip(),
                         "pid": int(info.get("MainPID", 0) or 0),
                         "memory_mb": round(int(memory) / 1048576, 1) if memory.isdigit() else 0,
                         "description": info.get("Description", name)})
    return rows


def service_action(name: str, action: str) -> dict:
    if name not in SERVICE_ALLOWLIST:
        return {"ok": False, "error": f"Service '{name}' tidak diizinkan"}
    if action not in ("start", "stop", "restart", "enable", "disable"):
        return {"ok": False, "error": "action harus start/stop/restart/enable/disable"}
    # hermes-gateway is a --user service; others are system services
    is_user = name == "hermes-gateway"
    cmd = ["systemctl"]
    if is_user:
        cmd.append("--user")
    cmd.append(action)
    cmd.append(name)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        time.sleep(0.8)
        st = subprocess.run(["systemctl"] + (["--user"] if is_user else []) + ["is-active", name],
                            capture_output=True, text=True, timeout=4)
        return {"ok": r.returncode == 0, "action": action, "name": name,
                "state_after": st.stdout.strip(), "message": (r.stderr or r.stdout).strip()[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def update_default_model(model: str, provider: str = "") -> dict:
    """Update model.default/provider in ~/.hermes/config.yaml, keep everything else."""
    import yaml
    if not HERMES_CONFIG.exists():
        return {"ok": False, "error": "config.yaml tidak ada"}
    model = str(model).strip()
    provider = str(provider).strip()
    if not model or len(model) > 80:
        return {"ok": False, "error": "model tidak valid"}
    if provider and (len(provider) > 100 or not provider.startswith("custom:")):
        return {"ok": False, "error": "provider tidak valid"}
    try:
        data = yaml.safe_load(HERMES_CONFIG.read_text()) or {}
    except Exception as e:
        return {"ok": False, "error": f"yaml error: {e}"}
    data.setdefault("model", {})["default"] = model
    if provider:
        data["model"]["provider"] = provider
    # config.yaml is immutable (chattr +i) — temporarily lift, write, re-lock.
    def _attr(op):
        try:
            subprocess.run(["chattr", op, str(HERMES_CONFIG)], capture_output=True, text=True, timeout=5)
        except Exception:
            pass
    _attr("-i")
    try:
        tmp = HERMES_CONFIG.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False))
        os.replace(tmp, HERMES_CONFIG)
    finally:
        _attr("+i")
    # restart task-router so it reloads
    import subprocess
    r = subprocess.run(["systemctl", "--user", "restart", "task-router.service"], capture_output=True, text=True, timeout=15)
    return {"ok": True, "model": model, "provider": provider, "router_restart": r.returncode == 0, "message": (r.stderr or r.stdout).strip()[:200]}

