"""Deterministic artificial inputs for numerical and workflow regression tests."""

import csv
from pathlib import Path
import tempfile
import unittest

import numpy as np

from .data import AXES, LABELS, features, load_partition, make_split, read_batch, read_json, write_json
from .evaluate import evaluate
from .export import export
from .quant import quantize, requantize
from .train import train


def fake_batch(root, label, number, count=301, fault=None):
    """Generate temporary diagnostic signals; these are not motion recordings."""
    path = root / label / f"{number:02}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(10 * LABELS.index(label) + number)
    amplitude = [4, 100, 1000][LABELS.index(label)]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["seq", "ms", *AXES, "status", "host_ns"])
        for i in range(count):
            seq, ms, status = i, 20 * i, "OK"
            if fault is not None:
                seq, ms, status = fault(i)
            axes = rng.integers(-amplitude, amplitude + 1, size=6)
            axes[2] += 4096
            writer.writerow([seq, ms, *axes, status, i * 20_000_000])
    write_json(path.with_suffix(".json"), {"label": label, "batch": path.stem, "complete": True,
                                         "valid_start_ms": 0, "valid_end_ms": count * 20})
    return path


class Sampling(unittest.TestCase):
    def test_units_and_axis_major_layout(self):
        raw = np.zeros((100, 6), np.int16)
        raw[:, 0] = 4096
        raw[::2, 1] = 4096
        raw[1::2, 1] = -4096
        raw[:, 3] = 328
        expected = np.array([1, 0, 1, 0, 0, 1, 1, 2, 0, 0, 0, 0,
                             10, 0, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32)
        np.testing.assert_allclose(features(raw), expected, atol=1e-6)
        np.testing.assert_array_equal(features(np.stack([raw, raw]))[1], features(raw))

    def test_missing_invalid_and_transition_samples(self):
        with tempfile.TemporaryDirectory() as d:
            # One 100-point run and two 100-point runs separated by status and gaps.
            def fault(i):
                return (i + int(i >= 201), i * 20 + 23 * int(i >= 201), "ERR" if i == 100 else "OK")
            path = fake_batch(Path(d), "still", 0, count=301, fault=fault)
            meta = read_json(path.with_suffix(".json"))
            meta["valid_end_ms"] += 23
            write_json(path.with_suffix(".json"), meta)
            _, starts, q = read_batch(path)
            self.assertEqual(starts, [0, 101, 202])
            self.assertEqual((q["invalid_status"], q["sequence_or_time_breaks"]), (1, 1))
            meta["valid_segments_ms"] = [[0, 2000], [4100, 6043]]
            write_json(path.with_suffix(".json"), meta)
            self.assertEqual(read_batch(path)[1], [0])

    def test_sequence_and_clock_wrap(self):
        with tempfile.TemporaryDirectory() as d:
            mask = 2**32 - 1
            path = fake_batch(Path(d), "still", 0, count=100,
                              fault=lambda i: ((mask - 9 + i) & mask, (mask - 99 + i * 20) & mask, "OK"))
            _, starts, q = read_batch(path)
            self.assertEqual(starts, [mask - 9])
            self.assertEqual(q["sequence_or_time_breaks"], 0)

    def test_device_reset_in_middle_of_batch(self):
        with tempfile.TemporaryDirectory() as d:
            path = fake_batch(Path(d), "still", 0, count=220,
                              fault=lambda i: (i, 5000 + 20 * i if i < 120 else 20 * (i - 120), "OK"))
            with self.assertRaisesRegex(ValueError, "clock reversed"):
                read_batch(path)


class IntegerMath(unittest.TestCase):
    def test_cmsis_double_rounding(self):
        values = np.arange(-8, 9, dtype=np.int32)
        # Doubling high multiply by 0.5 rounds ties up; power-of-two divide rounds ties away.
        expected = [-2, -2, -2, -1, -1, -1, -1, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2]
        np.testing.assert_array_equal(requantize(values, 1 << 30, -1), expected)
        np.testing.assert_array_equal(requantize(values, 1 << 30, 1), values)
        with self.assertRaises(OverflowError):
            requantize(np.array([2**30]), 1 << 30, 1)

    def test_input_nearest_even_and_saturation(self):
        np.testing.assert_array_equal(quantize([-200, -1.5, -0.5, 0.5, 1.5, 200], 1),
                                      [-128, -2, 0, 0, 2, 127])


class Workflow(unittest.TestCase):
    def test_train_convert_evaluate_artificial_data(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            data, model, evidence = root / "raw", root / "model", root / "eval"
            for label in LABELS:
                for i in range(10):
                    fake_batch(data, label, i)
            split = make_split(data, 407)
            self.assertEqual([len(split[k]) for k in ("train", "validation", "test")], [18, 6, 6])
            self.assertEqual(len(set(split["train"] + split["validation"] + split["test"])), 30)
            train(data, model, epochs=2)
            # A text checkout with LF instead of CRLF preserves the sealed data version.
            csv_path = next(data.glob("still/*.csv"))
            csv_path.write_bytes(csv_path.read_bytes().replace(b"\r\n", b"\n"))
            before = (model / "model_data.h").read_bytes()
            exported = export(data, model, root / "converted.h")
            self.assertEqual((root / "converted.h").read_bytes(), before)
            report = evaluate(data, model, evidence)
            test = load_partition(data, split, "test")
            self.assertEqual(report["windows"], 54)
            with np.load(evidence / "test_windows.npz", allow_pickle=False) as outputs:
                self.assertEqual(outputs["model_id"].item(), exported["model_id"])
                np.testing.assert_array_equal(outputs["raw"], test.raw)
                self.assertEqual(outputs["scores_q"].shape, (54, 3))
            path = next(data.glob("still/*.json"))
            meta = read_json(path)
            meta["valid_start_ms"] = 20
            write_json(path, meta)
            with self.assertRaisesRegex(ValueError, "differ from the frozen"):
                export(data, model, root / "changed.h")


if __name__ == "__main__":
    unittest.main()
