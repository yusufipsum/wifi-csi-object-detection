# Tek Kutu Algılama — Replay Değerlendirmesi (2026-11-09 14:38)

Motor: `single_box_v1` · senaryo: `room` · kalibrasyon: `data/csi/captures_phase_20260530/csi-alpha-web-20260529-235132.pcap@0:300` (293.9 sn, 2908 kare, 1 PHY modu)

Eşikler (kalibrasyondan, ölçek çarpanı uygulanmadan): `{"motion": 0.00783, "novelty": 0.174714, "power": 0.036517, "static": 2.158617, "micro": null}`

| Oturum | Etiket | Süre (sn) | Kabul | Hareket aktif % | Statik aktif % | Alarm (adet) | Alarm/saat | Hareket değeri p50 / p99 |
|---|---|---:|---:|---:|---:|---|---|---|
| `csi-alpha-web-20260529-235132.pcap` @300: | empty | 676.7 | 0.78 | 0.0 | 0.0 | 0 | 0 | 0.002913 / 0.004775 |
| `csi-alpha-web-20260529-225105.pcap`  | empty_noisy | 987.1 | 0.78 | 7.9 | 0.0 | motion: 5, signal_loss: 1 | motion: 18.24, signal_loss: 3.65 | 0.00363 / 0.089261 |
| `csi-alpha-web-20260529-230809.pcap`  | empty_noisy | 1097.6 | 0.77 | 13.2 | 0.0 | motion: 5, signal_loss: 1 | motion: 16.4, signal_loss: 3.28 | 0.003599 / 0.102003 |
| `csi-alpha-web-20260530-002230.pcap`  | passage | 942.0 | 0.78 | 53.3 | 0.0 | motion: 41 | motion: 156.69 | 0.00795 / 0.03995 |
| `csi-alpha-web-20260530-003846.pcap`  | passage | 926.6 | 0.77 | 52.6 | 0.0 | motion: 38 | motion: 147.63 | 0.006859 / 0.045938 |
| `csi-alpha-web-20260530-010105.pcap`  | hand_motion | 702.4 | 0.78 | 78.7 | 67.2 | motion: 37, static_change: 1 | motion: 189.65, static_change: 5.13 | 0.009076 / 0.021124 |
| `csi-alpha-web-20260530-012008.pcap`  | hand_motion | 822.9 | 0.76 | 97.6 | 0.0 | motion: 7 | motion: 30.62 | 0.014412 / 0.03776 |

## Özet

- **Negatif (normal) süre**: 46.0 dk
- **Negatif oturumlarda hareket alarmı**: 10 (13.04/saat)
- **Negatif oturumlarda tüm alarmlar**: 12 (15.64/saat)
- **Negatif pencere yanlış-aktif oranı (hareket)**: %7.04
- **Pozitif oturum pencere aktif oranı (hareket, ort.)**: %70.6
- **Hareket alarmı üreten pozitif oturum**: 4/4
- **Pencere-seviyesi ROC-AUC (hareket değeri, oturum etiketleri)**: 0.8129

## Eşik çarpanı taraması (pencere seviyesi, hareket skoru)

| motionScale | Negatif oturum pencere oranı | Pozitif oturum pencere oranı |
|---:|---:|---:|
| 0.6 | 7.9 | 71.8 |
| 0.8 | 7.0 | 65.0 |
| 1.0 | 7.0 | 58.2 |
| 1.2 | 6.9 | 52.9 |
| 1.5 | 6.7 | 45.5 |
| 2.0 | 6.6 | 32.0 |
| 3.0 | 6.3 | 12.9 |

Notlar:

- 'Aktif %' pencere (karar) seviyesidir; pozitif etiketli oturumlar boşta geçen aralar da içerdiği için %100 olmaları beklenmez (oturum-seviyesi etiket gürültüsü).
- AUC, oturum etiketleriyle pencere seviyesinde hesaplanır; aynı nedenle alt sınır niteliğindedir.
