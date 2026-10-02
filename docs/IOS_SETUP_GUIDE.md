# JARVIS iOS Native App & Wireless Build Manager — Setup Guide

Bu kılavuz, iPhone 13 cihazınızı Mac mini M4 üzerindeki JARVIS sunucusuna ve Xcode Build Manager'a bağlamak için gerekli adımları içerir.

---

## 1. Ön Koşullar ve Yapılandırma Bilgileri

- **Mac mini**: macOS 14+ (M4), Tam Xcode 16+ kurulu (`/Applications/Xcode.app`)
- **Hedef Cihaz**: iPhone 13 (iOS 17+)
- **Apple Geliştirici Hesabı**: Ücretsiz Personal Team (`T92VJM9C5B`)
- **Sabit Bundle Identifier**: `com.mfatihc.jarvis`
- **Aktif Developer Directory**: `/Applications/Xcode.app/Contents/Developer`

---

## 2. Adım Adım Kurulum

### Adım 1: Apple Hesabını Xcode'a Ekleme
1. Mac mini'de **Xcode** uygulamasını açın.
2. Üst menüden **Xcode → Settings... (veya Preferences) → Accounts** sekmesine gidin.
3. Sol alttaki **`+`** butonuna tıklayıp **Apple ID** seçeneğini seçin.
4. Geliştirici e-posta adresinizle (`muhammedfatihcetintas54@gmail.com`) giriş yapın.
5. Sağ tarafta **Personal Team** (`T92VJM9C5B`) göründüğünü doğrulayın.

### Adım 2: iPhone'da Geliştirici Modunu (Developer Mode) Açma
1. iPhone'unuzda **Ayarlar → Gizlilik ve Güvenlik** bölümüne gidin.
2. En alta kaydırıp **Geliştirici Modu (Developer Mode)** seçeneğine dokunun.
3. Modu **Açık** konuma getirin ve cihazınızı yeniden başlatın.
4. Cihaz açıldığında ekranda beliren uyarıda **Aç** butonuna dokunun ve parolanızı girin.

### Adım 3: İlk USB Bağlantısı ve Güven Eşleştirmesi
1. iPhone 13'ü Mac mini'ye kablo ile bağlayın.
2. iPhone ekranında beliren **"Bu Bilgisayara Güvenilsin mi?"** uyarısında **Güven** seçeneğine dokunun ve kilit parolasını girin.
3. Xcode'da **Window → Devices and Simulators** (Kısayol: `Cmd + Shift + 2`) penceresini açın.
4. Sol menüde **Fatih** isimli iPhone'u seçin.
5. **Connect via network** (Ağ üzerinden bağlan) kutucuğunu işaretleyin. Bu işlem kablosuz build ve deploy yeteneğini etkinleştirir.

### Adım 4: Bağlantı Durumunu Terminalden Doğrulama
Terminalde şu komutu çalıştırarak cihazın tanındığını kontrol edin:
```bash
xcrun devicectl list devices
```
Veya Jarvis CLI aracını kullanın:
```bash
uv run python scripts/ios_build_status.py
```
Çıktıda şunlar görünmelidir:
- `Paired: paired`
- `Dev Mode: Enabled`
- `Reachable: YES`

---

## 3. İlk Manuel Build ve Yükleme

Otomatik modu açmadan önce uygulamanın ilk kez başarıyla yüklenmesi şarttır:

```bash
# 1. Uygulamayı derleyin ve imzalayın
uv run python scripts/ios_build.py

# 2. iPhone'a kablosuz/kablolu yükleyin
uv run python scripts/ios_install.py
```

*Not:* iPhone kilitliyse yükleme sırasında *"Device is passcode locked"* uyarısı alırsınız. Telefonun ekranını açmanız yeterlidir.

---

## 4. 7 Günlük Personal Team Süresi ve Otomatik Yenileme (LaunchAgent)

Apple ücretsiz Personal Team profilleri **7 gün** sonra sona erer. JARVIS Build Manager, profil süresinin bitmesine **yaklaşık 2 gün** kala otomatik yenileme tetikler.

### Otomatik Arka Plan Servisini (LaunchAgent) Kurma:
```bash
# 1. LaunchAgents klasörünü oluşturun
mkdir -p ~/Library/LaunchAgents

# 2. Plist dosyasını kopyalayın
cp scripts/launchd/com.jarvis.ios-build-manager.plist ~/Library/LaunchAgents/

# 3. Servisi launchd'ye kaydedin
launchctl load ~/Library/LaunchAgents/com.jarvis.ios-build-manager.plist
```
Bu servis her **15 dakikada bir** cihaz durumunu ve sertifika süresini denetler.

---

## 5. Jarvis Sesli ve Metin Komutları

Build Manager doğrudan Jarvis konuşma motoruna ve Policy Engine'e entegredir:

- *"Jarvis, iPhone uygulamanın durumunu kontrol et."* → `ios.build.status` (R0, onay gerektirmez)
- *"Jarvis, telefonun bağlantısını kontrol et."* → `ios.build.check_device` (R0, onay gerektirmez)
- *"Jarvis, iPhone uygulamasını derle."* → `ios.build.build` (R2, işlem onayı gerektirir)
- *"Jarvis, uygulamayı telefona yükle."* → `ios.build.install` (R2, işlem onayı gerektirir)
- *"Jarvis, imzanın bitmesine kaç gün kaldı?"* → `ios.build.status` (Provisioning kalan gün raporlanır)
- *"Jarvis, telefon bağlandığında güncellemeyi yükle."* → Otomasyon politikasını tetikler
