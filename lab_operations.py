"""Sessions, Labs chat persistence, live model activity, and Labs UI preferences."""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

STATE_DB = Path(os.environ.get("LABS_HERMES_DIR", "/home/USER/.hermes")) / "state.db"
LAB_SETTINGS = Path(__file__).resolve().parent / "data" / "lab-settings.json"
LAB_OPTIONS = {
    "theme": {"label": "Tema Labs", "description": "Warna seluruh dashboard Labs.", "choices": [["system", "Ikuti perangkat"], ["light", "Terang lembut"], ["dark", "Gelap nyaman"]], "default": "system"},
    "language": {"label": "Bahasa Labs", "description": "Bahasa label dan bantuan antarmuka Labs.", "choices": [["id", "Indonesia"], ["en", "English"]], "default": "id"},
    "density": {"label": "Kepadatan tampilan", "description": "Jarak kartu dan isi dashboard.", "choices": [["comfortable", "Nyaman"], ["compact", "Ringkas"]], "default": "comfortable"},
    "motion": {"label": "Animasi", "description": "Gerakan halus pada kartu dan panel.", "choices": [["full", "Normal"], ["reduced", "Dikurangi"]], "default": "full"},
    "session_order": {"label": "Urutan sesi", "description": "Urutan awal arsip percakapan.", "choices": [["recent", "Terbaru dulu"], ["oldest", "Terlama dulu"]], "default": "recent"},
}


def _db():
    con = sqlite3.connect(STATE_DB, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _source_label(source):
    return {"telegram": "Telegram", "whatsapp": "WhatsApp", "web": "Labs Web", "labs": "Labs Web", "cron": "Scheduler", "subagent": "Subagent", "cli": "Terminal"}.get(source or "", (source or "Lainnya").title())


def list_sessions(limit=100):
    limit = max(1, min(int(limit), 200))
    with _db() as con:
        rows = con.execute("""SELECT id,source,chat_type,display_name,title,model,started_at,ended_at,message_count,tool_call_count,
            COALESCE((SELECT MAX(timestamp) FROM messages m WHERE m.session_id=sessions.id),ended_at,started_at) last_active
            FROM sessions ORDER BY last_active DESC LIMIT ?""", (limit,)).fetchall()
    items = []
    now = time.time()
    for r in rows:
        d = dict(r); source = d.get("source") or "other"
        last = float(d.get("last_active") or 0)
        d.update(gateway=source, gateway_label=_source_label(source), open=not bool(d.get("ended_at")),
                 online=bool(last and now-last < 180),
                 title=d.get("title") or d.get("display_name") or f"{_source_label(source)} session")
        items.append(d)
    groups = []
    for key in dict.fromkeys(x["gateway"] for x in items):
        chunk = [x for x in items if x["gateway"] == key]
        groups.append({"id": key, "label": chunk[0]["gateway_label"], "count": len(chunk), "sessions": chunk})
    return {"sessions": items, "groups": groups, "last_active": items[0] if items else None,
            "online": [x for x in items if x["online"]], "updated_at": now}


def get_session(sid):
    with _db() as con:
        row = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            return {"error": "session tidak ditemukan"}
        msgs = con.execute("""SELECT id,role,content,timestamp,tool_name,token_count,finish_reason,reasoning FROM
            (SELECT id,role,content,timestamp,tool_name,token_count,finish_reason,reasoning FROM messages
             WHERE session_id=? ORDER BY timestamp DESC,id DESC LIMIT 500) ORDER BY timestamp,id""", (sid,)).fetchall()
    data = dict(row); source = data.get("source") or "other"
    last = (dict(msgs[-1]).get("timestamp") if msgs else data.get("ended_at") or data.get("started_at"))
    return {"id": sid, "title": data.get("title") or data.get("display_name") or "Session", "gateway": source,
            "gateway_label": _source_label(source), "model": data.get("model") or "—", "started_at": data.get("started_at"),
            "last_active": last, "online": bool(last and time.time()-float(last) < 180),
            "open": not bool(data.get("ended_at")), "message_count": data.get("message_count") or len(msgs),
            "shown_messages": len(msgs), "truncated": (data.get("message_count") or len(msgs)) > len(msgs),
            "tool_call_count": data.get("tool_call_count") or 0,
            "input_tokens": data.get("input_tokens") or 0, "output_tokens": data.get("output_tokens") or 0,
            "messages": [dict(x) for x in msgs]}


def record_lab_exchange(sid, user_text, reply, model):
    now = time.time(); sid = sid if sid and sid.startswith("labs_") else "labs_" + uuid.uuid4().hex[:16]
    title = (user_text or "Percakapan Laboratorium")[:60]
    with _db() as con:
        exists = con.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone()
        if not exists:
            # UNIQUE constraint on title — pastikan tak bentrok
            dup = con.execute("SELECT 1 FROM sessions WHERE title=?", (title,)).fetchone()
            if dup:
                title = f"{title[:50]} {uuid.uuid4().hex[:6]}"
            try:
                con.execute("""INSERT INTO sessions(id,source,user_id,session_key,chat_id,chat_type,display_name,model,started_at,message_count,tool_call_count,title,profile_name)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (sid,"labs","labs-user",sid,"labs","private","Labs Web",model,now,0,0,title,"default"))
            except sqlite3.IntegrityError:
                # title masih bentrok (race) — fallback ke id sebagai title
                title = f"{title[:40]} {sid[-8:]}"
                con.execute("""INSERT OR IGNORE INTO sessions(id,source,user_id,session_key,chat_id,chat_type,display_name,model,started_at,message_count,tool_call_count,title,profile_name)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (sid,"labs","labs-user",sid,"labs","private","Labs Web",model,now,0,0,title,"default"))
        con.executemany("INSERT INTO messages(session_id,role,content,timestamp) VALUES(?,?,?,?)",
                        [(sid,"user",user_text,now),(sid,"assistant",reply,now+.001)])
        con.execute("UPDATE sessions SET message_count=message_count+2,model=? WHERE id=?", (model,sid))
    return sid


def activity(limit=80):
    limit = max(10, min(int(limit), 200)); cutoff = time.time() - 86400
    with _db() as con:
        rows = con.execute("""SELECT m.id,m.session_id,m.role,m.content,m.tool_name,m.timestamp,s.source,s.title,s.display_name
            FROM messages m JOIN sessions s ON s.id=m.session_id
            WHERE m.timestamp>? AND (m.tool_name IS NOT NULL OR m.role IN ('tool','assistant'))
            ORDER BY m.timestamp DESC LIMIT ?""", (cutoff, limit)).fetchall()
        active = con.execute("SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL AND started_at>?", (cutoff,)).fetchone()[0]
    events=[]
    for r in rows:
        d=dict(r); content=(d.get("content") or "").strip().replace("\x00","")
        if not content and not d.get("tool_name"): continue
        # Keep Process Logs useful without leaking raw payloads, prompts, or secrets.
        if d.get("tool_name"):
            summary = f"Menjalankan {d['tool_name']}"
            phase = "tool"
        else:
            clean = " ".join(content.split())
            clean = clean.split("```", 1)[0].strip()
            summary = clean[:180].rstrip(" ,;:-") or "Memproses permintaan"
            phase = "response"
        d.pop("content", None)
        d["summary"] = summary
        d["phase"] = phase
        d["status"] = "running" if d["timestamp"] > time.time() - 90 else "completed"
        d["gateway_label"] = _source_label(d.get("source")); events.append(d)
    return {"events": events, "active_sessions": active, "updated_at": time.time()}


def get_lab_settings():
    values = {k:v["default"] for k,v in LAB_OPTIONS.items()}
    try: values.update(json.loads(LAB_SETTINGS.read_text()))
    except (OSError, ValueError): pass
    return {"ok": True, "values": values, "options": LAB_OPTIONS}


def update_lab_setting(key, value):
    if key not in LAB_OPTIONS or value not in {x[0] for x in LAB_OPTIONS[key]["choices"]}:
        return {"ok": False, "error": "pilihan tidak valid"}
    current = get_lab_settings()["values"]; current[key] = value
    LAB_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = LAB_SETTINGS.with_suffix(".tmp"); tmp.write_text(json.dumps(current, indent=2)); os.replace(tmp, LAB_SETTINGS)
    return {"ok": True, "key": key, "value": value}


if __name__ == "__main__":
    assert "groups" in list_sessions(2)
    assert get_lab_settings()["values"]["theme"] in {"system", "light", "dark"}
    print("lab_operations self-check OK")
