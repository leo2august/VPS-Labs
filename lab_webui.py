"""Labs — SOUL editor, session continue, 9router detail."""
import os
import json
import subprocess
from pathlib import Path

import urllib.request

HERMES_DIR = Path(os.environ.get('LABS_HERMES_DIR', '/home/USER/.hermes'))
SOUL_FILE = HERMES_DIR / "SOUL.md"
WEBUI_SESSIONS_DIR = HERMES_DIR / "webui" / "sessions"

ROUTER_URL = "http://127.0.0.1:20128"


def get_soul() -> dict:
    if SOUL_FILE.exists():
        return {"ok": True, "content": SOUL_FILE.read_text(errors="replace")}
    return {"ok": False, "error": "SOUL.md tidak ditemukan"}


def save_soul(content: str) -> dict:
    try:
        SOUL_FILE.write_text(content)
        return {"ok": True, "chars": len(content)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def continue_session(sid: str, message: str, model: str = "gatekey-unlimited-deepseek-v4-flash") -> dict:
    """Load session messages, append user message, send to GateKey, append reply."""
    f = WEBUI_SESSIONS_DIR / f"{sid}.json"
    if not f.exists():
        return {"ok": False, "error": "session tidak ditemukan"}
    try:
        data = json.loads(f.read_text(errors="replace"))
    except Exception as e:
        return {"ok": False, "error": f"gagal baca: {e}"}
    msgs = []
    for m in data.get("messages") or []:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
        if role in ("user", "assistant") and str(content).strip():
            msgs.append({"role": role, "content": str(content)[:6000]})
    msgs.append({"role": "user", "content": message})
    msgs = msgs[-20:]
    # call GateKey
    body = json.dumps({"model": model, "messages": msgs, "max_tokens": 1500, "stream": False}).encode()
    req = urllib.request.Request("https://ai.gatekey.cloud/v1/chat/completions", data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ.get("LABS_GATEKEY_KEY", "")})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            raw = r.read().decode("utf-8", "replace")
        d = json.loads(raw)
        reply = d["choices"][0]["message"].get("content") or ""
    except Exception as e:
        return {"ok": False, "error": f"LLM gagal: {str(e)[:150]}"}
    # persist: append user msg + assistant reply
    data.setdefault("messages", []).append({"role": "user", "content": message,
                                            "timestamp": __import__("time").time()})
    data["messages"].append({"role": "assistant", "content": reply,
                             "timestamp": __import__("time").time()})
    try:
        f.write_text(json.dumps(data, ensure_ascii=False))
    except Exception as e:
        return {"ok": False, "error": f"gagal simpan: {str(e)[:150]}"}
    return {"ok": True, "reply": reply, "session_id": sid}


def router_status() -> dict:
    """9router live status — works while 9router is up; falls back to snapshot."""
    try:
        req = urllib.request.Request(ROUTER_URL + "/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            health = json.loads(r.read().decode())
        up = bool(health.get("ok"))
    except Exception:
        up = False
    providers = []
    if up:
        try:
            req = urllib.request.Request(ROUTER_URL + "/api/providers", method="GET")
            with urllib.request.urlopen(req, timeout=8) as r:
                prow = json.loads(r.read().decode())
            rows = prow.get("connections") or prow.get("data") or prow.get("providers") or []
            if isinstance(prow, list):
                rows = prow
            for p in rows[:120]:
                if not isinstance(p, dict):
                    continue
                providers.append({
                    "name": str(p.get("name") or p.get("id") or "?")[:60],
                    "status": str(p.get("status") or (p.get("active") and "active") or "?")[:20],
                })
        except Exception:
            pass
    # limits from snapshot
    try:
        snap = json.loads(Path("/home/ubuntu/vps-audit/static/9router-snapshot.json").read_text())
        limits = {"providers_total": snap.get("providers_total", 0),
                  "providers_active": snap.get("providers_active", 0),
                  "models_total": snap.get("models_total", 0)}
    except Exception:
        limits = {}
    return {"ok": True, "up": up, "live_providers": len(providers),
            "providers": providers[:60], "limits": limits}
