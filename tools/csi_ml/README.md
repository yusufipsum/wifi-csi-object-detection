# CSI ML Pipeline

Full Turkish system documentation lives here:

```text
docs/csi-nexmon-ml-pipeline-tr.md
```

Alpha writes labelled sessions under `/home/admin/csi/datasets/*.ndjson` when a label is selected in the WebUI.

You can download both CSI pcap files and ML dataset files from the WebUI:

```text
http://192.168.1.99:8080
```

Copy data to this computer:

```bash
mkdir -p data/csi/raw
scp -i ~/.ssh/id_ed25519_csi_codex admin@192.168.1.99:/home/admin/csi/datasets/*.ndjson data/csi/raw/
```

Audit downloaded datasets before training:

```bash
python tools/csi_ml/audit_dataset.py data/csi/raw --min-samples-per-label 400
```

For the new phase-aware pipeline, use a separate folder and require `phaseResiduals`:

```bash
mkdir -p data/csi/raw_phase
scp -i ~/.ssh/id_ed25519_csi_codex admin@192.168.1.99:/home/admin/csi/datasets/*.ndjson data/csi/raw_phase/

python tools/csi_ml/audit_dataset.py data/csi/raw_phase \
  --require-phase \
  --min-samples-per-label 400 \
  --json data/csi/reports/phase_dataset_audit.json
```

Multiscale physical transmitter target:

```bash
TX_PROFILE=multiscale_physical_v1 RATE_PPS=15 PAYLOAD_SIZE=1200 /home/admin/csi/start_tx.sh 192.168.1.7 5501
```

The expected Alpha WebUI `packetRate` is roughly 15-20 pkt/s. Use `RATE_PPS=20`
if it stays low, or `RATE_PPS=12` if management/WebUI becomes unstable.

Prepare fixed-length windows:

```bash
python3 -m venv .venv-csi
source .venv-csi/bin/activate
pip install -r tools/csi_ml/requirements.txt
python tools/csi_ml/prepare_dataset.py data/csi/raw -o data/csi/csi_dataset.npz --window 64 --stride 16
```

Train a compact CNN/LSTM:

```bash
python tools/csi_ml/train_cnn_lstm.py data/csi/csi_dataset.npz -o data/csi/csi_cnn_lstm.pt --epochs 30
```

The CNN extracts per-frame subcarrier patterns. The LSTM then learns how those patterns evolve over time across the CSI window.

Phase-aware temporal model:

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

Phase-aware multi-scale model:

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

The short branch (`16` samples) targets hand-scale and quicker CSI changes. The
long branch (`48` samples) gives the classifier enough context for passage,
empty-room stability, and false-alarm suppression. Both branches are aligned to the
same end time, so each prediction answers one current moment at two temporal
scales.

For a stricter estimate, use session-holdout test splitting. This keeps the
latest session of each label fully outside training:

```bash
python tools/csi_ml/prepare_multiscale_session_splits.py data/csi/raw_phase_20260530 \
  -o data/csi/csi_multiscale_phase_20260530_w16_w48_holdout.npz \
  --windows 16,48 \
  --stride 4 \
  --train-ratio 0.78 \
  --purge 32 \
  --features amp,phase,amp_delta,phase_delta
```

Collect each class in separate sessions. The 2026-05-30 phase-aware live model was trained only with `empty`, `hand_motion`, and `passage`. Add `sit` and `stand` only after collecting separate phase-ready sessions for those labels.
