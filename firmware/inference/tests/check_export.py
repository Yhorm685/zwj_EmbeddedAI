"""Compare an exported header with the Python integer results on identical saved windows."""
import argparse
from pathlib import Path
import tempfile

import numpy as np

from check import build, run


def check_export(header, windows):
    with np.load(windows, allow_pickle=False) as data:
        raw, inputs, scores = data["raw"], data["input_q"], data["scores_q"]
        model_id = data["model_id"].item()
    n = len(raw)
    if not n or raw.shape != (n, 100, 6) or inputs.shape != (n, 24) or scores.shape != (n, 3):
        raise ValueError("Unexpected replay dataset dimensions")
    with tempfile.TemporaryDirectory() as temp:
        exe = build(Path(temp) / "host", header.resolve())
        requests = [("W", window.ravel()) for window in raw] + [("Q", q) for q in inputs]
        output = run(exe, requests)
    if output[0] != model_id or len(output) != 1 + 2*n:
        raise ValueError("Model ID or response count mismatch")
    native = np.array([list(map(int, s.split())) for s in output[1:1+n]])
    integer_only = np.array([list(map(int, s.split())) for s in output[1+n:]])
    np.testing.assert_array_equal(native[:, :24], inputs)
    np.testing.assert_array_equal(native[:, 24:], scores)
    np.testing.assert_array_equal(integer_only, scores)
    print(f"PASS: {n} raw windows and {n} integer inputs, CMSIS-NN host vs Python, model {model_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--header", required=True, type=Path)
    parser.add_argument("--windows", required=True, type=Path)
    args = parser.parse_args()
    check_export(args.header, args.windows)
