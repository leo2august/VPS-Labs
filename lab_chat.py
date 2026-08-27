"""VPS Sentinel Labs — model listing for chat."""
import os, yaml
from pathlib import Path

HERMES_DIR = Path(os.environ.get("LABS_HERMES_DIR", "/home/USER/.hermes"))
CONFIG = HERMES_DIR / "config.yaml"

GATEKEY_MODELS = [
    {"id": "gatekey-unlimited-deepseek-v4-flash", "label": "GateKey DeepSeek V4 Flash", "provider": "gatekey"},
    {"id": "gatekey-unlimited-deepseek-v4-flash-vision", "label": "GateKey DeepSeek V4 Vision", "provider": "gatekey"},
    {"id": "gatekey-unlimited-hy3", "label": "GateKey Hy3", "provider": "gatekey"},
    {"id": "gatekey-unlimited-mimo-v2.5", "label": "GateKey MiMo V2.5", "provider": "gatekey"},
]


def _provider_model_list(p):
    """Daftar model id dari sebuah provider — dukung field `models` (list/str) DAN `model` (str)."""
    out = []
    models = p.get("models", [])
    if isinstance(models, str):
        models = [models]
    for m in models:
        if isinstance(m, str) and m:
            out.append(m)
    single = p.get("model")
    if isinstance(single, str) and single and single not in out:
        out.append(single)
    return out


def _resolve_provider(data, model):
    """Temukan provider (custom_providers) yang menyediakan model tsb."""
    for p in data.get("custom_providers", []) or []:
        if not isinstance(p, dict):
            continue
        pname = p.get("name", "?")
        base = p.get("base_url") or ""
        if not base:
            continue
        for m in _provider_model_list(p):
            # cocok model langsung, atau model ber-prefix "provider/model"
            if m == model or (isinstance(m, str) and m.endswith("/" + model)):
                return {"provider": pname, "base_url": base, "api_key": p.get("api_key", ""),
                        "model": m, "label": m}
    return None


def list_available_models() -> dict:
    """Semua model yang bisa dipakai chat: GateKey + model dari config provider."""
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
        r = _resolve_provider(data, default_model)
        if r:
            out.append({"id": default_model, "label": r["label"], "provider": r["provider"]})
        else:
            out.append({"id": default_model, "label": default_model + " (default config)", "provider": "config"})
    for p in data.get("custom_providers", []) or []:
        if not isinstance(p, dict):
            continue
        pname = p.get("name", "?")
        for m in _provider_model_list(p):
            if not m or m in seen:
                continue
            seen.add(m)
            out.append({"id": m, "label": m, "provider": pname})
    return {"models": out}
