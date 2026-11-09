# Wi-Fi CSI ile Nesne ve Hareket Algılama

Raspberry Pi üzerinde, kamera kullanmadan, Wi-Fi kanal durum bilgisiyle (CSI) hareket ve ortam
değişimi algılayan bir yüksek lisans projesi.

- **Tek kutu (güncel):** Tek bir Raspberry Pi 5 ("Alpha") üzerinde Intel AX210 kartı yayın yapar,
  aynı cihazdaki Broadcom yongası (Nexmon CSI) yansımayı okur. Doğrudan sızıntı, en küçük kareler
  izdüşümüyle iptal edilir. Artık, verici durumuna göre beyazlatılır ve iki pencereli Hotelling T²
  testiyle alarm üretilir. Ayarlar ve izleme web panelinden yapılır.
- **İki cihaz (önceki aşama):** Alpha–Bravo bağlantısı üzerinde CNN/BiLSTM sınıflandırıcı.

**Bağımsız test** (aynı oda, kurallar önceden sabit):

| Ölçüm | Sonuç |
|---|---|
| Boş oda | 15,5 dk'da 1 alarm |
| Oda içi olaylar | 28/32 |
| Cihaza ≤ 2 m | 10/10 |
| Kapı/giriş (4–5 m) | 8/12 |
| Kapı kapalıyken oda dışı hareket (yakalanmaması istenir) | 1/5 |

Aynı yanlış alarm seviyesinde karşılaştırma: önerilen yöntem 21/27, klasik genlik varyansı 15/27,
önceki yöntem 0/27.

## Hızlı başlangıç

```bash
# Pi'ye panel ve algılama motorunu kur (Mac/Linux'tan)
SSH_OPTS="-i ~/.ssh/<anahtar>" device-files/csi-alpha-webui/deploy.sh admin@csi-alpha.local
```

1. Paneli açın: `http://csi-alpha.local:8080`. Cihaz ağ bulamazsa kendi Wi-Fi'ını açar
   (`csi-alpha-kurulum`); o durumda adres `http://10.42.0.1:8080`.
2. **Başlat** → Kurulum → oda boşken **Kalibre et** (180 sn) → **Kurulu**.
3. Canlı sekmesinden izleyin. Deneylerde olayları işaretleyip Analiz sekmesinde ölçün.

Panelin ve bütün ayarların ayrıntılı anlatımı: [docs/projeyi-anlamak-tr.md](docs/projeyi-anlamak-tr.md).

## Depo yapısı

| Yol | İçerik |
|---|---|
| `device-files/csi-alpha-webui/` | Web paneli ve algılama motoru (`csi_sensing.py`, `csi_residual.py`, `csi_web.py`, …), `deploy.sh` |
| `device-files/csi-alpha-robust/` | Pi tarafı betikleri: ağ geçişi, açılışta ağ koruması, sürücü ayarları |
| `device-files/csi-alpha/`, `csi-bravo/` | İki cihazlı önceki kurulumun dosyaları |
| `tools/csi_sensing/` | Birim testleri, sentetik veri, kayıt tekrar oynatma, karşılaştırma/ablasyon betikleri (`bench/`) |
| `tools/csi_ml/` | İki cihazlı CNN/BiLSTM veri hazırlama ve eğitim |
| `data/csi/reports/` | Deney ve karşılaştırma raporları |
| `docs/` | Belgeler (aşağıda) |

## Belgeler

- [Projeyi Detaylarıyla Anlamak](docs/projeyi-anlamak-tr.md): yeni başlayanlar için kavramlar,
  sistem, matematik, panel kılavuzu, oda krokisi.
- [Tek Kutu Algılama](docs/tek-kutu-algilama-tr.md): teknik belge, bütün deneyler ve ölçümler.
- [Algoritma ve Matematik](docs/csi-algoritma-matematik-tr.md): §1–21 iki cihazlı ML hattı,
  §22 tek kutu matematiği.
- [Karşılaştırma, ablasyon, eşik taraması](data/csi/reports/single_box_bench_2026-11-09.md)
- [Nexmon ML hattı](docs/csi-nexmon-ml-pipeline-tr.md),
  [fazlı veri toplama protokolü](docs/csi-fazli-veri-toplama-protokolu-tr.md)

## Testler

```bash
python3 -m unittest tools/csi_sensing/test_sensing_engine.py
```

Karşılaştırma tablolarını yeniden üretmek için: [tools/csi_sensing/bench/README.md](tools/csi_sensing/bench/README.md).

## Sınırlar

Tek oda, tek gün ve tek kişiyle ölçüldü. Menzil cihaza yaklaşık 4–5 m. Cihaz yeniden başladığında
ölçüm henüz kendiliğinden başlamıyor. Kamyonet kasası senaryosu henüz sahada denenmedi.
Ayrıntılar belgelerde.

---
Yusuf Fındık · İstanbul Aydın Üniversitesi, Bilgisayar Mühendisliği
