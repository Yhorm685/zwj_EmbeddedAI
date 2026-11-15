"""Run the frozen test windows on a serial-connected F407 and retain raw replies."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from .serial_link import Link, open_port


def octets(fields, length):
    values = [int(n) for n in fields]
    if len(values) != length or any(n < -128 or n > 127 for n in values):
        raise ValueError("unexpected quantized output")
    return values


def cycles(value):
    value = int(value)
    if not 0 < value <= 0xffffffff:
        raise ValueError("invalid cycle count; verify the F407 DWT counter is running")
    return value


def board_info(link):
    link.send("STATUS")
    fields = link.expect("INFO")
    if len(fields) < 3 or int(fields[2]) <= 0:
        raise ValueError(f"invalid board info {fields}")
    return fields


def replay_one(link, ident, raw, input_q):
    link.send("BEGIN", ident)
    link.expect("READY", ident)
    for index, sample in enumerate(raw, start=1):
        link.send("D", *sample)
        if link.expect("ACK", ident)[2:] != [str(index)]:
            raise ValueError(f"board did not acknowledge row {index}")
    link.send("END", ident)
    board_input = octets(link.expect("Q", ident)[2:], 24)
    result = link.expect("R", ident)
    if len(result) != 8:
        raise ValueError(f"invalid replay result {result}")
    scores = octets(result[3:6], 3)
    predicted = int(result[2])
    if predicted not in range(3) or scores[predicted] != max(scores):
        raise ValueError("class and scores disagree")
    net, total = map(cycles, result[6:8])
    if total < net:
        raise ValueError("window time below network time")
    link.send("I", ident, *input_q)
    exact = link.expect("J", ident)
    if len(exact) != 6:
        raise ValueError("invalid integer-only result")
    return board_input, scores, predicted, net, total, octets(exact[2:5], 3), cycles(exact[5])


def class_metrics(labels, predicted):
    confusion = np.zeros((3, 3), dtype=int)
    np.add.at(confusion, (labels, predicted), 1)
    return dict(accuracy=float(np.mean(labels == predicted)),
                recall=[float(row[i]/row.sum()) if row.sum() else None
                        for i, row in enumerate(confusion)],
                confusion=confusion.tolist())


def timing(values, hz):
    us = np.asarray(values, dtype=np.float64) * (1e6 / hz)
    return dict(n=len(us), mean_us=float(us.mean()), p95_us=float(np.percentile(us, 95)),
                max_us=float(us.max()))


def execute(link, archive, dest, firmware_version):
    raw, ref_input, ref_scores = (archive[k] for k in ("raw", "input_q", "scores_q"))
    logits, labels = archive["logits"], archive["labels"]
    model = str(archive["model_id"].item())
    scale = float(archive["output_scale"].item())
    n = len(labels)
    if not (n and raw.shape == (n, 100, 6) and raw.dtype == np.int16 and
            ref_input.shape == (n, 24) and ref_input.dtype == np.int8 and
            ref_scores.shape == (n, 3) and ref_scores.dtype == np.int8 and
            logits.shape == (n, 3) and np.isfinite(logits).all() and
            labels.shape == (n,) and np.issubdtype(labels.dtype, np.integer) and
            set(labels.tolist()) == {0, 1, 2} and np.isfinite(scale) and scale > 0 and
            archive["batch"].shape == (n,) and archive["start_seq"].shape == (n,)):
        raise ValueError("invalid or incomplete frozen test archive")
    batches, starts = archive["batch"], archive["start_seq"]
    info = board_info(link)
    if info[1] != model:
        raise ValueError(f"board model {info[1]!r}, archive model {model!r}")
    hz = int(info[2])
    score_rows, predicted, network, window = [], [], [], []
    feature_mismatch = direct_mismatch = raw_mismatch = 0
    dest.mkdir(parents=True, exist_ok=False)
    with (dest / "windows.csv").open("x", encoding="utf-8", newline="") as stream:
        out = csv.writer(stream)
        out.writerow(["id", "batch", "start_seq", "label", "class", "q0", "q1", "q2",
                      "network_cycles", "window_cycles", "board_input_q", "integer_only_scores",
                      "integer_only_cycles", "input_matches", "raw_scores_match", "integer_scores_match"])
        for i in range(n):
            inp, scores, cls, net, total, exact, int_cycles = replay_one(link, i, raw[i], ref_input[i])
            same_input = np.array_equal(inp, ref_input[i])
            same_raw = np.array_equal(scores, ref_scores[i])
            same_direct = np.array_equal(exact, ref_scores[i])
            feature_mismatch += int(not same_input)
            raw_mismatch += int(not same_raw)
            direct_mismatch += int(not same_direct)
            out.writerow([i, batches[i], starts[i], labels[i], cls, *scores, net, total,
                          " ".join(map(str, inp)), " ".join(map(str, exact)), int_cycles,
                          same_input, same_raw, same_direct])
            stream.flush()
            score_rows.append(scores)
            predicted.append(cls)
            network.append(net)
            window.append(total)
    info_after = board_info(link)
    if info_after[1:3] != info[1:3]:
        raise RuntimeError("board identity/clock changed during replay")
    board_scores = np.asarray(score_rows, dtype=np.float64) * scale
    delta = np.abs(board_scores - logits)
    float_cls = logits.argmax(axis=1)
    board_cls = np.asarray(predicted)
    metrics = dict(firmware_version=firmware_version, model_id=model, clock_hz=hz,
                   board_info_start=info, board_info_end=info_after,
                   class_order=["still", "walk", "run"], windows=n,
                   board=class_metrics(labels, board_cls),
                   pc_float=class_metrics(labels, float_cls),
                   pc_int=class_metrics(labels, ref_scores.argmax(axis=1)),
                   logits_mae=float(delta.mean()), logits_max_abs=float(delta.max()),
                   class_disagreement=float(np.mean(board_cls != float_cls)),
                   board_minus_float_accuracy=float(np.mean(board_cls == labels)-np.mean(float_cls == labels)),
                   input_mismatch_windows=feature_mismatch, raw_score_mismatch_windows=raw_mismatch,
                   integer_score_mismatch_windows=direct_mismatch,
                   network=timing(network, hz), full_window=timing(window, hz))
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--windows", type=Path, default=Path("evidence/pc/test_windows.npz"))
    parser.add_argument("--out", type=Path, default=Path("evidence/board/replay-01"))
    parser.add_argument("--firmware-version", required=True, help="flashed Git commit and build configuration")
    args = parser.parse_args()
    with np.load(args.windows, allow_pickle=False) as archive, open_port(args.port) as port:
        port.reset_input_buffer()
        metrics = execute(Link(port), archive, args.out, args.firmware_version)
    with args.windows.open("rb") as infile:
        metrics["test_archive_sha256"] = hashlib.file_digest(infile, "sha256").hexdigest()
    report = json.dumps(metrics, indent=2, ensure_ascii=False, allow_nan=False)
    (args.out / "summary.json").write_text(report + "\n", encoding="utf-8")
    print(report)
    if any(metrics[k] for k in ("input_mismatch_windows", "raw_score_mismatch_windows",
                                 "integer_score_mismatch_windows")):
        raise SystemExit("board/PC comparison differs; inspect windows.csv")


if __name__ == "__main__":
    main()
