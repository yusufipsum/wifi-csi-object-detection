# Wi-Fi CSI Projesi: Algoritma ve Matematik Notu

Bu not, projede kullanılan ML dataset'i ve CNN/LSTM tabanlı sınıflandırma modelini matematiksel açıdan açıklamak için hazırlanmıştır. Hedef okuyucu, CSI verisini zaman-frekans matrisi olarak inceleyecek ve bu veri üzerinden araştırma/yazı hazırlayacak bir matematikçi veya sinyal işleme araştırmacısıdır.

## 1. Fiziksel Problem

İki Raspberry Pi cihazı arasında kontrollü bir Wi-Fi bağlantısı kurulur:

- Bravo: verici
- Alpha: alıcı
- Alpha, Nexmon CSI ile Wi-Fi paketlerinden kanal durum bilgisini toplar.

Wi-Fi OFDM yapısında sinyal birçok alt taşıyıcıya, yani subcarrier/tone bileşenine ayrılır. Her zaman anında ve her subcarrier için kablosuz kanal yaklaşık şu şekilde modellenebilir:

```text
y_k(t) = H_k(t) x_k(t) + n_k(t)
```

Burada:

- `x_k(t)`: k. subcarrier'da gönderilen sembol
- `y_k(t)`: alıcıda gözlenen sembol
- `H_k(t)`: kanal frekans cevabı
- `n_k(t)`: gürültü
- `t`: zaman/frame indeksi
- `k`: subcarrier/tone indeksi

CSI değeri temelde `H_k(t)` hakkında ölçüm sağlar. Karmaşık sayı olarak düşünülebilir:

```text
H_k(t) = |H_k(t)| exp(j phi_k(t))
```

Bu projedeki ML dataset'te yalnızca amplitüd tarafı kullanılmıştır:

```text
|H_k(t)|
```

İlk eğitilen modelde faz bilgisi kullanılmamıştır. Bunun sebebi, Wi-Fi CSI fazının donanım saat kayması, CFO/SFO, paket başlangıç kayması ve sürücü kaynaklı ofsetlerden ciddi şekilde etkilenebilmesidir. Fazı güvenilir kullanmak için ayrıca kalibrasyon gerekir.

Yeni geliştirme hattında faz doğrudan ham haliyle değil, şu kalibrasyonla kaydedilir:

```text
phi_raw,k(t) = atan2(Q_k(t), I_k(t))
phi_unwrapped,k(t) = unwrap(phi_raw,k(t))
phi_residual,k(t) = phi_unwrapped,k(t) - (a_t k + b_t)
```

Burada `a_t k + b_t`, her frame için subcarrier eksenindeki lineer faz trendidir. Bu trend çıkarılınca `phaseResiduals` alanı elde edilir. Amaç, donanım/senkronizasyon kaynaklı lineer faz kaymasını azaltıp hareketin bıraktığı göreli faz desenini modele verebilmektir.

## 2. Multipath Yorumu

Kapalı ortamda alıcıya gelen sinyal tek bir yol üzerinden gelmez. Duvar, masa, insan gövdesi, el ve diğer nesnelerden yansıyan birçok bileşen vardır. Kanal cevabı kabaca şu toplamla düşünülebilir:

```text
H_k(t) = sum_l alpha_l(t) exp(-j 2 pi f_k tau_l(t))
```

Burada:

- `l`: yol/multipath bileşeni
- `alpha_l(t)`: o yolun genlik katsayısı
- `tau_l(t)`: o yolun gecikmesi
- `f_k`: k. subcarrier frekansı

İnsan hareketi olduğunda bazı `alpha_l(t)` ve `tau_l(t)` değerleri değişir. Bu değişim, subcarrier'lar boyunca farklı genlik dalgalanmaları üretir. Modelin öğrenmeye çalıştığı şey doğrudan "insanın geometrisi" değil, bu hareketin CSI amplitüd matrisinde bıraktığı istatistiksel ve zamansal izdir.

Önemli sınırlama: Sadece amplitüd kullanarak "doğrudan gelen sinyal" ile "yansıyan sinyal"i fiziksel olarak kusursuz ayırmıyoruz. Model, etiketli verideki örüntüleri öğrenerek sınıflandırma yapıyor. Daha fiziksel ayrıştırma için faz kalibrasyonu, ToF/AoA tahmini, Doppler analizi veya anten dizisi gerekir.

## 3. Dataset Formatı

ML dataset dosyaları:

```text
data/csi/raw/*.ndjson
```

Toplanan sınıflar:

```text
empty
sit
stand
passage
hand_motion
```

Bu beş sınıflı liste 2026-05-25 tarihli eski amplitüd-only dataset'e aittir. 2026-05-30 tarihli fazlı yeni modelde `sit` ve `stand` verisi toplanmamıştır; yeni fazlı model yalnızca `empty`, `hand_motion` ve `passage` sınıflarıyla eğitilmiştir.

İlk dataset'te her `sample` satırında ana girdi:

```text
amps: 128 boyutlu log-amplitüd vektörü
```

Bu kritik bir ayrıntıdır: Dataset'teki `amps` ham amplitüd değil, kayıt sırasında log ölçeğine alınmış amplitüddür:

```text
x_t,k = log10(max(1, A_t,k))
```

Burada:

- `A_t,k`: ham CSI amplitüdü
- `x_t,k`: ML modelinin kullandığı log-amplitüd değeri

Log dönüşümünün amacı, çok büyük genlik tepe değerlerinin modeli baskılamasını azaltmak ve değer aralığını sıkıştırmaktır.

Yeni kaydedilecek dataset'te buna ek olarak şu alan bulunur:

```text
phaseResiduals: 128 boyutlu lineer trendden arındırılmış faz vektörü
```

Bu sayede çok kanallı model şu özelliklerle eğitilebilir:

```text
amp
phase
amp_delta
phase_delta
```

Burada `delta`, ardışık örnekler arasındaki farktır:

```text
amp_delta_t,k = amp_t,k - amp_{t-1,k}
phase_delta_t,k = phase_t,k - phase_{t-1,k}
```

Mevcut veri sayıları:

| Sınıf | Sample sayısı | Tone sayısı |
| --- | ---: | ---: |
| empty | 1246 | 128 |
| sit | 338 | 128 |
| stand | 594 | 128 |
| hand_motion | 394 | 128 |
| passage | 1121 | 128 |

## 4. Zaman-Frekans Matrisi

Tek bir CSI örneği şu vektördür:

```text
x_t = [x_t,1, x_t,2, ..., x_t,128] in R^128
```

Model tek bir vektöre bakmaz. Ardışık 24 örneği bir pencere yapar:

```text
X_s =
[
  x_s
  x_{s+1}
  ...
  x_{s+23}
] in R^(24 x 128)
```

Bu matrisin:

- satırları zaman ekseni
- sütunları subcarrier/tone eksenidir.

Bu yüzden problem doğal olarak iki eksenli bir örüntü tanıma problemidir:

```text
zaman x frekans
```

## 5. Pencereleme

Eğitimde kullanılan pencere parametreleri:

```text
window = 24 sample
stride = 4 sample
```

Yani birinci pencere `[0, 23]`, ikinci pencere `[4, 27]`, üçüncü pencere `[8, 31]` gibi ilerler.

Pencereler örtüşür. Bu, küçük dataset'te daha fazla eğitim örneği üretir; fakat ardışık pencereler birbirine benzediği için test ayrımında dikkat gerektirir.

Bu nedenle random split yerine temporal split kullanıldı.

## 6. Temporal Split

Her sınıf kaydı zaman sırasına göre bölündü:

```text
train:      ilk %60
validation: sonraki %20
test:       son %20
```

Train/validation/test sınırları arasında purge gap kullanıldı:

```text
purge = 12 sample
```

Amaç, pencere örtüşmesi yüzünden train ve test tarafına neredeyse aynı zaman kesitlerinin düşmesini azaltmaktır.

Nihai pencere sayıları:

| Split | Toplam | empty | hand_motion | passage | sit | stand |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 527 | 181 | 54 | 163 | 45 | 84 |
| validation | 143 | 54 | 11 | 48 | 9 | 21 |
| test | 143 | 54 | 11 | 48 | 9 | 21 |

Bu hâlâ tam bağımsız bir deney değildir; aynı oturumların farklı zaman parçalarıdır. Matematiksel/istatistiksel yorumda bu sınırlama özellikle belirtilmelidir.

## 7. Pencere Normalizasyonu

Her pencere kendi içinde tone bazında normalize edilir. Bir pencere:

```text
X in R^(W x K)
W = 24
K = 128
```

Her tone için ortalama:

```text
mu_k = (1 / W) sum_{t=1..W} X_t,k
```

Her tone için standart sapma:

```text
sigma_k = sqrt((1 / W) sum_{t=1..W} (X_t,k - mu_k)^2)
```

Normalize değer:

```text
Z_t,k = (X_t,k - mu_k) / (sigma_k + epsilon)
epsilon = 1e-6
```

Bu işlem her subcarrier'ın pencere içindeki göreli değişimini öne çıkarır. Mutlak sinyal seviyesi yerine, kısa zaman aralığındaki dalgalanma deseni vurgulanır.

## 8. Modelin Matematiksel Yapısı

Model adı:

```text
csi_cnn_lstm_temporal_v2
```

Girdi:

```text
Z in R^(24 x 128)
```

Çıktı:

```text
p in R^5
```

Burada `p_j`, pencerenin j. sınıfa ait olma olasılığıdır:

```text
p = softmax(logits)
```

Sınıflar:

```text
[empty, hand_motion, passage, sit, stand]
```

### 8.1 CNN Encoder

CNN her zaman satırını yani her CSI frame'ini ayrı bir 1D sinyal gibi işler:

```text
z_t = CNN(Z_t)       where Z_t in R^128
```

CNN'in görevi subcarrier eksenindeki lokal örüntüleri çıkarmaktır:

- bazı tone bölgelerinde eşzamanlı artış/azalış
- frekans boyunca ripple/dalgalanma
- lokal bozulmalar
- belirli subcarrier gruplarında enerji değişimi

Mimari:

```text
Conv1D(1 -> 32, kernel=7)
BatchNorm
GELU
MaxPool
Dropout
Conv1D(32 -> 64, kernel=5)
BatchNorm
GELU
AdaptiveAvgPool1D(16)
Flatten
```

Adaptive pooling sonrası her zaman adımı için yaklaşık şu boyutta temsil oluşur:

```text
z_t in R^(64 * 16) = R^1024
```

Bu temsil, ham 128 tone vektörünün öğrenilmiş frekans özetidir.

### 8.2 BiLSTM Temporal Katman

CNN her frame için bir temsil üretir:

```text
z_1, z_2, ..., z_24
```

LSTM bu diziyi zaman boyunca işler:

```text
h_t = LSTM(z_t, h_{t-1})
```

Bu projede bidirectional LSTM kullanıldı. Yani model pencereyi hem ileri hem geri yönde okur:

```text
h_forward = LSTM_forward(z_1 -> z_24)
h_backward = LSTM_backward(z_24 -> z_1)
```

Son temsil:

```text
r = concat(h_forward_last, h_backward_last)
```

Hidden size 64 olduğu için bidirectional çıktı yaklaşık:

```text
r in R^128
```

LSTM'in görevi, hareketin zaman içindeki izini öğrenmektir. Örneğin:

- el hareketi kısa ve ritmik dalgalanma üretebilir
- passage daha geniş ve güçlü bir zamansal bozulma olabilir
- sit/stand daha düşük frekanslı veya daha durağan değişimler içerebilir
- empty sınıfında normalize pencere daha sakin kalır

### 8.3 Sınıflandırıcı

LSTM çıktısı şu katmandan geçer:

```text
LayerNorm -> Dropout -> Linear(5)
```

Lineer katman logits üretir:

```text
o = W r + b
```

Softmax:

```text
p_j = exp(o_j) / sum_i exp(o_i)
```

Tahmin:

```text
y_hat = argmax_j p_j
confidence = max_j p_j
```

### 8.4 Çok Ölçekli CNN/LSTM Genişletmesi

Tez hattında model tek pencereye bağlı kalmaz. Aynı karar anı için iki pencere kullanılır:

```text
W_short = 16
W_long  = 48
K = 128 tone
C = feature channel sayısı
```

Yeni fazlı modelde kanal sayısı:

```text
C = 4
feature channels = [amp, phase, amp_delta, phase_delta]
```

Her karar anı `t` için iki tensör oluşturulur:

```text
X_short(t) in R^(16 x C x 128)
X_long(t)  in R^(48 x C x 128)
```

Bu iki pencere aynı bitiş anına hizalanır:

```text
X_short(t) = [x_{t-15}, ..., x_t]
X_long(t)  = [x_{t-47}, ..., x_t]
```

Yani kısa ve uzun model dalları farklı olaylara değil, aynı anın farklı zaman bağlamlarına bakar. Kısa dal el hareketi gibi hızlı değişimleri yakalamaya, uzun dal ise `passage`, boş oda stabilitesi ve yanlış alarm bastırma bağlamını ayırmaya çalışır.

Her frame ortak CNN encoder'dan geçirilir:

```text
z_tau = E(x_tau)
E: R^(C x 128) -> R^1024
```

Sonra iki ayrı BiLSTM dalı kullanılır:

```text
r_16 = BiLSTM_16(E(X_short))
r_48 = BiLSTM_48(E(X_long))
```

Son temsil bu iki bağlamın birleşimidir:

```text
r = concat(r_16, r_48)
```

Sınıflandırıcı:

```text
o = W r + b
p = softmax(o)
```

Bu yapı pratikte şu soruyu sorar:

```text
"Son birkaç saniyede hızlı bir el izi var mı?"
"Aynı anda daha uzun bağlam boş oda veya passage ile uyumlu mu?"
```

Bu yüzden çok ölçekli model, sadece mikro hareketi büyütmek yerine, mikro hareketi makro bağlamla birlikte sınar. Bu boş oda false positive problemini azaltmak için daha savunulabilir bir yoldur.

## 9. Kayıp Fonksiyonu

Sınıf dengesizliği olduğu için weighted cross entropy kullanıldı:

```text
L = -(1 / N) sum_{n=1..N} w_{y_n} log p_{n, y_n}
```

Burada:

- `N`: batch içindeki örnek sayısı
- `y_n`: gerçek sınıf
- `p_{n, y_n}`: modelin gerçek sınıfa verdiği olasılık
- `w_{y_n}`: sınıf ağırlığı

Sınıf ağırlıkları yaklaşık ters frekans mantığıyla üretildi ve karekökle yumuşatıldı:

```text
w_c proportional sqrt(total / class_count_c)
```

Sonra ağırlıkların ortalaması 1 civarına normalize edildi.

Kullanılan sınıf ağırlıkları:

| Sınıf | Weight |
| --- | ---: |
| empty | 0.680 |
| hand_motion | 1.244 |
| passage | 0.716 |
| sit | 1.363 |
| stand | 0.997 |

Bu ağırlıklar, az örnekli `sit` ve `hand_motion` sınıflarının eğitimde kaybolmasını azaltır.

## 10. Eğitim Optimizasyonu

Optimizasyon:

```text
AdamW
learning_rate = 7e-4
weight_decay = 1e-3
batch_size = 32
max_epoch = 80
early_stopping_patience = 14
selection_metric = validation macro-F1
```

AdamW, Adam optimizer'ın weight decay ayrıştırılmış versiyonudur. Güncelleme kabaca:

```text
theta <- theta - eta * AdamGradient(theta) - eta * lambda * theta
```

Validation macro-F1 iyileşmezse eğitim erken durdurulur. Bu koşuda:

```text
epochs_run = 38
best_epoch = 26
```

## 11. Augmentasyon

Eğitim sırasında üç hafif augmentasyon kullanıldı:

1. Gaussian noise:

```text
Z' = Z + E
E_t,k ~ Normal(0, 0.025^2)
```

2. Time masking:

```text
Pencerenin kısa bir zaman aralığı sıfırlanır.
```

3. Tone masking:

```text
Bazı komşu tone sütunları sıfırlanır.
```

Bu augmentasyonlar modelin tekil frame'lere veya tekil subcarrier bölgelerine aşırı bağımlı olmasını azaltır.

## 12. Metrikler

Her sınıf için:

```text
precision_c = TP_c / (TP_c + FP_c)
recall_c    = TP_c / (TP_c + FN_c)
F1_c        = 2 precision_c recall_c / (precision_c + recall_c)
```

Macro-F1:

```text
macroF1 = (1 / C) sum_c F1_c
```

Macro-F1, sınıf dengesizliğinde accuracy'den daha bilgilendiricidir; çünkü her sınıfa eşit ağırlık verir.

Nihai test sonucu:

```text
accuracy = 0.902
macro-F1 = 0.820
```

Sınıf bazlı test F1:

| Sınıf | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| empty | 1.000 | 1.000 | 1.000 |
| hand_motion | 0.667 | 0.727 | 0.696 |
| passage | 0.842 | 1.000 | 0.914 |
| sit | 1.000 | 0.556 | 0.714 |
| stand | 0.933 | 0.667 | 0.778 |

## 13. Alarm Gruplaması

Canlı sistemde beş sınıf iki üst gruba ayrılır:

```text
normal = empty + sit + stand
alarm  = passage + hand_motion
```

Bu gruplamada test sonucu:

```text
alarm accuracy  = 0.930
alarm recall    = 1.000
alarm precision = 0.855
false alarm     = 10 / 84 normal pencere
missed alarm    = 0 / 59 alarm pencere
```

Yorum:

- Recall'ın yüksek olması alarm olaylarını kaçırmama eğilimini gösterir.
- Precision'ın daha düşük olması bazı normal durumların alarm gibi yorumlandığını gösterir.
- Bu yüzden canlı sistemde ek alarm kapısı kullanılır.

## 14. Realtime Alarm Kapısı

Modelin `hand_motion` veya `passage` demesi tek başına alarm kaydı üretmez. Ek koşullar:

```text
confidence >= 0.85
motionScore >= 0.05
packetRate >= 5 pkt/s
same-label streak >= 2
cooldown = 5 s
```

Matematiksel olarak bir zaman anında model çıktısı:

```text
y_hat_t, c_t
```

Burada:

- `y_hat_t`: tahmin etiketi
- `c_t`: confidence

Alarm adaylığı:

```text
candidate_t =
  1 if y_hat_t in {hand_motion, passage}
       and c_t >= 0.85
       and motionScore_t >= 0.05
       and packetRate_t >= 5
  0 otherwise
```

Kayıt için aynı alarm etiketinin ardışık iki model kararında gelmesi gerekir:

```text
alarm_t = 1 if candidate_t = 1 and y_hat_t = y_hat_{t-1}
```

Cooldown, aynı etiketin çok sık tekrar kaydedilmesini engeller.

## 15. motionScore

`motionScore`, ana CNN/LSTM girdisi değildir; canlı alarm kapısında yardımcı hareket yoğunluğu ölçüsüdür. Ardışık ham amplitüd vektörleri üzerinden hesaplanır:

```text
motionScore_t =
mean_k |A_t,k - A_{t-1,k}| / max(1, mean_k A_{t-1,k})
```

Sonuç 0 ile 1 civarına sıkıştırılır. Bu skor, "model alarm dedi ama CSI'da anlık değişim gerçekten var mı?" sorusu için ek kontrol sağlar.

## 16. Canlı Inference Temposu

Dataset örnekleri her CSI frame'inde değil, kayıt sırasında belirli aralıklarla yazıldı:

```text
DATASET_FRAME_STRIDE = 6
```

Bu yüzden canlı sistemde model de her frame'de çalıştırılmaz:

```text
MODEL_INFER_STRIDE = 6
```

Sebep: Model eğitimde belirli örnekleme temposu ve pencere uzunluğuyla eğitilir. `multiscale_physical_v1` profilinde hedef pencereler 16 ve 48 örnektir. Canlıda her frame'i pencereye koymak, modelin gördüğü zaman ölçeğini yaklaşık 6 kat kısaltır. Bu durum boş odada yanlış alarm üretme riskini artırabilir.

## 17. Sonuçların Yorumu

Modelin güçlü olduğu durumlar:

- `empty` sınıfını iyi ayırıyor.
- `passage` sınıfını yüksek recall ile yakalıyor.

Zayıf olduğu durumlar:

- `sit` ve `stand` bazı pencerelerde hareketli sınıflara benzeyebiliyor.
- `hand_motion` ile `passage` arasında kısmi karışma var.
- Veri tek ortam ve sınırlı oturumdan geldiği için genelleme garantisi yok.

## 18. Matematiksel Araştırma İçin Öneriler

Bu dataset üzerinde çalışacak araştırmacı şu yönlere bakabilir:

1. Zaman-frekans matrislerinin sınıflar arası ayrılabilirliği:

```text
class-wise covariance
PCA / t-SNE / UMAP
between-class vs within-class variance
```

2. Subcarrier önem analizi:

```text
Hangi tone bölgeleri sınıflandırmada daha etkili?
```

3. Alternatif özellikler:

```text
temporal derivative
spectral entropy
Doppler-like STFT
autocorrelation
low-rank + sparse decomposition
```

4. Daha fiziksel kanal modeli:

```text
H_k(t) = static_component_k + dynamic_component_k(t)
```

Bu ayrım, boş oda/sabit insan/hareketli insan ayrımında faydalı olabilir.

5. Domain shift:

```text
farklı mesafe
farklı oda
farklı cihaz konumu
farklı gün/saat
```

Modelin bu değişkenlere duyarlılığı ayrıca ölçülmelidir.

## 19. Paylaşılacak Dosyalar

Matematiksel inceleme için öncelikli dosyalar:

```text
data/csi/raw/*.ndjson
data/csi/reports/training_run_2026-05-25.md
data/csi/models/best_csi_cnn_lstm_temporal.report.json
tools/csi_ml/prepare_temporal_splits.py
tools/csi_ml/train_temporal_cnn_lstm.py
tools/csi_ml/prepare_multiscale_splits.py
tools/csi_ml/train_multiscale_cnn_lstm.py
```

Model ağırlığı gerekiyorsa:

```text
data/csi/models/best_csi_cnn_lstm_temporal.pt
```

Ham CSI paketleri üzerinden yeniden işleme yapılacaksa pcap kayıtları da paylaşılabilir. Ancak mevcut ML dataset'i amplitüd-log vektörleriyle çalışmak için yeterlidir.

## 20. Yeni Fiziksel Özellik Pipeline'ı

Fazlı yeni kayıtlar toplandıktan sonra çok kanallı dataset şu komutla hazırlanır:

```bash
python tools/csi_ml/prepare_temporal_splits.py data/csi/raw \
  -o data/csi/csi_temporal_physical_w16_s4.npz \
  --window 16 \
  --stride 4 \
  --train-ratio 0.60 \
  --val-ratio 0.20 \
  --purge 16 \
  --features amp,phase,amp_delta,phase_delta
```

Bu durumda tek pencere şu tensör olur:

```text
X in R^(16 x 4 x 128)
```

Boyutların anlamı:

```text
16  = zaman örneği
4   = feature kanalı
128 = tone/subcarrier
```

Eğitim:

```bash
python tools/csi_ml/train_temporal_cnn_lstm.py data/csi/csi_temporal_physical_w16_s4.npz \
  -o data/csi/models/csi_cnn_lstm_physical_w16_s4.pt \
  --epochs 80 \
  --patience 14
```

Model mimarisi aynı kalır; yalnızca ilk Conv1D katmanı artık `in_channels=4` kullanır. Böylece CNN aynı anda amplitüd, faz kalıntısı ve bunların zamansal türevlerini subcarrier ekseninde işler.

Tez için tercih edilen çok ölçekli sürüm ise şu komutlarla hazırlanır:

```bash
python tools/csi_ml/prepare_multiscale_splits.py data/csi/raw_phase \
  -o data/csi/csi_multiscale_physical_w16_w48_s4.npz \
  --windows 16,48 \
  --stride 4 \
  --train-ratio 0.60 \
  --val-ratio 0.20 \
  --purge 32 \
  --features amp,phase,amp_delta,phase_delta

python tools/csi_ml/train_multiscale_cnn_lstm.py data/csi/csi_multiscale_physical_w16_w48_s4.npz \
  -o data/csi/models/csi_cnn_lstm_multiscale_w16_w48.pt \
  --epochs 80 \
  --patience 14
```

Bu modelin checkpoint'i canlı backend tarafından doğrudan okunabilir. Backend `windows=[16,48]` alanını gördüğünde buffer uzunluğunu 48 sample'a çıkarır, her karar anında son 16 ve son 48 sample'ı ayrı ayrı normalize eder ve modeli iki girdiyle çalıştırır.

2026-05-30 tarihli seçilen fazlı model bu çok ölçekli hatla eğitilmiştir, ancak sınıf listesi üç sınıftır:

```text
empty
hand_motion
passage
```

Bu model `sit` veya `stand` tahmini üretmez; bu sınıflar için ayrıca fazlı kayıt toplanıp yeni bir genişletilmiş model eğitilmelidir.

## 21. Kısa Özet

Bu projede CSI verisi, zaman içinde değişen 128 boyutlu log-amplitüd vektörleri olarak ele alındı. İlk modelde ardışık 24 örnek bir zaman-frekans matrisi oluşturdu. Her pencere tone bazında normalize edildi. CNN, subcarrier eksenindeki lokal frekans örüntülerini; BiLSTM ise bu örüntülerin zaman içindeki evrimini öğrendi. Model weighted cross entropy ile beş sınıflı olarak eğitildi. Canlı sistemde `hand_motion` ve `passage` sınıfları alarm olarak yorumlandı; yanlış pozitifleri azaltmak için confidence, motionScore, packetRate, streak ve cooldown içeren ek bir karar kapısı kullanıldı.

Yeni geliştirme hattında bu yapı amplitüd-only olmaktan çıkarılıp `amp + phase + amp_delta + phase_delta` kanallarına genişletildi. Ayrıca tek pencere yerine aynı karar anına hizalanmış 16 ve 48 sample'lık iki pencere kullanılacak şekilde çok ölçekli CNN/LSTM mimarisi eklendi. Bu, modeli fiziksel kanal değişimlerine daha duyarlı hale getirir ve kısa el hareketlerini uzun bağlamla doğrulamaya yardımcı olur; ancak gerçek ToF/AoA düzeyinde ayrıştırma için yine çoklu anten, daha geniş bant veya ek alıcı/verici linkleri gerekebilir.
