#!/usr/bin/env python3
"""Decode the secondary-MCU diagnostics attributes (Basic cluster, endpoint 1).

    0xff40          counters
    0xff41..0xff45  event log pages (oldest first)

Each value is the octet string payload (without the ZCL length byte), given
either as hex ("0A1B2C..") or as a JSON byte array ("[10,27,44,..]", the form
the Zigbee2MQTT dev console shows).

    decode_mcu_diag.py --counters <value> --log <page1> <page2> ...
"""
import argparse
import json
import struct

COUNTER_FIELDS = (
    "rx_frames", "rx_dps", "rx_bad", "rx_resync_bytes", "rx_oversize",
    "rx_hal_dropped", "tx_dp", "tx_cmd", "tx_ack", "tx_timeout",
    "tx_coalesced", "tx_drop", "tx_fail", "uart_reinit",
    "ack_latency_last_ms", "ack_latency_max_ms", "log_total",
    # appended later: older firmware sends fewer fields
    "ota_failures", "ota_pause_min",
    # phase 3: Open Dimmer's build and bootloader version (identity answer),
    # boot timing (ms since the module's start), the last block request
    # (bootloader version << 8 | mode, the installed build), rescues and link
    # probes asked for
    "mcu_build", "mcu_boot", "uart_up_ms", "first_answer_ms",
    "request_boot_mode", "request_build", "rescues", "link_probes",
)

KINDS = {
    1: "TX_DP", 2: "TX_CMD", 3: "RX_DP", 4: "RX_DP_PASSIVE", 5: "RX_CMD",
    6: "ACK", 7: "ACK_TIMEOUT", 8: "RX_BAD", 9: "TX_DROP", 10: "TX_FAIL",
    11: "UART_REINIT", 12: "IDENT", 13: "BOOT_REQUEST",
}

# Block request modes of the v2 bootloader (zdms16-py32 src/ota_proto.h).
MODES = {0: "v1 bootloader", 1: "asked update", 2: "no app", 3: "power-on probe",
         4: "rescue (crash loop)", 5: "rescue (module button)", 6: "link probe"}

ENTRY = struct.Struct("<HBBI")


def to_bytes(value: str) -> bytes:
    value = value.strip()
    if value in ("", "-"):
        return b""
    if value.startswith("["):
        return bytes(json.loads(value))
    return bytes.fromhex(value)


def decode_counters(data: bytes) -> dict:
    if not data:
        return {}
    fmt = data[0]
    if fmt != 1:
        raise ValueError(f"unknown counters format {fmt}")
    last_event_ms, = struct.unpack_from("<I", data, 1)
    count = min(len(COUNTER_FIELDS), (len(data) - 5) // 2)
    values = struct.unpack_from("<" + "H" * count, data, 5)
    return {"format": fmt, "last_event_ms": last_event_ms,
            **dict(zip(COUNTER_FIELDS, values))}


def decode_log(pages: list[bytes]) -> list[dict]:
    raw = b"".join(pages)
    out = []
    for off in range(0, len(raw) - len(raw) % ENTRY.size, ENTRY.size):
        t_ms, kind, ident, value = ENTRY.unpack_from(raw, off)
        out.append({"t_ms": t_ms, "kind": KINDS.get(kind, kind), "id": ident,
                    "value": value})
    return out


def describe(e: dict) -> str:
    """The packed values of the phase-3 entries, spelled out."""
    v = e["value"]
    length, boot, build = v >> 24, (v >> 16) & 0xFF, v & 0xFFFF
    if e["kind"] == "IDENT":
        return f"  ({length} B: build {build}, bootloader {boot})"
    if e["kind"] == "BOOT_REQUEST":
        return (f"  ({MODES.get(e['id'], e['id'])}, {length} B, bootloader {boot}, "
                f"installed build {build})")
    return ""


def format_log(entries: list[dict]) -> str:
    lines = []
    prev = None
    for e in entries:
        dt = "" if prev is None else f"+{(e['t_ms'] - prev) % 65536} ms"
        prev = e["t_ms"]
        lines.append(f"{e['t_ms']:5d} {dt:>10} {e['kind']:<14} id=0x{e['id']:02X} "
                     f"value={e['value']}{describe(e)}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--counters")
    ap.add_argument("--log", nargs="*", default=[])
    args = ap.parse_args()
    if args.counters:
        for k, v in decode_counters(to_bytes(args.counters)).items():
            print(f"{k:>20}: {v}")
    if args.log:
        print(format_log(decode_log([to_bytes(p) for p in args.log])))


if __name__ == "__main__":
    main()
