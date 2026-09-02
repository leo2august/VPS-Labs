"""VPS Labs — 9router snapshot reader (works after 9router is stopped)."""
import json
from pathlib import Path

SNAPSHOT = Path("/home/ubuntu/vps-audit/static/9router-snapshot.json")


def get_snapshot() -> dict:
    if SNAPSHOT.exists():
        try:
            return json.loads(SNAPSHOT.read_text())
        except Exception:
            return {"error": "snapshot corrupt"}
    return {"error": "snapshot belum ada"}


def models_for_picker() -> dict:
    """Merge config provider models + 9router snapshot models for the /model picker."""
    import yaml
    seen = set()
    out = []
    snap = get_snapshot()
    if isinstance(snap, dict) and "models" in snap:
        for m in snap["models"]:
            if m not in seen:
                seen.add(m)
                out.append({"id": m, "provider": "9router", "group": "9router"})
    try:
        cfg = yaml.safe_load(open("/home/ubuntu/.hermes/config.yaml")) or {}
    except Exception:
        cfg = {}
    for p in cfg.get("custom_providers", []) or []:
        if not isinstance(p, dict):
            continue
        pname = p.get("name", "?")
        models = p.get("models", [])
        if isinstance(models, str):
            models = [models]
        for m in models:
            if m and m not in seen:
                seen.add(m)
                out.append({"id": m, "provider": pname, "group": "config"})
    return {"models": out}
