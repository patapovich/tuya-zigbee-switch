"""Open Dimmer 2.2.0 (build 500) on the module side: the countdown (ZCL OnWithTimedOff and
genOnOff 0xff10, DP 6 / 12), the wall switch mode (genOnOffSwitchCfg 0xff10, DP 162 / 163),
the level after a power cut (Level Control StartUpCurrentLevel, DP 164 / 165), identify
(genIdentify, DP 166 / 167) and the start boost (lightingBallastCfg 0xff10, DP 168 / 169).
Those DPs go only to an Open Dimmer identified with build 500 or later; any other MCU gets what
it got before (an OnWithTimedOff is an On), and a write of their attributes sends it nothing.
"""
import pytest
from own_mcu import FEATURES_BUILD, LAMP_COMP_BUILD, own_mcu_contact
from test_dimmer_mcu import ack_writes, scaled_to_tuya, scaled_to_zcl, start
from tuya_mcu import (
    TUYA_CMD_REPORT_PASSIVE,
    bool_dp,
    dp_writes,
    mcu_report,
    pump,
    take_tx,
    value_dp,
)

IDENTIFY, ON_OFF, SWITCH_CFG, LEVEL, BALLAST = 0x0003, 0x0006, 0x0007, 0x0008, 0x0301
ATTR_ONOFF = 0x0000
ATTR_COUNTDOWN = 0xFF10          # genOnOff
ATTR_INPUT_MODE = 0xFF10         # genOnOffSwitchCfg
ATTR_FAST_PRESS = 0xFF11         # genOnOffSwitchCfg
ATTR_STARTUP_LEVEL = 0x4000      # Level Control StartUpCurrentLevel
ATTR_IDENTIFY_TIME = 0x0000
ATTR_BOOST = 0xFF10              # lightingBallastCfg
ATTR_LAMP_COMP = 0xFF11          # lightingBallastCfg (2.2.8)
ATTR_CUSTOM_CURVE = 0xFF12       # lightingBallastCfg (2.2.8): 17 u16, little-endian
CMD_OFF, CMD_ON, CMD_ON_WITH_TIMED_OFF = 0x00, 0x01, 0x42
CMD_IDENTIFY = 0x00
MINIMUM, PREVIOUS = 0x00, 0xFF

DP_ON = {1: 1, 2: 7}
DP_COUNTDOWN = {1: 6, 2: 12}
DP_INPUT = {1: 162, 2: 163}
DP_POWER_LEVEL = {1: 164, 2: 165}
DP_IDENTIFY = {1: 166, 2: 167}
DP_BOOST = {1: 168, 2: 169}
DP_FAST_PRESS = {1: 170, 2: 171}
DP_LAMP_COMP = {1: 172, 2: 173}
NEW_DPS = {6, 12, *range(162, 172)}
MCU_MIN = 200                    # the firmware's default min (OwnMcu)


def own(build: int | None = FEATURES_BUILD, prepare=None):
    """An identified Open Dimmer of that build after its first contact (dump read)."""
    proc, device = start()
    mcu = own_mcu_contact(proc, device, build=build, prepare=prepare)
    take_tx(proc)
    mcu.writes.clear()
    return proc, device, mcu


def attr(device, ep: int, cluster: int, attr_id: int) -> int:
    return int(device.read_zigbee_attr(ep, cluster, attr_id))


def cmd(device, mcu, ep: int, cluster: int, command: int, payload: bytes | None = None):
    device.call_zigbee_cmd(ep, cluster, command, payload)
    mcu.serve()
    out = list(mcu.writes)
    mcu.writes.clear()
    return out


def write(device, mcu, ep: int, cluster: int, attr_id: int, value: int):
    device.write_zigbee_attr(ep, cluster, attr_id, value)
    mcu.serve()
    out = list(mcu.writes)
    mcu.writes.clear()
    return out


def timed_off(ctrl: int, tenths: int, off_wait: int = 0) -> bytes:
    return bytes([ctrl]) + tenths.to_bytes(2, "little") + off_wait.to_bytes(2, "little")


# ---- countdown ---------------------------------------------------------------

@pytest.mark.parametrize("ep", [1, 2])
def test_on_with_timed_off_is_an_on_and_then_the_countdown(ep):
    proc, device, mcu = own()
    try:
        w = cmd(device, mcu, ep, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 255))   # 25.5 s
        assert w == [(DP_ON[ep], 1), (DP_COUNTDOWN[ep], 26)]                      # whole s, up
        assert attr(device, ep, ON_OFF, ATTR_ONOFF) == 1
        assert attr(device, ep, ON_OFF, ATTR_COUNTDOWN) == 26
        assert mcu.ch[ep - 1]["countdown"] == 26
    finally:
        proc.stop()


def test_on_with_timed_off_accept_only_when_on():
    proc, device, mcu = own()
    try:
        assert cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(1, 100)) == []
        assert attr(device, 1, ON_OFF, ATTR_ONOFF) == 0
        cmd(device, mcu, 1, ON_OFF, CMD_ON)
        w = cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(1, 100))
        assert w == [(DP_ON[1], 1), (DP_COUNTDOWN[1], 10)]
    finally:
        proc.stop()


@pytest.mark.parametrize("tenths", [0, 0xFFFF])
def test_on_with_timed_off_without_a_time_is_an_on(tenths):
    proc, device, mcu = own()
    try:
        assert cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, tenths)) == \
            [(DP_ON[1], 1)]
    finally:
        proc.stop()


def test_a_short_on_with_timed_off_is_malformed():
    proc, device, mcu = own()
    try:
        device.call_zigbee_cmd(1, ON_OFF, CMD_ON_WITH_TIMED_OFF, bytes([0, 10]),
                               expected_result="MALFORMED_COMMAND")
        mcu.serve()
        assert mcu.writes == []
    finally:
        proc.stop()


def test_the_countdown_attribute_is_written_while_on_only():
    proc, device, mcu = own()
    try:
        cmd(device, mcu, 2, ON_OFF, CMD_ON)
        assert write(device, mcu, 2, ON_OFF, ATTR_COUNTDOWN, 90) == [(DP_COUNTDOWN[2], 90)]
        assert write(device, mcu, 2, ON_OFF, ATTR_COUNTDOWN, 100000) == [(DP_COUNTDOWN[2], 86400)]
        assert attr(device, 2, ON_OFF, ATTR_COUNTDOWN) == 86400
        assert write(device, mcu, 2, ON_OFF, ATTR_COUNTDOWN, 0) == [(DP_COUNTDOWN[2], 0)]
        cmd(device, mcu, 2, ON_OFF, CMD_OFF)
        assert write(device, mcu, 2, ON_OFF, ATTR_COUNTDOWN, 60) == []     # off: nothing to count
        assert attr(device, 2, ON_OFF, ATTR_COUNTDOWN) == 0
    finally:
        proc.stop()


@pytest.mark.parametrize("how", ["zigbee", "wall switch", "its end"])
def test_any_off_ends_the_countdown(how):
    proc, device, mcu = own()
    try:
        cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 300))
        assert attr(device, 1, ON_OFF, ATTR_COUNTDOWN) == 30
        if how == "zigbee":
            assert cmd(device, mcu, 1, ON_OFF, CMD_OFF) == [(DP_ON[1], 0)]
        else:                       # the wall switch, or the countdown's end: a switch off
            mcu.ch[0]["on"], mcu.ch[0]["countdown"] = 0, 0
            mcu.send([mcu._report([mcu._dp(DP_ON[1])], passive=False)])
        assert attr(device, 1, ON_OFF, ATTR_ONOFF) == 0
        assert attr(device, 1, ON_OFF, ATTR_COUNTDOWN) == 0
    finally:
        proc.stop()


def test_a_shorter_on_with_timed_off_leaves_the_longer_countdown():
    """ZCL: while on, the longer of the time left and the new on time wins."""
    proc, device, mcu = own()
    try:
        assert cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 300)) == \
            [(DP_ON[1], 1), (DP_COUNTDOWN[1], 30)]
        device.step_time(10_000)                                  # 20 s left
        assert cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 150)) == \
            [(DP_ON[1], 1)]                                        # 15 s: the 20 s stay
        assert attr(device, 1, ON_OFF, ATTR_COUNTDOWN) == 30
        assert cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 250)) == \
            [(DP_ON[1], 1), (DP_COUNTDOWN[1], 25)]                 # 25 s: longer
        device.step_time(20_000)                                  # 5 s left
        assert cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 100)) == \
            [(DP_ON[1], 1), (DP_COUNTDOWN[1], 10)]
    finally:
        proc.stop()


def test_a_countdown_the_mcu_reports_ended_is_gone():
    """2.2.0 reports DP 6 / 12 = 0 when its countdown runs out (with DP 1 = 0)."""
    proc, device, mcu = own()
    try:
        cmd(device, mcu, 2, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 600))
        mcu.ch[1]["countdown"] = 0
        mcu.send([mcu._report([mcu._dp(DP_COUNTDOWN[2])], passive=False)])
        assert attr(device, 2, ON_OFF, ATTR_COUNTDOWN) == 0
        assert cmd(device, mcu, 2, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 50)) == \
            [(DP_ON[2], 1), (DP_COUNTDOWN[2], 5)]                  # nothing left to keep
    finally:
        proc.stop()


def test_a_bogus_countdown_from_the_mcu_is_its_longest():
    """A report of DP 6 above 86400 s (never sent by Open Dimmer) is taken as 86400 s: no
    wrap of the module's own end time, so the longer time still wins."""
    proc, device, mcu = own()
    try:
        cmd(device, mcu, 1, ON_OFF, CMD_ON)
        mcu.send([mcu._report([value_dp(DP_COUNTDOWN[1], 0xFFFFFFFF)], passive=False)])
        assert attr(device, 1, ON_OFF, ATTR_COUNTDOWN) == 86400
        assert cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 1000)) == \
            [(DP_ON[1], 1)]                                        # 100 s < the day left
    finally:
        proc.stop()


def mcu_restarts(device, mcu, down_ms: int = 0) -> list[tuple[int, int]]:
    """The MCU restarts alone (a watchdog reset, an update), silent for that long: its light
    comes back as it was, its countdown and identify do not. Returns the module's writes."""
    mcu.writes.clear()
    mcu.restart()
    device.step_time(down_ms)
    mcu.query_network()
    mcu.serve(1000)
    return list(mcu.writes)


def test_an_mcu_restart_ends_the_identify_and_the_countdown_is_written_again():
    """The MCU comes back with the light on and no countdown: it would stay on for good. The
    module writes what is left of the countdown again."""
    proc, device, mcu = own()
    try:
        cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 600))
        cmd(device, mcu, 2, IDENTIFY, CMD_IDENTIFY, (100).to_bytes(2, "little"))
        assert attr(device, 1, ON_OFF, ATTR_COUNTDOWN) == 60
        device.step_time(20000)
        device.clear_events()
        w = mcu_restarts(device, mcu)
        again = [v for dp, v in w if dp == DP_COUNTDOWN[1]]
        assert len(again) == 1 and 38 <= again[0] <= 40            # the 40 s left, its start in it
        assert [dp for dp, _ in w if dp in (DP_ON[1], DP_ON[2], DP_COUNTDOWN[2])] == []
        assert attr(device, 1, ON_OFF, ATTR_COUNTDOWN) == again[0] == mcu.ch[0]["countdown"]
        assert attr(device, 1, ON_OFF, ATTR_ONOFF) == 1
        assert attr(device, 2, IDENTIFY, ATTR_IDENTIFY_TIME) == 0
        changed = {(int(e.payload["ep"]), int(e.payload["cluster"], 16), int(e.payload["attr"], 16))
                   for e in device._events if e.kind == "zcl_attr_change"}
        assert (1, ON_OFF, ATTR_COUNTDOWN) in changed                  # both reported
        assert (2, IDENTIFY, ATTR_IDENTIFY_TIME) in changed
        assert [dp for dp, _ in mcu_restarts(device, mcu, 3000) if dp == DP_COUNTDOWN[1]] == \
            [DP_COUNTDOWN[1]]                                          # and at the next restart
    finally:
        proc.stop()


def test_a_countdown_that_ended_while_the_mcu_restarted_switches_the_light_off():
    proc, device, mcu = own()
    try:
        cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 50))        # 5 s
        cmd(device, mcu, 2, ON_OFF, CMD_ON)
        w = mcu_restarts(device, mcu, 6000)
        assert [(dp, v) for dp, v in w if dp in (*DP_ON.values(), *DP_COUNTDOWN.values())] == \
            [(DP_ON[1], 0)]
        assert attr(device, 1, ON_OFF, ATTR_ONOFF) == 0 == mcu.ch[0]["on"]
        assert attr(device, 1, ON_OFF, ATTR_COUNTDOWN) == 0
        assert attr(device, 2, ON_OFF, ATTR_ONOFF) == 1                # no countdown: left alone
    finally:
        proc.stop()


def test_an_mcu_that_restarts_with_the_light_off_gets_no_countdown():
    """A restart that leaves the channel off (a power cut with the power-on state off): the
    countdown ended with the light, nothing is written."""
    proc, device, mcu = own()
    try:
        cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 600))
        mcu.ch[0]["on"] = 0
        w = mcu_restarts(device, mcu)
        assert [dp for dp, _ in w if dp in (DP_ON[1], DP_COUNTDOWN[1])] == []
        assert attr(device, 1, ON_OFF, ATTR_ONOFF) == 0
        assert attr(device, 1, ON_OFF, ATTR_COUNTDOWN) == 0
        cmd(device, mcu, 1, ON_OFF, CMD_ON)                            # a later on: no countdown
        assert [dp for dp, _ in mcu_restarts(device, mcu) if dp == DP_COUNTDOWN[1]] == []
        assert attr(device, 1, ON_OFF, ATTR_COUNTDOWN) == 0
    finally:
        proc.stop()


# ---- the dump of 2.2.0 -----------------------------------------------------------

def settings(mcu):
    mcu.ch[0].update(input=1, power_level=0, boost=300)
    mcu.ch[1].update(input=2, power_level=600, boost=0)


def test_the_dump_of_2_2_0_fills_the_settings():
    proc, device, mcu = own(prepare=settings)
    try:
        assert [attr(device, ep, SWITCH_CFG, ATTR_INPUT_MODE) for ep in (1, 2)] == [1, 2]
        assert [attr(device, ep, LEVEL, ATTR_STARTUP_LEVEL) for ep in (1, 2)] == [
            PREVIOUS, scaled_to_zcl(600, MCU_MIN, 1000)]
        assert [attr(device, ep, BALLAST, ATTR_BOOST) for ep in (1, 2)] == [300, 0]
        assert [attr(device, ep, ON_OFF, ATTR_COUNTDOWN) for ep in (1, 2)] == [0, 0]
    finally:
        proc.stop()


def test_a_power_cut_level_below_the_minimum_is_minimum():
    """0x00 (minimum) is written as the lowest value, kept by the MCU as 12; it reads back
    as minimum, not as level 1. A min raised later still makes it the minimum."""
    proc, device, mcu = own(prepare=lambda m: m.ch[0].update(power_level=12))
    try:
        assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == MINIMUM
    finally:
        proc.stop()


# ---- wall switch mode, power-cut level, start boost ------------------------------

def test_the_wall_switch_mode_is_written_per_channel():
    proc, device, mcu = own(prepare=settings)
    try:
        assert write(device, mcu, 1, SWITCH_CFG, ATTR_INPUT_MODE, 0) == [(DP_INPUT[1], 0)]
        assert write(device, mcu, 2, SWITCH_CFG, ATTR_INPUT_MODE, 1) == [(DP_INPUT[2], 1)]
        assert [mcu.ch[0]["input"], mcu.ch[1]["input"]] == [0, 1]
        assert write(device, mcu, 2, SWITCH_CFG, ATTR_INPUT_MODE, 3) == []     # no such mode
        assert attr(device, 2, SWITCH_CFG, ATTR_INPUT_MODE) == 1
    finally:
        proc.stop()


def test_the_level_after_a_power_cut_is_written_scaled():
    proc, device, mcu = own()
    try:
        assert write(device, mcu, 1, LEVEL, ATTR_STARTUP_LEVEL, PREVIOUS) == [(DP_POWER_LEVEL[1], 0)]
        assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == PREVIOUS
        assert write(device, mcu, 1, LEVEL, ATTR_STARTUP_LEVEL, MINIMUM) == [(DP_POWER_LEVEL[1], 10)]
        assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == MINIMUM
        value = scaled_to_tuya(128, MCU_MIN, 1000)
        assert write(device, mcu, 2, LEVEL, ATTR_STARTUP_LEVEL, 128) == [(DP_POWER_LEVEL[2], value)]
        assert mcu.ch[1]["power_level"] == (value + 2) // 4 * 4        # kept in steps of 4
        assert attr(device, 2, LEVEL, ATTR_STARTUP_LEVEL) == 128         # read back as written
    finally:
        proc.stop()


@pytest.mark.parametrize("written", [MINIMUM, 1, 2, PREVIOUS, 200])
def test_the_level_after_a_power_cut_reads_back_as_written(written):
    """With the absolute minimum (10) minimum and level 1 are one MCU value (12): it reads back
    as written while the MCU keeps that value; a change made otherwise is read from the value."""
    proc, device, mcu = own(prepare=lambda m: m.ch[0].update(min=10))
    try:
        write(device, mcu, 1, LEVEL, ATTR_STARTUP_LEVEL, written)
        assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == written
        mcu_report(proc, value_dp(3, 10), cmd=TUYA_CMD_REPORT_PASSIVE)       # the dump again
        mcu.send(mcu.dump())
        assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == written
        mcu.ch[0]["power_level"] = 600                                     # changed otherwise
        mcu.send([mcu._report([mcu._dp(DP_POWER_LEVEL[1])])])
        assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == scaled_to_zcl(600, 10, 1000)
    finally:
        proc.stop()


def test_the_level_after_a_power_cut_follows_a_new_range():
    proc, device, mcu = own(prepare=lambda m: m.ch[0].update(power_level=600))
    try:
        assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == scaled_to_zcl(600, MCU_MIN, 1000)
        mcu_report(proc, value_dp(3, 400), cmd=TUYA_CMD_REPORT_PASSIVE)         # min 400
        assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == scaled_to_zcl(600, 400, 1000)
    finally:
        proc.stop()


def test_the_start_boost_is_written_in_ms():
    proc, device, mcu = own()
    try:
        assert write(device, mcu, 1, BALLAST, ATTR_BOOST, 300) == [(DP_BOOST[1], 300)]
        assert write(device, mcu, 2, BALLAST, ATTR_BOOST, 275) == [(DP_BOOST[2], 275)]
        assert attr(device, 2, BALLAST, ATTR_BOOST) == 250                 # what the MCU kept
        assert write(device, mcu, 1, BALLAST, ATTR_BOOST, 900) == [(DP_BOOST[1], 750)]
        assert attr(device, 1, BALLAST, ATTR_BOOST) == 750
    finally:
        proc.stop()


def test_the_fast_press_is_read_from_the_dump_and_written_per_channel():
    proc, device, mcu = own(prepare=lambda m: m.ch[1].update(fast_press=1))
    try:
        assert [attr(device, ep, SWITCH_CFG, ATTR_FAST_PRESS) for ep in (1, 2)] == [0, 1]
        assert write(device, mcu, 1, SWITCH_CFG, ATTR_FAST_PRESS, 1) == [(DP_FAST_PRESS[1], 1)]
        assert write(device, mcu, 2, SWITCH_CFG, ATTR_FAST_PRESS, 0) == [(DP_FAST_PRESS[2], 0)]
        assert [mcu.ch[0]["fast_press"], mcu.ch[1]["fast_press"]] == [1, 0]
        assert [attr(device, ep, SWITCH_CFG, ATTR_FAST_PRESS) for ep in (1, 2)] == [1, 0]
    finally:
        proc.stop()


def test_the_fast_press_gesture_is_a_multistate_value_of_its_own():
    """Gesture 6 (press, at once) on DP 105 / 106: presentValue sequence << 4 | 6, each one a
    change; 7 is none."""
    from tuya_mcu import enum_dp
    proc, device, mcu = own()
    try:
        assert int(device.read_zigbee_attr(1, 0x0012, 0x004A)) == 7            # NumberOfStates
        mcu_report(proc, enum_dp(105, 6))
        first = int(device.read_zigbee_attr(1, 0x0012, 0x0055))
        mcu_report(proc, enum_dp(105, 6))
        second = int(device.read_zigbee_attr(1, 0x0012, 0x0055))
        assert first & 0x0F == 6 and second & 0x0F == 6 and first != second
        mcu_report(proc, enum_dp(106, 7))
        assert int(device.read_zigbee_attr(2, 0x0012, 0x0055)) == 0
    finally:
        proc.stop()


# ---- identify ----------------------------------------------------------------------

def test_identify_blinks_the_channel_and_counts_down():
    proc, device, mcu = own()
    try:
        assert cmd(device, mcu, 1, IDENTIFY, CMD_IDENTIFY, (10).to_bytes(2, "little")) == \
            [(DP_IDENTIFY[1], 10)]
        assert attr(device, 1, IDENTIFY, ATTR_IDENTIFY_TIME) == 10
        for _ in range(3):
            device.step_time(1000)
        assert attr(device, 1, IDENTIFY, ATTR_IDENTIFY_TIME) == 7
        for _ in range(8):
            device.step_time(1000)
        assert attr(device, 1, IDENTIFY, ATTR_IDENTIFY_TIME) == 0
        mcu.serve()
        assert mcu.writes == []                     # the MCU ends it by itself
    finally:
        proc.stop()


def test_identify_is_capped_at_255_s_and_0_stops_it():
    proc, device, mcu = own()
    try:
        assert cmd(device, mcu, 2, IDENTIFY, CMD_IDENTIFY, (600).to_bytes(2, "little")) == \
            [(DP_IDENTIFY[2], 255)]
        assert attr(device, 2, IDENTIFY, ATTR_IDENTIFY_TIME) == 255
        assert cmd(device, mcu, 2, IDENTIFY, CMD_IDENTIFY, bytes(2)) == [(DP_IDENTIFY[2], 0)]
        assert attr(device, 2, IDENTIFY, ATTR_IDENTIFY_TIME) == 0
        assert write(device, mcu, 1, IDENTIFY, ATTR_IDENTIFY_TIME, 5) == [(DP_IDENTIFY[1], 5)]
        device.step_time(1000)
        assert attr(device, 1, IDENTIFY, ATTR_IDENTIFY_TIME) == 4
    finally:
        proc.stop()


# ---- any other MCU -------------------------------------------------------------------

def no_writes(proc, device) -> list:
    pump(proc)
    out = dp_writes(take_tx(proc))
    device.step_time(100)
    pump(proc)
    return out + dp_writes(take_tx(proc))


NONE_VALUES = [(ON_OFF, ATTR_COUNTDOWN, 0), (SWITCH_CFG, ATTR_INPUT_MODE, 0),
               (SWITCH_CFG, ATTR_FAST_PRESS, 0),
               (LEVEL, ATTR_STARTUP_LEVEL, PREVIOUS), (BALLAST, ATTR_BOOST, 0),
               (IDENTIFY, ATTR_IDENTIFY_TIME, 0)]


@pytest.mark.parametrize("build", [400, None])
def test_an_older_open_dimmer_gets_none_of_it(build):
    proc, device, mcu = own(build=build)
    try:
        w = cmd(device, mcu, 1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 100))
        assert w == [(DP_ON[1], 1)]
        for cluster, attr_id, none in NONE_VALUES:
            for value in (1, 2):
                assert write(device, mcu, 1, cluster, attr_id, value) == [], hex(attr_id)
            assert attr(device, 1, cluster, attr_id) == none, hex(attr_id)
        assert cmd(device, mcu, 2, IDENTIFY, CMD_IDENTIFY, (5).to_bytes(2, "little")) == []
        assert attr(device, 2, IDENTIFY, ATTR_IDENTIFY_TIME) == 0
    finally:
        proc.stop()


def test_a_factory_mcu_gets_an_on_for_an_on_with_timed_off_and_nothing_else():
    proc, device = start()                          # never identified
    try:
        device.call_zigbee_cmd(1, ON_OFF, CMD_ON_WITH_TIMED_OFF, timed_off(0, 100))
        assert ack_writes(proc, device) == [(DP_ON[1], 1)]
        for cluster, attr_id, none in NONE_VALUES:
            device.write_zigbee_attr(2, cluster, attr_id, 1)
            assert no_writes(proc, device) == [], hex(attr_id)
            assert attr(device, 2, cluster, attr_id) == none, hex(attr_id)
        device.call_zigbee_cmd(2, IDENTIFY, CMD_IDENTIFY, (5).to_bytes(2, "little"))
        assert no_writes(proc, device) == []
        mcu_report(proc, value_dp(DP_INPUT[1], 1), cmd=TUYA_CMD_REPORT_PASSIVE)
        mcu_report(proc, value_dp(DP_BOOST[1], 300), cmd=TUYA_CMD_REPORT_PASSIVE)
        mcu_report(proc, value_dp(DP_COUNTDOWN[1], 30), cmd=TUYA_CMD_REPORT_PASSIVE)
        assert attr(device, 1, SWITCH_CFG, ATTR_INPUT_MODE) == 0       # not its DPs
        assert attr(device, 1, BALLAST, ATTR_BOOST) == 0
        assert attr(device, 1, ON_OFF, ATTR_COUNTDOWN) == 0
        mcu_report(proc, bool_dp(DP_ON[1], 0))
    finally:
        proc.stop()


def test_the_dump_of_an_older_open_dimmer_has_none_of_the_new_dps():
    """The model as the firmware: before build 500 its dump ends with the fade and hold
    settings (the module reads only what is there)."""
    proc, device, mcu = own(build=400, prepare=settings)
    try:
        frames = mcu.dump()
        assert len(frames) == 5
        assert sum(len(f) // 2 for f in frames) == 234
        assert [attr(device, ep, SWITCH_CFG, ATTR_INPUT_MODE) for ep in (1, 2)] == [0, 0]
    finally:
        proc.stop()


def test_the_dump_of_2_2_0_is_one_burst_of_298_bytes():
    proc, device, mcu = own()
    try:
        frames = mcu.dump()
        assert sum(len(f) // 2 for f in frames) == 298
        dps = set()
        for f in frames:
            data = bytes.fromhex(f)[8:-1]
            while data:
                dps.add(data[0])
                data = data[4 + int.from_bytes(data[2:4], "big"):]
        assert dps & NEW_DPS == {6, 12, 162, 163, 164, 165, 168, 169, 170, 171}   # identify: never
    finally:
        proc.stop()


def test_the_lamp_compensation_is_read_from_the_dump_and_written_in_levels():
    """Open Dimmer 2.2.8: DP 172 / 173 in value units of 1000 (steps of 4); the attribute in
    levels like the min and max (0..63)."""
    proc, device, mcu = own(build=LAMP_COMP_BUILD, prepare=lambda m: m.ch[1].update(lamp_comp=32))
    try:
        assert [attr(device, ep, BALLAST, ATTR_LAMP_COMP) for ep in (1, 2)] == [0, 8]
        assert write(device, mcu, 1, BALLAST, ATTR_LAMP_COMP, 8) == [(DP_LAMP_COMP[1], 31)]
        assert mcu.ch[0]["lamp_comp"] == 32
        assert attr(device, 1, BALLAST, ATTR_LAMP_COMP) == 8            # what the MCU kept
        assert write(device, mcu, 2, BALLAST, ATTR_LAMP_COMP, 70) == [(DP_LAMP_COMP[2], 248)]
        assert attr(device, 2, BALLAST, ATTR_LAMP_COMP) == 63
        assert write(device, mcu, 2, BALLAST, ATTR_LAMP_COMP, 0) == [(DP_LAMP_COMP[2], 0)]
        assert attr(device, 2, BALLAST, ATTR_LAMP_COMP) == 0
    finally:
        proc.stop()


@pytest.mark.parametrize("build", [FEATURES_BUILD, LAMP_COMP_BUILD - 1])
def test_an_open_dimmer_without_the_lamp_compensation_gets_no_write(build):
    """Its dump has no DP 172 / 173: the attribute stays "none" (0xFF) and a write of it sends
    nothing and sets it back."""
    proc, device, mcu = own(build=build)
    try:
        assert attr(device, 1, BALLAST, ATTR_LAMP_COMP) == 0xFF
        assert write(device, mcu, 1, BALLAST, ATTR_LAMP_COMP, 8) == []
        assert attr(device, 1, BALLAST, ATTR_LAMP_COMP) == 0xFF
    finally:
        proc.stop()


# ---- the wall switch options (2.2.8) -------------------------------------------
ATTR_DARK_HOLD = 0xFF15
ATTR_DOUBLE_FULL = 0xFF16
DP_SWITCH_OPTS = 180


def test_the_switch_options_are_read_from_the_dump_and_written_by_their_bit():
    """Open Dimmer 2.2.8: DP 180, both channels' options, bits set = off (0 / 1 the long press
    from off's rise, 2 / 3 the double tap to full), written as mask << 8 | bits; per channel
    genOnOffSwitchCfg 0xff15 (rise) and 0xff16 (double tap), 1 = on."""
    proc, device, mcu = own(build=LAMP_COMP_BUILD, prepare=lambda m: m.ch[1].update(dark_hold=0))
    try:
        assert [attr(device, ep, SWITCH_CFG, ATTR_DARK_HOLD) for ep in (1, 2)] == [1, 0]
        assert [attr(device, ep, SWITCH_CFG, ATTR_DOUBLE_FULL) for ep in (1, 2)] == [1, 1]
        assert write(device, mcu, 1, SWITCH_CFG, ATTR_DARK_HOLD, 0) == [(DP_SWITCH_OPTS, 0x0101)]
        assert mcu.ch[0]["dark_hold"] == 0 and mcu.ch[1]["dark_hold"] == 0
        assert attr(device, 1, SWITCH_CFG, ATTR_DARK_HOLD) == 0
        assert write(device, mcu, 2, SWITCH_CFG, ATTR_DOUBLE_FULL, 0) == [(DP_SWITCH_OPTS, 0x0808)]
        assert mcu.ch[1]["double_full"] == 0 and mcu.ch[0]["double_full"] == 1
        assert attr(device, 2, SWITCH_CFG, ATTR_DOUBLE_FULL) == 0
        assert attr(device, 1, SWITCH_CFG, ATTR_DOUBLE_FULL) == 1        # its own bit only
        assert write(device, mcu, 2, SWITCH_CFG, ATTR_DARK_HOLD, 1) == [(DP_SWITCH_OPTS, 0x0200)]
        assert attr(device, 2, SWITCH_CFG, ATTR_DARK_HOLD) == 1
    finally:
        proc.stop()


@pytest.mark.parametrize("build", [FEATURES_BUILD, LAMP_COMP_BUILD - 1])
def test_an_open_dimmer_without_the_switch_options_gets_no_write(build):
    """Its dump has no DP 180: the attributes stay "none" (0xFF) and a write of one sends nothing
    and sets it back."""
    proc, device, mcu = own(build=build)
    try:
        for a in (ATTR_DARK_HOLD, ATTR_DOUBLE_FULL):
            assert attr(device, 1, SWITCH_CFG, a) == 0xFF
            assert write(device, mcu, 1, SWITCH_CFG, a, 0) == []
            assert attr(device, 1, SWITCH_CFG, a) == 0xFF
    finally:
        proc.stop()


# ---- custom curve (2.2.8) -------------------------------------------------------
CURVE = [0, 100, 250, 450, 700, 1000, 1400, 1900, 2500, 3200, 4000, 4900, 5900, 7000, 8100, 9100,
         10000]


def le_hex(points) -> str:
    return "".join(f"{p & 0xFF:02x}{p >> 8:02x}" for p in points)


def curve_attr(device, ep: int) -> str:
    return device.read_zigbee_attr(ep, BALLAST, ATTR_CUSTOM_CURVE).lower()


def test_the_custom_curve_is_written_in_three_parts_and_shown_as_the_mcu_has_it():
    """lightingBallastCfg 0xff12: 17 little-endian points; to the MCU as DPs 174 + 3 * channel
    + part, 6 points each as they are (the last part's sixth: 0); shown as its answers tell. A
    falling table or a wrong length is refused: the MCU's one is shown again, nothing sent."""
    proc, device, mcu = own(build=LAMP_COMP_BUILD)
    try:
        assert curve_attr(device, 2) == "-"                 # none (the stub shows an empty one so)
        out = write(device, mcu, 2, BALLAST, ATTR_CUSTOM_CURVE, le_hex(CURVE))
        assert [d for d, _ in out] == [177, 178, 179]
        assert mcu.custom[1] == CURVE and mcu.custom_ok(1) and not mcu.custom_ok(0)
        assert curve_attr(device, 2) == le_hex(CURVE)
        bad = CURVE[:5] + [50] + CURVE[6:]
        assert write(device, mcu, 2, BALLAST, ATTR_CUSTOM_CURVE, le_hex(bad)) == []
        assert write(device, mcu, 2, BALLAST, ATTR_CUSTOM_CURVE, le_hex(CURVE[:16])) == []
        assert write(device, mcu, 2, BALLAST, ATTR_CUSTOM_CURVE, le_hex(CURVE[:16] + [9999])) == []
        assert curve_attr(device, 2) == le_hex(CURVE)
        # none: the MCU's table made invalid (it goes back to led_soft)
        out = write(device, mcu, 2, BALLAST, ATTR_CUSTOM_CURVE, "-")
        assert [d for d, _ in out] == [179] and not mcu.custom_ok(1)
        assert curve_attr(device, 2) == "-"
    finally:
        proc.stop()


def test_the_mcu_keeps_its_custom_curve_and_its_dump_shows_it():
    """Saved on the MCU: after its restart and after the module's, nothing is written again;
    the dump fills the attribute."""
    proc, device, mcu = own(build=LAMP_COMP_BUILD)
    try:
        write(device, mcu, 1, BALLAST, ATTR_CUSTOM_CURVE, le_hex(CURVE))
        out = mcu_restarts(device, mcu, 300)
        assert [d for d, _ in out if 174 <= d < 180] == []
        assert curve_attr(device, 1) == le_hex(CURVE)
    finally:
        proc.stop()
    proc, device, mcu = own(build=LAMP_COMP_BUILD, prepare=lambda m: m.custom.__setitem__(0, list(CURVE)))
    try:
        assert curve_attr(device, 1) == le_hex(CURVE) and curve_attr(device, 2) == "-"
    finally:
        proc.stop()


@pytest.mark.parametrize("build", [FEATURES_BUILD, LAMP_COMP_BUILD - 1])
def test_an_open_dimmer_without_the_custom_curve_gets_none(build):
    proc, device, mcu = own(build=build)
    try:
        assert write(device, mcu, 1, BALLAST, ATTR_CUSTOM_CURVE, le_hex(CURVE)) == []
        assert curve_attr(device, 1) == "-"                 # it has none
    finally:
        proc.stop()
