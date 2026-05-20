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

Collect each class in separate sessions. A useful first set is `empty`, `stable`, `walk`, `sit`, `stand`, `hand_motion`, and `passage`, repeated at 2 m, 3 m, and 5 m.
