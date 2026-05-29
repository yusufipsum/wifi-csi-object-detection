#!/usr/bin/env python3
import argparse
import json
import random
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


class FrameEncoder(nn.Module):
    def __init__(self, input_channels=1, conv_channels=32, dropout=0.35):
        super().__init__()
        self.output_dim = conv_channels * 2 * 16
        self.net = nn.Sequential(
            nn.Conv1d(input_channels, conv_channels, kernel_size=7, padding=3),
            nn.BatchNorm1d(conv_channels),
            nn.GELU(),
            nn.MaxPool1d(2),
            nn.Dropout(dropout * 0.35),
            nn.Conv1d(conv_channels, conv_channels * 2, kernel_size=5, padding=2),
            nn.BatchNorm1d(conv_channels * 2),
            nn.GELU(),
            nn.AdaptiveAvgPool1d(16),
            nn.Flatten(),
        )

    def forward(self, x):
        return self.net(x)


class CsiMultiScaleCnnLstm(nn.Module):
    def __init__(
        self,
        windows,
        classes,
        input_channels=1,
        conv_channels=32,
        hidden=64,
        dropout=0.35,
        bidirectional=True,
    ):
        super().__init__()
        self.windows = [int(window) for window in windows]
        self.encoder = FrameEncoder(input_channels, conv_channels, dropout)
        self.lstms = nn.ModuleDict({
            str(window): nn.LSTM(
                self.encoder.output_dim,
                hidden,
                batch_first=True,
                bidirectional=bidirectional,
                dropout=0.0,
            )
            for window in self.windows
        })
        branch_dim = hidden * (2 if bidirectional else 1)
        self.classifier = nn.Sequential(
            nn.LayerNorm(branch_dim * len(self.windows)),
            nn.Dropout(dropout),
            nn.Linear(branch_dim * len(self.windows), classes),
        )

    def _encode_sequence(self, x):
        if x.dim() == 3:
            batch, steps, tones = x.shape
            x = x.reshape(batch * steps, 1, tones)
        elif x.dim() == 4:
            batch, steps, channels, tones = x.shape
            x = x.reshape(batch * steps, channels, tones)
        else:
            raise ValueError(f"expected 3D or 4D input, got shape {tuple(x.shape)}")
        encoded = self.encoder(x)
        return encoded.reshape(batch, steps, -1)

    def forward(self, inputs):
        branches = []
        for window, x in zip(self.windows, inputs):
            encoded = self._encode_sequence(x)
            out, _ = self.lstms[str(window)](encoded)
            branches.append(out[:, -1, :])
        return self.classifier(torch.cat(branches, dim=1))


def seed_everything(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.backends.mps.is_available():
        torch.mps.manual_seed(seed)


def augment_tensor(x, noise_std=0.025, time_mask=0.10, tone_mask=0.06):
    if noise_std > 0:
        x = x + torch.randn_like(x) * noise_std
    if x.dim() == 3:
        batch, steps, tones = x.shape
        has_channels = False
    else:
        batch, steps, _, tones = x.shape
        has_channels = True
    if time_mask > 0 and steps > 4:
        mask_len = max(1, int(steps * time_mask))
        for idx in range(batch):
            start = random.randint(0, max(0, steps - mask_len))
            if has_channels:
                x[idx, start:start + mask_len, :, :] = 0
            else:
                x[idx, start:start + mask_len, :] = 0
    if tone_mask > 0 and tones > 8:
        mask_len = max(1, int(tones * tone_mask))
        for idx in range(batch):
            start = random.randint(0, max(0, tones - mask_len))
            if has_channels:
                x[idx, :, :, start:start + mask_len] = 0
            else:
                x[idx, :, start:start + mask_len] = 0
    return x


def augment_inputs(inputs):
    return [augment_tensor(item) for item in inputs]


def confusion_matrix(y_true, y_pred, classes):
    matrix = np.zeros((classes, classes), dtype=np.int64)
    for true, pred in zip(y_true, y_pred):
        matrix[int(true), int(pred)] += 1
    return matrix


def metrics_from_confusion(matrix):
    total = int(matrix.sum())
    correct = int(np.trace(matrix))
    per_class = []
    f1_values = []
    for idx in range(matrix.shape[0]):
        tp = int(matrix[idx, idx])
        fp = int(matrix[:, idx].sum() - tp)
        fn = int(matrix[idx, :].sum() - tp)
        support = int(matrix[idx, :].sum())
        precision = tp / max(1, tp + fp)
        recall = tp / max(1, tp + fn)
        f1 = 2 * precision * recall / max(1e-12, precision + recall)
        f1_values.append(f1)
        per_class.append({
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
        })
    return {
        "accuracy": correct / max(1, total),
        "macroF1": float(np.mean(f1_values)) if f1_values else 0.0,
        "total": total,
        "correct": correct,
        "perClass": per_class,
    }


def make_loader(xs_by_window, y, windows, batch_size, shuffle):
    tensors = [
        torch.tensor(xs_by_window[window], dtype=torch.float32)
        for window in windows
    ]
    tensors.append(torch.tensor(y, dtype=torch.long))
    return DataLoader(TensorDataset(*tensors), batch_size=batch_size, shuffle=shuffle)


def evaluate(model, loader, device, classes, windows):
    model.eval()
    losses = []
    preds = []
    labels = []
    loss_fn = nn.CrossEntropyLoss(reduction="sum")
    with torch.no_grad():
        for batch in loader:
            inputs = [item.to(device) for item in batch[:-1]]
            batch_y = batch[-1].to(device)
            logits = model(inputs)
            losses.append(float(loss_fn(logits, batch_y).cpu()))
            preds.extend(logits.argmax(dim=1).cpu().numpy().tolist())
            labels.extend(batch_y.cpu().numpy().tolist())
    matrix = confusion_matrix(labels, preds, classes)
    metrics = metrics_from_confusion(matrix)
    metrics["loss"] = sum(losses) / max(1, len(labels))
    return metrics, matrix


def load_split(data, split, windows):
    return {
        window: data[f"x_w{window}_{split}"]
        for window in windows
    }, data[f"y_{split}"]


def infer_input_shape(xs_by_window, windows):
    first = xs_by_window[windows[0]]
    if first.ndim == 3:
        input_channels = 1
        tones = int(first.shape[-1])
    elif first.ndim == 4:
        input_channels = int(first.shape[2])
        tones = int(first.shape[-1])
    else:
        raise SystemExit(f"Unsupported input shape for window {windows[0]}: {first.shape}")
    return input_channels, tones


def main():
    parser = argparse.ArgumentParser(description="Train a multi-scale CNN/LSTM model for CSI activity labels.")
    parser.add_argument("dataset", help="NPZ from prepare_multiscale_splits.py")
    parser.add_argument("-o", "--output", default="data/csi/models/csi_cnn_lstm_multiscale.pt")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--patience", type=int, default=14)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=7e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-bidirectional", action="store_true")
    args = parser.parse_args()

    seed_everything(args.seed)
    data = np.load(args.dataset, allow_pickle=True)
    labels = [str(item) for item in data["labels"].tolist()]
    windows = [int(item) for item in data["windows"].tolist()]
    if "featureNames" in data.files:
        feature_names = [str(item) for item in data["featureNames"].tolist()]
    else:
        feature_names = ["amp"]

    x_train, y_train = load_split(data, "train", windows)
    x_val, y_val = load_split(data, "val", windows)
    x_test, y_test = load_split(data, "test", windows)
    input_channels, tones = infer_input_shape(x_train, windows)
    classes = len(labels)

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    train_loader = make_loader(x_train, y_train, windows, args.batch_size, True)
    val_loader = make_loader(x_val, y_val, windows, args.batch_size, False)
    test_loader = make_loader(x_test, y_test, windows, args.batch_size, False)

    model = CsiMultiScaleCnnLstm(
        windows=windows,
        classes=classes,
        input_channels=input_channels,
        bidirectional=not args.no_bidirectional,
    ).to(device)

    class_counts = np.bincount(y_train, minlength=classes).astype(np.float32)
    class_weights = class_counts.sum() / np.maximum(1.0, class_counts * classes)
    class_weights = np.sqrt(class_weights)
    class_weights = class_weights / class_weights.mean()
    loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(class_weights, dtype=torch.float32, device=device))
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max", factor=0.5, patience=5)

    best = {"epoch": 0, "macroF1": -1.0, "accuracy": 0.0, "state": None}
    history = []
    stale = 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        total = 0
        for batch in train_loader:
            inputs = [item.to(device) for item in batch[:-1]]
            batch_y = batch[-1].to(device)
            inputs = augment_inputs(inputs)
            optimizer.zero_grad(set_to_none=True)
            logits = model(inputs)
            loss = loss_fn(logits, batch_y)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 2.0)
            optimizer.step()
            total_loss += float(loss.detach().cpu()) * batch_y.numel()
            total += batch_y.numel()

        val_metrics, _ = evaluate(model, val_loader, device, classes, windows)
        scheduler.step(val_metrics["macroF1"])
        row = {
            "epoch": epoch,
            "trainLoss": total_loss / max(1, total),
            "valAccuracy": val_metrics["accuracy"],
            "valMacroF1": val_metrics["macroF1"],
            "lr": optimizer.param_groups[0]["lr"],
        }
        history.append(row)
        print(
            f"epoch={epoch:03d} train_loss={row['trainLoss']:.4f} "
            f"val_acc={row['valAccuracy']:.3f} val_macro_f1={row['valMacroF1']:.3f} lr={row['lr']:.6f}"
        )
        if val_metrics["macroF1"] > best["macroF1"] + 1e-5:
            best = {
                "epoch": epoch,
                "macroF1": val_metrics["macroF1"],
                "accuracy": val_metrics["accuracy"],
                "state": {key: value.detach().cpu() for key, value in model.state_dict().items()},
            }
            stale = 0
        else:
            stale += 1
            if stale >= args.patience:
                print(f"early_stop epoch={epoch} best_epoch={best['epoch']}")
                break

    model.load_state_dict(best["state"] or model.state_dict())
    val_metrics, val_matrix = evaluate(model, val_loader, device, classes, windows)
    test_metrics, test_matrix = evaluate(model, test_loader, device, classes, windows)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "model": "csi_cnn_lstm_multiscale_v1",
        "model_state": model.state_dict(),
        "labels": labels,
        "tones": tones,
        "windows": windows,
        "window": max(windows),
        "inputChannels": input_channels,
        "featureNames": feature_names,
        "bidirectional": not args.no_bidirectional,
        "classWeights": class_weights.tolist(),
        "bestEpoch": best["epoch"],
        "bestValMacroF1": best["macroF1"],
        "bestValAccuracy": best["accuracy"],
        "history": history,
        "splitReport": json.loads(str(data["report"])),
    }
    torch.save(checkpoint, output)

    report = {
        "labels": labels,
        "dataset": str(args.dataset),
        "output": str(output),
        "device": str(device),
        "epochsRequested": args.epochs,
        "epochsRun": len(history),
        "bestEpoch": best["epoch"],
        "windows": windows,
        "inputShapes": {
            str(window): list(x_train[window].shape[1:])
            for window in windows
        },
        "inputChannels": input_channels,
        "featureNames": feature_names,
        "classWeights": {label: float(class_weights[idx]) for idx, label in enumerate(labels)},
        "validation": val_metrics,
        "validationConfusion": val_matrix.tolist(),
        "test": test_metrics,
        "testConfusion": test_matrix.tolist(),
        "history": history,
        "splitReport": checkpoint["splitReport"],
    }
    report_path = output.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output}")
    print(f"saved {report_path}")
    print(f"test_acc={test_metrics['accuracy']:.3f} test_macro_f1={test_metrics['macroF1']:.3f}")
    print("test_confusion")
    print(test_matrix)


if __name__ == "__main__":
    main()
