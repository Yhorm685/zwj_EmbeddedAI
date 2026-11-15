"""Compile the production C core and exercise both model configurations on the host."""
import os
import random
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build(root, header=None):
    flags = [f"-DMOTION_MODEL_HEADER={header.as_posix()}"] if header else []
    subprocess.run(["cmake", "-G", "Ninja", "-S", str(HERE), "-B", str(root),
                    "-DCMAKE_C_COMPILER=gcc", "-DCMAKE_BUILD_TYPE=Release", *flags], check=True)
    subprocess.run(["cmake", "--build", str(root)], check=True)
    return root / ("motion_host.exe" if os.name == "nt" else "motion_host")


def run(exe, requests):
    text = "".join(mode + " " + " ".join(map(str, values)) + "\n" for mode, values in requests)
    return subprocess.run([str(exe)], input=text, text=True, check=True,
                          stdout=subprocess.PIPE).stdout.splitlines()


def requant(acc, shift):
    # Both fixture multipliers are 2^30. The first rounding is toward +inf at
    # half ties, followed by a power-of-two division with half ties away from zero.
    high = (acc * (1 << max(shift, 0)) + 1) // 2
    divisor = 1 << max(-shift, 0)
    magnitude = (abs(high) + divisor // 2) // divisor
    return magnitude if high >= 0 else -magnitude


def score(inputs, weights, biases, shift, minimum):
    return [max(minimum, min(127, requant(b + sum(x*w for x, w in zip(inputs, row)), shift)))
            for row, b in zip(weights, biases)]


def fixture(header, rng):
    weights1 = [[rng.randrange(-4, 5) for _ in range(24)] for _ in range(16)]
    weights2 = [[rng.randrange(-4, 5) for _ in range(16)] for _ in range(3)]
    bias1 = list(range(-8, 8))
    bias2 = [-19, 5, 15]
    declarations = ['#define MOTION_MODEL_ID "synthetic-test-only"']
    for typ, name, values in [
        ("float", "feature_mean", [0]*24), ("float", "feature_std", [1]*24),
        ("int8_t", "w1", sum(weights1, [])), ("int8_t", "w2", sum(weights2, [])),
        ("int32_t", "b1", bias1), ("int32_t", "b2", bias2),
    ]:
        declarations.append(f"static const {typ} motion_{name}[] = {{{','.join(map(str, values))}}};")
    declarations += ["static const float motion_input_scale=1.0f;",
                     "static const int32_t motion_mult1=1073741824, motion_shift1=-2;",
                     "static const int32_t motion_mult2=1073741824, motion_shift2=1;"]
    header.write_text("\n".join(declarations) + "\n", encoding="ascii")
    return lambda q: score(score(q, weights1, bias1, -2, 0), weights2, bias2, 1, -128)


def main():
    rng = random.Random(203)
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        sampler = build(root / "sampler")
        assert run(sampler, [("Q", [0]*24), ("W", [0]*600)]) == ["none", "NO_MODEL", "NO_MODEL"]
        header = root / "model_data.h"
        expected_scores = fixture(header, rng)
        model = build(root / "with_model", header)
        q_cases = [[v]*24 for v in (-128, -1, 0, 1, 127)]
        q_cases.extend([rng.randint(-128, 127) for _ in range(24)] for _ in range(64))
        # Accel ±1g, 1.5g, -2.5g, gyro ±50dps, ±500dps and 0dps.
        # Covers axis order, nearest-even ties, signed mean, RMS, std and saturation.
        rows = [[s*4096, 6144, -10240, s*1640, s*16400, 0] for s in [-1, 1]*50]
        feature_q = [0,1,1,2, 2,0,2,0, -2,0,2,0, 0,50,50,100, 0,127,127,127, 0,0,0,0]
        windows = [([0]*600, [0]*24), (sum(rows, []), feature_q)]
        output = run(model, [("Q", q) for q in q_cases] + [("W", raw) for raw, _ in windows])
        assert output[0] == "synthetic-test-only"
        actual = [list(map(int, line.split())) for line in output[1:]]
        expected = [expected_scores(q) for q in q_cases] + [q+expected_scores(q) for _, q in windows]
        assert len(actual) == len(expected)
        for i, (got, want) in enumerate(zip(actual, expected)):
            assert got == want, (i, got, want)
    print("PASS: no-model Q/W, 69 int8 vectors, 2 raw windows (synthetic, host only)")


if __name__ == "__main__":
    main()
