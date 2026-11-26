"""brightness_fade_time (Level Control 0xff12, per channel, 1/10 s, NV-kept; 0xFFFF: unset): a
lit-to-lit level change without a time (0xFFFF, as the converter sends it without a transition)
fades in that time for the whole range, a part of it for a smaller change: ms = time * |to -
from| / range, in the channel's MCU values (DP 160 / 161 before the level, Open Dimmer from
build 400). From dark, to off, a given time, unset: as before. Any other MCU: nothing sent."""
import pytest
from own_mcu import own_mcu_contact
from test_dimmer_mcu import ack_writes, level, scaled_to_tuya, scaled_to_zcl, set_level, start
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


def uptime(device) -> int:
    return int(device.status()["uptime_ms"])


def test_a_change_during_a_running_fade_starts_from_where_the_light_is():
    """A slider drag: commands 200 ms apart. The second one's time is its part of the whole range
    from the light's estimated position (the even point of the running move), not from the
    first command's target, so the speed stays range / fade time."""
    proc, device, mcu = od()
    try:
        device.write_zigbee_attr(1, LEVEL, ATTR_FADE, 20)                       # 2 s
        lit_at(device, mcu, 50)
        t0 = uptime(device)
        w1 = send_now(device, mcu, bytes([200]) + tt(NONE))
        ms1 = expected_ms(20, 50, 200)
        assert w1[0] == (DP_TIME, ms1)
        device.step_time(t0 + 200 - uptime(device))                              # 200 ms later
        w2 = send_now(device, mcu, bytes([150]) + tt(NONE))
        a, b = scaled_to_tuya(50, MN, MX), scaled_to_tuya(200, MN, MX)
        at = a + (b - a) * 200 // ms1                                          # where the light is
        to = scaled_to_tuya(150, MN, MX)
        ms2 = max(1, 2000 // (MX - MN) * abs(to - at) + 2000 % (MX - MN) * abs(to - at) // (MX - MN))
        assert w2[0] == (DP_TIME, ms2)
        assert ms2 != expected_ms(20, 200, 150)                                # not from the target
        device.step_time(5000)                                                   # long done
        w3 = send_now(device, mcu, bytes([100]) + tt(NONE))
        assert w3[0] == (DP_TIME, expected_ms(20, 150, 100))
    finally:
        proc.stop()


def send_now(device, mcu, payload: bytes) -> list[tuple[int, int]]:
    """A MoveToLevel, its writes answered without moving the clock far (< 200 ms)."""
    mcu.writes.clear()
    device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL, payload)
    mcu.serve(step_ms=10)
    return list(mcu.writes)


# ---- Stop, Move and Step during a timed move: from where the light is ------------------------
MOVE, STOP = 0x01, 0x03
UP = 0x00
SLOW = 300                                 # 30 s, the timed move of these tests: 254 -> 10


def slow_move(build=500):
    """Lit at 254, then a MoveToLevel to 10 in 30 s. Returns the uptime of that command."""
    proc, device, mcu = od(build)
    lit_at(device, mcu, 254)
    t0 = uptime(device)
    send_now(device, mcu, bytes([10]) + tt(SLOW))
    return proc, device, mcu, t0


def slow_at(ms: int) -> int:
    """The level the light is at that long into the slow move."""
    a, b = scaled_to_tuya(254, MN, MX), scaled_to_tuya(10, MN, MX)
    return scaled_to_zcl(a - (a - b) * ms // (SLOW * 100), MN, MX)


def test_a_stop_during_a_timed_move_stops_the_light_where_it_is():
    """The MCU has the whole move: without a write it would go on to its end, and CurrentLevel
    showed that end from the start. A Stop writes the level the light is at, at once."""
    proc, device, mcu, t0 = slow_move()
    try:
        assert level(device) == 10                                # the move's end, as before
        device.step_time(t0 + 5000 - uptime(device))
        mcu.writes.clear()
        device.call_zigbee_cmd(1, LEVEL, STOP, b"")
        mcu.serve()
        at = slow_at(5000)
        assert 200 < at < 230                                     # a sixth of the way down
        assert mcu.writes == [(DP_TIME, 1), (DP_LEVEL, scaled_to_tuya(at, MN, MX))]
        assert level(device) == at
        device.step_time(4000)
        mcu.writes.clear()
        device.call_zigbee_cmd(1, LEVEL, STOP, b"")               # nothing runs: nothing
        mcu.serve()
        assert mcu.writes == [] and level(device) == at
    finally:
        proc.stop()


def test_a_stop_after_the_timed_move_ended_sends_nothing():
    proc, device, mcu, t0 = slow_move()
    try:
        device.step_time(t0 + SLOW * 100 - uptime(device))
        mcu.writes.clear()
        device.call_zigbee_cmd(1, LEVEL, STOP, b"")
        mcu.serve()
        assert mcu.writes == [] and level(device) == 10
    finally:
        proc.stop()


def test_the_same_level_again_during_a_timed_move_keeps_the_move_as_it_runs():
    """A scene or an automation that fires twice: Open Dimmer keeps the running move for the same
    end (open-dimmer dim_fader_set_timed(), "the same move going on"), and so does the module's
    estimate: a Stop in it is where the first move has the light, one after its end nothing."""
    proc, device, mcu, t0 = slow_move()
    try:
        device.step_time(t0 + 10000 - uptime(device))
        send_now(device, mcu, bytes([10]) + tt(SLOW))             # the same again, at 10 s
        device.step_time(t0 + 20000 - uptime(device))
        mcu.writes.clear()
        device.call_zigbee_cmd(1, LEVEL, STOP, b"")
        mcu.serve()
        at = slow_at(20000)
        assert mcu.writes == [(DP_TIME, 1), (DP_LEVEL, scaled_to_tuya(at, MN, MX))]
        assert level(device) == at
    finally:
        proc.stop()
    proc, device, mcu, t0 = slow_move()
    try:
        device.step_time(t0 + 10000 - uptime(device))
        send_now(device, mcu, bytes([10]) + tt(SLOW))
        device.step_time(t0 + SLOW * 100 + 1000 - uptime(device))  # 1 s after the first's end
        mcu.writes.clear()
        device.call_zigbee_cmd(1, LEVEL, STOP, b"")
        mcu.serve()
        assert mcu.writes == [] and level(device) == 10
    finally:
        proc.stop()


def test_another_level_during_a_timed_move_starts_a_new_one_from_where_the_light_is():
    proc, device, mcu, t0 = slow_move()
    try:
        device.step_time(t0 + 10000 - uptime(device))
        t1 = uptime(device)
        send_now(device, mcu, bytes([20]) + tt(SLOW))             # 30 s from where it is
        device.step_time(t1 + 15000 - uptime(device))
        mcu.writes.clear()
        device.call_zigbee_cmd(1, LEVEL, STOP, b"")
        mcu.serve()
        top, end = scaled_to_tuya(254, MN, MX), scaled_to_tuya(10, MN, MX)
        a, b = top - (top - end) * (t1 - t0) // (SLOW * 100), scaled_to_tuya(20, MN, MX)
        at = scaled_to_zcl(a - (a - b) * 15000 // (SLOW * 100), MN, MX)
        assert mcu.writes == [(DP_TIME, 1), (DP_LEVEL, scaled_to_tuya(at, MN, MX))]
    finally:
        proc.stop()


def test_a_step_during_a_timed_move_steps_from_where_the_light_is():
    proc, device, mcu, t0 = slow_move()
    try:
        device.step_time(t0 + 5000 - uptime(device))
        mcu.writes.clear()
        device.call_zigbee_cmd(1, LEVEL, STEP, bytes([UP, 20]) + tt(0))
        mcu.serve()
        to = slow_at(5000) + 20
        assert mcu.writes == [(DP_TIME, 1), (DP_LEVEL, scaled_to_tuya(to, MN, MX))]
        assert level(device) == to
    finally:
        proc.stop()


def test_a_move_during_a_timed_move_sets_out_from_where_the_light_is():
    proc, device, mcu, t0 = slow_move()
    try:
        device.step_time(t0 + 5000 - uptime(device))
        mcu.writes.clear()
        device.call_zigbee_cmd(1, LEVEL, MOVE, bytes([UP, 10]))   # 3 levels a step
        device.step_time(300)                                     # its first step
        mcu.serve()
        to = slow_at(5300) + 3
        assert mcu.writes[:2] == [(DP_TIME, 300), (DP_LEVEL, scaled_to_tuya(to, MN, MX))]
        device.call_zigbee_cmd(1, LEVEL, STOP, b"")               # the ramp's end, no timed move
        mcu.serve()
        assert [v for dp, v in mcu.writes if dp == DP_TIME and v == 1] == []
    finally:
        proc.stop()


@pytest.mark.parametrize("build", [300, "stock"])
def test_a_stop_sends_nothing_to_an_mcu_without_timed_moves(build):
    """An Open Dimmer before build 400 and a factory MCU get no time, so no timed move runs."""
    proc, device = start()
    try:
        if build == "stock":
            set_level(proc, device, 254)
        else:
            mcu = own_mcu_contact(proc, device, build=build)
            lit_at(device, mcu, 254)
        take_tx(proc)
        device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL, bytes([10]) + tt(SLOW))
        ack_writes(proc, device)
        device.step_time(5000)
        take_tx(proc)
        device.call_zigbee_cmd(1, LEVEL, STOP, b"")
        device.step_time(200)
        assert take_tx(proc) == b"" and level(device) == 10
    finally:
        proc.stop()
