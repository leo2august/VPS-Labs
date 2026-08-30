#!/usr/bin/env python3
"""Labs Kiro login via AWS CLI SSO — device/authorization flow.

Meniru cara 9router: login AWS Builder ID via AWS CLI, lalu import refreshToken
dari ~/.aws/sso/cache/ ke database 9router (shared twin DB).

Alur:
1. aws sso login --profile kiro --no-browser  → cetak URL authorize
2. User buka URL di browser, login AWS Builder ID / Google
3. AWS CLI menyelesaikan login → tulis ~/.aws/sso/cache/*.json (refreshToken aorAAAAAG)
4. import_kiro_token() membaca cache & buat provider connection di DB
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

HOME = Path.home()
AWS_CFG = HOME / ".aws"
SSO_CACHE = AWS_CFG / "sso" / "cache"
KIRO_PROFILE = "kiro"


def ensure_config():
    """Pastikan ~/.aws/config punya profile kiro + sso-session."""
    AWS_CFG.mkdir(parents=True, exist_ok=True)
    cfg_path = AWS_CFG / "config"
    if cfg_path.exists() and "profile kiro" in cfg_path.read_text():
        return
    cfg = """[profile kiro]
sso_session = kiro
sso_region = us-east-1
sso_start_url = https://view.awsapps.com/start
region = us-east-1
output = json

[sso-session kiro]
sso_start_url = https://view.awsapps.com/start
sso_region = us-east-1
sso_registration_scopes = sso:account:access:sso:role:access:codewhisperer:conversations
"""
    cfg_path.write_text(cfg)
    os.chmod(cfg_path, 0o600)


def start_login():
    """Mulai flow login; return auth_url untuk dibuka user di browser."""
    ensure_config()
    # Jalankan aws sso login di background; tangkap URL authorize dari stdout
    proc = subprocess.Popen(
        ["aws", "sso", "login", "--profile", KIRO_PROFILE, "--no-browser"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    out = ""
    try:
        while proc.poll() is None:
            chunk = proc.stdout.read(1)
            if chunk:
                out += chunk
                if "authorize?" in out or "Please visit" in out:
                    break
    except Exception:
        pass
    # ambil URL https://...authorize?...
    m = re.search(r"https://[^\s]+authorize\?[^\s]+", out)
    if not m:
        # coba tunggu output selesai
        try:
            rest = proc.stdout.read()
            out += rest or ""
            m = re.search(r"https://[^\s]+authorize\?[^\s]+", out)
        except Exception:
            pass
    if not m:
        proc.kill()
        return {"ok": False, "error": "Tidak dapat mengambil URL authorize dari AWS CLI: " + out[:200]}
    url = m.group(0).strip()
    return {"ok": True, "auth_url": url, "note": "Buka URL ini di browser, login AWS Builder ID. Setelah sukses, token otomatis tersimpan."}


def poll_login(timeout=180):
    """Tunggu sampai AWS CLI menulis refreshToken ke SSO cache, lalu import."""
    SSO_CACHE.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        for f in SSO_CACHE.glob("*.json"):
            if f.name in seen:
                continue
            try:
                data = json.loads(f.read_text())
                rt = data.get("refreshToken") or ""
                if rt.startswith("aorAAAAAG"):
                    return {"ok": True, "refresh_token": rt, "source": f.name}
                seen.add(f.name)
            except Exception:
                continue
        time.sleep(2)
    return {"ok": False, "error": "Timeout menunggu refresh token dari AWS SSO cache."}


def get_cached_token():
    """Baca refreshToken kiro dari SSO cache (jika sudah ada)."""
    if not SSO_CACHE.exists():
        return None
    for f in SSO_CACHE.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            rt = data.get("refreshToken") or ""
            if rt.startswith("aorAAAAAG"):
                return {"refresh_token": rt, "source": f.name}
        except Exception:
            continue
    return None


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "poll":
        r = poll_login(int(sys.argv[2]) if len(sys.argv) > 2 else 180)
        print(json.dumps(r))
    else:
        r = start_login()
        print(json.dumps(r))
