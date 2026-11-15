"""Fit and freeze the 451-parameter CPU neural network."""

import argparse
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import torch
from torch import nn

from .data import features, load_partition, make_split, text_sha256, write_json


def network():
    return nn.Sequential(nn.Linear(24, 16), nn.ReLU(), nn.Linear(16, 3))


def train(data, out, seed=407, epochs=100):
    if epochs < 1:
        raise ValueError("epochs must be positive")
    if out.exists():
        raise FileExistsError(f"{out} exists; choose a new model version directory")
    torch.set_num_threads(1)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)

    split = make_split(data, seed)
    train_set = load_partition(data, split, "train")
    val_set = load_partition(data, split, "validation")
    xf = features(train_set.raw)
    mean = xf.mean(axis=0, dtype=np.float64).astype(np.float32)
    std = xf.std(axis=0, dtype=np.float64).astype(np.float32)
    std[std < 1e-6] = 1.0
    x = torch.from_numpy((xf - mean) / std)
    x_val = torch.from_numpy((features(val_set.raw) - mean) / std)
    y = torch.from_numpy(train_set.labels)
    y_val = torch.from_numpy(val_set.labels)

    net = network()
    optimizer = torch.optim.Adam(net.parameters(), lr=0.003)
    criterion = nn.CrossEntropyLoss()
    best_loss = float("inf")
    history, best_state, best_epoch = [], None, None
    for epoch in range(1, epochs + 1):
        net.train()
        train_loss = 0.0
        for ids in torch.randperm(len(y)).split(64):
            optimizer.zero_grad()
            loss = criterion(net(x[ids]), y[ids])
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * len(ids)
        net.eval()
        with torch.no_grad():
            val_logits = net(x_val)
            val_loss = criterion(val_logits, y_val).item()
            accuracy = (val_logits.argmax(1) == y_val).float().mean().item()
        history.append({"epoch": epoch, "train_ce": train_loss / len(y),
                        "validation_ce": val_loss, "validation_accuracy": accuracy})
        if val_loss < best_loss:
            best_loss, best_state, best_epoch = val_loss, deepcopy(net.state_dict()), epoch
    if best_state is None:
        raise ValueError("no finite validation loss; check recorded values")

    from .export import export

    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as temp:
        # Publish the complete version in one rename after training and export succeed.
        staged = Path(temp) / "version"
        staged.mkdir()
        write_json(staged / "split.json", split)
        torch.save({"version": 1, "state": best_state, "mean": torch.from_numpy(mean),
                    "std": torch.from_numpy(std), "split_sha256": text_sha256(staged / "split.json")},
                   staged / "model.pt")
        write_json(staged / "training.json", {
            "seed": seed, "epochs": epochs, "selected_epoch": best_epoch,
            "selection": "minimum validation cross entropy (first tied epoch)",
            "optimizer": "Adam", "learning_rate": 0.003, "batch_size": 64,
            "minimum_std": 1e-6, "replacement_std": 1.0, "parameters": 451,
            "numpy": np.__version__, "torch": str(torch.__version__), "history": history,
            "train_batches": train_set.quality, "validation_batches": val_set.quality})
        export(data, staged, staged / "model_data.h")
        staged.rename(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=407)
    parser.add_argument("--epochs", type=int, default=100)
    args = parser.parse_args()
    train(args.data, args.out, args.seed, args.epochs)
    print(f"Frozen checkpoint and int8 parameters: {args.out}")


if __name__ == "__main__":
    main()
