"""Evaluate the sealed PC test set and provide identical raw windows for board replay."""

import argparse
import csv
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import torch

from .data import LABELS, features, load_partition, read_json, sha256, write_json
from .export import load_frozen
from .quant import model_digest, predict_q, quantized_features
from .train import network


def classification(labels, predicted):
    matrix = np.zeros((3, 3), dtype=np.int64)
    np.add.at(matrix, (labels, predicted), 1)
    totals = matrix.sum(axis=1)
    recall = [float(v / n) if n else None for v, n in zip(matrix.diagonal(), totals)]
    return {"accuracy": float(np.mean(predicted == labels)), "recall": dict(zip(LABELS, recall)),
            "confusion_rows_true_columns_predicted": matrix.tolist()}


def evaluate(data, model_dir, out):
    if out.exists():
        raise FileExistsError(f"{out} exists; select a new evaluation directory")
    ckpt, split = load_frozen(data, model_dir)
    model = read_json(model_dir / "model.json")
    if model["checkpoint_sha256"] != sha256(model_dir / "model.pt") or model["model_id"] != model_digest(model):
        raise ValueError("quantized parameters and checkpoint do not match")
    test = load_partition(data, split, "test")
    x = features(test.raw)
    net = network()
    net.load_state_dict(ckpt["state"])
    net.eval()
    with torch.no_grad():
        logits = net((torch.from_numpy(x) - ckpt["mean"]) / ckpt["std"]).numpy()
    inputs = quantized_features(x, model)
    scores = predict_q(inputs, model)
    scale = np.float32(model["output_scale"])
    errors = np.abs(logits - scores.astype(np.float32) * scale)
    pf, pi = logits.argmax(axis=1), scores.argmax(axis=1)
    report = {"model_id": model["model_id"], "class_order": list(LABELS), "windows": len(x),
              "float": classification(test.labels, pf), "int8_reference": classification(test.labels, pi),
              "mean_absolute_logit_error": float(errors.mean()), "max_absolute_logit_error": float(errors.max()),
              "prediction_disagreement": float(np.mean(pf != pi)),
              "accuracy_delta_int8_minus_float": float(np.mean(pi == test.labels) - np.mean(pf == test.labels)),
              "test_batches": test.quality}
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as temp:
        staged = Path(temp) / "evaluation"
        staged.mkdir()
        np.savez_compressed(staged / "test_windows.npz", raw=test.raw, input_q=inputs, scores_q=scores,
                            logits=logits, labels=test.labels, batch=test.batch, start_seq=test.start_seq,
                            model_id=np.asarray(model["model_id"]), output_scale=scale)
        with (staged / "predictions.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("batch", "start_seq", "truth", "float_prediction", "int8_prediction",
                             "float_still", "float_walk", "float_run", "q_still", "q_walk", "q_run"))
            for i in range(len(x)):
                writer.writerow((test.batch[i], test.start_seq[i], LABELS[test.labels[i]], LABELS[pf[i]],
                                 LABELS[pi[i]], *logits[i], *scores[i]))
        write_json(staged / "metrics.json", report)
        staged.rename(out)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = evaluate(args.data, args.model, args.out)
    print(f"Evaluated {report['windows']} sealed PC test windows: {args.out}")


if __name__ == "__main__":
    main()
