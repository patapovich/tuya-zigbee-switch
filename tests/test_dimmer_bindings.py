"""The wall switch's bindings (DIMMER_BINDINGS, src/zigbee/dimmer_cluster.c): each dimmer's wall
switch has an endpoint of its own after all others (3 / 4 on the ZDMS16-2, Dimmer Switch, On/Off
and Level Control clients), and lights bound to it follow it. Normal mode (any MCU): the wall
switch's on / off as On / Off; its hold (the MCU reports the level on the way) as one Move
(direction from the first report, from the second right after the wall switch's on; rate
measured between the first two, again if the direction turns), at its end (Open Dimmer's
release, else 0.8 s without a report) StopWithOnOff + MoveToLevelWithOnOff(level, 0.4 s);
nothing for our commands, their answers, a query's answer, the first report after a boot or
the countdown's end. Detached mode (Open Dimmer): single /
press Toggle, hold MoveWithOnOff up and Move down by turns, release StopWithOnOff, double
MoveToLevelWithOnOff(254, 0.4 s), triple nothing. Off mode: nothing. Not joined: nothing.
On / Off / Toggle / MoveToLevel ask for an APS ack, Move / Stop not."""
# A level report every 0.5 s through a hold, ~80 of 1000 a report: a house unit's factory MCU.
import pytest
from own_mcu import FEATURES_BUILD, own_mcu_contact
from test_dimmer_mcu import (DP_LEVEL_L1, DP_ON_L1, DP_ON_L2, ack_writes, known_off, level, start,
                             tuya_to_zcl)

STOP = (3, 0x0008, 0x07, b"")
from tuya_mcu import TUYA_CMD_REPORT_PASSIVE, bool_dp, enum_dp, mcu_report, take_tx, value_dp

ON_OFF, LEVEL = 0x0006, 0x0008
OFF, ON, TOGGLE = 0x00, 0x01, 0x02
MOVE, MOVE_TO_LEVEL_WITH_ONOFF, MOVE_WITH_ONOFF, STOP_WITH_ONOFF = 0x01, 0x04, 0x05, 0x07
ON_WITH_TIMED_OFF = 0x42
SINGLE, DOUBLE, TRIPLE, HOLD, RELEASE, PRESS = 1, 2, 3, 4, 5, 6
DP_ACTION = {1: 105, 2: 106}
DP_ON = {1: 1, 2: 7}


def flush(device) -> None:
    """A task due at the end of the last step runs after that step's answer: one more command
    (no time) brings its output in."""
    device.read_zigbee_attr(1, ON_OFF, 0)


def sent(device, eps=(3, 4)) -> list[tuple]:
    """The commands to bindings from the wall switches' endpoints so far (and forget them)."""
    flush(device)
    out = [(e.ep, e.cluster, e.cmd, e.data) for e in device.zcl_list_cmds() if e.ep in eps]
    device._events = [e for e in device._events if e.kind != "zcl_cmd_send"]
    return out


def acks(device, eps=(3, 4)) -> list[tuple]:
    """(command, APS ack asked) of the commands to bindings so far (kept)."""
    flush(device)
    return [(int(e.payload["cmd"], 16), int(e.payload["ack"])) for e in device._events
            if e.kind == "zcl_cmd_send" and int(e.payload["ep"]) in eps]


def settle(proc, device) -> None:
    device.step_time(50)
    ack_writes(proc, device)


@pytest.fixture
def stock():
    proc, device = start()
    device.set_network(1)
    yield proc, device
    proc.stop()


def test_the_wall_switch_mirrors_on_and_off_to_its_bindings(stock):
    proc, device = stock
    known_off(proc)
    mcu_report(proc, bool_dp(DP_ON_L2, 0))
    settle(proc, device)
    sent(device)
    mcu_report(proc, bool_dp(DP_ON_L1, 1))
    settle(proc, device)
    assert sent(device) == [(3, ON_OFF, ON, b"")]
    mcu_report(proc, bool_dp(DP_ON_L1, 0))
    settle(proc, device)
    assert sent(device) == [(3, ON_OFF, OFF, b"")]
    mcu_report(proc, bool_dp(DP_ON_L2, 1))
    settle(proc, device)
    assert sent(device) == [(4, ON_OFF, ON, b"")]


def test_our_commands_answers_and_the_first_report_are_not_mirrored(stock):
    proc, device = stock
    mcu_report(proc, bool_dp(DP_ON_L1, 1))                     # first report after boot
    settle(proc, device)
    device.call_zigbee_cmd(1, ON_OFF, OFF)
    settle(proc, device)
    device.call_zigbee_cmd(1, ON_OFF, ON)
    settle(proc, device)
    mcu_report(proc, bool_dp(DP_ON_L1, 0), cmd=TUYA_CMD_REPORT_PASSIVE)   # a query's answer
    settle(proc, device)
    assert sent(device) == []


def test_nothing_goes_out_when_not_joined(stock):
    proc, device = stock
    device.set_network(0)
    known_off(proc)
    mcu_report(proc, bool_dp(DP_ON_L1, 1))
    settle(proc, device)
    assert sent(device) == []


def od(mode: int = 1):
    """Open Dimmer 2.2.0, channel 1 in that wall switch mode, joined."""
    proc, device = start()
    mcu = own_mcu_contact(proc, device, build=FEATURES_BUILD,
                          prepare=lambda m: m.ch[0].update(input=mode))
    take_tx(proc)
    device.set_network(1)
    sent(device)
    return proc, device, mcu


def gesture(proc, device, g: int, ch: int = 1) -> list[tuple]:
    mcu_report(proc, enum_dp(DP_ACTION[ch], g))
    device.step_time(50)
    return sent(device)


def test_detached_gestures_drive_the_bound_lights():
    proc, device, mcu = od()
    try:
        assert gesture(proc, device, SINGLE) == [(3, ON_OFF, TOGGLE, b"")]
        assert gesture(proc, device, PRESS) == [(3, ON_OFF, TOGGLE, b"")]
        assert gesture(proc, device, HOLD) == [(3, LEVEL, MOVE_WITH_ONOFF, bytes([0, 50]))]
        assert gesture(proc, device, RELEASE) == [(3, LEVEL, STOP_WITH_ONOFF, b"")]
        assert gesture(proc, device, HOLD) == [(3, LEVEL, MOVE, bytes([1, 50]))]    # by turns
        assert gesture(proc, device, RELEASE) == [(3, LEVEL, STOP_WITH_ONOFF, b"")]
        assert gesture(proc, device, HOLD) == [(3, LEVEL, MOVE_WITH_ONOFF, bytes([0, 50]))]
        gesture(proc, device, RELEASE)
        gesture(proc, device, SINGLE)                                  # starts again at up
        assert gesture(proc, device, HOLD) == [(3, LEVEL, MOVE_WITH_ONOFF, bytes([0, 50]))]
        gesture(proc, device, RELEASE)
        assert gesture(proc, device, DOUBLE) == [(3, LEVEL, MOVE_TO_LEVEL_WITH_ONOFF,
                                                   bytes([254, 4, 0]))]
        assert gesture(proc, device, TRIPLE) == []
        assert gesture(proc, device, SINGLE, ch=2) == []               # channel 2: normal mode
    finally:
        proc.stop()


def test_a_hold_takes_the_default_move_rate():
    proc, device, mcu = od()
    try:
        device.write_zigbee_attr(1, LEVEL, 0x0014, 80)
        assert gesture(proc, device, HOLD) == [(3, LEVEL, MOVE_WITH_ONOFF, bytes([0, 80]))]
    finally:
        proc.stop()


@pytest.mark.parametrize("mode", [0, 2])
def test_no_gestures_out_in_the_normal_and_off_modes(mode):
    proc, device, mcu = od(mode)
    try:
        for g in (SINGLE, HOLD, RELEASE, DOUBLE, PRESS):
            assert gesture(proc, device, g) == []
    finally:
        proc.stop()


def wall(mcu, on: int, ch: int = 1) -> None:
    mcu.ch[ch - 1]["on"] = on
    if not on:
        mcu.ch[ch - 1]["countdown"] = 0
    mcu.send([mcu._report([mcu._dp(DP_ON[ch])], passive=False)])
    mcu.serve()


def test_open_dimmer_normal_mode_mirrors_but_not_the_countdowns_end():
    proc, device, mcu = od(0)
    try:
        wall(mcu, 1)
        assert sent(device) == [(3, ON_OFF, ON, b"")]
        wall(mcu, 0)
        assert sent(device) == [(3, ON_OFF, OFF, b"")]
        device.call_zigbee_cmd(1, ON_OFF, ON_WITH_TIMED_OFF, bytes([0]) + (300).to_bytes(2, "little")
                               + bytes(2))
        mcu.serve()
        device.step_time(31_000)
        wall(mcu, 0)                                                   # the countdown's end
        assert sent(device) == []
        device.call_zigbee_cmd(1, ON_OFF, ON_WITH_TIMED_OFF, bytes([0]) + (300).to_bytes(2, "little")
                               + bytes(2))
        mcu.serve()
        wall(mcu, 0)                                                   # the wall switch, earlier
        assert sent(device) == [(3, ON_OFF, OFF, b"")]
    finally:
        proc.stop()


def report(proc, device, value: int, after_ms: int = 500) -> list[tuple]:
    device.step_time(after_ms)
    mcu_report(proc, value_dp(DP_LEVEL_L1, value))
    return sent(device)


def lit_at(proc, device, value: int) -> None:
    """On by the wall switch a while ago, at that level (a query's answer: no hold)."""
    known_off(proc)
    mcu_report(proc, bool_dp(DP_ON_L1, 1))
    settle(proc, device)
    mcu_report(proc, value_dp(DP_LEVEL_L1, value), cmd=TUYA_CMD_REPORT_PASSIVE)
    device.step_time(2500)
    sent(device)


def end_of(level_value: int) -> list[tuple]:
    return [STOP, (3, LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([tuya_to_zcl(level_value), 4, 0]))]


def test_a_wall_switch_hold_goes_out_as_one_move(stock):
    proc, device = stock
    lit_at(proc, device, 200)
    assert report(proc, device, 300, 0) == [(3, LEVEL, MOVE_WITH_ONOFF, bytes([0, 50]))]
    assert report(proc, device, 500) == [(3, LEVEL, MOVE_WITH_ONOFF, bytes([0, 102]))]  # measured
    assert report(proc, device, 700) == []
    assert report(proc, device, 500) == [(3, LEVEL, MOVE, bytes([1, 102]))]   # it turned
    assert report(proc, device, 300) == []
    device.step_time(700)
    assert sent(device) == []
    device.step_time(100)                                      # 0.8 s without a report
    assert acks(device) == [(STOP_WITH_ONOFF, 0), (MOVE_TO_LEVEL_WITH_ONOFF, 1)]
    assert sent(device) == end_of(300)
    assert report(proc, device, 400, 2000) == [(3, LEVEL, MOVE_WITH_ONOFF, bytes([0, 102]))]
    # the next hold: the last one's rate


def test_a_hold_right_after_the_wall_switchs_on_sets_out_from_its_first_report(stock):
    """The level known before is the one before the off: a hold from dark rises from the
    minimum, so the first report only gives where it set out."""
    proc, device = stock
    known_off(proc)
    mcu_report(proc, value_dp(DP_LEVEL_L1, 800), cmd=TUYA_CMD_REPORT_PASSIVE)
    mcu_report(proc, bool_dp(DP_ON_L1, 1))
    settle(proc, device)
    assert sent(device) == [(3, ON_OFF, ON, b"")]
    assert report(proc, device, 300, 900) == []
    assert report(proc, device, 500) == [(3, LEVEL, MOVE_WITH_ONOFF, bytes([0, 102]))]
    device.step_time(800)
    assert sent(device) == end_of(500)


def test_a_single_level_change_moves_and_ends(stock):
    """An MCU that reports a hold's level only at its end (Open Dimmer before 2.2.4)."""
    proc, device = stock
    lit_at(proc, device, 200)
    assert report(proc, device, 600, 0) == [(3, LEVEL, MOVE_WITH_ONOFF, bytes([0, 50]))]
    device.step_time(800)
    assert sent(device) == end_of(600)


def test_levels_not_by_the_wall_switch_are_not_followed(stock):
    proc, device = stock
    lit_at(proc, device, 200)
    mcu_report(proc, value_dp(DP_LEVEL_L1, 700), cmd=TUYA_CMD_REPORT_PASSIVE)   # a query's answer
    device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([100, 0, 0]))   # ours
    for _ in range(4):
        settle(proc, device)                                   # the MCU answers its writes
    assert report(proc, device, 300, 100) == []                # the MCU's fade to it
    device.step_time(3500)
    assert sent(device) == [] and ack_writes(proc, device) == []
    assert report(proc, device, 500, 0) != []                  # a hold, then off under it
    mcu_report(proc, bool_dp(DP_ON_L1, 0))
    assert sent(device) == [(3, ON_OFF, OFF, b"")]
    device.step_time(1000)
    assert sent(device) == []                                  # no Stop after the Off
    assert report(proc, device, 800) == []                     # while off


@pytest.mark.parametrize("mode", [0, 1])
def test_open_dimmer_hold_followed_in_the_normal_mode_its_release_ends_it(mode):
    proc, device, mcu = od(mode)
    try:
        wall(mcu, 1)
        device.step_time(2500)
        sent(device)
        out = []
        for v in (520, 440):                                   # a hold down, every 0.5 s
            mcu.ch[0]["level"] = v
            mcu.send([mcu._report([mcu._dp(2)], passive=False)])
            mcu.serve()
            out += sent(device)
            device.step_time(500)
        mcu_report(proc, enum_dp(DP_ACTION[1], RELEASE))       # at once, no 0.8 s
        device.step_time(50)
        out += sent(device)
        want = [(3, LEVEL, MOVE, bytes([1, 50])), None,
                STOP, (3, LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([level(device), 4, 0]))]
        if mode == 0:
            assert out[0] == want[0] and out[-2:] == want[2:]
            assert len(out) in (3, 4)                          # a second Move if the rate differs
        else:
            assert out == [STOP]                               # detached: its release's own Stop
    finally:
        proc.stop()


def test_aps_acks_for_switching_not_for_moving():
    proc, device, mcu = od()
    try:
        for g in (SINGLE, HOLD, RELEASE, DOUBLE):
            mcu_report(proc, enum_dp(DP_ACTION[1], g))
            device.step_time(50)
        assert acks(device) == [(TOGGLE, 1), (MOVE_WITH_ONOFF, 0), (STOP_WITH_ONOFF, 0),
                                (MOVE_TO_LEVEL_WITH_ONOFF, 1)]
    finally:
        proc.stop()
