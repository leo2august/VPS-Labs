"""Leo2agust Lab — config viewer, gateway control, usage stats."""
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import yaml

HERMES_DIR = Path("/home/ubuntu/.hermes")
CONFIG = HERMES_DIR / "config.yaml"
WEBUI_SESSIONS_DIR = HERMES_DIR / "webui" / "sessions"
ROUTER_DB = Path("/home/ubuntu/.9router/db/data.sqlite")
STATE_DB = HERMES_DIR / "state.db"

GATEWAY_UNITS = [
    ("hermes-gateway.service", "Hermes Gateway (messaging)"),
    ("hermes-task-router.service", "Task-aware model router"),
]

_GENERIC_PROVIDERS = {"custom", "openai", "openrouter", "anthropic", "gemini", "auto"}


def _provider_index() -> dict:
    """model -> provider name, built from config.yaml custom_providers."""
    index = {}
    try:
        data = yaml.safe_load(CONFIG.read_text()) or {}
        for p in data.get("custom_providers", []) or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            name = str(p["name"])
            models = p.get("models", [])
            if not isinstance(models, list):
                models = [models] if models else []
            one = p.get("model") or p.get("default_model")
            if one and one not in models:
                models.insert(0, one)
            for m in models:
                if m:
                    index.setdefault(str(m), name)
    except (OSError, yaml.YAMLError, TypeError, ValueError):
        pass
    return index


def _provider_by_base_url(base_url: str) -> str:
    """Cari nama provider dari config yang base_url/relay-nya cocok dgn base_url runtime."""
    if not base_url:
        return ""
    b = str(base_url).rstrip("/").lower()
    try:
        data = yaml.safe_load(CONFIG.read_text()) or {}
        for p in data.get("custom_providers", []) or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            candidates = [p.get("base_url")] + (p.get("relays") or [])
            for c in candidates:
                if c and str(c).rstrip("/").lower() == b:
                    return str(p["name"])
    except (OSError, yaml.YAMLError, TypeError, ValueError):
        pass
    return ""


def _find_active_runtime() -> str:
    """Cari runtime_provider konkret dari sesi gateway aktif (dalam 5 menit).

    Prioritas: provider non-generik dari gateway_runtime, lalu cocokkan base_url
    ke custom_providers (relay Vercel milik provider tertentu).
    """
    try:
        with sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=4) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""
                SELECT s.model_config
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.ended_at IS NULL
                  AND s.source IN ('telegram', 'whatsapp', 'web', 'webui', 'labs')
                GROUP BY s.id
                HAVING COALESCE(MAX(m.timestamp), s.started_at) > ?
                ORDER BY MAX(m.timestamp) DESC
                LIMIT 6
            """, (time.time() - 300,)).fetchall()
            for r in rows:
                cfg = json.loads(r["model_config"] or "{}")
                rt = (cfg.get("gateway_runtime") or {}) if isinstance(cfg, dict) else {}
                rt = rt if isinstance(rt, dict) else {}
                prov = str(rt.get("provider") or "") if rt else ""
                if prov and prov not in _GENERIC_PROVIDERS:
                    return prov
            # 2nd pass: match by base_url (relay B.AI / Zen / dst)
            for r in rows:
                cfg = json.loads(r["model_config"] or "{}")
                rt = (cfg.get("gateway_runtime") or {}) if isinstance(cfg, dict) else {}
                rt = rt if isinstance(rt, dict) else {}
                b = str(rt.get("base_url") or "") if rt else ""
                if b:
                    name = _provider_by_base_url(b)
                    if name:
                        return name
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass
    return ""


def _resolve_provider(model: str, runtime_provider: str, billing_provider: str, configured_provider: str = "", base_url: str = "") -> str:
    """Effective provider for a session route, newest evidence first.

    Prioritas: base_url sesi (relay → provider di config) > runtime_provider
    spesifik > billing_provider spesifik > runtime sesi lain > config model.
    """
    # 1. base_url sesi itu sendiri — paling akurat (relay B.AI/Zen/relay milik provider X)
    if base_url:
        by_url = _provider_by_base_url(base_url)
        if by_url:
            return by_url
    # 2. runtime_provider spesifik (bukan generic)
    if runtime_provider and str(runtime_provider) not in _GENERIC_PROVIDERS:
        return str(runtime_provider)
    # 3. billing_provider spesifik
    if billing_provider and str(billing_provider) not in _GENERIC_PROVIDERS:
        return str(billing_provider)
    # 4. Cari di sesi gateway lain yang aktif (mungkin ada runtime_provider nyata)
    runtime_provider = _find_active_runtime()
    if runtime_provider:
        return runtime_provider
    # 5. Fallback: config model→provider mapping (bisa kurang akurat)
    idx = _provider_index()
    if model and model in idx:
        return idx[model]
    if configured_provider:
        return str(configured_provider)
    return runtime_provider or billing_provider or "—"


# ---- Config ----
def _current_runtime_model() -> dict | None:
    """Latest active messaging session route from Hermes state.db."""
    try:
        with sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=4) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("""
                SELECT s.id, s.source, s.display_name, s.model, s.model_config,
                       s.billing_provider, s.billing_base_url,
                       COALESCE(MAX(m.timestamp), s.started_at) AS last_active
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.ended_at IS NULL
                  AND s.source IN ('telegram', 'whatsapp', 'web', 'webui', 'labs')
                GROUP BY s.id
                ORDER BY last_active DESC
                LIMIT 1
            """).fetchone()
        if not row or time.time() - float(row["last_active"] or 0) > 300:
            return None
        cfg = json.loads(row["model_config"] or "{}")
        runtime = cfg.get("gateway_runtime") if isinstance(cfg, dict) else {}
        runtime = runtime if isinstance(runtime, dict) else {}
        model = row["model"] or "—"
        runtime_provider = runtime.get("provider") or ""
        billing_provider = row["billing_provider"] or ""
        return {
            "model": model,
            "provider": _resolve_provider(model, runtime_provider, billing_provider, base_url=runtime.get("base_url") or row["billing_base_url"] or ""),
            "provider_raw": runtime_provider or billing_provider or "—",
            "base_url": runtime.get("base_url") or row["billing_base_url"] or "",
            "source": row["source"],
            "display_name": row["display_name"] or "",
            "session_id": row["id"],
            "last_active": row["last_active"],
        }
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None


def gateway_routes() -> dict:
    """Live route per messaging gateway plus configured route for next request."""
    try:
        data = yaml.safe_load(CONFIG.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError("config.yaml bukan object")
        platforms = data.get("platforms") if isinstance(data.get("platforms"), dict) else {}
        providers = []
        for p in data.get("custom_providers", []) or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            models = p.get("models", [])
            if not isinstance(models, list):
                models = [models] if models else []
            one = p.get("model") or p.get("default_model")
            if one and one not in models:
                models.insert(0, one)
            providers.append({"name": str(p["name"]), "models": [str(x) for x in models if x]})
        now = time.time()
        with sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=4) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""
                SELECT s.id,s.source,s.display_name,s.model,s.model_config,
                       s.billing_provider,s.billing_base_url,
                       COALESCE(MAX(m.timestamp),s.started_at) last_active
                FROM sessions s LEFT JOIN messages m ON m.session_id=s.id
                WHERE s.ended_at IS NULL AND s.source IN ('telegram','whatsapp','web','webui','labs')
                GROUP BY s.id ORDER BY last_active DESC
            """).fetchall()
        routes = []
        seen = set()
        for row in rows:
            source = "labs" if row["source"] in ("web", "webui", "labs") else row["source"]
            if source in seen:
                continue
            seen.add(source)
            cfg = json.loads(row["model_config"] or "{}")
            runtime = cfg.get("gateway_runtime") if isinstance(cfg, dict) else {}
            runtime = runtime if isinstance(runtime, dict) else {}
            pcfg = platforms.get(source) if isinstance(platforms.get(source), dict) else {}
            model = row["model"] or "—"
            runtime_provider = runtime.get("provider") or ""
            billing_provider = row["billing_provider"] or ""
            configured_provider = (pcfg.get("provider") or "").replace("custom:", "")
            routes.append({
                "source": source, "label": {"telegram":"Telegram","whatsapp":"WhatsApp","labs":"Labs Web"}.get(source, source.title()),
                "session_id": row["id"], "display_name": row["display_name"] or "Sesi gateway",
                "model": model,
                "provider": _resolve_provider(model, runtime_provider, billing_provider, configured_provider, base_url=runtime.get("base_url") or row["billing_base_url"] or ""),
                "provider_raw": runtime_provider or billing_provider or "—",
                "base_url": runtime.get("base_url") or row["billing_base_url"] or "",
                "last_active": row["last_active"], "online": bool(row["last_active"] and now-float(row["last_active"]) < 180),
                "configured_provider": configured_provider, "configured_model": pcfg.get("model") or "",
            })
        # Pastikan Telegram & WhatsApp selalu muncul di daftar (walau tanpa sesi aktif)
        for src in ("telegram", "whatsapp"):
            if src not in seen:
                pcfg = platforms.get(src) if isinstance(platforms.get(src), dict) else {}
                configured_provider = (pcfg.get("provider") or "").replace("custom:", "")
                routes.append({
                    "source": src, "label": {"telegram":"Telegram","whatsapp":"WhatsApp"}.get(src, src.title()),
                    "session_id": "", "display_name": "Belum ada sesi",
                    "model": pcfg.get("model") or "—",
                    "provider": configured_provider or "—",
                    "provider_raw": configured_provider or "—",
                    "base_url": "",
                    "last_active": 0, "online": False,
                    "configured_provider": configured_provider, "configured_model": pcfg.get("model") or "",
                })
                seen.add(src)
        return {"ok": True, "routes": routes, "providers": providers, "updated_at": now}
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)[:200]}


def update_gateway_route(source: str, provider: str, model: str) -> dict:
    """Set platform route. Runtime session reflects it after its next request."""
    source, provider, model = str(source).strip(), str(provider).strip(), str(model).strip()
    if source not in {"telegram", "whatsapp"}:
        return {"ok": False, "error": "Gateway hanya Telegram atau WhatsApp"}
    try:
        data = yaml.safe_load(CONFIG.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError("config.yaml bukan object")
        provider_rows = {str(p.get("name")): p for p in data.get("custom_providers", []) if isinstance(p, dict)}
        canonical = provider[7:] if provider.startswith("custom:") else provider
        if canonical not in provider_rows:
            return {"ok": False, "error": "Provider tidak ditemukan"}
        row = provider_rows[canonical]
        allowed = row.get("models", [])
        if not isinstance(allowed, list):
            allowed = [allowed] if allowed else []
        single = row.get("model") or row.get("default_model")
        if single and single not in allowed:
            allowed.insert(0, single)
        allowed = [str(x) for x in allowed if x]
        if model and model not in allowed:
            return {"ok": False, "error": "Model tidak tersedia pada provider terpilih"}
        if not model and allowed:
            model = allowed[0]
        route_provider = "custom:" + canonical
        data.setdefault("platforms", {}).setdefault(source, {})
        platform = data["platforms"][source]
        platform["provider"] = route_provider
        if model:
            platform["model"] = model
        # Gateway route means every channel on that gateway. Existing per-channel
        # overrides otherwise win and make a successful save appear ineffective.
        if source == "whatsapp" and isinstance(platform.get("channel_overrides"), dict):
            for override in platform["channel_overrides"].values():
                if isinstance(override, dict):
                    override["provider"] = route_provider
                    if model:
                        override["model"] = model
        attr = subprocess.run(["lsattr", str(CONFIG)], capture_output=True, text=True, timeout=5)
        was_immutable = bool(attr.stdout and "i" in attr.stdout.split()[0])
        subprocess.run(["chattr", "-i", str(CONFIG)], capture_output=True, timeout=5)
        try:
            tmp = CONFIG.with_suffix(".yaml.tmp")
            tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
            os.replace(tmp, CONFIG)
        finally:
            # Preserve existing policy. Labs must not silently re-lock an editable config.
            if was_immutable:
                subprocess.run(["chattr", "+i", str(CONFIG)], capture_output=True, timeout=5)
        check = yaml.safe_load(CONFIG.read_text()) or {}
        if not isinstance(check, dict):
            raise ValueError("hasil config bukan object")
        saved = check.get("platforms", {}).get(source, {})
        if saved.get("provider") != route_provider or (model and saved.get("model") != model):
            return {"ok": False, "error": "Verifikasi config gagal"}
        # Restart gateway asinkron: jangan blokir POST (restart bisa >45s dan
        # memicu TimeoutExpired -> HTTP 500 -> browser "Unexpected token").
        threading.Thread(target=_restart_gateway_async, args=(source,), daemon=True).start()
        return {"ok": True, "source": source, "provider": route_provider, "model": saved.get("model"),
                "impact": "Config terverifikasi. Gateway sedang direstart; request berikutnya memakai rute baru."}
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": "Penyimpanan config memakan waktu terlalu lama: " + str(exc)[:160]}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _restart_gateway_async(source):
    """Restart hanya gateway/platform yang dimaksud, aman dari timeout & sandbox.
    - source == 'whatsapp' → restart bridge WhatsApp (child process). Telegram TIDAK ikut
      terganggu; gateway mem-bridge ulang bridge.js otomatis.
    - source == 'telegram' (atau lainnya) → restart gateway penuh (satu process utk Telegram)."""
    # WhatsApp bridge is a child process without an independent supervisor.
    # Killing bridge.js leaves gateway in `retrying`; restart gateway for every route change.
    cmd = ["sudo", "su", "-", "ubuntu", "-c",
           "XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart hermes-gateway.service"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "restart gagal")[:200])
    except Exception as exc:
        print(f"Gateway restart gagal setelah ganti rute {source}: {exc}", flush=True)


def config_summary() -> dict:
    """Read config.yaml safely (never expose secrets verbatim)."""
    try:
        data = yaml.safe_load(CONFIG.read_text()) or {}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    providers = []
    for p in data.get("custom_providers", []) or []:
        if not isinstance(p, dict):
            continue
        name = p.get("name", "?")
        base = p.get("base_url", "")
        models = p.get("models", [])
        if not isinstance(models, list):
            models = [str(models)] if models else []
        single_model = p.get("model") or p.get("default_model")
        if single_model and single_model not in models:
            models.insert(0, str(single_model))
        # redact api_key
        has_key = bool(p.get("api_key") or p.get("apiKey"))
        providers.append({
            "name": name,
            "base_url": str(base)[:70],
            "models": models,
            "default_model": str(single_model or (models[0] if models else "")),
            "has_key": has_key,
            "enabled": p.get("enabled", True),
        })
    return {
        "ok": True,
        "model": data.get("model"),
        "runtime_model": _current_runtime_model(),
        "providers": providers,
        "provider_count": len(providers),
        "auxiliary": data.get("auxiliary"),
        "max_tokens": data.get("max_tokens"),
        "config_version": data.get("_config_version"),
        "raw_lines": CONFIG.read_text(errors="replace").count("\n") + 1,
    }


GATEWAY_UNITS = [
    ("hermes-gateway.service", "Hermes Gateway (messaging)", ["hermes", "gateway"]),
    ("hermes-task-router.service", "Task-aware model router", ["task-router", "router.py"]),
]


def _pgrep_state(patterns) -> str:
    """Fallback: check if a matching process is alive (works without user bus)."""
    try:
        r = subprocess.run(["pgrep", "-f", "|".join(patterns)], capture_output=True, text=True, timeout=8)
        return "active" if r.stdout.strip() else "inactive"
    except Exception:
        return "unknown"


# ---- Gateway control ----
def gateway_status() -> dict:
    out = []
    for unit, label, pat in GATEWAY_UNITS:
        try:
            r = subprocess.run(["systemctl", "--user", "is-active", unit],
                               capture_output=True, text=True, timeout=8)
            state = r.stdout.strip()
            if state not in ("active", "inactive", "failed"):
                state = _pgrep_state(pat)
            mem = ""
            try:
                rr = subprocess.run(["systemctl", "--user", "show", unit, "--property=MemoryCurrent"],
                                    capture_output=True, text=True, timeout=8)
                for line in rr.stdout.splitlines():
                    if "MemoryCurrent=" in line:
                        try:
                            mem = _fmt_bytes(int(line.split("=")[1]))
                        except Exception:
                            pass
            except Exception:
                pass
            out.append({"unit": unit, "label": label, "state": state, "memory": mem})
        except Exception as e:
            out.append({"unit": unit, "label": label, "state": "error", "memory": "", "err": str(e)[:80]})
    # ---- platform status ----
    platforms = []
    # WhatsApp bridge (node process)
    try:
        r = subprocess.run(["pgrep", "-f", "whatsapp-bridge"], capture_output=True, text=True, timeout=6)
        wa = "active" if r.stdout.strip() else "inactive"
    except Exception:
        wa = "unknown"
    platforms.append({"name": "whatsapp", "label": "WhatsApp", "state": wa,
                      "detail": "bridge.js" if wa == "active" else "bridge off"})
    # Telegram — gateway process is up; check log for telegram ready marker
    try:
        r = subprocess.run(["sudo", "su", "-", "ubuntu", "-c",
                            "journalctl --user -u hermes-gateway.service -n 300 --no-pager 2>/dev/null | grep -icE 'telegram'"],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12, check=False)
        tg_hits = int((r.stdout or "0").strip() or "0")
    except Exception:
        tg_hits = 0
    gw_state = next((g["state"] for g in out if "gateway" in g["unit"]), "inactive")
    tg = "active" if gw_state == "active" and tg_hits > 0 else ("active" if gw_state == "active" else "inactive")
    platforms.append({"name": "telegram", "label": "Telegram", "state": tg,
                      "detail": f"{tg_hits} log hits" if tg_hits else "via gateway"})
    return {"gateways": out, "platforms": platforms}


def _fmt_bytes(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def gateway_action(unit: str, action: str) -> dict:
    if unit not in [u[0] for u in GATEWAY_UNITS]:
        return {"ok": False, "error": "unit tidak valid"}
    if action not in ("restart", "stop", "start"):
        return {"ok": False, "error": "action tidak valid"}
    try:
        shell = f"XDG_RUNTIME_DIR=/run/user/1000 systemctl --user {action} {unit}"
        r = subprocess.run(["sudo", "su", "-", "ubuntu", "-c", shell],
                           capture_output=True, text=True, timeout=120)
        time.sleep(2)
        check = f"XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active {unit}"
        st = subprocess.run(["sudo", "su", "-", "ubuntu", "-c", check],
                            capture_output=True, text=True, timeout=15).stdout.strip()
        return {"ok": r.returncode == 0, "unit": unit, "action": action,
                "state_after": st, "message": (r.stderr or r.stdout).strip()[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def gateway_logs(lines: int = 60) -> dict:
    try:
        r = subprocess.run(["sudo", "-u", "ubuntu", "journalctl", "--user", "-u", "hermes-gateway.service", "-n", str(lines),
                            "--no-pager", "-o", "short-iso"],
                           capture_output=True, text=True, timeout=15)
        text = r.stdout
        # redact obvious secrets
        text = re.sub(r'(Bearer |sk-|gk_live_|9r_live_|vcp_)[A-Za-z0-9_\-\.]+', r'\1***', text)
        return {"ok": True, "logs": text[-8000:]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ---- Usage stats (from webui session store) ----
def usage_stats(period_days=30) -> dict:
    period_days = period_days if period_days in (1, 7, 30) else 30
    cutoff = time.time() - period_days * 86400
    total_sessions = 0
    total_messages = 0
    by_source = {}
    by_date = {}
    if WEBUI_SESSIONS_DIR.is_dir():
        for f in WEBUI_SESSIONS_DIR.glob("*.json"):
            if f.name == "_index.json":
                continue
            try:
                data = json.loads(f.read_text(errors="replace"))
            except Exception:
                continue
            msgs = data.get("messages") or []
            n = len(msgs)
            if n == 0:
                continue
            total_sessions += 1
            total_messages += n
            src = data.get("source") or data.get("platform") or "webui"
            by_source[src] = by_source.get(src, 0) + 1
            for m in msgs:
                ts = m.get("timestamp", 0)
                if isinstance(ts, (int, float)) and ts > 0:
                    day = time.strftime("%Y-%m-%d", time.localtime(ts))
                    by_date[day] = by_date.get(day, 0) + 1
    # last 14 days
    days = sorted(by_date.keys())[-14:]
    router = {"total_tokens": 0, "total_requests": 0, "providers": []}
    try:
        with sqlite3.connect(f"file:{ROUTER_DB}?mode=ro", uri=True, timeout=4) as db:
            aliases = {}
            for pid, name in db.execute("SELECT id, name FROM providerNodes"):
                aliases[pid] = name or pid
            rows = db.execute("""
                SELECT COALESCE(NULLIF(provider, ''), 'Unknown'), COUNT(*),
                       COALESCE(SUM(promptTokens), 0), COALESCE(SUM(completionTokens), 0),
                       COUNT(DISTINCT model)
                FROM usageHistory WHERE datetime(timestamp) >= datetime('now', ?) GROUP BY 1
                ORDER BY COALESCE(SUM(promptTokens), 0) + COALESCE(SUM(completionTokens), 0) DESC
            """, (f"-{period_days} days",)).fetchall()
            for provider, requests, prompt, completion, models in rows:
                router["providers"].append({
                    "provider": provider, "name": aliases.get(provider, provider),
                    "requests": requests, "prompt_tokens": prompt,
                    "completion_tokens": completion, "total_tokens": prompt + completion,
                    "models": models,
                })
            router["total_tokens"] = sum(x["total_tokens"] for x in router["providers"])
            router["total_requests"] = sum(x["requests"] for x in router["providers"])
    except (OSError, sqlite3.Error) as e:
        router["error"] = str(e)[:160]

    # Hermes ledger covers direct providers outside 9router (GateKey, B.AI,
    # TabiAI, GoRouter, Tamandata, etc.). Counters are recorded by Hermes,
    # never estimated from message text.
    hermes = {"total_tokens": 0, "total_requests": 0, "providers": []}
    try:
        with sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=4) as db:
            rows = db.execute("""
                SELECT COALESCE(NULLIF(billing_provider, ''), 'Unknown'),
                       COALESCE(NULLIF(billing_base_url, ''), 'unknown'),
                       COALESCE(SUM(api_call_count), 0),
                       COALESCE(SUM(input_tokens), 0),
                       COALESCE(SUM(output_tokens), 0),
                       COALESCE(SUM(cache_read_tokens), 0),
                       COUNT(DISTINCT model)
                FROM session_model_usage WHERE last_seen >= ?
                GROUP BY 1, 2
                ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
            """, (cutoff,)).fetchall()
        grouped = {}
        for provider, base_url, requests, prompt, completion, cache, models in rows:
            raw = provider.removeprefix("custom:")
            low, url = raw.lower(), base_url.lower()
            if "gatekey" in low or "gatekey" in url:
                key, name = "gatekey", "GateKey"
            elif low.startswith("b.ai") or "relay-1787577782" in url:
                key, name = "b.ai", "B.AI"
            elif "tabi" in low:
                key, name = "tabiai", "TabiAI"
            elif "gorouter" in low:
                key, name = "gorouter", "GoRouter"
            elif "tamandata" in low or "tamandata" in url:
                key, name = "tamandata", "Tamandata"
            elif low == "custom" and "127.0.0.1:20128" in url:
                key, name = "local-router", "Local Router (Tabi/GoRouter)"
            elif low == "custom" and "127.0.0.1:20129" in url:
                key, name = "taskrouter", "TaskRouter"
            else:
                key, name = low, raw
            item = grouped.setdefault(key, {"provider": key, "name": name,
                "requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "cache_tokens": 0, "total_tokens": 0, "models": 0})
            item["requests"] += requests
            item["prompt_tokens"] += prompt
            item["completion_tokens"] += completion
            item["cache_tokens"] += cache
            item["total_tokens"] += prompt + completion
            item["models"] += models
        hermes["providers"] = sorted(grouped.values(), key=lambda x: -x["total_tokens"])
        hermes["total_tokens"] = sum(x["total_tokens"] for x in hermes["providers"])
        hermes["total_requests"] = sum(x["requests"] for x in hermes["providers"])
    except (OSError, sqlite3.Error) as e:
        hermes["error"] = str(e)[:160]
    return {
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "by_source": dict(sorted(by_source.items(), key=lambda x: -x[1])),
        "recent_days": [{"date": d, "messages": by_date.get(d, 0)} for d in days],
        "router": router,
        "hermes": hermes,
        "period_days": period_days,
    }


def usage_report_pdf(data):
    """Branded, multi-page Labs usage report rendered with Pillow."""
    from datetime import datetime, timedelta, timezone
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont

    period = data.get("period_days", 30)
    hermes, router = data.get("hermes", {}), data.get("router", {})
    providers = hermes.get("providers", [])
    W, H, margin = 1240, 1754, 84
    navy, blue, red, ink, muted, paper, white = "#101d36", "#356db4", "#e34b4b", "#14213d", "#667085", "#f2f4f7", "#ffffff"
    regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font = lambda size, heavy=False: ImageFont.truetype(bold if heavy else regular, size)
    pages = []

    def new_page(number):
        im = Image.new("RGB", (W, H), paper); d = ImageDraw.Draw(im)
        d.rectangle((0, 0, W, 230), fill=navy); d.rectangle((0, 0, 18, H), fill=red)
        d.text((margin, 58), "LEO2AGUST LABS", fill=white, font=font(28, True))
        d.text((margin, 108), "AI Usage Report", fill=white, font=font(54, True))
        d.text((margin, 177), f"{period} hari  •  {datetime.now(timezone(timedelta(hours=7))):%d %b %Y, %H:%M WIB}", fill="#b8c8e4", font=font(21))
        d.text((W-margin-120, H-54), f"PAGE {number:02d}", fill=muted, font=font(17, True))
        return im, d

    def card(d, box, label, value, accent=blue):
        d.rounded_rectangle(box, 22, fill=white, outline="#dde4ec", width=2)
        x, y, _, _ = box; d.rectangle((x, y, x+9, box[3]), fill=accent)
        d.text((x+30, y+28), label.upper(), fill=muted, font=font(17, True))
        d.text((x+30, y+73), f"{value:,}", fill=ink, font=font(33, True))

    im, d = new_page(1)
    y, gap, cw = 285, 22, (W-2*margin-22)//2
    card(d, (margin, y, margin+cw, y+150), "Total token Hermes", hermes.get("total_tokens", 0), red)
    card(d, (margin+cw+gap, y, W-margin, y+150), "Total request Hermes", hermes.get("total_requests", 0), blue)
    y += 185
    card(d, (margin, y, margin+cw, y+150), "Token 9router", router.get("total_tokens", 0), "#29966f")
    card(d, (margin+cw+gap, y, W-margin, y+150), "Request 9router", router.get("total_requests", 0), "#d29336")
    y += 205
    d.text((margin, y), "Provider breakdown", fill=ink, font=font(30, True)); y += 58
    max_token = max([p.get("total_tokens", 0) for p in providers] or [1])
    for row in providers[:8]:
        total = row.get("total_tokens", 0); name = str(row.get("name", "Unknown"))[:32]
        d.rounded_rectangle((margin, y, W-margin, y+112), 16, fill=white, outline="#e0e6ed")
        d.text((margin+24, y+18), name, fill=ink, font=font(22, True))
        d.text((W-margin-280, y+18), f"{total:,} token", fill=blue, font=font(20, True))
        d.rounded_rectangle((margin+24, y+65, W-margin-24, y+82), 8, fill="#e8edf3")
        d.rounded_rectangle((margin+24, y+65, margin+24+int((W-2*margin-48)*total/max_token), y+82), 8, fill=red)
        d.text((margin+24, y+88), f"{row.get('requests', 0):,} request  •  {row.get('models', 0)} model", fill=muted, font=font(15))
        y += 126
    pages.append(im)

    remaining = providers[8:]
    while remaining:
        im, d = new_page(len(pages)+1); y = 285
        d.text((margin, y), "Provider details", fill=ink, font=font(30, True)); y += 65
        for row in remaining[:7]:
            d.rounded_rectangle((margin, y, W-margin, y+170), 18, fill=white, outline="#dde4ec", width=2)
            d.text((margin+26, y+20), str(row.get("name", "Unknown"))[:42], fill=ink, font=font(24, True))
            d.text((margin+26, y+66), f"TOTAL  {row.get('total_tokens', 0):,}", fill=red, font=font(20, True))
            stats = [("PROMPT", row.get("prompt_tokens", 0)), ("OUTPUT", row.get("completion_tokens", 0)),
                     ("CACHE", row.get("cache_tokens", 0)), ("REQUEST", row.get("requests", 0)), ("MODEL", row.get("models", 0))]
            sx = margin+26
            for label, value in stats:
                d.text((sx, y+111), label, fill=muted, font=font(13, True)); d.text((sx, y+134), f"{value:,}", fill=ink, font=font(17, True)); sx += 205
            y += 190
        pages.append(im); remaining = remaining[7:]

    d = ImageDraw.Draw(pages[-1]); d.text((margin, H-108), "Catatan: ledger Hermes dan 9router ditampilkan terpisah untuk mencegah hitung ganda.", fill=muted, font=font(16))
    out = BytesIO(); pages[0].save(out, format="PDF", save_all=True, append_images=pages[1:], resolution=150.0)
    return out.getvalue()
