"""Report linked FLASH and static RAM from GNU objdump's ELF section table."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def sections(text):
    rows = []
    header = re.compile(r"^\s*\d+\s+(\S+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+")
    lines = text.splitlines()
    for first, flags in zip(lines, lines[1:]):
        match = header.match(first)
        if not match or "ALLOC" not in flags:
            continue
        name, size, vma, lma = match.groups()
        size, vma, lma = (int(x, 16) for x in (size, vma, lma))
        ram = ""
        if 0x20000000 <= vma < 0x20020000:
            ram = "SRAM"
        elif 0x10000000 <= vma < 0x10010000:
            ram = "CCM"
        flash = "LOAD" in flags and 0x08000000 <= lma < 0x08100000
        if size and (ram or flash):
            rows.append(dict(section=name, size=size, vma=hex(vma), lma=hex(lma),
                             ram=ram, occupies_flash=flash))
    if not any(r["occupies_flash"] for r in rows) or not any(r["ram"] for r in rows):
        raise ValueError("no F407 flash or SRAM sections in ELF")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    args = parser.parse_args()
    table = subprocess.check_output(["arm-none-eabi-objdump", "-h", str(args.elf)], text=True)
    rows = sections(table)
    flash_end = max(int(r["lma"], 16) + r["size"] for r in rows if r["occupies_flash"])
    print(json.dumps(dict(flash_image_span_bytes=flash_end - 0x08000000,
                          flash_load_bytes=sum(r["size"] for r in rows if r["occupies_flash"]),
                          sram_reserved_bytes=sum(r["size"] for r in rows if r["ram"] == "SRAM"),
                          ccm_reserved_bytes=sum(r["size"] for r in rows if r["ram"] == "CCM"),
                          sections=rows), indent=2))


if __name__ == "__main__":
    main()
