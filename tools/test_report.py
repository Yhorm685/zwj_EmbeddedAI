"""Offline report checks. All board records and the miniature ELF are synthetic."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from .report import digest, image_layout, main, render


MODEL = "1" * 64


def fake_summary():
    def info(free, libc):
        return ["INFO", MODEL, "168000000", f"heap_min_free={free}", "msp_min_free=64",
                "comm_stack_min_free=20", "sample_stack_min_free=16",
                "idle_stack_min_free=12", "timer_stack_min_free=8", f"newlib_heap_peak={libc}"]

    cls = {"confusion": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
           "accuracy": 1.0, "recall": [1.0] * 3}
    time = {"n": 3, "mean_us": 1.5, "p95_us": 1.9, "max_us": 2.0}
    return {"firmware_version": "artificial-test-only", "model_id": MODEL, "clock_hz": 168000000,
            "board_info_start": info(230, 8), "board_info_end": info(224, 16),
            "class_order": ["still", "walk", "run"], "windows": 3, "pc_float": cls,
            "pc_int": cls, "board": cls, "network": time, "full_window": time,
            "logits_mae": 0.125, "logits_max_abs": 0.25, "class_disagreement": 0.0,
            "board_minus_float_accuracy": 0.0, "input_mismatch_windows": 0,
            "raw_score_mismatch_windows": 0, "integer_score_mismatch_windows": 0}


class Report(unittest.TestCase):
    rows = [{"size": 1000, "ram": "", "occupies_flash": True, "section": ".text"},
            {"size": 768, "ram": "SRAM", "occupies_flash": False, "section": ".bss"},
            {"size": 192, "ram": "SRAM", "occupies_flash": False, "section": "._user_heap_stack"}]
    symbols = {"ucHeap": 512, "_sstack": 0x2001ff80, "_estack": 0x20020000, "_end": 0x20000300}

    def test_fixed_heap_msp_and_libc_do_not_double_count_task_stacks(self):
        text = render(fake_summary(), self.rows, self.symbols, "e", "s")
        # 256 fixed + (512−224) reserved in the RTOS heap + (128−64) MSP + 16 newlib.
        self.assertIn("分项高水位合计为 624 B", text)
        self.assertIn("100.00%", text)
        self.assertIn("完整窗口处理 | 3 | 1.500", text)

    def test_missing_or_inconsistent_runtime_evidence(self):
        summary = fake_summary()
        summary["board_info_end"].pop()
        self.assertIn("整机峰值待补测", render(summary, self.rows, self.symbols, "e", "s"))
        summary["board_info_end"][3] = "heap_min_free=500"
        with self.assertRaisesRegex(ValueError, "watermark moved backwards"):
            render(summary, self.rows, self.symbols, "e", "s")
        summary = fake_summary()
        summary["elf_sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "digest differs"):
            render(summary, self.rows, self.symbols, "e", "s")

    @unittest.skipUnless(all(shutil.which("arm-none-eabi-" + name) for name in ("as", "ld", "objdump", "nm")),
                         "Arm GNU toolchain required to build the synthetic ELF")
    def test_real_elf_parser_and_markdown_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            asm, linker, obj, elf = (folder / n for n in ("fixture.s", "fixture.ld", "fixture.o", "fixture.elf"))
            asm.write_text(f'.section .rodata,"a"\n.asciz "{MODEL}"\n.section .bss,"aw",%nobits\n'
                           '.balign 4\n.type ucHeap,%object\nucHeap: .space 512\n.size ucHeap, . - ucHeap\n'
                           '.space 256\n', encoding="ascii")
            linker.write_text("SECTIONS { .rodata 0x08000000 : { *(.rodata) } "
                              ".bss 0x20000000 (NOLOAD) : { *(.bss) } _end = .; "
                              "._user_heap_stack (NOLOAD) : { . += 192; } "
                              "_sstack = 0x2001ff80; _estack = 0x20020000; }", encoding="ascii")
            subprocess.run(["arm-none-eabi-as", str(asm), "-o", str(obj)], check=True)
            subprocess.run(["arm-none-eabi-ld", "-T", str(linker), str(obj), "-o", str(elf)], check=True)
            with self.assertRaisesRegex(ValueError, "absent from ELF"):
                image_layout(elf, "2" * 64)
            summary = fake_summary()
            summary["elf_sha256"] = digest(elf)
            src, dest = folder / "summary.json", folder / "report.md"
            src.write_text(json.dumps(summary), encoding="utf-8")
            with patch.object(sys, "argv", ["report", "--elf", str(elf), "--summary", str(src), "--out", str(dest)]):
                main()
            result = dest.read_text(encoding="utf-8")
            self.assertIn("分项高水位合计为 624 B", result)
            self.assertIn(digest(src), result)


if __name__ == "__main__":
    unittest.main()
