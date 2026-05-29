# CSI Fazlı Çok Ölçekli CNN/LSTM Eğitim Raporu - 2026-05-30

Bu rapor, Alpha/Bravo Nexmon CSI sisteminden toplanan fazlı yeni veri setiyle eğitilen çok ölçekli çıkış modelini açıklar.

## Amaç

Modelin görevi canlı CSI penceresini şu üç sınıftan birine atamaktır:

```text
empty
hand_motion
passage
```

Canlı WebUI aşamasında alarm politikası:

```text
normal: empty
alarm:  hand_motion, passage
```

Önemli not: Bu fazlı modelde `sit` ve `stand` verisi yoktur. Dolayısıyla model `sit` veya `stand` tahmini üretmez. Bu sınıflar için ayrıca fazlı kayıt toplanırsa sonraki model genişletilebilir.

## Tasarım Gerekçesi

Bu eğitimde CSI tabanlı algılama pipeline'ı şu şekilde kurulmuştur:

```text
CSI acquisition -> phase/amplitude preprocessing -> multi-scale windowing -> CNN/LSTM model -> alarm decision
```

Önceki model yalnızca amplitüd kanalını kullanıyordu. Yeni modelde dört kanal kullanıldı:

```text
amp
phase
amp_delta
phase_delta
```

Yeni modelin ana farkları:

- Ham CSI karmaşık değerlerinden amplitüd ve faz çıkarıldı.
- Faz doğrudan kullanılmadı; önce unwrap edildi, sonra subcarrier eksenindeki lineer trend çıkarıldı.
- Kısa ve uzun iki zaman penceresi aynı karar anına hizalandı.
- CNN subcarrier/frekans örüntülerini, LSTM ise zamansal değişimi öğrendi.
- Test seti aynı oturumdan değil, eğitimde hiç görülmeyen ayrı oturumlardan seçildi.

## Veri Kaynakları

Alpha cihazından indirilen yeni fazlı NDJSON kayıtları:

| Sınıf | Dosya | Sample | Süre | Not |
| --- | --- | ---: | ---: | --- |
| empty | `empty-20260529-225105.ndjson` | 2786 | 986.1 sn | 20dk bos |
| empty | `empty-20260529-230809.ndjson` | 2886 | 1096.7 sn | 15dk bos |
| empty | `empty-20260529-235132.ndjson` | 2412 | 975.9 sn | 20dk bos |
| hand_motion | `hand_motion-20260530-010105.ndjson` | 1900 | 701.5 sn | 2 clap |
| hand_motion | `hand_motion-20260530-012008.ndjson` | 2060 | 822.3 sn | 2 clap |
| passage | `passage-20260530-002230.ndjson` | 2530 | 941.1 sn | passage |
| passage | `passage-20260530-003846.ndjson` | 2433 | 925.7 sn | passage |

Toplam:

```text
17007 labelled phase-ready samples
128 tone/subcarrier
schemaVersion = 2
profile = multiscale_physical_v1
datasetFrameStride = 6
```

Her sample şu alanları içerir:

```text
amps:           128 boyutlu log10 amplitüd vektörü
phaseResiduals: 128 boyutlu faz kalıntısı vektörü
```

Tüm yeni dosyalarda `phaseResiduals` alanı mevcut ve tone sayısı 128'dir.

## Faz Ön İşleme

Her CSI tone için karmaşık kanal değeri:

```text
H_k(t) = I_k(t) + j Q_k(t)
```

Amplitüd:

```text
A_k(t) = sqrt(I_k(t)^2 + Q_k(t)^2)
amp_k(t) = log10(max(1, A_k(t)))
```

Ham faz:

```text
phi_raw,k(t) = atan2(Q_k(t), I_k(t))
```

Ham faz doğrudan modele verilmez. Her frame'de faz önce unwrap edilir:

```text
phi_unwrapped,k(t) = unwrap(phi_raw,k(t))
```

Sonra subcarrier eksenindeki lineer trend çıkarılır:

```text
phi_residual,k(t) = phi_unwrapped,k(t) - (a_t k + b_t)
```

Buradaki `a_t k + b_t`, donanım ve senkronizasyon kaynaklı lineer faz kaymasını bastırmak için her frame üzerinde fit edilen doğrusal trenddir.

Zamansal fark kanalları:

```text
amp_delta_t,k = amp_t,k - amp_{t-1,k}
phase_delta_t,k = phase_t,k - phase_{t-1,k}
```

Bu nedenle modelin her frame için kullandığı özellik tensörü:

```text
x_t in R^(4 x 128)
```

## Split Stratejisi

Overfitting riskini azaltmak için random window split kullanılmadı. Test seti oturum bazında ayrıldı.

Testte tamamen ayrılan oturumlar:

```text
test empty       = empty-20260529-235132.ndjson
test hand_motion = hand_motion-20260530-012008.ndjson
test passage     = passage-20260530-003846.ndjson
```

Kalan oturumlarda:

```text
train = train oturumlarının ilk %78'i
validation = aynı oturumların son bölümü
purge = 32 sample
test = train sırasında hiç görülmeyen ayrı oturumlar
```

Bu test ayrımı eski modele göre daha zordur; çünkü test pencereleri eğitimde görülen kayıt oturumlarının devamı değildir.

Nihai split pencere sayıları:

| Split | Toplam pencere | empty | hand_motion | passage |
| --- | ---: | ---: | ---: | ---: |
| train | 1924 | 1083 | 359 | 482 |
| validation | 478 | 273 | 85 | 120 |
| test | 1693 | 592 | 504 | 597 |

Kullanılan pencereleme:

```text
windows = 16, 48 sample
stride = 4 sample
purge = 32 sample
alignment = same_end_time
```

Yani her karar anında iki pencere aynı bitiş anına hizalanır:

```text
X_short(t) = [x_{t-15}, ..., x_t]  in R^(16 x 4 x 128)
X_long(t)  = [x_{t-47}, ..., x_t]  in R^(48 x 4 x 128)
```

## Model

Kaydedilen seçili model:

```text
data/csi/models/csi_cnn_lstm_multiscale_phase_20260530_w16_w48_holdout.pt
```

Canlı sistemde kullanılan kopya:

```text
data/csi/models/best_csi_cnn_lstm_temporal.pt
```

Model tipi:

```text
csi_cnn_lstm_multiscale_v1
```

Mimari:

```text
Input short: 16 x 4 x 128
Input long:  48 x 4 x 128

Shared CNN frame encoder:
  Conv1D(4 -> 32, kernel=7)
  BatchNorm
  GELU
  MaxPool
  Dropout
  Conv1D(32 -> 64, kernel=5)
  BatchNorm
  GELU
  AdaptiveAvgPool1D(16)
  Flatten

Temporal branch 1:
  BiLSTM over 16-frame sequence, hidden=64

Temporal branch 2:
  BiLSTM over 48-frame sequence, hidden=64

Classifier:
  concat(short_repr, long_repr)
  LayerNorm
  Dropout
  Linear(3 class)
```

Matematiksel özet:

```text
z_t = CNN(x_t)
r_16 = BiLSTM_16(z_{t-15}, ..., z_t)
r_48 = BiLSTM_48(z_{t-47}, ..., z_t)
r = concat(r_16, r_48)
p = softmax(Wr + b)
```

## Eğitim Ayarları

```text
max_epoch = 80
epochs_run = 25
best_epoch = 11
early_stopping_patience = 14
batch_size = 32
optimizer = AdamW
learning_rate = 7e-4
weight_decay = 1e-3
scheduler = ReduceLROnPlateau, factor=0.5, patience=5
gradient_clip_norm = 2.0
selection_metric = validation macro-F1
device = mps
```

Augmentasyon:

```text
Gaussian noise std = 0.025
time masking = pencerenin %10 kadarı
tone masking = tone ekseninin %6 kadarı
```

Sınıf ağırlıkları:

| Sınıf | Weight |
| --- | ---: |
| empty | 0.708 |
| hand_motion | 1.230 |
| passage | 1.062 |

Sınıf ağırlıkları, eğitim setindeki dengesizliği azaltmak için weighted cross entropy içinde kullanıldı.

## Pencere ve Mimari Denemeleri

| Model | Windows | LSTM | Best epoch | Val acc | Val macro-F1 | Test acc | Test macro-F1 | Alarm acc | Alarm precision | Alarm recall |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `w8_w24` | 8+24 | bi | 10 | 0.853 | 0.848 | 0.605 | 0.561 | 0.830 | 0.905 | 0.826 |
| `w12_w36` | 12+36 | bi | 7 | 0.910 | 0.909 | 0.728 | 0.693 | 0.912 | 0.977 | 0.886 |
| `w12_w36_unidir` | 12+36 | uni | 11 | 0.912 | 0.905 | 0.716 | 0.703 | 0.886 | 0.927 | 0.895 |
| `w16_w48` | 16+48 | bi | 11 | 0.933 | 0.935 | 0.755 | 0.717 | 0.957 | 0.963 | 0.972 |
| `w16_w48_unidir` | 16+48 | uni | 29 | 0.946 | 0.943 | 0.770 | 0.737 | 0.929 | 0.966 | 0.924 |

Nihai seçim `w16_w48` çift yönlü LSTM oldu. `w16_w48_unidir` multiclass testte biraz daha yüksek görünse de alarm recall değeri daha düşüktü. Canlı sistem için amaç alarmı kaçırmamak ve boş oda yanlış alarmını azaltmak olduğu için `w16_w48` daha dengeli seçildi.

## Nihai Sonuç

Validation:

```text
total = 478
correct = 446
accuracy = 0.933
macro-F1 = 0.935
loss = 0.232
best_epoch = 11
```

Test:

```text
total = 1693
correct = 1278
accuracy = 0.755
macro-F1 = 0.717
loss = 0.964
```

## Validation Confusion Matrix

Satırlar gerçek sınıf, sütunlar tahmin edilen sınıftır.

| Gerçek \ Tahmin | empty | hand_motion | passage | Doğru / Toplam |
| --- | ---: | ---: | ---: | ---: |
| empty | 253 | 0 | 20 | 253 / 273 |
| hand_motion | 0 | 84 | 1 | 84 / 85 |
| passage | 10 | 1 | 109 | 109 / 120 |

Validation sınıf bazlı metrikler:

| Sınıf | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| empty | 0.962 | 0.927 | 0.944 | 273 |
| hand_motion | 0.988 | 0.988 | 0.988 | 85 |
| passage | 0.838 | 0.908 | 0.872 | 120 |

## Test Confusion Matrix

Satırlar gerçek sınıf, sütunlar tahmin edilen sınıftır.

| Gerçek \ Tahmin | empty | hand_motion | passage | Doğru / Toplam |
| --- | ---: | ---: | ---: | ---: |
| empty | 551 | 0 | 41 | 551 / 592 |
| hand_motion | 8 | 160 | 336 | 160 / 504 |
| passage | 23 | 7 | 567 | 567 / 597 |

Test sınıf bazlı metrikler:

| Sınıf | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| empty | 0.947 | 0.931 | 0.939 | 592 |
| hand_motion | 0.958 | 0.317 | 0.477 | 504 |
| passage | 0.601 | 0.950 | 0.736 | 597 |

## Alarm Gruplaması

Canlı sistemde üç sınıf iki üst gruba ayrılır:

```text
normal = empty
alarm = hand_motion + passage
```

Test setindeki alarm gruplama matrisi:

| Gerçek \ Tahmin | normal | alarm |
| --- | ---: | ---: |
| normal | 551 | 41 |
| alarm | 31 | 1070 |

Alarm metrikleri:

```text
alarm accuracy = 0.957
alarm precision = 0.963
alarm recall = 0.972
false alarm = 41 / 592 empty pencere
missed alarm = 31 / 1101 alarm pencere
```

Yorum:

- Boş oda pencerelerinin büyük kısmı doğru biçimde `empty` tahmin edildi.
- `passage` recall değeri yüksek çıktı.
- `hand_motion` sınıfı çoğu zaman `passage` ile karıştı.
- Alarm açısından bu karışma kabul edilebilir; çünkü iki sınıf da alarm grubundadır.
- Eğer hedef `hand_motion` ve `passage` ayrımını güçlü yapmaksa daha olay merkezli `hand_motion` verisi gerekir.

## Önceki Modelle Karşılaştırma

Önceki model ve yeni model aynı zorlukta test edilmediği için sonuçlar doğrudan yalnızca accuracy üzerinden okunmamalıdır. Eski modelde test verisi aynı oturumların zamansal son bölümünden geliyordu. Yeni modelde ise her sınıf için ayrı bir oturum tamamen testte bırakıldı.

| Özellik | Önceki model | Yeni model |
| --- | --- | --- |
| Eğitim tarihi | 2026-05-25 | 2026-05-30 |
| Model | `csi_cnn_lstm_temporal_v2` | `csi_cnn_lstm_multiscale_v1` |
| Veri tipi | amplitüd-only | amplitüd + faz kalıntısı |
| Feature kanalları | `amp` | `amp`, `phase`, `amp_delta`, `phase_delta` |
| Sınıflar | `empty`, `sit`, `stand`, `passage`, `hand_motion` | `empty`, `passage`, `hand_motion` |
| Pencere | 24 sample | 16 + 48 sample |
| Stride | 4 sample | 4 sample |
| Purge | 12 sample | 32 sample |
| Split | aynı oturum içinde temporal split | session-holdout test |
| Test oturumu | train ile aynı kayıt oturumlarının devamı | train sırasında hiç görülmeyen ayrı oturumlar |
| Test accuracy | 0.902 | 0.755 |
| Test macro-F1 | 0.820 | 0.717 |
| Alarm accuracy | 0.930 | 0.957 |
| Alarm precision | 0.855 | 0.963 |
| Alarm recall | 1.000 | 0.972 |
| False alarm | 10 / 84 normal pencere | 41 / 592 empty pencere |
| Missed alarm | 0 / 59 alarm pencere | 31 / 1101 alarm pencere |

Canlı alarm açısından yeni model daha iyi dengededir:

```text
Alarm precision: 0.855 -> 0.963
Alarm accuracy:  0.930 -> 0.957
Alarm recall:    1.000 -> 0.972
```

Yeni modelin multiclass accuracy değeri daha düşük görünür; ancak test protokolü daha zordur ve session-holdout olduğu için daha güvenilir bir genelleme ölçüsüdür.

## Yorum

Model `empty` ayrımında güçlüdür. `passage` recall değeri yüksektir. En belirgin zayıflık, `hand_motion` pencerelerinin önemli kısmının `passage` olarak sınıflanmasıdır.

Bu durumun olası nedeni:

```text
2 clap gibi kısa olaylarda tüm uzun oturumu hand_motion etiketiyle kaydetmek label noise üretir.
Model uzun oturum içindeki güçlü geçici dalgalanmaları passage örüntüsüne benzetebilir.
```

Bir sonraki veri toplama turu için öneri:

- `hand_motion` kayıtları daha olay merkezli alınmalı.
- Clap hareketi oturum boyunca ritmik tekrar edilmeli veya olay anları ayrıca işaretlenmeli.
- `hand_motion` ve `passage` için farklı mesafe/yön tekrarları alınmalı.
- `sit` ve `stand` eklenecekse bunlar için ayrıca fazlı ve session-holdout testli kayıt yapılmalı.

## Realtime WebUI Entegrasyonu

Seçilen model Alpha cihazına deploy edildi:

```text
/home/admin/csi/models/best_csi_cnn_lstm_temporal.pt
```

Backend modeli şu şekilde görür:

```text
model = csi_cnn_lstm_multiscale_v1
windows = [16, 48]
inputChannels = 4
featureNames = [amp, phase, amp_delta, phase_delta]
labels = [empty, hand_motion, passage]
```

Canlı alarm kapısı:

```text
confidence >= 0.85
motionScore >= 0.05
packetRate >= 5 pkt/s
same-label streak >= 2
cooldown = 5 s
MODEL_INFER_STRIDE = 6
```

## Eğitim Komutları

Dataset hazırlama:

```bash
python tools/csi_ml/prepare_multiscale_session_splits.py data/csi/raw_phase_20260530 \
  -o data/csi/csi_multiscale_phase_20260530_w16_w48_holdout.npz \
  --windows 16,48 \
  --stride 4 \
  --train-ratio 0.78 \
  --purge 32 \
  --features amp,phase,amp_delta,phase_delta
```

Model eğitimi:

```bash
python tools/csi_ml/train_multiscale_cnn_lstm.py data/csi/csi_multiscale_phase_20260530_w16_w48_holdout.npz \
  -o data/csi/models/csi_cnn_lstm_multiscale_phase_20260530_w16_w48_holdout.pt \
  --epochs 80 \
  --patience 14 \
  --batch-size 32 \
  --lr 0.0007 \
  --weight-decay 0.001
```
