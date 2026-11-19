#!/usr/bin/env python3
"""Check a firmware ELF against its board's device_db dimmer_only.

A dimmer_only board (the AVATTO ZDMS16-2) is built with DIMMER_ONLY: its
firmware leaves out the switch, relay, cover and battery endpoints
(src/device_config/config_parser.c). Every other board keeps them, as a config
written to it may ask for any. The flag comes from the board's Makefile
wiring, which no host test sees: this check reads the symbols of the linked
ELF.

    check_dimmer_only_symbols.py --nm <nm> [--dimmer-only] <elf>

--dimmer-only: the board's entry has dimmer_only; its ELF must hold the
dimmer endpoints and none of the others. Without it, the ELF must hold all of
them. Exit status 1 on a mismatch.
"""
import argparse
import subprocess
import sys

# Kept by any optimisation: each has a caller in parse_config(). A rename must
# update this list: every board fails the check until it is.
LEFT_OUT = ("switch_cluster_add_to_endpoint", "relay_cluster_add_to_endpoint",
            "cover_switch_cluster_add_to_endpoint", "cover_cluster_add_to_endpoint",
            "battery_cluster_add_to_endpoint")
KEPT = ("dimmer_cluster_add_to_endpoint",)


def read_symbols(nm: str, elf: str) -> set[str]:
    out = subprocess.run([nm, elf], check=True, capture_output=True, text=True).stdout
    return {line.split()[-1] for line in out.splitlines() if line.strip()}


def problems(symbols: set[str], dimmer_only: bool) -> list[str]:
    out = [f"{name} missing" for name in KEPT if name not in symbols]
    if dimmer_only:
        out += [f"{name} present in a dimmer_only board" for name in LEFT_OUT if name in symbols]
    else:
        out += [f"{name} missing in a board without dimmer_only" for name in LEFT_OUT
                if name not in symbols]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--nm", required=True, help="the toolchain's nm")
    parser.add_argument("--dimmer-only", action="store_true",
                        help="the board's device_db entry has dimmer_only")
    parser.add_argument("elf")
    args = parser.parse_args()
    found = problems(read_symbols(args.nm, args.elf), args.dimmer_only)
    if found:
        for problem in found:
            print(f"ERROR: {args.elf}: {problem}", file=sys.stderr)
        return 1
    kind = "with" if args.dimmer_only else "without"
    print(f"Endpoint symbols as expected for a board {kind} dimmer_only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
