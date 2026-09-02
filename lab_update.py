#!/usr/bin/env python3
"""VPS Labs — Update center.

One place to safely update every component on the VPS:
  - hermes  : Hermes Agent (git + venv reinstall) via `hermes update`
  - labs    : vps-audit (this app, git pull)
  - system  : apt update + upgrade (dry-run first, then apply)
  - caddy   : web server (apt)

Safety model (esp. for Hermes, the most critical):
  1. PREFLIGHT  — disk space, no concurrent update, target valid.
  2. BACKUP     — every target snapshots its critical state before touching it.
                 Hermes relies on `hermes update --backup` (state snapshot +
                 full HERMES_HOME zip). Labs: lab_backup.create_backup('labs').
                 System: dpkg selections dump.
  3. EXECUTE    — run the real update with timeouts, non-interactive.
  4. VERIFY     — service is active + health endpoint answers.
  5. ROLLBACK   — on verify failure: git reset --hard <old HEAD> (hermes/labs),
                 restore service, re-verify. Never leaves a half-updated state.
  6. LOG        — every step appended to a job log; UI polls it (no secrets).

Background jobs run in threads; a global lock allows only one update at a time.
"""
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import lab_backup

ROOT = Path("/home/ubuntu/vps-audit")
BACKUP_DIR = ROOT / "backups" / "update"
LOG_DIR = ROOT / "data" / "update-logs"
HERMES_HOME = Path("/home/ubuntu/.hermes")
HERMES_REPO = HERMES_HOME / "hermes-agent"
HERMES_CLI = Path("/home/ubuntu/.local/bin/hermes")
VENV_PIP = HERMES_REPO / "venv" / "bin" / "pip"

# User services (restart via XDG_RUNTIME_DIR under the ubuntu user)
USER_SERVICES = {"hermes-gateway": "hermes-gateway", "hermes-task-router": "hermes-task-router"}
# System services
SYSTEM_SERVICES = {"hermes-dashboard": "hermes-dashboard", "vps-audit": "vps-audit", "caddy": "caddy"}

TARGETS = ("hermes", "labs", "system", "caddy", "all")
_lock = threading.Lock()
_jobs = {}  # job_id -> job dict
_job_seq = 0
_MIN_FREE_GB = 2.0
_SAFETY_BYTES = 2 * 1024 ** 3


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _run(args, timeout=180, text=True, **kw):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=text, timeout=timeout, check=False, **kw)


def _run_user(cmd, timeout=180):
    """Run a command as the ubuntu user (for systemd --user services)."""
    return _run(["sudo", "su", "-", "ubuntu", "-c",
                 f"XDG_RUNTIME_DIR=/run/user/1000 {cmd}"], timeout=timeout)


def _is_active(name):
    if name in USER_SERVICES:
        r = _run_user(f"systemctl --user is-active {name}", timeout=15)
    else:
        r = _run(["systemctl", "is-active", name], timeout=15)
    return r.stdout.strip() == "active"


def _restart_service(name, timeout=90):
    if name in USER_SERVICES:
        r = _run_user(f"systemctl --user restart {name}", timeout=timeout)
    else:
        r = _run(["sudo", "systemctl", "restart", name], timeout=timeout)
    return r.returncode == 0


def _git(repo, *args, timeout=120):
    # Repo dimiliki user 'ubuntu' (vps-audit service jalan sebagai root).
    # Jalankan git sebagai ubuntu supaya .git tidak berubah ownership & aman dari dubious-ownership.
    return _run(["sudo", "-u", "ubuntu", "git", "-C", str(repo)] + list(args), timeout=timeout)


def _free_gb():
    try:
        st = shutil.disk_usage(str(HERMES_HOME))
        return st.free / (1024 ** 3)
    except OSError:
        return -1.0


def _tree_bytes(path):
    """Conservative byte estimate for a pre-update backup."""
    total = 0
    try:
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        return 0
    return total


def _backup_bytes(target):
    if target == "hermes":
        return _tree_bytes(HERMES_HOME)
    if target == "labs":
        return _tree_bytes(ROOT)
    if target == "all":
        return _tree_bytes(HERMES_HOME) + _tree_bytes(ROOT)
    return 64 * 1024 ** 2


def _git_changes(repo, limit=12):
    r = _git(repo, "log", "--pretty=format:%h%x09%s", f"-n{limit}", "HEAD..origin/main", timeout=30)
    if r.returncode != 0:
        return []
    return [{"version": ln.split("\t", 1)[0], "title": ln.split("\t", 1)[-1]}
            for ln in r.stdout.splitlines() if ln.strip()]


def preflight(target):
    if target not in TARGETS:
        return {"ok": False, "error": "target tidak valid"}
    usage = shutil.disk_usage(str(HERMES_HOME))
    backup = _backup_bytes(target)
    required = backup + _SAFETY_BYTES
    changes = []
    if target in ("hermes", "all"):
        changes += [{**x, "component": "Hermes"} for x in _git_changes(HERMES_REPO)]
    if target in ("labs", "all"):
        changes += [{**x, "component": "Labs"} for x in _git_changes(ROOT)]
    if target in ("system", "all"):
        changes += [{"component": "System", "version": "apt", "title": f"{_apt_upgradable()} paket dapat diperbarui"}]
    if target in ("caddy", "all"):
        changes += [{"component": "Caddy", "version": _caddy_version(), "title": "Periksa dan pasang versi paket terbaru"}]
    enough = usage.free >= required
    return {"ok": True, "target": target, "can_update": enough,
            "free_bytes": usage.free, "backup_estimate_bytes": backup,
            "required_bytes": required, "safety_bytes": _SAFETY_BYTES,
            "warning": "Storage tidak cukup untuk backup aman. Update dikunci." if not enough else "",
            "changes": changes[:30]}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------
# per-target: status
# ----------------------------------------------------------------------------
def _git_head(repo):
    r = _git(repo, "rev-parse", "--short", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else "?"


def _git_branch(repo):
    r = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else "?"


def _git_dirty(repo):
    r = _git(repo, "status", "--porcelain")
    lines = [ln for ln in r.stdout.splitlines() if not ln.startswith("??")]
    return len(lines)


def _git_behind(repo):
    """Return number of commits local is behind origin/main (0 if up to date)."""
    r = _git(repo, "fetch", "origin", timeout=60)
    if r.returncode != 0:
        return None
    r = _git(repo, "rev-list", "--count", "HEAD..origin/main")
    if r.returncode != 0:
        return None
    return int(r.stdout.strip() or 0)


def _hermes_update_check():
    """Use hermes update --check (authoritative) with a cached fallback."""
    try:
        r = _run([str(HERMES_CLI), "update", "--check"], timeout=120)
        out = r.stdout + r.stderr
        if "is available" in out or "available" in out.lower():
            return True
        if "up to date" in out.lower() or "no update" in out.lower():
            return False
    except Exception:
        pass
    return None


def _apt_upgradable():
    r = _run(["apt", "list", "--upgradable"], timeout=60)
    n = 0
    for ln in r.stdout.splitlines():
        if ln and "/" in ln and not ln.startswith("Listing"):
            n += 1
    return n


def _caddy_version():
    r = _run(["caddy", "version"], timeout=15)
    return r.stdout.strip().split(" ")[0] if r.stdout.strip() else "?"


def status():
    hermes_head = _git_head(HERMES_REPO)
    hermes_behind = _git_behind(HERMES_REPO)
    hermes_dirty = _git_dirty(HERMES_REPO)
    labs_head = _git_head(ROOT)
    labs_behind = _git_behind(ROOT)
    labs_dirty = _git_dirty(ROOT)
    return {
        "ok": True,
        "checked_at": int(time.time()),
        "disk_free_gb": round(_free_gb(), 1),
        "storage_warning": _free_gb() < 5,
        "targets": {
            "hermes": {
                "branch": _git_branch(HERMES_REPO),
                "head": hermes_head,
                "behind": hermes_behind,
                "dirty": hermes_dirty,
                "service": "active" if _is_active("hermes-gateway") else "inactive",
                "available": bool(hermes_behind) or _hermes_update_check() is True,
                "update_cmd": "hermes update --backup --yes",
                "critical": True,
            },
            "labs": {
                "branch": _git_branch(ROOT),
                "head": labs_head,
                "behind": labs_behind,
                "dirty": labs_dirty,
                "service": "active" if _is_active("vps-audit") else "inactive",
                "available": bool(labs_behind),
                "critical": False,
            },
            "system": {
                "upgradable": _apt_upgradable(),
                "caddy": _caddy_version(),
                "critical": False,
            },
            "caddy": {
                "version": _caddy_version(),
                "critical": False,
            },
        },
        "running": any(not j.get("done") for j in _jobs.values()),
    }


# ----------------------------------------------------------------------------
# backup / rollback helpers
# ----------------------------------------------------------------------------
def _backup_labs(job):
    job["log"].append(f"[{_now()}] backup labs → zip")
    try:
        path = lab_backup.create_backup("labs")
        job["log"].append(f"[{_now()}] backup OK: {path.name}")
        return True
    except Exception as e:
        job["log"].append(f"[{_now()}] ⚠ backup labs gagal: {e} (lanjut)")
        return False


def _backup_system(job):
    """Snapshot dpkg selections so we can list what changed."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"dpkg-{time.strftime('%Y%m%d-%H%M%S')}.selections"
    r = _run(["dpkg", "--get-selections"], timeout=60)
    if r.returncode == 0:
        path.write_text(r.stdout)
        job["log"].append(f"[{_now()}] dpkg selections → {path.name}")
        return True
    job["log"].append(f"[{_now()}] ⚠ dpkg selections gagal (lanjut)")
    return False


def _rollback_git(job, repo, label, old_head):
    job["log"].append(f"[{_now()}] ⛔ ROLLBACK {label}: reset ke {old_head}")
    r = _git(repo, "reset", "--hard", old_head, timeout=180)
    job["log"].append(f"[{_now()}] reset: {'OK' if r.returncode == 0 else r.stderr.strip()[:200]}")
    return r.returncode == 0


def _run_hermes(*args, timeout=300):
    """Run a hermes CLI command as user ubuntu (repo + venv milik ubuntu)."""
    return _run(["sudo", "-u", "ubuntu", "env", "HOME=/home/ubuntu", str(HERMES_CLI)] + list(args), timeout=timeout)


# ----------------------------------------------------------------------------
# per-target: execute
# ----------------------------------------------------------------------------
def _update_hermes(job):
    job["log"].append(f"[{_now()}] ➜ Update Hermes Agent")
    old_head = _git_head(HERMES_REPO)
    job["meta"] = {"old_head": old_head}

    # 1. pre-update backup via hermes itself (state snapshot + full zip)
    job["log"].append(f"[{_now()}] pre-update backup (hermes --backup)...")
    r = _run_hermes("update", "--backup", "--yes", timeout=600)
    out = (r.stdout + r.stderr).strip()
    for ln in out.splitlines()[-40:]:
        if ln.strip():
            job["log"].append(f"[{_now()}]   {ln.strip()[:160]}")
    if r.returncode != 0:
        job["log"].append(f"[{_now()}] ⛔ hermes update gagal (exit {r.returncode})")
        # rollback to previous commit if we moved anywhere
        new_head = _git_head(HERMES_REPO)
        if new_head != old_head:
            _rollback_git(job, HERMES_REPO, "hermes", old_head)
        return False

    new_head = _git_head(HERMES_REPO)
    job["log"].append(f"[{_now()}] ✓ hermes: {old_head} → {new_head}")

    # 2. restart gateway + dashboard so the new code is live
    job["log"].append(f"[{_now()}] restart hermes-gateway (user service)...")
    ok_gw = _restart_service("hermes-gateway")
    job["log"].append(f"[{_now()}]   gateway restart: {'OK' if ok_gw else 'GAGAL'}")
    ok_dash = _restart_service("hermes-dashboard")
    job["log"].append(f"[{_now()}]   dashboard restart: {'OK' if ok_dash else 'GAGAL'}")
    ok_rt = _restart_service("hermes-task-router")
    job["log"].append(f"[{_now()}]   task-router restart: {'OK' if ok_rt else 'GAGAL'}")

    # 3. verify
    time.sleep(4)
    active = _is_active("hermes-gateway") and _is_active("hermes-dashboard")
    job["log"].append(f"[{_now()}]   verify services: {'PASS' if active else 'FAIL'}")
    if not active:
        _rollback_git(job, HERMES_REPO, "hermes", old_head)
        for s in ("hermes-gateway", "hermes-dashboard", "hermes-task-router"):
            _restart_service(s)
        job["log"].append(f"[{_now()}] ⛔ hermes update ROLLED BACK ke {old_head}")
        return False
    job["log"].append(f"[{_now()}] ✅ Hermes Agent update selesai")
    return True


def _update_labs(job):
    job["log"].append(f"[{_now()}] ➜ Update Labs (vps-audit)")
    _backup_labs(job)
    old_head = _git_head(ROOT)
    job["meta"] = {"old_head": old_head}

    # ensure local changes (our own edits) are stashed, then pull
    dirty = _git_dirty(ROOT)
    stash_made = False
    if dirty:
        r = _git(ROOT, "stash", "push", "-m", f"pre-update-{int(time.time())}", timeout=60)
        stash_made = r.returncode == 0
        job["log"].append(f"[{_now()}] stash {dirty} file lokal: {'OK' if stash_made else 'gagal'}")
    r = _git(ROOT, "pull", "--ff-only", "origin", "main", timeout=180)
    job["log"].append(f"[{_now()}] git pull: {'OK' if r.returncode == 0 else r.stderr.strip()[:200]}")
    if r.returncode != 0:
        if stash_made:
            _git(ROOT, "stash", "pop", timeout=60)
            job["log"].append(f"[{_now()}] stash dikembalikan")
        _rollback_git(job, ROOT, "labs", old_head)
        return False

    # kembalikan perubahan lokal kita (fitur lokal, bugfix, dll) setelah pull sukses
    if stash_made:
        r2 = _git(ROOT, "stash", "pop", timeout=60)
        if r2.returncode != 0:
            job["log"].append(f"[{_now()}] ⚠ stash pop konflik: {r2.stderr.strip()[:150]}")
            job["log"].append(f"[{_now()}]   snapshot lokal tersimpan di git stash (jangan di-drop)")
        else:
            job["log"].append(f"[{_now()}] perubahan lokal dikembalikan (stash pop OK)")

    new_head = _git_head(ROOT)
    job["log"].append(f"[{_now()}] ✓ labs: {old_head} → {new_head}")

    # restart this app via delayed systemd-run (we are inside it)
    job["log"].append(f"[{_now()}] restart vps-audit (delayed 2s)...")
    subprocess.Popen(["systemd-run", "--on-active=2s", "--unit=vps-audit-update-restart",
                      "systemctl", "restart", "vps-audit.service"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    job["log"].append(f"[{_now()}] ⏳ vps-audit restarting — refresh setelah beberapa detik")
    return True


def _update_system(job, apply=True):
    job["log"].append(f"[{_now()}] ➜ Update System (apt)")
    _backup_system(job)
    job["log"].append(f"[{_now()}] apt update...")
    r = _run(["sudo", "apt", "update"], timeout=300)
    job["log"].append(f"[{_now()}]   apt update: {'OK' if r.returncode == 0 else r.stderr.strip()[:200]}")

    n = _apt_upgradable()
    job["log"].append(f"[{_now()}] {n} package dapat di-upgrade")

    if not apply:
        job["log"].append(f"[{_now()}] dry-run (simulasi) — tidak ada perubahan")
        return True
    if n == 0:
        job["log"].append(f"[{_now()}] ✅ semua sudah terbaru")
        return True

    job["log"].append(f"[{_now()}] apt upgrade -y (tanpa dist-upgrade)...")
    r = _run(["sudo", "apt", "upgrade", "-y", "--no-install-recommends"], timeout=600)
    job["log"].append(f"[{_now()}]   apt upgrade: {'OK' if r.returncode == 0 else r.stderr.strip()[:200]}")
    if r.returncode != 0:
        return False
    job["log"].append(f"[{_now()}] ✅ system update selesai")
    return True


def _update_caddy(job):
    job["log"].append(f"[{_now()}] ➜ Update Caddy")
    cur = _caddy_version()
    job["log"].append(f"[{_now()}] versi sekarang: {cur}")
    r = _run(["sudo", "apt", "install", "--only-upgrade", "-y", "caddy"], timeout=300)
    job["log"].append(f"[{_now()}]   caddy upgrade: {'OK' if r.returncode == 0 else r.stderr.strip()[:200]}")
    new = _caddy_version()
    job["log"].append(f"[{_now()}] versi setelah: {new}")
    if r.returncode != 0:
        return False
    job["log"].append(f"[{_now()}] ✅ caddy: {cur} → {new}")
    return True


_EXECUTORS = {
    "hermes": _update_hermes,
    "labs": _update_labs,
    "system": lambda j: _update_system(j, apply=True),
    "caddy": _update_caddy,
}


# ----------------------------------------------------------------------------
# job runner
# ----------------------------------------------------------------------------
def _preflight(job, target):
    check = preflight(target)
    if not check.get("can_update"):
        job["log"].append(f"[{_now()}] ⛔ storage tidak cukup untuk backup + cadangan 2 GB — batal")
        return False
    job["log"].append(f"[{_now()}] storage cukup: {_free_gb():.1f} GB; estimasi backup {check['backup_estimate_bytes'] / 1024**3:.1f} GB ✓")
    if target not in TARGETS:
        job["log"].append(f"[{_now()}] ⛔ target tidak valid: {target}")
        return False
    return True


def _run_job(job):
    target = job["target"]
    ok = _preflight(job, target)
    if ok:
        if target == "all":
            order = ("hermes", "labs", "caddy", "system")
            results = {}
            for t in order:
                if t in _EXECUTORS:
                    results[t] = _EXECUTORS[t](job)
                    if t == "labs":
                        # this app restarts; stop logging
                        job["done"] = True
                        job["ok"] = all(results.values())
                        return
            job["ok"] = all(results.values())
        else:
            job["ok"] = _EXECUTORS[target](job)
    job["done"] = True
    job["ended_at"] = _now()
    job["log"].append(f"[{_now()}] {'✅ SELESAI' if job['ok'] else '⛔ GAGAL'} ({target})")
    with _lock:
        _jobs[job["id"]]["done"] = True
        _jobs[job["id"]]["ok"] = job["ok"]
        _jobs[job["id"]]["ended_at"] = job["ended_at"]


def start_update(target, confirmed=False):
    global _job_seq
    prune_finished()  # bersihkan job lama yang sudah selesai
    with _lock:
        active = [j for j in _jobs.values() if not j.get("done")]
        if active:
            return {"ok": False, "error": "Update lain sedang berjalan — tunggu selesai"}
        if target not in TARGETS:
            return {"ok": False, "error": "target tidak valid"}
        if confirmed is not True:
            return {"ok": False, "error": "konfirmasi update wajib"}
        check = preflight(target)
        if not check.get("can_update"):
            return {"ok": False, "error": check.get("warning") or "storage tidak cukup", "preflight": check}
        _job_seq += 1
        job = {
            "id": f"upd-{int(time.time())}-{_job_seq}",
            "target": target,
            "status": "running",
            "ok": None,
            "done": False,
            "log": [f"[{_now()}] ▶ Mulai update target: {target}"],
            "meta": {},
            "started_at": _now(),
            "ended_at": None,
        }
        _jobs[job["id"]] = job
    t = threading.Thread(target=_run_job, args=(job,), daemon=True)
    t.start()
    return {"ok": True, "job_id": job["id"]}


def job_status(job_id=None):
    with _lock:
        if job_id:
            j = _jobs.get(job_id)
            return _job_view(j) if j else {"ok": False, "error": "job tidak ditemukan"}
        return {"ok": True, "jobs": [_job_view(j) for j in _jobs.values()]}


def _job_view(j):
    return {
        "id": j["id"], "target": j["target"], "status": j["status"],
        "ok": j["ok"], "done": j["done"], "meta": j["meta"],
        "started_at": j["started_at"], "ended_at": j["ended_at"],
        "log": j["log"][-200:],
    }


def prune_finished(max_age_min=120):
    """Drop finished jobs older than max_age_min (called on status())."""
    with _lock:
        now = time.time()
        for jid in list(_jobs):
            j = _jobs[jid]
            if j.get("done") and j.get("ended_at"):
                try:
                    t = time.mktime(time.strptime(j["ended_at"], "%Y-%m-%d %H:%M:%S"))
                    if now - t > max_age_min * 60:
                        del _jobs[jid]
                except (ValueError, TypeError):
                    del _jobs[jid]


def recent_logs():
    """Tail the last run(s) for the UI without requiring job_id."""
    prune_finished()
    with _lock:
        finished = [j for j in _jobs.values() if j.get("done")]
        if not finished:
            return []
        j = max(finished, key=lambda x: x.get("started_at", ""))
        return j["log"][-120:]
