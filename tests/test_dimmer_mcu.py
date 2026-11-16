"""Dimmer <-> secondary MCU behaviour for the AVATTO ZDMS16-2 (PY32F002A MCU).

Facts these tests rely on (Tuya schema of the DMS16 family, same DP map on the
WiFi DMS16-W1/W2 and the Zigbee ZDMS16-2):

    DP 1/7   on/off            bool
    DP 2/8   brightness        value, valid range 10..1000
    DP 3/9   minimum brightness value, 10..1000
    DP 4/10  switch type       enum flip/sync/button
    DP 5/11  maximum brightness value, 10..1000
    DP 14    power-on state    enum off/on/memory

The MCU fades internally and reports intermediate brightness values while it
fades (seen with OpenBeken on the WiFi twin). OpenBeken also needed a TX queue
(one frame in flight) to drive this MCU reliably.
"""
import subprocess
import tempfile
from pathlib import Path

from client import StubProc
from conftest import Device
from zcl_consts import (
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
    ZCL_CMD_LEVEL_MOVE,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF,
    ZCL_CMD_LEVEL_STEP,
    ZCL_CMD_LEVEL_STEP_WITH_ON_OFF,
    ZCL_CMD_LEVEL_STOP,
    ZCL_CMD_LEVEL_STOP_WITH_ON_OFF,
    ZCL_LEVEL_MOVE_DOWN,
    ZCL_LEVEL_MOVE_UP,
)

ZDMS16_2 = "jtbgusdc;TS0601;DM2;P00O01L02S04M03X05;P01O07L08S0AM09X0B;U0E;M;"

ZCL_CLUSTER_BALLAST = 0x0301
ZCL_ATTR_BALLAST_MIN = 0x0010
ZCL_ATTR_BALLAST_MAX = 0x0011
ZCL_ATTR_CURRENT_LEVEL = 0x0000
ZCL_ATTR_ONOFF = 0x0000
ZCL_ATTR_SWITCH_TYPE = 0x0000

TUYA_CMD_WRITE = 0x04
TUYA_CMD_REPORT_PASSIVE = 0x05
TUYA_CMD_REPORT = 0x06

TUYA_TYPE_BOOL = 0x01
TUYA_TYPE_VALUE = 0x02
TUYA_TYPE_ENUM = 0x04

DP_ON_L1, DP_LEVEL_L1, DP_MIN_L1, DP_SWITCH_TYPE_L1, DP_MAX_L1 = 1, 2, 3, 4, 5
DP_ON_L2, DP_LEVEL_L2, DP_MIN_L2 = 7, 8, 9

TUYA_BRIGHTNESS_MIN = 10

RAMP_PERIOD_MS = 300
RAMP_STEP = 12

# Longer than any ack wait or fade hold-off the firmware may use.
SETTLE_MS = 6000


# --------------------------------------------------------------------------
# helpers


def tuya_frame(cmd: int, data: bytes = b"", seq: int = 1) -> str:
    body = (
        bytes([0x55, 0xAA, 0x02])
        + seq.to_bytes(2, "big")
        + bytes([cmd])
        + len(data).to_bytes(2, "big")
        + data
    )
    return (body + bytes([sum(body) % 256])).hex().upper()


def dp_payload(dp: int, dp_type: int, value: bytes) -> bytes:
    return bytes([dp, dp_type]) + len(value).to_bytes(2, "big") + value


def value_dp(dp: int, value: int) -> bytes:
    return dp_payload(dp, TUYA_TYPE_VALUE, value.to_bytes(4, "big"))


def bool_dp(dp: int, value: int) -> bytes:
    return dp_payload(dp, TUYA_TYPE_BOOL, bytes([value]))


def enum_dp(dp: int, value: int) -> bytes:
    return dp_payload(dp, TUYA_TYPE_ENUM, bytes([value]))


def zcl_to_tuya(level: int) -> int:
    return (level * 1000 + 127) // 254


def tuya_to_zcl(value: int) -> int:
    return (value * 254 + 500) // 1000


def pump(proc: StubProc, times: int = 4) -> None:
    for _ in range(times):
        proc.exec("s")


def take_tx(proc: StubProc) -> bytes:
    res = proc.exec("uart_tx")
    assert res.ok
    return bytes.fromhex(res.payload["tx"])


def parse_frames(raw: bytes) -> list[tuple[int, bytes]]:
    """Split a TX capture into (cmd, data) frames, checking every checksum."""
    frames = []
    i = 0
    while i + 9 <= len(raw):
        assert raw[i : i + 3] == b"\x55\xaa\x02", raw[i:].hex()
        cmd = raw[i + 5]
        dlen = int.from_bytes(raw[i + 6 : i + 8], "big")
        end = i + 8 + dlen
        assert sum(raw[i:end]) % 256 == raw[end], "bad checksum"
        frames.append((cmd, raw[i + 8 : end]))
        i = end + 1
    assert i == len(raw), f"trailing bytes {raw[i:].hex()}"
    return frames


def dp_writes(raw: bytes) -> list[tuple[int, int]]:
    """(dpid, value) for every DP write in a TX capture."""
    out = []
    for cmd, data in parse_frames(raw):
        if cmd != TUYA_CMD_WRITE:
            continue
        dpid, _, vlen = data[0], data[1], int.from_bytes(data[2:4], "big")
        out.append((dpid, int.from_bytes(data[4 : 4 + vlen], "big")))
    return out


def mcu_report(proc: StubProc, *dps: bytes, cmd: int = TUYA_CMD_REPORT) -> None:
    proc.exec(f"uart_rx {tuya_frame(cmd, b''.join(dps))}")
    pump(proc)


def ack_writes(proc: StubProc, device: Device, rounds: int = 8) -> list[tuple[int, int]]:
    """Play a well-behaved MCU: echo every DP write back as a 0x06 report, until
    the firmware has nothing more to send. Returns all writes seen."""
    seen = []
    for _ in range(rounds):
        pump(proc)
        writes = dp_writes(take_tx(proc))
        if not writes:
            device.step_time(50)
            pump(proc)
            writes = dp_writes(take_tx(proc))
            if not writes:
                break
        for dpid, value in writes:
            seen.append((dpid, value))
            if dpid in (DP_LEVEL_L1, DP_LEVEL_L2, DP_MIN_L1, DP_MAX_L1, DP_MIN_L2):
                mcu_report(proc, value_dp(dpid, value))
            else:
                mcu_report(proc, dp_payload(dpid, TUYA_TYPE_BOOL, bytes([value])))
    return seen


def level(device: Device, ep: int = 1) -> int:
    return int(device.read_zigbee_attr(ep, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_ATTR_CURRENT_LEVEL))


def onoff(device: Device, ep: int = 1) -> int:
    return int(device.read_zigbee_attr(ep, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF))


def start() -> tuple[StubProc, Device]:
    proc = StubProc(device_config=ZDMS16_2).start()
    device = Device(proc)
    pump(proc)
    take_tx(proc)  # boot chatter
    return proc, device


def set_level(proc: StubProc, device: Device, lvl: int) -> None:
    device.call_zigbee_cmd(
        1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
        bytes([lvl, 0, 0]),
    )
    ack_writes(proc, device)
    device.step_time(SETTLE_MS)
    pump(proc)
    take_tx(proc)


# --------------------------------------------------------------------------
# finding 2: MCU-side settings must be read back


def test_min_max_reports_reach_ballast_attributes():
    """Answer to query-all carries min/max: the ballast attributes must show the
    MCU's real values, not the parser defaults (max 100/254 = 39 %)."""
    proc, device = start()
    try:
        mcu_report(proc, value_dp(DP_MIN_L1, 100), cmd=TUYA_CMD_REPORT_PASSIVE)
        mcu_report(proc, value_dp(DP_MAX_L1, 600), cmd=TUYA_CMD_REPORT_PASSIVE)
        mcu_report(proc, value_dp(DP_MIN_L2, 200), cmd=TUYA_CMD_REPORT_PASSIVE)
        assert int(device.read_zigbee_attr(1, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MIN)) == tuya_to_zcl(100)
        assert int(device.read_zigbee_attr(1, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MAX)) == tuya_to_zcl(600)
        assert int(device.read_zigbee_attr(2, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MIN)) == tuya_to_zcl(200)
    finally:
        proc.stop()


def test_switch_type_report_reaches_switch_config():
    proc, device = start()
    try:
        mcu_report(proc, enum_dp(DP_SWITCH_TYPE_L1, 2), cmd=TUYA_CMD_REPORT_PASSIVE)
        assert int(device.read_zigbee_attr(
            1, ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG, ZCL_ATTR_SWITCH_TYPE)) == 2
    finally:
        proc.stop()


def test_no_default_settings_pushed_to_mcu():
    """The MCU stores min/max/switch type itself. The firmware must never
    overwrite them with its own parser defaults, at boot or on first contact."""
    proc, device = start()
    try:
        mcu_report(proc, cmd=0x20)  # MCU asks for network status = first contact
        device.step_time(SETTLE_MS)
        pump(proc)
        written = {dp for dp, _ in dp_writes(take_tx(proc))}
        assert not written & {DP_MIN_L1, DP_MAX_L1, DP_SWITCH_TYPE_L1, DP_MIN_L2, 11, 10}
    finally:
        proc.stop()


# --------------------------------------------------------------------------
# finding 5: several datapoints in one frame


def test_multi_dp_frame_is_fully_decoded():
    proc, device = start()
    try:
        mcu_report(
            proc,
            bool_dp(DP_ON_L1, 1),
            value_dp(DP_LEVEL_L1, 500),
            bool_dp(DP_ON_L2, 1),
            cmd=TUYA_CMD_REPORT_PASSIVE,
        )
        assert onoff(device, 1) == 1
        assert level(device, 1) == tuya_to_zcl(500)
        assert onoff(device, 2) == 1
    finally:
        proc.stop()


def test_full_status_dump_larger_than_64_bytes():
    """Query-all answer for both channels is ~90 bytes in one frame."""
    proc, device = start()
    try:
        mcu_report(
            proc,
            bool_dp(1, 1), value_dp(2, 400), value_dp(3, 100), enum_dp(4, 2),
            value_dp(5, 900), value_dp(6, 0),
            bool_dp(7, 1), value_dp(8, 700), value_dp(9, 150), enum_dp(10, 2),
            value_dp(11, 800), value_dp(12, 0),
            enum_dp(14, 0),
            cmd=TUYA_CMD_REPORT_PASSIVE,
        )
        assert level(device, 1) == tuya_to_zcl(400)
        assert level(device, 2) == tuya_to_zcl(700)
        assert int(device.read_zigbee_attr(2, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MAX)) == tuya_to_zcl(800)
    finally:
        proc.stop()


# --------------------------------------------------------------------------
# finding 4: one frame in flight


def test_one_frame_in_flight_until_mcu_acks():
    proc, device = start()
    try:
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
            bytes([200, 0, 0]),
        )
        pump(proc)
        first = dp_writes(take_tx(proc))
        assert len(first) == 1, f"expected one frame in flight, got {first}"
        dpid, value = first[0]
        mcu_report(proc, value_dp(dpid, value) if dpid == DP_LEVEL_L1 else bool_dp(dpid, value))
        second = dp_writes(take_tx(proc))
        assert len(second) == 1, f"next frame must follow the ack, got {second}"
        assert {first[0][0], second[0][0]} == {DP_ON_L1, DP_LEVEL_L1}
    finally:
        proc.stop()


def test_queue_moves_on_after_ack_timeout():
    """An MCU that never answers must not stall the queue forever."""
    proc, device = start()
    try:
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
            bytes([200, 0, 0]),
        )
        pump(proc)
        assert len(dp_writes(take_tx(proc))) == 1
        device.step_time(500)
        pump(proc)
        assert len(dp_writes(take_tx(proc))) == 1
    finally:
        proc.stop()


def test_level_is_sent_before_on():
    """Level first: the MCU then fades on straight to the new level instead of
    flashing the previous one. (Verify on the bench that the MCU accepts a level
    write while off; flip this test if it does not.)"""
    proc, device = start()
    try:
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
            bytes([200, 0, 0]),
        )
        writes = ack_writes(proc, device)
        assert [dp for dp, _ in writes][:2] == [DP_LEVEL_L1, DP_ON_L1]
    finally:
        proc.stop()


# --------------------------------------------------------------------------
# finding 3: MCU fade reports must not fight our own target


def test_ramp_ignores_lagging_fade_reports():
    proc, device = start()
    try:
        set_level(proc, device, 60)
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF,
            bytes([ZCL_LEVEL_MOVE_UP, 0xFF]),
        )
        for _ in range(4):
            device.step_time(RAMP_PERIOD_MS + 10)
            pump(proc)
            for dpid, value in dp_writes(take_tx(proc)):
                if dpid == DP_LEVEL_L1:
                    # MCU still fading: reports where it is, behind the target.
                    mcu_report(proc, value_dp(DP_LEVEL_L1, max(TUYA_BRIGHTNESS_MIN, value - 40)))
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_STOP_WITH_ON_OFF, None
        )
        assert level(device) >= 60 + 4 * RAMP_STEP - RAMP_STEP, level(device)
    finally:
        proc.stop()


def test_interim_fade_reports_do_not_move_current_level():
    proc, device = start()
    try:
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
            bytes([200, 0, 0]),
        )
        ack_writes(proc, device)
        mcu_report(proc, value_dp(DP_LEVEL_L1, 300))  # fading up
        mcu_report(proc, value_dp(DP_LEVEL_L1, 550))
        assert level(device) == 200
    finally:
        proc.stop()


def test_physical_dimming_after_holdoff_is_accepted():
    proc, device = start()
    try:
        set_level(proc, device, 200)
        mcu_report(proc, value_dp(DP_LEVEL_L1, 300))  # wall switch held, MCU dims
        assert level(device) == tuya_to_zcl(300)
    finally:
        proc.stop()


# --------------------------------------------------------------------------
# finding 6: brightness range


def test_level_one_maps_inside_tuya_range():
    proc, device = start()
    try:
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
            bytes([1, 0, 0]),
        )
        levels = [v for dp, v in ack_writes(proc, device) if dp == DP_LEVEL_L1]
        assert levels and min(levels) >= TUYA_BRIGHTNESS_MIN
    finally:
        proc.stop()


def test_move_down_with_onoff_turns_off_at_bottom():
    proc, device = start()
    try:
        set_level(proc, device, 30)
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF,
            bytes([ZCL_LEVEL_MOVE_DOWN, 0xFF]),
        )
        writes = []
        for _ in range(10):
            device.step_time(RAMP_PERIOD_MS + 10)
            writes += ack_writes(proc, device)
        levels = [v for dp, v in writes if dp == DP_LEVEL_L1]
        assert all(v >= TUYA_BRIGHTNESS_MIN for v in levels), levels
        assert (DP_ON_L1, 0) in writes
        assert onoff(device) == 0
    finally:
        proc.stop()


def test_plain_move_down_stays_on_at_minimum():
    proc, device = start()
    try:
        set_level(proc, device, 30)
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE,
            bytes([ZCL_LEVEL_MOVE_DOWN, 0xFF, 0, 0]),
        )
        writes = []
        for _ in range(10):
            device.step_time(RAMP_PERIOD_MS + 10)
            writes += ack_writes(proc, device)
        assert (DP_ON_L1, 0) not in writes
        assert onoff(device) == 1
        assert level(device) >= 1
    finally:
        proc.stop()


# --------------------------------------------------------------------------
# finding 7: commands without OnOff, and Step


def test_move_to_level_without_onoff():
    proc, device = start()
    try:
        set_level(proc, device, 50)
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
            bytes([128, 0, 0, 0, 0]),
        )
        writes = ack_writes(proc, device)
        assert (DP_LEVEL_L1, zcl_to_tuya(128)) in writes
        assert not [w for w in writes if w[0] == DP_ON_L1]
    finally:
        proc.stop()


def test_plain_move_and_stop_are_processed():
    proc, device = start()
    try:
        set_level(proc, device, 50)
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE,
            bytes([ZCL_LEVEL_MOVE_UP, 0xFF, 0, 0]),
        )
        for _ in range(3):
            device.step_time(RAMP_PERIOD_MS + 10)
            ack_writes(proc, device)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_STOP, bytes([0, 0]))
        stopped = level(device)
        assert stopped > 50
        device.step_time(3 * RAMP_PERIOD_MS)
        ack_writes(proc, device)
        assert level(device) == stopped
    finally:
        proc.stop()


def test_step_changes_level_by_step_size():
    proc, device = start()
    try:
        set_level(proc, device, 100)
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_STEP,
            bytes([ZCL_LEVEL_MOVE_UP, 50, 0, 0, 0, 0]),
        )
        writes = ack_writes(proc, device)
        assert (DP_LEVEL_L1, zcl_to_tuya(150)) in writes
        assert level(device) == 150
    finally:
        proc.stop()


def test_step_with_onoff_up_turns_on():
    proc, device = start()
    try:
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_STEP_WITH_ON_OFF,
            bytes([ZCL_LEVEL_MOVE_UP, 40, 0, 0]),
        )
        writes = ack_writes(proc, device)
        assert (DP_ON_L1, 1) in writes
        assert onoff(device) == 1
    finally:
        proc.stop()


# --------------------------------------------------------------------------
# finding 9: NV item ids must not move between releases


def _nv_ids() -> dict[str, list[int]]:
    """Evaluate the real macros with the C compiler."""
    root = Path(__file__).parent.parent
    prog = r"""
#include <stdio.h>
#include "device_config/nvm_items.h"
int main(void) {
    for (int i = 0; i < MAX_RELAYS; i++) printf("relay %d\n", NV_ITEM_RELAY_CLUSTER_DATA(i));
    for (int i = 0; i < MAX_COVER_SWITCHES; i++) printf("cover_switch %d\n", NV_ITEM_COVER_SWITCH_CONFIG(i));
    for (int i = 0; i < MAX_COVERS; i++) printf("cover %d\n", NV_ITEM_COVER_CONFIG(i));
    for (int i = 0; i < MAX_SWITCHES; i++) printf("switch %d\n", NV_ITEM_SWITCH_CLUSTER_DATA(i));
    printf("fixed %d\nfixed %d\nfixed %d\nfixed %d\nfixed %d\nfixed %d\nfixed %d\nfixed %d\n",
           NV_ITEM_CURRENT_VERSION_IN_NV, NV_ITEM_DEVICE_CONFIG, NV_ITEM_BASIC_CLUSTER_DATA,
           NV_ITEM_DEVICE_TYPE, NV_ITEM_MULTI_PRESS_RESET_COUNT, NV_ITEM_POLL_CONTROL_CONFIG,
           NV_ITEM_DP_CONFIG, NV_ITEM_DEVICE_CONFIG_EXT);
    return 0;
}
"""
    with tempfile.TemporaryDirectory() as tmp:
        c, exe = Path(tmp) / "nv.c", Path(tmp) / "nv"
        c.write_text(prog)
        subprocess.run(["gcc", "-I", str(root / "src"), str(c), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    ids: dict[str, list[int]] = {}
    for line in out.splitlines():
        kind, value = line.split()
        ids.setdefault(kind, []).append(int(value))
    return ids


def test_nv_item_ids_match_released_layout():
    """Ids in 1.1.x: relays 9..13, cover switches 14..16, covers 17..19.
    Moving them makes an OTA-updated device read another item's data."""
    ids = _nv_ids()
    assert ids["relay"][:5] == [9, 10, 11, 12, 13]
    assert ids["cover_switch"] == [14, 15, 16]
    assert ids["cover"] == [17, 18, 19]


def test_nv_item_ids_are_unique():
    ids = _nv_ids()
    flat = [v for values in ids.values() for v in values]
    assert len(flat) == len(set(flat)), sorted(flat)
