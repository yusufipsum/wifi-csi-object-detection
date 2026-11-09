# Tek kutu CSI: karşılaştırma, ablasyon, eşik taraması

Kayıtlar (Pi: `/home/admin/csi/captures/`), çalışma dizinine kopyalanmalı:
`csi-alpha-web-20261109-{171655,190248,194918,202201}.{pcap,events.ndjson}`.

```bash
cd <kayitlarin-oldugu-dizin>
cp <repo>/tools/csi_sensing/bench/*.py <repo>/tools/csi_sensing/bench/cfg_live.json .
python3 run_bench.py     # B1, B3, B4, T2 ve ablasyonlar -> stat_*.npz  (~1 dk)
python3 run_b2.py        # B2: onceki motor, gercek motor replay'i      (~5 dk)
python3 eval2.py         # tablolar -> bench_tables.md
```

- Karar katmanı her yöntem için aynıdır: 0.5 sn adım, 3/4 histerezis, 4 sakin adımla bırakma.
- Eşik kalibrasyon penceresinden çıkar: `max(med + 8·1.4826·MAD, 1.2·P99.5)`. "Dağıtılan" T² için
  ayrıca 2.0 alt eşik uygulanır.
- Yakalama ölçütü: olay penceresi [başlangıç − 1 sn, bitiş + 5 sn] içinde **yeni** bir alarm başlaması.
  Olay başladığında zaten alarmda olan dedektör, o olayı yakalamış sayılmaz.
- Boş oda için alarm sayısı ve alarmda geçen süre birlikte raporlanır.
- `dev` = 171655 (yöntem bu kayıtta seçildi). `S2`, `S3`, `S4` bağımsız (S4, S3'ün 180 sn kalibrasyonunu kullanır).
