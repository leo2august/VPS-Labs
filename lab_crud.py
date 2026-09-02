"""VPS Labs — CRUD: providers (config.yaml), skills, sessions.
9router-style provider management + WebUI-style content editing.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import yaml

HERMES_DIR = Path("/home/ubuntu/.hermes")
CONFIG = HERMES_DIR / "config.yaml"
SKILLS_DIR = HERMES_DIR / "skills"
WEBUI_SESSIONS_DIR = HERMES_DIR / "webui" / "sessions"


def _chattr(op, path):
    try:
        subprocess.run(["chattr", op, str(path)], capture_output=True, text=True, timeout=5)
    except Exception:
        pass


# ---------------- Provider CRUD (config.yaml) ----------------
def _load_cfg():
    try:
        return yaml.safe_load(CONFIG.read_text()) or {}
    except Exception as e:
        raise ValueError(f"config.yaml rusak: {e}")


def _save_cfg(data):
    """Atomically write config.yaml (lifts immutable flag, re-locks)."""
    _chattr("-i", CONFIG)
    try:
        tmp = CONFIG.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False))
        os.replace(tmp, CONFIG)
    finally:
        _chattr("+i", CONFIG)


def list_providers() -> dict:
    """Full provider list with models (no secrets)."""
    try:
        data = _load_cfg()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    provs = []
    for p in data.get("custom_providers", []) or []:
        if not isinstance(p, dict):
            continue
        models = p.get("models", [])
        if not isinstance(models, list):
            models = [str(models)] if models else []
        single_model = p.get("model") or p.get("default_model")
        if single_model and single_model not in models:
            models.insert(0, str(single_model))
        provs.append({
            "name": p.get("name", "?"),
            "base_url": p.get("base_url", ""),
            "api_key": bool(p.get("api_key") or p.get("apiKey")),
            "models": models,
            "default_model": str(single_model or (models[0] if models else "")),
            "enabled": p.get("enabled", True),
            "index": provs and provs[-1]["index"] + 1 or 0,
        })
    return {"ok": True, "providers": provs}


def add_provider(name: str, base_url: str, api_key: str, models: list) -> dict:
    name = str(name).strip()
    base_url = str(base_url).strip()
    if not name or not base_url:
        return {"ok": False, "error": "name & base_url wajib"}
    try:
        data = _load_cfg()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    provs = data.setdefault("custom_providers", [])
    # check duplicate
    for p in provs:
        if p.get("name", "").lower() == name.lower():
            return {"ok": False, "error": "provider dengan nama itu sudah ada"}
    models = models if isinstance(models, list) else ([models] if models else [])
    entry = {"name": name, "base_url": base_url, "models": models or []}
    if models:
        entry["model"] = str(models[0])
    if api_key:
        entry["api_key"] = str(api_key)
    provs.append(entry)
    try:
        _save_cfg(data)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "added": name, "provider_count": len(provs)}


def update_provider(name: str, base_url=None, api_key=None, models=None, enabled=None) -> dict:
    try:
        data = _load_cfg()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    provs = data.get("custom_providers", [])
    for p in provs:
        if p.get("name", "") == name:
            if base_url is not None:
                p["base_url"] = str(base_url)
            if api_key:
                p["api_key"] = str(api_key)
            if models is not None:
                p["models"] = models if isinstance(models, list) else [models]
                if p["models"]:
                    p["model"] = str(p["models"][0])
            if enabled is not None:
                p["enabled"] = bool(enabled)
            try:
                _save_cfg(data)
            except Exception as e:
                return {"ok": False, "error": str(e)[:200]}
            return {"ok": True, "updated": name}
    return {"ok": False, "error": "provider tidak ditemukan"}


def delete_provider(name: str) -> dict:
    try:
        data = _load_cfg()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    provs = data.get("custom_providers", [])
    new_provs = [p for p in provs if p.get("name", "") != name]
    if len(new_provs) == len(provs):
        return {"ok": False, "error": "provider tidak ditemukan"}
    data["custom_providers"] = new_provs
    try:
        _save_cfg(data)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "deleted": name, "provider_count": len(new_provs)}


# ---------------- Skills CRUD ----------------
def skill_path(name: str):
    if not SKILLS_DIR.is_dir():
        return None
    for cat_dir in SKILLS_DIR.iterdir():
        sk = cat_dir / name
        if sk.is_dir():
            return sk
    return None


def update_skill(name: str, content: str) -> dict:
    sk = skill_path(name)
    if not sk:
        return {"ok": False, "error": "skill tidak ditemukan"}
    try:
        (sk / "SKILL.md").write_text(content)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "updated": name, "chars": len(content)}


def delete_skill(name: str) -> dict:
    sk = skill_path(name)
    if not sk:
        return {"ok": False, "error": "skill tidak ditemukan"}
    try:
        shutil.rmtree(sk)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "deleted": name}


# ---------------- Sessions ----------------
def delete_session(sid: str) -> dict:
    f = WEBUI_SESSIONS_DIR / f"{sid}.json"
    if not f.exists():
        return {"ok": False, "error": "session tidak ditemukan"}
    try:
        f.unlink()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "deleted": sid}
