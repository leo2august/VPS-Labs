"""Hermes profile inventory, routing, and guarded text-file editor for Labs."""
import os
import json
import re
import shutil
import time
from pathlib import Path

import yaml

HERMES_ROOT = Path("/home/ubuntu/.hermes")
PROFILES_ROOT = HERMES_ROOT / "profiles"
_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_STANDARD_DIRS = ("memories", "sessions", "skills", "skins", "logs", "plans", "workspace", "cron", "home")
_DOCS = {
    "soul": ("SOUL.md", 200_000),
    "memory": ("memories/MEMORY.md", 200_000),
    "user": ("memories/USER.md", 200_000),
    "config": ("config.yaml", 500_000),
}
_SKILL_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")


def normalize_name(name):
    name = str(name or "default").strip().lower()
    if name == "default":
        return name
    if not _PROFILE_RE.fullmatch(name):
        raise ValueError("Profile tidak valid")
    return name


def profile_home(name="default"):
    name = normalize_name(name)
    path = HERMES_ROOT if name == "default" else PROFILES_ROOT / name
    try:
        path.resolve().relative_to(HERMES_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Profile di luar direktori Hermes") from exc
    if not path.is_dir():
        raise ValueError("Profile tidak ditemukan")
    return path


def _yaml(path):
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def _count_skills(home):
    root = home / "skills"
    return sum(1 for p in root.rglob("SKILL.md")) if root.is_dir() else 0


def _session_count(home):
    db = home / "state.db"
    if db.is_file():
        try:
            import sqlite3
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2) as con:
                return int(con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        except Exception:
            pass
    webui = home / "webui" / "sessions"
    return sum(1 for p in webui.glob("*.json")) if webui.is_dir() else 0


def _profile(name, home):
    cfg = _yaml(home / "config.yaml")
    model_cfg = cfg.get("model") or {}
    if isinstance(model_cfg, str):
        model, provider = model_cfg, ""
    else:
        model = str(model_cfg.get("default") or model_cfg.get("model") or "")
        provider = str(model_cfg.get("provider") or "")
    meta = _yaml(home / "profile.yaml")
    distribution = _yaml(home / "distribution.yaml")
    missing = [d for d in _STANDARD_DIRS if not (home / d).is_dir()]
    return {
        "name": name,
        "label": "Default" if name == "default" else name.replace("-", " ").replace("_", " ").title(),
        "path": str(home),
        "is_default": name == "default",
        "model": model or "auto",
        "provider": provider,
        "description": str(meta.get("description") or "").strip(),
        "description_auto": bool(meta.get("description_auto", False)),
        "skills": _count_skills(home),
        "sessions": _session_count(home),
        "has_memory": any((home / "memories" / f).is_file() for f in ("MEMORY.md", "USER.md")),
        "has_credentials": (home / ".env").is_file(),
        "distribution": distribution.get("name") or "",
        "legacy": bool(missing or (name != "default" and not (home / "profile.yaml").is_file())),
        "missing_dirs": missing,
    }


def list_profiles():
    """Normalize old/default/WebUI layout into one read-only response; never move user data."""
    rows = []
    if HERMES_ROOT.is_dir():
        rows.append(_profile("default", HERMES_ROOT))
    if PROFILES_ROOT.is_dir():
        for home in sorted(PROFILES_ROOT.iterdir(), key=lambda p: p.name.lower()):
            if not home.is_dir():
                continue
            try:
                name = normalize_name(home.name)
            except ValueError:
                continue
            # Duplicate/case-legacy directories are surfaced once, without destructive rename.
            if name != "default" and not any(p["name"] == name for p in rows):
                rows.append(_profile(name, home))
    active = "default"
    active_file = HERMES_ROOT / "active_profile"
    try:
        candidate = normalize_name(active_file.read_text().strip())
        if any(p["name"] == candidate for p in rows):
            active = candidate
    except (OSError, ValueError):
        pass
    return {"ok": True, "profiles": rows, "active": active,
            "migration": {"mode": "non-destructive", "webui_as_default": True,
                          "legacy_detected": sum(bool(p["legacy"]) for p in rows)}}


def _contained(home, relative):
    path = home / relative
    try:
        path.resolve().relative_to(home.resolve())
    except ValueError as exc:
        raise ValueError("Path di luar profile") from exc
    return path


def _skill_rows(home):
    rows = []
    root = home / "skills"
    if not root.is_dir():
        return rows
    for path in sorted(root.glob("*/*/SKILL.md"), key=lambda p: str(p).lower()):
        try:
            rel = path.relative_to(root)
            path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        skill_id = f"{rel.parts[0]}/{rel.parts[1]}"
        raw = path.read_text(encoding="utf-8", errors="replace")[:4000]
        title = rel.parts[1]
        description = ""
        match = re.search(r"^title:\s*(.+)$", raw, re.M)
        if match:
            title = match.group(1).strip().strip("\"'")
        match = re.search(r"^description:\s*(.+)$", raw, re.M)
        if match:
            description = match.group(1).strip().strip("\"'")[:180]
        rows.append({"id": skill_id, "category": rel.parts[0], "name": rel.parts[1],
                     "title": title, "description": description,
                     "chars": path.stat().st_size, "mtime": int(path.stat().st_mtime)})
    return rows


def workspace(name="default"):
    home = profile_home(name)
    docs = []
    labels = {"soul": "SOUL", "memory": "Memory", "user": "User profile", "config": "Config"}
    for kind, (relative, limit) in _DOCS.items():
        path = _contained(home, relative)
        docs.append({"kind": kind, "label": labels[kind], "path": relative,
                     "exists": path.is_file(), "chars": path.stat().st_size if path.is_file() else 0,
                     "limit": limit, "editable": True})
    return {"ok": True, "profile": normalize_name(name), "docs": docs,
            "skills": _skill_rows(home)}


def _document_path(home, kind, key=""):
    if kind == "skill":
        if not _SKILL_ID_RE.fullmatch(str(key or "")):
            raise ValueError("Skill tidak valid")
        return _contained(home, f"skills/{key}/SKILL.md"), 500_000
    if kind not in _DOCS:
        raise ValueError("Dokumen tidak valid")
    relative, limit = _DOCS[kind]
    return _contained(home, relative), limit


def get_document(name, kind, key=""):
    home = profile_home(name)
    path, limit = _document_path(home, kind, key)
    if kind == "skill" and not path.is_file():
        raise ValueError("Skill tidak ditemukan")
    content = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    return {"ok": True, "profile": normalize_name(name), "kind": kind, "key": key,
            "path": str(path.relative_to(home)), "content": content, "chars": len(content), "limit": limit}


def save_document(name, kind, content, key=""):
    home = profile_home(name)
    path, limit = _document_path(home, kind, key)
    if kind == "skill" and not path.is_file():
        raise ValueError("Skill tidak ditemukan")
    if not isinstance(content, str):
        raise ValueError("Konten harus teks")
    size = len(content.encode("utf-8"))
    if size > limit:
        raise ValueError(f"Dokumen melebihi batas {limit:,} byte")
    if kind == "config":
        try:
            parsed = yaml.safe_load(content) if content.strip() else {}
        except yaml.YAMLError as exc:
            raise ValueError(f"YAML tidak valid: {str(exc).splitlines()[0]}") from exc
        if parsed is not None and not isinstance(parsed, dict):
            raise ValueError("Config YAML harus berupa object/map")
    if kind == "skill" and content.strip() and not content.lstrip().startswith("---"):
        raise ValueError("SKILL.md harus diawali YAML frontmatter ---")
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = path.stat() if path.exists() else home.stat()
    old_mode = owner.st_mode & 0o777 if path.exists() else 0o600
    backup = ""
    if path.is_file():
        backup_root = HERMES_ROOT / "backups" / "labs-profile-editor" / normalize_name(name)
        rel = path.relative_to(home)
        backup_path = backup_root / rel.parent / f"{rel.name}.{int(time.time())}.bak"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_path)
        backup = str(backup_path)
    tmp = path.with_name(f".{path.name}.labs-{os.getpid()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.chmod(tmp, old_mode)
        os.chown(tmp, owner.st_uid, owner.st_gid)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "profile": normalize_name(name), "kind": kind, "key": key,
            "chars": len(content), "bytes": size, "backup": backup, "saved_at": int(time.time())}


def delete_document(name, kind, key=""):
    """Delete one managed profile document after copying it to Labs backups."""
    home = profile_home(name)
    path, _ = _document_path(home, kind, key)
    if not path.is_file():
        return {"ok": True, "profile": normalize_name(name), "kind": kind,
                "key": key, "deleted": False, "backup": ""}
    backup_root = HERMES_ROOT / "backups" / "labs-profile-editor" / normalize_name(name)
    rel = path.relative_to(home)
    backup_path = backup_root / rel.parent / f"{rel.name}.{int(time.time())}.deleted.bak"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    path.unlink()
    if kind == "skill":
        current = path.parent
        skills_root = home / "skills"
        while current != skills_root and current.is_relative_to(skills_root):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
    return {"ok": True, "profile": normalize_name(name), "kind": kind,
            "key": key, "deleted": True, "backup": str(backup_path)}


def self_check():
    data = list_profiles()
    assert data["ok"] and any(p["name"] == "default" for p in data["profiles"])
    assert profile_home("DEFAULT") == HERMES_ROOT
    for bad in ("../x", "/tmp/x", "A B"):
        try:
            profile_home(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    return data


if __name__ == "__main__":
    print(json.dumps(self_check(), ensure_ascii=False, indent=2))
