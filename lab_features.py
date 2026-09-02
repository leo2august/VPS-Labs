"""VPS Labs — WebUI-feature integration: chat, skills, memory, sessions.
Reads local Hermes data + proxies chat via 9router. All read+local actions.
"""
import json
import os
import re
import subprocess
import shutil
import time
from pathlib import Path

import urllib.error
import urllib.request

import lab_profiles

HERMES_DIR = Path("/home/ubuntu/.hermes")
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


def _gatekey_key(config=CONFIG):
    try:
        data = yaml.safe_load(config.read_text()) or {}
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


def _resolve_endpoint(model, config=CONFIG):
    """Tentukan (base_url, api_key, model_request, extra_headers) dari config.

    Urutan:
    1. model gatekey-* -> endpoint GateKey dari config
    2. cocok model dengan custom_providers (persis atau suffix) — pakai model/models field
    3. fallback default provider dari config
    """
    try:
        data = yaml.safe_load(config.read_text()) or {}
    except Exception:
        return None
    provs = data.get("custom_providers", []) or []
    # 1. GateKey
    if str(model).startswith("gatekey-unlimited-"):
        key = _gatekey_key(config)
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


def _soul_system(home=HERMES_DIR):
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
        p = home / "SOUL.md"
        if p.exists():
            txt = p.read_text(errors="replace").strip()
            if txt:
                parts.append(txt)
    except Exception:
        pass
    mem = _memory_context(home)
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


def chat(messages: list, model: str = "", provider: str = "", max_tokens: int = 1500,
         profile: str = "default") -> dict:
    """Chat completion — resolve endpoint dari config berdasarkan model/provider.

    Provider mana pun dari dropdown Labs (GateKey, B.AI, LimitRouter, Tamandata, dll)
    dipakai sesuai base_url + api_key yang terdaftar di config.yaml.
    System prompt dari SOUL.md (persona) disuntikkan ke awal riwayat.
    """
    # Model virtual GateKey punya resolver khusus. Selain itu provider dropdown
    # bersifat otoritatif karena model sama bisa ada pada beberapa relay.
    home = lab_profiles.profile_home(profile)
    config = home / "config.yaml"
    if str(model).startswith("gatekey-unlimited-"):
        ep = _resolve_endpoint(model, config)
    else:
        ep = _resolve_provider_by_name(provider, model, config) if provider else None
        if not ep:
            ep = _resolve_endpoint(model, config) if model else None
    if not ep:
        return {"ok": False, "error": f"Model '{model}' tidak ditemukan di config. Pilih model dari dropdown."}
    base_url, api_key, request_model, extra_headers = ep
    if not api_key:
        return {"ok": False, "error": f"Provider untuk model '{model}' tidak punya API key di config."}

    msgs = list(messages[-20:])
    soul = _soul_system(home)
    if soul:
        # sisipkan system prompt di awal (jangan duplikat)
        if not any(m.get("role") == "system" for m in msgs):
            msgs.insert(0, {"role": "system", "content": soul})

    body = json.dumps({"model": request_model, "messages": msgs,
                        "max_tokens": max_tokens, "stream": False}).encode()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + api_key,
               # Beberapa relay Vercel memblokir User-Agent urllib default (403).
               # UA openai-python SDK biar sama seperti gateway (telegram/WebUI) yang lancar.
               "User-Agent": "OpenAI/Python 1.30.0"}
    if isinstance(extra_headers, dict):
        for k, v in extra_headers.items():
            if k.lower() not in ("content-type", "authorization"):
                headers[str(k)] = str(v)
    # Normalisasi URL: endpoint inference wajib berakhiran /chat/completions.
    # base_url di config bisa berupa root relay (https://relay-...vercel.app),
    # base /v1 (https://api.x.com/v1), atau path lengkap — kalau tidak di-normalisasi,
    # request nyasar ke path non-inference → HTTP 403 "node only allows inference API paths".
    url = str(base_url or "").strip().rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    req = urllib.request.Request(url, data=body, headers=headers)
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


def _resolve_provider_by_name(provider, model, config=CONFIG):
    """Resolve provider config; suffix @@UUID memilih akun 9router tertentu."""
    if not provider:
        return None
    if "@@" in provider:
        _, connection_id = provider.rsplit("@@", 1)
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", connection_id):
            return None
        return (ROUTER_URL, "9router", model, {"x-connection-id": connection_id})
    try:
        data = yaml.safe_load(config.read_text()) or {}
    except Exception:
        return None
    for p in data.get("custom_providers", []) or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("name", "")).lower() == str(provider).lower():
            m = model or next(iter(_provider_model_list(p)), "")
            return (p.get("base_url"), p.get("api_key", ""), m, p.get("extra_headers") or {})
    return None


HERMES_PY = "/home/ubuntu/.hermes/hermes-agent/venv/bin/python"

# ---- Agent job manager (async) ----
import threading
AGENT_JOBS = {}
_AGENT_LOCK = threading.Lock()
AGENT_JOB_DIR = Path("/home/ubuntu/vps-audit/data/agent-jobs")
ATTACHMENT_DIR = Path("/home/ubuntu/vps-audit/data/attachments")


def _persist_agent_job(job_id):
    """Simpan state tanpa objek process agar polling tetap hidup setelah restart Labs."""
    job = AGENT_JOBS.get(job_id)
    if not job:
        return
    safe = {k: v for k, v in job.items() if k != "proc"}
    AGENT_JOB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = AGENT_JOB_DIR / f".{job_id}.tmp"
    tmp.write_text(json.dumps(safe, ensure_ascii=False))
    tmp.replace(AGENT_JOB_DIR / f"{job_id}.json")


def _load_agent_job(job_id):
    try:
        return json.loads((AGENT_JOB_DIR / f"{job_id}.json").read_text())
    except (OSError, ValueError):
        return None


def _new_job_id():
    return "agent_" + os.urandom(6).hex()


def _agent_env(profile="default"):
    home = lab_profiles.profile_home(profile)
    return dict(os.environ,
                HERMES_ACCEPT_HOOKS="1", XDG_RUNTIME_DIR="/run/user/1000",
                HERMES_SAFE_MODE="0", TERM="dumb",
                HOME="/home/ubuntu", HERMES_HOME=str(home),
                USER="ubuntu", LOGNAME="ubuntu")


def _history_block(history):
    """Format riwayat percakapan sebagai konteks untuk prompt agent (kesinambungan sesi)."""
    if not history:
        return ""
    rows = []
    for m in (history or [])[-8:]:
        if not isinstance(m, dict):
            continue
        role = "User" if str(m.get("role")) == "user" else "LeoAI"
        content = str(m.get("content") or "").strip()
        if content:
            rows.append(f"{role}: {content[:1000]}")
    if not rows:
        return ""
    return ("\n\n[Konteks percakapan sebelumnya — pesan-pesan ini sudah dibahas. "
            "Gunakan untuk menjawab perintah baru di bawah; jangan mengulang jawaban lama.]\n"
            + "\n".join(rows))


def _resolve_agent_provider(provider, profile="default"):
    """Map dropdown provider label to a Hermes custom-provider name.

    - ``Name@@<uuid>`` = 9router account-pinned label from ``lab_chat._router_models()``.
      Strip the ``@@uuid`` suffix and match against registered config providers;
      if none is found, fall back to the 9router relay for that connection's
      provider (``kiro`` → ``kiroAI``) so the agent runs through 9router
      (which distributes to an active account).
    - Plain labels (Gatekey, B.AI, …) are resolved case-insensitively to the
      canonical name in config.yaml.
    - Returns the bare provider name (without ``custom:`` prefix).
    """
    if not provider:
        return ""
    provider = str(provider).strip().removeprefix("custom:")
    base = provider
    conn_id = ""
    if "@@" in provider:
        base, _, conn_id = provider.rpartition("@@")
    # 1) try canonical match by base name (case-insensitive)
    config = lab_profiles.profile_home(profile) / "config.yaml"
    try:
        cfg = yaml.safe_load(config.read_text()) or {}
    except Exception:
        cfg = {}
    for p in cfg.get("custom_providers", []) or []:
        if isinstance(p, dict) and str(p.get("name", "")).lower() == base.lower():
            return str(p.get("name"))
    # 2) 9router account-pinned — map to the relay for its provider family
    if conn_id:
        return _relay_for_9r_connection(conn_id, config)
    # 3) plain label — return as-is (caller prepends custom:)
    return base


def _relay_for_9r_connection(conn_id, config=CONFIG):
    """Cari relay provider (custom_providers di config) untuk koneksi 9router.

    Kalau koneksi punya nama yang sudah terdaftar di config, pakai itu.
    Kalau tidak, map berdasarkan family provider 9router: kiro → kiroAI.
    Mengembalikan nama provider tanpa prefix ``custom:`` (atau '' jika tak ada).
    """
    try:
        import sqlite3
        con = sqlite3.connect("/home/ubuntu/.9router/db/data.sqlite")
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT provider, name FROM providerConnections WHERE id=?",
            (conn_id,),
        ).fetchone()
        con.close()
    except Exception:
        row = None
    if row is None:
        return "kiroAI" if conn_id else ""
    try:
        cfg = yaml.safe_load(config.read_text()) or {}
    except Exception:
        cfg = {}
    cps = [p for p in (cfg.get("custom_providers") or []) if isinstance(p, dict)]
    # 1) relay dengan nama sama dengan akun 9router (mis. "TabiAI Akun 5")
    name = str(row["name"] or "").strip()
    if name:
        for p in cps:
            if str(p.get("name", "")).lower() == name.lower():
                return str(p.get("name"))
    # 2) family-based fallback (kiro → kiroAI)
    prov = str(row["provider"] or "").lower()
    if prov == "kiro" or prov.startswith("kiro"):
        return "kiroAI"
    return name or ""


def _run_agent_job(job_id, prompt, model, provider, history=None, session_id="", profile="default"):
    """Jalankan hermes -z di background thread; hasil disimpan ke AGENT_JOBS.
    Partial stdout ditulis ke data/agent-jobs/<job_id>.live.txt utk ditampilkan
    real-time sebagai "agent thinking" di UI."""
    artifact_note = ("\n\n[LABS AGENT] Kerjakan sampai selesai. Jika membuat file/artifact, "
                     "cantumkan setiap path absolut pada jawaban akhir dengan format MEDIA:/path/file.")
    hb = _history_block(history)
    full_prompt = (hb + "\n\n--- PERINTAH BARU ---\n" + prompt) if hb else prompt
    cmd = [HERMES_PY, "-m", "hermes_cli.main", "-z", full_prompt + artifact_note, "--cli", "--yolo"]
    if model:
        cmd += ["-m", model]
    if provider:
        provider = str(provider).strip()
        # Hermes custom provider names are case-sensitive. Resolve dropdown label
        # to canonical config name (for example `gatekey` -> `Gatekey`).
        provider = _resolve_agent_provider(provider, profile)
        if provider and provider not in {"openrouter", "nous", "anthropic", "openai"} and not provider.startswith("custom:"):
            provider = "custom:" + provider
        cmd += ["--provider", provider]
    live_path = AGENT_JOB_DIR / f"{job_id}.live.txt"
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env=_agent_env(profile))
        with _AGENT_LOCK:
            if job_id in AGENT_JOBS and AGENT_JOBS[job_id].get("cancel"):
                proc.kill()
                with _AGENT_LOCK:
                    AGENT_JOBS[job_id].update(status="cancelled", done=True)
                return
            AGENT_JOBS[job_id]["proc"] = proc
            AGENT_JOBS[job_id]["session_id"] = session_id
            AGENT_JOBS[job_id].update(phase="Agent aktif · menjalankan proses", updated_at=time.time())
            _persist_agent_job(job_id)
        # baca stdout baris demi baris -> simpan partial (live) + deteksi provider error
        tail = []
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            line_s = line.rstrip("\n")
            if line_s.strip():
                tail.append(line_s)
                if len(tail) > 200:
                    tail.pop(0)
            try:
                live_path.write_text("\n".join(tail[-120:]), encoding="utf-8", errors="replace")
            except Exception:
                pass
        proc.wait(timeout=30)
        out_s = "\n".join(tail).strip()
        err_s = ""
        rc = proc.returncode
        with _AGENT_LOCK:
            was_cancelled = bool(AGENT_JOBS.get(job_id, {}).get("cancel"))
        if was_cancelled:
            with _AGENT_LOCK:
                AGENT_JOBS[job_id].update(status="cancelled", done=True, ok=False)
            return
        # deteksi kegagalan provider di stdout ATAU stderr (402/403/balance)
        combined = (out_s + " " + err_s).lower()
        if rc == 0 and out_s and not _looks_like_provider_error(combined):
            reply = out_s
            for marker in ("FINAL ANSWER:", "Final answer:", "Assistant:"):
                if marker in reply:
                    reply = reply.rsplit(marker, 1)[-1].strip()
                    break
            result = {"status": "done", "done": True, "reply": reply, "ok": True, "agent": True,
                      "attachments": _collect_attachments(job_id, reply)}
        elif _looks_like_provider_error(combined):
            fallback_cmd = [HERMES_PY, "-m", "hermes_cli.main", "-z", full_prompt + artifact_note,
                            "--cli", "--yolo", "-m", "gatekey-unlimited-deepseek-v4-flash",
                            "--provider", "custom:Gatekey"]
            proc = subprocess.Popen(fallback_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, env=_agent_env(profile))
            with _AGENT_LOCK:
                AGENT_JOBS[job_id]["proc"] = proc
                AGENT_JOBS[job_id].update(phase="Provider utama gagal · Agent fallback aktif",
                                          provider="custom:Gatekey",
                                          model="gatekey-unlimited-deepseek-v4-flash",
                                          updated_at=time.time())
                _persist_agent_job(job_id)
            fb_tail = []
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                line_s = line.rstrip("\n")
                if line_s.strip():
                    fb_tail.append(line_s)
                    if len(fb_tail) > 200:
                        fb_tail.pop(0)
                try:
                    live_path.write_text("\n".join(fb_tail[-120:]), encoding="utf-8", errors="replace")
                except Exception:
                    pass
            proc.wait(timeout=30)
            fb_reply = "\n".join(fb_tail).strip()
            fb_err = ""
            fb_combined = (fb_reply + " " + fb_err).lower()
            if proc.returncode == 0 and fb_reply and not _looks_like_provider_error(fb_combined):
                result = {"status": "done", "done": True, "ok": True, "reply": fb_reply,
                          "agent": True, "fallback": "Gatekey Agent",
                          "attachments": _collect_attachments(job_id, fb_reply)}
            else:
                result = {"status": "error", "done": True, "ok": False,
                          "error": (fb_err or fb_reply or "Semua provider Agent gagal")[-500:]}
        else:
            # stderr dialihkan ke stdout (stderr=STDOUT), jadi pesan error asli
            # ada di out_s. Jangan buang — tampilkan supaya user tahu alasannya
            # (mis. "Unknown provider", 402/403, dll), bukan "exit 1" generik.
            err_s = (err_s or out_s)[-300:]
            result = {"status": "error", "done": True, "ok": False,
                      "error": err_s or f"Agent gagal (exit {rc})"}
        with _AGENT_LOCK:
            AGENT_JOBS[job_id].update(result)
            AGENT_JOBS[job_id].update(updated_at=time.time(), phase="Selesai" if result.get("ok") else "Gagal")
            _persist_agent_job(job_id)
    except Exception as e:
        if proc and proc.poll() is None:
            proc.kill()
        with _AGENT_LOCK:
            AGENT_JOBS[job_id].update(status="error", done=True, ok=False,
                                      error=str(e)[:300], phase="Gagal", updated_at=time.time())
            _persist_agent_job(job_id)
    finally:
        with _AGENT_LOCK:
            if job_id in AGENT_JOBS:
                AGENT_JOBS[job_id]["proc"] = None



def _looks_like_provider_error(text):
    return any(k in text for k in ("http 402", "http 403", "http 429",
                                   "http 500", "http 502", "http 503", "http 504",
                                   "api call failed", "failed after 3 retries",
                                   "insufficient balance", "insufficient_balance",
                                   "quota", "rate limit", "x-relay-target",
                                   "authentication_required"))


def _collect_attachments(job_id, reply):
    """Salin artifact yang disebut agent ke store; path lain tidak ikut terekspos."""
    found, out = set(), []
    for raw in re.findall(r"MEDIA:([^\s<>]+)", reply or ""):
        try:
            src = Path(raw.strip().strip("'\".,)")).expanduser().resolve()
            if not src.is_file() or src.stat().st_size > 200 * 1024 * 1024:
                continue
            if not any(str(src).startswith(root) for root in ("/home/ubuntu/", "/tmp/")):
                continue
            if str(src) in found:
                continue
            found.add(str(src))
            dst_dir = ATTACHMENT_DIR / job_id
            dst_dir.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", src.name)[:120] or "artifact"
            dst = dst_dir / name
            if dst.exists():
                dst = dst_dir / (src.stem[:80] + "_" + os.urandom(3).hex() + src.suffix)
            shutil.copy2(src, dst)
            out.append({"id": f"{job_id}/{dst.name}", "name": dst.name,
                        "size": dst.stat().st_size, "created_at": time.time()})
        except OSError:
            continue
    return out


def list_attachments():
    rows = []
    if not ATTACHMENT_DIR.exists():
        return rows
    for p in ATTACHMENT_DIR.glob("*/*"):
        if p.is_file():
            st = p.stat()
            rows.append({"id": f"{p.parent.name}/{p.name}", "job_id": p.parent.name,
                         "name": p.name, "size": st.st_size, "created_at": st.st_mtime})
    return sorted(rows, key=lambda x: x["created_at"], reverse=True)[:300]


def attachment_path(attachment_id):
    try:
        p = (ATTACHMENT_DIR / attachment_id).resolve()
        return p if p.is_file() and p.is_relative_to(ATTACHMENT_DIR.resolve()) else None
    except (OSError, ValueError):
        return None


def delete_attachments(ids):
    """Hapus attachment batch (id: 'job_id/name'). Hanya path di dalam ATTACHMENT_DIR."""
    removed = 0
    errors = []
    base = ATTACHMENT_DIR.resolve()
    for aid in ids or []:
        try:
            p = (ATTACHMENT_DIR / str(aid)).resolve()
            if not p.is_file() or not p.is_relative_to(base):
                errors.append(str(aid)); continue
            p.unlink()
            removed += 1
            # hapus folder job kalau sudah kosong
            parent = p.parent
            try:
                parent.rmdir()
            except OSError:
                pass
        except OSError as e:
            errors.append(str(aid))
    return {"removed": removed, "errors": errors}


def start_agent_job(prompt, model="", provider="", history=None, session_id="", profile="default"):
    """Mulai agent job async. Return job_id."""
    if not prompt or not prompt.strip():
        return None
    job_id = _new_job_id()
    with _AGENT_LOCK:
        AGENT_JOBS[job_id] = {"id": job_id, "status": "running", "done": False,
                              "cancel": False, "proc": None, "prompt": prompt[:100],
                              "model": model, "provider": provider,
                              "profile": lab_profiles.normalize_name(profile),
                              "session_id": session_id,
                              "phase": "Menyiapkan agent", "created_at": time.time(),
                              "updated_at": time.time()}
        _persist_agent_job(job_id)
    t = threading.Thread(target=_run_agent_job,
                         args=(job_id, prompt, model, provider, history, session_id, profile),
                         daemon=True)
    t.start()
    return job_id


def agent_job_status(job_id):
    with _AGENT_LOCK:
        j = AGENT_JOBS.get(job_id)
        if not j:
            j = _load_agent_job(job_id)
            if j and j.get("status") == "running":
                j.update(status="error", done=True, ok=False, phase="Terputus",
                         error="Proses Agent terputus karena Labs restart. Kirim ulang perintah.",
                         updated_at=time.time())
                AGENT_JOBS[job_id] = j
                _persist_agent_job(job_id)
        if not j:
            return {"status": "error", "done": True, "ok": False, "error": "Job tidak dikenal"}
        out = {k: v for k, v in j.items() if k != "proc"}
        out["elapsed"] = max(0, int(time.time() - float(out.get("created_at") or time.time())))
        # live output (agent thinking) — baca snapshot terakhir dari file
        live = AGENT_JOB_DIR / f"{job_id}.live.txt"
        try:
            if live.exists() and out.get("status") == "running":
                out["live_output"] = live.read_text(encoding="utf-8", errors="replace")[-1500:]
            else:
                out["live_output"] = ""
        except Exception:
            out["live_output"] = ""
        return out


def mark_agent_job_recorded(job_id):
    with _AGENT_LOCK:
        j = AGENT_JOBS.get(job_id) or _load_agent_job(job_id)
        if not j:
            return False
        j["recorded"] = True
        AGENT_JOBS[job_id] = j
        _persist_agent_job(job_id)
        return True


def cancel_agent_job(job_id):
    with _AGENT_LOCK:
        j = AGENT_JOBS.get(job_id)
        if not j:
            return False
        j["cancel"] = True
        proc = j.get("proc")
    if proc and proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass
    return True


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
