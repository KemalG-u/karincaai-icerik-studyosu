import tempfile
import unittest
from pathlib import Path
from studio.store import StudioStore, StoreError
from studio.generator import build_prompt
from studio.editorial import build_model_review_prompt
from studio.content_standard import load_content_standard
ROOT = Path(__file__).resolve().parent

class Mahremiyet(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mahremiyet-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.store = StudioStore(Path(self.temp.name) / "synthetic.sqlite3")
    def source(self, url, text="sentetik"):
        return self.store.create_manual_source(dict(kind="web", url=url, title="Örnek", text=text, author="Kurgu"))
    def test_url_userinfo_ve_sorgu_kapali(self):
        for url in ("https://u:p@example.invalid/a", "https://example.invalid/a?x=123", "https://example.invalid/a#private", "https://example.invalid:bad/a"):
            with self.subTest(url=url), self.assertRaises(StoreError):
                self.source(url)
        self.assertEqual(self.source("https://example.invalid/a")["url"], "https://example.invalid/a")
    def test_yanlis_ve_eklenmis_kaynak_reddedilir(self):
        a = self.source("https://example.invalid/a")
        b = self.source("https://example.invalid/b", "DIGER_PROJE")
        draft = self.store.create_draft(a["id"], "explainer", "Örnek", "Okur")
        foreign = self.store.create_draft(b["id"], "explainer", "Örnek", "Okur")
        good = self.store.source_for_draft(draft["id"])
        wrong = self.store.source_for_draft(foreign["id"])
        forged = dict(good, project_sources=good["project_sources"] + wrong["project_sources"])
        for builder in (lambda d, s: build_prompt(d, s, "Örnek"), build_model_review_prompt):
            for bad in (wrong, forged):
                with self.subTest(builder=builder, bad=bad["id"]), self.assertRaises((StoreError, ValueError)):
                    builder(draft, bad)
            output = builder(draft, good)
            self.assertIn("dış servise", output)
            self.assertIn("gönderilmez", output)
        attached = self.store.attach_project_source(draft["id"], draft["revision"], b["id"])
        bundle = self.store.source_for_draft(attached["id"])
        for builder in (lambda d, s: build_prompt(d, s, "Örnek"), build_model_review_prompt):
            self.assertIn("DIGER_PROJE", builder(attached, bundle))
            altered = dict(bundle, text="DEGISTIRILMIS")
            with self.assertRaises((StoreError, ValueError)):
                builder(attached, altered)
    def test_eksik_snapshot_iki_istemde_reddedilir(self):
        source = self.source('https://example.invalid/snapshot', 'ILK_METIN')
        draft = self.store.create_draft(source['id'], 'explainer', 'Örnek', 'Okur')
        bundle = self.store.source_for_draft(draft['id'])
        builders = (lambda d, s: build_prompt(d, s, 'Örnek'), build_model_review_prompt)
        for builder in builders:
            self.assertIn('ILK_METIN', builder(draft, bundle))
            altered = dict(bundle, text='DEGISTIRILMIS_METIN')
            for label, snapshot in (('yok', None), ('bos', {}), ('yanlis_tur', [])):
                bad = dict(draft)
                if label == 'yok':
                    bad.pop('source_snapshot')
                else:
                    bad['source_snapshot'] = snapshot
                with self.subTest(builder=builder.__name__, snapshot=label), self.assertRaisesRegex(ValueError, 'anlık görüntü'):
                    builder(bad, altered)
            for field in draft['source_snapshot']:
                bad = dict(draft, source_snapshot={k: v for k, v in draft['source_snapshot'].items() if k != field})
                with self.subTest(builder=builder.__name__, field=field), self.assertRaisesRegex(ValueError, 'anlık görüntü'):
                    builder(bad, altered)
                bad_type = dict(draft, source_snapshot=dict(draft['source_snapshot'], **{field: 42}))
                with self.subTest(builder=builder.__name__, wrong_type=field), self.assertRaisesRegex(ValueError, 'anlık görüntü'):
                    builder(bad_type, bundle)

    def test_ornekler_kaynak_ve_haklari_acik(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2026 KemalG-u", license_text)
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        examples = load_content_standard()["calibration_examples"]
        self.assertEqual(len(examples), 2)
        for example in examples:
            self.assertEqual(example["provenance"], "sentetik-bu-secki-icin-yazildi")
            self.assertEqual(example["rights"], "MIT")
            self.assertTrue(example["text"] and example["reasons"])
