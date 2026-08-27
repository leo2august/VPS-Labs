"""Lab — Jobs & Cron dashboard.
Membaca job scheduler Hermes (jobs.json + executions.db), crontab sistem, dan
systemd timers, lalu menyajikan kartu visual per job dengan status, jadwal,
riwayat eksekusi, dan kontrol pause/resume/run.
"""
import json, os, re, sqlite3, subprocess, time
from pathlib import Path
from datetime import datetime

HERMES_CRON = Path(os.environ.get("LABS_HERMES_DIR", "/home/USER/.hermes")) / "cron"
JOBS_JSON = HERMES_CRON / "jobs.json"
EXEC_DB = HERMES_CRON / "executions.db"
PY = "/home/USER/.hermes/hermes-agent/venv/bin/python"
HERMES_CLI = [PY, "-m", "hermes_cli.main"]
_SYSTEM_CACHE = {"at": 0.0, "crontab": [], "timers": []}


def _now():
    return datetime.now().isoformat()


def _short(s, n=40):
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _parse_schedule(sched):
    """Normalisasi jadwal job -> label manusiawi + ekspresi cron."""
    if not sched:
        return {"kind": "?", "expr": "", "label": "—"}
    kind = sched.get("kind", "cron")
    if kind == "interval":
        secs = int(sched.get("seconds", 0))
        if secs % 3600 == 0:
            label = f"setiap {secs//3600} jam"
        elif secs % 60 == 0:
            label = f"setiap {secs//60} menit"
        else:
            label = f"setiap {secs} detik"
        return {"kind": kind, "expr": sched.get("display", f"every {secs}s"), "label": label}
    expr = sched.get("expr") or sched.get("display") or ""
    label = expr or "—"
    return {"kind": kind, "expr": expr, "label": label}


def _fmt_dt(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%d %b %H:%M")
    except Exception:
        return (s or "")[:16]


def _cron_to_label(expr):
    """Terjemahan ekspresi cron sederhana ke label baca-manusia (best-effort)."""
    e = (expr or "").strip()
    if not e:
        return ""
    try:
        parts = e.split()
        if len(parts) != 5:
            return ""
        minute, hour, dom, mon, dow = parts
        if minute.startswith("*/"):
            m = minute[2:]
            if hour == "*" and dom == "*" and mon == "*" and dow == "*":
                return f"setiap {m} menit"
            if hour.startswith("*/"):
                return f"setiap {m} menit, tiap {hour[2:]} jam"
        if minute == "0" and hour.startswith("*/"):
            h = hour[2:]
            return f"setiap {h} jam"
        if minute == "0" and hour.isdigit():
            return f"jam {hour}:00"
        if "," in minute and hour.startswith("*/"):
            return f"menit {minute}, tiap {hour[2:]} jam"
        if minute == "30" and hour.isdigit():
            return f"jam {hour}:30"
        return ""
    except Exception:
        return ""


def list_jobs():
    """Semua job Hermes dari jobs.json + ringkasan eksekusi dari executions.db."""
    jobs = []
    try:
        data = json.loads(JOBS_JSON.read_text())
        raw_jobs = data.get("jobs", [])
    except Exception:
        raw_jobs = []
    # peta riwayat eksekusi per job (status terbaru dulu)
    exec_map = {}
    try:
        con = sqlite3.connect(f"file:{EXEC_DB}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT job_id, status, started_at, finished_at, error "
            "FROM executions ORDER BY id DESC LIMIT 300"
        ).fetchall()
        for r in rows:
            jid = r["job_id"]
            if jid not in exec_map:
                exec_map[jid] = []
            if len(exec_map[jid]) < 10:
                exec_map[jid].append(
                    {"status": r["status"], "started_at": r["started_at"],
                     "finished_at": r["finished_at"],
                     "error": _short(r["error"], 90)}
                )
        con.close()
    except Exception:
        pass
    for j in raw_jobs:
        jid = j.get("id", "")
        sched = _parse_schedule(j.get("schedule"))
        jobs.append({
            "id": jid,
            "name": j.get("name") or jid,
            "kind": "agent" if not j.get("no_agent") else "script",
            "script": j.get("script"),
            "prompt": _short(j.get("prompt", ""), 70),
            "schedule": sched,
            "schedule_expr": j.get("schedule_display") or "",
            "schedule_label": _cron_to_label(j.get("schedule_display") or ""),
            "enabled": bool(j.get("enabled")),
            "state": j.get("state") or "scheduled",
            "paused": bool(j.get("state") == "paused" or not j.get("enabled")),
            "last_status": j.get("last_status"),
            "last_error": _short(j.get("last_error") or j.get("last_delivery_error"), 90),
            "last_run_at": _fmt_dt(j.get("last_run_at")),
            "next_run_at": _fmt_dt(j.get("next_run_at")),
            "created_at": _fmt_dt(j.get("created_at")),
            "deliver": j.get("deliver"),
            "origin_chat": (j.get("origin") or {}).get("chat_name") or (j.get("origin") or {}).get("platform"),
            "completed": (j.get("repeat") or {}).get("completed", 0),
            "history": exec_map.get(jid, []),
            "model": j.get("model"),
            "enabled_toolsets": j.get("enabled_toolsets"),
            "workdir": j.get("workdir"),
            "context_from": j.get("context_from"),
        })
    # urut: aktif+terbaru dulu
    order = {"scheduled": 0, "paused": 1}
    jobs.sort(key=lambda x: (order.get(x["state"], 2), x["name"].lower()))
    return jobs


def scheduler_status():
    """Status scheduler Hermes (running? heartbeat segar?)."""
    hb = HERMES_CRON / "ticker_heartbeat"
    last = None
    fresh = False
    try:
        if hb.exists():
            ts = float(hb.read_text().strip())
            last = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            fresh = (datetime.now().timestamp() - ts) < 300
    except Exception:
        pass
    return {"running": fresh, "last_heartbeat": last, "fresh": fresh}


def list_crontab():
    """Baris crontab user (aktif) — parse 5-field cron + command."""
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("@"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            minute, hour, dom, mon, dow = parts[:5]
            cmd = parts[5]
            if minute.isdigit() and hour.startswith("*/"):
                label = f"tiap {hour[2:]} jam"
            elif minute.startswith("*/") and hour == "*":
                label = f"setiap {minute[2:]} menit"
            else:
                label = f"{minute} {hour} {dom} {mon} {dow}"
        except Exception:
            minute = hour = dom = mon = dow = "*"
            label = " ".join(parts[:5]) if len(parts) >= 5 else ""
            cmd = " ".join(parts[5:]) if len(parts) >= 6 else ""
        rows.append({"minute": minute, "hour": hour, "dom": dom, "mon": mon,
                     "dow": dow, "command": cmd[:100], "label": label})
    return rows


def list_timers():
    """Systemd timers (user-relevant, bukan hanya distro)."""
    try:
        out = subprocess.run(["systemctl", "list-timers", "--no-pager", "--no-legend"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            next_ = " ".join(parts[0:3])
            left = " ".join(parts[3:5])
            last = " ".join(parts[5:8])
            passed = " ".join(parts[8:10])
            unit = parts[-2] if len(parts) >= 11 else ""
            activate = parts[-1] if parts else ""
            if not unit.endswith(".timer"):
                continue
            rows.append({"next": next_, "left": left, "last": last,
                         "passed": passed, "unit": unit, "activates": activate})
        except Exception:
            continue
    return rows


def system_sources(max_age=60):
    """Cache sumber sistem mahal; daftar timer tidak perlu dipanggil tiap klik."""
    now = time.monotonic()
    if now - _SYSTEM_CACHE["at"] > max_age:
        _SYSTEM_CACHE.update(at=now, crontab=list_crontab(), timers=list_timers())
    return _SYSTEM_CACHE["crontab"], _SYSTEM_CACHE["timers"]


def _run_cli(args, timeout=90):
    """Jalankan perintah hermes cron CLI."""
    try:
        env = dict(os.environ, HERMES_ACCEPT_HOOKS="1", XDG_RUNTIME_DIR="/run/user/1000")
        r = subprocess.run(HERMES_CLI + args, capture_output=True, text=True, timeout=timeout, env=env)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        return {"ok": r.returncode == 0, "code": r.returncode, "stdout": out[-400:], "stderr": err[-400:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "stdout": "", "stderr": "timeout"}
    except Exception as exc:
        return {"ok": False, "code": -1, "stdout": "", "stderr": str(exc)}


def action(job_id, act):
    """pause | resume | run untuk satu job via hermes cron CLI."""
    cmd_map = {"pause": "pause", "resume": "resume", "run": "run", "remove": "remove"}
    if act not in cmd_map:
        return {"ok": False, "error": f"Aksi '{act}' tidak dikenal"}
    r = _run_cli(["cron", cmd_map[act], job_id])
    if not r["ok"]:
        return {"ok": False, "error": r["stderr"] or r["stdout"] or "gagal"}
    return {"ok": True, "action": act, "detail": r["stdout"][:200]}
