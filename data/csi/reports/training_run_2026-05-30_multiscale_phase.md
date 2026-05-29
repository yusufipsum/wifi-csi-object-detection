# CSI Multiscale Phase Training Run - 2026-05-30

## Dataset

Yeni fazlı kayıtlar:

| Label | Dosya | Sample | Not |
| --- | ---: | ---: | --- |
| empty | 3 | 8084 | 2026-05-29/30 boş oda |
| hand_motion | 2 | 3960 | `2 clap` |
| passage | 2 | 4963 | giriş/çıkış/geçiş |

Toplam:

```text
17007 labelled phase-ready samples
128 tone/subcarrier
features = amp, phase, amp_delta, phase_delta
profile = multiscale_physical_v1
```

Tüm yeni dosyalarda `phaseResiduals` alanı mevcut ve tone sayısı 128.

## Split Strategy

Overfitting riskini azaltmak için random window split kullanılmadı. Test seti oturum bazında ayrıldı:

```text
test empty       = empty-20260529-235132.ndjson
test hand_motion = hand_motion-20260530-012008.ndjson
test passage     = passage-20260530-003846.ndjson
```

Kalan oturumlarda:

```text
train = her train oturumunun ilk %78'i
val   = aynı oturumların son bölümü
purge = 32 sample
test  = train sırasında hiç görülmeyen ayrı oturumlar
```

Bu nedenle test metriği, aynı kaydın komşu pencerelerini ezberleme etkisine karşı daha dürüsttür.

## Candidates

| Model | Windows | LSTM | Best epoch | Val acc | Val macro-F1 | Test acc | Test macro-F1 | Alarm acc | Alarm precision | Alarm recall |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `w8_w24` | 8+24 | bi | 10 | 0.853 | 0.848 | 0.605 | 0.561 | 0.830 | 0.905 | 0.826 |
| `w12_w36` | 12+36 | bi | 7 | 0.910 | 0.909 | 0.728 | 0.693 | 0.912 | 0.977 | 0.886 |
| `w12_w36_unidir` | 12+36 | uni | 11 | 0.912 | 0.905 | 0.716 | 0.703 | 0.886 | 0.927 | 0.895 |
| `w16_w48` | 16+48 | bi | 11 | 0.933 | 0.935 | 0.755 | 0.717 | 0.957 | 0.963 | 0.972 |
| `w16_w48_unidir` | 16+48 | uni | 29 | 0.946 | 0.943 | 0.770 | 0.737 | 0.929 | 0.966 | 0.924 |

## Selected Model

Canlı alarm sistemi için seçilen model:

```text
data/csi/models/csi_cnn_lstm_multiscale_phase_20260530_w16_w48_holdout.pt
```

Seçim nedeni:

```text
En iyi alarm dengesi: precision 0.963, recall 0.972
Aktif olay kaçırma oranı düşük: 31 / 1101 alarm penceresi
Boş oda yanlış alarmı kontrollü: 41 / 592 empty penceresi
```

## Comparison With Previous Model

Önceki model ve yeni model aynı zorlukta test edilmediği için sonuçlar doğrudan "hangisi daha yüksek accuracy verdi?" diye okunmamalıdır. Eski modelde test verisi aynı oturumların zamansal son bölümünden geliyordu. Yeni modelde ise her sınıf için ayrı bir oturum tamamen testte bırakıldı.

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

Yorum:

```text
Önceki model multiclass test accuracy açısından daha yüksek görünür.
Ancak eski test aynı oturum içindeki komşu zaman parçalarından geldiği için daha kolaydır.
Yeni model daha zor ve daha gerçekçi session-holdout testte değerlendirildi.
```

Canlı alarm açısından yeni model daha iyi dengededir:

```text
Alarm precision: 0.855 -> 0.963
Alarm accuracy:  0.930 -> 0.957
Alarm recall:    1.000 -> 0.972
```

Bu, yeni modelin boş oda yanlış alarmlarını azaltma tarafında daha güçlü olduğunu gösterir. Buna karşılık `hand_motion` ile `passage` arasındaki ince sınıf ayrımı henüz yeterince güçlü değildir; `hand_motion` pencerelerinin önemli bölümü `passage` olarak tahmin edilmektedir. Alarm sistemi açısından ikisi de aktif sınıf olduğundan bu kabul edilebilir, fakat spesifik el hareketi sınıflandırması için daha olay merkezli `hand_motion` verisi gerekir.

## Selected Test Confusion Matrix

Label sırası:

```text
empty, hand_motion, passage
```

Confusion matrix:

```text
[[551,   0,  41],
 [  8, 160, 336],
 [ 23,   7, 567]]
```

Class metrics:

| Label | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| empty | 0.947 | 0.931 | 0.939 | 592 |
| hand_motion | 0.958 | 0.317 | 0.477 | 504 |
| passage | 0.601 | 0.950 | 0.736 | 597 |

## Interpretation

Model `empty` ayrımında güçlü. `passage` recall yüksek. `hand_motion` pencerelerinin önemli kısmı `passage` olarak sınıflanıyor. Bu canlı alarm açısından kabul edilebilir; çünkü `hand_motion` ve `passage` aynı alarm grubunda. Ancak sunumda bu ayrım açık belirtilmelidir:

```text
Bu eğitim turu aktif hareket algılama için güçlüdür.
Hand motion ile passage arasındaki ince ayrım için daha fazla, daha kısa ve olay merkezli hand_motion kaydı gerekir.
```

Özellikle `2 clap` gibi kısa olaylarda, tüm uzun oturumu `hand_motion` etiketiyle kaydetmek label noise üretir. Daha iyi hand/passage ayrımı için clap hareketi her 2-3 saniyede ritmik tekrarlanmalı veya olay anları ayrıca işaretlenmelidir.

## Training Command

```bash
python tools/csi_ml/prepare_multiscale_session_splits.py data/csi/raw_phase_20260530 \
  -o data/csi/csi_multiscale_phase_20260530_w16_w48_holdout.npz \
  --windows 16,48 \
  --stride 4 \
  --train-ratio 0.78 \
  --purge 32 \
  --features amp,phase,amp_delta,phase_delta

python tools/csi_ml/train_multiscale_cnn_lstm.py data/csi/csi_multiscale_phase_20260530_w16_w48_holdout.npz \
  -o data/csi/models/csi_cnn_lstm_multiscale_phase_20260530_w16_w48_holdout.pt \
  --epochs 80 \
  --patience 14 \
  --batch-size 32 \
  --lr 0.0007 \
  --weight-decay 0.001
```
