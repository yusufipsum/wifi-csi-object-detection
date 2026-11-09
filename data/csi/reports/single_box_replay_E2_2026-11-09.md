# Tek Kutu Algılama — Replay Değerlendirmesi (2026-11-09 14:39)

Motor: `single_box_v1` · senaryo: `room` · kalibrasyon: `data/csi/captures_phase_20260530/csi-alpha-web-20260529-225105.pcap@150:600` (443.5 sn, 6123 kare, 1 PHY modu)

Eşikler (kalibrasyondan, ölçek çarpanı uygulanmadan): `{"motion": 0.005974, "novelty": 0.15, "power": 0.125312, "static": 2.366739, "micro": null}`

| Oturum | Etiket | Süre (sn) | Kabul | Hareket aktif % | Statik aktif % | Alarm (adet) | Alarm/saat | Hareket değeri p50 / p99 |
|---|---|---:|---:|---:|---:|---|---|---|
| `csi-alpha-web-20260529-235132.pcap`  | empty | 976.7 | 0.77 | 0.0 | 0.0 | 0 | 0 | 0.003305 / 0.004812 |
| `csi-alpha-web-20260529-225105.pcap`  | empty_noisy | 987.1 | 0.78 | 7.9 | 0.0 | motion: 5, signal_loss: 1 | motion: 18.24, signal_loss: 3.65 | 0.00363 / 0.089261 |
| `csi-alpha-web-20260529-230809.pcap`  | empty_noisy | 1097.6 | 0.77 | 13.3 | 0.0 | motion: 5, signal_loss: 1 | motion: 16.4, signal_loss: 3.28 | 0.003599 / 0.102003 |
| `csi-alpha-web-20260530-002230.pcap`  | passage | 942.0 | 0.78 | 55.5 | 0.0 | motion: 41 | motion: 156.69 | 0.00795 / 0.03995 |
| `csi-alpha-web-20260530-003846.pcap`  | passage | 926.6 | 0.77 | 55.4 | 0.0 | motion: 38 | motion: 147.63 | 0.006859 / 0.045938 |
| `csi-alpha-web-20260530-010105.pcap`  | hand_motion | 702.4 | 0.78 | 96.2 | 0.0 | motion: 10 | motion: 51.26 | 0.009076 / 0.021124 |
| `csi-alpha-web-20260530-012008.pcap`  | hand_motion | 822.9 | 0.76 | 99.2 | 0.0 | motion: 2 | motion: 8.75 | 0.014412 / 0.03776 |

## Özet

- **Negatif (normal) süre**: 51.0 dk
- **Negatif oturumlarda hareket alarmı**: 10 (11.76/saat)
- **Negatif oturumlarda tüm alarmlar**: 12 (14.11/saat)
- **Negatif pencere yanlış-aktif oranı (hareket)**: %7.07
- **Pozitif oturum pencere aktif oranı (hareket, ort.)**: %76.5
- **Hareket alarmı üreten pozitif oturum**: 4/4
- **Pencere-seviyesi ROC-AUC (hareket değeri, oturum etiketleri)**: 0.8145

## Eşik çarpanı taraması (pencere seviyesi, hareket skoru)

| motionScale | Negatif oturum pencere oranı | Pozitif oturum pencere oranı |
|---:|---:|---:|
| 0.6 | 46.3 | 81.2 |
| 0.8 | 7.0 | 71.3 |
| 1.0 | 6.3 | 66.0 |
| 1.2 | 6.3 | 61.0 |
| 1.5 | 6.3 | 54.1 |
| 2.0 | 6.0 | 45.0 |
| 3.0 | 5.8 | 24.7 |

Notlar:

- 'Aktif %' pencere (karar) seviyesidir; pozitif etiketli oturumlar boşta geçen aralar da içerdiği için %100 olmaları beklenmez (oturum-seviyesi etiket gürültüsü).
- AUC, oturum etiketleriyle pencere seviyesinde hesaplanır; aynı nedenle alt sınır niteliğindedir.
