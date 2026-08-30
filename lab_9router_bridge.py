"""Labs 9router bridge — nyalakan 9router on-demand untuk OAuth login.

Konsep: 9router punya logika OAuth lengkap (client registration AWS, signing key,
device flow) yang tidak bisa direplikasi Labs. Jadi Labs memakainya sebagai
"token-issuer on-demand":
  1. start_9router()  -> jalankan 9router headless di background
  2. get_device_code(provider) -> minta device code dari 9router API
  3. user buka link, login (AWS Builder ID / GitHub / dll)
  4. poll_token(provider, ...) -> 9router menyelesaikan login & simpan token ke DB
  5. stop_9router()   -> matikan lagi (hemat RAM)

Semua provider yang didukung 9router bisa dipakai (kiro, github, qwen, dll).
"""
import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:20128"
NODE = shutil.which("node") or "/usr/bin/node"
PROC = None


def _req(method, path, payload=None, timeout=30):
    """Request ke 9router API dengan CLI token auth (mirror 9router CLI)."""
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    # CLI token: sha256(machineId + "9r-cli-auth")[:16]
    try:
        mpath = Path("/home/ubuntu/.9router/machine-id")
        if mpath.exists():
            mid = mpath.read_text().strip()
            import hashlib
            cli_token = hashlib.sha256((mid + "9r-cli-auth").encode()).hexdigest()[:16]
            headers["x-9r-cli-token"] = cli_token
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


def is_running():
    """Cek apakah 9router API merespons."""
    r = _req("GET", "/api/providers", timeout=3)
    return "connections" in r


def start_9router(timeout=30):
    """Nyalakan 9router headless; return status."""
    global PROC
    if is_running():
        return {"ok": True, "note": "sudah berjalan"}
    # cek binary
    for cand in ("/usr/bin/9router",):
        if os.path.exists(cand):
            cmd = [NODE, cand, "-p", "20128", "--no-browser", "--skip-update"]
            break
    else:
        return {"ok": False, "error": "9router binary tidak ditemukan"}
    try:
        PROC = subprocess.Popen(
            cmd, cwd="/home/ubuntu",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}
    # tunggu API siap
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_running():
            return {"ok": True, "pid": PROC.pid, "note": "9router aktif"}
        time.sleep(1)
    stop_9router()
    return {"ok": False, "error": "9router tidak merespons setelah %ds" % timeout}


def stop_9router():
    """Matikan 9router (hemat RAM)."""
    global PROC
    if PROC:
        try:
            os.killpg(os.getpgid(PROC.pid), signal.SIGTERM)
        except Exception:
            try:
                PROC.terminate()
            except Exception:
                pass
        PROC = None
    return {"ok": True}


def list_oauth_providers():
    """Provider OAuth yang didukung 9router (dari device-code endpoint)."""
    # 9router mendukung: kiro, github, qwen (DEVICE_CODE_PROVIDERS)
    candidates = ["kiro", "github", "qwen"]
    out = []
    for p in candidates:
        r = _req("GET", f"/api/oauth/{p}/device-code", timeout=5)
        if r.get("device_code"):
            out.append(p)
    return out


def get_device_code(provider):
    """Minta device code dari 9router. provider: kiro | github | qwen | ...
    Sertakan codeVerifier & extraData (seluruh respon, tanpa self-ref) untuk poll."""
    r = _req("GET", f"/api/oauth/{provider}/device-code", timeout=15)
    if r.get("device_code"):
        r["codeVerifier"] = r.get("codeVerifier") or r.get("code_verifier") or ""
        # CLI 9router: extraData = deviceData.extraData || deviceData (seluruh respon)
        extra = dict(r)  # shallow copy
        extra.pop("extraData", None)
        r["extraData"] = r.get("extraData") or extra
        r["interval"] = r.get("interval") or 5
    return r


def poll_token(provider, device_code, code_verifier=None, extra_data=None, timeout=120):
    """Polling hasil login dari 9router. Blocking sampai selesai/expired.
    code_verifier & extra_data WAJIB diambil dari get_device_code()."""
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
        # error terminal
        if r.get("error") and r["error"] not in ("authorization_pending", "slow_down"):
            return {"ok": False, "error": r["error"]}
        last = r
        time.sleep(3)
    return {"ok": False, "error": "timeout menunggu login", "last": last}


def get_models(provider_or_id=""):
    """Model tersedia dari 9router."""
    path = "/api/providers" + (f"/{provider_or_id}/models" if provider_or_id else "")
    return _req("GET", path)


if __name__ == "__main__":
    print("start:", start_9router())
    print("oauth providers:", list_oauth_providers())
    print("stop:", stop_9router())
