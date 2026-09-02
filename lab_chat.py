"""VPS Labs — model listing for chat."""
import json, os, sqlite3, yaml
from pathlib import Path

HERMES_DIR = Path("/home/ubuntu/.hermes")
CONFIG = HERMES_DIR / "config.yaml"
ROUTER_DB = Path("/home/ubuntu/.9router/db/data.sqlite")


def _router_models():
    """Model per akun 9router; ID koneksi ikut provider agar akun bisa dipilih."""
    if not ROUTER_DB.exists():
        return []
    con = sqlite3.connect(ROUTER_DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id,provider,name,data FROM providerConnections WHERE isActive=1 ORDER BY provider,priority,name"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = []
    for row in rows:
        try:
            data = json.loads(row["data"] or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        models = sorted(k[10:] for k, v in data.items() if k.startswith("modelLock_") and v)
        # Kiro reset kadang mengosongkan semua modelLock; akun tetap valid untuk model utama.
        if not models and row["provider"] == "kiro":
            models = ["kr/claude-sonnet-4.5"]
        for model in models:
            if "/" not in model:
                model = f'{row["provider"]}/{model}'
            out.append({"id": model, "label": model,
                        "provider": f'{row["name"] or row["provider"]}@@{row["id"]}'})
    return out

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


def _provider_label(p):
    """Label ringkas untuk dropdown: nama provider + akun bila ada."""
    name = str(p.get("name", "?"))
    return name


def list_available_models() -> dict:
    """Semua model yang bisa dipakai chat.

    Sinkron 1:1 dengan config.yaml:
    - Setiap provider (termasuk relay/akun ganda) tampil dengan modelnya sendiri.
    - Dedup per (provider, model) — B.AI, B.AI Relay 2, B.AI Relay 3 masing-masing
      muncul dengan modelnya, tidak saling menimpa.
    """
    out = _router_models()
    seen = {f"{m['provider']}|{m['id']}" for m in out}  # key: provider|model
    for m in GATEKEY_MODELS:
        key = f"gatekey|{m['id']}"
        if key not in seen:
            seen.add(key)
            out.append(m)
    try:
        data = yaml.safe_load(CONFIG.read_text()) or {}
    except Exception:
        return {"models": out}
    default_model = (data.get("model") or {}).get("default", "")
    for p in data.get("custom_providers", []) or []:
        if not isinstance(p, dict):
            continue
        pname = _provider_label(p)
        for m in _provider_model_list(p):
            if not m:
                continue
            key = f"{pname}|{m}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"id": m, "label": m, "provider": pname})
    # default model (config) — pastikan selalu ada di daftar
    if default_model and not any(x["id"] == default_model for x in out):
        out.append({"id": default_model, "label": default_model + " (default)", "provider": "config"})
    # urut: GateKey dulu, lalu alfabetis per provider
    def sort_key(x):
        return (0 if x["provider"] == "gatekey" else 1, x["provider"].lower(), x["id"].lower())
    out.sort(key=sort_key)
    return {"models": out}
