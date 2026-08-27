"""VPS Sentinel Labs — WebUI-feature integration: chat, skills, memory, sessions.
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

HERMES_DIR = Path(os.environ.get("LABS_HERMES_DIR", "/home/USER/.hermes"))
CONFIG = HERMES_DIR / "config.yaml"
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
import yaml

GATEKEY_URL = "https://ai.gatekey.cloud/v1/chat/completions"
# GateKey API key di-resolve dari config (provider "Gatekey"), jangan hardcode.
# GATEKEY_KEY lama dihapus — key dibaca dari config saat chat dipanggil.


def _gatekey_key():
    try:
        data = yaml.safe_load(CONFIG.read_text()) or {}
        for p in data.get("custom_providers", []) or []:
            if isinstance(p, dict) and "gatekey" in (p.get("name", "")).lower():
                return p.get("api_key", "") or ""
    except Exception:
        pass
    return ""


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


def _resolve_endpoint(model):
    """Tentukan (base_url, api_key, model_request, extra_headers) dari config.

    Urutan:
    1. model gatekey-* -> endpoint GateKey dari config
    2. cocok model dengan custom_providers (persis atau suffix) — pakai model/models field
    3. fallback default provider dari config
    """
    try:
        data = yaml.safe_load(CONFIG.read_text()) or {}
    except Exception:
        return None
    provs = data.get("custom_providers", []) or []
    # 1. GateKey
    if str(model).startswith("gatekey-unlimited-"):
        key = _gatekey_key()
        if key:
            return (GATEKEY_URL, key, model, {})
    # 2. cocok di custom providers
    for p in provs:
        if not isinstance(p, dict):
            continue
        base = p.get("base_url")
        key = p.get("api_key", "")
        if not base:
            continue
        for m in _provider_model_list(p):
            if m == model or m.endswith("/" + str(model)):
                return (base, key, m, p.get("extra_headers") or {})
    # 3. fallback default provider
    default_model = (data.get("model") or {}).get("default", "")
    if default_model:
        for p in provs:
            if not isinstance(p, dict):
                continue
            if default_model in _provider_model_list(p):
                return (p.get("base_url"), p.get("api_key", ""), default_model, p.get("extra_headers") or {})
    return None


def _soul_system():
    """System prompt untuk chat Labs.

    Gabungkan persona aktif dari config.yaml (agent.system_prompt — LeoAI) dengan
    SOUL.md (identitas root) + MEMORY.md/USER.md (memory Hermes) + daftar skill.
    Kalau config kosong, fallback ke SOUL.md saja.
    """
    parts = []
    try:
        cfg = yaml.safe_load(CONFIG.read_text()) or {}
        sp = (cfg.get("agent") or {}).get("system_prompt", "")
        if isinstance(sp, str) and sp.strip():
            parts.append(sp.strip())
    except Exception:
        pass
    try:
        p = HERMES_DIR / "SOUL.md"
        if p.exists():
            txt = p.read_text(errors="replace").strip()
            if txt:
                parts.append(txt)
    except Exception:
        pass
    mem = _memory_context()
    if mem:
        parts.append(mem)
    sk = _skills_context()
    if sk:
        parts.append(sk)
    vps = _vps_context()
    if vps:
        parts.append(vps)
    return "\n\n".join(parts).strip()


def _vps_context():
    """Info ringkas VPS tempat Labs berjalan — dipakai chat untuk konteks server."""
    try:
        import platform
        import socket
        host = socket.gethostname()
        osinfo = platform.platform()
        try:
            import psutil
            vm = psutil.virtual_memory()
            ram = f"{vm.total // (1024**3)} GB"
            up = time.time() - psutil.boot_time()
            uptime = f"{int(up // 86400)}h {int(up % 86400 // 3600)}j {int(up % 3600 // 60)}m"
            disk = psutil.disk_usage("/").percent
            load = os.getloadavg()
            load_s = ", ".join(f"{x:.2f}" for x in load)
            ram_pct = vm.percent
            extra = f" RAM: {ram} ({ram_pct}% dipakai), Disk: {disk}%, Load: {load_s}"
        except Exception:
            uptime, extra = "?", ""
        return f"### LINGKUNGAN VPS\nHost: {host}\nOS: {osinfo}\nUptime: {uptime}{extra}"
    except Exception:
        return ""


def _memory_context():
    """MEMORY.md + USER.md Hermes sebagai konteks — biar chat tahu memory asisten."""
    out = []
    for fname in ("MEMORY.md", "USER.md"):
        try:
            p = MEMORIES_DIR / fname
            if p.exists():
                txt = p.read_text(errors="replace").strip()
                if txt:
                    label = "MEMORY (catatan asisten lintas sesi)" if fname == "MEMORY.md" else "PROFIL USER"
                    out.append(f"### {label}\n{txt}")
        except Exception:
            pass
    return "\n\n".join(out)


def _skills_context(max_len=6000):
    """Ringkasan skill Hermes — nama + deskripsi, dipakai chat utk tahu kapabilitas."""
    try:
        if not SKILLS_DIR.is_dir():
            return ""
        rows = []
        for cat in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
            for sk in sorted(p for p in cat.iterdir() if p.is_dir()):
                f = sk / "SKILL.md"
                if not f.exists():
                    continue
                raw = f.read_text(errors="replace")[:800]
                m = re.search(r"^title:\s*(.+)$", raw, re.M)
                title = m.group(1).strip().strip('"').strip("'") if m else sk.name
                m = re.search(r"^description:\s*\S*\s*\n?(\S.*)$", raw, re.M)
                desc = (m.group(1).strip()[:120] if m else "")
                if not desc:
                    m = re.search(r"^description:\s*(.+)$", raw, re.M)
                    desc = (m.group(1).strip()[:120] if m else "")
                rows.append(f"- {title}: {desc}" if desc else f"- {title}")
        text = "\n".join(rows)
        return "### SKILLS (kapabilitas asisten)\n" + text[:max_len]
    except Exception:
        return ""


def chat(messages: list, model: str = "", provider: str = "", max_tokens: int = 1500) -> dict:
    """Chat completion — resolve endpoint dari config berdasarkan model/provider.

    Provider mana pun dari dropdown Labs (GateKey, B.AI, LimitRouter, Tamandata, dll)
    dipakai sesuai base_url + api_key yang terdaftar di config.yaml.
    System prompt dari SOUL.md (persona) disuntikkan ke awal riwayat.
    """
    # resolve endpoint
    ep = _resolve_endpoint(model) if model else None
    if not ep:
        # coba resolve by provider name
        ep = _resolve_provider_by_name(provider, model)
    if not ep:
        return {"ok": False, "error": f"Model '{model}' tidak ditemukan di config. Pilih model dari dropdown."}
    base_url, api_key, request_model, extra_headers = ep
    if not api_key:
        return {"ok": False, "error": f"Provider untuk model '{model}' tidak punya API key di config."}

    msgs = list(messages[-20:])
    soul = _soul_system()
    if soul:
        # sisipkan system prompt di awal (jangan duplikat)
        if not any(m.get("role") == "system" for m in msgs):
            msgs.insert(0, {"role": "system", "content": soul})

    body = json.dumps({"model": request_model, "messages": msgs,
                        "max_tokens": max_tokens, "stream": False}).encode()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + api_key}
    if isinstance(extra_headers, dict):
        for k, v in extra_headers.items():
            if k.lower() not in ("content-type", "authorization"):
                headers[str(k)] = str(v)
    req = urllib.request.Request(base_url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            raw = r.read().decode("utf-8", "replace")
        d = json.loads(raw)
        msg = d["choices"][0]["message"]
        reply = msg.get("content") or ""
        if not reply.strip() and msg.get("reasoning_content"):
            reply = msg["reasoning_content"]
        return {"ok": True, "model": d.get("model", request_model), "reply": reply.strip()}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}",
                "body": e.read()[:300].decode("utf-8", "replace")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def _resolve_provider_by_name(provider, model):
    """Fallback: resolve endpoint dengan nama provider eksplisit dari config."""
    if not provider:
        return None
    try:
        data = yaml.safe_load(CONFIG.read_text()) or {}
    except Exception:
        return None
    for p in data.get("custom_providers", []) or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("name", "")).lower() == str(provider).lower():
            m = model or next(iter(_provider_model_list(p)), "")
            return (p.get("base_url"), p.get("api_key", ""), m, p.get("extra_headers") or {})
    return None


HERMES_PY = os.environ.get("LABS_HERMES_PY", "/home/USER/.hermes/hermes-agent/venv/bin/python")


def agent_chat(prompt, model="", provider="", max_seconds=240):
    """Delegasi prompt ke Hermes agent CLI — punya memory, skill, terminal, file.

    Ini membuat chat Labs bertindak seperti WebUI: agent dapat menjalankan proses,
    membaca file, dan memakai skill/memory nyata Hermes. Model default Hermes
    dipakai bila model/provider tidak diberikan; kalau gagal 402/403, fallback
    ke GateKey (teruji jalan).
    """
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "Prompt kosong"}
    import shlex
    cmd = [HERMES_PY, "-m", "hermes_cli.main", "-z", prompt, "--cli"]
    if model:
        cmd += ["-m", model]
    if provider:
        cmd += ["--provider", provider]
    env = dict(os.environ,
               HERMES_ACCEPT_HOOKS="1", XDG_RUNTIME_DIR="/run/user/1000",
               HERMES_SAFE_MODE="0", TERM="dumb",
               HOME=os.environ.get("LABS_HERMES_HOME", "/home/USER"),
               HERMES_HOME=os.environ.get("LABS_HERMES_DIR", "/home/USER/.hermes"),
               USER=os.environ.get("LABS_SYSTEM_USER", "USER"), LOGNAME=os.environ.get("LABS_SYSTEM_USER", "USER"))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=max_seconds, env=env)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out:
            return {"ok": True, "reply": out, "agent": True}
        # gagal — coba fallback GateKey
        err = (r.stderr or "").strip()[-200:]
        if "402" in err or "403" in err or "balance" in err.lower():
            return _agent_chat_gatekey(prompt)
        return {"ok": False, "error": err or "Agent gagal (exit %d)" % r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Agent timeout ({max_seconds}s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def _agent_chat_gatekey(prompt):
    """Fallback: kirim prompt ke GateKey (bukan agent penuh, tapi jalan)."""
    ep = _resolve_endpoint("gatekey-unlimited-deepseek-v4-flash")
    if not ep:
        return {"ok": False, "error": "GateKey tidak tersedia"}
    base_url, api_key, request_model, extra_headers = ep
    soul = _soul_system()
    msgs = []
    if soul:
        msgs.append({"role": "system", "content": soul})
    msgs.append({"role": "user", "content": prompt})
    body = json.dumps({"model": request_model, "messages": msgs,
                       "max_tokens": 2000, "stream": False}).encode()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + api_key}
    if isinstance(extra_headers, dict):
        for k, v in extra_headers.items():
            if k.lower() not in ("content-type", "authorization"):
                headers[str(k)] = str(v)
    try:
        req = urllib.request.Request(base_url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=240) as r:
            raw = r.read().decode("utf-8", "replace")
        d = json.loads(raw)
        msg = d["choices"][0]["message"]
        reply = msg.get("content") or ""
        if not reply.strip() and msg.get("reasoning_content"):
            reply = msg["reasoning_content"]
        return {"ok": True, "reply": reply.strip(), "agent": True, "fallback": "gatekey"}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read()[:150].decode('utf-8','replace')}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
