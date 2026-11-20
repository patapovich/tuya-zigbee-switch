#!/usr/bin/env python3
"""Refuse a Telink ELF with a switch jump table inside .text.

tc32-gcc 4.5 compiles a switch either with a table of 32-bit addresses in .rodata
(-O2: right) or, under -Os or a per-function optimize level, with a byte or halfword
table right after the dispatch in .text that the dispatch still reads as 32-bit
addresses (`lsl #2; ldr rX, [rY, rX]; mov pc, rX`): it jumps to garbage and the
module restarts (found on a ZDMS16-2 with the Scenes server, 2026-11-20). The
dimmer-only builds use -Os with -fno-jump-tables, which makes no table at all.

A dispatch is a `tmov pc, rN` whose table address is loaded from a literal pool a
few instructions before it; the table must not lie inside .text.

Usage: check_jump_tables.py [--objdump tc32-elf-objdump] <elf>
"""
import argparse
import re
import subprocess
import sys


def text_range(objdump: str, elf: str) -> tuple[int, int]:
    out = subprocess.run([objdump, "-h", elf], check=True, capture_output=True, text=True).stdout
    m = re.search(r"^\s*\d+\s+\.text\s+([0-9a-f]+)\s+([0-9a-f]+)", out, re.M)
    if not m:
        raise SystemExit(f"{elf}: no .text section")
    size, vma = int(m.group(1), 16), int(m.group(2), 16)
    return vma, vma + size


def inline_tables(objdump: str, elf: str) -> list[str]:
    lo, hi = text_range(objdump, elf)
    lines = subprocess.run([objdump, "-d", elf], check=True, capture_output=True,
                           text=True).stdout.splitlines()
    words = {}
    for line in lines:
        m = re.match(r"\s+([0-9a-f]+):\s+[0-9a-f]{8}\s+\.word\s+0x([0-9a-f]+)", line)
        if m:
            words[int(m.group(1), 16)] = int(m.group(2), 16)
    bad, func = [], "?"
    for i, line in enumerate(lines):
        m = re.match(r"^[0-9a-f]+ <(.+)>:", line)
        if m:
            func = m.group(1)
        if not re.search(r"\ttmov\tpc, r\d", line):
            continue
        for prev in reversed(lines[max(0, i - 6):i]):
            m = re.search(r"\ttloadr\tr\d, \[pc, #\d+\]\s+; \(([0-9a-f]+)", prev)
            if m:
                table = words.get(int(m.group(1), 16))
                if table is not None and lo <= table < hi:
                    bad.append(f"{func}: jump table at {table:#x} inside .text")
                break
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--objdump", default="tc32-elf-objdump")
    ap.add_argument("elf")
    args = ap.parse_args()
    bad = inline_tables(args.objdump, args.elf)
    if bad:
        print(f"{args.elf}: switch jump tables that tc32-gcc dispatches wrongly:", file=sys.stderr)
        for b in bad:
            print("  " + b, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
