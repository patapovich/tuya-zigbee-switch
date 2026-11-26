#!/usr/bin/env python3
"""Check a firmware ELF for the Mgmt_Rtg_req answer from the routing table
(src/telink/patch_sdk/zigbee/zdo/zdp.c.patch, zdo_mgmtRtgIndicate), against
its board.

Every router answers it but the ZDMS16-2 (SECONDARY_MCU_IMAGE): its image has
no room left for it below the OTA limit, so src/telink/Makefile sets
NO_MGMT_RTG_RSP there and that router answers NOT_SUPPORTED, as SDK 3.7.2.0
does. An end device has no routing table (the answer is router-only code). The
flag comes from the board's Makefile wiring, which no host test sees; this
check reads the symbols of the linked ELF.

    check_mgmt_rtg_symbol.py --nm <nm> (--answers | --not-answers) <elf>

Exit status 1 on a mismatch.
"""
import argparse
import subprocess
import sys

# The answer's function (static in the patched zdp.c: kept in the ELF only
# when g_zdpClientFunc's row refers to it, --gc-sections drops it otherwise).
SYMBOL = "zdo_mgmtRtgIndicate"


def has_symbol(nm: str, elf: str) -> bool:
    out = subprocess.run([nm, elf], check=True, capture_output=True, text=True).stdout
    return any(line.split()[-1] == SYMBOL for line in out.splitlines() if line.split())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--nm", required=True, help="the toolchain's nm")
    want = parser.add_mutually_exclusive_group(required=True)
    want.add_argument("--answers", action="store_true",
                      help="a router build that answers Mgmt_Rtg_req (all but the ZDMS16-2)")
    want.add_argument("--not-answers", action="store_true",
                      help="the ZDMS16-2 (NO_MGMT_RTG_RSP) or an end device")
    parser.add_argument("elf")
    args = parser.parse_args()
    found = has_symbol(args.nm, args.elf)
    if found != args.answers:
        what = "present: Mgmt_Rtg_rsp in a build that must not have it" if found else \
            "missing: no Mgmt_Rtg_rsp in a router build that must answer it"
        print(f"ERROR: {args.elf}: {SYMBOL} {what}", file=sys.stderr)
        return 1
    print(f"Mgmt_Rtg_rsp as expected: {'answered' if found else 'not answered'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
