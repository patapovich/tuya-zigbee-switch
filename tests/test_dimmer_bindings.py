"""The wall switch's bindings (DIMMER_BINDINGS, src/zigbee/dimmer_cluster.c): each dimmer's wall
switch has an endpoint of its own after all others (3 / 4 on the ZDMS16-2, Dimmer Switch, On/Off
and Level Control clients), and lights bound to it follow it. Normal mode (any MCU): the wall
switch's on / off as On / Off, nothing for our commands, their answers, a query's answer, the
first report after a boot or the countdown's end. Detached mode (Open Dimmer): single / press
Toggle, hold MoveWithOnOff up and Move down by turns, release StopWithOnOff, double
MoveToLevelWithOnOff(254, 0.4 s), triple nothing. Off mode: nothing. Not joined: nothing."""
import pytest
from own_mcu import FEATURES_BUILD, own_mcu_contact
from test_dimmer_mcu import DP_ON_L1, DP_ON_L2, ack_writes, known_off, start
from tuya_mcu import TUYA_CMD_REPORT_PASSIVE, bool_dp, enum_dp, mcu_report, take_tx

ON_OFF, LEVEL = 0x0006, 0x0008
OFF, ON, TOGGLE = 0x00, 0x01, 0x02
MOVE, MOVE_TO_LEVEL_WITH_ONOFF, MOVE_WITH_ONOFF, STOP_WITH_ONOFF = 0x01, 0x04, 0x05, 0x07
ON_WITH_TIMED_OFF = 0x42
SINGLE, DOUBLE, TRIPLE, HOLD, RELEASE, PRESS = 1, 2, 3, 4, 5, 6
DP_ACTION = {1: 105, 2: 106}
DP_ON = {1: 1, 2: 7}


def sent(device, eps=(3, 4)) -> list[tuple]:
    """The commands to bindings from the wall switches' endpoints so far (and forget them)."""
    out = [(e.ep, e.cluster, e.cmd, e.data) for e in device.zcl_list_cmds() if e.ep in eps]
    device._events = [e for e in device._events if e.kind != "zcl_cmd_send"]
    return out


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
