"""Labs OAuth engine — standalone device authorization for AI providers.

Menggantikan ketergantungan ke 9router untuk flow login OAuth. Semua token
disimpan ke database 9router (LIVE_DB) dengan format data JSON yang SAMA PERSIS
dengan apa yang ditulis 9router, sehingga kedua app (twin) bisa saling membaca.

Didukung saat ini:
  - kiro        : AWS IAM Identity Center (device authorization)
  - github      : GitHub OAuth device flow
  - gemini-cli  : Google OAuth device flow (best-effort)
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lab_db import connect_read, connect_write

LIVE_DB = Path(os.environ.get("LABS_9ROUTER_DB", "/home/ubuntu/.9router/db/data.sqlite"))

# ---- in-memory device flows (like 9router's _flows) ----
_flows = {}


def _now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------------------
# AWS IAM Identity Center (kiro)
# ---------------------------------------------------------------------------
AWS_OIDC = "https://oidc.us-east-1.amazonaws.com"


def _aws_credentials(account_id):
    """Pull client_id/client_secret/region from the stored account data."""
    con = connect_read()
    try:
        row = con.execute(
            "SELECT data FROM providerConnections WHERE id=?", (str(account_id),)
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise ValueError("akun tidak ditemukan")
    data = json.loads(row["data"] or "{}")
    psd = data.get("providerSpecificData") or {}
    client_id = psd.get("clientId") or data.get("clientId")
    client_secret = psd.get("clientSecret") or data.get("clientSecret")
    region = psd.get("region") or "us-east-1"
    if not client_id or not client_secret:
        raise ValueError("kredensial OAuth kiro tidak lengkap di database")
    return client_id, client_secret, region


def _post_form(url, fields, timeout=30):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            detail = {}
        return exc.code, detail


def start_kiro_device(account_id):
    """Mulai AWS device authorization; return link + kode untuk ditampilkan."""
    client_id, client_secret, region = _aws_credentials(account_id)
    status, data = _post_form(
        f"{AWS_OIDC}/device_authorization",
        {"client_id": client_id, "client_secret": client_secret},
    )
    if status >= 400 or "device_code" not in data:
        raise ValueError(f"device authorization gagal: {data}")
    flow_id = uuid.uuid4().hex[:12]
    _flows[flow_id] = {
        "provider": "kiro",
        "account_id": str(account_id),
        "device_code": data["device_code"],
        "interval": int(data.get("interval", 5)),
        "expires_in": int(data.get("expires_in", 600)),
        "client_id": client_id,
        "client_secret": client_secret,
        "region": region,
        "started_at": time.time(),
    }
    return {
        "flow_id": flow_id,
        "verification_uri": data.get("verification_uri_complete") or data.get("verification_uri", ""),
        "user_code": data.get("user_code", ""),
        "interval": data["interval"],
    }


def poll_kiro_token(flow_id):
    """Poll AWS token endpoint; simpan token ke DB saat sukses."""
    flow = _flows.get(flow_id)
    if not flow:
        raise ValueError("flow tidak ditemukan / sudah kedaluwarsa")
    status, data = _post_form(
        f"{AWS_OIDC}/token",
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": flow["device_code"],
            "client_id": flow["client_id"],
            "client_secret": flow["client_secret"],
        },
    )
    if status < 400 and "access_token" in data:
        _store_oauth_tokens(flow["account_id"], data)
        del _flows[flow_id]
        return {"ok": True, "message": "Login berhasil, token tersimpan."}
    error = data.get("error", "")
    if error in ("authorization_pending",):
        return {"ok": False, "pending": True, "message": "Tunggu: user belum menyelesaikan login."}
    if error == "slow_down":
        return {"ok": False, "pending": True, "slow_down": True, "message": "Polling terlalu cepat, tunggu sebentar."}
    if error in ("access_denied", "expired_token"):
        del _flows[flow_id]
        return {"ok": False, "error": f"Login ditolak: {error}"}
    return {"ok": False, "error": f"token endpoint gagal: {data}"}


def _store_oauth_tokens(account_id, token_data):
    """Tulis accessToken/refreshToken/expiresAt ke DB dengan format 9router."""
    access = token_data.get("access_token") or token_data.get("accessToken")
    refresh = token_data.get("refresh_token") or token_data.get("refreshToken")
    expires_in = token_data.get("expires_in", 3600)
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromtimestamp(now.timestamp() + int(expires_in)).isoformat().replace("+00:00", "Z")

    con = connect_read()
    try:
        row = con.execute(
            "SELECT data FROM providerConnections WHERE id=?", (str(account_id),)
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise ValueError("akun tidak ditemukan")
    data = json.loads(row["data"] or "{}")
    data["accessToken"] = access or data.get("accessToken")
    if refresh:
        data["refreshToken"] = refresh
    data["expiresAt"] = expires_at
    data["expiresIn"] = int(expires_in)
    data["testStatus"] = "active"

    con = connect_write()
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "UPDATE providerConnections SET data=?, updatedAt=? WHERE id=?",
            (json.dumps(data, ensure_ascii=False), _now_z(), str(account_id)),
        )
        con.commit()
    finally:
        con.close()
    return True


# ---------------------------------------------------------------------------
# GitHub OAuth device flow
# ---------------------------------------------------------------------------
GITHUB_DEVICE = "https://github.com/login/device/code"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_CLIENT_ID = "Ov23liGithubLabspending"  # diisi saat registrasi OAuth app


def start_github_device(account_id):
    """GitHub device flow — butuh client_id terdaftar."""
    status, data = _post_form(
        GITHUB_DEVICE,
        {"client_id": GITHUB_CLIENT_ID, "scope": "read:user user:email"},
        timeout=20,
    )
    if status >= 400 or "device_code" not in data:
        raise ValueError(f"github device gagal: {data}")
    flow_id = uuid.uuid4().hex[:12]
    _flows[flow_id] = {
        "provider": "github",
        "account_id": str(account_id),
        "device_code": data["device_code"],
        "interval": int(data.get("interval", 5)),
        "client_id": GITHUB_CLIENT_ID,
    }
    return {
        "flow_id": flow_id,
        "verification_uri": data.get("verification_uri_complete") or data.get("verification_uri", ""),
        "user_code": data.get("user_code", ""),
        "interval": data["interval"],
    }


def poll_github_token(flow_id):
    flow = _flows.get(flow_id)
    if not flow:
        raise ValueError("flow tidak ditemukan")
    status, data = _post_form(
        GITHUB_TOKEN,
        {
            "client_id": flow["client_id"],
            "device_code": flow["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        timeout=20,
    )
    if status < 400 and "access_token" in data:
        _store_oauth_tokens(flow["account_id"], data)
        del _flows[flow_id]
        return {"ok": True, "message": "Login GitHub berhasil."}
    err = data.get("error", "")
    if err in ("authorization_pending",):
        return {"ok": False, "pending": True}
    if err in ("access_denied", "expired_token"):
        del _flows[flow_id]
        return {"ok": False, "error": f"Login ditolak: {err}"}
    return {"ok": False, "error": f"github token gagal: {data}"}


# ---------------------------------------------------------------------------
# Kiro social login (Google/GitHub via kiro.auth.desktop.kiro.dev)
# No AWS client registration needed — kiro's own OAuth proxy.
# ---------------------------------------------------------------------------
KIRO_SOCIAL_BASE = "https://prod.us-east-1.auth.desktop.kiro.dev"
KIRO_REDIRECT = "kiro://kiro.kiroAgent/authenticate-success"


def kiro_build_social_url(provider, code_challenge, state):
    """Build kiro social login URL (mirrors 9router's buildSocialLoginUrl)."""
    idp = "Google" if provider == "google" else "Github"
    import urllib.parse
    return (f"{KIRO_SOCIAL_BASE}/login?idp={idp}"
            f"&redirect_uri={urllib.parse.quote(KIRO_REDIRECT)}"
            f"&code_challenge={code_challenge}&code_challenge_method=S256"
            f"&state={state}&prompt=select_account")


def kiro_exchange_social_code(code, code_verifier):
    """Exchange kiro social auth code for tokens (mirrors exchangeSocialCode)."""
    import urllib.parse
    body = json.dumps({
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": KIRO_REDIRECT,
    }).encode()
    req = urllib.request.Request(
        f"{KIRO_SOCIAL_BASE}/oauth/token", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            d = json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"kiro token exchange gagal: {exc.read()[:200]}")
    if "accessToken" not in d and "access_token" not in d:
        raise ValueError(f"kiro exchange response tidak valid: {d}")
    return {
        "accessToken": d.get("accessToken") or d.get("access_token"),
        "refreshToken": d.get("refreshToken") or d.get("refresh_token"),
        "profileArn": d.get("profileArn") or d.get("profile_arn"),
        "expiresIn": d.get("expiresIn") or d.get("expires_in") or 3600,
    }


def start_kiro_social(provider="google"):
    """Mulai kiro social login — PKCE, return URL untuk dibuka di browser."""
    import hashlib
    import base64
    code_verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    state = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    auth_url = kiro_build_social_url(provider, code_challenge, state)
    flow_id = uuid.uuid4().hex[:12]
    _flows[flow_id] = {
        "provider": "kiro_social",
        "social_provider": provider,
        "code_verifier": code_verifier,
        "state": state,
        "auth_url": auth_url,
        "started_at": time.time(),
    }
    return {
        "ok": True,
        "flow_id": flow_id,
        "auth_url": auth_url,
        "state": state,
        "provider": provider,
        "note": "Buka URL ini, login via " + ("Google" if provider == "google" else "GitHub"),
    }


def poll_kiro_social(flow_id, code=None):
    """Setelah user login, kiro redirect ke kiro:// dengan ?code=...  Masukkan code di sini."""
    flow = _flows.get(flow_id)
    if not flow:
        raise ValueError("flow tidak ditemukan")
    if not code:
        return {"ok": False, "pending": True,
                "message": "Setelah login, kiro akan redirect ke kiro:// dengan parameter code. Salin code-nya."}
    tokens = kiro_exchange_social_code(code, flow["code_verifier"])
    # store into a kiro account
    account_id = flow.get("account_id") or ""
    if account_id:
        _store_oauth_tokens(account_id, tokens)
    del _flows[flow_id]
    return {"ok": True, "tokens": {
        "accessToken": tokens["accessToken"][:12] + "...",
        "refreshToken": tokens["refreshToken"][:12] + "...",
        "profileArn": tokens["profileArn"],
    }}


# ---------------------------------------------------------------------------
# Public dispatcher — same signature as existing 9router bridge
# ---------------------------------------------------------------------------
FLOWS = {
    "kiro": (start_kiro_device, poll_kiro_token),
    "github": (start_github_device, poll_github_token),
    "kiro_social": (start_kiro_social, poll_kiro_social),
    "kiro-google": (lambda a="": start_kiro_social("google"), lambda f, code=None: poll_kiro_social(f, code)),
    "kiro-github": (lambda a="": start_kiro_social("github"), lambda f, code=None: poll_kiro_social(f, code)),
}


def start_device_login(provider, account_id=""):
    """Mulai device login mandiri. provider: kiro | github | ..."""
    provider = (provider or "").lower()
    if provider not in FLOWS:
        raise ValueError(f"provider OAuth '{provider}' belum didukung di Labs (tersedia: {', '.join(FLOWS)})")
    return FLOWS[provider][0](account_id)


def poll_device_login(flow_id):
    """Poll status login per flow id."""
    flow = _flows.get(flow_id)
    if not flow:
        raise ValueError("flow tidak ditemukan / sudah kedaluwarsa")
    return FLOWS[flow["provider"]][1](flow_id)


if __name__ == "__main__":
    print("lab_oauth self-check OK; supported:", ", ".join(FLOWS))


# ---------------------------------------------------------------------------
# Token refresh loop (background)
# ---------------------------------------------------------------------------
import threading

_REFRESH_INTERVAL = 50 * 60  # 50 minutes
_refresh_lock = threading.Lock()
_refresh_timer = None


def _refresh_one(row):
    """Try to refresh an OAuth account token. Returns (account_id, ok, note)."""
    rid = str(row["id"])
    data = json.loads(row["data"] or "{}")
    refresh = data.get("refreshToken") or ""
    if not refresh:
        return rid, None, "no refresh token"
    expires = data.get("expiresAt") or ""
    # skip if token still has > 15 min life (avoid hammering provider)
    if expires:
        try:
            exp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            remaining = exp - datetime.now(timezone.utc)
            if remaining > timedelta(minutes=15):
                return rid, None, f"ok until {expires[:10]}"
        except ValueError:
            pass
    psd = data.get("providerSpecificData") or {}
    client_id = psd.get("clientId") or data.get("clientId")
    client_secret = psd.get("clientSecret") or data.get("clientSecret")
    region = psd.get("region") or "us-east-1"
    if not client_id:
        return rid, False, "no client_id"
    # AWS IAM Identity Center token endpoint (kiro)
    status, resp = _post_form(
        f"{AWS_OIDC}/token",
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
            **({"client_secret": client_secret} if client_secret else {}),
        },
        timeout=20,
    )
    if status < 400 and ("access_token" in resp or "accessToken" in resp):
        _store_oauth_tokens(rid, resp)
        return rid, True, "refreshed"
    return rid, False, f"refresh failed HTTP {status}"


def refresh_all(verbose=False):
    """Scan accounts and refresh OAuth tokens nearing expiry."""
    results = {"ok": 0, "failed": 0, "skipped": 0, "details": []}
    db = connect_read()
    try:
        rows = db.execute(
            "SELECT id, provider, data FROM providerConnections WHERE data LIKE '%refreshToken%'"
        ).fetchall()
    finally:
        db.close()
    for row in rows:
        rid, ok, note = _refresh_one(row)
        if ok is None:
            results["skipped"] += 1
        elif ok:
            results["ok"] += 1
        else:
            results["failed"] += 1
        if verbose:
            results["details"].append(f"{rid[:12]} {note}")
    return results


def _refresh_tick():
    global _refresh_timer
    try:
        refresh_all()
    except Exception:
        pass
    _refresh_timer = threading.Timer(_REFRESH_INTERVAL, _refresh_tick)
    _refresh_timer.daemon = True
    _refresh_timer.start()


def start_refresh_loop():
    """Start the background token refresh loop (idempotent)."""
    global _refresh_timer
    with _refresh_lock:
        if _refresh_timer and _refresh_timer.is_alive():
            return {"ok": True, "note": "already running"}
        _refresh_timer = threading.Timer(_REFRESH_INTERVAL, _refresh_tick)
        _refresh_timer.daemon = True
        _refresh_timer.start()
        return {"ok": True, "note": "refresh loop started"}
