"""Factory firmware compatibility gate (zdms16-py32 docs/BOOTLOADER.md section
15, rule 3): towards a stock PY32 (1.0.7, the ZDMS16-2's factory firmware) the module
sends exactly what phase 1 sends: the same commands, data, order and timing
(within 50 ms).

The golden file data/stock_mcu_trace.json holds the UART traces of the
scenarios below, recorded from the stub build of home-dimmer 51f5c95a (the
phase-1 ZTU, 1.1.3-194952ee differs from it only in the carried image) with

    STOCK_TRACE_RECORD=1 pytest tests/test_stock_mcu_compat.py

It is the phase-1 behaviour and is never recorded again from a later build:
a difference is a change towards the factory MCU and must be argued in the
design (section 15), not re-recorded."""

import json
import os
from pathlib import Path

import pytest
from client import StubProc
from conftest import Device
from test_dimmer_mcu import (
    ZCL_ATTR_BALLAST_MAX,
    ZCL_ATTR_BALLAST_MIN,
    ZCL_CLUSTER_BALLAST,
    ZDMS16_2,
    ZDMS16_2_DP_CONFIG,
)
from test_mcu_ota import CRC, IMAGE, SIZE, VERSION
from tuya_mcu import OTA_BLOCK, StockMcu, pump
from zcl_consts import (
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CMD_ON_WITH_RECALL_GLOBAL_SCENE,
    ZCL_CMD_ONOFF_OFF,
    ZCL_CMD_ONOFF_ON,
    ZCL_CMD_ONOFF_TOGGLE,
)

GOLDEN = Path(__file__).resolve().parent / "data" / "stock_mcu_trace.json"
SOURCE = "home-dimmer 51f5c95a"
TIME_TOLERANCE_MS = 50
ATTR_MCU_VERSION = 0xFF12
RECORD = os.environ.get("STOCK_TRACE_RECORD") == "1"

# What the module may send to a stock MCU at all (rule 3): the version query,
# the dump query, DP writes, the replies to its 0x20 / 0x24 / 0x25 queries, and
# 0xA1 block answers only as the reply to a request.
STOCK_CMDS = {0x0B, 0x28, 0x04, 0x20, 0x24, 0x25, 0xA1}


def request(offset: int, max_len: int, mode: int | None = None) -> bytes:
    """An 0xA1 block request: 6 bytes (v1 bootloader) or, with a mode, the
    16 bytes of a v2 bootloader (version 2, the installed 2.1.12 header's
    bytes 8..15)."""
    data = offset.to_bytes(4, "big") + max_len.to_bytes(2, "big")
    if mode is not None:
        data += bytes([2, mode]) + bytes.fromhex("a6ad1bcd9c000000")
    return data


# ---- scenarios -----------------------------------------------------------
# Each drives a StockMcu; t = 0 is the module's app init.


def cold_boot(mcu: StockMcu, device: Device) -> None:
    """Mains on: the stock MCU listens 1.5 s after its power-up."""
    mcu.ready_ms = 1500
    mcu.run(60_000)


def ztu_restart(mcu: StockMcu, device: Device) -> None:
    """Only the module restarts (a ZTU OTA, a config write): the MCU runs on."""
    mcu.run(60_000)


def late_ready(mcu: StockMcu, device: Device) -> None:
    """The MCU listens only after the first-contact dump went out."""
    mcu.ready_ms = 2500
    mcu.run(60_000)


def zigbee_commands(mcu: StockMcu, device: Device) -> None:
    mcu.run(5000)
    for ep in (1, 2):
        for cluster, cmd, payload in (
            (ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON, None),
            (ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([128, 0, 0])),
            (ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL, bytes([20, 10, 0])),
            (ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF, None),
            (ZCL_CLUSTER_ON_OFF, ZCL_CMD_ON_WITH_RECALL_GLOBAL_SCENE, None),
            (ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_TOGGLE, None),
        ):
            device.call_zigbee_cmd(ep, cluster, cmd, payload)
            mcu.run(2500)


def module_button(mcu: StockMcu, device: Device) -> None:
    """The stock MCU asks for a rejoin after a 3 s hold of the module button."""
    mcu.run(5000)
    mcu.send(0x03, bytes([0x01]))
    mcu.run(10_000)


def spurious_frames(mcu: StockMcu, device: Device) -> None:
    """Frames a stock MCU should never send, as if it did: seq-0 network
    queries, an OK update result, block requests of a v1 and a v2 bootloader
    (power-on probe and link probe)."""
    mcu.run(5000)
    for _ in range(3):
        mcu.send(0x20, seq=0)
        mcu.run(30_000, step_ms=20)
    mcu.send(0xA2, bytes([0, 0x47]))
    mcu.run(3000)
    mcu.send(0x20, seq=0)
    mcu.run(10_000)
    for data in (request(0, 64), request(0xC0, 16), request(0xC0, 16, mode=3),
                 request(0xC0, 16, mode=6), request(64, 64, mode=6)):
        mcu.send(0xA1, data)
        mcu.run(3000)
    mcu.send(0x20, seq=0)
    mcu.run(10_000)


def idle(mcu: StockMcu, device: Device) -> None:
    mcu.run(10_000)
    mcu.run(10 * 60_000, step_ms=50)


SCENARIOS = {f.__name__: f for f in (cold_boot, ztu_restart, late_ready, zigbee_commands,
                                     module_button, spurious_frames, idle)}


def state(device: Device) -> dict:
    """What a lost frame would change: the version and the dump's min / max."""
    out = {"mcu_version": device.read_zigbee_attr(1, 0x0000, ATTR_MCU_VERSION)}
    for ep in (1, 2):
        out[f"min_{ep}"] = device.read_zigbee_attr(ep, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MIN)
        out[f"max_{ep}"] = device.read_zigbee_attr(ep, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MAX)
    return out


def run_scenario(name: str) -> tuple[StockMcu, dict]:
    proc = StubProc(device_config=ZDMS16_2, dp_config=ZDMS16_2_DP_CONFIG).start()
    try:
        device = Device(proc)
        pump(proc)
        mcu = StockMcu(proc, device)
        mcu.poll()                       # boot chatter: nothing on the UART
        SCENARIOS[name](mcu, device)
        return mcu, state(device)
    finally:
        proc.stop()


def encode(mcu: StockMcu) -> list:
    """The trace as the golden file keeps it. A block answer holds the carried
    image, which py32-bump replaces: kept as what it answers (offset, number
    of image bytes) and checked against the carried image on its own."""
    out = []
    for t, cmd, data in mcu.trace:
        if cmd == OTA_BLOCK:
            out.append([t, cmd, f"block {data[:4].hex()} +{len(data) - 13}"])
        else:
            out.append([t, cmd, data.hex()])
    return out


def golden() -> dict:
    return json.loads(GOLDEN.read_text())


def diff(got: list, want: list) -> str | None:
    """None when equal: the same frames in the same order, each within the
    time tolerance; else where they part."""
    for i, (g, w) in enumerate(zip(got, want)):
        if g[1:] != w[1:] or abs(g[0] - w[0]) > TIME_TOLERANCE_MS:
            return f"frame {i}: got {g}, phase 1 sent {w}"
    if len(got) != len(want):
        extra = got[len(want):] or want[len(got):]
        return f"{len(got)} frames, phase 1 sent {len(want)}; first extra {extra[0]}"
    return None


@pytest.fixture(scope="module")
def runs() -> dict:
    out = {name: run_scenario(name) for name in SCENARIOS}
    if RECORD:
        record(out)
    return out


def record(out: dict) -> None:
    """One frame per line, so a diff of the file reads like the traces."""
    scenarios = []
    for name, (mcu, st) in out.items():
        frames = ",\n".join("    " + json.dumps(f) for f in encode(mcu))
        scenarios.append(f'  {json.dumps(name)}: {{\n   "state": {json.dumps(st)},\n'
                         f'   "trace": [\n{frames}\n   ]\n  }}')
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_text(f'{{\n "source": {json.dumps(SOURCE)},\n'
                      f' "time_tolerance_ms": {TIME_TOLERANCE_MS},\n'
                      f' "scenarios": {{\n' + ",\n".join(scenarios) + "\n }\n}\n")


def test_the_golden_trace_is_the_phase_1_one(runs):
    g = golden()
    assert g["source"] == SOURCE
    assert set(g["scenarios"]) == set(SCENARIOS)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_stock_mcu_gets_what_phase_1_sends(runs, name):
    mcu, st = runs[name]
    want = golden()["scenarios"][name]
    assert diff(encode(mcu), want["trace"]) is None, diff(encode(mcu), want["trace"])
    assert st == want["state"]


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_stock_mcu_gets_only_the_phase_1_command_set_within_its_limits(runs, name):
    mcu, _ = runs[name]
    assert set(mcu.cmds()) <= STOCK_CMDS
    assert not [c for c in mcu.cmds() if c in (0xA0, 0xA2, 0xA3)]
    assert mcu.faults == []
    assert mcu.requests == 0             # every block request got its one reply


def test_block_answers_hold_the_carried_image(runs):
    for mcu, _ in runs.values():
        for _, cmd, data in mcu.trace:
            if cmd != OTA_BLOCK:
                continue
            off = int.from_bytes(data[:4], "big")
            assert data[4:13] == (SIZE.to_bytes(4, "big") + CRC.to_bytes(4, "big")
                                  + bytes([VERSION]))
            assert data[13:] == IMAGE[off:off + len(data) - 13]


def test_block_requests_of_a_link_probe_are_answered_like_any_other(runs):
    """Rule 9: the link watchdog's mode-6 requests get the answer a power-on
    probe (mode 3) and a v1 bootloader get."""
    mcu, _ = runs["spurious_frames"]
    answers = [data for _, cmd, data in mcu.trace if cmd == OTA_BLOCK]
    assert len(answers) == 5
    assert answers[1] == answers[2] == answers[3]          # 0xC0 / 16: v1, mode 3, mode 6
    assert answers[1][13:] == IMAGE[0xC0:0xD0]
    assert answers[4][:4] == (64).to_bytes(4, "big") and len(answers[4]) == 13 + 64
