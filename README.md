# ToriiBatch

**ToriiBatch**, manga ve webtoon bölümlerini toplu olarak [toriitranslate.com](https://toriitranslate.com) API'si üzerinden otomatik çeviren bir masaüstü uygulamasıdır. Python 3.11+ ve PyQt6 ile geliştirilmiştir.

---

## Özellikler

- **Toplu bölüm çevirisi** — Ana klasörün altındaki her alt klasörü bir bölüm olarak tanır; tüm sayfaları sırayla işler.
- **Otomatik bağlam zinciri (Context Chain)** — Bölüm içindeki sayfalar arasında karakter adları ve terminoloji tutarlılığını korur. Her bölüm kendi bağımsız zincirine sahiptir.
- **Kaldığı yerden devam** — Daha önce çevrilmiş sayfaları algılar ve atlar; yarım kalan işler sorunsuz sürdürülür.
- **BYOK (Bring Your Own Key)** — OpenAI, OpenRouter, Google, Anthropic, DeepSeek, xAI veya yerel model API anahtarlarınızı kullanabilirsiniz.
- **Çoklu model desteği** — Gemini, GPT, Claude, DeepSeek ve daha fazlası; model listesi ayarlardan düzenlenebilir.
- **Özelleştirilebilir çıktı** — PNG/JPG/WebP format seçimi, inpainted (temiz) kopya kaydetme, orijinal yedekleme.
- **Duraklat / Devam Et / İptal** — Çeviri sürecini kesintisiz yönetin.
- **Koyu tema** — Modern, göz yormayan arayüz.
- **Tek tık kurulum** — `install.bat` ile her şey otomatik kurulur.

---

## Gereksinimler

- Windows 10/11
- [Python 3.11 veya üzeri](https://www.python.org/downloads/) (kurulumda **"Add python.exe to PATH"** seçeneğini işaretleyin)
- İnternet bağlantısı (ilk kurulum ve çeviri için)
- Geçerli bir [toriitranslate.com](https://toriitranslate.com) hesabı ve kredisi

---

## Kurulum

1. Bu repoyu veya ZIP dosyasını indirin ve bir klasöre çıkarın.
2. Klasördeki **`install.bat`** dosyasına **çift tıklayın**.
   - Python varlığı ve sürümü otomatik kontrol edilir.
   - Python bulunamazsa ekranda indirme bağlantısı gösterilir.
   - Sanal ortam (`venv`) oluşturulur ve tüm bağımlılıklar yüklenir.
3. Kurulum tamamlandığında **`start.bat`** dosyasına çift tıklayarak uygulamayı başlatın.

> **Not:** Kurulum yalnızca ilk seferinde yapılır. Sonraki kullanımlarda doğrudan `start.bat` yeterlidir.

---

## İlk Kullanım — API Anahtarı Ayarlama

ToriiBatch, çeviri için toriitranslate.com API'sini kullanır. Çeviri yapabilmek için geçerli bir API anahtarı gereklidir.

### API Anahtarı Nasıl Alınır?

1. [toriitranslate.com](https://toriitranslate.com) adresine gidin ve hesap oluşturun (veya giriş yapın).
2. Hesap ayarlarından **API** bölümüne gidin: `https://toriitranslate.com/api`
3. "API Key" bölümünden anahtarınızı kopyalayın (`tt_...` ile başlar).

### Anahtarı Uygulamaya Girin

1. Uygulamayı açın (ilk açılışta otomatik olarak Ayarlar uyarısı çıkar).
2. Sağ üstteki **⚙ Ayarlar** butonuna tıklayın.
3. **API & Kimlik** sekmesinde API Anahtarı alanına anahtarınızı yapıştırın.
4. **Kaydet**'e tıklayın.

Anahtarınız yerel olarak Fernet şifrelemesiyle saklanır; hiçbir şekilde dışarıya gönderilmez.

---

## Kullanım

### 1. Kaynak Klasörü Seçin

Sol paneldeki **sürükle-bırak alanına** manga/webtoon klasörünüzü sürükleyin veya tıklayarak seçin.

Beklenen klasör yapısı:

```
MangaAdı/
├── Bölüm 1/
│   ├── 001.jpg
│   ├── 002.jpg
│   └── ...
├── Bölüm 2/
│   ├── 001.jpg
│   └── ...
└── ...
```

> Klasörün doğrudan görsel içermesi durumunda (alt klasör yoksa) tek bölüm olarak işlenir.

### 2. Çıktı Klasörünü Belirleyin

Kaynak klasörü seçildiğinde çıktı klasörü otomatik olarak `<KaynakKlasörAdı>_translated` olarak ayarlanır. İstediğiniz zaman **Değiştir** butonuyla değiştirebilirsiniz.

### 3. Bölümleri Seçin

Sol paneldeki listede çevirmek istediğiniz bölümlerin yanındaki onay kutularını işaretleyin. **Tümünü Seç / Tümünü Kaldır** bağlantılarını kullanabilirsiniz.

### 4. Çeviri Ayarlarını Yapılandırın

**⚙ Ayarlar > Çeviri** sekmesinden:
- Hedef dil (örn. `tr` Türkçe, `en` İngilizce)
- Çeviri modeli
- Font
- Özel çeviri talimatı (isteğe bağlı)

### 5. Çeviriyi Başlatın

Alt kısımdaki büyük **▶ Çeviriyi Başlat** butonuna tıklayın.

- **⏸ Duraklat** — İşlemi mevcut sayfanın bitmesini bekleyerek duraklatır.
- **▶ Devam Et** — Duraklatılmış işlemi sürdürür.
- **✕ İptal Et** — İşlemi sonlandırır.

Çeviri tamamlandığında ekran altında bildirim çıkar ve **"Klasörü Aç"** butonu ile çıktı klasörü doğrudan açılabilir.

---

## Sık Karşılaşılan Sorunlar

### Python kurulu değil veya bulunamıyor

`install.bat` çalıştırıldığında **"Python bulunamadı"** hatası alıyorsanız:

1. [python.org/downloads](https://www.python.org/downloads/) adresinden Python 3.11 veya üzerini indirin.
2. Kurulum sırasında **"Add python.exe to PATH"** seçeneğini mutlaka işaretleyin.
3. Kurulumu tamamlayın ve `install.bat`'ı tekrar çalıştırın.

### API anahtarı hatalı veya geçersiz

- **"401 Unauthorized"** hatası: Ayarlar > API sekmesinde anahtarı kontrol edin. Anahtarın `tt_` ile başladığından emin olun.
- **"Bağlantı hatası"**: İnternet bağlantınızı kontrol edin. VPN kullanıyorsanız devre dışı bırakmayı deneyin.
- Anahtarı toriitranslate.com üzerinden yeniden oluşturup tekrar girin.

### Rate limit hatası (429 / 503)

toriitranslate.com API'si saniyede 1 istek sınırına sahiptir. ToriiBatch bu sınırı otomatik olarak yönetir:
- 429/503 hatalarında exponential backoff ile otomatik yeniden deneme yapılır (maksimum 5 deneme).
- Uygulamayı kapatıp açmanıza gerek yoktur; işlem kendi kendine devam eder.

### Kredi bitmesi

Çeviri sırasında kredi yetersizliği hatası alırsanız:
1. Sağ üstteki **Kredi** göstergesini takip edin.
2. toriitranslate.com hesabınızdan kredi yükleyin.
3. Uygulamayı yeniden başlatmadan **▶ Çeviriyi Başlat**'a tekrar tıklayın; kaldığı yerden devam eder.

### Uygulama açılırken hata

`%APPDATA%\ToriiBatch\logs\app.log` dosyasını açarak hata detaylarına bakın.

---

## Proje Yapısı

```
ToriiBatch/
├── main.py                  # Uygulama giriş noktası
├── requirements.txt         # Python bağımlılıkları
├── install.bat              # Tek tık kurulum (Windows)
├── start.bat                # Uygulamayı başlatma (Windows)
├── config/
│   └── default_config.json  # Varsayılan ayarlar
├── core/
│   ├── api_client.py        # toriitranslate.com HTTP istemcisi
│   ├── translator_engine.py # Asenkron çeviri motoru
│   ├── file_scanner.py      # Klasör/sayfa tarama
│   └── settings_manager.py  # Ayar yönetimi ve şifreleme
├── ui/
│   ├── main_window.py       # Ana pencere
│   ├── settings_dialog.py   # Ayarlar diyaloğu
│   ├── widgets.py           # Özel widget bileşenleri
│   └── theme.py             # Koyu tema (QSS)
└── assets/
    └── icon.ico             # Uygulama ikonu
```

---

## Lisans ve Uyarı

Bu yazılım **bağımsız, açık kaynaklı** bir istemcidir ve [toriitranslate.com](https://toriitranslate.com) ile **resmi bir bağlantısı yoktur**.

- Çeviri hizmeti için [toriitranslate.com](https://toriitranslate.com) API'si kullanılır.
- Uygulamayı kullanabilmek için geçerli bir **Torii hesabı** ve **yeterli kredi** gereklidir.
- API kullanım koşulları için: [toriitranslate.com/terms](https://toriitranslate.com/terms)
- Bu araç yalnızca kişisel kullanım amacıyla geliştirilmiştir. Çevirilen içeriklerin telif hakkı sahiplerine saygı göstermeniz kullanıcının sorumluluğundadır.

---

*ToriiBatch, toriitranslate.com tarafından geliştirilmemiştir ve desteklenmemektedir.*