import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import lab_backup


class Upload:
    def __init__(self, path):
        self.path = Path(path)
        self.filename = self.path.name

    def save(self, dst):
        Path(dst).write_bytes(self.path.read_bytes())


def tree(root, ignored=(), skip=()):
    ignored = set(ignored)
    skip = set(skip)
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob('*')
        if p.is_file() and p.relative_to(root).parts[0] not in ignored
        and p.relative_to(root).as_posix() not in skip
    }


class BackupRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.roots = {
            '9router': base / 'router',
            'webui': base / 'webui',
            'labs': base / 'labs',
        }
        for root in self.roots.values():
            root.mkdir(parents=True)
        self.backups = base / 'backups'
        self.patches = [
            patch.object(lab_backup, 'ROOT', self.backups),
            patch.object(lab_backup, 'IMPORTS', self.backups / 'imports'),
            patch.object(lab_backup, 'ROUTER', self.roots['9router']),
            patch.object(lab_backup, 'HERMES', self.roots['webui']),
            patch.object(lab_backup, 'LABS', self.roots['labs']),
            patch.object(lab_backup, '_active', return_value=False),
            patch.object(lab_backup, '_run'),
        ]
        for p in self.patches: p.start()
        for target, root in self.roots.items():
            lab_backup.TARGETS[target]['root'] = root

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def seed(self, target):
        root = self.roots[target]
        if target == '9router':
            db = root / 'db/data.sqlite'; db.parent.mkdir()
            con = sqlite3.connect(db)
            con.execute('create table accounts(id integer primary key, name text)')
            con.execute("insert into accounts(name) values ('alpha')")
            con.commit(); con.close()
            (root / 'jwt-secret').write_text('jwt')
            (root / 'machine-id').write_text('machine')
            (root / 'auth').mkdir(); (root / 'auth/token.json').write_text('{"x":1}')
            (root / 'mitm').mkdir(); (root / 'mitm/aliases.json').write_text('{"a":"b"}')
        elif target == 'webui':
            (root / 'config.yaml').write_text('theme: dark\n')
            con = sqlite3.connect(root / 'state.db')
            con.execute('create table messages(id integer primary key, body text)')
            con.execute("insert into messages(body) values ('hello')")
            con.commit(); con.close()
            (root / 'webui').mkdir()
            (root / 'webui/settings.json').write_text('{"theme":"dark"}')
            (root / 'memories').mkdir(); (root / 'memories/MEMORY.md').write_text('memory')
            (root / 'skills').mkdir(); (root / 'skills/demo.md').write_text('skill')
            (root / 'cron').mkdir(); (root / 'cron/jobs.json').write_text('{}')
            (root / 'profiles').mkdir(); (root / 'profiles/work').mkdir()
            (root / 'profiles/work/config.yaml').write_text('model: demo\n')
            (root / 'sessions').mkdir(); (root / 'sessions/chat.json').write_text('{"m":1}')
            (root / 'attachments').mkdir(); (root / 'attachments/file.bin').write_bytes(b'abc')
        else:
            (root / 'app.py').write_text('APP=1\n')
            (root / 'templates').mkdir(); (root / 'templates/index.html').write_text('<main>ok</main>')
            (root / 'static').mkdir(); (root / 'static/redesign.css').write_text('body{}')
            (root / 'data').mkdir(); (root / 'data/state.json').write_text('{"enabled":true}')
            (root / 'lab_new_feature.py').write_text('FEATURE=True\n')
            (root / 'tests').mkdir(); (root / 'tests/test_feature.py').write_text('assert True\n')

    def test_roundtrip_restores_complete_managed_tree(self):
        for target in ('9router', 'webui', 'labs'):
            with self.subTest(target=target):
                self.seed(target)
                ignored = lab_backup.EXCLUDES[target]
                skip = {'db/data.sqlite'} if target == '9router' else ({'state.db'} if target == 'webui' else set())
                expected = tree(self.roots[target], ignored, skip)
                archive = lab_backup.create_backup(target)
                with zipfile.ZipFile(archive) as z:
                    names = set(z.namelist())
                    self.assertTrue(lab_backup.TARGETS[target]['required'] <= names)
                # Audit imported copy, then corrupt live files and add stale data.
                result = lab_backup.audit_upload(Upload(archive), target)
                for p in self.roots[target].rglob('*'):
                    rel = p.relative_to(self.roots[target]).as_posix()
                    protected_db = rel in {'db/data.sqlite', 'state.db'}
                    if p.is_file() and p.relative_to(self.roots[target]).parts[0] not in ignored and not protected_db:
                        p.write_bytes(b'corrupt')
                if target == '9router':
                    con = sqlite3.connect(self.roots[target] / 'db/data.sqlite')
                    con.execute("update accounts set name='corrupt'")
                    con.commit(); con.close()
                elif target == 'webui':
                    con = sqlite3.connect(self.roots[target] / 'state.db')
                    con.execute("update messages set body='corrupt'")
                    con.commit(); con.close()
                (self.roots[target] / 'stale.txt').write_text('must disappear')
                restored = lab_backup.restore(result['token'], target)
                self.assertTrue(restored['ok'])
                self.assertEqual(expected, tree(self.roots[target], ignored, skip))
                if target == '9router':
                    con = sqlite3.connect(self.roots[target] / 'db/data.sqlite')
                    self.assertEqual([('alpha',)], con.execute('select name from accounts').fetchall())
                    self.assertEqual('ok', con.execute('pragma integrity_check').fetchone()[0])
                    con.close()
                elif target == 'webui':
                    con = sqlite3.connect(self.roots[target] / 'state.db')
                    self.assertEqual([('hello',)], con.execute('select body from messages').fetchall())
                    self.assertEqual('ok', con.execute('pragma integrity_check').fetchone()[0])
                    con.close()
                self.assertFalse((self.roots[target] / 'stale.txt').exists())

    def test_wrong_target_rejected(self):
        self.seed('webui')
        archive = lab_backup.create_backup('webui')
        with self.assertRaisesRegex(ValueError, 'tidak boleh masuk'):
            lab_backup.audit_upload(Upload(archive), 'labs')


if __name__ == '__main__':
    unittest.main()
