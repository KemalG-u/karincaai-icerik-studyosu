"""Offline synthetic checks: local storage, revisions and the network/process guard."""
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.store import StudioStore, RevisionConflict
from studio.generator import build_prompt, parse_generated_draft

ROOT = Path(__file__).resolve().parent

class SeçkiTesti(unittest.TestCase):
    def test_kaynak_taslak_ayrim_ve_rev(constraint=None):
        self = constraint
        with patch.object(socket, 'socket', side_effect=AssertionError('ağ yasak')), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError('süreç yasak')), \
             tempfile.TemporaryDirectory(dir=ROOT) as temp:
            store = StudioStore(Path(temp) / 'synthetic.sqlite3')
            def source(text):
                return store.create_manual_source({'kind': 'web', 'url': 'https://example.invalid/' + text, 'title': 'Örnek', 'text': text, 'author': 'Örnek Yazar'})
            a, b, c = map(source, ('SENTETIK_ELMA', 'SENTETIK_ARMUT', 'SENTETIK_KIRAZ'))
            pa = store.create_draft(a['id'], 'explainer', 'Açıkla', 'Okur')
            pb = store.create_draft(b['id'], 'explainer', 'Açıkla', 'Okur')
            old_revision = pa['revision']
            pa = store.attach_project_source(pa['id'], old_revision, c['id'])
            with self.assertRaises(RevisionConflict):
                store.update_draft(pa['id'], old_revision, {'main': 'eski revizyon'})
            pa = store.update_draft(pa['id'], pa['revision'], {'main': 'İnsan eliyle yazılmış sentetik taslak.'})
            self.assertEqual(pa['main'], 'İnsan eliyle yazılmış sentetik taslak.')
            self.assertFalse(pa['copy_approved'])
            self.assertFalse(pa['publication_ready'])
            for draft, included, excluded in ((pa, (a, c), b), (pb, (b,), a)):
                bundle = store.source_for_draft(draft['id'])
                self.assertEqual([x['text'] for x in bundle['project_sources']], [x['text'] for x in included])
                prompt = build_prompt(draft, bundle, 'Kaynakları değerlendir.')
                for item in included:
                    self.assertIn(item['text'], prompt)
                    self.assertIn(item['url'], prompt)
                self.assertNotIn(excluded['text'], prompt)
                self.assertNotIn(excluded['url'], prompt)

    def test_model_yaniti_sayac_yoksa_reddedilir(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('ağ yasak')), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError('süreç yasak')):
            with self.assertRaisesRegex(ValueError, 'kapalı'):
                parse_generated_draft('{"title":"Örnek","main":"Örnek","reply":"","scene_texts":[]}')

if __name__ == '__main__':
    unittest.main(verbosity=2)
