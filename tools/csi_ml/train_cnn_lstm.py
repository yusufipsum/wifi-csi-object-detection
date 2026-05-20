#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset, random_split


class CsiCnnLstm(nn.Module):
    def __init__(self, tones, classes, conv_channels=32, hidden=64):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv1d(1, conv_channels, kernel_size=7, padding=3),
            nn.BatchNorm1d(conv_channels),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(conv_channels, conv_channels * 2, kernel_size=5, padding=2),
            nn.BatchNorm1d(conv_channels * 2),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(16),
            nn.Flatten(),
        )
        self.lstm = nn.LSTM(conv_channels * 2 * 16, hidden, batch_first=True)
        self.classifier = nn.Sequential(
            nn.Dropout(0.25),
            nn.Linear(hidden, classes),
        )

    def forward(self, x):
        batch, steps, tones = x.shape
        x = x.reshape(batch * steps, 1, tones)
        x = self.encoder(x)
        x = x.reshape(batch, steps, -1)
        out, _ = self.lstm(x)
        return self.classifier(out[:, -1, :])


def accuracy(model, loader, device):
    model.eval()
    total = 0
    correct = 0
    with torch.no_grad():
        for x, y in loader:
            x = x.to(device)
            y = y.to(device)
            pred = model(x).argmax(dim=1)
            total += y.numel()
            correct += (pred == y).sum().item()
    return correct / max(1, total)


def main():
    parser = argparse.ArgumentParser(description="Train a compact CNN/LSTM on CSI windows.")
    parser.add_argument("dataset", help="NPZ file from prepare_dataset.py")
    parser.add_argument("-o", "--output", default="csi_cnn_lstm.pt")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    args = parser.parse_args()

    data = np.load(args.dataset, allow_pickle=True)
    x = torch.tensor(data["x"], dtype=torch.float32)
    y = torch.tensor(data["y"], dtype=torch.long)
    labels = [str(item) for item in data["labels"].tolist()]

    dataset = TensorDataset(x, y)
    val_len = max(1, int(len(dataset) * 0.2))
    train_len = len(dataset) - val_len
    train_set, val_set = random_split(dataset, [train_len, val_len], generator=torch.Generator().manual_seed(42))
    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_set, batch_size=args.batch_size)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = CsiCnnLstm(tones=x.shape[-1], classes=len(labels)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    loss_fn = nn.CrossEntropyLoss()

    best_acc = 0.0
    best_state = None
    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        for batch_x, batch_y in train_loader:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * batch_y.numel()
        val_acc = accuracy(model, val_loader, device)
        train_loss = total_loss / max(1, train_len)
        print(f"epoch={epoch:03d} loss={train_loss:.4f} val_acc={val_acc:.3f}")
        if val_acc >= best_acc:
            best_acc = val_acc
            best_state = {key: value.detach().cpu() for key, value in model.state_dict().items()}

    output = Path(args.output)
    torch.save(
        {
            "model_state": best_state or model.state_dict(),
            "labels": labels,
            "tones": int(x.shape[-1]),
            "window": int(x.shape[1]),
            "model": "csi_cnn_lstm_v1",
        },
        output,
    )
    output.with_suffix(".labels.json").write_text(json.dumps(labels, indent=2), encoding="utf-8")
    print(f"saved {output} labels={labels} best_val_acc={best_acc:.3f}")


if __name__ == "__main__":
    main()
