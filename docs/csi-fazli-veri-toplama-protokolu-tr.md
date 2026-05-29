# Fazlı CSI Modeli İçin Veri Toplama Protokolü

Bu protokol, `phaseResiduals` destekli yeni modelden daha doğru sonuç almak için kullanılacaktır. Eski kayıtlar amplitüd-only olduğu için yeni fiziksel modelde ana eğitim verisi olarak yeni kayıtlar kullanılmalıdır.

## Hedef

Modelin daha doğru çalışması için her sınıfın aynı kanal, aynı mesafe ve benzer süre koşullarında dengeli toplanması gerekir.

Başlangıç sınıfları:

```text
empty
sit
stand
passage
hand_motion
```

Alarm gruplaması:

```text
normal = empty + sit + stand
alarm  = passage + hand_motion
```

## Minimum Hedef

İlk fazlı model için minimum:

```text
her sınıf >= 400 sample
her sınıf >= 2 ayrı oturum
```

Daha iyi hedef:

```text
her sınıf >= 800 sample
her sınıf >= 3 ayrı oturum
```

WebUI'daki `datasetSamples` sayacı esas alınır. Süre yerine sample sayısına bakmak daha doğru olur; çünkü paket hızı ortama göre değişebilir.

## Multiscale Physical PacketRate Ayarı

Bu profil için hedef Alpha WebUI `packetRate` değeri:

```text
15-20 pkt/s
```

Bravo tarafında varsayılan UDP gönderim hızı:

```text
TX_PROFILE=multiscale_physical_v1 RATE_PPS=15
```

Neden 15 pps?

```text
Yerel doğrulamada UDP gönderim hızı Alpha WebUI packetRate değerine yakın yansıdı. Bu yüzden verici hızı düşük riskli tarafta 15 pps seçildi.
```

Bu profilin ana fikri paket hızını gereksiz yükseltmek değil, aynı anı iki farklı zaman ölçeğinden okumaktır:

```text
kısa pencere: 16 sample  -> el, bilek, küçük hareket, hızlı değişim
uzun pencere: 48 sample  -> passage, otur/ayakta bağlamı, boş odada stabilite
```

WebUI dataset'i her 6 CSI frame'inde bir sample yazar. Alpha tarafında `packetRate` yaklaşık 15-20 pkt/s ise bu, dataset tarafında yaklaşık 2.5-3.3 sample/s anlamına gelir. Dolayısıyla:

```text
16 sample pencere yaklaşık 5-6 saniyelik kısa bağlam
48 sample pencere yaklaşık 15-19 saniyelik uzun bağlam
```

Bu iki ölçek birlikte kullanıldığında model hem mikro değişimi kaçırmamaya hem de tekil gürültü dalgalanmalarına alarm vermemeye zorlanır.

Alpha WebUI'da birkaç dakika boyunca `packetRate` şu aralıkta kalmalı:

```text
ideal: 15-20 pkt/s
kabul edilebilir: 10-25 pkt/s
```

Eğer sürekli `15 pkt/s` altındaysa Bravo'da:

```bash
RATE_PPS=20 /home/admin/csi/start_tx.sh 192.168.1.7 5501
```

Eğer SSH/WebUI ağırlaşırsa:

```bash
RATE_PPS=12 /home/admin/csi/start_tx.sh 192.168.1.7 5501
```

## Toplama Sırası

Tek bir sınıfı tamamen bitirip diğerine geçmek yerine sınıfları tur tur toplamak daha sağlıklıdır. Önerilen sıra:

```text
Tur 1:
empty -> sit -> stand -> passage -> hand_motion

Tur 2:
empty -> sit -> stand -> passage -> hand_motion

Tur 3:
empty -> sit -> stand -> passage -> hand_motion
```

Bu, gün içindeki sıcaklık, ağ yoğunluğu ve cihaz drift etkilerinin tek sınıfa yığılmasını azaltır.

## Etiket Kullanımı

WebUI label alanında sınıf adı sade kalmalı:

```text
empty
sit
stand
passage
hand_motion
```

Mesafe, yön veya not bilgisi WebUI note alanına yazılmalıdır:

```text
distance=2m round=1 chair=center
distance=2m round=2 left_to_right
```

Böylece eğitim sınıf sayısı gereksiz büyümez ama metadata korunur.

## Sınıf Tanımları

`empty`:

- Odada insan yok.
- Kapı/masa/sandalye gibi statik nesneler hareket ettirilmez.
- Bravo ve Alpha sabit kalır.

`sit`:

- Kişi oturur.
- Büyük hareket yapılmaz.
- Küçük doğal hareketler serbesttir ama el sallama yapılmaz.

`stand`:

- Kişi ayakta sabit durur.
- Mümkünse hem link hattı üzerinde hem hattın biraz dışında ayrı oturumlar alınır.

`passage`:

- Kişi Alpha-Bravo hattından geçer.
- Her oturumda aynı ritimde birkaç giriş/çıkış yapılır.
- Yön note alanına yazılır: `left_to_right` veya `right_to_left`.

`hand_motion`:

- Belirlenen curl/el hareketi yapılır.
- Hareket ritmi çok hızlı ve rastgele olmamalıdır.
- Oturumlar arasında hız değiştirilebilir; note alanına `slow`, `normal`, `fast` yazılabilir.

## Eğitimden Önce Denetim

Yeni kayıtlar bilgisayara indirildikten sonra:

```bash
mkdir -p data/csi/raw_phase
scp -i ~/.ssh/id_ed25519_csi_codex admin@192.168.1.99:/home/admin/csi/datasets/*.ndjson data/csi/raw_phase/
```

Denetim:

```bash
python tools/csi_ml/audit_dataset.py data/csi/raw_phase \
  --require-phase \
  --min-samples-per-label 400 \
  --json data/csi/reports/phase_dataset_audit.json
```

Eğer `Issues: none` görülürse eğitim için veri yeterli kabul edilir. Eksik sınıf veya eksik `phaseResiduals` varsa önce veri toplama tamamlanmalıdır.

## Eğitim Komutu

Tek ölçekli hızlı kontrol modeli:

```bash
python tools/csi_ml/prepare_temporal_splits.py data/csi/raw_phase \
  -o data/csi/csi_temporal_physical_w16_s4.npz \
  --window 16 \
  --stride 4 \
  --train-ratio 0.60 \
  --val-ratio 0.20 \
  --purge 16 \
  --features amp,phase,amp_delta,phase_delta

python tools/csi_ml/train_temporal_cnn_lstm.py data/csi/csi_temporal_physical_w16_s4.npz \
  -o data/csi/models/csi_cnn_lstm_physical_w16_s4.pt \
  --epochs 80 \
  --patience 14
```

Tez için tercih edilen çok ölçekli model:

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

Çok ölçekli model için `purge=32` seçilmesinin nedeni, uzun pencere yüzünden train/validation/test sınırlarında birbirine çok benzeyen kesitlerin sızmasını azaltmaktır. Eğer bir sınıfta validation veya test penceresi üretilemiyorsa veri süresi yetersizdir; özellikle `hand_motion` ve `sit` için 800 sample hedefi bu yüzden daha sağlıklıdır.

## Kabul Kriteri

İlk fazlı modelin mevcut amplitüd-only modele göre daha iyi kabul edilebilmesi için:

```text
test macro-F1 >= 0.82
alarm precision > 0.855
alarm recall yaklaşık 1.0
false alarm sayısı azalmalı
```

Özellikle boş oda için yanlış alarm üretmemesi ana kriterdir.

Çok ölçekli model canlı backend'e takıldığında checkpoint içinde şu alanlar bulunmalıdır:

```text
model = csi_cnn_lstm_multiscale_v1
windows = [16, 48]
featureNames = [amp, phase, amp_delta, phase_delta]
inputChannels = 4
```
