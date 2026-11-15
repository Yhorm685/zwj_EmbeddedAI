"""Read independent labeled six-axis captures and build gap-safe windows."""

import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random

import numpy as np

LABELS = ("still", "walk", "run")
AXES = ("ax", "ay", "az", "gx", "gy", "gz")
WINDOW = 100
STRIDE = 25
PERIOD = 20
UINT32_MASK = 2**32 - 1
DIVISORS = np.array([4096] * 3 + [32.8] * 3, dtype=np.float32)


def write_json(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.write_bytes(text.encode("utf-8"))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_sha256(path):
    # Git can check out text with different line endings on Windows and Linux.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def make_split(root, seed):
    """Assign whole batches before looking at windows; seal both data and annotations."""
    rng = random.Random(seed)
    split = {"version": 1, "seed": seed, "labels": list(LABELS),
             "train": [], "validation": [], "test": [], "files": {}}
    seen = set()
    for label in LABELS:
        paths = sorted((root / label).glob("*.csv"))
        if len(paths) != 10:
            raise ValueError(f"{root / label}: expected 10 separate CSV batches, found {len(paths)}")
        keys = []
        for path in paths:
            key = path.relative_to(root).with_suffix("").as_posix()
            meta = path.with_suffix(".json")
            digest = text_sha256(path)
            if digest in seen:
                raise ValueError(f"{path}: duplicate batch contents")
            seen.add(digest)
            split["files"][key] = {"csv": digest, "json": text_sha256(meta)}
            keys.append(key)
        rng.shuffle(keys)
        split["train"].extend(keys[:6])
        split["validation"].extend(keys[6:8])
        split["test"].extend(keys[8:])
    return split


@dataclass
class Windows:
    raw: np.ndarray
    labels: np.ndarray
    batch: np.ndarray
    start_seq: np.ndarray
    quality: list


def _integer(value, minimum, maximum):
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"out of range: {result}")
    return result


def _intervals(meta, duration):
    begin = meta.get("valid_start_ms")
    end = meta.get("valid_end_ms")
    begin = 2000 if begin is None else begin
    end = duration - 2000 if end is None else end
    if not 0 <= begin < end:
        raise ValueError(f"empty/invalid valid range [{begin}, {end})")
    ranges = meta.get("valid_segments_ms", [[begin, end]])
    result = []
    for low, high in ranges:
        if not begin <= low < high <= end or (result and low < result[-1][1]):
            raise ValueError("valid_segments_ms must contain ordered, disjoint ranges within the valid range")
        result.append((low, high))
    return result


def read_batch(path):
    """Yield no window spanning a gap, invalid sample, trim interval or batch end."""
    meta = read_json(path.with_suffix(".json"))
    label = path.parent.name
    if meta["label"] != label or str(meta["batch"]) != path.stem:
        raise ValueError(f"{path}: label or batch does not match path")
    if meta.get("complete") is False:
        raise ValueError(f"{path}: incomplete capture")
    for field, expected in (("sample_rate_hz", 50), ("accel_range_g", 8), ("gyro_range_dps", 1000)):
        if field in meta and meta[field] != expected:
            raise ValueError(f"{path}: {field} must be {expected}")

    records = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"seq", "ms", *AXES, "status", "host_ns"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path}: required CSV columns are {sorted(required)}")
        for line, row in enumerate(reader, 2):
            try:
                seq = _integer(row["seq"], 0, UINT32_MASK)
                ms = _integer(row["ms"], 0, UINT32_MASK)
                axes = tuple(_integer(row[a], -32768, 32767) for a in AXES)
                records.append((seq, ms, axes, row["status"]))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"{path}, line {line}: malformed sample: {exc}") from exc
    if not records:
        raise ValueError(f"{path}: empty capture")
    elapsed = [0]
    for previous, current in zip(records, records[1:]):
        delta = (current[1] - previous[1]) & UINT32_MASK
        if delta >= 2**31:
            raise ValueError(f"{path}: device clock reversed; collect board resets in separate batches")
        elapsed.append(elapsed[-1] + delta)
    ranges = _intervals(meta, elapsed[-1] + PERIOD)
    chunks = []
    segment = []
    last_seq = last_ms = last_range = None
    gaps = bad = 0
    for (seq, ms, axes, status), at in zip(records, elapsed):
        interval = next((i for i, (lo, hi) in enumerate(ranges) if lo <= at < hi), None)
        broken = last_seq is not None and (
            ((seq - last_seq) & UINT32_MASK) != 1 or not 10 <= ((ms - last_ms) & UINT32_MASK) <= 30)
        if broken:
            gaps += 1
        if status != "OK":
            bad += 1
        if segment and (broken or interval != last_range or status != "OK"):
            chunks.append(segment)
            segment = []
        if status == "OK" and interval is not None:
            segment.append((seq, axes, at))
        last_seq, last_ms, last_range = seq, ms, interval
    if segment:
        chunks.append(segment)

    samples, starts = [], []
    for chunk in chunks:
        data = np.asarray([r[1] for r in chunk], dtype=np.int16)
        for i in range(0, len(chunk) - WINDOW + 1, STRIDE):
            samples.append(data[i:i + WINDOW])
            starts.append(chunk[i][0])
    quality = {"batch": f"{label}/{path.stem}", "samples": len(records), "invalid_status": bad,
               "sequence_or_time_breaks": gaps, "recorded_ms": elapsed[-1] + PERIOD,
               "usable_samples": sum(map(len, chunks)),
               "usable_ms": sum(c[-1][2] - c[0][2] + PERIOD for c in chunks),
               "segments": len(chunks), "windows": len(starts)}
    return samples, starts, quality


def load_partition(root, split, name):
    raw, labels, batches, starts, quality = [], [], [], [], []
    for key in split[name]:
        # Keys originate from make_split (or from an exactly verified saved split).
        path = Path(root) / (key + ".csv")
        samples, offsets, report = read_batch(path)
        if not offsets:
            raise ValueError(f"{path}: no complete 100-sample stable window")
        raw.extend(samples)
        labels.extend([LABELS.index(path.parent.name)] * len(samples))
        starts.extend(offsets)
        batches.extend([key] * len(samples))
        quality.append(report)
    return Windows(np.stack(raw), np.asarray(labels, dtype=np.int64), np.asarray(batches),
                   np.asarray(starts, dtype=np.uint32), quality)


def features(raw):
    """Output axis-major [mean, population std, RMS, peak-to-peak] in float32."""
    data = np.asarray(raw)
    if data.shape[-2:] != (WINDOW, 6):
        raise ValueError(f"expected (..., {WINDOW}, 6), got {data.shape}")
    values = data.astype(np.float32) / DIVISORS
    total = np.zeros_like(values[..., 0, :])
    squares = np.zeros_like(total)
    # Match the board's sequential float32 additions, including their rounding.
    for i in range(WINDOW):
        row = values[..., i, :]
        total += row
        squares += row * row
    mean = total / np.float32(WINDOW)
    mean_square = squares / np.float32(WINDOW)
    std = np.sqrt(np.maximum(mean_square - mean * mean, np.float32(0)))
    block = np.stack((mean, std, np.sqrt(mean_square), np.ptp(values, axis=-2)), axis=-1)
    return block.reshape((*data.shape[:-2], 24))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Audit batches, disjoint partitions and stable-window counts")
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=407)
    args = parser.parse_args()
    split = make_split(args.data, args.seed)
    audit = {"split": split, "quality": {}}
    for part in ("train", "validation", "test"):
        windows = load_partition(args.data, split, part)
        audit["quality"][part] = windows.quality
        print(f"{part}: {len(windows.quality)} batches, {len(windows.raw)} stable windows")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.out, audit)


if __name__ == "__main__":
    main()
