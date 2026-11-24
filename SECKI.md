# KarıncaAI İçerik Stüdyosu — çevrimdışı kod seçkisi

**Dar sentetik testlerden geçti.** Tam uygulama değildir. Yerel SQLite kaynak/taslak çekirdeği ve istem hazırlayıcıdır. Arayüz, sunucu, medya, otomatik URL okuma, model sağlayıcısı ve X'e yazma yoktur.

## Özel veri ve proje sınırı

İki istem hazırlayıcı kaynakları, kanıtı, taslağı ve talimatı metne aynen koyar; kişisel veriyi otomatik silmez. Kullanıcı metni dış servise kopyalarsa özel veriler de gider. Her iki metnin başında uyarı vardır. Seçki model çağrısı veya dış gönderim yapmaz. Uyarı onay mekanizması veya veri sızıntısı filtresi değildir. Gerçek veritabanına bağlamayın: `StudioStore` verilen yolu oluşturur/değiştirir.

Yeni kaynak ve doğrulanmış/bağlı URL kontrolleri kullanıcı bilgisi, geçersiz port, tüm sorgu ve parçaları reddeder; bilinmeyen hassas parametreler için bilinçli daraltmadır. YouTube gibi sorgu gerektiren bağlantılar da reddedilir. URL yolundaki veya serbest metindeki özel bilgiler otomatik tespit edilmez; eski kayıtlar geriye dönük temizlenmez. Paylaşmadan önce insan incelemesi gerekir.

İki istem hazırlayıcı kaynak listesini ve ana kaynağı taslağın anlık görüntüsüyle karşılaştırır; yanlış proje ve eklenmemiş kaynak reddedilir. Kontrol çağıranın verdiği sözlüklerin doğruluğunu veya kimliğini kanıtlamaz; hem taslak hem kaynak değiştirilirse güvenlik sınırı değildir. Yalnız güvenilir `StudioStore.get_draft` ve aynı taslağın `source_for_draft` çıktısını kullanın; bu bir yetkilendirme sistemi değildir. On alanın her biri zorunlu ve türü kontrol edilir; eksik veya bozuk tarihsel anlık görüntüler reddedilir.

## Dosya kökeni, haklar ve bağımlılıklar

Kaynak: yazarın özel içerik projesinin `studio` modülü; `store.py`, `source_evidence.py`, `editorial.py`, `content_standard.py`, `content_standard.v1.json`, `editorial_policy.json`, `generator.py`. Yedi dosyanın tümü mevcut; satır karşılaştırmasında adayla benzerlik sırasıyla %99,8, %97,1, %95,5, %98,4, %95,1, %100 ve %97,4. Politika dosyasının SHA-256 değeri iki tarafta aynıdır (`a7ab82889f9330444377e050abfe5009d72b30d3082a85b104815ca5cfdbd39e`); diğer altısı değiştirilmiştir. Kaynak kökünde ve üstlerinde Git deposu bulunmadı: commit/yazar zinciri doğrulanamadı. Dosya başlıklarında üçüncü taraf telif/lisans bildirimi görülmedi; kod importları standart kitaplık ve yerel modüllerdir. Bu tarama özgünlüğü veya üçüncü taraf haklarının yokluğunu ispatlamaz.

Kemal kendi projeleri için MIT açık kaynak izni verdi; doğrulanmış GitHub adı KemalG-u olarak bildirildi. Bu beyana dayanarak seçkiye `LICENSE` eklendi. Bildirilmeyen üçüncü taraf katkısı varsa bu izin onu kapsamaz; hak durumu ayrıca çözülmelidir. Git geçmişi bulunmadığından bağımsız hak zinciri kanıtı eksiktir; seçkinin yayımlanmış olması hak zincirini doğrulamaz ve bu çalışma hukuki garanti değildir.

Standarttaki kökeni belirsiz iki örnek çıkarıldı; yerlerine bu görev için özgün sentetik iki örnek yazıldı, `provenance` ve `rights` alanları açıklandı. Bu yeni örneklerin MIT işareti kökteki gerçek `LICENSE` metnine bağlıdır. Standardın `1.0.0` kimliği yükleyici sözleşmesini korur; örnekler değişmiştir, özgün standardın birebir arşivi değildir.

Statik import taramasında standart kitaplık ve yerel `studio` modülleri dışında paket görülmedi. `twitter-text`, Node ve üçüncü taraf medyası seçilmedi; `text_budget.py` yaklaşık sayaç uydurmadan kapalı hata verir. Python ve SQLite çalışma zamanı kendi lisanslarına tabidir; bu inceleme üçüncü taraf hak zincirini kanıtlamaz. `__init__.py`, `text_budget.py` ve testler seçki için yazılmıştır.

Gerçek veri, arşiv, oturum, sır, `.env`, veritabanı, medya ve kişisel raporlar seçilmedi.

## Test ve sınırlar

`test_mahremiyet.py` sentetik URL reddi, yanlış proje, eklenmemiş kaynak, eksik/boş/yanlış türde snapshot, her zorunlu alan ve değiştirilmiş ana kaynak, bağlanmış kaynak, iki istemde uyarı ve örnek kökenini sınar. Diğer testler yerel saklama, revizyon ve Python soket/DNS/süreç gözcüsünü sınar. Geçici SQLite yalnız testin aday altında oluşturduğu klasördedir ve temizlenir. Python gözcüsü OS düzeyinde ağ engeli değildir; yerel uzantıları ve önceden alınmış referansları kapsamaz. Testler `python -B -m unittest discover` ile çalıştırılır. Tam ürün, yayın, OS yalıtımı veya hak doğrulaması testi değildir.
