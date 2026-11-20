#!/usr/bin/env python3
"""Check a firmware ELF for Open Dimmer support, against its board's
device_db entry.

Only boards whose entry has secondary_mcu_image may ask an MCU who it is
(0xA3) or take its seq-0 network queries for a restart: every other board
compiles the stubs of tuya_mcu_ota.c, so the factory MCUs of touch switches
and relays never see either (src/zigbee/tuya_mcu_ota.h, contract items 4 and
6; open-dimmer docs/BOOTLOADER.md section 15, rule 6). The compiler flag that
selects them comes from the board's Makefile wiring, which no host test sees;
this check reads the symbols of the linked ELF.

    check_open_dimmer_symbols.py --nm <nm> [--with-image] <elf>

--with-image: the board carries an image (secondary_mcu_image: true); its ELF
must hold the identity question and an image of at least a header. Without
it, the ELF must hold neither. Exit status 1 on a mismatch.
"""
import argparse
import subprocess
import sys

# Statics of the Open Dimmer branch of tuya_mcu_ota.c (none in the stubs),
# and the SWD master's main-loop entry (src/zigbee/mcu_flasher.c). All are
# kept by any optimisation: the timer's address is taken, ident_ask has
# several callers, app_task calls mcu_flasher_task. A rename must update this
# list: the boards with an image fail the check until it is.
OPEN_DIMMER_SYMBOLS = ("g_ident_timer", "ident_ask", "mcu_flasher_task")
IMAGE_SYMBOL = "secondary_mcu_image"
IMAGE_MIN = 0xD0            # the image header ends at 0xD0
STUB_IMAGE_MAX = 1          # the stubs' secondary_mcu_image[1]


def read_symbols(nm: str, elf: str) -> dict[str, int]:
    """Symbol name -> size (0 when nm gives none)."""
    out = subprocess.run([nm, "-S", elf], check=True, capture_output=True,
                         text=True).stdout
    symbols: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4:
            symbols[parts[3]] = max(symbols.get(parts[3], 0), int(parts[1], 16))
        elif len(parts) == 3:
            symbols.setdefault(parts[2], 0)
    return symbols


def problems(symbols: dict[str, int], with_image: bool) -> list[str]:
    found = [name for name in OPEN_DIMMER_SYMBOLS if name in symbols]
    image = symbols.get(IMAGE_SYMBOL, 0)
    if with_image:
        out = [f"{name} missing: no Open Dimmer support"
               for name in OPEN_DIMMER_SYMBOLS if name not in found]
        if image < IMAGE_MIN:
            out.append(f"{IMAGE_SYMBOL} is {image} bytes: no image carried")
        return out
    out = [f"{name} present: Open Dimmer support in a board without an image"
           for name in found]
    if image > STUB_IMAGE_MAX:
        out.append(f"{IMAGE_SYMBOL} is {image} bytes in a board without an image")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--nm", required=True, help="the toolchain's nm")
    parser.add_argument("--with-image", action="store_true",
                        help="the board's device_db entry has secondary_mcu_image")
    parser.add_argument("elf")
    args = parser.parse_args()
    found = problems(read_symbols(args.nm, args.elf), args.with_image)
    kind = "with" if args.with_image else "without"
    if found:
        for problem in found:
            print(f"ERROR: {args.elf}: {problem}", file=sys.stderr)
        return 1
    print(f"Open Dimmer symbols as expected for a board {kind} an image")
    return 0


if __name__ == "__main__":
    sys.exit(main())
