"""Labs — password management (labs, vps root/ubuntu), web status, alerts."""
import hashlib
import hmac
import io
import os
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

NOTIFICATION_DB = Path(__file__).resolve().parent / "data" / "notifications.db"


# ---------------- VPS access management ----------------
SSH_OVERRIDE = Path("/etc/ssh/sshd_config.d/90-labs-access.conf")


def _authorize(admin_password: str) -> dict | None:
    if not hmac.compare_digest(admin_password, os.environ.get("LABS_PASSWORD", "")):
        return {"ok": False, "error": "password admin Labs salah"}
    return None


def ssh_access_status() -> dict:
    """Return effective SSH policy without exposing key material."""
    try:
        r = subprocess.run(["sshd", "-T"], capture_output=True, text=True, timeout=10, check=True)
        values = {}
        for line in r.stdout.splitlines():
            key, _, value = line.partition(" ")
            if key in {"passwordauthentication", "pubkeyauthentication", "permitrootlogin"}:
                values[key] = value
        users = []
        for user, home in (("root", Path("/root")), ("ubuntu", Path("/home/ubuntu"))):
            auth = home / ".ssh" / "authorized_keys"
            count = 0
            try:
                count = sum(1 for line in auth.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#"))
            except (OSError, UnicodeError):
                pass
            users.append({"user": user, "keys": count})
        key_only = values.get("passwordauthentication") == "no"
        return {"ok": True, "mode": "key" if key_only else "password", "effective": values, "users": users}
    except Exception as e:
        return {"ok": False, "error": f"gagal membaca konfigurasi SSH: {e}"}


def generate_ssh_key(user: str, admin_password: str) -> dict:
    """Generate Ed25519 key, install public half, return private half once."""
    if error := _authorize(admin_password):
        return error
    homes = {"root": Path("/root"), "ubuntu": Path("/home/ubuntu")}
    if user not in homes:
        return {"ok": False, "error": "hanya root/ubuntu"}
    import pwd
    import tempfile
    stamp = time.strftime("%Y%m%d-%H%M%S")
    comment = f"labs-{user}-{stamp}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "id_ed25519"
            r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)],
                               capture_output=True, text=True, timeout=20)
            if r.returncode:
                return {"ok": False, "error": (r.stderr or "ssh-keygen gagal")[:200]}
            private = key.read_text()
            public = key.with_suffix(".pub").read_text().strip()
            fingerprint = subprocess.run(["ssh-keygen", "-lf", str(key.with_suffix('.pub'))],
                                         capture_output=True, text=True, timeout=10).stdout.strip()
        account = pwd.getpwnam(user)
        ssh_dir = homes[user] / ".ssh"
        auth = ssh_dir / "authorized_keys"
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(ssh_dir, 0o700)
        with auth.open("a") as f:
            f.write(public + "\n")
        os.chmod(auth, 0o600)
        os.chown(ssh_dir, account.pw_uid, account.pw_gid)
        os.chown(auth, account.pw_uid, account.pw_gid)
        return {"ok": True, "user": user, "private_key": private,
                "filename": f"{user}-labs-{stamp}", "fingerprint": fingerprint,
                "note": "public key terpasang; private key hanya dikirim sekali"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def set_ssh_mode(mode: str, admin_password: str) -> dict:
    """Atomically switch global SSH authentication policy with validation and rollback."""
    if error := _authorize(admin_password):
        return error
    if mode not in {"key", "password"}:
        return {"ok": False, "error": "mode harus key/password"}
    status = ssh_access_status()
    if mode == "key" and (not status.get("ok") or not any(x["keys"] for x in status["users"])):
        return {"ok": False, "error": "buat minimal satu SSH key sebelum mengaktifkan Key only"}
    content = ("# Managed by VPS Sentinel\nPubkeyAuthentication yes\n" +
               ("PasswordAuthentication no\nPermitRootLogin prohibit-password\n" if mode == "key" else
                "PasswordAuthentication yes\nPermitRootLogin prohibit-password\n"))
    backup = SSH_OVERRIDE.read_text() if SSH_OVERRIDE.exists() else None
    tmp = SSH_OVERRIDE.with_suffix(".tmp")
    try:
        tmp.write_text(content)
        os.replace(tmp, SSH_OVERRIDE)
        check = subprocess.run(["sshd", "-t"], capture_output=True, text=True, timeout=10)
        if check.returncode:
            raise RuntimeError(check.stderr.strip() or "sshd -t gagal")
        subprocess.run(["systemctl", "reload", "ssh"], capture_output=True, text=True, timeout=15, check=True)
        return {"ok": True, "mode": mode, "note": "SSH Key only aktif" if mode == "key" else "Password + SSH key aktif; root tetap key-only"}
    except Exception as e:
        if backup is None:
            SSH_OVERRIDE.unlink(missing_ok=True)
        else:
            SSH_OVERRIDE.write_text(backup)
        return {"ok": False, "error": f"konfigurasi dibatalkan: {e}"}


def change_labs_password(username: str, old_pw: str, new_pw: str, new_username: str = "") -> dict:
    """Change Labs credentials in private EnvironmentFile."""
    import hmac
    cur_user = os.environ.get("LABS_USER", "")
    cur_pw = os.environ.get("LABS_PASSWORD", "")
    if not (hmac.compare_digest(username, cur_user) and hmac.compare_digest(old_pw, cur_pw)):
        return {"ok": False, "error": "username/password lama salah"}
    new_username = new_username.strip() or username
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", new_username):
        return {"ok": False, "error": "username baru 3-32 karakter: huruf, angka, titik, _ atau -"}
    if len(new_pw) < 10:
        return {"ok": False, "error": "password baru minimal 10 karakter"}
    if not re.search(r'[A-Za-z]', new_pw) or not re.search(r'\d', new_pw):
        return {"ok": False, "error": "password wajib punya huruf dan angka"}
    if re.search(r'[\s%"\\]', new_pw):
        return {"ok": False, "error": "password tidak boleh berisi spasi, %, tanda kutip, atau backslash"}
    env_file = Path(__file__).resolve().parent / 'data' / 'labs.env'
    try:
        txt = env_file.read_text()
    except Exception as e:
        return {"ok": False, "error": f"gagal baca credential store: {e}"}
    txt, cu = re.subn(r'(?m)^LABS_USER=.*$', 'LABS_USER=' + new_username, txt)
    txt, cp = re.subn(r'(?m)^LABS_PASSWORD=.*$', 'LABS_PASSWORD=' + new_pw, txt)
    if cu != 1 or cp != 1:
        return {"ok": False, "error": "credential store tidak lengkap"}
    try:
        tmp = env_file.with_suffix('.tmp'); tmp.write_text(txt); os.chmod(tmp, 0o600); os.replace(tmp, env_file)
    except Exception as e:
        return {"ok": False, "error": f"gagal simpan: {e}"}
    return {"ok": True, "username": new_username, "restart_required": True}


def change_vps_password(user: str, new_pw: str, admin_password: str = "") -> dict:
    """Change Linux user password (root or ubuntu)."""
    if error := _authorize(admin_password):
        return error
    if user not in ("root", "ubuntu"):
        return {"ok": False, "error": "hanya root/ubuntu"}
    if len(new_pw) < 8:
        return {"ok": False, "error": "password minimal 8 karakter"}
    try:
        r = subprocess.run(["chpasswd"], input=f"{user}:{new_pw}\n", text=True,
                           capture_output=True, timeout=15)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout or "chpasswd gagal")[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "user": user, "note": "password diubah via chpasswd"}


# ---------------- Web status ----------------
WEB_DOMAINS = [d.strip() for d in os.environ.get("LABS_WEB_DOMAINS", "").split(",") if d.strip()]


def web_status(timeout: int = 12) -> dict:
    """Check each web domain: reachable?, http code, response ms, ssl ok."""
    out = []
    for url in WEB_DOMAINS:
        start = time.time()
        entry = {"url": url, "ok": False, "code": 0, "ms": 0, "error": ""}
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "lab-monitor"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                entry["code"] = r.status
                entry["ok"] = 200 <= r.status < 400
        except urllib.error.HTTPError as e:
            entry["code"] = e.code
            entry["ok"] = 200 <= e.code < 400
        except urllib.error.URLError as e:
            entry["error"] = str(e.reason)[:80]
        except Exception as e:
            entry["error"] = str(e)[:80]
        entry["ms"] = int((time.time() - start) * 1000)
        out.append(entry)
    total = len(out)
    up = sum(1 for x in out if x["ok"])
    return {"sites": out, "total": total, "up": up,
            "status": "OK" if up == total else f"{up}/{total} online"}


# ---------------- Urgent alerts ----------------
def urgent_alerts() -> dict:
    """Scan for urgent VPS conditions and return human alerts."""
    alerts = []
    # disk
    try:
        d = subprocess.run(["df", "-P", "/"], capture_output=True, text=True, timeout=8)
        pct = int(re.search(r"(\d+)%", d.stdout.splitlines()[1]).group(1))
        if pct >= 85:
            alerts.append({"level": "danger", "icon": "💾", "title": f"Disk / {pct}%",
                           "detail": "Kapasitas disk hampir penuh — bersihkan log/backup."})
        elif pct >= 70:
            alerts.append({"level": "warn", "icon": "💾", "title": f"Disk / {pct}%",
                           "detail": "Disk mulai penuh."})
    except Exception:
        pass
    # memory
    try:
        import psutil
        m = psutil.virtual_memory()
        if m.percent >= 90:
            alerts.append({"level": "danger", "icon": "🧠", "title": f"RAM {m.percent}%",
                           "detail": "RAM kritis — pertimbangkan stop webui/9router."})
        elif m.percent >= 80:
            alerts.append({"level": "warn", "icon": "🧠", "title": f"RAM {m.percent}%",
                           "detail": "RAM tinggi."})
    except Exception:
        pass
    # failed ssh logins
    try:
        r = subprocess.run(["journalctl", "-u", "ssh", "--since", "1 hour ago", "--no-pager",
                            "-o", "cat"], capture_output=True, text=True, timeout=10)
        fails = sum(1 for ln in r.stdout.splitlines() if "Failed password" in ln)
        if fails >= 5:
            alerts.append({"level": "danger", "icon": "🔐", "title": f"{fails} gagal SSH/jam",
                           "detail": "Ada percobaan login SSH gagal berulang."})
    except Exception:
        pass
    # fail2ban
    try:
        r = subprocess.run(["systemctl", "is-active", "fail2ban"], capture_output=True, text=True, timeout=6)
        if r.stdout.strip() != "active":
            alerts.append({"level": "warn", "icon": "🛡️", "title": "fail2ban off",
                           "detail": "Firewall ban tidak aktif!"})
    except Exception:
        pass
    # services down (system services)
    try:
        from app import SERVICES, service_state
        for svc in SERVICES:
            st = service_state(svc)
            if st == "inactive":
                alerts.append({"level": "warn", "icon": "🔄", "title": f"{svc} mati",
                               "detail": f"Service {svc} tidak berjalan."})
    except Exception:
        pass
    for alert in alerts:
        stable_title = re.sub(r"\d+(?:\.\d+)?", "#", alert["title"])
        raw = "|".join((alert["level"], stable_title, alert["detail"]))
        alert["key"] = hashlib.sha256(raw.encode()).hexdigest()[:20]
    return {"alerts": alerts, "count": len(alerts),
            "urgent": sum(1 for a in alerts if a["level"] == "danger")}


def _notification_db(db_path=NOTIFICATION_DB):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS notifications (
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
    )""")
    return db


def notification_log(alerts=None, db_path=NOTIFICATION_DB) -> dict:
    """Sync current alerts, then return persistent notification history."""
    with _notification_db(db_path) as db:
        if alerts is not None:
            now = int(time.time())
            active_keys = []
            for alert in alerts:
                key = alert.get("key") or hashlib.sha256(
                    "|".join((alert["level"], alert["title"], alert["detail"])).encode()
                ).hexdigest()[:20]
                active_keys.append(key)
                db.execute("""INSERT INTO notifications
                    (alert_key, level, icon, title, detail, first_seen, last_seen, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(alert_key) DO UPDATE SET
                    level=excluded.level, icon=excluded.icon, title=excluded.title,
                    detail=excluded.detail, last_seen=excluded.last_seen, active=1""",
                    (key, alert["level"], alert["icon"], alert["title"], alert["detail"], now, now))
            if active_keys:
                marks = ",".join("?" for _ in active_keys)
                db.execute(f"UPDATE notifications SET active=0 WHERE alert_key NOT IN ({marks})", active_keys)
            else:
                db.execute("UPDATE notifications SET active=0")
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM notifications ORDER BY active DESC, last_seen DESC, id DESC"
        )]
    for row in rows:
        row["active"] = bool(row["active"])
        row["is_read"] = bool(row["is_read"])
    return {"ok": True, "notifications": rows, "total": len(rows),
            "unread": sum(not row["is_read"] for row in rows),
            "active": sum(row["active"] for row in rows)}


def update_notifications(action: str, db_path=NOTIFICATION_DB, notification_id=None) -> dict:
    """Read/delete one notification or all notifications."""
    with _notification_db(db_path) as db:
        if action == "read_all":
            db.execute("UPDATE notifications SET is_read=1")
        elif action == "delete_all":
            db.execute("DELETE FROM notifications")
        elif action in {"read", "delete"} and notification_id is not None:
            sql = "UPDATE notifications SET is_read=1 WHERE id=?" if action == "read" else "DELETE FROM notifications WHERE id=?"
            db.execute(sql, (int(notification_id),))
        else:
            return {"ok": False, "error": "aksi notifikasi tidak valid"}
    return notification_log(None, db_path)
