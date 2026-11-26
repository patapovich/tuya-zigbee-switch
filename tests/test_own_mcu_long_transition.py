"""The long transitions of tests/test_hk_dimmer_long_transitions.py to Open Dimmer on the ZDMS16-2
(2.2.8, the build this firmware carries): what the module writes for Adaptive Lighting's
MoveToLevelWithOnOff to a lit channel, also right after a wall switch hold (its gestures and level
reports). The time first (DP 160, ms; at most 65535, the DP's range: a longer time is 65.535 s
there), then the on (DP 1 also to a lit channel: Open Dimmer's dimmer.c leaves a lit channel's light
as it is at an on) and the level; nothing else, nor later (no level a hold left in the module).
CurrentLevel is the end at once, and a Stop writes the level where Open Dimmer's move is then (its
time capped as the DP has it)."""
import pytest

from own_mcu import LAMP_COMP_BUILD, own_mcu_contact
from test_dimmer_mcu import ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CLUSTER_ON_OFF, level, scaled_to_tuya, start
from tuya_mcu import TUYA_TYPE_ENUM, dp_payload, take_tx

from tests.zcl_consts import (
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CMD_LEVEL_STOP,
    ZCL_CMD_ONOFF_ON,
)

DP_ON_L1, DP_LEVEL_L1, DP_ACTION_L1, DP_TRANSITION_L1 = 1, 2, 105, 160
HOLD, RELEASE = 4, 5
OD_MIN, OD_MAX = 200, 1000                 # OwnMcu's range
TIMES = (200, 600, 655, 660, 1200, 6000)   # tenths: 20, 60, 65.5, 66, 120 and 600 s
TARGET = 168


def move_z2m(device, lvl: int, tenths: int, with_onoff: bool = True) -> None:
    cmd = ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF if with_onoff else ZCL_CMD_LEVEL_MOVE_TO_LEVEL
    device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL, cmd,
                           bytes([lvl]) + tenths.to_bytes(2, "little") + b"\x00\x00")


def od_ms(tenths: int) -> int:
    """The time as Open Dimmer gets it (DP 160)."""
    return min(tenths * 100, 0xFFFF)


@pytest.fixture
def od():
    """Open Dimmer 2.2.8, L1 lit at 49 a while ago."""
    proc, device = start()
    mcu = own_mcu_contact(proc, device, build=LAMP_COMP_BUILD)
    take_tx(proc)
    move_z2m(device, 49, 0)
    mcu.serve(5000)
    mcu.writes.clear()
    yield proc, device, mcu
    proc.stop()


def report(mcu, units: list[bytes]) -> None:
    """An active report of the MCU's (its wall switch)."""
    mcu.send([mcu._report(units, passive=False)])
    mcu.serve()


def hold_down(mcu, to: int, steps: int = 4) -> None:
    """Its wall switch held down from where the light is to `to`: the hold, a level report every
    0.5 s, the release (the level, then the gesture), as Open Dimmer reports them."""
    c = mcu.ch[0]
    report(mcu, [dp_payload(DP_ACTION_L1, TUYA_TYPE_ENUM, bytes([HOLD]))])
    start_level = c["level"]
    for i in range(1, steps + 1):
        mcu.serve(500)
        c["level"] = start_level + (to - start_level) * i // steps
        report(mcu, [mcu._dp(DP_LEVEL_L1)])
    report(mcu, [mcu._dp(DP_ON_L1), mcu._dp(DP_LEVEL_L1)])
    report(mcu, [dp_payload(DP_ACTION_L1, TUYA_TYPE_ENUM, bytes([RELEASE]))])


def sends_the_time_the_on_and_the_level(proc, device, mcu, tenths: int) -> None:
    frm = level(device)
    mcu.writes.clear()
    move_z2m(device, TARGET, tenths)
    mcu.serve()
    assert mcu.writes == [(DP_TRANSITION_L1, od_ms(tenths)), (DP_ON_L1, 1),
                          (DP_LEVEL_L1, scaled_to_tuya(TARGET, OD_MIN, OD_MAX))]
    assert level(device) == TARGET                               # the end, at once
    mcu.serve(10000)
    assert mcu.writes[3:] == [] and level(device) == TARGET      # nothing more, nor later
    device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_STOP, b"")
    mcu.serve()
    expect = frm + (TARGET - frm) * 10000 / od_ms(tenths)
    assert mcu.writes[3:5] == [(DP_TRANSITION_L1, 1),
                               (DP_LEVEL_L1, scaled_to_tuya(level(device), OD_MIN, OD_MAX))]
    assert abs(level(device) - expect) <= 2, (frm, level(device), expect)


@pytest.mark.parametrize("tenths", TIMES)
def test_a_long_transition_to_a_lit_channel(od, tenths):
    sends_the_time_the_on_and_the_level(*od, tenths)


@pytest.mark.parametrize("tenths", TIMES)
def test_a_long_transition_right_after_a_hold(od, tenths):
    """0.6 s after the release of a hold down from 168: as without the hold."""
    proc, device, mcu = od
    move_z2m(device, TARGET, 0)                  # lit higher first, then a hold down
    mcu.serve(5000)
    hold_down(mcu, 400)
    assert 40 < level(device) < 100
    mcu.serve(600)
    sends_the_time_the_on_and_the_level(proc, device, mcu, tenths)


def test_an_on_then_a_level_with_a_long_time_to_a_lit_channel(od):
    proc, device, mcu = od
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    mcu.serve(200)
    mcu.writes.clear()
    move_z2m(device, TARGET, 1200, with_onoff=False)
    mcu.serve()
    assert mcu.writes == [(DP_TRANSITION_L1, 0xFFFF),
                          (DP_LEVEL_L1, scaled_to_tuya(TARGET, OD_MIN, OD_MAX))]
    assert level(device) == TARGET
