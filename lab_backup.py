#!/usr/bin/env python3
import hashlib, json, os, shutil, sqlite3, subprocess, tempfile, time, zipfile
from pathlib import Path, PurePosixPath

ROOT = Path('/home/ubuntu/vps-audit/backups')
IMPORTS = ROOT / 'imports'
ROUTER = Path('/home/ubuntu/.9router')
HERMES = Path('/home/ubuntu/.hermes')
LABS = Path('/home/ubuntu/vps-audit')
EXCLUDES = {
    '9router': {'bin', 'runtime', 'logs', 'test-write'},
    # WebUI backup is the complete Hermes data/profile backup. Exclude only
    # installed code and regenerable runtime/cache trees.
    'webui': {'hermes-agent', 'bin', 'cache', 'logs', 'lsp', 'audio_cache',
              'image_cache', 'backups'},
    'labs': {'.git', '__pycache__', 'backups'},
}
TARGETS = {
    '9router': {'root': ROUTER, 'required': {'db/data.sqlite'}, 'service': '9router.service'},
    'webui': {'root': HERMES, 'required': {'webui/settings.json', 'config.yaml', 'state.db'}, 'service': 'hermes-webui.service'},
    'labs': {'root': LABS, 'required': {'app.py', 'templates/index.html', 'static/redesign.css'}, 'service': 'vps-audit.service'},
}
MAX_UPLOAD = 5 * 1024 * 1024 * 1024


def _run(*args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120, check=False)


def _active(service):
    return _run('systemctl', 'is-active', service).stdout.strip() == 'active'


def _safe_name(name):
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and '..' not in p.parts and '\\' not in name


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _manifest(z):
    try:
        raw = z.read('manifest.json')
        if len(raw) > 64 * 1024:
            raise ValueError('manifest terlalu besar')
        m = json.loads(raw)
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError('manifest.json tidak valid') from e
    if m.get('format') != 'vps-labs-backup-v1' or m.get('target') not in TARGETS:
        raise ValueError('jenis backup tidak dikenali')
    return m


def create_backup(target):
    if target not in TARGETS:
        raise ValueError('target tidak valid')
    ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    final = ROOT / f'{target}-{stamp}.zip'
    tmp = final.with_suffix('.tmp')
    files = []
    with tempfile.TemporaryDirectory(dir=ROOT) as td:
        stage = Path(td)
        db = stage / 'db/data.sqlite'
        hermes_db = stage / 'state.db'
        if target == '9router':
            db.parent.mkdir(parents=True)
            src = sqlite3.connect(f'file:{ROUTER / "db/data.sqlite"}?mode=ro', uri=True)
            dst = sqlite3.connect(db)
            try:
                src.backup(dst)
                # Rebuild copied indexes: live 9router writes can leave stale index pages.
                dst.execute('REINDEX')
                dst.commit()
            finally:
                dst.close(); src.close()
            candidates = [p.name for p in ROUTER.iterdir()
                          if p.name not in EXCLUDES['9router'] and p.name != 'db']
            candidates.append('db')
        else:
            source_root = TARGETS[target]['root']
            candidates = [p.name for p in source_root.iterdir()
                          if p.name not in EXCLUDES[target]] if source_root.exists() else []
            if target == 'webui':
                # Snapshot live SQLite atomically; never archive a mismatched DB/WAL pair.
                src = sqlite3.connect(f'file:{source_root / "state.db"}?mode=ro', uri=True)
                dst = sqlite3.connect(hermes_db)
                try:
                    src.backup(dst)
                finally:
                    dst.close(); src.close()
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
            if target == '9router':
                z.write(db, 'db/data.sqlite'); files.append('db/data.sqlite')
            elif target == 'webui':
                z.write(hermes_db, 'state.db'); files.append('state.db')
            for rel in candidates:
                src = TARGETS[target]['root'] / rel
                if not src.exists():
                    continue
                if target == 'webui' and rel in {'state.db', 'state.db-wal', 'state.db-shm'}:
                    continue
                if src.is_file():
                    z.write(src, rel); files.append(rel)
                else:
                    for p in src.rglob('*'):
                        if p.is_file() and not p.is_symlink():
                            arc = p.relative_to(TARGETS[target]['root']).as_posix()
                            if target == '9router' and arc == 'db/data.sqlite':
                                continue
                            z.write(p, arc); files.append(arc)
            manifest = {'format':'vps-labs-backup-v1','target':target,'created_at':int(time.time()),'files':len(files),'required':sorted(TARGETS[target]['required'])}
            if target == 'labs':
                manifest['username'] = os.environ.get('LABS_USER', '')
                manifest['excludes'] = ['password', 'session secret', 'SMTP password']
            z.writestr('manifest.json', json.dumps(manifest, separators=(',',':')))
    os.replace(tmp, final)
    return final


def audit_upload(file_storage, expected):
    if expected not in TARGETS:
        raise ValueError('target tidak valid')
    IMPORTS.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    path = IMPORTS / f'{token}.zip'
    file_storage.save(path)
    if path.stat().st_size > MAX_UPLOAD:
        path.unlink(missing_ok=True); raise ValueError('file melebihi 5 GB')
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > 100000 or any(not _safe_name(i.filename) for i in infos):
                raise ValueError('struktur arsip tidak aman')
            m = _manifest(z)
            if m['target'] != expected:
                raise ValueError(f'backup {m["target"]} tidak boleh masuk ke {expected}')
            names = {i.filename.rstrip('/') for i in infos}
            missing = TARGETS[expected]['required'] - names
            if missing:
                raise ValueError('file wajib hilang: ' + ', '.join(sorted(missing)))
            total = sum(i.file_size for i in infos)
            if total > 20 * 1024 * 1024 * 1024:
                raise ValueError('isi arsip terlalu besar')
            if expected == '9router':
                with tempfile.TemporaryDirectory() as td:
                    db = Path(td) / 'data.sqlite'; db.write_bytes(z.read('db/data.sqlite'))
                    con = sqlite3.connect(db); ok = con.execute('PRAGMA integrity_check').fetchone()[0]; con.close()
                    if ok != 'ok': raise ValueError('database 9router rusak')
        return {'ok':True,'token':token,'target':expected,'name':file_storage.filename or path.name,'size':path.stat().st_size,'files':len(infos)-1,'sha256':_sha(path),'created_at':m.get('created_at')}
    except Exception:
        path.unlink(missing_ok=True); raise


def restore(token, target):
    if target not in TARGETS or not token.isalnum():
        raise ValueError('permintaan tidak valid')
    archive = IMPORTS / f'{token}.zip'
    if not archive.exists():
        raise ValueError('hasil audit tidak ditemukan atau kedaluwarsa')
    cfg = TARGETS[target]; root = cfg['root']; service = cfg['service']; was_active = _active(service)
    safety = create_backup(target)
    with tempfile.TemporaryDirectory(dir=ROOT) as td:
        stage = Path(td) / 'payload'; stage.mkdir()
        with zipfile.ZipFile(archive) as z:
            m = _manifest(z)
            if m['target'] != target: raise ValueError('jenis backup berubah')
            for info in z.infolist():
                if info.filename != 'manifest.json': z.extract(info, stage)
        if target != 'labs':
            _run('systemctl', 'stop', service)
        try:
            if target == '9router':
                root.mkdir(parents=True, exist_ok=True)
                for dst in root.iterdir():
                    if dst.name in EXCLUDES['9router']: continue
                    shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
                for src in stage.iterdir():
                    os.replace(src, root / src.name)
                db_dst = root / 'db/data.sqlite'
                for suffix in ('-wal','-shm'): Path(str(db_dst)+suffix).unlink(missing_ok=True)
            elif target == 'webui':
                # Root is ~/.hermes: replace every backed-up profile/data tree,
                # but preserve installed code and regenerable excluded trees.
                root.mkdir(parents=True, exist_ok=True)
                for dst in root.iterdir():
                    if dst.name in EXCLUDES['webui']: continue
                    shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
                for src in stage.iterdir():
                    os.replace(src, root / src.name)
                db_dst = root / 'state.db'
                for suffix in ('-wal', '-shm'):
                    Path(str(db_dst) + suffix).unlink(missing_ok=True)
            else:
                for dst in root.iterdir():
                    if dst.name in EXCLUDES['labs']: continue
                    shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
                for src in stage.iterdir():
                    dst = root / src.name
                    os.replace(src, dst)
            _run('chown','-R','ubuntu:ubuntu',str(root))
        finally:
            if was_active and target != 'labs': _run('systemctl','start',service)
    archive.unlink(missing_ok=True)
    return {'ok':True,'target':target,'service':'active' if was_active else 'tetap nonaktif',
            'safety_backup':safety.name, 'restart_required': target == 'labs'}


def list_backups():
    ROOT.mkdir(parents=True, exist_ok=True)
    out=[]
    for p in sorted(ROOT.glob('*.zip'), key=lambda x:x.stat().st_mtime, reverse=True)[:20]:
        if p.parent == IMPORTS: continue
        target=p.name.split('-',1)[0]
        if target in TARGETS: out.append({'name':p.name,'target':target,'size':p.stat().st_size,'at':int(p.stat().st_mtime)})
    return out
