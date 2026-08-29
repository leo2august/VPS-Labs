"""
⚠️  VPS Sentinel Labs — Copyright (c) 2026 Leo2agust. All Rights Reserved.
    License: https://github.com/leo2august/VPS-Labs/blob/main/LICENSE
    This software may be installed on your own server for personal use only.
    Redistribution, forking, or claiming ownership is prohibited.
"""

#!/usr/bin/env python3
import hmac, io, json, os, platform, re, smtplib, socket, sqlite3, subprocess, threading, time, zipfile
from collections import deque
from functools import wraps
from pathlib import Path
from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
import psutil

import lab_integration
import lab_features
import lab_admin
import lab_chat
import lab_snapshot
import lab_crud
import lab_webui
import lab_operations
import lab_provider
import lab_proxy
import lab_quota
import lab_oauth
import lab_security
import lab_backup
import lab_router_accounts
import lab_failover
import lab_account
import lab_cron
import lab_update
import lab_malware
import lab_restart

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "latest-report.txt"
SCRIPT = ROOT / "vps-audit.sh"

def _lab_brand():
    """Nama branding dinamis dari lab-settings.json (default: Labs)."""
    try:
        import lab_operations
        v = lab_operations.get_lab_settings().get("values", {})
        return (v.get("brand_name") or "Labs"), (v.get("brand_sub") or "Laboratory")
    except Exception:
        return "Labs", "Laboratory"


app = Flask(__name__)
app.secret_key = os.environ.get("NUVULABS_SECRET", "")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=os.environ.get("NUVULABS_SECURE_COOKIE") == "1", PERMANENT_SESSION_LIFETIME=43200, MAX_CONTENT_LENGTH=200 * 1024 * 1024)

# ---- CSRF defense (defense-in-depth on top of SameSite=Lax) ----
_CSRF_SAFE_PATHS = ("/login", "/forgot-password", "/logout", "/reset/", "/health")


@app.before_request
def _csrf_guard():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if not request.path.startswith("/api/"):
        return None
    origin = request.headers.get("Origin") or ""
    if not origin:
        return None  # non-browser clients (curl, internal) are fine
    try:
        from urllib.parse import urlparse
        o = urlparse(origin)
        if o.scheme in ("http", "https") and o.netloc == request.host:
            return None
    except ValueError:
        pass
    return jsonify(error="origin_tidak_diizinkan"), 403


@app.after_request
def _security_headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if resp.headers.get("Server", "").startswith("Werkzeug"):
        resp.headers["Server"] = "Leo2agust"
    return resp

# ---- Login brute-force guard (in-memory, per IP + per username) ----
_login_attempts = {}


def _rate_limited(key: str, limit: int = 10, window: int = 900) -> bool:
    now = time.time()
    row = _login_attempts.get(key)
    if row is None or now - row["start"] > window:
        _login_attempts[key] = {"start": now, "count": 1}
        return False
    row["count"] += 1
    return row["count"] > limit


def _clear_rate(key: str):
    _login_attempts.pop(key, None)
lock = threading.Lock()
history = deque(maxlen=144)
net_history = deque(maxlen=144)
io_history = deque(maxlen=144)
boot_net = psutil.net_io_counters()
last_net = {"at": time.time(), "sent": boot_net.bytes_sent, "recv": boot_net.bytes_recv}
_boot_io = None
try:
    _boot_io = psutil.disk_io_counters()
except (psutil.Error, AttributeError):
    pass
last_io = {"at": time.time(), "read": getattr(_boot_io, "read_bytes", 0) if _boot_io else 0, "write": getattr(_boot_io, "write_bytes", 0) if _boot_io else 0}
last_audit = {"running": False, "at": 0, "checks": [], "summary": {"pass": 0, "warn": 0, "fail": 0}}
SERVICES = ("hermes-webui", "hermes-dashboard", "hermes-gateway", "hermes-ta\1***", "vps-audit", "caddy", "fail2ban", "9router", "model-router")
USER_SERVICES = {"hermes-gateway", "hermes-ta\1***"}


def sh(*args, timeout=4):
    try:
        return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if session.get("authenticated"):
            return fn(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify(error="authentication_required"), 401
        return redirect(url_for("login", next=request.full_path))
    return wrapped


def clamp(value):
    return round(max(0, min(100, value)), 1)


USER_SERVICE_PGREP = {
    "hermes-gateway": ["hermes_cli", "main"],
    "hermes-ta\1***": ["ta\1***", "router.py"],
}


def service_state(name):
    if name in USER_SERVICES:
        # user services: prefer systemctl --user via ubuntu's su, else pgrep fallback
        state = ""
        try:
            r = subprocess.run(["sudo", "su", "-", "ubuntu", "-c",
                                "XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active " + name],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8, check=False)
            state = r.stdout.strip()
        except Exception:
            state = ""
        if state not in {"active", "inactive", "failed", "activating", "deactivating"}:
            pats = USER_SERVICE_PGREP.get(name, [name])
            try:
                r = subprocess.run(["pgrep", "-f", "|".join(pats)], text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False)
                state = "active" if r.stdout.strip() else "inactive"
            except Exception:
                state = "inactive"
        return state
    state = sh("systemctl", "is-active", name)
    return state if state in {"active", "inactive", "failed", "activating", "deactivating"} else "missing"


def metric_status(value, warn=70, danger=90):
    return "danger" if value >= danger else "warn" if value >= warn else "healthy"


def metrics():
    global last_io
    vm, swap, disk = psutil.virtual_memory(), psutil.swap_memory(), psutil.disk_usage("/")
    net, now = psutil.net_io_counters(), time.time()
    elapsed = max(now - last_net["at"], .1)
    rx_rate = max(0, (net.bytes_recv - last_net["recv"]) / elapsed)
    tx_rate = max(0, (net.bytes_sent - last_net["sent"]) / elapsed)
    last_net.update(at=now, recv=net.bytes_recv, sent=net.bytes_sent)
    # disk IO rates
    try:
        io = psutil.disk_io_counters()
        io_elapsed = max(now - last_io["at"], .1)
        read_rate = max(0, (io.read_bytes - last_io["read"]) / io_elapsed) if io else 0
        write_rate = max(0, (io.write_bytes - last_io["write"]) / io_elapsed) if io else 0
        last_io = {"at": now, "read": io.read_bytes, "write": io.write_bytes}
    except (psutil.Error, AttributeError):
        read_rate = write_rate = 0
    cpu = clamp(psutil.cpu_percent(interval=.12))
    data = {
        "at": int(now), "cpu": cpu, "memory": clamp(vm.percent), "disk": clamp(disk.percent), "swap": clamp(swap.percent),
        "memory_used": vm.used, "memory_total": vm.total, "disk_used": disk.used, "disk_total": disk.total,
        "load": [round(x, 2) for x in os.getloadavg()], "uptime": int(now - psutil.boot_time()),
        "net_sent": net.bytes_sent, "net_recv": net.bytes_recv, "net_tx_rate": round(tx_rate), "net_rx_rate": round(rx_rate),
        "io_read_rate": round(read_rate), "io_write_rate": round(write_rate),
        "processes": len(psutil.pids()), "connections": len(psutil.net_connections(kind="inet")),
        "cores": psutil.cpu_count(logical=True),
    }
    history.append({k: data[k] for k in ("at", "cpu", "memory", "disk")})
    net_history.append({"at": data["at"], "rx": data["net_rx_rate"], "tx": data["net_tx_rate"]})
    io_history.append({"at": data["at"], "read": data["io_read_rate"], "write": data["io_write_rate"]})
    return data


def process_list(limit=30):
    rows = []
    for proc in psutil.process_iter(("pid", "name", "username", "cpu_percent", "memory_percent", "status", "create_time")):
        try:
            info = proc.info
            rows.append({"pid": info["pid"], "name": info["name"] or "unknown", "user": info["username"] or "-",
                         "cpu": round(info["cpu_percent"] or 0, 1), "memory": round(info["memory_percent"] or 0, 1),
                         "status": info["status"], "since": int(info["create_time"] or 0)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return sorted(rows, key=lambda x: x["cpu"] + x["memory"], reverse=True)[:limit]


def filesystems():
    rows, seen = [], set()
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint in seen or not part.device.startswith("/"):
            continue
        seen.add(part.mountpoint)
        try:
            usage = psutil.disk_usage(part.mountpoint)
            rows.append({"device": part.device, "mount": part.mountpoint, "type": part.fstype, "percent": clamp(usage.percent), "used": usage.used, "total": usage.total})
        except (PermissionError, OSError):
            pass
    return rows


PORT_CATALOG = {
    22: ("SSH", "Akses administrasi VPS", None),
    53: ("DNS resolver", "Resolusi nama lokal", None),
    443: ("HTTPS / Caddy", "Gateway domain publik", None),
    2019: ("Caddy Admin API", "Kontrol internal reverse proxy", None),
    3000: ("9router UI", "Panel dan API router AI", "9router"),
    5002: ("Kanji API", "Backend Atlas Kanji", "kanji-api"),
    5030: ("WMS", "Warehouse Management System", "wms"),
    5031: ("Kasir POS", "Backend Kasir POS", "pos"),
    8099: ("Hermes WebUI API", "Backend WebUI internal", "hermes-webui"),
    8787: ("Hermes WebUI", "Panel Hermes internal", "hermes-webui"),
    9118: ("Leo2agust Labs", "Dashboard ini", None),
    9119: ("Hermes Dashboard", "Dashboard Hermes internal", "hermes-dashboard"),
    20128: ("9router", "Router model AI", "9router"),
    20129: ("Task Router", "Routing model berbasis tugas", "hermes-ta\1***"),
}


def ports():
    rows = []
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            pid, port = conn.pid or 0, conn.laddr.port
            try: name = psutil.Process(pid).name() if pid else "kernel"
            except (psutil.Error, PermissionError): name = "unknown"
            label, purpose, service = PORT_CATALOG.get(port, (name.title(), "Listener aplikasi atau sistem", None))
            address = conn.laddr.ip or "*"
            exposure = "public" if address in ("0.0.0.0", "::", "*") else ("tailscale" if address.startswith("100.") else "local")
            rows.append({"address": address, "port": port, "pid": pid, "process": name,
                         "label": label, "purpose": purpose, "service": service,
                         "exposure": exposure, "closable": bool(service)})
    except (psutil.Error, PermissionError):
        pass
    return sorted(rows, key=lambda x: (x["exposure"] != "public", x["port"], x["address"]))


def close_port(port, confirmed=False):
    if not confirmed:
        return {"ok": False, "error": "konfirmasi wajib"}
    try: port = int(port)
    except (TypeError, ValueError): return {"ok": False, "error": "port tidak valid"}
    current = next((x for x in ports() if x["port"] == port and x.get("service")), None)
    if not current:
        return {"ok": False, "error": "port tidak aktif atau tidak dikelola Labs"}
    result = lab_integration.service_action(current["service"], "stop")
    result.update(port=port, label=current["label"])
    return result


def network_interfaces():
    """Per-interface counters with rates (sampled on call; rate from boot deltas)."""
    rows = []
    try:
        counters = psutil.net_io_counters(pernic=True)
    except (psutil.Error, OSError):
        return rows
    boot = time.time() - psutil.boot_time()
    for name, c in sorted(counters.items()):
        if not c.bytes_recv and not c.bytes_sent:
            continue  # skip idle virtual interfaces
        rows.append({
            "name": name, "rx": c.bytes_recv, "tx": c.bytes_sent,
            "rx_rate": round(c.bytes_recv / max(boot, 1)), "tx_rate": round(c.bytes_sent / max(boot, 1)),
            "packets_recv": c.packets_recv, "packets_sent": c.packets_sent,
            "errin": c.errin, "errout": c.errout, "dropin": c.dropin, "dropout": c.dropout,
        })
    return rows


def parse_report(text):
    checks = [{"status": status.lower(), "name": name.strip(), "detail": detail.strip()}
              for status, name, detail in re.findall(r"^\[(PASS|WARN|FAIL)]\s+(.+?)\s+-\s+(.+)$", text, re.M)]
    return checks, {k: sum(c["status"] == k for c in checks) for k in ("pass", "warn", "fail")}


def run_audit():
    if not lock.acquire(blocking=False): return
    last_audit["running"] = True
    last_audit.pop("error", None)
    try:
        proc = subprocess.run([str(SCRIPT)], cwd=ROOT, env=os.environ | {"TERM": "dumb"}, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        clean = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout)
        REPORT.write_text(clean)
        checks, summary = parse_report(clean)
        last_audit.update(at=int(time.time()), checks=checks, summary=summary, exit_code=proc.returncode)
    except Exception as exc:
        last_audit.update(at=int(time.time()), error=str(exc))
    finally:
        last_audit["running"] = False
        lock.release()


@app.route("/login", methods=("GET", "POST"))
def login():
    if session.get("authenticated"): return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        user = request.form.get("username", "")
        password = request.form.get("password", "")
        ip = request.remote_addr or "?"
        if _rate_limited(f"ip:{ip}", limit=15, window=900) or _rate_limited(f"user:{user}", limit=10, window=900):
            time.sleep(1.5)
            return render_template("login.html", error="Terlalu banyak percobaan. Tunggu 15 menit.", brand_name=_lab_brand()[0], brand_sub=_lab_brand()[1]), 429
        valid = lab_account.valid_identifier(user) and hmac.compare_digest(password, os.environ.get("NUVULABS_PASSWORD", ""))
        if valid:
            _clear_rate(f"ip:{ip}"); _clear_rate(f"user:{user}")
            session.clear(); session.permanent = True; session["authenticated"] = True; session["user"] = user
            target = request.args.get("next", "/")
            return redirect(target if target.startswith("/") and not target.startswith("//") else "/")
        time.sleep(.35); error = "Username atau password salah."
    return render_template("login.html", error=error, brand_name=_lab_brand()[0], brand_sub=_lab_brand()[1])


@app.post("/forgot-password")
def forgot_password():
    ip = request.remote_addr or "?"
    if _rate_limited(f"fp:{ip}", limit=5, window=3600):
        return render_template("login.html", notice="Terlalu banyak permintaan reset. Tunggu 1 jam.", brand_name=_lab_brand()[0], brand_sub=_lab_brand()[1]), 429
    try:
        result = lab_account.request_reset(request.form.get("identifier", ""), request.url_root)
    except (OSError, ValueError, smtplib.SMTPException):
        result = {'ok': True, 'message': 'Jika akun dan email pemulihan aktif, tautan reset sudah dikirim.'}
    return render_template("login.html", notice=result['message'], brand_name=_lab_brand()[0], brand_sub=_lab_brand()[1])


@app.route("/reset/<token>", methods=("GET", "POST"))
def reset_password(token):
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if password != request.form.get("password2", ""):
            error = "Ulangi password dengan sama."
        else:
            result = lab_account.reset_password(token, password)
            if result.get('ok'):
                subprocess.Popen(['systemctl', 'daemon-reload'])
                subprocess.Popen(['systemctl', 'restart', 'vps-audit.service'])
                return render_template("login.html", notice="Password berubah. Masuk memakai password baru.", brand_name=_lab_brand()[0], brand_sub=_lab_brand()[1])
            error = result.get('error')
    return render_template("reset.html", token=token, error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    return render_template("index.html", host=socket.gethostname(), user=session.get("user", "operator"),
                           brand_name=_lab_brand()[0])


@app.get("/api/overview")
@login_required
def overview():
    data = metrics()
    runtime = lab_admin._current_runtime_model()
    data.update({"host": socket.gethostname(), "os": platform.system(), "os_detail": platform.platform(), "kernel": platform.release(),
                 "audit": last_audit, "history": list(history), "net_history": list(net_history), "io_history": list(io_history),
                 "services": [{"name": n, "state": service_state(n)} for n in SERVICES],
                 "process_list": process_list(), "filesystems": filesystems(), "ports": ports(),
                 "interfaces": network_interfaces(),
                 "model": (runtime or {}).get("model", "—"),
                 "provider": (runtime or {}).get("provider", "—"),
                 "model_runtime": runtime})
    data["health"] = {"cpu": metric_status(data["cpu"]), "memory": metric_status(data["memory"]), "disk": metric_status(data["disk"], 75, 90)}
    return jsonify(data)

@app.get("/api/interfaces")
@login_required
def interfaces():
    return jsonify({"interfaces": network_interfaces(), "net_history": list(net_history)})


@app.post("/api/ports/close")
@login_required
def api_close_port():
    body = request.get_json(force=True) or {}
    return jsonify(close_port(body.get("port"), body.get("confirmed") is True))


@app.get("/api/logs")
@login_required
def logs():
    """Tail recent journald / syslog lines."""
    lines = sh("journalctl", "-n", "120", "--no-pager", "-o", "short", timeout=6)
    if not lines:
        lines = sh("tail", "-n", "120", "/var/log/syslog", timeout=4)
    out = []
    for line in lines.splitlines()[-120:]:
        m = re.match(r"^(\w{3}\s+\d+\s+\d+:\d+:\d+)\s+(\S+)\s+(.*)$", line)
        if m:
            out.append({"time": m.group(1), "host": m.group(2), "msg": m.group(3)[:300]})
        else:
            out.append({"time": "", "host": "", "msg": line[:300]})
    return jsonify({"logs": out})


@app.get("/api/system")
@login_required
def system_info():
    vm = psutil.virtual_memory()
    return jsonify({
        "hostname": socket.gethostname(), "os": platform.system(), "os_release": platform.release(),
        "os_version": platform.version(), "machine": platform.machine(), "python": platform.python_version(),
        "cores_physical": psutil.cpu_count(logical=False), "cores_logical": psutil.cpu_count(logical=True),
        "cpu_freq_mhz": round(getattr(psutil.cpu_freq(), "current", 0) or 0),
        "ram_total": vm.total, "swap_total": psutil.swap_memory().total,
        "boot_time": int(psutil.boot_time()),
    })


def _cache_stats():
    roots = (Path("/home/USER/.cache"), Path("/var/cache/apt/archives"))
    total = files = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
                    files += 1
            except OSError:
                pass
    vm = psutil.virtual_memory()
    reclaimable = max(0, vm.cached + vm.buffers)
    return {"cache_bytes": total, "cache_files": files, "ram_available": vm.available,
            "ram_total": vm.total, "ram_reclaimable": reclaimable}


@app.get("/api/storage/cleanup")
@login_required
def storage_cleanup_status():
    return jsonify(ok=True, **_cache_stats())


@app.post("/api/storage/cleanup")
@login_required
def storage_cleanup():
    action = (request.get_json(silent=True) or {}).get("action", "")
    if action == "ram":
        try:
            subprocess.run(["sync"], check=True, timeout=15)
            Path("/proc/sys/vm/drop_caches").write_text("3\n")
        except (OSError, subprocess.SubprocessError) as exc:
            return jsonify(ok=False, error=str(exc)), 500
    elif action == "cache":
        # Cache only: never touch app data, config, logs, or running processes.
        roots = (Path("/home/USER/.cache"), Path("/var/cache/apt/archives"))
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                try:
                    if path.is_symlink() or path.is_file():
                        path.unlink(missing_ok=True)
                    elif path.is_dir():
                        path.rmdir()
                except OSError:
                    pass
    else:
        return jsonify(ok=False, error="aksi tidak valid"), 400
    return jsonify(ok=True, action=action, **_cache_stats())


@app.post("/api/audit")
@login_required
def audit():
    if not last_audit["running"]: threading.Thread(target=run_audit, daemon=True).start()
    return jsonify(ok=True, running=True), 202


@app.post("/api/lab/malware-scan")
@login_required
def api_malware_scan_start():
    body = request.get_json(silent=True) or {}
    return jsonify(lab_malware.start(body.get("full") is True))


@app.get("/api/lab/malware-scan")
@login_required
def api_malware_scan_status():
    return jsonify(lab_malware.status(str(request.args.get("job_id", ""))))


@app.get("/api/lab/malware-report")
@login_required
def api_malware_report():
    return jsonify(lab_malware.latest_report())


@app.get("/api/lab/malware-report/download")
@login_required
def api_malware_report_download():
    report = lab_malware.latest_report()
    if not report.get("ok"):
        return jsonify(report), 404
    payload = json.dumps(report, ensure_ascii=False, indent=2).encode()
    return send_file(io.BytesIO(payload), mimetype="application/json", as_attachment=True,
                     download_name=f"malware-report-{report.get('id', 'latest')}.json")


@app.post("/api/lab/malware-report/agent")
@login_required
def api_malware_report_agent():
    report = lab_malware.latest_report()
    if not report.get("ok"):
        return jsonify(report), 404
    job_id = lab_features.start_agent_job(lab_malware.agent_prompt(report))
    return jsonify(ok=bool(job_id), job_id=job_id)


@app.get("/api/router")
@login_required
def api_router():
    return jsonify({
        "static": lab_integration.parse_router_static(),
        "hermes": lab_integration.read_hermes_config(),
        "status": lab_integration.router_status(),
    })


@app.get("/api/webui/settings")
@login_required
def api_webui_settings():
    return jsonify(lab_integration.read_webui_settings())


@app.post("/api/webui/settings")
@login_required
def api_webui_setting_update():
    body = request.get_json(force=True) or {}
    key = body.get("key", "")
    value = body.get("value")
    if not key:
        return jsonify(ok=False, error="key wajib"), 400
    return jsonify(lab_integration.update_webui_setting(key, value))



@app.post("/api/router/default")
@login_required
def api_router_default():
    body = request.get_json(force=True) or {}
    model = body.get("model", "")
    provider = body.get("provider", "")
    return jsonify(lab_integration.update_default_model(model, provider))


@app.get("/api/lab/skills")
@login_required
def api_lab_skills():
    return jsonify(lab_features.list_skills())

@app.get("/api/lab/skill")
@login_required
def api_lab_skill():
    name = request.args.get("name", "")
    return jsonify(lab_features.get_skill(name))

@app.get("/api/lab/memory")
@login_required
def api_lab_memory():
    return jsonify(lab_features.get_memories())

@app.post("/api/lab/memory")
@login_required
def api_lab_memory_save():
    body = request.get_json(force=True) or {}
    return jsonify(lab_features.save_memory(str(body.get("file", "")), str(body.get("content", ""))))

@app.get("/api/lab/sessions")
@login_required
def api_lab_sessions():
    limit = int(request.args.get("limit", 60))
    return jsonify(lab_operations.list_sessions(limit))

@app.get("/api/lab/session")
@login_required
def api_lab_session():
    sid = request.args.get("id", "")
    return jsonify(lab_operations.get_session(sid))


@app.get("/api/lab/chat/history")
@login_required
def api_lab_chat_history():
    """Riwayat chat Labs dari server (session_id) — persist walau localStorage hilang."""
    sid = str(request.args.get("session_id", "")).strip()
    if not sid or not sid.startswith("labs_"):
        return jsonify(ok=True, messages=[])
    try:
        con = sqlite3.connect("/home/USER/.hermes/state.db", timeout=5)
        con.row_factory = sqlite3.Row
        msgs = con.execute(
            "SELECT role, content, timestamp FROM messages WHERE session_id=? ORDER BY timestamp, id",
            (sid,)).fetchall()
        con.close()
        return jsonify(ok=True, messages=[dict(m) for m in msgs])
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:200], messages=[])

@app.post("/api/lab/chat")
@login_required
def api_lab_chat():
    body = request.get_json(force=True) or {}
    messages = body.get("messages") or []
    model = str(body.get("model", ""))
    provider = str(body.get("provider", ""))
    max_tokens = int(body.get("max_tokens", 1200))
    result = lab_features.chat(messages, model, provider, max_tokens)
    if result.get("ok") and messages:
        result["session_id"] = lab_operations.record_lab_exchange(
            str(body.get("session_id", "")), str(messages[-1].get("content", "")), result.get("reply", ""), model or provider)
    return jsonify(result)


@app.post("/api/lab/agent")
@login_required
def api_lab_agent():
    """Chat Labs dalam mode Agent — delegasi ke Hermes agent (async job)."""
    body = request.get_json(force=True) or {}
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        return jsonify(ok=False, error="Prompt kosong"), 400
    model = str(body.get("model", ""))
    provider = str(body.get("provider", ""))
    history = body.get("history") or []
    session_id = str(body.get("session_id", ""))
    job_id = lab_features.start_agent_job(prompt, model, provider, history, session_id)
    if not job_id:
        return jsonify(ok=False, error="Gagal membuat job"), 500
    return jsonify(ok=True, job_id=job_id)


@app.get("/api/lab/agent/status")
@login_required
def api_lab_agent_status():
    job_id = str(request.args.get("job_id", ""))
    st = lab_features.agent_job_status(job_id)
    if st.get("status") == "done" and st.get("ok") and not st.get("recorded"):
        lab_operations.record_lab_exchange(str(st.get("session_id") or ""), st.get("prompt", ""), st.get("reply", ""), "agent")
        lab_features.mark_agent_job_recorded(job_id)
    return jsonify(st)


@app.post("/api/lab/agent/cancel")
@login_required
def api_lab_agent_cancel():
    body = request.get_json(force=True) or {}
    job_id = str(body.get("job_id", ""))
    return jsonify(ok=lab_features.cancel_agent_job(job_id))


@app.get("/api/lab/attachments")
@login_required
def api_lab_attachments():
    return jsonify(ok=True, attachments=lab_features.list_attachments())


@app.get("/api/lab/attachments/<path:attachment_id>")
@login_required
def api_lab_attachment_download(attachment_id):
    p = lab_features.attachment_path(attachment_id)
    if not p:
        return jsonify(ok=False, error="Attachment tidak ditemukan"), 404
    return send_file(p, as_attachment=True, download_name=p.name)


@app.post("/api/lab/attachments/delete")
@login_required
def api_lab_attachments_delete():
    """Hapus attachment batch (multi-select). Body: {"ids": ["job/name", ...]}"""
    body = request.get_json(force=True) or {}
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify(ok=False, error="Tidak ada attachment dipilih"), 400
    if len(ids) > 200:
        return jsonify(ok=False, error="Maksimal 200 item per penghapusan"), 400
    result = lab_features.delete_attachments(ids)
    return jsonify(ok=True, **result)


@app.get("/api/lab/activity")
@login_required
def api_lab_activity():
    return jsonify(lab_operations.activity(int(request.args.get("limit", 80))))


@app.get("/api/lab/settings")
@login_required
def api_lab_settings():
    return jsonify(lab_operations.get_lab_settings())


@app.post("/api/lab/settings")
@login_required
def api_lab_settings_update():
    body = request.get_json(force=True) or {}
    return jsonify(lab_operations.update_lab_setting(str(body.get("key", "")), str(body.get("value", ""))))


@app.get("/api/lab/config")
@login_required
def api_lab_config():
    return jsonify(lab_admin.config_summary())


@app.route("/api/lab/gateway-routes", methods=("GET", "POST"))
@login_required
def api_lab_gateway_routes():
    if request.method == "GET":
        return jsonify(lab_admin.gateway_routes())
    body = request.get_json(silent=True) or {}
    result = lab_admin.update_gateway_route(body.get("source", ""), body.get("provider", ""), body.get("model", ""))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.get("/api/lab/gateway")
@login_required
def api_lab_gateway():
    return jsonify(lab_admin.gateway_status())

@app.post("/api/lab/gateway/action")
@login_required
def api_lab_gateway_action():
    body = request.get_json(force=True) or {}
    return jsonify(lab_admin.gateway_action(str(body.get("unit", "")), str(body.get("action", ""))))

@app.get("/api/lab/gateway/logs")
@login_required
def api_lab_gateway_logs():
    return jsonify(lab_admin.gateway_logs(int(request.args.get("lines", 60))))

@app.get("/api/lab/usage")
@login_required
def api_lab_usage():
    try: period = int(request.args.get("period", 30))
    except ValueError: period = 30
    return jsonify(lab_admin.usage_stats(period))


@app.get("/api/lab/provider-quota")
@login_required
def api_lab_provider_quota():
    return jsonify(lab_quota.quota_status())


@app.post("/api/lab/router-account/toggle")
@login_required
def api_lab_router_account_toggle():
    b = request.get_json(force=True) or {}
    try:
        return jsonify(lab_router_accounts.update_account(b.get("id"), b.get("enabled")))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/lab/router-account/toggle-provider")
@login_required
def api_lab_router_account_toggle_provider():
    b = request.get_json(force=True) or {}
    try:
        result = lab_router_accounts.update_provider_accounts(b.get("provider"), b.get("enabled"))
        return jsonify(result), (200 if result.get("ok") else 502)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/lab/router-account/test")
@login_required
def api_lab_router_account_test():
    b = request.get_json(force=True) or {}
    try:
        return jsonify(lab_router_accounts.test_account(b.get("id")))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.get("/api/lab/router-account/models")
@login_required
def api_lab_router_account_models():
    try:
        return jsonify(lab_router_accounts.account_models(request.args.get("id")))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/lab/router-account/delete")
@login_required
def api_lab_router_account_delete():
    b = request.get_json(force=True) or {}
    try:
        return jsonify(lab_router_accounts.delete_account(b.get("id")))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/lab/router-account/api-key")
@login_required
def api_lab_router_account_api_key():
    b = request.get_json(force=True) or {}
    try:
        return jsonify(lab_router_accounts.create_api_key(b.get("provider"), b.get("name"), b.get("api_key")))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/lab/router-login/start")
@login_required
def api_lab_router_login_start():
    b = request.get_json(force=True) or {}
    try:
        return jsonify(lab_router_accounts.start_device_login(b.get("provider"), b.get("account_id") or ""))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/lab/router-login/poll")
@login_required
def api_lab_router_login_poll():
    b = request.get_json(force=True) or {}
    try:
        return jsonify(lab_router_accounts.poll_device_login(b.get("flow_id")))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.get("/api/lab/failover")
@login_required
def api_lab_failover():
    return jsonify(lab_failover.failover_status())


# ---- Labs API Proxy (OpenAI-compatible, twin without 9router) ----
@app.route("/v1/chat/completions", methods=["POST"])
@login_required
def api_v1_chat_completions():
    body = request.get_json(force=True, silent=True) or {}
    data, status = lab_proxy.chat_completions(body)
    resp = app.response_class(
        response=json.dumps(data, ensure_ascii=False),
        status=status,
        mimetype="application/json",
    )
    return resp


@app.route("/v1/models", methods=["GET"])
@login_required
def api_v1_models():
    models = lab_proxy.list_models()
    return jsonify({"object": "list", "data": [{"id": m, "object": "model"} for m in models]})


@app.get("/api/lab/cron-jobs")
@login_required
def api_lab_cron_jobs():
    crontab, timers = lab_cron.system_sources()
    return jsonify({
        "ok": True,
        "jobs": lab_cron.list_jobs(),
        "scheduler": lab_cron.scheduler_status(),
        "crontab": crontab,
        "timers": timers,
        "now": lab_cron._now(),
    })


@app.post("/api/lab/cron-jobs/action")
@login_required
def api_lab_cron_job_action():
    b = request.get_json(force=True) or {}
    job_id = (b.get("id") or "").strip()
    act = (b.get("action") or "").strip()
    if not job_id or act not in ("pause", "resume", "run", "remove"):
        return jsonify(ok=False, error="Parameter id/action tidak valid"), 400
    r = lab_cron.action(job_id, act)
    return jsonify(r)


@app.post("/api/lab/cron-jobs/model")
@login_required
def api_lab_cron_job_model():
    b = request.get_json(force=True) or {}
    job_id = (b.get("id") or "").strip()
    model = b.get("model")
    provider = b.get("provider")
    if not job_id:
        return jsonify(ok=False, error="id job wajib"), 400
    try:
        return jsonify(lab_cron.update_job(job_id, model=model, provider=provider))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/lab/failover")
@login_required
def api_lab_failover_toggle():
    b = request.get_json(force=True) or {}
    if "enabled" in b:
        return jsonify(lab_failover.failover_set_enabled(b["enabled"]))
    if b.get("action") == "run_now":
        return jsonify(lab_failover.failover_tick(force_keepalive=True))
    return jsonify(ok=False, error="unknown action"), 400


@app.get("/api/lab/usage/report.pdf")
@login_required
def api_lab_usage_pdf():
    try: period = int(request.args.get("period", 30))
    except ValueError: period = 30
    data = lab_admin.usage_stats(period)
    return send_file(io.BytesIO(lab_admin.usage_report_pdf(data)), mimetype="application/pdf",
                     as_attachment=True, download_name=f"leo2agust-labs-usage-{data['period_days']}d.pdf")


@app.get("/api/lab/models")
@login_required
def api_lab_models():
    return jsonify(lab_chat.list_available_models())


@app.get("/api/lab/9router-snapshot")
@login_required
def api_lab_snapshot():
    return jsonify(lab_snapshot.get_snapshot())

@app.get("/api/lab/model-picker")
@login_required
def api_lab_model_picker():
    return jsonify(lab_snapshot.models_for_picker())


# ---- CRUD endpoints ----
@app.get("/api/lab/providers")
@login_required
def api_lab_providers():
    return jsonify(lab_crud.list_providers())

@app.post("/api/lab/providers")
@login_required
def api_lab_providers_add():
    b = request.get_json(force=True) or {}
    return jsonify(lab_crud.add_provider(b.get("name",""), b.get("base_url",""),
                                         b.get("api_key",""), b.get("models", [])))

@app.post("/api/lab/providers/update")
@login_required
def api_lab_providers_update():
    b = request.get_json(force=True) or {}
    return jsonify(lab_crud.update_provider(b.get("name",""), b.get("base_url"),
                                            b.get("api_key"), b.get("models"), b.get("enabled")))

@app.post("/api/lab/providers/delete")
@login_required
def api_lab_providers_delete():
    b = request.get_json(force=True) or {}
    return jsonify(lab_crud.delete_provider(b.get("name","")))

@app.post("/api/lab/skill/save")
@login_required
def api_lab_skill_save():
    b = request.get_json(force=True) or {}
    return jsonify(lab_crud.update_skill(b.get("name",""), b.get("content","")))

@app.post("/api/lab/skill/delete")
@login_required
def api_lab_skill_delete():
    b = request.get_json(force=True) or {}
    return jsonify(lab_crud.delete_skill(b.get("name","")))

@app.post("/api/lab/session/delete")
@login_required
def api_lab_session_delete():
    b = request.get_json(force=True) or {}
    return jsonify(lab_crud.delete_session(b.get("id","")))


# ---- WebUI / SOUL / router ----
@app.get("/api/lab/soul")
@login_required
def api_lab_soul():
    return jsonify(lab_webui.get_soul())

@app.post("/api/lab/soul")
@login_required
def api_lab_soul_save():
    b = request.get_json(force=True) or {}
    return jsonify(lab_webui.save_soul(b.get("content", "")))

@app.post("/api/lab/session/continue")
@login_required
def api_lab_session_continue():
    b = request.get_json(force=True) or {}
    return jsonify(lab_webui.continue_session(str(b.get("id", "")), str(b.get("message", "")), str(b.get("model", "gatekey-unlimited-deepseek-v4-flash"))))

@app.get("/api/lab/router-status")
@login_required
def api_lab_router_status():
    return jsonify(lab_webui.router_status())


# ---- Provider management advanced ----
@app.post("/api/lab/providers/ping")
@login_required
def api_lab_providers_ping():
    b = request.get_json(force=True) or {}
    return jsonify(lab_provider.ping_model(b.get("base_url",""), b.get("api_key",""), b.get("model",""), provider_name=b.get("name","")))

@app.post("/api/lab/providers/rename")
@login_required
def api_lab_providers_rename():
    b = request.get_json(force=True) or {}
    return jsonify(lab_provider.rename_provider(b.get("name",""), b.get("new_name","")))

@app.get("/api/lab/full-config")
@login_required
def api_lab_full_config():
    return jsonify(lab_provider.full_config())

@app.post("/api/lab/providers/edit")
@login_required
def api_lab_providers_edit():
    b = request.get_json(force=True) or {}
    return jsonify(lab_provider.edit_provider(
        b.get("name",""), b.get("new_name"), b.get("base_url"), b.get("api_key"),
        b.get("models"), b.get("model_add"), b.get("model_remove"), b.get("enabled")))


# ---- Security / password / web / alerts ----
@app.post("/api/lab/password")
@login_required
def api_lab_password():
    b = request.get_json(force=True) or {}
    return jsonify(lab_security.change_vps_password(str(b.get("target", "")), str(b.get("new_password", "")),
                                                     str(b.get("admin_password", ""))))


@app.get("/api/lab/ssh-access")
@login_required
def api_lab_ssh_access():
    return jsonify(lab_security.ssh_access_status())


@app.post("/api/lab/ssh-key")
@login_required
def api_lab_ssh_key():
    b = request.get_json(force=True) or {}
    return jsonify(lab_security.generate_ssh_key(str(b.get("target", "")), str(b.get("admin_password", ""))))


@app.post("/api/lab/ssh-mode")
@login_required
def api_lab_ssh_mode():
    b = request.get_json(force=True) or {}
    return jsonify(lab_security.set_ssh_mode(str(b.get("mode", "")), str(b.get("admin_password", ""))))

@app.get("/api/lab/web-status")
@login_required
def api_lab_web_status():
    return jsonify(lab_security.web_status())


@app.get("/api/lab/backups")
@login_required
def api_lab_backups():
    return jsonify({"ok": True, "backups": lab_backup.list_backups()})


@app.route("/api/lab/account", methods=("GET", "POST"))
@login_required
def api_lab_account():
    if request.method == "GET":
        return jsonify(lab_account.summary())
    body = request.get_json(silent=True) or {}
    if body.get('action') == 'credentials':
        result = lab_security.change_labs_password(str(body.get('username', '')),
                                                   str(body.get('current_password', '')),
                                                   str(body.get('new_password', '')),
                                                   str(body.get('new_username', '')))
        if result.pop('restart_required', False):
            subprocess.Popen(['systemd-run', '--on-active=2s', '--unit=vps-audit-account-restart',
                              'systemctl', 'restart', 'vps-audit.service'], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    else:
        result = lab_account.set_email(str(body.get('email', '')), str(body.get('current_password', '')))
    return jsonify(result), (200 if result.get('ok') else 400)


@app.get("/api/lab/backups/<name>")
@login_required
def api_lab_backup_download(name):
    if not re.fullmatch(r"(?:9router|webui|labs)-\d{8}-\d{6}\.zip", name):
        return jsonify(ok=False, error="nama backup tidak valid"), 400
    p = lab_backup.ROOT / name
    if not p.is_file():
        return jsonify(ok=False, error="backup tidak ditemukan"), 404
    return send_file(p, as_attachment=True, download_name=name)


@app.post("/api/lab/backup/<target>")
@login_required
def api_lab_backup_create(target):
    try:
        p = lab_backup.create_backup(target)
        return jsonify(ok=True, name=p.name, size=p.stat().st_size)
    except (ValueError, OSError, sqlite3.Error) as e:
        return jsonify(ok=False, error=str(e)), 400


@app.post("/api/lab/backup/<target>/audit")
@login_required
def api_lab_backup_audit(target):
    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="file wajib dipilih"), 400
    try:
        return jsonify(lab_backup.audit_upload(f, target))
    except (ValueError, OSError, zipfile.BadZipFile, sqlite3.Error) as e:
        return jsonify(ok=False, error=str(e)), 400


@app.post("/api/lab/backup/<target>/restore")
@login_required
def api_lab_backup_restore(target):
    token = str((request.get_json(silent=True) or {}).get("token", ""))
    try:
        result = lab_backup.restore(token, target)
        if result.pop('restart_required', False):
            subprocess.Popen(['systemd-run', '--on-active=2s', '--unit=vps-audit-restore-restart',
                              'systemctl', 'restart', 'vps-audit.service'], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return jsonify(result)
    except (ValueError, OSError, zipfile.BadZipFile, sqlite3.Error) as e:
        return jsonify(ok=False, error=str(e)), 400

@app.get("/api/lab/alerts")
@login_required
def api_lab_alerts():
    current = lab_security.urgent_alerts()
    log = lab_security.notification_log(current["alerts"], lab_security.NOTIFICATION_DB)
    return jsonify(alerts=current["alerts"], count=current["count"], urgent=current["urgent"],
                   unread=log["unread"], total=log["total"])


@app.get("/api/lab/restart")
@login_required
def api_lab_restart():
    rec = lab_restart.current()
    if not rec:
        return jsonify(ok=False, recorded=False)
    return jsonify(ok=True, recorded=True, **rec)


@app.route("/api/lab/notifications", methods=("GET", "POST"))
@login_required
def api_lab_notifications():
    if request.method == "GET":
        current = lab_security.urgent_alerts()
        return jsonify(lab_security.notification_log(current["alerts"], lab_security.NOTIFICATION_DB))
    body = request.get_json(silent=True) or {}
    result = lab_security.update_notifications(body.get("action", ""), lab_security.NOTIFICATION_DB,
                                               notification_id=body.get("id"))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.get("/api/lab/core-config")
@login_required
def api_lab_core_config():
    return jsonify(lab_provider.get_core_config())

@app.post("/api/lab/core-config")
@login_required
def api_lab_core_config_save():
    b = request.get_json(force=True) or {}
    return jsonify(lab_provider.save_core_config(
        b.get("model_default"), b.get("model_provider"),
        b.get("fallback_providers"), b.get("max_tokens")))

@app.get("/api/services")
@login_required
def api_services():
    return jsonify({"services": lab_integration.list_services()})


@app.post("/api/service/action")
@login_required
def api_service_action():
    body = request.get_json(force=True) or {}
    name, action = body.get("name", ""), body.get("action", "")
    return jsonify(lab_integration.service_action(name, action))


@app.get("/api/lab/update/status")
@login_required
def api_lab_update_status():
    return jsonify(lab_update.status())


@app.get("/api/lab/update/preflight/<target>")
@login_required
def api_lab_update_preflight(target):
    result = lab_update.preflight(target)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.get("/api/lab/update/jobs")
@login_required
def api_lab_update_jobs():
    return jsonify(lab_update.job_status())


@app.get("/api/lab/update/jobs/<job_id>")
@login_required
def api_lab_update_job(job_id):
    return jsonify(lab_update.job_status(job_id))


@app.post("/api/lab/update/<target>")
@login_required
def api_lab_update_start(target):
    body = request.get_json(silent=True) or {}
    return jsonify(lab_update.start_update(target, body.get("confirmed") is True))


@app.get("/health")
def health(): return jsonify(ok=True)


if REPORT.exists():
    checks, summary = parse_report(REPORT.read_text(errors="replace"))
    last_audit.update(at=int(REPORT.stat().st_mtime), checks=checks, summary=summary)

if __name__ == "__main__":
    if not app.secret_key: raise RuntimeError("NUVULABS_SECRET is required")
    try:
        lab_restart.record_restart()
    except Exception:
        pass
    if not last_audit["checks"]: threading.Thread(target=run_audit, daemon=True).start()
    threading.Thread(target=lab_failover.scheduler_loop, kwargs={"interval": 300}, daemon=True).start()
    lab_oauth.start_refresh_loop()
    # Default tetap privat. Set NUVULABS_HOST=0.0.0.0 hanya untuk akses IP yang dibatasi firewall/VPN.
    app.run(host=os.environ.get("NUVULABS_HOST", "127.0.0.1"), port=int(os.environ.get("NUVULABS_PORT", "9118")))

