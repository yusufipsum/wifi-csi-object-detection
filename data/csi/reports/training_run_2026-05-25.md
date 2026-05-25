# CSI CNN/LSTM Eğitim Raporu - 2026-05-25

Bu rapor, Alpha/Bravo Nexmon CSI sisteminden toplanan ilk çok sınıflı veri setiyle eğitilen çıkış modelini açıklar.

## Amaç

Modelin görevi canlı CSI penceresini şu sınıflardan birine atamaktır:

```text
empty
sit
stand
passage
hand_motion
```

Canlı WebUI aşamasında bu etiketler alarm kayıtları için kullanılacaktır. Önerilen alarm politikası:

```text
normal: empty, sit, stand
alarm:  passage, hand_motion
```

## Makaleden Alınan Tasarım Gerekçesi

`Makale.pdf` içindeki CSI tabanlı insan algılama taramasında tipik pipeline şu şekilde tarif ediliyor:

```text
CSI acquisition -> preprocessing -> model design -> sensing analysis
```

Bu eğitimde aynı fikir izlendi:

- CSI amplitüdleri `log10` ölçeğinde kullanıldı.
- Sürekli CSI akışı sliding window ile segmentlere bölündü.
- CNN, subcarrier/frekans eksenindeki lokal örüntüleri çıkarmak için kullanıldı.
- LSTM, pencere içindeki zamansal değişimi modellemek için kullanıldı.
- Veri azlığı ve sınıf dengesizliği için class weight ve hafif augmentasyon kullanıldı.

Makalede vurgulanan sınırlamalar bu veri seti için de geçerli: sınırlı veri, tek ortam, tek oturum/sınıf ve domain generalization riski.

## Veri Kaynakları

Alpha cihazından indirilen NDJSON kayıtları:

| Sınıf | Dosya | Sample | Süre |
| --- | --- | ---: | ---: |
| empty | `empty-20260525-001442.ndjson` | 1246 | 3328.0 sn |
| sit | `sit-20260525-011707.ndjson` | 338 | 902.1 sn |
| stand | `stand-20260525-013521.ndjson` | 594 | 1182.7 sn |
| hand_motion | `hand_motion-20260525-015623.ndjson` | 394 | 874.5 sn |
| passage | `passage-20260525-021502.ndjson` | 1121 | 935.9 sn |

Her örnek 128 CSI tone/subcarrier içeriyor.

## Split Stratejisi

Eldeki veri her sınıf için tek oturumdan oluştuğu için klasik random split kullanılmadı. Random split aynı oturumdaki çok benzer komşu pencereleri hem eğitim hem test tarafına sokabilir ve sonucu olduğundan iyi gösterir.

Bu yüzden temporal split kullanıldı:

```text
train: %60
validation: %20
test: %20
purge gap: pencere uzunluğunun yarısı
```

Seçilen nihai pencere:

```text
window = 24 sample
stride = 4 sample
purge = 12 sample
```

Nihai split sayıları:

| Split | Toplam pencere | empty | hand_motion | passage | sit | stand |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 527 | 181 | 54 | 163 | 45 | 84 |
| validation | 143 | 54 | 11 | 48 | 9 | 21 |
| test | 143 | 54 | 11 | 48 | 9 | 21 |

Bu test ayrımı tamamen bağımsız oturum testi değildir; aynı oturumların zaman bakımından son bölümü test olarak ayrılmıştır. Bilimsel olarak daha güçlü doğrulama için her sınıftan ayrı gün/oturum test kaydı alınmalıdır.

## Model

Kaydedilen model:

```text
data/csi/models/best_csi_cnn_lstm_temporal.pt
```

Model tipi:

```text
csi_cnn_lstm_temporal_v2
```

Mimari:

```text
Input: 24 x 128 CSI amplitude window
CNN: Conv1D -> BatchNorm -> GELU -> MaxPool -> Conv1D -> BatchNorm -> GELU -> AdaptiveAvgPool
Temporal layer: BiLSTM hidden=64
Classifier: LayerNorm -> Dropout -> Linear(5 class)
```

Eğitim ayarları:

```text
max_epoch = 80
early_stopping_patience = 12
batch_size = 32
optimizer = AdamW
learning_rate = 7e-4
weight_decay = 1e-3
class_weight = inverse frequency sqrt normalization
augmentation = Gaussian noise + time masking + tone masking
selection_metric = validation macro-F1
```

Sınıf ağırlıkları:

| Sınıf | Weight |
| --- | ---: |
| empty | 0.680 |
| hand_motion | 1.244 |
| passage | 0.716 |
| sit | 1.363 |
| stand | 0.997 |

## Pencere Boyutu Denemeleri

| Window | Epoch run | Best epoch | Val acc | Val macro-F1 | Test acc | Test macro-F1 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 12 | 28 | 16 | 0.903 | 0.862 | 0.807 | 0.717 |
| 16 | 39 | 27 | 0.949 | 0.932 | 0.892 | 0.818 |
| 20 | 35 | 23 | 0.960 | 0.935 | 0.821 | 0.700 |
| 24 | 38 | 26 | 0.979 | 0.958 | 0.902 | 0.820 |
| 32 | 44 | 30 | 0.984 | 0.961 | 0.898 | 0.727 |
| 40 | 21 | 9 | 0.991 | 0.971 | 0.841 | 0.575 |

Nihai seçim `window=24` oldu. Daha uzun pencereler validasyonda iyi görünse de temporal testte `sit/stand` ayrımı zayıfladı.

## Nihai Sonuç

Validation:

```text
accuracy = 0.979
macro-F1 = 0.958
best_epoch = 26
```

Test:

```text
accuracy = 0.902
macro-F1 = 0.820
```

Test confusion matrix:

Satırlar gerçek sınıf, sütunlar tahmin edilen sınıftır.

| Gerçek \ Tahmin | empty | hand_motion | passage | sit | stand |
| --- | ---: | ---: | ---: | ---: | ---: |
| empty | 54 | 0 | 0 | 0 | 0 |
| hand_motion | 0 | 8 | 3 | 0 | 0 |
| passage | 0 | 0 | 48 | 0 | 0 |
| sit | 0 | 2 | 1 | 5 | 1 |
| stand | 0 | 2 | 5 | 0 | 14 |

Sınıf bazlı test F1:

| Sınıf | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| empty | 1.000 | 1.000 | 1.000 |
| hand_motion | 0.667 | 0.727 | 0.696 |
| passage | 0.842 | 1.000 | 0.914 |
| sit | 1.000 | 0.556 | 0.714 |
| stand | 0.933 | 0.667 | 0.778 |

Alarm gruplaması açısından:

```text
normal = empty + sit + stand
alarm  = passage + hand_motion
```

Bu gruplamada test sonucu:

```text
alarm accuracy = 0.930
alarm recall   = 1.000
alarm precision = 0.855
false alarm = 10 / 84 normal pencere
missed alarm = 0 / 59 alarm pencere
```

## Yorum

Model `empty` ve `passage` sınıflarını çok iyi ayırıyor. En belirgin hata `stand -> passage` ve `sit -> hand_motion/passage` yönünde. Bu, sabit insan pozisyonlarının bazı temporal bölümlerde hareketli sınıflara benzediğini gösteriyor.

Bu model realtime deneme için uygundur, ancak nihai akademik/ürün modeli olarak görülmemelidir. Bir sonraki veri toplama turunda özellikle şu kayıtlar gerekli:

- `sit` için farklı oturuş yönleri ve daha uzun kayıt
- `stand` için Alpha-Bravo hattı üzerinde ve dışında ayrı kayıt
- `hand_motion` için curl hareketinin farklı hızları
- Her sınıf için en az bir bağımsız test oturumu

## Realtime WebUI Entegrasyon Planı

Canlı sistemde yapılacak adımlar:

1. Backend canlı CSI amplitüdlerini 128 tone olacak şekilde ring buffer'a alır.
2. Son 24 örnek aynı eğitimdeki gibi pencere haline getirilir.
3. Pencere tone bazında normalize edilir.
4. Model `label + confidence` üretir.
5. WebUI anlık etiketi gösterir.
6. `passage` ve `hand_motion` sınıfları alarm olayı olarak kaydedilir.
7. Alarm kayıtları WebUI'da listelenir, indirilebilir ve silinebilir olur.

Önerilen alarm kaydı alanları:

```json
{
  "ts": 1779650000000,
  "label": "passage",
  "confidence": 0.94,
  "sourceMac": "88:a2:9e:5d:4e:a6",
  "seq": 12345,
  "rssi": -47,
  "motionScore": 0.11,
  "model": "csi_cnn_lstm_temporal_v2",
  "window": 24
}
```

Alarm tekrarını azaltmak için aynı etiket için 3-5 saniyelik cooldown önerilir.
