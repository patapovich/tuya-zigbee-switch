"""brightness_fade_time (Level Control 0xff12, per channel, 1/10 s, NV-kept; 0xFFFF: unset): a
lit-to-lit level change without a time (0xFFFF, as the converter sends it without a transition)
fades in that time for the whole range, a part of it for a smaller change: ms = time * |to -
from| / range, in the channel's MCU values (DP 160 / 161 before the level, Open Dimmer from
build 400). From dark, to off, a given time, unset: as before. Any other MCU: nothing sent."""
import pytest
from own_mcu import own_mcu_contact
from test_dimmer_mcu import ack_writes, scaled_to_tuya, set_level, start
from tuya_mcu import take_tx

LEVEL, ON_OFF = 0x0008, 0x0006
ATTR_FADE = 0xFF12
MOVE_TO_LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, STEP = 0x00, 0x04, 0x02
NONE = 0xFFFF
DP_ON, DP_LEVEL, DP_TIME = 1, 2, 160
MN, MX = 200, 1000                         # OwnMcu's range


def tt(t: int) -> bytes:
    return t.to_bytes(2, "little")


def od(build=500):
    proc, device = start()
    mcu = own_mcu_contact(proc, device, build=build)
    take_tx(proc)
    return proc, device, mcu


def send(device, mcu, command: int, payload: bytes) -> list[tuple[int, int]]:
    mcu.writes.clear()
    device.call_zigbee_cmd(1, LEVEL, command, payload)
    mcu.serve()
    device.step_time(2000)                 # past the 1.5 s an earlier time could apply
    return list(mcu.writes)


def expected_ms(tenths: int, a: int, b: int, span: int = MX - MN) -> int:
    full, d = tenths * 100, min(abs(scaled_to_tuya(b, MN, MX) - scaled_to_tuya(a, MN, MX)), span)
    return max(1, full // span * d + full % span * d // span)


def lit_at(device, mcu, level: int) -> None:
    send(device, mcu, MOVE_TO_LEVEL_WITH_ONOFF, bytes([level]) + tt(NONE))


def test_unset_by_default_and_kept_over_a_restart():
    proc, device = start()
    try:
        assert int(device.read_zigbee_attr(1, LEVEL, ATTR_FADE)) == NONE
        device.write_zigbee_attr(1, LEVEL, ATTR_FADE, 35)
    finally:
        proc.stop()
    proc, device = start()
    try:
        assert int(device.read_zigbee_attr(1, LEVEL, ATTR_FADE)) == 35
        assert int(device.read_zigbee_attr(2, LEVEL, ATTR_FADE)) == NONE        # per channel
    finally:
        proc.stop()


def test_a_write_is_reported():
    proc, device = start()
    try:
        device.clear_events()
        device.write_zigbee_attr(2, LEVEL, ATTR_FADE, 12)
        assert any(e.kind == "zcl_attr_change" and int(e.payload["attr"], 16) == ATTR_FADE
                   and int(e.payload["ep"]) == 2 for e in device._events)
    finally:
        proc.stop()


@pytest.mark.parametrize("build", [500, 400])
@pytest.mark.parametrize("tenths, a, b", [(20, 50, 200), (20, 200, 50), (12, 1, 254), (5, 100, 110),
                                          (600, 30, 230)])
def test_a_lit_change_without_a_time_fades_its_part_of_the_whole_range(build, tenths, a, b):
    proc, device, mcu = od(build)
    try:
        device.write_zigbee_attr(1, LEVEL, ATTR_FADE, tenths)
        lit_at(device, mcu, a)
        w = send(device, mcu, MOVE_TO_LEVEL, bytes([b]) + tt(NONE))
        assert w == [(DP_TIME, expected_ms(tenths, a, b)), (DP_LEVEL, scaled_to_tuya(b, MN, MX))]
    finally:
        proc.stop()


def test_the_bounds_of_the_math():
    """The smallest change takes at least 1 ms; the longest time over the whole range is the
    DP's 65535 ms (no overflow: 6553.4 s * 990 would not fit 32 bits as one product)."""
    proc, device, mcu = od()
    try:
        device.write_zigbee_attr(1, LEVEL, ATTR_FADE, 1)                      # 0.1 s
        lit_at(device, mcu, 100)
        assert send(device, mcu, MOVE_TO_LEVEL, bytes([101]) + tt(NONE))[0] == (DP_TIME, 1)
        device.write_zigbee_attr(1, LEVEL, ATTR_FADE, 0xFFFE)                 # 6553.4 s
        lit_at(device, mcu, 1)
        assert send(device, mcu, MOVE_TO_LEVEL, bytes([254]) + tt(NONE))[0] == (DP_TIME, 0xFFFF)
        device.write_zigbee_attr(1, LEVEL, ATTR_FADE, 0)                      # 0: at once
        assert send(device, mcu, MOVE_TO_LEVEL, bytes([10]) + tt(NONE))[0] == (DP_TIME, 1)
    finally:
        proc.stop()


def test_what_keeps_its_old_behaviour():
    proc, device, mcu = od()
    try:
        device.write_zigbee_attr(1, LEVEL, ATTR_FADE, 20)
        # from dark: the fade settings
        assert DP_TIME not in [dp for dp, _ in send(device, mcu, MOVE_TO_LEVEL_WITH_ONOFF,
                                                     bytes([120]) + tt(NONE))]
        assert send(device, mcu, MOVE_TO_LEVEL, bytes([120]) + tt(NONE)) == \
            [(DP_LEVEL, scaled_to_tuya(120, MN, MX))]                          # no change: nothing
        assert send(device, mcu, MOVE_TO_LEVEL, bytes([200]) + tt(5))[0] == (DP_TIME, 500)  # given
        assert send(device, mcu, MOVE_TO_LEVEL, bytes([100]) + tt(0))[0] == (DP_TIME, 1)    # given 0
        w = send(device, mcu, MOVE_TO_LEVEL_WITH_ONOFF, bytes([0]) + tt(NONE))  # to off: fade off
        assert w == [(DP_ON, 0)]
    finally:
        proc.stop()


def test_a_step_without_a_time_fades_its_part_too():
    proc, device, mcu = od()
    try:
        device.write_zigbee_attr(1, LEVEL, ATTR_FADE, 30)
        lit_at(device, mcu, 100)
        w = send(device, mcu, STEP, bytes([0, 40]) + tt(NONE))
        assert w == [(DP_TIME, expected_ms(30, 100, 140)), (DP_LEVEL, scaled_to_tuya(140, MN, MX))]
    finally:
        proc.stop()


def test_unset_is_the_mcus_own_rate():
    proc, device, mcu = od()
    try:
        lit_at(device, mcu, 100)
        assert send(device, mcu, MOVE_TO_LEVEL, bytes([200]) + tt(NONE)) == \
            [(DP_LEVEL, scaled_to_tuya(200, MN, MX))]
    finally:
        proc.stop()


@pytest.mark.parametrize("build", [300, None, "stock"])
def test_no_other_mcu_gets_a_time(build):
    if build == "stock":
        proc, device = start()
    else:
        proc, device, _ = od(build)
    try:
        device.write_zigbee_attr(1, LEVEL, ATTR_FADE, 20)
        if build == "stock":
            set_level(proc, device, 50)          # lit, its first contact done
        else:
            device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([50]) + tt(NONE))
            ack_writes(proc, device)
            device.step_time(2000)
        device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL, bytes([200]) + tt(NONE))
        assert [dp for dp, _ in ack_writes(proc, device)] == [DP_LEVEL]
    finally:
        proc.stop()
