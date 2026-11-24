> 🚧 **Durum: aktif geliştirme.** Yazarın kullanımında çalışır; arayüzler değişebilir.

# karincaai-icerik-studyosu

[English](README.md)

İçerik yazım akışı için ("İçerik Stüdyosu") çevrimdışı, yalnız standart kitaplık kullanan bir Python çekirdeği: **kaynaklar** ve **taslaklar** için yerel SQLite deposu, sıkı revizyon denetimi, editoryal kontrol ve kendi başınıza bir modele yapıştırabileceğiniz metni hazırlayan iki istem hazırlayıcı. Dar bir kod seçkisidir, **tam uygulama değildir**: arayüz, sunucu, otomatik URL okuma, model sağlayıcısı, medya ve sosyal ağlara yazma yoktur. Hiçbir zaman model çağırmaz.

Yapay zekâ destekli yazımın kolay yanlış yapılan kısımlarını açık ve sınanabilir tutmak için vardır: hangi kaynak hangi projeye ait, hangi taslak revizyonu incelendi ve bir istem neyi içerebilir.

## Nasıl çalışır

- `studio/store.py` — `StudioStore`: kaynaklar (elle veya içe aktarılmış), tek ana kaynağa ve isteğe bağlı proje kaynaklarına bağlı taslaklar, revizyon numaraları (eski revizyonda güncelleme `RevisionConflict` verir), geçmiş ve geri yükleme, metin onayı, işler ve öneriler.
- `studio/source_evidence.py` — URL ve kanıt doğrulaması. Kullanıcı bilgisi, geçersiz port, sorgu veya parça içeren kaynak URL'leri reddedilir.
- `studio/editorial.py` — okur özeti, açı önerileri, iddia haritası kontrolleri ve taslağın deterministik incelemesi.
- `studio/generator.py` — `build_prompt` (taslak + kaynaklar + talimat → metin) ve `parse_generated_draft` (modelin JSON cevabı için sıkı ayrıştırıcı).
- `studio/content_standard.py` + `content_standard.v1.json`, `editorial_policy.json` — kontrollerin okuduğu içerik standardı ve editoryal politika.
- `studio/text_budget.py` — kapalı hata verir (yaklaşık sayaç uydurulmaz).

## Hızlı başlangıç

Gereksinim: Python 3.10 veya üstü, yalnız standart kitaplık (3.12 ile denendi).

```python
from pathlib import Path
from studio.store import StudioStore
from studio.generator import build_prompt

store = StudioStore(Path("demo.sqlite3"))          # at-ve-unut bir veritabanı kullanın
source = store.create_manual_source({"kind": "web", "url": "https://example.invalid/a",
                                     "title": "Örnek", "text": "Sentetik bir kaynak metni.",
                                     "author": "A. Yazar"})
draft = store.create_draft(source["id"], "explainer", "Açıkla", "Genel okur")
print(build_prompt(draft, store.source_for_draft(draft["id"]), "Kaynakları değerlendir."))
```

Depo kökünden çalıştırın. Testler:

```sh
python -B -m unittest discover -p "test_*.py" -v
```

(9 test; yalnız depo klasörü altında geçici SQLite dosyaları kullanır ve temizler.)

## Gizlilik ve sınırlar

- İki istem hazırlayıcı kaynakları, kanıtı, taslağı ve talimatı metne **aynen** koyar ve başa uyarı yazar; kişisel veriyi silmez. Metni dış servise kopyalarsanız özel veriler de gider. Uyarı bir onay mekanizması veya veri sızıntısı filtresi değildir.
- URL doğrulaması bilinçli bir daraltmadır: URL yolu ve serbest metin yine özel bilgi taşıyabilir, eski kayıtlar temizlenmez. Gerçek veritabanına bağlamayın; paylaşmadan önce insan incelemesi yapın.
- Taslak anlık görüntüsü ile proje kaynakları arasındaki tutarlılık kontrolü yanlış proje ve eklenmemiş kaynağı yakalar ama kimlik doğrulaması değildir: depodan çıkan taslak ve kaynağı birlikte kullanın.
- Testlerde soket, DNS ve süreç başlatmaya karşı Python düzeyinde bir gözcü vardır; bu işletim sistemi düzeyinde ağ yalıtımı değildir.
- Yalnız sentetik testler. Denenmedi: tam ürün, gerçek veri, yayın, işletim sistemi yalıtımı. Ayrıntılı kapsam, köken ve sınırlar için `SECKI.md`.

## Lisans

MIT, bkz. `LICENSE`. Telif (c) 2026 Kemal (KarıncaAI). Python ve SQLite çalışma zamanları kendi lisanslarına tabidir. Güvenlik: `SECURITY.md`.
