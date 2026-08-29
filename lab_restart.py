"""Labs restart tracking: record every boot so restarts are visible in logs & chat."""
import json
import os
import platform
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESTART_FILE = ROOT / "data" / "restart.json"
NOTIFICATION_DB = ROOT / "data" / "notifications.db"


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        return out.stdout.strip() or "?"
    except Exception:
        return "?"


def current() -> dict | None:
    """Return the latest restart record, or None if none recorded yet."""
    try:
        return json.loads(RESTART_FILE.read_text())
    except (OSError, ValueError):
        return None


def record_restart() -> dict:
    """Call once at startup.  Persists restart info + adds a notification."""
    prev = current()
    now = int(time.time())
    prev_boot = prev.get("boot_at") if prev else None
    interval_s = (now - prev_boot) if (prev_boot and now >= prev_boot) else None

    boot_id = f"{now}-{os.getpid()}"
    rec = {
        "boot_id": boot_id,
        "boot_at": now,
        "host": platform.node(),
        "pid": os.getpid(),
        "commit": _git_head(),
        "prev_boot_at": prev_boot,
        "interval_s": interval_s,
        "count": (prev.get("count", 0) + 1) if prev else 1,
    }
    RESTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESTART_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2))
    os.replace(tmp, RESTART_FILE)

    _notify(rec, prev)
    return rec


def _notify(rec: dict, prev: dict | None) -> None:
    """Insert/update a restart notification row in notifications.db."""
    icon = "🔄"
    count = rec.get("count", 1)
    if count == 1:
        title = "Labs mulai ulang"
    else:
        title = f"Labs mulai ulang ({count}x)"

    WIB = timezone(timedelta(hours=7))
    now = datetime.fromtimestamp(rec["boot_at"], tz=WIB).strftime("%Y-%m-%d %H:%M:%S WIB")
    host = rec.get("host", "?")
    commit = rec.get("commit", "")
    interval = rec.get("interval_s")
    parts = [f"Labs (vps-audit) mulai ulang pada {now} dari host {host}"]
    if commit:
        parts.append(f"commit {commit}")
    if interval is not None:
        parts.append(f"interval sejak boot sebelumnya {interval} detik")
    detail = " · ".join(parts)

    # Same alert_key every time → upsert, so only one row stays in the DB.
    alert_key = "labs-restart"
    try:
        path = Path(NOTIFICATION_DB)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute(
            """CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_key TEXT NOT NULL UNIQUE,
                level TEXT NOT NULL,
                icon TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                is_read INTEGER NOT NULL DEFAULT 0
            )"""
        )
        db.execute(
            """INSERT INTO notifications
               (alert_key, level, icon, title, detail, first_seen, last_seen, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(alert_key) DO UPDATE SET
               level=excluded.level, icon=excluded.icon, title=excluded.title,
               detail=excluded.detail, last_seen=excluded.last_seen, active=1""",
            (alert_key, "warn", icon, title, detail, rec["boot_at"], rec["boot_at"]),
        )
        db.commit()
        db.close()
    except Exception:
        pass  # best-effort; restart tracking should not crash the app