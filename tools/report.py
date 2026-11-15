"""Combine a completed serial replay summary and its F407 ELF into an acceptance table."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .resources import sections


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def image_layout(elf, model_id):
    if not re.fullmatch("[0-9a-f]{64}", model_id):
        raise ValueError("expected a frozen SHA-256 motion model ID")
    table = subprocess.check_output(["arm-none-eabi-objdump", "-h", str(elf)], text=True)
    rows = sections(table)
    # Verify the board's exported ID actually appears in the linked constant section.
    image = elf.read_bytes()
    matches = [line.split() for line in table.splitlines() if re.match(r"^\s*\d+\s+\.rodata\s+", line)]
    if len(matches) != 1:
        raise ValueError("missing ELF .rodata")
    record = matches[0]
    offset, length = int(record[5], 16), int(record[2], 16)
    if model_id.encode("ascii") + b"\0" not in image[offset:offset + length]:
        raise ValueError("board model ID is absent from ELF .rodata")
    listing = subprocess.check_output(["arm-none-eabi-nm", "-P", "-S", str(elf)], text=True)
    symbols = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in {"_sstack", "_estack", "_end", "ucHeap"}:
            symbols[parts[0]] = int(parts[3] if parts[0] == "ucHeap" else parts[2], 16)
    if symbols.keys() != {"_sstack", "_estack", "_end", "ucHeap"}:
        raise ValueError("ELF is missing linker or FreeRTOS heap symbols")
    return rows, symbols


def info_values(info, summary):
    if info[:3] != ["INFO", summary["model_id"], str(summary["clock_hz"])]:
        raise ValueError("STATUS identity or clock differs from replay summary")
    values = {}
    for field in info[3:]:
        key, value = field.split("=", 1)
        n = int(value)
        if n < 0 or key in values:
            raise ValueError(f"invalid STATUS field: {field}")
        values[key] = n
    return values


def pct(value):
    return "无样本" if value is None else f"{value:.2%}"


def render(summary, rows, symbols, elf_sha, summary_sha):
    n = summary["windows"]
    labels = summary["class_order"]
    if n <= 0 or labels != ["still", "walk", "run"]:
        raise ValueError("empty replay or unknown label order")
    if summary.get("elf_sha256", elf_sha) != elf_sha:
        raise ValueError("ELF digest differs from replay metadata")
    start = info_values(summary["board_info_start"], summary)
    end = info_values(summary["board_info_end"], summary)
    sram = sum(r["size"] for r in rows if r["ram"] == "SRAM")
    ccm = sum(r["size"] for r in rows if r["ram"] == "CCM")
    flash = sum(r["size"] for r in rows if r["occupies_flash"])
    reserve = sum(r["size"] for r in rows if r["section"] == "._user_heap_stack")
    heap = symbols["ucHeap"]
    msp = symbols["_estack"] - symbols["_sstack"]
    base = sram + ccm - reserve - heap
    if reserve < msp or not 0 < heap <= sram or base < 0 or msp <= 0:
        raise ValueError("unexpected ELF RAM layout")

    lines = ["# 任务 04 板端回放实测", "",
             f"固件记录 `{summary['firmware_version']}`，模型 `{summary['model_id']}`，"
             f"设备时钟 {summary['clock_hz']:,} Hz，共 {n} 个封存窗口。",
             f"ELF SHA-256 为 `{elf_sha}`。回放摘要 SHA-256 为 `{summary_sha}`。"]
    if "test_archive_sha256" in summary:
        lines.append(f"测试窗口归档 SHA-256 为 `{summary['test_archive_sha256']}`。")
    if "elf_sha256" not in summary:
        lines.append("串口模型 ID 已与 ELF 常量核对。同一模型的不同固件构建由烧录记录区分。")
    lines += ["", "## 封存窗口识别", "",
              "| 执行位置 | 准确率 | 静止召回率 | 走路召回率 | 跑步召回率 |",
              "| --- | ---: | ---: | ---: | ---: |"]
    systems = (("PC 浮点", "pc_float"), ("PC 整数参考", "pc_int"), ("F407", "board"))
    for title, key in systems:
        m = summary[key]
        if len(m["recall"]) != 3 or sum(map(sum, m["confusion"])) != n:
            raise ValueError(f"incomplete classification results for {title}")
        lines.append("| " + " | ".join([title, pct(m["accuracy"]), *map(pct, m["recall"])]) + " |")
    lines += ["", "| 执行位置 | 真实动作 | 预测静止 | 预测走路 | 预测跑步 |",
              "| --- | --- | ---: | ---: | ---: |"]
    for title, key in systems:
        for label, row in zip(("静止", "走路", "跑步"), summary[key]["confusion"], strict=True):
            lines.append("| " + " | ".join([title, label, *map(str, row)]) + " |")
    lines += ["", f"板端反量化 logits 相对 PC 浮点的平均绝对误差为 {summary['logits_mae']:.6g}，"
              f"最大绝对误差为 {summary['logits_max_abs']:.6g}。分类分歧率为 {pct(summary['class_disagreement'])}，"
              f"准确率差（板端减 PC 浮点）为 {100 * summary['board_minus_float_accuracy']:+.2f} 个百分点。",
              f"量化输入有 {summary['input_mismatch_windows']} 个窗口与参考不一致，原始窗口回放的整数分数有 "
              f"{summary['raw_score_mismatch_windows']} 个窗口不一致，直接量化输入回放的分数有 "
              f"{summary['integer_score_mismatch_windows']} 个窗口不一致。",
              "", "## 板端处理耗时", "",
              "| 测量范围 | 窗口数 | 均值（µs） | P95（µs） | 最大（µs） |", "| --- | ---: | ---: | ---: | ---: |"]
    for label, key in (("两层网络", "network"), ("完整窗口处理", "full_window")):
        t = summary[key]
        if t["n"] != n:
            raise ValueError(f"timing count differs from window count: {key}")
        lines.append(f"| {label} | {n} | {t['mean_us']:.3f} | {t['p95_us']:.3f} | {t['max_us']:.3f} |")
    lines += ["", "固件通过 DWT 周期计数测量。完整窗口处理从原始窗口就绪到分类结果产生，"
              "不含 2 秒采样和串口传输，任务抢占和中断会影响观测值。",
              "", "## Flash 和 RAM", "",
              "| 链接布局 | 字节 | 来源 |", "| --- | ---: | --- |",
              f"| Flash 装载 | {flash} | ELF 的 Flash LMA，含初始化数据装载副本 |",
              f"| SRAM 链接预留 | {sram} | 包含 RTOS 堆及链接器堆栈预留 |",
              f"| CCM 链接预留 | {ccm} | 独立存储区 |",
              f"| RTOS 堆容量 | {heap} | ELF 的 ucHeap，已含在 SRAM |",
              f"| MSP 预留 | {msp} | ELF 的 _estack − _sstack |",
              f"| 固定 RAM（SRAM 与 CCM） | {base} | RAM 装载节/静态节扣除 ucHeap 与 ._user_heap_stack |",
              "", "| 运行时项目 | 回放前 | 回放后 | 字节口径 |", "| --- | ---: | ---: | --- |"]
    fields = (("RTOS 堆最小空闲", "heap_min_free"), ("通信任务栈最小空闲", "comm_stack_min_free"),
              ("采样任务栈最小空闲", "sample_stack_min_free"), ("空闲任务栈最小空闲", "idle_stack_min_free"),
              ("定时器任务栈最小空闲", "timer_stack_min_free"), ("主栈最小空闲", "msp_min_free"),
              ("newlib 堆峰值", "newlib_heap_peak"))
    for title, key in fields:
        if key in start and key in end:
            reversed_mark = end[key] < start[key] if key == "newlib_heap_peak" else end[key] > start[key]
            if reversed_mark:
                raise ValueError(f"watermark moved backwards: {key}; check board reset")
        lines.append(f"| {title} | {start.get(key, '未记录')} | {end.get(key, '未记录')} | 设备 STATUS |")
    hfree, mfree, newlib = (end.get(k) for k in ("heap_min_free", "msp_min_free", "newlib_heap_peak"))
    if hfree is not None and not 0 <= hfree <= heap or mfree is not None and not 0 <= mfree <= msp:
        raise ValueError("reported free memory exceeds the matching ELF capacity")
    if newlib is not None and newlib > symbols["_sstack"] - symbols["_end"]:
        raise ValueError("newlib heap watermark exceeds its physical address range")
    if hfree is not None and mfree is not None:
        used = base + (heap - hfree) + (msp - mfree)
        lines += ["", f"RTOS 堆最大占用（含管理开销及已分配任务栈）{heap - hfree} B，"
                  f"MSP 水位折算占用 {msp - mfree} B。"]
        if newlib is not None and mfree > 0:
            lines.append(f"固定 RAM、RTOS 堆、MSP 与 newlib 堆的**分项高水位合计为 {used + newlib} B**。"
                         "各区域峰值可能出现在不同时间，此数为本轮占用估算。")
        else:
            lines.append(f"固定 RAM、RTOS 堆及 MSP 可核算小计为 {used} B。"
                         "newlib 堆水位缺失或 MSP 已用尽预留区，整机峰值待补测。")
    else:
        lines += ["", "设备记录缺少堆或主栈水位，整机峰值待补测。"]
    lines += ["", "任务栈水位仅列明细。通信与采样栈已随 RTOS 堆计入，空闲与定时器任务的静态栈已计入固定 RAM。"
              "MSP 填充值在初始化时写入，并为当时的调用帧保留保护区。"
              "水位涵盖从监测启用到本轮结束的执行路径；更深的中断嵌套或其他工况需另测。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8-sig"))
    rows, symbols = image_layout(args.elf, summary["model_id"])
    md = render(summary, rows, symbols, digest(args.elf), digest(args.summary))
    if args.out.resolve() in (args.summary.resolve(), args.elf.resolve()):
        parser.error("report path overlaps an input file")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
