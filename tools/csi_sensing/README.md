# Tek kutu algılama araçları

Motor: `device-files/csi-alpha-webui/webui/csi_sensing.py` (canlı servisle aynı kod).
Yöntem ve sonuçlar: `docs/tek-kutu-algilama-tr.md`.

```bash
# Testler (17 birim/senaryo testi, ~15 sn)
python3 -m unittest tools/csi_sensing/test_sensing_engine.py -v

# Gerçek pcap üzerinde değerlendirme
python3 tools/csi_sensing/replay_eval.py \
  --calib data/csi/captures_phase_20260530/csi-alpha-web-20260529-235132.pcap@0:300 \
  --session empty=data/csi/captures_phase_20260530/csi-alpha-web-20260529-235132.pcap@300: \
  --session passage=data/csi/captures_phase_20260530/csi-alpha-web-20260530-002230.pcap \
  --negative empty --preset room --md rapor.md --json rapor.json

# Panelden indirilen kalibrasyon + işaretli olaylarla (pcap yanında .events.ndjson)
python3 tools/csi_sensing/replay_eval.py --calibration-json sensing_calibration.json \
  --session test=csi-alpha-web-XXXX.pcap --negative empty

# Sentetik tek kutu pcap
python3 tools/csi_sensing/synth_csi.py /tmp/x.pcap --duration 180 --motion 100:115 --remove-path-at 140
```

Segment: `yol.pcap@BAŞ:BİT` (dosya başına göre saniye). `--set anahtar=değer` ile herhangi bir
parametre, `--timeline-dir` ile karar zaman çizelgesi CSV'leri.
