"""Sentetik seçki kontrolleri. Merkez test kapısı açılana kadar çalıştırmayın.

Bu gözcü yalnız Python seviyesindedir; işletim sistemi ağ engeli yerine geçmez.
"""
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.store import StudioStore, RevisionConflict
from studio.generator import build_prompt

ROOT = Path(__file__).resolve().parent


class OfflineGuard:
    def __init__(self):
        self.attempts = []
        self.patches = []

    def deny(self, name):
        def blocked(*args, **kwargs):
            self.attempts.append(name)
            raise AssertionError('Çevrimdışı sınır: ' + name)
        return blocked

    def __enter__(self):
        targets = [(socket, 'socket'), (socket, 'create_connection'),
                   (socket, 'getaddrinfo'), (socket, 'gethostbyname'),
                   (socket, 'gethostbyname_ex'), (socket, 'gethostbyaddr'),
                   (subprocess, 'Popen'), (os, 'system'), (os, 'popen')]
        try:
            for module, name in targets:
                p = patch.object(module, name, self.deny(name))
                p.start()
                self.patches.append(p)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


class CevrimdisiTest(unittest.TestCase):
    def setUp(self):
        self.guard = OfflineGuard().__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)
        self.addCleanup(self.assert_no_attempts)
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.store = StudioStore(Path(self.temp.name) / 'synthetic.sqlite3')

    def assert_no_attempts(self):
        self.assertEqual(self.guard.attempts, [], 'Yutulmuş ağ veya süreç girişimi')

    def source(self, text):
        return self.store.create_manual_source({'kind': 'web', 'url': 'https://example.invalid/' + text,
            'title': 'Örnek', 'text': text, 'author': 'Sentetik Yazar ' + text})

    def test_proje_siniri_elle_taslak_ve_kalici_kayit(self):
        a, b, c = map(self.source, ('SENTETIK_ELMA', 'SENTETIK_ARMUT', 'SENTETIK_KIRAZ'))
        pa = self.store.create_draft(a['id'], 'explainer', 'Açıkla', 'Okur')
        pb = self.store.create_draft(b['id'], 'explainer', 'Açıkla', 'Okur')
        eski = pa['revision']
        pa = self.store.attach_project_source(pa['id'], eski, c['id'])
        with self.assertRaises(RevisionConflict):
            self.store.update_draft(pa['id'], eski, {'main': 'eski revizyon'})
        self.assertEqual(self.store.get_draft(pa['id'])['main'], '')
        pa = self.store.update_draft(pa['id'], pa['revision'], {'main': 'Elle yazılmış sentetik taslak.'})
        pa = self.store.get_draft(pa['id'])
        self.assertEqual(pa['main'], 'Elle yazılmış sentetik taslak.')
        self.assertEqual(self.store.get_draft(pb['id'])['main'], '')
        self.assertFalse(pa['copy_approved'])
        self.assertFalse(pa['publication_ready'])
        self.assertFalse(pa['cloud_tts_allowed'])
        for draft, included, excluded in ((pa, (a, c), (b,)), (pb, (b,), (a, c))):
            bundle = self.store.source_for_draft(draft['id'])
            self.assertEqual([x['id'] for x in bundle['project_sources']], [x['id'] for x in included])
            prompt = build_prompt(draft, bundle, 'Kaynakları değerlendir.')
            for item in included:
                for field in ('text', 'url', 'author'):
                    self.assertIn(item[field], prompt)
            for item in excluded:
                for field in ('text', 'url', 'author'):
                    self.assertNotIn(item[field], prompt)

    def test_gozcu_kasitli_girisimleri_yakalar_ve_yerel_is_sessizdir(self):
        probes = [(socket.socket, ()), (socket.create_connection, (('example.invalid', 443),)),
                  (socket.getaddrinfo, ('example.invalid', 443)),
                  (socket.gethostbyname, ('example.invalid',)),
                  (socket.gethostbyname_ex, ('example.invalid',)),
                  (socket.gethostbyaddr, ('192.0.2.1',)),
                  (subprocess.Popen, (['synthetic-never-execute'],)),
                  (os.system, ('synthetic-never-execute',)),
                  (os.popen, ('synthetic-never-execute',))]
        for call, args in probes:
            with self.assertRaisesRegex(AssertionError, 'Çevrimdışı sınır'):
                call(*args)
        self.assertEqual(self.guard.attempts,
                         ['socket', 'create_connection', 'getaddrinfo', 'gethostbyname',
                          'gethostbyname_ex', 'gethostbyaddr', 'Popen', 'system', 'popen'])
        self.guard.attempts.clear()  # Yalnız kasıtlı pozitif denemeler.
        self.source('SENTETIK_CEVRIMDISI')
        self.assertEqual(self.guard.attempts, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
