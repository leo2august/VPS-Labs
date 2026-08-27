"""Labs — model listing for chat."""
import os
import yaml
from pathlib import Path

HERMES_DIR = Path(os.environ.get('LABS_HERMES_DIR', '/home/USER/.hermes'))
CONFIG = HERMES_DIR / "config.yaml"

GATEKEY_MODELS = [
    {"id": "gatekey-unlimited-deepseek-v4-flash", "label": "GateKey DeepSeek V4 Flash", "provider": "gatekey"},
    {"id": "gatekey-unlimited-deepseek-v4-flash-vision", "label": "GateKey DeepSeek V4 Vision", "provider": "gatekey"},
    {"id": "gatekey-unlimited-hy3", "label": "GateKey Hy3", "provider": "gatekey"},
    {"id": "gatekey-unlimited-mimo-v2.5", "label": "GateKey MiMo V2.5", "provider": "gatekey"},
]

def list_available_models() -> dict:
    """Return all models that can be used for chat: GateKey models + config provider models."""
    seen = set()
    out = list(GATEKEY_MODELS)
    for m in GATEKEY_MODELS:
        seen.add(m["id"])
    try:
        data = yaml.safe_load(CONFIG.read_text()) or {}
    except Exception:
        return {"models": out}
    default_model = (data.get("model") or {}).get("default", "")
    if default_model and default_model not in seen:
        seen.add(default_model)
        out.append({"id": default_model, "label": default_model + " (default config)", "provider": "config"})
    for p in data.get("custom_providers", []) or []:
        if not isinstance(p, dict):
            continue
        pname = p.get("name", "?")
        models = p.get("models", [])
        if isinstance(models, str):
            models = [models]
        for m in models:
            if m and m not in seen:
                seen.add(m)
                out.append({"id": m, "label": m, "provider": pname})
    return {"models": out}