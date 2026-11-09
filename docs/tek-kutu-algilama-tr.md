# Tek Cihazlı (Quasi-Monostatik) Wi-Fi CSI ile Gerçek Zamanlı Nesne/Hareket Algılama

Motor sürümü: `single_box_v1` · Kod: `device-files/csi-alpha-webui/webui/csi_sensing.py`
(zaman serisi: `csi_timeseries.py`, ağ: `csi_network.py`, panel grafikleri: `charts.js`)
Son güncelleme: 2026-11-09

Bu belge, Alpha–Bravo iki cihazlı (bistatik) sistemin tek cihaza (csi-alpha, Raspberry Pi 5)
indirgenmiş hâlini; yöntemi, matematiği, gerçek veriden çıkan tasarım kararlarını, değerlendirme
sonuçlarını ve sahada yapılacak deney protokolünü anlatır. Tezin "Yöntem", "Deneysel Kurulum" ve
"Sonuçlar" bölümlerine doğrudan kaynak olacak şekilde yazılmıştır.

---

## 1. Problem ve Katkılar

Hedef: Tek bir cihazdan çıkan Wi-Fi sinyalinin yine aynı cihazda yakalanmasıyla, **ortam bağımsız**
(oda, kapı, kamyonet kasası, depo …) şekilde

1. ortamda **hareketli nesne/insan** varlığını,
2. ortamın **statik RF imzasındaki kalıcı değişimi** (nesne bırakıldı/alındı, kapı/branda açık kaldı),
3. isteğe bağlı olarak **etiketli durumları** (açık/kapalı, dolu/boş) ve bilinmeyen durumu,
4. (deneysel) **hareketsiz canlıyı** (solunum bandında periyodik enerji),
5. **sinyal kaybı / sabotajı**

gerçek zamanlı tespit edip alarm üretmek; tüm parametreleri panelden ayarlanabilir, varsayılanları
senaryoya göre hazır bir sistem kurmak.

Özgün katkılar (tez için):

- **Eğitimsiz, kalibrasyon tabanlı** algılama: Hareket sınıfı verisi gerekmeden, yalnızca "normal"
  ortamın kısa bir kaydından robust istatistikle eşik üretimi. Yeni ortama kurulum = 60 sn kalibrasyon.
- **PHY mod bankası**: Aynı vericiden gelen çerçevelerin farklı PHY modlarında farklı CSI şekli
  taşıdığının gerçek veride gösterilmesi ve hareket/statik metriklerin her modun *kendi içinde*
  hesaplanması (bkz. §4).
- **Nexmon bcm43455c0 için doğrulanmış ön işleme**: geçersiz DC/guard tonlarının ve non-QoS
  çerçevelerin ayıklanması, unwrap gerektirmeyen faz temizleme.
- **Drift-dayanıklı statik değişim tespiti**: sessiz anlarda yavaş referans uyarlaması + kurulumda
  referans tazeleme + yeni PHY modlarının otomatik kaydı.
- **Açık küme (open-set) durum sınıflandırma**: hiçbir profile benzemeyen durumun "bilinmeyen"
  olarak raporlanması (ör. branda kesilmesi gibi önceden kaydedilmemiş durumlar).
- **Ölçülebilirlik**: canlı sistemle aynı kodu kullanan replay değerlendiricisi ve panelden yer
  gerçeği (olay) işaretleme → recall, gecikme, yanlış alarm/saat.

---

## 2. Donanım ve Radyo Düzeni

| Arayüz | Kart | Görev |
|---|---|---|
| `mgmt0` | Intel AX210 | SSH/web yönetimi **ve** sinyal kaynağı (managed modda kalır) |
| `csi0` | Broadcom bcm43455c0 (dahili) | RX, Nexmon CSI, monitor mod |

### 2.1 Neden "BlockAck sondalama" (ax210_ba)? — sahada ölçülen

2026-11-09'da cihazın bağlı olduğu modem (`loremipsum5G`, kanal 48) **802.11ax (HE), 160 MHz**.
Ölçümler (csi0'da 25 sn'lik yakalamalar):

| Kaynak / ayar | Sonuç |
|---|---|
| AX210 kendi UDP veri çerçeveleri (HE/160) | **0 kare** — bcm43455c0 (802.11ac) HE çerçevesini çözemez |
| Modem çerçeveleri @ 48/80 | ~33–47 kare/sn ama zamansal gürültü **0.10** (20 MHz legacy çerçeve, 256 tonun ~1/4'ü dolu) — kullanılamaz |
| Modem çerçeveleri @ 48/20 | ~55–61 kare/sn, gürültü 0.012–0.019 |
| **AX210 BlockAck @ 48/20** (Pi ağ geçidine 50 Hz ping atarken) | **47 kare/sn, tek PHY modu (%99.8), gürültü 0.009, RSSI −25 dBm** |
| AX210 BlockAck @ 48/20 (TCP yük) | 220 kare/sn, gürültü 0.009 |

Yönetim modundaki (managed) bir Wi-Fi kartı ancak bir erişim noktasıyla konuşurken yayın yapar;
HE modemde AX210'un veri çerçeveleri Broadcom'ca okunamaz. AX210'un **legacy (non-HT) formatta
yaydığı** çerçeve ise BlockAck'tir: modemin her cevabına AX210 bir BlockAck yayar. Pi, AX210 üzerinden
ağ geçidine sabit hızda ping atar (`PingProbeLoop`, ham ICMP soketi); Broadcom AX210 MAC'ine filtreli
olarak bu BlockAck'lerin CSI'ını okur. **Ölçülen kanal AX210 → ortam/yansıtıcı → Broadcom'dur (tek kutu)**;
modem yalnızca tetikleyicidir, AX210'un yönetim bağlantısı (SSH/web) etkilenmez. Kare hızı = sondalama
hızı (varsayılan 50 pps).

TX yöntemleri (Ayarlar → Radyo → *TX yöntemi*):

| Yöntem | Kaynak çerçeve | Nexmon kanalı | Ne zaman |
|---|---|---|---|
| `ax210_ba` (varsayılan) | AX210 BlockAck (0x94) | `<ana kanal>/20` | Modem var (802.11ax/160 MHz dahil) |
| **`ax210_action`** (gerçek tek kutu) | AX210 Public Action yönetim çerçevesi (0xd0), AP'ye adresli | `<ana kanal>/20` | AX210 herhangi bir ağa bağlı; modemin cevabına bağlı değil |
| `ap_udp` | AX210 UDP veri (0x88) | `<kanal>/80` | Yalnız AX210–modem 802.11ac/80 MHz ise (`iwlwifi disable_11ax=1`) |
| `inject` (deneysel) | AX210 enjekte veri (0x08) | `<kanal>/20` | Modemsiz; yönetim Ethernet'ten |

`fcFilter = auto` çerçeve tipini yönteme göre seçer. Kanal `auto`: AX210'un ana kanalı `iw` ile okunur.

**`ax210_action` (2026-11-09).** `ax210_ba`'da AX210, BlockAck'leri modemle olan bağlantısının
durumuna göre iki ayrık güç seviyesinde yayıyordu (§11.5); bu yüzden algılama kısmen modem
bağlantısını ölçüyordu. Kısıt: AX210 hep SSH kartı olarak bağlı kalmalı. Bu kısıt altında
denenen kaynaklar:

| Yöntem (AX210 bağlıyken) | Sonuç |
|---|---|
| AX210'da AP arayüzü | Mümkün değil: `managed + AP` ⇒ `#channels <= 1`, bağlı kanal 5 GHz ve TR'de AX210 için 5 GHz AP (IR) yok |
| Monitor vif + radiotap enjeksiyonu | 1500 çerçeve hatasız "gönderildi", Nexmon **0** gördü. Büyük olasılıkla mac80211 bunu chanctx olmadan sessizce düşürüyor (doğrulanmadı) |
| nl80211 `CMD_FRAME`, yayın Public Action | Kabul edildi, havaya çıkmadı (Nexmon 0) |
| **nl80211 `CMD_FRAME`, AP'ye adresli Public Action** | **119/119 ACK, 119/119 Nexmon'da (0xd0)**. Canlı: 52 kare/sn, boşluk yok, %97 tek RSSI durumu (−19), şekil gürültüsü 0.0175 |

Yönetim çerçeveleri en düşük temel hızda (6 Mbps OFDM, 20 MHz legacy), hız uyarlaması olmadan
gider. Çerçeveyi AX210 bizim isteğimizle yayar; AP yalnızca ACK döner, o ACK de MAC filtresiyle
elenir. Vendor-specific Public Action (yerel OUI) AP tarafından yok sayılır. Hava süresi yükü
50 pps × ~0.15 ms ≈ %0.75. Kod: `csi_nl80211.py` (bağımlılıksız genetlink), `ActionProbeLoop`.

### 2.2 Ağ taşınabilirliği

- Cihaz belirli bir ağa bağlı değildir. Açılışta (`csi-alpha-net-guard.sh`) sırayla: kayıtlı
  Wi-Fi'lardan herhangi biri (NetworkManager önceliği) → Ethernet → **kurulum hotspot'u**
  (`csi-alpha-kurulum`, 2.4 GHz, panel `http://10.42.0.1:8080`) → eski Broadcom yedeği.
- Panel **Ağ** sekmesi: tarama, yeni ağa bağlanma, kayıtlı ağlar (öncelik/unut), hotspot, panel
  parolası. Geçiş `net_switch.sh` ile ayrı bir systemd birimi olarak yapılır; başarısızsa önceki ağa,
  o da olmazsa hotspot'a döner. Servis içindeki bekçi, mgmt0 ve eth0 `autoHotspotAfterS` (180 sn)
  boyunca IP'siz kalırsa hotspot açar.
- Aynı ağdaki her bilgisayardan `http://csi-alpha.local:8080` (mDNS). Tüm ayar/kalibrasyon/profil
  cihazda saklanır; hangi bilgisayardan açılırsa açılsın aynı sistem görünür.
- AX210 2.4 GHz'de erişim noktası olabilir (5 GHz TR'de "No IR"). Modemsiz senaryoda
  (kamyonet) AX210 hotspot'unun beacon/broadcast çerçeveleri kaynak olarak kullanılabilir — bu mod
  henüz ölçülmedi (mevcut bağlantıyı keseceği için onay bekliyor).

## 3. Fiziksel Model

Alt taşıyıcı `k` için (taşıyıcı `f_c`, aralık `Δf = 312.5 kHz`):

```text
H_k(t) = g(t) · e^{j(s(t)·k + c(t))} · [ H_leak,k + Σ_p a_p e^{-j2π(f_c + kΔf)τ_p} + Σ_d a_d(t) e^{-j2π(f_c + kΔf)τ_d(t)} ] + n_k(t)
```

- `H_leak`: aynı kutudaki TX→RX doğrudan sızıntı; çok güçlü ve sabittir (quasi-monostatik düzenin
  ayırt edici özelliği). Yansımalar bu güçlü bileşenin üzerine **küçük bir modülasyon** olarak biner.
- `a_p, τ_p`: statik yansıtıcılar (duvar, kasa, branda, raf…). Statik imzayı belirler.
- `a_d(t), τ_d(t)`: hareketli saçıcılar. `f_c·τ_d(t)` terimi nedeniyle santimetrelik hareket bile
  fazı hızla döndürür → genlikte zamansal dalgalanma.
- `g(t)`: AGC kazancı; `s(t)`: örnekleme zamanı kayması (STO) → frekansla doğrusal faz;
  `c(t)`: ortak faz (CFO/CPE). Hepsi donanım kaynaklı, kare başına rastgeledir.

Sonuçlar:

1. **Hareket** = sabit kanal etrafında zamansal varyans. Ölçüt, AGC'den bağımsız olmalı.
2. **Statik değişim** = pencere ortalamasının (zaman-ortalamalı kanal şeklinin) referanstan sapması.
3. **Bant genişliği önemlidir**: AGC'yi bastırmak için her karede ortalama log-genlik çıkarılır.
   Yansıma yolunun gecikmesi küçükse ve bant darsa (20 MHz) yansımanın etkisi tüm tonlarda aynıdır
   (ortak mod) ve bu normalizasyonla silinir. 80 MHz'de kanal frekans-seçicidir ve hareket
   ton şeklinde görünür. Bu nedenle mümkünse **80 MHz** kullanılmalı; 20 MHz kurulumlar için
   isteğe bağlı "ortak-mod güç kanalı" eklenmiştir (§7.1).

---

## 4. Gerçek Veriden Çıkan Tasarım Kararları

Eski Alpha–Bravo kayıtları (`data/csi/captures_phase_20260530/`, 7 oturum, ~108 dk, 48/80,
bcm43455c0) ham pcap düzeyinde yeniden analiz edildi. Bulgular ve verilen kararlar:

| # | Bulgu (ölçülen) | Karar |
|---|---|---|
| B1 | Ham CSI **FFT sırasında**; indis 0, 1, 255 (DC, k=0,±1) ve 123–133 (guard) **çöp değer** taşıyor (ör. indis 1'de 34607, normal tonlar 300–1000). Eski kod bunları ortalamaya katıyordu. | Bant genişliğine göre geçerli ton tablosu (80 MHz: 2≤\|k\|≤122, pilotlar ±11,±39,±75,±103 hariç → 234 ton). |
| B2 | Karelerin **%22'si QoS-Data değil** (BlockAck 0x94, Action 0xD0/0xE0); bunların şekil korelasyonu ana modla ~0.15–0.2 (iyi kareler arası 0.997). | Frame-control filtresi (varsayılan `0x88,0x08`). |
| B3 | QoS-Data karelerinin kendisi de **birden çok PHY modunda**; modlar 2'şerli gruplar hâlinde iç içe geliyor ve baskın mod oturum içinde değişebiliyor (ör. 230809: ilk %20'de mod A baskın, sonra mod B). | **Mod bankası** (§6); hareket ve statik metrikler mod içinde. |
| B4 | Mod-içi genlik şekli std'si: temiz boş oturum p99 **0.0056**; geçiş p50 0.0083; el hareketi p50 0.011–0.017. Faz için aynı metrik boş ortamda ağır kuyruklu ve ayırt edici değil. | Hareket = **yalnız genlik**; faz yalnızca statik parmak izinde. |
| B5 | Temizlenmiş fazın pencere-içi sapması 0.06 rad, gruplar arası yayılımı 1.25 rad (genlikte oran ~17, fazda ~20). | Statik/durum mesafesinde genlik + faz (ağırlık 0.5). |
| B6 | Statik mesafe **boş ortamda bile** 15 dk'da ~1'den ~2.4–3.9'a sürükleniyor (termal/AGC drift). Sessizken τ=60–180 sn uyarlamayla max ≈2.2'de kalıyor. Oturumlar arası (~1 saat) fark daha büyük. | Uyarlamalı referans + **kurulumda referans tazeleme** + asgari eşik 3.0. |
| B7 | "empty" etiketli 3 oturumdan ikisinde yüksek varyanslı pencereler zamanda **kümeleniyor** (230809'un ilk ~120 sn'si, 225105'in 120–140, ~700 ve 880–950 sn'leri): kayıt başlatma/bitirme sırasında ortamda biri var. | Oturum etiketleri gürültülü; değerlendirmede "empty_noisy" ayrı raporlanır. Panelde **yer gerçeği işaretleme** eklendi. Bu bulgu eski CNN/LSTM'deki empty→passage karışmasını da kısmen açıklar. |
| B8 | 80 MHz'de ortak-mod güç kanalı ek tespit sağlamıyor (%48.7→%48.7) ama yanlış aktifliği %0→%0.1 artırıyor. | Güç kanalı varsayılan kapalı (20 MHz için opsiyon). |

Analiz betikleri motorun içine taşınmıştır; aynı sonuçlar `tools/csi_sensing/replay_eval.py` ile
yeniden üretilebilir (§11).

---

## 5. Ön İşleme

Her CSI karesi için (`extract_frame`):

1. **Ton seçimi**: FFT indisinden işaretli alt taşıyıcıya `k = idx` (idx < N/2) ya da `idx − N`;
   yalnızca geçerli veri tonları tutulur (B1). 20 MHz'de legacy-güvenli ±1..±26.
2. **Kalite kapısı**: sıfır ton oranı > %10, ortalama genlik < 2 → red.
3. **Log-genlik şekli ve AGC normalizasyonu**:
   ```text
   L_k = log10(max(1, |H_k|))
   A_g = mean_{k∈g} L_k                  (G = 56 grup; gürültü azaltma)
   a_g = A_g − mean_g A_g                (kare kazancı g(t) silinir)
   u   = a / ||a||                       (mod eşleştirme için birim vektör)
   ```
4. **Faz temizleme (unwrap'siz)**: STO eğimi, komşu tonların çapraz çarpımından:
   ```text
   ŝ = arg( Σ_{k,k+1 komşu} H_{k+1} · H_k^* )
   H'_k = H_k · e^{−j ŝ k},   ĉ = arg(Σ_k H'_k),   φ_g = arg( e^{−jĉ} Σ_{k∈g} H'_k )
   ```
   Klasik "unwrap + doğrusal fit" yöntemi gürültülü tonlarda 2π sıçramalarıyla bozulur (gerçek
   veride artık std ≈4 rad); bu kestirici sıçrama üretmez (birim testte eğim ve ortak fazın
   0.05 rad içinde kaldırıldığı doğrulanır).

---

## 6. PHY Mod Bankası

Çevrimiçi şekil kümelemesi (`ModeBank`):

```text
mod* = argmax_m <u, c_m>
<u, c_m*> ≥ ρ (=0.90)  →  c_m* ← normalize((1−η) c_m* + η u),  η = max(0.01, 1/(n_m+1))
aksi hâlde             →  yeni mod (doluysa en uzun süredir görülmeyen silinir)
```

- Bir karenin modu < 20 kare görmüşse veya yeni açılmışsa **yeni (novel)** sayılır.
- Hareket ve statik hesaplar mod içinde yapılır; sınırdaki geçici modlar (pencerede < 8 kare veya
  < %10) statik karardan dışlanır, çünkü seçilim nedeniyle yanlı ortalama taşırlar (gerçek bir hata
  olarak bulundu ve düzeltildi).
- Yakın hareket CSI şeklini tüm bilinen modların dışına itebilir → **yenilik oranı** ayrıca hareket
  kanıtıdır.

---

## 7. Dedektörler

Kararlar `hopS = 0.5 sn` aralıkla, kare zaman damgasına göre (replay ile birebir aynı) verilir.

### 7.1 Hareket

Son `W_m = 2 sn` içinde, her mod `m` için grup başına zamansal std'nin medyanı, kare sayısı
ağırlıklı:

```text
M = Σ_m n_m · median_g( std_t a_g(t) | mod m ) / Σ_m n_m
N = (yeni karelerin oranı)
P = Σ_m n_m · median_t |ℓ(t) − ℓ(t−1)| / Σ_m n_m      (opsiyonel ortak-mod güç; ℓ = mean_g A_g)
G = |{ f : tol < |rssi_f − r*| ≤ maxDb }| / |{ f : |rssi_f − r*| ≤ maxDb }|   (r* = penceredeki en sık RSSI)
skor_hareket = max( M / (θ_M·s_M), N / (θ_N·s_M), G / (max(θ_G, g_min)·s_M), [P / (θ_P·s_M)] )
```

**Kazanç/verici durumu kanalı `G` (2026-11-09 eklendi, §11.5).** Tek kutuda AX210 BlockAck'leri
iki ayrık güç durumunda (RSSI −24 / −20 dBm; CSI gücü 54.0 / 55.7 dB) gelir; iki durumun frekans
şekli aynıdır (r = 0.997), yani fark yolda değil vericinin/kazancın ayrık durumundadır. Ortam
sakinken kareler tek durumda kalır; hareket olunca kareler durumlar arasında geçer. `G` bu geçişin
payıdır; tol = 2 dB, maxDb = 12 dB (başka verici/bozuk kare dışarıda), g_min = 0.03 (2 sn·50 kare/sn'de ≈3 kare).

`θ` kalibrasyon eşikleri, `s_M` panelden eşik çarpanı.

### 7.1b Sızıntı iptalli değişim istatistiği T² (2026-11-09, `csi_residual.py`)

Tek kutuda ölçülen kanal, alt taşıyıcı k ve kare t için:

```text
H_k(t) = g(t) · e^{−j k θ(t)} · D_k^{(s)}  +  Σ_i a_i(t) e^{−j2π f_k τ_i(t)}  +  n_k(t)
```

- D^{(s)}: doğrudan AX210→Broadcom sızıntısı + statik yansımalar (verici durumu s başına).
- g, θ: kare başına karmasık kazanç (AGC, CPE, verici güç durumu) ve STO.
- Σ: insan/nesne yansımaları; ≈ −30…−40 dB.

Sızıntının büyüklüğü tek başına sorun değil. Asıl sorun, g(t)'deki %1'lik bir hatanın sızıntıyı
−37 dB'lik bir artık olarak yansımanın üstüne yazması. Çözüm, sızıntının kendisini pilot olarak
kullanıp g'yi kare kare kestirmek:

1. **STO**: θ̂ = ∠Σ_k H_{k+1} H_k^* (unwrap'sız), H ← H e^{jkθ̂}.
2. **En küçük kareler izdüşümü**: ĝ = Dᴴh / DᴴD, r = h/ĝ − D. Artık r, D'ye **tam diktir**; kazanç
   sıçramaları ve CPE r'ye hiç girmez. Yansıma ise farklı gecikme taşıdığı için alt taşıyıcılar
   boyunca döner ve D'ye dik bileşenini korur (10 ns'de ≈ %8 güç, 27 ns'de ≈ %48).
3. **Durumlar**: anahtar = (şekil-mod bankası kimliği, RSSI kazanç durumu). RSSI durumu, ±1 dB
   toleranslı kümelemeyle belirlenir. Her durumun D'si EWMA ile izlenir (τ = 30 sn) ve her durumun
   **kendi beyazlatması** vardır. Yeni durum D'yi 100 karede, kovaryansı 1000 karede öğrenir.
   Ortak beyazlatmayla, 17:46'daki durum geçişinden sonra boş odada T² medyanı 64.7 çıkıyordu. Ölçüm: AX210'un −19/−21/−23 durumlarının sızıntı şekli özdeş
   (karşılıklı artık 7×10⁻⁴), ama ortalama sapmaları farklı. Bu yüzden durum başına D gerekiyor.
4. **Bozucu beyazlatma**: x = [Re r, Im r]. Boş oda kalibrasyonundan Ledoit–Wolf büzültmeli kovaryans
   alınır, z = W(x − μ). Ton sayısı > 64 ise ardışık tonlar 64 gruba ortalanır (80 MHz'de 468
   boyutun 1 800 kareyle kestirilmesi aşırı uyuma yol açıyordu).
5. **İki pencere Hotelling testi** (yalnız baskın durumun kareleri):
   `T²(t) = (n_W n_B/(n_W+n_B)) · ‖ z̄_{[t−W,t]} − z̄_{[t−B,t−W]} ‖² / dim`. H0 altında ≈ χ²_dim/dim;
   son sürümde boş oda medyanı 0.95. Kuyruk χ²'den kalın; nedeni kovaryansın zamanla değişmesi
   (ayrıntı: `csi-algoritma-matematik-tr.md` §22.6). W = 3 sn, B = 20 sn. Baskın durum değişince B sn yerleşme beklenir.
6. **Yeniden gönderimler atılır** (aynı sıra-kontrol alanı art arda): verici yeniden gönderimi
   farklı ayarla yapıyor. Atılmazsa boş odada 29 yanlış küme çıkıyor, atılınca 2.
7. Eşik, kalibrasyon kareleri aynı dedektörden geçirilerek robust olarak bulunur: max(med + 8·MAD,
   P99.5·1.2, 2.0). Skor hareket kanalına `T²/θ_T²` olarak katılır.

Denenip elenenler: Doppler bant enerjisi (0.3–25 Hz) ve en büyük özdeğer (Roy) testi. Bu mesafede
insanın etkisi hızlı dönen bir yansıma değil, çok yollu yapıda yavaş bir değişim. Enerji neredeyse
tamamen < 0.3 Hz'de.

### 7.2 Statik değişim (nesne varlığı / kalıcı değişim)

Son `W_s = 4 sn` içindeki her önemli mod için zaman ortalaması `ā_g`, dairesel ortalama faz `φ̄_g`;
referans (`μ, σ`) kalibrasyonda **pencere-seviyesi** parçaların ortalaması ve sapmasıdır (canlı karar
da pencere ortalamasıyla verildiği için):

```text
D = sqrt( ( Σ_g ((ā_g − μ_g)/σ_g)² + w_φ Σ_g (wrap(φ̄_g − μ^φ_g)/σ^φ_g)² ) / (G (1 + w_φ)) )
skor_statik = D / ( max(θ_S, D_min) · s_S )            (D_min = 3.0)
```

- Mod eşleştirme kalibrasyon referansının birim şekliyle yapılır (ρ − 0.05). Bilinen mod varsa karar
  onunla verilir; yalnızca yeni bir modun belirmesi (hız adaptasyonu) değişim sayılmaz. Hiçbir mod
  eşleşmiyorsa skor 3.0 (büyük değişim ya da tamamen yeni PHY).
- **Sessizlik koşulu**: statik karar, hareket dedektörü aktif değilken ve anlık hareket skoru < 1
  iken en az `staticQuietS` süre geçtikten sonra güncellenir (yürüyen insan "kalıcı değişim"
  sanılmasın).
- **Uyarlama**: sessiz, normal (skor < 0.6) ve alarm yokken `μ ← (1−α)μ + α ā`, `α = hop/τ`
  (τ = 180 sn varsayılan). Bu sırada eşi olmayan kalıcı yeni modlar otomatik referansa eklenir.
- **Kurulumda tazeleme** (`rebaselineOnStart`): başlatınca/kurunca ilk sessiz pencere statik
  referans olur. Hareket eşikleri kalibrasyondan kalır. (B6: saatlik drift sahte alarm üretmesin.)
- **Otomatik yeni referans** (`staticAutoRelearnS`, oda senaryosunda 900 sn): statik alarm hareketsiz
  bu kadar sürerse yeni durum normal kabul edilir. Güvenlik senaryolarında 0 (asla).

### 7.3 Durum profilleri (açık küme)

Her etiket `ℓ` için kayıttan mod referansları ve kendi parçalarının mesafe dağılımının p99'u `q_ℓ`.

```text
r_ℓ = D_ℓ / max(1.5, q_ℓ)
r* > openSetFactor (2.0)  → "bilinmeyen"
(r₂ − r₁)/(r₂ + r₁) < stateMargin → "belirsiz"
son N kararın K'sı aynı etiket → kararlı durum; alarm etiketlerinden birine geçiş → alarm
```

Eski `reflection_live` (kapalı/açık en yakın profil) bu genellemenin özel hâlidir; fark: ön işleme
(B1–B3), mod bazlı karşılaştırma ve açık küme reddi.

### 7.4 Mikro-hareket (deneysel)

Hareketsizlik ≥ `W_µ = 20 sn` iken birincil modun en değişken 8 grubu 4 Hz'e doğrusal
interpolasyonla yeniden örneklenir, doğrusal trend çıkarılır, Hann penceresi uygulanır;
0.05–1.9 Hz DFT gücünde `max_{f∈[0.15,0.6]} P(f) / median_f P(f)` (tepe/medyan, ölçekten bağımsız).
Sentetik solunumda (0.25 Hz, 8 mm) doğrulandı; **gerçek veride henüz doğrulanmadı**, varsayılan
kapalı (araç kasası senaryosunda açık).

### 7.5 Sinyal ve sabotaj

- Geçerli kare hızı < `minPacketRate` (5/sn), `signalLossS` (5 sn) sürerse → `signal_loss`.
  Sinyal oturumda en az bir kez iyi olmadan alarm verilmez (açılış).
- RSSI medyanı kalibrasyona göre `rssiDropDb` (10 dB) düşük, `rssiHoldS` sürerse → `rssi_drop`
  (anten kapatma, cihazın örtülmesi).

---

## 8. Kalibrasyon İstatistiği

Kalibrasyon sırasında her karar anında `M, N, P, µ` ve parçalar için `D` toplanır:

```text
θ = max( median + k · 1.4826 · MAD ,  P_q · m ,  taban )      k=8, q=99.5, m=1.2
```

(statik ve mikro için `k/2`). MAD tabanlı terim tek tük aykırı pencereye dayanıklıdır; yüzdelik
terim dağılım çarpık olduğunda alt sınır verir. Kalibrasyon kalite uyarıları:
düşük geçerli kare hızı (< 10/sn), düşük kabul oranı (< %50), `p99/p50 > 4` (kalibrasyonda hareket
olmuş olabilir), > 3 PHY modu. Kalibrasyon; kanal, TX MAC, nfft ve pencere ayarlarını saklar,
uyumsuzlukta panel yeniden kalibrasyon ister.

---

## 9. Karar Katmanı

- **K-of-N giriş**: son N kararın en az K'sında skor ≥ 1 (hareket 3/4 → ~1.5–2 sn gecikme).
- **Histerezis çıkış**: skor < `releaseRatio·1` (0.9) ardışık `offCount` kez. Not: kalibre eşikte
  normal skor ~0.6–0.75'tir; 0.7 gibi bir bırakma oranı alarmı takılı bırakıyordu (testte bulundu).
- **Bekleme (cooldown)** ve **kurulu/devre dışı** (devre dışıyken karar hesaplanır, alarm kaydı yok).
- Kalibrasyon/profil kaydı ve ilk `max(W_m, W_s) + 1` sn ısınma süresince dedektör güncellenmez.

---

## 10. Senaryolar ve Varsayılanlar

| Parametre | Oda | Araç kasası | Kapı | Depo |
|---|---|---|---|---|
| Hareket penceresi | 2 sn | 2 sn | 1.5 sn | 2 sn |
| Hareket eşik çarpanı | 1.0 | 1.0 | 1.0 | 1.5 |
| Statik pencere | 4 sn | 4 sn | 3 sn | 8 sn |
| Statik eşik çarpanı / asgari | 1.0 / 3.0 | 1.0 / 3.0 | 1.0 / 3.0 | 0.8 / 2.5 |
| Referans uyarlama τ | 180 sn | 600 sn | 180 sn | 1800 sn |
| Otomatik yeni referans | 900 sn | asla | asla | asla |
| Alarm etiketleri | — | open, acik, branda_acik, kesik | open, acik | — |
| Mikro-hareket | kapalı | **açık** | kapalı | kapalı |
| RSSI düşüşü | kapalı | **açık** | kapalı | kapalı |
| Sinyal kaybı süresi | 5 sn | 3 sn | 5 sn | 5 sn |
| Alarm bekleme | 10 sn | 30 sn | 10 sn | 10 sn |

Tüm 59 parametre panelde **Ayarlar ve parametreler** altında açıklamalarıyla listelenir
(⟳ işaretliler yeniden başlatınca etkili, diğerleri anında). Kalıcı dosyalar (Pi):

```text
/home/admin/csi/config/sensing_config.json       ayarlar
/home/admin/csi/models/sensing_calibration.json  kalibrasyon (eşikler + mod referansları)
/home/admin/csi/models/sensing_profiles.json     durum profilleri
/home/admin/csi/captures/*.pcap                  ham CSI
/home/admin/csi/captures/*.sensing.ndjson        her karar anı (2 Hz) – tez grafikleri için
/home/admin/csi/captures/*.events.ndjson         alarmlar + işaretlenen yer gerçeği
```

---

## 11. Değerlendirme

### 11.1 Protokol

Canlı servisle **aynı** motor kodu pcap üzerinde yeniden oynatılır:

```bash
python3 tools/csi_sensing/replay_eval.py \
  --calib <kalibrasyon.pcap>@0:300 \
  --session empty=<test.pcap> --session passage=<test2.pcap> \
  --negative empty --preset room \
  --json rapor.json --md rapor.md [--timeline-dir zaman_cizelgeleri/]
```

Yer gerçeği varsa (`.events.ndjson` veya `--gt dosya.pcap=bas:bit,...`) olay-seviyesi recall,
tespit gecikmesi ve yanlış alarm/saat raporlanır. Panelde üretilen kalibrasyon da kullanılabilir
(`--calibration-json`).

### 11.2 Gerçek veri sonuçları (Alpha–Bravo kayıtları üzerinde)

> Önemli: Bu kayıtlar **bistatik** (Bravo verici, 2 m) düzendendir; tek kutu kaydı henüz yoktur.
> Sonuçlar ön işleme ve dedektörlerin gerçek Nexmon verisinde çalıştığını gösterir; tek kutu
> başarımı ayrıca ölçülmelidir (§12).

Raporlar: `data/csi/reports/single_box_replay_E1_2026-11-09.md`, `..._E2_2026-11-09.md`.

**E1** — kalibrasyon: temiz boş oturum 235132'nin ilk 300 sn'si; test: aynı oturumun kalanı + diğer 6 oturum.
**E2** — kalibrasyon: *farklı* oturum 225105'in temiz 150–600 sn aralığı; test: 235132'nin tamamı + diğerleri.

| Oturum (etiket) | Süre | E1 hareket aktif % | E1 alarm | E2 hareket aktif % | E2 alarm |
|---|---:|---:|---|---:|---|
| 235132 (empty, temiz) | 11.3 / 16.3 dk | **0.0** | **0** | **0.0** | **0** |
| 225105 (empty_noisy) | 16.5 dk | 7.9 | 5 hareket, 1 sinyal | 7.9 | 5 hareket, 1 sinyal |
| 230809 (empty_noisy) | 18.3 dk | 13.2 | 5 hareket, 1 sinyal | 13.3 | 5 hareket, 1 sinyal |
| 002230 (passage) | 15.7 dk | 53.3 | 41 | 55.5 | 41 |
| 003846 (passage) | 15.4 dk | 52.6 | 38 | 55.4 | 38 |
| 010105 (hand_motion) | 11.7 dk | 78.7 | 37 (+1 statik) | 96.2 | 10 |
| 012008 (hand_motion) | 13.7 dk | 97.6 | 7 | 99.2 | 2 |

Özet:

- **Temiz boş ortamda 0 yanlış alarm** (E1 11.3 dk, E2 16.3 dk — aynı oturum; E2'de kalibrasyon başka oturumdan).
- **Tüm hareket oturumları alarm üretti (4/4)**; hareket pencerelerinin %53–99'u aktif. Oturum
  etiketleri boş aralar içerdiği için %100 beklenmez.
- "empty_noisy" oturumlardaki alarmların zamanları (225105: 124, 711, 893–966 sn; 230809: 6–77 sn),
  ham veride bağımsız olarak bulunan insan-varlığı kümeleriyle (B7) örtüşür → büyük olasılıkla doğru
  tespitlerdir; yer gerçeği olmadığı için kesin değildir.
- Pencere-seviyesi ROC-AUC 0.81 (oturum etiketleriyle; etiket gürültüsü nedeniyle **alt sınır**).
- Kalibrasyonun oturumlar arası transferi: E2'de eşikler başka oturumdan geldiği hâlde boş ortam
  sessiz kaldı ve tespit oranı arttı.
- 010105'teki tek statik alarm: kişi masada oturduğu için statik imza değişmiştir (statik mesafe
  10–15, eşik 3; eşleşmeyen mod payı 0) → "ortamda kişi var" doğru tespitidir.
- Hesap maliyeti: 108 dk veri Mac'te ~15 sn'de işlendi (≈400× gerçek zaman); Pi 5 için bol pay.

**Yanlış alarm oranı hakkında dürüst not**: E1'in negatif kısmı E2'deki oturumun alt kümesidir;
bağımsız temiz negatif süre yalnızca 16.3 dk'dır. 0 olay, "üçler kuralı"na göre %95 güvenle
yalnızca FAR < ~11/saat demektir. Tezde FAR iddiası için **≥ 8 saat** (ideali 24 saat)
boş ortam kaydı gerekir (§12).

### 11.3 Sentetik senaryo testleri

`tools/csi_sensing/test_sensing_engine.py` (17 test, `python3 -m unittest ...`):
fiziksel modele dayalı üreteç (`synth_csi.py`: sızıntı + statik yollar + hareketli/solunum yolu,
taşıyıcı terimi, AGC, STO/CPE, PHY modları, çöp çerçeveler, drift) ile:

| Test | Sonuç |
|---|---|
| Hareket (80 MHz) | alarm < 4 sn, öncesinde alarm yok |
| 15 dk statik + drift + %30 ikinci mod + %25 çöp | 0 alarm |
| Frame filtresi kapalıyken çöp çerçeveler | mod bankası ayırır, 0 alarm |
| Kalıcı statik değişim (yol kaldırma) | < 15 sn alarm, kalıcı |
| Otomatik yeni referans | alarm temizlenir |
| Sinyal boşluğu (12 sn) | 4–8 sn içinde `signal_loss` |
| RSSI −18 dB | `rssi_drop` |
| Devre dışı | alarm kaydı yok, bastırılmış olay var |
| Durum profilleri kapalı/açık/kapalı/bilinmeyen | doğru sırayla, açık-küme reddi çalışıyor |
| Solunum (0.25 Hz) | `micro_motion` alarmı, öncesinde yok |
| Ton düzeni, faz temizleme, AGC değişmezliği, pcap gidiş-dönüş, config sınırları | geçti |

Uçtan uca (Mac'te gerçek sunucu + gerçek zamanlı sentetik akış): kalibrasyon → hareket alarmı →
canlı parametre değişikliği → profil kaydı → statik alarm; panelin ürettiği kalibrasyon ve olay
işaretleriyle offline değerlendirme: recall 2/2, gecikme p50 1.25 sn, 0 yanlış alarm.

---

### 11.4 Zaman serisi analizi (panel)

Karar katmanı tamamen zaman serisi üzerinedir (pencereli mod-içi std, K-of-N, uyarlamalı referans).
Panel bunu görünür kılar:

- **Canlı**: 15 dk karar geçmişi cihazda tutulur (2 Hz). Çok izli grafik: skorlar (eşik = 1),
  hareket metriği ve kalibre eşiği, statik z-mesafe ve eşiği, kare hızı, RSSI; alarm bantları ve
  işaretli olaylar. Son 60 sn'lik tam hızlı grup genlikleri tarayıcıda işlenir:
  1. birincil PHY modu seçilir, 20 Hz'e doğrusal interpolasyonla yeniden örneklenir,
  2. grup ortalaması çıkarılır → **alt taşıyıcı sapma ısı haritası**,
  3. G×G kovaryansın güç yinelemesiyle **PCA-1** (en güçlü ortak değişim yönü),
  4. PCA-1 üzerinde **STFT** (Hann, 64 örnek = 3.2 sn, adım 8) → spektrogram (0–10 Hz).
- **Analiz**: kayıtlı bir pcap aynı motorla yeniden oynatılır (cihazdaki ya da kaydın bir
  aralığından yapılan kalibrasyonla); aynı zaman eksenine hizalı skor izleri, PCA-1, spektrogram
  (numpy, 10 sn kenar-normalize hareketli ortalamayla drift giderilmiş) ve ısı haritası; işaretli
  olaylardan recall / gecikme / yanlış alarm-saat; CSV/JSON dışa aktarım. Pi 5'te 33 sn kayıt 0.6 sn.

İlk gerçek tek kutu ölçümü (2026-11-09, `ax210_ba`, 48/20, 60 sn boş kalibrasyon):
kare 53.7/sn, kabul %100, tek mod; hareket metriği p50 0.0089, p99 0.0094 (p99/p50 = 1.07, bistatik
veriden daha kararlı) → eşik 0.0114. Cihaz yeri değiştikten sonraki ikinci kalibrasyon: p50 0.0043.

### 11.5 İlk tek kutu hareket testi (2026-11-09, oda)

Düzen: cihaz yatağın başında duvarda; ~1 m yanında masa/sandalye; kapı odanın karşı ucunda
(yaklaşık 4–5 m, L biçimli oda); dolap kapı tarafında. 60 sn boş kalibrasyondan sonra panelde
işaretlenen 17 olay: kapı açıldı ×4, kapı kapandı ×4 (2.9–7 sn), insan girdi-çıktı ×9 (9.7–13.5 sn).
Kayıt: `csi-alpha-web-20261109-151850.pcap` (1218 sn, 68 172 BlockAck karesi, 56 kare/sn).

| Kanal (aynı kalibrasyon, 3/4 histerezis) | Yakalanan | Gecikme |
|---|---|---|
| `M` mod içi şekil std'si (önceki motor) | **0 / 17** | – |
| Aynı kazanç durumu içinde ortak güç dalgalanması | 0 / 17 | – |
| Şekil PCA / STO eğimi (1 sn ortalama) | göze çarpan tepki yok | – |
| **`G` kazanç durumu geçişi** (yeni motor, replay) | **17 / 17** | kapı ≤ 0.8 sn, insan 2.1–3.8 sn |

- Olaylarda `M` değeri 0.0085–0.0099, boş ortamdaki p99 0.0094: şekil kanalı gürültü tabanında.
  Nedeni fiziksel: verici ve alıcı aynı kutuda olduğu için doğrudan sızıntı baskın; birkaç metredeki
  yansıma −40 dB civarında kalıyor. Gecikmesi bant genişliğine göre çok kısa olduğundan şekli değil
  tonların ortak genliğini değiştiriyor; ön işleme bu ortak genliği AGC normalizasyonuyla atıyor.
- `G` sessiz kalibrasyon penceresinde sürekli 0. Kapı olaylarında 0.035–0.10, insan geçişlerinde
  0.19–0.50.
- İşaretsiz zamanlarda 22 alarm var (kalibrasyondan önce, kalibrasyonla ilk olay arası, son olaydan
  sonra). Bunlar kullanıcının cihaza ~1 m mesafedeki masa başı hareketi ve dönüş yürüyüşüyle uyumlu,
  ama işaretlenmedikleri için yanlış alarm sayılıp sayılmadıklarına karar verilemez. FAR için
  §12 madde 3'teki uzun boş kayıt gerekiyor.
- **Geçerlilik tehdidi:** iki durumun şekli aynı ve RSSI ara değer almadan 4 dB'lik basamakla
  değişiyor. Bu, değişimin verici tarafında olduğunu gösteriyor: AX210, BlockAck'i modemle olan
  bağlantısının durumuna göre ayrık güç/zincir seçimiyle yayıyor (açık döngü TPC hipotezi). Bu
  durumda `G`, sadece AX210 → Broadcom yansımasını değil, modem ↔ AX210 bağlantısının insan tarafından
  bozulmasını da ölçüyor. Modemsiz ortamda (kamyonet) bu mekanizma olmayabilir; `ax210_ap` modunda
  ayrıca ölçülmeli. Doğrulama için `iw dev mgmt0 station dump`'taki sinyal ve TX gücü `G` ile eş
  zamanlı kaydedilmeli.

**İkinci test (16:25–16:55, aynı oda; modem masanın sağ üst köşesinde, cihaza ~2 m).** Cihaz–modem
hattının dışında yapılan olaylar ve ilk boş oda kaydı. Kayıt `csi-alpha-web-20261109-154704.pcap`.
AX210 bağlantısı (`iw station dump`, 2 Hz) da eş zamanlı kaydedildi.

| Olay | `G` ile yakalanan (motor replay) |
|---|---|
| `hat_disi_uzak`: yatak ayak ucu ile dolap arası yürüme | 4 / 5 |
| `hat_disi_yakin`: yatakta cihaza ~1.5 m, el sallama | **1 / 6** |
| `oda_bos`: 909 sn boş oda | **6 alarm** (16:47 ve 16:51–16:53 arası, 2 küme) |

- Cihazın yakınındaki ama hattın dışındaki hareket büyük ölçüde görülmüyor; modem tarafındaki
  yürüme görülüyor. Bu, `G`'nin ağırlıkla modem ↔ AX210 bağlantısının bozulmasını ölçtüğü
  hipotezini destekliyor; saf tek kutu yansıması zayıf kalıyor.
- AX210'un NSS 1↔2 geçişleri `G` ile ilişkisiz (G aktifken %1.8, sakinken %1.9). AP sinyalinin
  std'siyle korelasyon da zayıf (r = 0.14). Yani durum geçişinin kaynağı NSS seçimi değil;
  mekanizma hâlâ açık.
- Boş oda alarmları ya ev içinde başka birinin (komşu oda/koridor, modem duvarının arkası)
  hareketinden ya da yanlış alarmdan kaynaklanıyor. Kullanıcıya soruldu; ayrılmazsa FAR üst
  sınırı ≈ 24/saat (alarm) ≈ 8/saat (küme).
- **Nexmon takılması:** 16:55:45'te `csi0`'a CSI gelmesi durdu (AX210 ping'leri normaldi). Yakalama
  artık 20 sn kare gelmezse kendini yeniden başlatıyor (`RX_STALL_RESTART_S`), en fazla 2 dk'da bir.

### 11.6 `ax210_action` ile test (2026-11-09, 17:17–17:51)

60 sn kalibrasyon, 20 işaretli olay, 15.5 dk boş oda. Kayıt: `csi-alpha-web-20261109-171655.pcap`
(105 556 Public Action karesi, 51.4 kare/sn).

| Olay (cihaza uzaklık) | `G` (durum geçişi) | Üst zarf gücü `U` (0.5 sn P90, durum içi) |
|---|---|---|
| `hat_disi_yakin`: yatakta el sallama (~1.5 m) | sessiz | **5 / 5** (0.06–1.3 dB) |
| `sandalye`: oturup kalkma (~1 m) | sessiz | **3 / 3** |
| `kapi_acildi` / `kapi_kapandi` (~4–5 m) | sessiz | 1 / 7 |
| `insan_girdi` (kapı tarafı) | sessiz | 3 / 5 (sınırda) |
| `bos_oda` (15.5 dk) | 17:46'daki durum değişiminde sürekli yüksek | **20–28 yanlış küme** |

- **`G` bu kaynakta hiçbir olaya tepki vermedi.** Bu, `ax210_ba`'daki 17/17 sonucunun modem ↔ AX210
  bağlantı uyarlamasından geldiğinin doğrudan kanıtı (§11.5).
- **Gerçek tek kutu yansıması ortak güçte görünüyor, menzili kısa.** Cihazın ~1.5 m yakınındaki
  hareket 8/8 yakalandı; 4–5 m'deki kapı görülmüyor. Bu, doğrudan sızıntının baskın olduğu monostatik
  düzenle uyumlu.
- **Gürültü kaynağı, ayrık güç durumları.** Bir yanda 0.12–0.15 dB aralıklı seviye titreşimi var
  (boş odada da açılıp kapanıyor), öbür yanda ~2 dB'lik RSSI durumları (−19/−21/−23; 17:46'da
  kalıcı geçiş). AX210 gücü 10 dBm'e sınırlandığında da iki durum sürdü (−28/−26). Yani güç
  basamağı değil. En olası açıklama, iwlwifi'ın yönetim çerçevelerinde TX anteni döndürmesi
  (doğrulanmadı).
- **Anten sabitleme mümkün değil.** `iw phy phy0 set antenna 0x1 0x3` → `Operation not supported`
  (iwlmvm `set_antenna` uygulamıyor).
- **Durumların kaynağı kare bazında verici kararı.** 105 556 karenin 2 993'ü yeniden gönderim (aynı
  sıra no). Yeniden gönderimlerin %67'si azınlık durumunda; normal karelerde bu oran %14. Azınlık
  kareleri çoğunlukla tekil (2 036 koşu uzunluğu 1). AX210 yeniden gönderim sayacıyla korelasyon
  0.07; komşu ağ girişimi verilerle desteklenmiyor.
- **Kapılı detektör** (yeniden gönderim atılır, yalnız baskın RSSI durumu, 0.5 sn P90 üst zarf,
  10 sn tabandan sapma, durum değişince 10 sn yerleşme):

  | Eşik (dB) | Yakın (8) | Uzak (12) | Boş oda yanlış küme (15.5 dk) |
  |---|---|---|---|
  | 0.026 (otomatik) | 8 | 4 | 20 |
  | 0.05 | 8 | 0 | 16 |
  | 0.10 | 2 | 0 | 6 |
  | 0.15 | 0 | 0 | 0 |

  Boş odadaki 0.12 dB'lik seviye titreşimi, yakın hareketin etkisiyle aynı büyüklükte. Bu donanımla
  saf monostatik algılamada yakalama ve yanlış alarm aynı anda sağlanamıyor.
- **Sonuç (tez için):** Aynı kutudaki ticari kartlarla monostatik CSI algılama iki şeyle sınırlı.
  (i) Doğrudan sızıntı menzili ~1–1.5 m'ye indiriyor. (ii) Vericinin ayrık kare bazında
  güç/anten durumları, yansıma etkisi kadar büyük bir gürültü üretiyor. Pratik algılama
  `ax210_ba`'da, yani AP ↔ AX210 bağlantısının bozulmasından geliyor; bu bistatik bir algılama.
  Kamyonette ağın AP'si kasanın öbür ucuna konursa bağlantı kasayı keser ve bu düzen bilinçli
  bir tasarım hâline gelir. G kanalı artık yalnız `ax210_ba`'da etkin.

### 11.7 Sızıntı iptalli T² ile aynı kayıt (§11.6 verisi)

| Yöntem | Yakın (8) | Uzak 4–5 m (12) | Boş oda yanlış küme (15.5 dk) |
|---|---|---|---|
| Önceki motor (şekil std, novelty) | 0 | 0 | 0 |
| Üst zarf gücü, yeniden gönderim atılmış | 8 | 0–4 | 16–20 |
| T² çevrimdışı (durum başına D, W3/B20) | 8 | 10 | 1 |
| T² motor (ara sürüm, ortak beyazlatma) | 6 | 8 | 1 küme, ama ~4 dk sürdü (%24) |
| **T² motor (son: durum başına beyazlatma)** | **6** | **9** | **3 kısa küme, sürenin %1.7'si** |

Ek kontrol: 15:47 boş odası (`ax210_ba`, 15:10 kalibrasyonu, yalnız T²): 15.1 dk'da 8 alarm (%7.8).
Hepsi G'nin de sıçradığı anlarda; `ax210_ba`'da bunun modem bağlantısından mı ortamdan mı geldiği
ayrılamaz. CUSUM denendi ve elendi (kalın kuyrukta takılı kalıyor).

Uyarı: yöntem ve parametreler bu kayıt üzerinde seçildi. Bağımsız bir `ax210_action` kaydıyla
doğrulanması gerekiyor (§12). `ax210_ba` kayıtları (15:18, 15:47) modem bağlantısı etkisi
içerdiği için saf yansıma doğrulaması sayılmaz.

### 11.8 Bağımsız boş oda testi (19:20–19:40, parametrelere dokunulmadan)

Kullanıcı 19:12:56'da yeniden kalibre etti (sızıntı modeli: artık −27.85 dB, baskın durum −21, T²
eşiği 2.0 = alt sınır; kalibrasyon p50 0.80, p99 1.25). Ardından 20.5 dk boş oda kaydı alındı.
Kayıt: `csi-alpha-web-20261109-190248`. Canlı motorun ürettiği değerler:

- T² medyan 0.91, p95 1.44, p99 2.0, en çok 20.1. Hareket alarmında geçen süre **%1.2**.
- 5 kısa alarm (her biri 2–4 sn). 4'ü zayıf (T² 2.3–2.7), 1'i güçlü (19:40:03, T² 17.2; "Bitti"
  işaretinden 47 sn önce).
- Kullanıcıya göre bu sürede salonda oda kapısının yakınında bir kez hareket oldu; salonun tam
  altında da alt kat var. Kapı ~4–5 m'deki hareketin algılandığı mesafe; dolayısıyla güçlü alarm
  büyük olasılıkla gerçek, oda dışı bir hareket. Zayıf olanların kaynağı belirsiz (alt kat ya da
  kuyruk).
- Kalibrasyon p99 (1.25), boş oda p99'unu (2.0) hafife aldı. Bu, `csi-algoritma-matematik-tr.md`
  §22.6'da öngörülen kuyruk sorunu.

| T² alt eşiği | Bu boş oda | 17:16 kaydı: yakın / uzak |
|---|---|---|
| 2.0 | 3–5 alarm, %0.8–1.2 | 6/8, 9/12 |
| 2.5 | 1 alarm, %0.2 | 6/8, 7/12 |
| 3.0 | 0 | 7/8, 5/12 |

Yorum: odada sistem, kapının içindeki ve dışındaki 4–5 m'lik hareketi ayıramıyor; eşik bir
menzil–yanlış alarm ödünleşimidir. Metal kasada (kamyonet) dış hareket büyük ölçüde kalkanlanır.
Öneri: oda/ev için `residualMinThr` 2.5 ve 3–5 dk kalibrasyon; kasa için 2.0.

### 11.9 Ön kayıtlı bağımsız test, 1. kısım: boş oda (19:55–20:14)

**Ön kayıt** (2026-11-09 19:49'da, test başlamadan sabitlendi; değerlendirmede değiştirilmedi):
`ax210_action`, kalibrasyon 180 sn, `residualMinThr` 2.0 (eşik kalibrasyondan otomatik), W 3 / B 20 /
τ 30, K-of-N 3/4, senaryo room. Protokol: boş odada kalibrasyon, ardından ≥ 20 dk `bos_oda`, sonra
olay denemeleri. Ölçütler: olay başına yakalama (olay −1 … +5 sn), boş odada alarm sayısı ve alarm
süresi oranı. Kullanıcının bildirdiği oda dışı hareketler not edilir, ama boş oda sayımından çıkarılmaz. Değerlendirme, canlı motorun kendi alarm ve satır kayıtlarından yapıldı (replay yok).

- **Kalibrasyon** (19:52–19:55, 9 004 kare, 50 kare/sn): artık −28.04 dB, T² eşiği **2.19**
  (otomatik), kalibrasyon p50 0.87, p99 1.34. Uyarı yok.
- **Boş oda** (iki aralık, toplam 15.5 dk): **1 alarm** (20:04:16, T² 5.1, ~5 sn). Kullanıcı o
  saatte salonda hareket olmuş olabileceğini bildirdi (kesin değil); kurala uygun olarak sayımda tutuldu. Hareket alarmında
  geçen süre %0.86. T² medyan 0.90, p95 1.25, p99 1.74, en çok 5.6. Başka dedektörde alarm yok.
- **60 sn kalibrasyona göre:** kalibrasyon p99'u ile boş oda p99'u arasındaki fark 1.25 → 2.0'dan
  1.34 → 1.74'e indi. Alarmlar 5 / 20.5 dk'dan 1 / 15.5 dk'ya düştü (≈ 15/saat → ≈ 4/saat).
  Örnek küçük; 1 olay için Poisson %95 güven aralığı ≈ 0.1–21/saat.

### 11.10 Ön kayıtlı bağımsız test, 2. kısım: olaylar (20:23–20:41)

Aynı kalibrasyon (19:55, T² eşiği 2.19), parametrelere dokunulmadı. Değerlendirme canlı motorun
çıktısından yapıldı. Ölçüt: olay başından 1 sn önce ile bitişinden 5 sn sonra arasında hareket
detektörünün aktif olması. 20:21:59'da Nexmon yine takıldı; takılma bekçisi yakalamayı 22 sn'de
yeniden başlattı (kalibrasyon aynı).

| Olay (kullanıcı tanımı) | Yaklaşık mesafe | Yakalanan | Gecikme medyan | T² tepe medyan |
|---|---|---|---|---|
| `sandalye_otur_kalk` | ~1 m | **4/4** | 2.4 sn | 12.2 |
| `hat_disi_yakin`: yatakta el sallama | ~1.5 m | **5/5** | 2.3 sn | 13.4 |
| `hareketsiz_insan_ayakta`: derin nefes, yüz cihaza dönük (58 sn) | — | **1/1**, sürenin %64'ü alarmda | 0.8 sn | 21.3 |
| `hat_disi_uzak`: kapı önü ↔ yatak ayağı yürüme | 2–4 m | **5/5** | 4.8 sn | 6.8 |
| `insan_girdi_cikti` | 4–5 m → içeri | 4/5 | 7.0 sn | 4.2 |
| `kapi_acildi` / `kapi_kapandi` | 4–5 m | 4/7 | 5.0 sn | 3.7–4.2 |
| `kapi_disi_hareket`: kapı kapalı, oda dışında | dışarıda | **1/5** (istenen: düşük) | — | 1.55 |

Oda içi olaylar: **28/32**. Yakın (≤ 2 m): 10/10. Kapı kapalıyken oda dışı hareket: 1/5; kapı ve duvar
dış hareketi büyük ölçüde ayırıyor. Aynı odada önceki motor 0/8 yakın, 0/12 uzak yakalamıştı (§11.7).
Olaylar arasındaki 18 alarm, kullanıcının denemeler arasında yer değiştirmesiyle çakışıyor; yanlış
alarm ölçüsü olarak §11.9'daki boş oda kaydı kullanılır (15.5 dk'da 1 alarm).

Gecikme mesafeyle artıyor (yakın ~2.4 sn, kapı/giriş 5–7 sn). Bunun nedeni W = 3 sn pencere ve 3/4
histerezis: uzak, zayıf bir değişimin eşiği geçmesi için pencerenin dolması gerekiyor.

### 11.11 Karşılaştırma, ablasyon ve eşik taraması

Ayrıntı ve tablolar: `data/csi/reports/single_box_bench_2026-11-09.md`. Özet: eşit yanlış alarm
bütçesinde T² 21/27 (genlik varyansı 15/27, arka plan çıkarma 2/27, önceki motor 0/27). Kovaryans
beyazlatma belirleyici bileşen. Ortak güç kanalı hareket değil, varlık/yakınlık detektörü gibi
davranıyor. Oda için önerilen eşik çarpanı 1.25–1.5.

## 12. Tek Kutu Deney Protokolü (sahada yapılacak)

Her ortamda aynı adımlar:

1. Kurulum: cihaz sabit, antenler sabit; Radyo teşhisinde uyarı yok; geçerli kare hızı ≥ 25/sn.
2. **Kalibrasyon**: ortam normal ve boşken 120 sn (Kalibre et). Uyarıları not edin.
3. **Uzun negatif kayıt**: ≥ 2 saat boş (gece). → FAR/saat.
4. **Olaylı kayıt** (her olay ≥ 20 tekrar, araları ≥ 30 sn boş; panelde **Başladı/Bitti** ile işaretleyin):

| Ortam | Olaylar |
|---|---|
| Oda | giriş-çıkış, yürüme (1/3/5 m), el sallama, oturma (hareketsiz insan → mikro), sandalye taşıma (statik) |
| Kapı | aç-kapa, yarım açık bırakma, geçiş |
| Kamyonet kasası / maket | kapı/branda açma, içeri girip çıkma, içeride hareketsiz bekleme, kutu bırakma/alma, motor çalışırken titreşim (negatif), cihazı örtme (RSSI) |
| Depo/raf | nesne bırakma/alma (farklı boyut/malzeme: metal, karton, su şişesi), uzaktan geçiş |

5. Durum profilleri (kapı/kasa): `kapali` ve `acik` için 2'şer kayıt (30–60 sn); test sırasında
   bilinmeyen durum (ör. yarım açık) deneyin.
6. Değerlendirme: `replay_eval.py --calibration-json ... --session ...` → recall, gecikme p50/p90,
   FAR/saat; senaryo başına bir tablo.

Önerilen kabul ölçütleri: hareket recall ≥ %95, gecikme p90 ≤ 3 sn; statik değişim recall ≥ %90,
gecikme p90 ≤ 10 sn; boş ortamda FAR ≤ 1/saat.

Ek deneyler (tez gücü): TX pps (20/40/80) ve kanal genişliği (20/80 MHz) taraması; anten arası
mesafe/metal perde ile sızıntının etkisi; `ap_udp` vs `inject`; mesafeye göre tespit (1–6 m).

---

## 13. Sınırlamalar ve Geçerlilik Tehditleri

- Gerçek veri değerlendirmesi bistatik kayıtlarla yapıldı; tek kutuda güçlü sızıntı modülasyon
  derinliğini azaltır. Kalibrasyon-göreli eşikler bunu telafi eder, ama erişim mesafesi kısalabilir.
- Sürekli titreşim (çalışan araç, vantilatör) hareket gibi görünür; güvenlik senaryosu "park hâlinde
  kurulu" varsayar.
- Durum profilleri uyarlanmaz; uzun süreli drift'te "bilinmeyen" kararına kayabilir — profilleri
  dönemsel yenileyin.
- `inject` TX yöntemi ve mikro-hareket gerçek donanımda henüz doğrulanmadı.
- Tek RX anteni (bcm43455c0) → açı/konum kestirimi yok; algılama varlık/değişim düzeyindedir.

---

## 14. Dosya Haritası

| Dosya | İçerik |
|---|---|
| `device-files/csi-alpha-webui/webui/csi_sensing.py` | Motor (stdlib): şema/senaryolar, ön işleme, mod bankası, dedektörler, kalibrasyon, pcap okuma |
| `device-files/csi-alpha-webui/webui/csi_web.py` | Servis: TX (ax210_ba/ap_udp/inject), radyo teşhisi, motor entegrasyonu, API |
| `device-files/csi-alpha-webui/webui/csi_timeseries.py` | Canlı geçmiş satırları, kayıt yeniden oynatma, PCA-1, STFT, ısı haritası |
| `device-files/csi-alpha-webui/webui/csi_network.py` | Ağ yönetimi (nmcli), hotspot, panel parolası, bekçi |
| `device-files/csi-alpha-webui/webui/{index.html,app.js,charts.js,style.css}` | Panel (Canlı/Analiz/Kurulum/Ağ/Ayarlar/Kayıtlar) |
| `device-files/csi-alpha-robust/home-csi/net_switch.sh` | Güvenli ağ geçişi (geri dönüşlü) |
| `device-files/csi-alpha-robust/usr-local-sbin/csi-alpha-net-guard.sh` | Açılış ağ sırası (ağ adından bağımsız) |
| `device-files/csi-alpha-webui/deploy.sh` | Mac → Pi dağıtım |
| `device-files/csi-alpha-robust/etc-modprobe.d/iwlwifi-csi.conf` | İsteğe bağlı 11ax kapatma |
| `tools/csi_sensing/replay_eval.py` | Offline değerlendirme |
| `tools/csi_sensing/synth_csi.py` | Fiziksel model tabanlı sentetik CSI |
| `tools/csi_sensing/test_sensing_engine.py` | Birim/senaryo testleri |
| `data/csi/reports/single_box_replay_E{1,2}_2026-11-09.{md,json}` | Gerçek veri sonuçları |

API (özet): `GET /api/history?seconds=900`, `GET /api/recordings`, `POST /api/analyze {name, calibrateFrom?, calibrateTo?}`,
`GET /api/network`, `POST /api/network/{scan|connect|activate|forget|priority|hotspot|config}`, `GET /api/sensing[?radio=1]`, `POST /api/sensing/config {config|reset|applyScenario}`,
`POST /api/sensing/command {command: calibrate|profile|cancel|accept_baseline|arm|delete_profile|mark}`,
`POST /api/start {mode: single_box|motion}`.
