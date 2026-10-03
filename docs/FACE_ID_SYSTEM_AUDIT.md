# JARVIS V1 — Face ID ve sistem kabul kaydı

3 Ekim 2026. Mevcut JARVIS projesi kullanılır; yeni Firebase hesabı oluşturulmadı. Gemini’nin avatar/görsel tasarımına müdahale edilmedi; mevcut logo asset catalog ve uygulama simgesine bağlandı.

## Bu sürümdeki düzeltmeler

- LocalAuthentication ile otomatik açılış; Face ID veya cihaz parolası. Keychain `WhenPasscodeSetThisDeviceOnly` + `userPresence` ile ID/refresh token korunur. Eski kayıtlar ancak yeni korumalı kayıt yazılıp geri okunduğunda temizlenir.
- Arka planda oturum ve RAM anahtarları kilitlenir. Uygulama değiştirici için senkron UIKit örtüsü vardır. İptal edilen istem tekrar döngüsüne girmez; gecikmiş ağ/doğrulama yanıtları yeni oturumu açamaz.
- Geçerli token tekrar kullanılır; yenileme eşzamanlı isteklerde tek işlem olur. Geçici ağ hataları oturumu silmez; hesap iptali/disabled/deleted durumu yeniden giriş gerektirir. Çıkış Keychain kimlik bilgilerini temizler.
- Firebase bağlantısı bundled `GoogleService-Info.plist` üzerinden gelir. Normal kullanıcı için proje ID, API anahtarı, UID ve servis adresi formları kaldırıldı. Yönetici kimlik bilgileri uygulamaya eklenmedi.
- Son 200 mesaj iPhone’da AES-GCM ile saklanır; anahtar korumalı oturumdadır. Çıkış sonrası Firebase geçmişinden kurtarma vardır. Mac’in son 20 konuşma mesajı da ayrı Keychain anahtarıyla şifreli saklanır. Mevcut bulut konuşması yerel şifreli bağlama taşındı.
- E-posta/görev ekranlarının iPhone `localhost` bağlantısı düzeltildi. Artık sahibin Firebase kuyruğunu kullanırlar; bağlantı hatası boş/başarılı sonuç gibi gösterilmez. Yeni e-posta analizinin gerçek özeti şifreli kalıcı saklanır; ekran açmak analiz veya bildirim üretmez.
- Görev yazmaları ayrı sunucu onay ID’si + işlem özeti + kullanıcı kimliğine bağlıdır. Tekrar dokunma aynı komutu izler. Takvim/hatırlatıcı geri okuması gerçek ID, başlık ve tarihleri eşleştirmeden başarı bildirmez. Belirsiz kayıt yeniden oluşturulmaz.
- Yerel hafıza yönetim API’sindeki PRIVATE okuma/yazma izin atlaması kapatıldı. Özel kategori izni test amacıyla açılmadı.
- Çevrimdışı iPhone derlemeyi engellemez. Mac uygulaması sonraki derlemelerde önceki sertifikasını kullanarak takvim izin kimliğini korur.

## Kabul matrisi

PASS gerçek servis/cihaz gözlemidir. PASS (MOCK) kontrollü testtir; biyometrik donanım kanıtı değildir.

| Modül | Durum | Doğrulama yöntemi | Kalan |
|---|---|---|---|
| Qwen + Firebase Worker | PASS | Gerçek üretim kuyruğundan Mac kaynaklı salt okuma denemesi: gerçek yanıt, düşünme etiketi sızıntısı yok | Bu deneme iPhone kaynaklı değildir |
| Face ID / oturum güvenliği | PASS + PASS (MOCK) | Kullanıcı yapı 18’de parolasız Face ID ve gerçek sohbet yanıtını doğruladı; 12 Swift güvenlik testi | Cihaz parolası/iptal/çevrimdışı senaryoları yalnızca kontrollü test |
| Teknik ayar gerektirmeyen giriş | PASS | Bundled proje/bundle doğrulaması, formlar kaldırıldı, imzalı derleme | Kullanıcı giriş ve Ayarlar ekranını doğruladı |
| Hafıza | PASS | 28 LOCAL_ONLY, 6 PRIVATE; 6 şifreli kayıtta gerçek Mac Keychain ile doğrulanmış çözüm; 5 hassas kategorinin tamamı pending | İçerik rapora/loga çıkarılmadı; izin verilmedi |
| Gmail bağlantı / yenileme | PASS | Gerçek süresi dolmuş OAuth token yenilendi ve Gmail profil isteği başarılı | Yeni gerçek ileti gönderilmedi |
| Gmail yeni ileti / takvim | PASS + PASS (MOCK) | Yeni ileti sınırı korunuyor, 103 eski kayıt korunuyor, bekleyen analiz 0; 09.00/20.00 İstanbul; kalıcı zamanlama ve kota geri çekilme testleri | Yeni önemli ileti bildiriminin bu sürümde fiziksel gelişi bekleniyor |
| Apple Calendar / Reminders okuma | PASS | Gerçek EventKit: 6 takvim, 1 hatırlatıcı listesi, bir haftada 6 etkinlik; 67 uygun zaman aralığı hesaplandı | Kullanıcı iPhone takvim ekranını doğruladı |
| Takvim / hatırlatıcı yazma | PASS (MOCK), BLOCKED | Sahip/onay/digest, mükerrer işlem, hata/boş/yanlış geri okuma testleri | Belirlenen iki sentetik kayıt için açık onay ve iPhone onay kartı gerekiyor |
| E-posta / görev ekranı bağlantısı | PASS | Gerçek üretim kuyruğu: 0 yeni analiz özeti, 10 mevcut görev ayrıntısı döndü | Kullanıcı iPhone ekranlarını doğruladı |
| Ses / avatar | PASS | Kullanıcı yapı 18’de dokunarak Türkçe konuşma, sesli Qwen yanıtı ve avatar tepkisini doğruladı | Ayrı yerel TTS modeli yok |
| Yerel Türkçe TTS modeli | NOT TESTED | Apple’ın mevcut Türkçe sesi kullanılıyor | Ayrı indirilen ses modeli kurulmadı; ücretli sağlayıcı açılmadı |
| Mac servisleri / izinler | PASS | launchd süreçleri yeniden başlatıldı; Qwen/Worker hazır; yeni sertifikayla iki Mac derlemesi sonrası EventKit izinleri korundu | Tam Mac yeniden başlatma ve uyku/uyanma NOT TESTED |
| İmzalı iOS derleme | PASS | Yapı 19, Apple imzalı (codesign + profile) ve devicectl kablosuz yükleme doğrulandı | Fiziksel cihazda sürüm 19 çalıştırıldı |
| Otomatik imza yenileme | PASS (MOCK) | Otomatik yenileme açık; profil yaklaşık 6,7 gün geçerli | 2 günlük eşik oluşmadı; gerçek yenileme PASS değildir |
| CarPlay | BLOCKED | Tarih/takvim/hava metni kontrollü testler ve gerçek Mac takvim okuması | Park halindeki araç/Kestirmeler testi yok; kilitliyken özel gündem otomatik okunmaz |

## Test sonuçları

- Python: **237 geçti**.
- iOS Swift: **26 geçti** (gerçek uygulama kaynaklarına sembolik bağlantılar).
- Mac Swift: **17 geçti**.
- Toplam: **280 otomatik test**; ayrıca **7 Firebase Emulator kabul grubu** başarılı.
- iOS unsigned ve Apple imzalı yapı 19 başarılı. Mac release derlemesi ve sertifika imzası doğrulandı.
- GitHub CI: push sonrası sonuç eklenecek.

## Fiziksel iPhone kabulü

Güncelleme öncesi uygulama veri işareti okundu ve eşleşti. Yapı 19 kablosuz (CoreDevice localNetwork) devicectl ile başarıyla yüklendi, sürüm/bundle doğrulandı (1.3.0 / 19) ve uygulama cihazda başlatıldı. Güncelleme sonrasında veri işareti yeniden okundu ve eşleşti. Kullanıcı yapı 18'de Face ID, sohbet yanıtı, Türkçe ses ve avatar tepkisini; arka plan gizleme, yeniden Face ID istemi ve geçmişin korunmasını doğruladı. Yapı 19 bu yapılandırmayı korur.

Güvenli test sırası: Face ID ile mevcut hesabı aç → uygulamayı kapat/aç → iptal ve cihaz parolası → gerçek sohbet → mikrofonu dokunarak başlat/bitir → takvim/e-posta/durum ekranları → kısa internet kesintisi → kablosuz güncelleme. Gerçek hesabın token’ı test için iptal edilmez; iptal ve süresi dolma kontrollü HTTP testlerindedir.

## Dört kabul sorusu

1. Evet. Kullanıcı yapı 18’de mevcut hesabın Face ID ile açıldığını ve gerçek sohbet yanıtı geldiğini doğruladı.
2. Normal kullanımda API anahtarı/proje ID girmek gerekmez. İlk dağıtım konfigürasyonu derlemeye eklenir; kayıtlı hesap korunur.
3. iPhone’da Face ID, gerçek sohbet yanıtı, Türkçe sesli konuşma ve avatar tepkisi doğrulandı. Mac/Firebase/Gmail/EventKit okuma akışları da gerçek servislerle geçti.
4. Cihaz parolası/iptal/çevrimdışı ve  sentetik yazma onayı, kablosuz yükleme, Mac reboot/uyku ve park halindeki araç testi kullanıcı/fiziksel ortam gerektiriyor. Ayrı yerel Türkçe TTS ve kilitli CarPlay özel gündemi ek geliştirme gerektiriyor.
