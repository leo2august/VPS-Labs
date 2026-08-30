"""Labs 9router bridge — 9router sebagai token-issuer on-demand.

9router service (systemd) nyala saat butuh OAuth login provider
(kiro, github, qwen, kilocode). Labs:
  1. start_9router() -> pastikan 9router jalan
  2. get_device_code(provider) -> minta device code
  3. user buka link, login di browser
  4. poll_token() -> tunggu sampai login selesai, akun tersimpan di DB
  5. watchdog: 9router otomatis mati 15 menit setelah terdeteksi nyala
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:20128"
NODE = shutil.which("node") or "/usr/bin/node"
PROC = None
_STARTED_AT = None
_WATCHDOG_SEC = 15 * 60
_watchdog_lock = threading.Lock()
_watchdog_timer = None


def _req(method, path, payload=None, timeout=30):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    try:
        mpath = Path("/home/ubuntu/.9router/machine-id")
        if mpath.exists():
            import hashlib
            mid = mpath.read_text().strip()
            headers["x-9r-cli-token"] = hashlib.sha256((mid + "9r-cli-auth").encode()).hexdigest()[:16]
    except Exception:
        pass
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


def _mark_started():
    global _STARTED_AT
    with _watchdog_lock:
        if _STARTED_AT is None:
            _STARTED_AT = time.time()


def _mark_stopped():
    global _STARTED_AT
    with _watchdog_lock:
        _STARTED_AT = None


def is_running():
    try:
        s = socket.create_connection(("127.0.0.1", 20128), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def _systemctl(action):
    try:
        r = subprocess.run(["systemctl", action, "9router.service"],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def start_9router(timeout=60):
    global PROC
    if is_running():
        _mark_started()
        return {"ok": True, "note": "sudah berjalan"}
    # systemd start
    _systemctl("start")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_running():
            _mark_started()
            return {"ok": True, "note": "9router aktif (systemd)"}
        time.sleep(1)
    # fallback subprocess
    if os.path.exists("/usr/bin/9router"):
        try:
            PROC = subprocess.Popen(
                ["/usr/bin/node", "/usr/bin/9router", "-p", "20128", "--no-browser", "--skip-update"],
                cwd="/home/ubuntu", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}
        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_running():
                _mark_started()
                return {"ok": True, "pid": PROC.pid, "note": "9router aktif (subprocess)"}
            time.sleep(1)
        try:
            os.killpg(os.getpgid(PROC.pid), signal.SIGTERM)
        except Exception:
            pass
        PROC = None
    return {"ok": False, "error": "9router tidak bisa dijalankan"}


def stop_9router():
    global PROC, _STARTED_AT
    with _watchdog_lock:
        _STARTED_AT = None
    if PROC:
        try:
            os.killpg(os.getpgid(PROC.pid), signal.SIGTERM)
        except Exception:
            pass
        PROC = None
    try:
        subprocess.run(["systemctl", "stop", "9router.service"], capture_output=True, timeout=30)
    except Exception:
        pass
    return {"ok": True, "note": "9router dimatikan"}


def _watchdog_tick():
    global _watchdog_timer
    try:
        with _watchdog_lock:
            started = _STARTED_AT
        if is_running():
            if started is None:
                _mark_started()
            elif (time.time() - started) > _WATCHDOG_SEC:
                stop_9router()
    except Exception:
        pass
    _watchdog_timer = threading.Timer(60, _watchdog_tick)
    _watchdog_timer.daemon = True
    _watchdog_timer.start()


def start_watchdog():
    global _watchdog_timer
    with _watchdog_lock:
        if _watchdog_timer and _watchdog_timer.is_alive():
            return
        _watchdog_timer = threading.Timer(60, _watchdog_tick)
        _watchdog_timer.daemon = True
        _watchdog_timer.start()


def list_oauth_providers():
    candidates = ["kiro", "github", "qwen", "kilocode"]
    out = []
    for p in candidates:
        r = _req("GET", f"/api/oauth/{p}/device-code", timeout=5)
        if r.get("device_code"):
            out.append(p)
    return out


def get_device_code(provider):
    r = _req("GET", f"/api/oauth/{provider}/device-code", timeout=15)
    if r.get("device_code"):
        r["codeVerifier"] = r.get("codeVerifier") or r.get("code_verifier") or ""
        extra = dict(r)
        extra.pop("extraData", None)
        r["extraData"] = r.get("extraData") or extra
        r["interval"] = r.get("interval") or 5
    return r


def poll_token(provider, device_code, code_verifier=None, extra_data=None, timeout=120):
    body = {"deviceCode": device_code}
    if code_verifier:
        body["codeVerifier"] = code_verifier
    if extra_data:
        body["extraData"] = extra_data
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        r = _req("POST", f"/api/oauth/{provider}/poll", body, timeout=20)
        if r.get("success") or r.get("connection"):
            return {"ok": True, "data": r}
        if r.get("pending"):
            time.sleep(3)
            continue
        if r.get("error") and r["error"] not in ("authorization_pending", "slow_down"):
            return {"ok": False, "error": r["error"]}
        last = r
        time.sleep(3)
    return {"ok": False, "error": "timeout menunggu login", "last": last}


if __name__ == "__main__":
    print("start:", start_9router())
    print("oauth providers:", list_oauth_providers())
    print("stop:", stop_9router())
