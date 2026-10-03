# JARVIS CarPlay karşılama

Build 16, Kestirmeler için **Yolculuk özetini al** eylemini ekler. Uygulama içindeki **Ayarlar → CarPlay karşılama** ekranında aynı kurulum ve canlı takvim/bağlantı önizlemesi bulunur.

## Bir kez kurulum

1. JARVIS’i aç, hesabına giriş yap. Mac açık ve internete bağlı olsun. Telefonun aynı Wi-Fi’da olması gerekmez.
2. iPhone **Kestirmeler** uygulamasında yeni kestirme oluştur; adını **JARVIS Karşılama** yap.
3. **Şu Anki Hava Durumunu Al** eylemini ekle. Mevcut konumu veya istediğin şehri seç.
4. Bir **Metin** eylemi ekle. Hava durumu sonucunun **Koşullar** ve **Sıcaklık** alanlarını değişken olarak ekle. Örneğin `[Koşullar], [Sıcaklık]`. Sıcaklık değişkeninin birimi derece Celsius olsun; birimi ikinci kez elle ekleme.
5. **JARVIS → Yolculuk özetini al** eylemini ekle. **Hava durumu** parametresine önceki **Metin** sonucunu bağla.
6. Sonuna **Metni Seslendir** eylemi ekle. Girdi olarak **Yolculuk özeti** sonucunu, dil olarak Türkçe’yi seç.
7. Kestirmeyi bir kez elle çalıştır ve hava durumu/konum/eylem izinlerini tamamla. JARVIS eylemi listede yoksa güncel uygulamayı bir kez açıp Kestirmeler’i yeniden aç.
8. **Otomasyon → + → CarPlay → Bağlandığında → Hemen Çalıştır** yolunu izle; **JARVIS Karşılama** kestirmesini seç. Menü adı iOS diline/sürümüne göre değişebilir.

## Okunan bilgiler

Karşılama “Merhaba patron” ile başlar. Türkiye tarihini, Kestirmeler’den gelen hava durumunu ve günün kalan en fazla üç etkinliğinin saat/başlığını okur. Etkinlik notlarını okumaz. Yerel model, MacAgent ve komut işçisi ancak güncel sinyal doğrulandığında hazır olarak duyurulur. Bağlantı/takvim yoksa özet bunu açıkça söyler; boş takvim uydurmaz.

Bu akış mikrofonu açmaz, takvimi değiştirmez ve onay vermez. Seslendirme Kestirmeler’in seçtiğin Apple sesiyle yapılır. Yerel Chatterbox gibi ayrı bir ses modeli henüz entegre değildir. JARVIS şu aşamada araç ekranında ayrı bir CarPlay uygulaması olarak görünmez; bunun için Apple’ın uygun CarPlay yetkisi gerekir.

## Araçta doğrulama

Park halindeyken CarPlay’e bağlan. Karşılamanın bir kez duyulduğunu, sesin araçtan geldiğini ve gerçek takvim/hava bilgilerini kontrol et. Yeniden bağlanma ayrı bir tetikleyicidir; bağlantı kopup gelirse kestirme tekrar çalışabilir. Mac kapalıyken de dene: hazır mesajı yerine erişilemediği söylenmeli. Kilitli telefon testi, ilk manuel çalıştırma ve izinlerden sonra ayrıca yapılmalı. Bu testler henüz yapılmadı.

## Apple kaynakları

- [iOS 26 otomasyonları](https://support.apple.com/en-qa/guide/shortcuts/apdfbdbd7123/9.0/ios/26)
- [Otomasyonun onay sormadan çalışması](https://support.apple.com/en-qa/guide/shortcuts/apd602971e63/9.0/ios/26)
- [CarPlay uygulamaları ve yetki başvurusu](https://developer.apple.com/carplay/)
