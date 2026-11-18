"""Factory firmware compatibility gate (zdms16-py32 docs/BOOTLOADER.md section
15, rule 3): towards a stock PY32 (1.0.7, the ZDMS16-2's factory firmware) the module
sends exactly what phase 1 sends: the same commands, data, order and timing
(within 50 ms).

The golden file data/stock_mcu_trace.json holds the UART traces of the
scenarios below, recorded from the stub build of home-dimmer 51f5c95a (the
phase-1 ZTU, 1.1.3-194952ee differs from it only in the carried image) with

    STOCK_TRACE_RECORD=1 pytest tests/test_stock_mcu_compat.py

It is the phase-1 behaviour and is never recorded again from a later build
(recording refuses a src/ tree that differs from 51f5c95a): a difference is a
change towards the factory MCU and must be argued in the design (section 15),
not re-recorded. The one difference argued there is the dump retry (section
10, change 6): a first-contact dump that got no DP report is asked once more,
1 s after the contact (scenario late_ready)."""

import json
import os
import subprocess
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
    root = Path(__file__).resolve().parent.parent
    same = subprocess.run(["git", "-C", str(root), "diff", "--quiet", SOURCE.split()[-1],
                           "--", "src"], capture_output=True)
    assert same.returncode == 0, f"src/ is not that of {SOURCE}: the golden trace stays"
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


# The one deliberate difference (section 10, change 6): when the first-contact
# dump got no DP report, it is asked once more, 1 s after the contact.
CONTACT_MS = 2000
DUMP_RETRY_MS = 1000


def without_dump_retry(trace: list) -> list:
    """The trace with its dump retry taken out; it must be there exactly once,
    at contact + 1 s."""
    retries = [i for i, (t, cmd, _) in enumerate(trace)
               if cmd == 0x28 and abs(t - (CONTACT_MS + DUMP_RETRY_MS)) <= TIME_TOLERANCE_MS]
    assert len(retries) == 1, f"no single dump retry at {CONTACT_MS + DUMP_RETRY_MS} ms: {trace}"
    return trace[:retries[0]] + trace[retries[0] + 1:]


@pytest.mark.parametrize("name", [n for n in SCENARIOS if n != "late_ready"])
def test_stock_mcu_gets_what_phase_1_sends(runs, name):
    mcu, st = runs[name]
    want = golden()["scenarios"][name]
    assert diff(encode(mcu), want["trace"]) is None, diff(encode(mcu), want["trace"])
    assert st == want["state"]


def test_a_late_stock_mcu_gets_phase_1_and_the_dump_retry(runs):
    """It listens only after the first dump went out: the retry is the only
    frame phase 1 did not send, and the min / max it brings are known now
    (phase 1 left them unknown until the next module restart)."""
    mcu, st = runs["late_ready"]
    want = golden()["scenarios"]["late_ready"]
    got = without_dump_retry(encode(mcu))
    assert diff(got, want["trace"]) is None, diff(got, want["trace"])
    assert want["state"]["min_1"] == "1"                     # phase 1: lost
    assert st == {**want["state"], "min_1": "51", "min_2": "51"}


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


def stock_run(**kw) -> tuple[StubProc, Device, StockMcu]:
    proc = StubProc(device_config=ZDMS16_2, dp_config=ZDMS16_2_DP_CONFIG).start()
    device = Device(proc)
    pump(proc)
    return proc, device, StockMcu(proc, device, **kw)


@pytest.mark.parametrize("ready_ms", range(0, 3001, 250))
def test_a_stock_mcu_gets_the_dump_whenever_it_starts_to_listen(ready_ms):
    """Rule 5: the module starts nothing before app init + 2 s (contract item
    3), and the dump's min / max reach it for a stock MCU that starts to
    listen up to 3 s after app init: by the first-contact dump up to 2150 ms
    (as in phase 1), by its retry after that."""
    proc, device, mcu = stock_run(ready_ms=ready_ms)
    try:
        mcu.run(12_000)
        assert min(t for t, _, _ in mcu.trace) >= CONTACT_MS
        assert state(device) == {"mcu_version": "1.0.7", "min_1": "51", "max_1": "254",
                                 "min_2": "51", "max_2": "254"}
        dumps = [t for t, cmd, _ in mcu.trace if cmd == 0x28]
        assert dumps == ([2000] if ready_ms <= 2000 else [2150] if ready_ms <= 2150
                         else [2150, CONTACT_MS + DUMP_RETRY_MS])
        assert mcu.faults == []
    finally:
        proc.stop()


def test_the_dump_retry_is_dropped_when_the_mcu_restarts_meanwhile():
    """An OK update result (or a block request for offset 0) means the MCU
    restarts: its next query is a first contact with a dump of its own."""
    proc, device, mcu = stock_run(ready_ms=10 ** 9)          # hears nothing
    try:
        mcu.run(2500)
        mcu.send(0xA2, bytes([0, 0x47]))
        mcu.run(3000)
        assert [t for t, cmd, _ in mcu.trace if cmd == 0x28] == [2150]
    finally:
        proc.stop()


def test_no_zigbee_input_breaks_a_stock_mcus_limits():
    """Rule 4: a fuzz of what Zigbee can ask (on / off / level / move / step /
    scene commands on both channels, settings writes, the diag dump query),
    with the stock MCU answering: every frame within the factory limits, and
    only the phase-1 command set."""
    import random
    rng = random.Random(20261118)
    proc, device, mcu = stock_run()
    try:
        mcu.run(3000)
        writes = [(0x0301, 0x0010, lambda: rng.randrange(1, 255)),       # ballast min
                  (0x0301, 0x0011, lambda: rng.randrange(1, 255)),       # ballast max
                  (0x0008, 0x0011, lambda: rng.randrange(0, 256)),       # on level
                  (0x0006, 0x4003, lambda: rng.choice([0, 1, 2, 0xFF])), # start-up on/off
                  (0x0007, 0x0000, lambda: rng.randrange(0, 3))]         # switch type
        for _ in range(300):
            ep = rng.choice([1, 2])
            kind = rng.randrange(4)
            if kind == 0:
                cmd = rng.choice([ZCL_CMD_ONOFF_OFF, ZCL_CMD_ONOFF_ON, ZCL_CMD_ONOFF_TOGGLE,
                                  ZCL_CMD_ON_WITH_RECALL_GLOBAL_SCENE])
                device.call_zigbee_cmd_raw(ep, ZCL_CLUSTER_ON_OFF, cmd)
            elif kind == 1:
                cmd = rng.randrange(8)
                payload = {0: [rng.randrange(256), rng.randrange(30), 0],
                           1: [rng.randrange(2), rng.randrange(1, 255)],
                           2: [rng.randrange(2), rng.randrange(1, 255), rng.randrange(30), 0],
                           3: []}[cmd & 3]
                device.call_zigbee_cmd_raw(ep, ZCL_CLUSTER_LEVEL_CONTROL, cmd,
                                           bytes(payload) or None)
            elif kind == 2:
                cluster, attr, value = rng.choice(writes)
                proc.exec(f"zcl_write {ep} 0x{cluster:04X} 0x{attr:04X} {value()}")
            else:
                proc.exec("zcl_write 1 0x0000 0xFF4F 2")                # diag: query all
            mcu.run(rng.choice([10, 50, 300, 1500]))
        mcu.run(5000)
        assert mcu.faults == []
        assert set(mcu.cmds()) <= STOCK_CMDS - {OTA_BLOCK}
        assert mcu.cmds().count(0x04) > 100
    finally:
        proc.stop()
