# Nexmon CSI ve El Hareketi Algılama Pipeline'ı

Bu doküman, Raspberry Pi 5 üzerinde Nexmon CSI ile kanal durum bilgisi toplayan, WebUI üzerinden canlı görselleştiren ve etiketli verilerle el hareketi algılama modeli eğitmeye hazırlanan sistemin mimarisini açıklar.

## Amaç

Sistemin hedefi iki Raspberry Pi cihazı arasında kontrollü bir Wi-Fi bağlantısı kurup, alıcı tarafta CSI verisini toplamaktır. CSI, kablosuz kanalın zaman içinde nasıl değiştiğini gösterir. İnsan eli, gövdesi veya ortam hareketi sinyalin genlik ve faz örüntüsünü değiştirir. Bu değişimler zaman serisi olarak işlenip `stable`, `hand_motion` gibi sınıflara ayrılabilir.

Bu projede ilk hedef `hand_motion` yani el hareketi algılamadır.

## Cihaz Rolleri

### Alpha

Alpha alıcı cihazdır.

- AX210 Wi-Fi kartı: SSH, WebUI ve yönetim bağlantısı için kullanılır.
- Dahili Broadcom Wi-Fi: Nexmon CSI toplamak için kullanılır.
- WebUI: `http://192.168.1.99:8080`
- CSI arayüzü: `csi0`
- Yönetim arayüzü: `mgmt0`

### Bravo

Bravo verici cihazdır.

- Belirli bir hızda UDP trafik üretir.
- Alpha, Bravo'nun MAC adresine göre filtreleme yapar.
- Bu sayede başka cihazların Wi-Fi trafiği mümkün olduğunca dışarıda bırakılır.

## Veri Akışı

Canlı akış şu sırayla çalışır:

1. Bravo UDP paketleri üretir.
2. Alpha üzerinde Broadcom Wi-Fi Nexmon CSI firmware ile monitor/CSI moduna alınır.
3. Nexmon firmware, Bravo kaynaklı Wi-Fi frame'lerinden CSI çıkarır.
4. CSI verisi UDP `5500` portunda pcap stream olarak yakalanır.
5. WebUI backend bu pcap stream'i okur.
6. Backend CSI paketlerini parse eder.
7. Her paket için amplitüd, RSSI, hareket skoru ve model çıktısı hesaplanır.
8. Tarayıcıya hafifletilmiş canlı veri gönderilir.
9. Tam pcap kaydı ve seçilirse ML dataset kaydı diske yazılır.

## Dosyalar

Alpha tarafındaki ana dosyalar:

```text
/home/admin/csi/start_rx_stream.sh
/home/admin/csi/webui/csi_web.py
/home/admin/csi/webui/index.html
/home/admin/csi/webui/app.js
/home/admin/csi/webui/style.css
/home/admin/csi/models/hand_motion_live_model.json
/home/admin/csi/captures/*.pcap
/home/admin/csi/datasets/*.ndjson
```

Bilgisayar tarafındaki ML araçları:

```text
tools/csi_ml/prepare_dataset.py
tools/csi_ml/train_cnn_lstm.py
tools/csi_ml/requirements.txt
tools/csi_ml/README.md
```

## Kayıt Formatları

### Pcap

`captures/*.pcap` dosyaları ham CSI paketlerini içerir. Bunlar arşiv, yeniden analiz ve doğrulama için saklanır. Büyük olabilirler.

### NDJSON

`datasets/*.ndjson` dosyaları ML eğitimi için küçültülmüş ve etiketlenmiş örnekleri içerir. Her satır ayrı bir JSON objesidir.

İlk satır oturum bilgisidir:

```json
{"type":"session","label":"hand_motion","distanceM":2.0,"tones":128,"feature":"log10_amplitude"}
```

Sonraki satırlar örnektir:

```json
{"type":"sample","label":"hand_motion","motionScore":0.12,"rssi":-51,"amps":[...]}
```

Buradaki `amps`, CSI amplitüd değerlerinin log ölçeğine alınmış ve 128 tona indirgenmiş halidir.

## CSI'dan Özellik Çıkarma

Nexmon CSI paketleri karmaşık sayılar içerir:

```text
H[k] = real[k] + j * imag[k]
```

Her subcarrier için amplitüd hesaplanır:

```text
amplitude[k] = sqrt(real[k]^2 + imag[k]^2)
```

Canlı UI tarafında amplitüdler görselleştirilir. ML dataset tarafında ise amplitüdler log ölçeğine alınır:

```text
feature[k] = log10(max(1, amplitude[k]))
```

Bunun sebebi CSI amplitüdlerinin çok geniş aralıkta değişebilmesidir. Log ölçeği, ani büyük tepe değerlerinin modeli baskılamasını azaltır.

## Hareket Skoru

Backend ardışık CSI amplitüd vektörleri arasındaki farkı ölçer:

```text
motionScore = mean(abs(current_amp - previous_amp)) / mean(previous_amp)
```

Bu değer tek başına nihai model değildir; ama canlı hareket yoğunluğu için hızlı bir göstergedir. Ayrıca geçici canlı el hareketi modelinde pencere özellikleri olarak kullanılır.

## CNN ve LSTM Neden Kullanılır?

CSI verisi iki boyutlu bir yapıya benzer:

- Frekans ekseni: subcarrier/tonlar
- Zaman ekseni: ardışık CSI frame'leri

El hareketi tek bir CSI paketinde her zaman net görünmeyebilir. Asıl bilgi çoğu zaman zaman içinde oluşan örüntüdedir. Bu yüzden model pencerelerle çalışır:

```text
window = 64 frame x 128 tone
```

Bu matris, kısa bir zaman kesitindeki kanal davranışını temsil eder.

### CNN Katmanı

CNN, her frame içindeki subcarrier örüntüsünü öğrenir.

Örneğin:

- belirli tonlarda eşzamanlı artış/azalış
- frekans boyunca dalgalanma şekli
- sinyaldeki lokal bozulmalar

`train_cnn_lstm.py` içinde CNN kısmı kabaca şunu yapar:

1. Her CSI frame'i `1 x tones` sinyal gibi ele alır.
2. 1D convolution ile frekans eksenindeki lokal örüntüleri çıkarır.
3. Pooling ile boyutu küçültür.
4. Her frame için daha kompakt bir temsil üretir.

### LSTM Katmanı

LSTM, CNN'in her frame için çıkardığı temsilleri zaman boyunca işler.

Bu katman şunu öğrenmeye çalışır:

- el hareketi başlıyor mu?
- sinyal dalgalanması düzenli mi?
- hareket kısa bir spike mı yoksa süreklilik taşıyor mu?
- `stable` ile `hand_motion` zaman içinde nasıl ayrışıyor?

Yani CNN frekans örüntüsünü, LSTM zaman örüntüsünü öğrenir.

## Eğitim Pipeline'ı

1. WebUI'da etiket seçilir: örneğin `stable` veya `hand_motion`.
2. `Başlat` ile kayıt alınır.
3. WebUI pcap ve ndjson üretir.
4. NDJSON dosyaları bilgisayara indirilir.
5. `prepare_dataset.py` sabit uzunlukta pencereler üretir.
6. `train_cnn_lstm.py` CNN/LSTM modelini eğitir.

Komutlar:

```bash
mkdir -p data/csi/raw

# WebUI'dan indirilen ndjson dosyaları buraya konur.

python3 -m venv .venv-csi
source .venv-csi/bin/activate
pip install -r tools/csi_ml/requirements.txt

python tools/csi_ml/prepare_dataset.py data/csi/raw -o data/csi/csi_dataset.npz --window 64 --stride 16
python tools/csi_ml/train_cnn_lstm.py data/csi/csi_dataset.npz -o data/csi/csi_cnn_lstm.pt --epochs 30
```

## Canlı WebUI'da Şu An Hangi Model Çalışıyor?

Canlı WebUI'da şu an PyTorch CNN/LSTM doğrudan çalışmıyor. Bunun yerine, `stable` ve `hand_motion` kayıtlarından öğrenilmiş hafif bir model çalışıyor:

```text
/home/admin/csi/models/hand_motion_live_model.json
```

Bu model `hand_motion_feature_logistic_v1` adını taşır.

Pi üzerinde PyTorch çalıştırmak yerine hafif JSON model kullanmamızın sebebi:

- Raspberry Pi tarafını daha stabil tutmak
- WebUI gecikmesini azaltmak
- Kurulum bağımlılığını azaltmak
- Canlı tespit için hızlı prototip elde etmek

Bu model her 32 frame'lik pencere için şu özellikleri kullanır:

```text
motion_mean
motion_max
motion_p90
motion_std
```

Ardından `hand_motionProbability` üretir. Bu olasılık eşik değerinin üstündeyse WebUI:

```text
El hareketi algılandı
```

şeklinde uyarı verir.

Mevcut canlı modelde eşik temkinli seçildi:

```text
threshold = 0.5
```

### Hafif Model Neden "Geçiş Katmanı"?

CNN/LSTM modeli daha güçlüdür; çünkü ham CSI pencere matrisindeki frekans ve zaman örüntülerini öğrenir. Ancak canlı WebUI'da ilk amaç, sistemin uçtan uca çalıştığını düşük gecikmeyle kanıtlamaktır:

```text
CSI stream -> motionScore -> pencere özellikleri -> hafif model -> WebUI uyarısı
```

Bu yüzden canlı sistemde küçük bir lojistik model kullanıldı. Bu model, CNN/LSTM'e geçmeden önce şu işleri doğrular:

- Alpha ve Bravo arasında CSI akışı düzenli geliyor.
- `stable` ve `hand_motion` etiketleri sistemde ayrışabilir sinyal üretiyor.
- WebUI canlı tahmin, confidence ve alarm gösterebiliyor.
- Veri toplama, eğitim ve deployment döngüsü çalışıyor.

Sunumda bunu "CNN/LSTM'e hazırlık için canlı prototip modeli" olarak anlatmak doğru olur.

### Model Dosyası Nasıl Görünüyor?

Canlı model tek bir JSON dosyasıdır. Pi tarafında ekstra ML kütüphanesi gerektirmez:

```json
{
  "model": "hand_motion_feature_logistic_v1",
  "window": 32,
  "threshold": 0.5,
  "featureNames": [
    "motion_mean",
    "motion_max",
    "motion_p90",
    "motion_std"
  ],
  "featureMean": [0.0640, 0.4359, 0.1511, 0.1056],
  "featureStd": [0.0400, 0.3331, 0.1156, 0.0794],
  "weights": [1.8872, 2.1536, -0.6795, 1.2384],
  "bias": 1.4421
}
```

Alanların anlamı:

- `window`: Tahmin için kullanılan son frame sayısıdır. Bu model son 32 `motionScore` değerine bakar.
- `featureNames`: Modelin kullandığı özniteliklerdir.
- `featureMean` ve `featureStd`: Eğitim setinden hesaplanan normalizasyon değerleridir.
- `weights` ve `bias`: Eğitilmiş lojistik model parametreleridir.
- `threshold`: `hand_motion` kararı için olasılık eşiğidir.

### Canlı Modelin Girdisi

Backend her CSI frame'i için önce hareket skorunu hesaplar:

```python
amps = sample["amps"]
motion = 0.0

if self.last_amps and len(self.last_amps) == len(amps):
    denom = max(1.0, sum(self.last_amps) / len(self.last_amps))
    diff = sum(abs(a - b) for a, b in zip(amps, self.last_amps)) / len(amps)
    motion = min(1.0, diff / denom)

self.last_amps = amps
```

Bu kodun yaptığı iş şudur:

```text
motionScore = mean(abs(current_amp - previous_amp)) / mean(previous_amp)
```

Yani model doğrudan ham CSI matrisini değil, ardışık CSI frame'leri arasındaki değişim yoğunluğunu kullanır.

### Pencere Özellikleri

Hafif model tek bir frame'e bakmaz. Son 32 hareket skorunu bir pencere olarak tutar:

```python
window = int(model.get("window", 32))

if self.ml_window.maxlen != window:
    self.ml_window = collections.deque(self.ml_window, maxlen=window)

self.ml_window.append(float(motion))
```

Pencere dolmadan model karar vermez. WebUI bu sırada modeli "hazırlanıyor" olarak gösterir:

```python
if len(self.ml_window) < window:
    return {
        "model": model.get("model"),
        "label": "ısınıyor",
        "active": False,
        "handMotionProbability": 0.0,
        "windowReady": len(self.ml_window),
        "window": window,
    }
```

Pencere dolunca dört öznitelik çıkarılır:

```python
values = list(self.ml_window)
sorted_values = sorted(values)
p90_index = min(len(sorted_values) - 1, int(0.9 * (len(sorted_values) - 1)))
mean = sum(values) / len(values)
variance = sum((value - mean) ** 2 for value in values) / len(values)

features_by_name = {
    "motion_mean": mean,
    "motion_max": max(values),
    "motion_p90": sorted_values[p90_index],
    "motion_std": math.sqrt(variance),
}
```

Bu özniteliklerin yorumu:

- `motion_mean`: Pencere boyunca ortalama hareket enerjisi.
- `motion_max`: Penceredeki en güçlü ani hareket.
- `motion_p90`: Ani tekil tepe değerlerden daha dengeli, üst seviye hareket göstergesi.
- `motion_std`: Hareketin pencere içinde ne kadar dalgalandığı.

### Normalizasyon ve Lojistik Karar

Model, her özniteliği eğitim setindeki ortalama ve standart sapmaya göre normalize eder:

```python
names = model.get("featureNames", [])
raw_features = [features_by_name.get(name, 0.0) for name in names]

feature_mean = model.get("featureMean", [])
feature_std = model.get("featureStd", [])

normalized = [
    (value - feature_mean[idx]) / max(1e-6, feature_std[idx])
    for idx, value in enumerate(raw_features)
]
```

Ardından klasik lojistik regresyon hesabı yapılır:

```python
weights = model.get("weights", [])
bias = float(model.get("bias", 0.0))

logit = sum(value * weights[idx] for idx, value in enumerate(normalized)) + bias
probability = sigmoid(logit)
```

Matematiksel karşılığı:

```text
z = w1*x1 + w2*x2 + w3*x3 + w4*x4 + b
p(hand_motion) = 1 / (1 + e^-z)
```

Burada `x1..x4` normalize edilmiş hareket öznitelikleridir. Sonuç `0` ile `1` arasında bir el hareketi olasılığıdır.

`sigmoid` fonksiyonu backend'de şöyle tanımlıdır:

```python
def sigmoid(value):
    value = max(-30.0, min(30.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))
```

`-30` ve `30` sınırı, aşırı büyük sayılarda taşma riskini azaltır.

### WebUI'a Gönderilen Tahmin

Olasılık eşik değerini geçerse model `hand_motion`, geçmezse `stable` döndürür:

```python
threshold = float(model.get("threshold", 0.5))
active = probability >= threshold

return {
    "model": model.get("model"),
    "label": "hand_motion" if active else "stable",
    "active": active,
    "confidence": probability if active else 1.0 - probability,
    "handMotionProbability": probability,
    "threshold": threshold,
    "features": {
        name: round(raw_features[idx], 5)
        for idx, name in enumerate(names)
    },
    "windowReady": len(self.ml_window),
    "window": window,
}
```

WebUI bu çıktıya göre alarm bandını değiştirir:

```javascript
if (info.active) {
  els.mlAlertTitle.textContent = "El hareketi algılandı";
  els.mlAlertMeta.textContent = `${info.model} · olasılık ${pct}`;
} else {
  els.mlAlertTitle.textContent = "El hareketi yok";
  els.mlAlertMeta.textContent = `${info.model} · hand_motion ${pct}`;
}
```

Bu sayede kullanıcı modelin sadece kararını değil, `hand_motion` olasılığını da canlı olarak görebilir.

### Hafif Model Nasıl Eğitildi?

Bu ilk model, `stable` ve `hand_motion` etiketli geçici kayıtlardan üretildi. Eğitim mantığı sadeleştirilmiş haliyle şöyledir:

```python
def window_features(motion_scores):
    values = list(motion_scores)
    values_sorted = sorted(values)
    p90 = values_sorted[int(0.9 * (len(values_sorted) - 1))]
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return [
        mean,
        max(values),
        p90,
        variance ** 0.5,
    ]
```

Etiketler ikili hale getirilir:

```python
label_to_y = {
    "stable": 0,
    "hand_motion": 1,
}
```

Her kayıt oturumundan hareket skorları çıkarılır, 32 frame'lik pencerelere bölünür ve her pencere için dört özellik hesaplanır:

```python
x = []  # feature vectors
y = []  # 0 stable, 1 hand_motion

for session in sessions:
    scores = session.motion_scores
    label = label_to_y[session.label]

    for start in range(0, len(scores) - 32 + 1, 8):
        window = scores[start:start + 32]
        x.append(window_features(window))
        y.append(label)
```

Eğitimde özellikler standartlaştırılır:

```python
feature_mean = x_train.mean(axis=0)
feature_std = x_train.std(axis=0) + 1e-6

x_train_norm = (x_train - feature_mean) / feature_std
```

Sonra lojistik regresyon şu hedefi öğrenir:

```text
stable       -> düşük hand_motion olasılığı
hand_motion  -> yüksek hand_motion olasılığı
```

Eğitilen parametreler JSON'a yazılır:

```python
model = {
    "model": "hand_motion_feature_logistic_v1",
    "window": 32,
    "threshold": 0.5,
    "featureNames": ["motion_mean", "motion_max", "motion_p90", "motion_std"],
    "featureMean": feature_mean.tolist(),
    "featureStd": feature_std.tolist(),
    "weights": weights.tolist(),
    "bias": float(bias),
}
```

Bu dosya Alpha'ya kopyalandığında backend yeniden başlatılmadan sonraki capture başlangıcında model yüklenebilir.

### İlk Modelin Ölçülen Sonuçları

Geçici `stable` ve `hand_motion` kayıtlarıyla elde edilen ilk canlı model ölçümleri:

```text
trainAccuracy = 0.939
valAccuracy   = 1.000
allAccuracy   = 0.952
allF1         = 0.944
precision     = 1.000
recall        = 0.895
totalWindows  = 42
```

Bu sonuçlar sistemin çalıştığını göstermek için değerlidir; ancak bilimsel/genellenebilir sonuç olarak sunulmamalıdır. Çünkü pencere sayısı azdır ve veri geçici kayıtlardan gelmiştir. Daha güvenilir sonuç için farklı günlerde, farklı mesafelerde ve ayrı test oturumlarında veri toplanmalıdır.

### Hafif Model ile CNN/LSTM Farkı

| Özellik | Hafif canlı model | CNN/LSTM modeli |
| --- | --- | --- |
| Girdi | 32 adet `motionScore` | 64 frame x 128 tone CSI matrisi |
| Öğrendiği şey | Hareket skorunun istatistiksel değişimi | Frekans ve zaman örüntüsü |
| Çalışma yeri | Raspberry Pi WebUI backend | Bilgisayar veya optimize edilmiş Pi inference |
| Bağımlılık | Sadece Python standart kütüphane | PyTorch/TorchScript/ONNX gerekir |
| Avantaj | Hızlı, stabil, düşük gecikme | Daha zengin ve güçlü sınıflandırma |
| Dezavantaj | Ham CSI detayını kullanmaz | Canlıya almak daha fazla mühendislik ister |

Bu nedenle hafif model "canlı doğrulama ve erken prototip", CNN/LSTM ise "asıl öğrenebilir model" olarak düşünülmelidir.

## CNN/LSTM ile Canlı Modele Geçiş

CNN/LSTM eğitim hattı hazırdır; ancak canlıya doğrudan bağlamak için birkaç yol vardır:

1. Modeli Pi üzerinde PyTorch ile çalıştırmak.
2. Modeli TorchScript veya ONNX formatına dönüştürmek.
3. Canlı WebUI backend'i bu modeli yükleyip her pencereye tahmin yaptırmak.
4. Daha güçlü bir bilgisayarda inference servisi çalıştırıp Alpha'dan canlı özellik göndermek.

İlk prototipte hafif model kullandık. Daha sağlıklı veri toplandıktan sonra CNN/LSTM modelini canlıya geçirmek mantıklı olacaktır.

## Sağlıklı Veri Toplama Protokolü

Modelin gerçek dünyada iyi çalışması için veri çeşitliliği gerekir.

Önerilen etiketler:

```text
empty
stable
hand_motion
walk
sit
stand
passage
```

Her etiket için:

- 2 metre, 3 metre, 5 metre mesafelerde kayıt
- en az 30-60 saniye
- aynı hareketin farklı hızları
- farklı el yönleri
- farklı ortam koşulları

`hand_motion` için özellikle:

- Alpha-Bravo hattının ortasında el hareketi
- hattın biraz önünde/arkasında el hareketi
- hızlı/yavaş el hareketi
- kısa/uzun hareket

`stable` için:

- ortamda insan yokken kayıt
- cihazlar sabitken kayıt
- aynı mesafe ve kanal ayarıyla kayıt

## Dikkat Edilmesi Gerekenler

### Mesafe

Mesafe değiştikçe RSSI ve CSI genliği değişir. Bu yüzden sadece 2 metrede eğitilen model 5 metrede daha zayıf çalışabilir.

### Ortam

Duvar, masa, monitör, insan gövdesi ve yansımalar CSI örüntüsünü değiştirir. Aynı hareket farklı ortamda farklı görünebilir.

### Kanal

Kanal değişirse CSI örüntüsü de değişebilir. Eğitim ve canlı kullanım aynı kanal/chanspec ile yapılmalıdır:

```text
48/80
```

### Veri Kaçağı

Aynı kayıt dosyasından hem eğitim hem test pencereleri alınırsa doğruluk olduğundan yüksek görünebilir. Gerçek doğrulama için ayrı oturumlar test seti olarak ayrılmalıdır.

## Mevcut Durum

Şu ana kadar yapılanlar:

- Alpha/Bravo CSI akışı kuruldu.
- AX210 yönetim, Broadcom CSI mimarisi oluşturuldu.
- WebUI canlı görselleştirme eklendi.
- Pcap ve NDJSON kayıtları indirilebilir hale getirildi.
- Kayıt silme eklendi.
- Etiketli dataset üretimi eklendi.
- CNN/LSTM eğitim scriptleri hazırlandı.
- `stable` ve `hand_motion` ile ilk model denemesi yapıldı.
- Canlı WebUI'a hafif `hand_motion` detektörü bağlandı.

WebUI adresi:

```text
http://192.168.1.99:8080
```

## Sonraki Aşamalar

1. Daha düzenli ve uzun dataset topla.
2. Her sınıf için ayrı oturumlar oluştur.
3. Eğitim/validasyon/test ayrımını oturum bazında yap.
4. CNN/LSTM modelini tekrar eğit.
5. Modeli canlı WebUI'a TorchScript/ONNX veya hafif model formatıyla bağla.
6. WebUI'da model versiyonu, confidence, eşik ve son tahmin geçmişini göster.
