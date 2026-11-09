# Wi-Fi CSI Projesi: Algoritma ve Matematik Notu

> **Güncelleme (2026-11-09):** Proje tek cihaza (yalnız Alpha: AX210 yayar, aynı Pi'deki Broadcom
> okur) taşındı. Tek kutu algılamanın matematiği, sayısal doğrulamaları ve önerilen zaman serisi
> yöntemleri **§22**'dedir. §1–§21 iki cihazlı (Alpha–Bravo) ML hattını anlatır.

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

## 21. Kısa Özet (iki cihazlı ML hattı)

Bu projede CSI verisi, zaman içinde değişen 128 boyutlu log-amplitüd vektörleri olarak ele alındı. İlk modelde ardışık 24 örnek bir zaman-frekans matrisi oluşturdu. Her pencere tone bazında normalize edildi. CNN, subcarrier eksenindeki lokal frekans örüntülerini; BiLSTM ise bu örüntülerin zaman içindeki evrimini öğrendi. Model weighted cross entropy ile beş sınıflı olarak eğitildi. Canlı sistemde `hand_motion` ve `passage` sınıfları alarm olarak yorumlandı; yanlış pozitifleri azaltmak için confidence, motionScore, packetRate, streak ve cooldown içeren ek bir karar kapısı kullanıldı.

Yeni geliştirme hattında bu yapı amplitüd-only olmaktan çıkarılıp `amp + phase + amp_delta + phase_delta` kanallarına genişletildi. Ayrıca tek pencere yerine aynı karar anına hizalanmış 16 ve 48 sample'lık iki pencere kullanılacak şekilde çok ölçekli CNN/LSTM mimarisi eklendi. Bu, modeli fiziksel kanal değişimlerine daha duyarlı hale getirir ve kısa el hareketlerini uzun bağlamla doğrulamaya yardımcı olur; ancak gerçek ToF/AoA düzeyinde ayrıştırma için yine çoklu anten, daha geniş bant veya ek alıcı/verici linkleri gerekebilir.

Tek kutu düzeninin özeti için §22.12'ye bakın.

## 22. Tek Kutu Algılamanın Matematiği (2026-11)

Bu bölüm, tek cihazlı düzeni (AX210 → ortam → aynı Pi'deki Broadcom/Nexmon) ele alır. Kod:
`device-files/csi-alpha-webui/webui/csi_residual.py` (sızıntı iptali, beyazlatma, T²) ve `csi_sensing.py`.
Veri: 2026-11-09 kayıtları (`csi-alpha-web-20261109-*.pcap`, olay işaretleri `.events.ndjson`).

### 22.1 Ölçülen kanal ve asıl zorluk

Kare t, alt taşıyıcı k (f_k = k·312.5 kHz, 20 MHz'de 48 veri tonu) için:

```text
H_k(t) = g(t) · e^{-j k θ(t)} · D_k^{(s)}  +  Σ_i a_i(t) e^{-j 2π f_k τ_i(t)}  +  n_k(t)
```

- `D^{(s)}`: doğrudan AX210→Broadcom sızıntısı + statik yansımalar (duvar, eşya); verici durumu `s` başına.
- `g(t) ∈ C`: kare başına karmaşık kazanç (AGC, ortak faz CPE, AX210'un ayrık güç durumları).
- `θ(t)`: örnekleme zamanı kayması (STO) → alt taşıyıcılar boyunca doğrusal faz.
- `Σ_i`: insan/nesne yansımaları, sızıntıya göre ≈ −30…−40 dB.

Sızıntının büyük olması tek başına sorun değildir; sabit bir vektör bilinip çıkarılabilir. **Asıl sorun
çarpımsal bozucudur:** `g` kestirimindeki göreli ε hatası, artığa `ε·D` olarak girer. Ölçülen 0.12 dB'lik
seviye titreşimi ε ≈ %1.4 demektir; bu da −37 dB'lik bir artık bırakır. Aradığımız yansıma da tam bu
seviyededir. Bu yüzden ham genlik/güç tabanlı metrikler (şekil std'si, ortak güç) ya hiçbir şey görmedi
ya da boş odada da alarm verdi (§22.10).

### 22.2 En küçük kareler izdüşümü: kazanca tam değişmez artık

STO önce kaldırılır (unwrap gerektirmez, sızıntı baskın olduğundan yansımadan etkilenmez):

```text
θ̂ = ∠ Σ_k H_{k+1} H_k^*          H_k ← H_k e^{+j k θ̂}
```

Sonra her kare için skaler kazanç, sızıntının kendisi "pilot" kullanılarak kestirilir:

```text
ĝ = argmin_g ‖h − g D‖² = Dᴴh / DᴴD
r = h / ĝ − D
```

İki özellik:

1. **Diklik:** `Dᴴ(h/ĝ) = DᴴD` olduğundan `Dᴴ r = 0`. Artık, sızıntıya tam diktir.
2. **Değişmezlik:** herhangi bir `c ∈ C` için `h → c·h` dönüşümü `ĝ → c·ĝ` verir, `r` değişmez. Yani
   AGC, CPE ve AX210'un güç durumu sıçramaları (|c| ve ∠c ne olursa olsun) artığa **hiç girmez**.

Ölçüm: kalibrasyonda artık/sızıntı gücü −27.8 dB (`ax210_action`) / −28.3 dB (`ax210_ba`). Bu değer
alıcı gürültü tabanıdır; sızıntıdan kalan sistematik bir pay görünmez.

### 22.3 Yansımanın ne kadarı korunur? (mesafeye bağlı yüksek geçiren)

Gecikmesi τ olan bir yansıma, aynı RF zincirinden geçtiği için `s_τ = D ⊙ e^{-j2π f_k τ}` biçimindedir.
İzdüşüm, `s_τ`'nun D'ye paralel kısmını ĝ'ye yükler; artıkta kalan güç payı:

```text
η(τ) = 1 − |Dᴴ s_τ|² / (‖D‖² ‖s_τ‖²)
```

Gerçek D (kalibrasyon) ile sayısal sonuç (tek yön mesafe ≈ c·τ/2):

| τ (ns) | ≈ mesafe farkı | η (artıkta kalan) |
|---|---|---|
| 3 | 0.45 m | 0.008 (−20.8 dB) |
| 5 | 0.75 m | 0.023 (−16.4 dB) |
| 10 | 1.5 m | 0.090 (−10.5 dB) |
| 15 | 2.25 m | 0.193 (−7.2 dB) |
| 20 | 3.0 m | 0.321 (−4.9 dB) |
| 30 | 4.5 m | 0.604 (−2.2 dB) |
| 50 | 7.5 m | 0.971 (−0.1 dB) |

**Yorum:** 20 MHz bantta (16.25 MHz kullanılan genişlik) kısa gecikmeli yansıma, sızıntıya neredeyse
paraleldir ve büyük kısmı ĝ'ye, yani **ortak güce** gider. Uzak yansıma ise alt taşıyıcılar boyunca
döner ve **artıkta** kalır. Ölçümlerle tutarlı: yakın hareket ortak güçte (|ĝ|) açıkça görüldü, uzak
hareket (4–5 m kapı) yalnız artık tabanlı T²'de görüldü. İki kanal birbirini tamamlar; ortak güç kanalı
ise AX210'un güç durumlarıyla kirlidir (§22.4).

### 22.4 Verici durumları

AX210, BlockAck/yönetim çerçevelerini kare kare ayrık güç durumlarında yollar (RSSI −19/−21/−23 dBm).
Durumlar ile anahtar şöyle ilişkili:

- **Şekil aynı:** bir durumun karelerinin başka durumun D'sine göre artığı 7×10⁻⁴; kendi D'sine göre
  de 7×10⁻⁴. Fark saf kazançtır ve §22.2 bunu yok eder.
- **Ortalama sapma ve gürültü kovaryansı farklı:** 17:46'da baskın durum −19'dan −21'e geçtikten sonra,
  −19'dan öğrenilmiş beyazlatmayla boş odada T² medyanı **64.7** oldu (geçiş öncesi 1.06).
- **Yeniden gönderimler** (aynı sıra-kontrol alanı art arda, karelerin %2.8'i) azınlık durumlarında
  yoğunlaşır (%67'si, normal karelerde %14). Bunlar atılmazsa boş odada 29 yanlış küme çıkar,
  atılınca 2. AX210 sürücüsü anten sabitlemeyi desteklemiyor (`set antenna` → EOPNOTSUPP); güç
  sınırı (10 dBm) durumları ortadan kaldırmadı.

Bu yüzden durum anahtarı `(şekil-mod kimliği, RSSI kümesi)` yapılır. RSSI kümesi, en yakın merkeze
±1 dB toleransla atanır; sürekli titreşen RSSI tek durumda kalır. Her durumun kendi D'si (EWMA,
τ = 30 sn) ve kendi beyazlatması vardır. Yeni durum 100 karede D'yi, 1000 karede kovaryansı öğrenir;
öğrenilene kadar o durumdan T² üretilmez.

### 22.5 Bozucu (clutter) beyazlatma

`x = [Re r, Im r] ∈ R^p` (p = 2·48 = 96; ton sayısı > 64 ise ardışık tonlar 64 gruba ortalanır).
Boş oda kovaryansı Ledoit–Wolf büzültmesiyle kestirilir:

```text
S = XᵀX/n,   m = tr(S)/p,   d² = ‖S − mI‖²_F,   b² = min(d², (Σ_i‖x_i‖⁴/n − ‖S‖²_F)/n)
Ĉ = (b²/d²)·m·I + (1 − b²/d²)·S,          z = Ĉ^{-1/2}(x − μ)
```

Neden gerekli: 80 MHz'de p = 468 boyut, 60 sn'de 1 800 kareyle kestirildiğinde örnek kovaryansı küçük
özdeğerleri sistematik olarak küçümser. Beyazlatma bu yönleri şişirir, kalibrasyon sonrası T²
patlar (sentetik testte gözlendi). Gruplama + büzültme bunu giderdi.

### 22.6 Değişim istatistiği: iki pencereli Hotelling T²

Baskın durumun kareleriyle, pencere W = 3 sn (n_W kare) ve taban B = 20 sn (n_B kare):

```text
T²(t) = (n_W n_B / (n_W + n_B)) · ‖ z̄_{(t−W, t]} − z̄_{(t−B, t−W]} ‖² / p
```

H0 (değişim yok, z bağımsız ve beyaz) altında `p·T² ~ χ²_p`, yani `E[T²] = 1`. Ölçülen (ax210_action
boş oda, 10.8 dk):

| | p50 | p95 | p99 |
|---|---|---|---|
| χ²_96/96 (teori) | 0.99 | 1.25 | 1.37 |
| Ölçülen T² | 1.07 | 1.59 | 1.91 |

- **Zamansal korelasyon önemsiz:** gecikme 1–5 otokorelasyonu |ρ| < 0.01, entegre korelasyon zamanı
  1.18 kare.
- **Kuyruk kalın, nedeni kovaryansın zamanla değişmesi:** boş odada beyazlatılmış boyutların varyansı
  medyan 1.07, en çok 2.2 (kalibrasyonda 1). 60 sn'lik kalibrasyon, sonraki 15 dakikanın bozucu
  yapısını tam temsil etmiyor.
- **Sonuç:** gövde teoriye uyuyor; durum başına beyazlatmayla son sürümde boş oda medyanı **0.95**.
  Eşik teorik χ² kantilinden değil, kalibrasyon kareleri aynı dedektörden geçirilerek robust olarak
  alınır: `max(med + 8·1.4826·MAD, 1.2·P99.5, 2.0)`.

### 22.7 Karar katmanı

T²/θ_T² hareket skoruna katılır; alarm K-of-N (3/4) histerezisle verilir. **CUSUM denendi ve
elendi:** `S_t = max(0, S_{t−1} + log(T²_t / k))` kalın kuyruk yüzünden takılı kaldı. k = 2–3'te test
süresinin %96–100'ünde alarmdaydı; bu yüzden "12/12" sonucu sahteydi. Sıralı testler ancak
kuyruğu kalibre edilmiş bir olabilirlik oranıyla (§22.11-3) kullanılabilir.

### 22.8 Denenip elenen yöntemler (aynı veri)

| Yöntem | Neden elendi |
|---|---|
| Şekil std'si (log-genlik, mod içi) | Olaylarda 0.0085–0.0099, boş p99 0.0094: gürültü tabanında |
| G: RSSI durum geçiş payı | `ax210_ba`'da 17/17, ama modem bağlantısı etkisi; `ax210_action`'da hiçbir olaya tepki yok |
| Ortak güç üst zarfı (0.5 sn P90, durum içi) | Yakın 8/8, ama boş oda titreşimiyle aynı büyüklük: hiçbir eşik ikisini birlikte sağlamıyor |
| Doppler bant enerjisi (0.3–25 Hz, beyaz uzayda) | Yakın 0–3/8: bu mesafede etki hızlı dönen yansıma değil, < 0.3 Hz yavaş değişim |
| En büyük özdeğer (Roy) testi, 4 sn | Boş p99 35, 14 yanlış küme |
| Monitör vif enjeksiyonu / yayın yönetim çerçevesi | iwlwifi, STA bağlıyken bunları havaya göndermiyor |

### 22.9 Sonuçlar (son sürüm, motor replay'i)

| Kayıt | Kaynak | Kalibrasyon | Yakın | Uzak 4–5 m | Boş oda |
|---|---|---|---|---|---|
| 17:16 | ax210_action | kendi (60 sn) | **6/8** | **9/12** | 15.4 dk: 3 alarm, sürenin %1.7'si |
| 17:16, eski motor | ax210_action | kendi | 0/8 | 0/12 | 0 |
| 15:47 (yalnız T², G kapalı) | ax210_ba | 15:10 kaydı (1 saat önce) | 4/6 | 1/5 | 15.1 dk: 8 alarm, %7.8 |

15:47 boş odasındaki 8 alarmın hepsi, bağımsız bir istatistik olan G'nin de sıçradığı anlarda
(16:47, 16:51–16:53). `ax210_ba`'da verici davranışı modem bağlantısına bağlı olduğundan, bu anların
ortamdan mı modemden mi geldiği ayrılamaz.

**Bağımsız test (19:20, yeni kalibrasyon, parametreler değiştirilmeden, 20.5 dk boş oda):**
T² medyan 0.91, p95 1.44, p99 2.0. Alarmda geçen süre %1.2; 5 kısa alarm, 4'ü eşiğin hemen üstünde
(2.3–2.7). Kullanıcı bu sürede oda kapısının dışında hareket olduğunu ve alt katın salonun tam altında
olduğunu bildirdi. Boş dağılımın gövdesi teoriyle bağımsız olarak doğrulandı. 60 sn kalibrasyonun kuyruğu
hafife aldığı da bağımsız olarak doğrulandı (kalibrasyon p99 1.25'e karşı boş oda p99 2.0).

**Ön kayıtlı bağımsız olay testi (20:23–20:41, aynı 180 sn kalibrasyon, parametreler sabit):**
oda içi olayların 28/32'si yakalandı. Yakın (≤ 2 m) 10/10; hareketsiz duran kişinin derin nefesi
58 sn'nin %64'ünde alarmda; 2–4 m yürüme 5/5; kapı/giriş (4–5 m) 8/12. Kapı kapalıyken oda dışı
hareket 1/5. Aynı ortamda boş odada 15.5 dk'da 1 alarm (§11.9, tek kutu belgesi). Böylece
§22.10-1'deki "bağımsız doğrulama yok" sınırlaması kaldırıldı.

**Karşılaştırma ve ablasyon** (`data/csi/reports/single_box_bench_2026-11-09.md`): eşit yanlış alarm
bütçesinde (bağımsız boş oda ≤ 4/saat) T² bağımsız oda içi olayların 21/27'sini yakaladı. Klasik
genlik varyansı 15/27, arka plan çıkarma 2/27, önceki motor 0/27 yakaladı. Ablasyon, kovaryans
beyazlatmanın belirleyici olduğunu gösterdi (çıkarılınca 0/27). LS izdüşümü, CPE + genlik
normalizasyonuyla pratikte eşdeğer. Durum ayrımı yalnız verici durum geçişlerinde; yeniden gönderim
süzme ise durum ayrımı varken gereksiz.

### 22.10 Matematiksel iddia ne kadar destekleniyor?

**Desteklenen:**

1. Kazanç/CPE/güç durumu değişmezliği bir teoremdir (§22.2). Artık seviyesi gürültü tabanında
   (−28 dB). **Düzeltme (ablasyon):** CPE kaldırma + genlik normalizasyonu da skaler kazancı yaklaşık
   olarak kaldırıyor ve ölçülen performans farkı yok. LS'nin katkısı kuramsal kesinlik.
   Performansı belirleyen adım bozucu kovaryansıyla beyazlatma (§22.5).
2. T²'nin boş odadaki gövdesi χ²_p/p ile uyumlu (medyan 0.95–1.07). Zamansal bağımsızlık
   varsayımı ölçümle doğrulandı (τ_int = 1.18).
3. Aynı kayıtta eski motor 0/20 yakaladı, T² 15/20. `ax210_action` boş odasında alarmda geçen süre
   %1.7.
4. §22.3'teki mesafe–koruma ilişkisi, "yakın = ortak güç, uzak = artık" gözlemini nicel olarak açıklıyor.

**Henüz desteklenmeyen / sınırlar:**

1. ~~Bağımsız doğrulama yok.~~ **Yapıldı (2026-11-09 19:55–20:41, ön kayıtlı):** boş odada 15.5 dk'da
   1 alarm; oda içi olaylarda 28/32. Tek bir oda ve tek bir oturum olduğu için genellenebilirlik
   hâlâ sınırlı: farklı oda, gün ve kasa ortamında tekrar edilmeli.
2. Kuyruk χ²'den kalın (p99 1.91 / 1.37). Kalibrasyon kovaryansı durağan değil; yanlış alarm oranı
   teorik olarak değil, yalnız ampirik olarak kontrol ediliyor.
3. Yakın yansımanın %90'dan fazlası ĝ'ye gidiyor (§22.3). Bu yüzden ortak güç kanalı da gerekli,
   ama o kanal verici durumlarıyla kirli.
4. T² bir **değişim** detektörü. Hareketsiz duran kişi B = 20 sn sonra tabana karışır; kalıcı
   varlık statik detektörün (§7.2 tek kutu belgesi) ve mikro-hareketin işi.
5. 20 MHz'de gecikme çözünürlüğü 1/16.25 MHz ≈ 62 ns (≈ 9 m tek yön). Yansımalar mesafeye göre
   doğrudan ayrılamıyor; ayrım yalnız §22.3'teki geometrik izdüşüm etkisiyle oluyor.
6. `ax210_ba` sonuçları modem bağlantısı etkisini içerir; saf monostatik iddiası için yalnız
   `ax210_action` (ya da kablolu yönetimde bağımsız verici) verisi kullanılmalıdır.

### 22.11 Bu projede kullanılabilecek diğer zaman serisi yöntemleri

Öncelik sırasıyla, her biri bu problemin belirli bir zayıflığına karşılık geliyor:

1. **Durum içi ortak güç kanalı (|ĝ|):** §22.3'ün doğrudan sonucu. Yakın hareketin enerjisi burada.
   Yeniden gönderimler atılmış, durum başına `log|ĝ|`'ye T² ile aynı iki pencereli test uygulanır.
   Verici seviye titreşimi için durum içi seviyeler ayrık bir karışım olarak modellenir (Gauss
   karışımı); test karışım ağırlıklarına değil, bileşen ortalamalarının kaymasına yapılır.
2. **Çevrimiçi kovaryans izleme:** sessiz dönemlerde (T² < 1.5) Ĉ'yi üstel unutmayla güncellemek
   (rank-1 güncelleme ya da PAST alt uzay izleyici). §22.10-2'deki kalın kuyruğun kaynağına
   doğrudan çözüm.
3. **Ampirik boş dağılımla GLR/CUSUM:** artım `log(f₁(T²)/f₀(T²))`, f₀ boş oda T²'sinin ampirik
   dağılımından (ör. log-normal ya da ölçekli χ² karışımı) gelir. Kuyruk doğru modellenirse
   K-of-N'den daha hızlı ve ARL (ortalama yanlış alarm aralığı) kontrollü bir karar verir.
4. **Durum-uzay modeli + Kalman süzgeci:** `(g, θ, D)` birlikte izlenir; yansıma süreç gürültüsü
   olarak modellenir. İnovasyon karesi (NIS) H0 altında χ² dağılır. ĝ ile T²'yi tek bir çerçevede
   birleştirir.
5. **Robust PCA (düşük rank + seyrek):** `H = L + S` ayrışımı (Candès). L sızıntı + statik yapı +
   verici durumları (düşük rank), S olaylar (seyrek). Çevrimdışı etiketleme ve eşik doğrulaması için
   güçlü bir araç.
6. **Gecikme alanında süper-çözünürlük (MUSIC/ESPRIT, alt taşıyıcılar üzerinde uzamsal
   yumuşatma):** yansımaların gecikmesini, yani mesafesini kestirir; τ ≈ 0 sızıntıyı kapılayıp
   (gating) atmayı ve kasa bölgesine göre alarm vermeyi sağlar. 20 MHz'de sınırlı, 80 MHz'de anlamlı.
7. **Çok-pencereli (Thomson multitaper) spektrum + F-testi:** artığın ilk temel bileşeninde
   0.1–0.5 Hz periyodik bileşen (nefes) için istatistiksel test. Hareketsiz insanın varlık kanıtı;
   §22.10-4'teki boşluğu doldurur.
8. **Değişim noktası analizi (PELT, Bayesçi çevrimiçi BOCPD) ve dalgacık dönüşümü:** kapı açık
   kaldı / nesne bırakıldı gibi kalıcı basamakları ve geçici olayları ayrı ölçeklerde ayırır.
9. **HMM / yarı-Markov model:** gözlem `[T², |ĝ| değişimi, statik mesafe]`, durumlar
   boş / hareketsiz insan / hareket. Kalma süresi öncülleriyle alarm titremesini azaltır.
10. **Literatür bağlantısı:** `e = ‖r‖²/‖D‖² = 1 − TRRS`. TRRS (time-reversal resonating strength,
    Origin Wireless) ölçütü, burada tanımlanan izdüşüm artığının normalize hâlidir. Bu bağlantı,
    yöntemin tezde mevcut literatüre yerleştirilmesini sağlar.
11. Makine öğrenmesi tarafında: boş oda artıklarıyla eğitilmiş tek-sınıf model (one-class SVM,
    otokodlayıcı). Doğrusal olmayan beyazlatma işlevi görür. Ancak etiketli bağımsız veri
    birikmeden önerilmez.

### 22.12 Kısa özet (tek kutu)

Tek kutuda ölçülen CSI'yı doğrudan sızıntı ve onu çarpan kare başına bozucu (kazanç, CPE, verici
güç durumu) belirler. Sızıntı, kendi kendine pilot olarak kullanılıp en küçük kareler izdüşümüyle
kazançtan bağımsız biçimde iptal edildi. Artık, verici durumu başına öğrenilen Ledoit–Wolf
kovaryansıyla beyazlatıldı ve iki pencereli Hotelling T² ile test edildi. Boş odada istatistik
teoriye uygun olarak ≈1. Aynı kayıtta eski yöntem 0/20 olay yakaladı, yeni yöntem 15/20; boş odada
sürenin %1.7'si alarmda. Matematik, yakın yansımanın büyük kısmının ortak güce gittiğini öngörüyor ve
ölçümler bunu doğruluyor. Sıradaki adımlar: bağımsız bir kayıtla doğrulama, durum içi ortak güç
kanalı ve çevrimiçi kovaryans izleme.

