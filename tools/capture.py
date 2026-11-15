"""Record one motion batch or examine the sampling quality of a saved batch."""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import statistics
import time

from .serial_link import Link, open_port


COLUMNS = ["seq", "ms", "ax", "ay", "az", "gx", "gy", "gz", "status", "host_ns"]
LABELS = ("still", "walk", "run")
MASK = 2**32 - 1


def parse_sample(line):
    fields = line.split(",")
    if len(fields) != 10 or fields[0] != "S":
        raise ValueError("invalid sample format")
    seq, ms, *axes = map(int, fields[1:9])
    status = fields[-1]
    if not (0 <= seq <= MASK and 0 <= ms <= MASK and
            all(-32768 <= x <= 32767 for x in axes) and
            re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", status)):
        raise ValueError("invalid sample field")
    return [seq, ms, *axes, status]


def quality(path):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != COLUMNS:
            raise ValueError("unexpected capture header")
        rows = list(reader)
    gaps = jumps = bad_dt = 0
    periods = []
    magnitudes = []
    sums = [0, 0, 0]
    previous = None
    valid = 0
    status_counts = {}
    for row in rows:
        seq, ms, *tail = parse_sample("S," + ",".join(row[k] for k in COLUMNS[:-1]))
        status = tail[-1]
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "OK":
            valid += 1
            magnitudes.append(math.sqrt(sum(a*a for a in tail[:3])) / 4096)
            for index in range(3):
                sums[index] += tail[index]
        if previous is not None:
            step = (seq - previous[0]) & MASK
            dt = (ms - previous[1]) & MASK
            if 1 < step < 2**31:
                gaps += step - 1
            if step == 0 or step >= 2**31:
                jumps += 1
            if step == 1:
                periods.append(dt)
                bad_dt += int(not 10 <= dt <= 30)
        previous = seq, ms
    span = ((int(rows[-1]["ms"]) - int(rows[0]["ms"])) & MASK) if rows else 0
    return dict(rows=len(rows), valid=valid, status_counts=status_counts,
                device_span_ms=span, missing_seq=gaps, duplicate_or_reset=jumps,
                irregular_periods=bad_dt,
                period_ms_min=min(periods, default=None),
                period_ms_median=statistics.median(periods) if periods else None,
                period_ms_max=max(periods, default=None),
                mean_accel_xyz_g=[s / (4096 * valid) for s in sums] if valid else None,
                mean_accel_norm_g=statistics.fmean(magnitudes) if magnitudes else None)


def record(args):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.batch):
        raise ValueError("batch must contain 1..64 letters, digits, '-' or '_'")
    if not math.isfinite(args.seconds) or args.seconds <= 4:
        raise ValueError("duration must exceed 4 seconds")
    folder = args.root / args.label
    folder.mkdir(parents=True, exist_ok=True)
    csv_path = folder / (args.batch + ".csv")
    meta_path = csv_path.with_suffix(".json")
    # Exclusive metadata reservation prevents concurrent writers and accidental reuse.
    meta = dict(label=args.label, batch=args.batch, complete=False,
                sample_rate_hz=50, accel_range_g=8, gyro_range_dps=1000,
                placement=args.placement, orientation=args.orientation, note=args.note,
                firmware_version=args.firmware_version, port=args.port, uart_baud=115200,
                duration_requested_s=args.seconds, started_unix_ns=time.time_ns(),
                valid_start_ms=2000)
    if csv_path.exists():
        raise FileExistsError(csv_path)
    with meta_path.open("x", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    malformed = 0
    with open_port(args.port) as port, csv_path.open("x", encoding="utf-8", newline="") as output:
        port.reset_input_buffer()
        link = Link(port)
        writer = csv.writer(output)
        writer.writerow(COLUMNS)
        wait_until = time.monotonic() + 8
        finish_at = None
        last_sample = None
        while finish_at is None or time.monotonic() < finish_at:
            deadline = wait_until if finish_at is None else min(finish_at, last_sample + 5)
            line = link.read(deadline)
            if line is None:
                if finish_at is not None and time.monotonic() >= finish_at:
                    break
                raise TimeoutError("no samples on UART; capture retained with complete=false")
            if not line.startswith("S,"):
                continue
            try:
                sample = parse_sample(line)
            except ValueError:
                malformed += 1
                continue
            last_sample = time.monotonic()
            if finish_at is None:
                finish_at = last_sample + args.seconds
            writer.writerow([*sample, time.time_ns()])
        output.flush()
    report = quality(csv_path)
    meta.update(quality=report, malformed_lines=malformed + link.invalid,
                valid_end_ms=max(0, report["device_span_ms"] - 2000 + 1),
                complete=True)
    temp = meta_path.with_suffix(".json.partial")
    with temp.open("x", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n")
    temp.replace(meta_path)
    print(csv_path)
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    if report["valid"] == 0:
        raise RuntimeError("no valid sensor samples; check wiring before recording more")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("record")
    cap.add_argument("--port", required=True)
    cap.add_argument("--root", type=Path, default=Path("data/raw"))
    cap.add_argument("--label", choices=LABELS, required=True)
    cap.add_argument("--batch", required=True)
    cap.add_argument("--seconds", type=float, default=60)
    cap.add_argument("--orientation", required=True, help="actual sensor mounting orientation")
    cap.add_argument("--placement", choices=("waist", "bench"), default="waist")
    cap.add_argument("--firmware-version", default="unrecorded")
    cap.add_argument("--note", default="")
    check = sub.add_parser("inspect")
    check.add_argument("csv", type=Path)
    watch = sub.add_parser("monitor")
    watch.add_argument("--port", required=True)
    args = parser.parse_args()
    if args.command == "record":
        record(args)
    elif args.command == "inspect":
        print(json.dumps(quality(args.csv), ensure_ascii=False, indent=2))
    else:
        with open_port(args.port) as port:
            link = Link(port)
            link.send("STATUS")
            last_print = 0
            last_status = None
            while True:
                line = link.read(time.monotonic() + 2)
                if not line:
                    continue
                if line.startswith("S,"):
                    status = line.rsplit(",", 1)[-1]
                    if status == last_status and time.monotonic() - last_print < 1:
                        continue
                    last_status, last_print = status, time.monotonic()
                elif line.startswith("P,"):
                    fields = line.split(",")
                    if len(fields) == 8 and fields[2] in ("0", "1", "2"):
                        line += "  " + LABELS[int(fields[2])]
                print(line, flush=True)


if __name__ == "__main__":
    main()
