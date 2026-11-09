# Tek Kutu CSI: Karşılaştırma, Ablasyon ve Eşik Taraması (2026-11-09)

Kaynak: `ax210_action` (AX210 → aynı Pi'deki Broadcom/Nexmon, 48/20, ~50 kare/sn). Betikler:
`tools/csi_sensing/bench/` (README'de yeniden üretme adımları).

## Veri ve protokol

| Rol | Kayıt | Kalibrasyon | İçerik |
|---|---|---|---|
| Geliştirme (yöntem burada seçildi) | 171655 | 60 sn | 8 yakın + 12 uzak olay, 15.4 dk boş oda (17:46'da verici durum geçişi) |
| Bağımsız S2 | 190248 | 60 sn | 20.4 dk boş oda (salonda hareket bildirildi) |
| Bağımsız S3 | 194918 | 180 sn | 15.3 dk boş oda |
| Bağımsız S4 | 202201 | S3'ünki | 10 yakın, 5 orta (2–4 m), 12 uzak (kapı/giriş 4–5 m), 5 kapı dışı |

- **Ortak karar katmanı:** 0.5 sn adım, 3/4 histerezis, 4 sakin adımla bırakma.
- **Ortak eşik kuralı:** kalibrasyon istatistiğinden `max(med + 8·MAD, 1.2·P99.5)`.
- **Yakalama:** olay penceresi [−1 sn, +5 sn] içinde **yeni bir alarm başlangıcı**. "Önceden aktif"
  sütunu, olay başladığında dedektörün zaten alarmda olduğu olay sayısıdır; bu olaylar yakalanmış
  sayılmaz. Protokolde her deneme, panelde T² "normal"e dönünce başlatıldı. Bu, diğer
  dedektörlerin önceden aktif olma olasılığını artırır.
- **Boş oda:** alarm sayısı (saat başına) ve alarmda geçen süre oranı.

### Tablo 1. Karşılaştırma (her yöntemin kendi kalibrasyon eşiği, çarpan 1.0)

| Yöntem | Gel. yakın | Gel. uzak | Gel. boş oda: alarm (/sa, süre) | Bğm. yakın | Bğm. orta | Bğm. uzak | **Bğm. oda içi** | önceden aktif | Bğm. kapı dışı ↓ | Bğm. boş oda: alarm (/sa, süre) |
|---|---|---|---|---|---|---|---|---|---|---|
| B1 Genlik varyansı (klasik) | 4/8 | 0/12 | 0 (0.0/sa, %0.0) | 5/10 | 1/5 | 1/12 | **7/27** | 2/27 | 0/5 | 0 (0.0/sa, %0.0) |
| B2 Önceki motor (şekil std + novelty) | 0/8 | 0/12 | 0 (0.0/sa, %0.0) | 0/10 | 0/5 | 0/12 | **0/27** | 0/27 | 0/5 | 0 (0.0/sa, %0.0) |
| B3 Arka plan çıkarma (normalize, beyazlatmasız) | 5/8 | 1/12 | 3 (11.7/sa, %1.9) | 4/10 | 1/5 | 0/12 | **5/27** | 0/27 | 0/5 | 4 (6.7/sa, %0.6) |
| B4 Ortak güç değişimi | 5/8 | 0/12 | 0 (0.0/sa, %0.0) | 6/10 | 4/5 | 9/12 | **19/27** | 17/27 | 4/5 | 0 (0.0/sa, %0.0) |
| Önerilen T² (yalnız kalibrasyon kuralı) | 6/8 | 10/12 | 9 (35.0/sa, %3.3) | 9/10 | 5/5 | 7/12 | **21/27** | 4/27 | 1/5 | 16 (26.9/sa, %3.2) |
| **Önerilen T², dağıtılan (alt eşik 2.0)** | 6/8 | 10/12 | 9 (35.0/sa, %3.3) | 9/10 | 5/5 | 7/12 | **21/27** | 4/27 | 1/5 | 5 (8.4/sa, %0.8) |

### Tablo 2. Ablasyon (alt eşik 2.0, çarpan 1.0)

| Yöntem | Gel. yakın | Gel. uzak | Gel. boş oda: alarm (/sa, süre) | Bğm. yakın | Bğm. orta | Bğm. uzak | **Bğm. oda içi** | önceden aktif | Bğm. kapı dışı ↓ | Bğm. boş oda: alarm (/sa, süre) |
|---|---|---|---|---|---|---|---|---|---|---|
| **Önerilen T², dağıtılan (alt eşik 2.0)** | 6/8 | 10/12 | 9 (35.0/sa, %3.3) | 9/10 | 5/5 | 7/12 | **21/27** | 4/27 | 1/5 | 5 (8.4/sa, %0.8) |
| A1 − LS izdüşümü (yerine CPE+genlik norm.) | 6/8 | 11/12 | 9 (35.0/sa, %3.3) | 9/10 | 5/5 | 8/12 | **22/27** | 6/27 | 1/5 | 7 (11.8/sa, %1.1) |
| A2 − kovaryans beyazlatma (skaler) | 1/8 | 1/12 | 1 (3.9/sa, %0.3) | 0/10 | 0/5 | 0/12 | **0/27** | 0/27 | 0/5 | 2 (3.4/sa, %0.3) |
| A3 − durum ayrımı (tek D, tek W) | 6/8 | 10/12 | 5 (19.4/sa, %8.2) | 9/10 | 5/5 | 7/12 | **21/27** | 4/27 | 1/5 | 5 (8.4/sa, %0.9) |
| A4 − yeniden gönderim süzme | 6/8 | 10/12 | 0 (0.0/sa, %0.0) | 9/10 | 4/5 | 7/12 | **20/27** | 4/27 | 1/5 | 4 (6.7/sa, %0.7) |

### Tablo 3. Eşik taraması: önerilen T² (eşik = çarpan × max(kalibrasyon eşiği, 2.0))

| Çarpan | Gel. yakın | Gel. uzak | Gel. boş oda: alarm (/sa, süre) | Bğm. yakın | Bğm. orta | Bğm. uzak | **Bğm. oda içi** | önceden aktif | Bğm. kapı dışı ↓ | Bğm. boş oda: alarm (/sa, süre) |
|---|---|---|---|---|---|---|---|---|---|---|
| × 0.5 | 3/8 | 9/12 | 41 (159.3/sa, %31.2) | 3/10 | 3/5 | 9/12 | **15/27** | 18/27 | 4/5 | 101 (169.6/sa, %26.6) |
| × 0.75 | 6/8 | 10/12 | 18 (69.9/sa, %7.8) | 7/10 | 5/5 | 9/12 | **21/27** | 8/27 | 1/5 | 18 (30.2/sa, %3.6) |
| × 1.0 | 6/8 | 10/12 | 9 (35.0/sa, %3.3) | 9/10 | 5/5 | 7/12 | **21/27** | 4/27 | 1/5 | 5 (8.4/sa, %0.8) |
| × 1.25 | 7/8 | 7/12 | 4 (15.5/sa, %1.1) | 9/10 | 5/5 | 7/12 | **21/27** | 2/27 | 0/5 | 3 (5.0/sa, %0.4) |
| × 1.5 | 7/8 | 6/12 | 2 (7.8/sa, %0.4) | 10/10 | 5/5 | 6/12 | **21/27** | 1/27 | 0/5 | 1 (1.7/sa, %0.2) |
| × 2.0 | 6/8 | 4/12 | 0 (0.0/sa, %0.0) | 9/10 | 4/5 | 2/12 | **15/27** | 1/27 | 0/5 | 1 (1.7/sa, %0.2) |
| × 3.0 | 6/8 | 2/12 | 0 (0.0/sa, %0.0) | 8/10 | 1/5 | 0/12 | **9/27** | 1/27 | 0/5 | 1 (1.7/sa, %0.2) |
| × 4.0 | 5/8 | 2/12 | 0 (0.0/sa, %0.0) | 7/10 | 0/5 | 0/12 | **7/27** | 0/27 | 0/5 | 1 (1.7/sa, %0.1) |

### Tablo 4. Eşit yanlış alarm bütçesi: bağımsız boş odada ≤ 4 alarm/saat ve alarm süresi ≤ %1 iken en yüksek oda içi yakalama

| Yöntem | Çarpan | **Bğm. oda içi** | yakın | orta | uzak | kapı dışı ↓ | boş oda | Gel. yakın / uzak / boş oda |
|---|---|---|---|---|---|---|---|---|
| B1 Genlik varyansı (klasik) | 0.75 | **15/27** | 6/10 | 3/5 | 6/12 | 0/5 | 1 (1.7/sa, %0.1) | 7/8 / 0/12 / 1 (3.9/sa, %0.3) |
| B3 Arka plan çıkarma (normalize, beyazlatmasız) | 1.5 | **2/27** | 2/10 | 0/5 | 0/12 | 0/5 | 1 (1.7/sa, %0.1) | 3/8 / 1/12 / 1 (3.9/sa, %1.3) |
| B4 Ortak güç değişimi | 1.0 | **19/27** | 6/10 | 4/5 | 9/12 | 4/5 | 0 (0.0/sa, %0.0) | 5/8 / 0/12 / 0 (0.0/sa, %0.0) |
| **Önerilen T², dağıtılan (alt eşik 2.0)** | 1.5 | **21/27** | 10/10 | 5/5 | 6/12 | 0/5 | 1 (1.7/sa, %0.2) | 7/8 / 6/12 / 2 (7.8/sa, %0.4) |
| A1 − LS izdüşümü (yerine CPE+genlik norm.) | 1.5 | **22/27** | 10/10 | 5/5 | 7/12 | 0/5 | 2 (3.4/sa, %0.3) | 8/8 / 6/12 / 3 (11.7/sa, %0.8) |
| A2 − kovaryans beyazlatma (skaler) | 1.0 | **0/27** | 0/10 | 0/5 | 0/12 | 0/5 | 2 (3.4/sa, %0.3) | 1/8 / 1/12 / 1 (3.9/sa, %0.3) |
| A3 − durum ayrımı (tek D, tek W) | 1.25 | **21/27** | 9/10 | 5/5 | 7/12 | 0/5 | 2 (3.4/sa, %0.5) | 7/8 / 7/12 / 4 (15.5/sa, %6.0) |
| A4 − yeniden gönderim süzme | 1.5 | **20/27** | 10/10 | 4/5 | 6/12 | 0/5 | 1 (1.7/sa, %0.2) | 7/8 / 6/12 / 0 (0.0/sa, %0.0) |
| B2 Önceki motor (motionScale 0.5) — bütçe dışı | – | **0/27** | 0/10 | 0/5 | 0/12 | 0/5 | 0 (0.0/sa, %100.0) | 0/8 / 0/12 / 0 (0.0/sa, %100.0) |
| B2 Önceki motor (motionScale 1.0) | – | **0/27** | 0/10 | 0/5 | 0/12 | 0/5 | 0 (0.0/sa, %0.0) | 0/8 / 0/12 / 0 (0.0/sa, %0.0) |
| B2 Önceki motor (motionScale 2.0) | – | **0/27** | 0/10 | 0/5 | 0/12 | 0/5 | 0 (0.0/sa, %0.0) | 0/8 / 0/12 / 0 (0.0/sa, %0.0) |


## Bulgular

1. **Önerilen T² en iyi dengeyi veriyor.** Eşit yanlış alarm bütçesinde (bağımsız boş odada
   ≤ 4/saat ve süre ≤ %1) bağımsız oda içi olayların **21/27**'sini yakalıyor (%95 Wilson
   aralığı %59–89): yakın 10/10, orta 5/5, uzak 6/12, kapı dışı 0/5. Boş odada 1 alarm (1.7/saat).
   Klasik genlik varyansı aynı bütçede 15/27 (%37–72), arka plan çıkarma 2/27, önceki motor 0/27.
2. **Ortak güç (B4) farklı bir şeyi ölçüyor: varlık ya da yakınlık.** 19/27 görünüyor, ama 17
   olayda zaten alarmdaydı. Kapı dışı hareketi de 4/5 yakaladı. Boş odada hiç alarm yok. Yani
   odada (ya da kapının hemen dışında) biri varken sürekli açık kalan bir kanal. Hareket
   detektörü değil, T²'yi tamamlayan bir varlık kanalı adayı.
3. **Önceki motor bu düzende çalışmıyor:** varsayılanda 0/47 olay. Eşik yarıya inince boş odada
   sürekli alarmda (%100).

### Ablasyon: hangi adım ne katkı veriyor?

| Bileşen | Etkisi (bu veride) | Sonuç |
|---|---|---|
| **Kovaryans beyazlatma (Ledoit–Wolf)** | Çıkarılınca bağımsız 21/27 → **0/27** | **Belirleyici bileşen.** Hareketin izi, boş odada az değişen yönlerde; skaler ölçekleme bunu gürültüye gömüyor. |
| LS izdüşümü (vs. CPE + genlik normalizasyonu) | 21/27 → 22/27; boş oda 5 → 7 alarm | Pratikte eşdeğer. İkisi de skaler kazancı kaldırıyor. LS'nin üstünlüğü kuramsal (tam değişmezlik), ölçülebilir fark yok. |
| Durum ayrımı (durum başına D ve W) | Bağımsız kayıtlarda fark yok. Geliştirme kaydında boş odada alarm süresi %3.3 → %8.2 | Yalnız verici durum geçişi olduğunda gerekli (17:46 gibi). Bağımsız kayıtlarda geçiş olmadı. |
| Yeniden gönderim süzme | 21/27 → 20/27; boş oda 5 → 4 | Durum ayrımı varken gereksiz. Yeniden gönderimler zaten azınlık durumlarına düşüyor. İlk ölçümdeki "29 → 2" etkisi, durum ayrımı eklenmeden önceki hattaydı. |

### Eşik taraması (T², dağıtılan)

- **Çarpan 1.0 (bugünkü varsayılan):** 21/27, boş oda 5 alarm (8.4/saat, süre %0.8).
- **Çarpan 1.25–1.5:** yakalama aynı (21/27), boş oda 1–3 alarm (1.7–5.0/saat). Uzak olaylar 7 → 6/12.
- **Çarpan 2.0:** uzak olaylar 2/12'ye düşüyor.

Öneri: oda için çarpan **1.25–1.5** (`motionScale` ile ya da `residualMinThr` ≈ 2.5–3.0).

## Geçerlilik tehditleri

- Tek oda, tek gün, tek kişi. Bağımsız olay sayısı küçük (27 + 5); güven aralıkları geniş.
- Bağımsız boş oda toplamı 35.8 dk. Saatlik yanlış alarm oranı kaba bir tahmin.
- Protokolde denemeler T²'nin "normal" görünmesine göre başlatıldı. Önceden aktif sütunu bu
  etkiyi şeffaf kılmak için verildi.
- Yöntem ve varsayılan parametreler geliştirme kaydında seçildi. Bağımsız kayıtlarda hiçbir
  parametre değiştirilmedi.
