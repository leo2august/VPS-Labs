"""Labs — WebUI-feature integration: chat, skills, memory, sessions.
Reads local Hermes data + proxies chat via 9router. All read+local actions.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import urllib.error
import urllib.request

HERMES_DIR = Path(os.environ.get('LABS_HERMES_DIR', '/home/USER/.hermes'))
SKILLS_DIR = HERMES_DIR / "skills"
MEMORIES_DIR = HERMES_DIR / "memories"
WEBUI_SESSIONS_DIR = HERMES_DIR / "webui" / "sessions"
ROUTER_URL = "http://127.0.0.1:20128/v1/chat/completions"

# ---- Skills ----
def list_skills() -> dict:
    """Tree: category -> [{name, title, description, path, mtime}]"""
    cats = {}
    if not SKILLS_DIR.is_dir():
        return {"categories": []}
    for cat_dir in sorted(SKILLS_DIR.iterdir()):
        if cat_dir.is_dir():
            skills = []
            for sk_dir in sorted(cat_dir.iterdir()):
                if not sk_dir.is_dir():
                    continue
                sk = sk_dir / "SKILL.md"
                title = name = sk_dir.name
                desc = ""
                if sk.exists():
                    raw = sk.read_text(errors="replace")[:1500]
                    m = re.search(r"^title:\s*(.+)$", raw, re.M)
                    if m:
                        title = m.group(1).strip().strip('"').strip("'")
                    m = re.search(r"^description:\s*\|?\s*$(.*?)^(?:trigger|---)", raw, re.M | re.S)
                    if m:
                        desc = " ".join(m.group(1).split())[:220]
                    if not desc:
                        m = re.search(r"^description:\s*(.+)$", raw, re.M)
                        if m:
                            desc = m.group(1).strip()[:220]
                try:
                    mtime = sk.stat().st_mtime if sk.exists() else sk_dir.stat().st_mtime
                except Exception:
                    mtime = 0
                skills.append({"name": name, "title": title, "description": desc,
                               "path": str(sk_dir), "mtime": int(mtime)})
            if skills:
                cats[cat_dir.name] = skills
    return {"categories": cats}


def get_skill(name: str) -> dict:
    """Read a single skill's SKILL.md + linked files listing."""
    for cat_dir in SKILLS_DIR.iterdir() if SKILLS_DIR.is_dir() else []:
        sk_dir = cat_dir / name
        if sk_dir.is_dir():
            sk = sk_dir / "SKILL.md"
            files = []
            for sub in ["references", "templates", "scripts", "assets"]:
                d = sk_dir / sub
                if d.is_dir():
                    for f in sorted(d.rglob("*")):
                        if f.is_file():
                            files.append(str(f.relative_to(sk_dir)))
            return {"name": name, "category": cat_dir.name,
                    "content": sk.read_text(errors="replace") if sk.exists() else "",
                    "files": files[:100]}
    return {"error": "skill tidak ditemukan"}


# ---- Memory ----
def get_memories() -> dict:
    out = {}
    for fn in ["MEMORY.md", "USER.md"]:
        f = MEMORIES_DIR / fn
        out[fn] = f.read_text(errors="replace") if f.exists() else ""
    return out


def save_memory(fn: str, content: str) -> dict:
    if fn not in ("MEMORY.md", "USER.md"):
        return {"ok": False, "error": "file tidak valid"}
    f = MEMORIES_DIR / fn
    f.write_text(content)
    return {"ok": True, "file": fn, "chars": len(content)}


# ---- Sessions ----
def list_sessions(limit: int = 60) -> dict:
    """Recent WebUI sessions, newest first."""
    items = []
    if WEBUI_SESSIONS_DIR.is_dir():
        for f in sorted(WEBUI_SESSIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
            if f.name == "_index.json":
                continue
            try:
                data = json.loads(f.read_text(errors="replace"))
            except Exception:
                continue
            msgs = data.get("messages") or []
            title = data.get("title") or data.get("name") or ""
            if not title and msgs:
                title = (msgs[0].get("content") or "")[:60] if msgs else ""
            first = msgs[0].get("timestamp", 0) if msgs else data.get("created_at", 0)
            last = msgs[-1].get("timestamp", first) if msgs else first
            items.append({
                "id": f.stem,
                "title": (title or "untitled")[:80],
                "count": len(msgs),
                "first": int(first) if isinstance(first, (int, float)) else 0,
                "last": int(last) if isinstance(last, (int, float)) else 0,
            })
    return {"sessions": items}


def get_session(sid: str) -> dict:
    f = WEBUI_SESSIONS_DIR / f"{sid}.json"
    if not f.exists():
        return {"error": "session tidak ditemukan"}
    try:
        data = json.loads(f.read_text(errors="replace"))
    except Exception as e:
        return {"error": f"gagal baca: {e}"}
    msgs = []
    for m in data.get("messages") or []:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
        msgs.append({"role": role, "content": str(content)[:2000],
                     "ts": m.get("timestamp", 0)})
    return {"id": sid, "title": data.get("title", "untitled"), "messages": msgs}


# ---- Chat ----
GATEKEY_URL = os.environ.get('LABS_GATEKEY_URL', 'https://ai.gatekey.cloud/v1/chat/completions')
GATEKEY_KEY = os.environ.get('LABS_GATEKEY_KEY', '')
GATEKEY_MODELS = ["gatekey-unlimited-deepseek-v4-flash", "gatekey-unlimited-mimo-v2.5"]

def chat(messages: list, model: str = "gatekey-unlimited-deepseek-v4-flash",
         max_tokens: int = 1500) -> dict:
    """Chat completion via GateKey (verified working on this box)."""
    body = json.dumps({"model": model, "messages": messages[-20:],
                       "max_tokens": max_tokens, "stream": False}).encode()
    req = urllib.request.Request(GATEKEY_URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + GATEKEY_KEY})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            raw = r.read().decode("utf-8", "replace")
        d = json.loads(raw)
        msg = d["choices"][0]["message"]
        reply = msg.get("content") or ""
        # some models stream reasoning into content separately; fall back to reasoning if empty
        if not reply.strip() and msg.get("reasoning_content"):
            reply = msg["reasoning_content"]
        return {"ok": True, "model": d.get("model", model), "reply": reply.strip()}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}",
                "body": e.read()[:300].decode("utf-8", "replace")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
