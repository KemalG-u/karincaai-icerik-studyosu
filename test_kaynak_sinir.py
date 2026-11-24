"""Aday koduna bağlı, yalnız sentetik veri kullanan dar yerel sınır testi.

Bu test yalnız aday Studio kopyasını kullanır; tam ürün testi değildir.
"""
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from studio.store import StudioStore
from studio.generator import build_prompt


class KaynakSiniri(unittest.TestCase):
    def test_sentetik_projeler_yalniz_kendi_kaynaklarini_tasir(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('Ağ yasak')), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError('Alt süreç yasak')), \
             tempfile.TemporaryDirectory(dir=ROOT) as temp:
            store = StudioStore(Path(temp) / 'test.sqlite3')
            def kaynak(url, yazar, metin):
                return store.create_manual_source({'kind': 'web', 'url': url, 'title': 'Sentetik örnek', 'text': metin, 'author': yazar})
            a = kaynak('https://example.invalid/a', 'Örnek Yazar A', 'SENTETIK_ELMA')
            b = kaynak('https://example.invalid/b', 'Örnek Yazar B', 'SENTETIK_ARMUT')
            c = kaynak('https://example.invalid/c', 'Örnek Yazar C', 'SENTETIK_KIRAZ')
            pa = store.create_draft(a['id'], 'explainer', 'Açıkla', 'Okur')
            pb = store.create_draft(b['id'], 'explainer', 'Açıkla', 'Okur')
            pa = store.attach_project_source(pa['id'], pa['revision'], c['id'])
            for project, included, excluded in ((pa, (a, c), b), (pb, (b,), a)):
                bundle = store.source_for_draft(project['id'])
                self.assertEqual([(x['url'], x['author'], x['text']) for x in bundle['project_sources']], [(x['url'], x['author'], x['text']) for x in included])
                prompt = build_prompt(project, bundle, 'Kaynakları değerlendir.')
                for item in included:
                    self.assertIn(item['url'], prompt)
                    self.assertIn(item['author'], prompt)
                    self.assertIn(item['text'], prompt)
                self.assertNotIn(excluded['url'], prompt)
                self.assertNotIn(excluded['text'], prompt)
                self.assertFalse(project['publication_ready'])
                self.assertFalse(project['copy_approved'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
