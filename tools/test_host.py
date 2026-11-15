import csv
from collections import deque
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import contextlib
import io
import json

import numpy as np

from .capture import COLUMNS, parse_sample, quality, record
from .replay import cycles, execute
from .resources import sections


class TestCapture(unittest.TestCase):
    def test_record_batch_with_device_metadata_and_no_valid_sample(self):
        for status in ("OK", "OFFLINE"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as d:
                args = SimpleNamespace(port="fixture", root=Path(d), label="still", batch="b1",
                                       orientation="Z up", placement="bench", note="", seconds=5,
                                       firmware_version="fixture")
                ticks = [-1]

                def next_line(deadline):
                    ticks[0] += 1
                    i = ticks[0]
                    if i == 0:
                        return "INFO,none,168000000"
                    return f"S,{i},{20*i},0,0,4096,0,0,0,{status}"

                with patch("tools.capture.open_port"), patch("tools.capture.Link") as link, \
                     patch("tools.capture.time.monotonic", side_effect=lambda: .02 * ticks[0]), \
                     contextlib.redirect_stdout(io.StringIO()):
                    link.return_value.read.side_effect = next_line
                    link.return_value.invalid = 0
                    if status == "OK":
                        record(args)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "no valid"):
                            record(args)
                meta = json.loads((Path(d) / "still/b1.json").read_text(encoding="utf-8"))
                self.assertEqual(meta["complete"], status == "OK")
                self.assertEqual(meta["board_info"], "INFO,none,168000000")
                self.assertGreaterEqual(meta["quality"]["rows"], 250)

    def test_unavailable_port_does_not_claim_batch(self):
        with tempfile.TemporaryDirectory() as d, patch("tools.capture.open_port", side_effect=OSError):
            args = SimpleNamespace(port="missing", root=Path(d), label="walk", batch="walk01",
                                   orientation="test", placement="waist", note="", seconds=60,
                                   firmware_version="test-only")
            with self.assertRaises(OSError):
                record(args)
            self.assertEqual(list(Path(d).rglob("*.json")), [])

    def test_gaps_rollover_status_and_gravity(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "test.csv"
            with p.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(COLUMNS)
                for seq, ms, status in ((0xffffffff, 0xfffffff8, "OK"), (0, 12, "OK"),
                                        (3, 72, "I2C"), (4, 119, "OK")):
                    writer.writerow([seq, ms, 4096, 0, 0, 0, 0, 0, status, 1])
            info = quality(p)
            self.assertEqual((info["missing_seq"], info["irregular_periods"], info["valid"]), (2, 1, 3))
            self.assertEqual(info["period_ms_min"], 20)
            self.assertEqual(info["mean_accel_norm_g"], 1)

    def test_reject_out_of_range(self):
        with self.assertRaises(ValueError):
            parse_sample("S,0,1,65535,0,0,0,0,0,OK")

    def test_resource_layout(self):
        text = ("  0 .text 00000010 08000000 08000000 00001000 2**2\n  CONTENTS, ALLOC, LOAD, CODE\n"
                "  1 .data 00000004 20000000 08000010 00002000 2**2\n  CONTENTS, ALLOC, LOAD, DATA\n"
                "  2 .bss  00000008 20000004 20000004 00002004 2**2\n  ALLOC\n")
        rows = sections(text)
        self.assertEqual(sum(r["size"] for r in rows if r["occupies_flash"]), 20)
        self.assertEqual(sum(r["size"] for r in rows if r["ram"]), 12)


class BoardFixture:
    def __init__(self):
        self.queue = deque()
        self.id = self.count = 0

    def send(self, cmd, *args):
        if cmd == "STATUS":
            self.queue.append(["INFO", "fixture", "168000000"])
        elif cmd == "BEGIN":
            self.id, self.count = args[0], 0
            self.queue.append(["READY", str(self.id)])
        elif cmd == "D":
            self.count += 1
            self.queue.append(["ACK", str(self.id), str(self.count)])
        elif cmd == "END":
            self.queue.append(["Q", str(self.id), *(["0"] * 24)])
            scores = ["1" if self.id == i else "0" for i in range(3)]
            self.queue.append(["R", str(self.id), str(self.id), *scores, "168", "504"])
        elif cmd == "I":
            self.queue.append(["J", str(self.id), *["1" if self.id == i else "0" for i in range(3)], "168"])

    def expect(self, tag, request_id=None):
        fields = self.queue.popleft()
        assert fields[0] == tag
        assert request_id is None or str(request_id) == fields[1]
        return fields


class TestReplay(unittest.TestCase):
    def test_disabled_dwt_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "DWT"):
            cycles("0")

    def test_full_raw_and_integer_paths(self):
        archive = dict(raw=np.zeros((3,100,6), "int16"), input_q=np.zeros((3,24), "int8"),
                       scores_q=np.eye(3, dtype="int8"), labels=np.arange(3),
                       logits=np.eye(3, dtype="float32")*.25, batch=np.array(list("abc")),
                       start_seq=np.array([1, 2, 3]), output_scale=np.array(.25), model_id=np.array("fixture"))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "trial"
            m = execute(BoardFixture(), archive, out, "test-only")
            self.assertEqual(m["network"]["mean_us"], 1.0)
            self.assertEqual(m["full_window"]["max_us"], 3.0)
            self.assertEqual(m["board"]["recall"], [1, 1, 1])
            self.assertEqual(m["integer_score_mismatch_windows"], 0)
            self.assertEqual(m["logits_max_abs"], 0)
            self.assertTrue((out / "windows.csv").is_file())


if __name__ == "__main__":
    unittest.main()
