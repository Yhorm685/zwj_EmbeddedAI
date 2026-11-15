"""Reproduce firmware parameters from a frozen checkpoint and its training batches."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import torch

from .data import features, load_partition, make_split, read_json, sha256, text_sha256, write_json
from .quant import calibrate, write_header


def load_frozen(data, model_dir):
    split_path = model_dir / "split.json"
    ckpt = torch.load(model_dir / "model.pt", map_location="cpu", weights_only=True)
    split = read_json(split_path)
    if text_sha256(split_path) != ckpt["split_sha256"] or make_split(data, split["seed"]) != split:
        raise ValueError("recordings, annotations or split differ from the frozen version")
    return ckpt, split


def export(data, model_dir, header):
    ckpt, split = load_frozen(data, model_dir)
    tensors = ckpt["state"]
    train_features = features(load_partition(data, split, "train").raw)
    model = calibrate(ckpt["mean"].numpy(), ckpt["std"].numpy(),
                      tensors["0.weight"].numpy(), tensors["0.bias"].numpy(),
                      tensors["2.weight"].numpy(), tensors["2.bias"].numpy(),
                      train_features, sha256(model_dir / "model.pt"))
    header.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=header.parent) as temp:
        candidate = Path(temp) / "model_data.h"
        write_header(candidate, model)
        candidate.replace(header)
    with TemporaryDirectory(dir=model_dir) as temp:
        candidate = Path(temp) / "model.json"
        write_json(candidate, model)
        candidate.replace(model_dir / "model.json")
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--header", type=Path, help="default: MODEL/model_data.h")
    args = parser.parse_args()
    header = args.header or args.model / "model_data.h"
    model = export(args.data, args.model, header)
    print(f"{model['model_id']}  {header}")


if __name__ == "__main__":
    main()
