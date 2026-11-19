from tests.client import StubProc
from tests.conftest import Device
from tests.zcl_consts import (
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF,
    ZCL_CMD_LEVEL_STOP_WITH_ON_OFF,
)

DIMMER_CFG = "A;B;DM2;P00O01L02S04M03X05;P01O07L08S0AM09X0B;U0E;M;"

# Ramp: a step every 300 ms (the pace the MCU always got), as many levels as the
# command's rate (levels/s) makes in that time: 10/s = 3 per step, the default
# (0xFF) 40/s = 12 per step (the wall switch's speed).
RAMP_PERIOD_MS = 300
RAMP_STEP = 3


def test_move_up_ramps_by_step_and_stop_freezes():
    with StubProc(device_config=DIMMER_CFG) as proc:
        dev = Device(proc)
        dev.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF,
                            bytes([0x00, 0x0A]))
        for _ in range(5):
            dev.step_time(RAMP_PERIOD_MS + 10)
        lvl1 = int(dev.read_zigbee_attr(1, ZCL_CLUSTER_LEVEL_CONTROL, 0x0000))
        # Off + MoveWithOnOff up: ZCL starts the move at the minimum level (1).
        expected = 1 + 5 * RAMP_STEP
        expect = expected if expected <= 254 else 254
        assert lvl1 == expect, f"expected ~{expect} after 5 steps, got {lvl1}"

        dev.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_STOP_WITH_ON_OFF)
        for _ in range(3):
            dev.step_time(RAMP_PERIOD_MS + 10)
        lvl2 = int(dev.read_zigbee_attr(1, ZCL_CLUSTER_LEVEL_CONTROL, 0x0000))
        assert lvl1 == lvl2, f"level moved after stop: {lvl1} -> {lvl2}"


# ---- the command's rate, DefaultMoveRate, Open Dimmer's transition per step ------------

import pytest  # noqa: E402

from tests.own_mcu import own_mcu_contact  # noqa: E402
from tests.test_dimmer_mcu import ack_writes, set_level, start  # noqa: E402

LEVEL = ZCL_CLUSTER_LEVEL_CONTROL
ATTR_CURRENT_LEVEL, ATTR_DEFAULT_MOVE_RATE = 0x0000, 0x0014
MOVE, MOVE_WITH_ONOFF = 0x01, ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF
UP, DOWN = 0x00, 0x01
DP_LEVEL_L1, DP_TRANSITION_L1 = 2, 160


def level(dev, ep: int = 1) -> int:
    return int(dev.read_zigbee_attr(ep, LEVEL, ATTR_CURRENT_LEVEL))


def ramp_from_off(rate: int, steps: int) -> int:
    with StubProc(device_config=DIMMER_CFG) as proc:
        dev = Device(proc)
        dev.call_zigbee_cmd(1, LEVEL, MOVE_WITH_ONOFF, bytes([UP, rate]))
        for _ in range(steps):
            dev.step_time(RAMP_PERIOD_MS + 10)
        return level(dev)


@pytest.mark.parametrize("rate, steps, expected", [
    (0xFF, 5, 1 + 5 * 12),          # the default: 40/s, the wall switch's speed
    (40, 5, 1 + 5 * 12),
    (100, 3, 1 + 3 * 30),
    (254, 3, 1 + 3 * 76),
    (1, 10, 1 + 3),                 # 0.3 levels per step: a level every 3 or 4 steps
])
def test_the_ramp_follows_the_commands_rate(rate, steps, expected):
    assert ramp_from_off(rate, steps) == expected


def test_a_move_at_rate_0_has_no_effect():
    with StubProc(device_config=DIMMER_CFG) as proc:
        dev = Device(proc)
        dev.call_zigbee_cmd(1, LEVEL, MOVE_WITH_ONOFF, bytes([UP, 0]))
        for _ in range(3):
            dev.step_time(RAMP_PERIOD_MS + 10)
        assert int(dev.read_zigbee_attr(1, 0x0006, 0x0000)) == 0
        assert level(dev) == 0


def test_default_move_rate_sets_the_rate_of_0xff_and_survives_a_restart():
    with StubProc(device_config=DIMMER_CFG) as proc:
        dev = Device(proc)
        assert int(dev.read_zigbee_attr(1, LEVEL, ATTR_DEFAULT_MOVE_RATE)) == 0xFF   # none
        dev.write_zigbee_attr(1, LEVEL, ATTR_DEFAULT_MOVE_RATE, 80)
    with StubProc(device_config=DIMMER_CFG) as proc:
        dev = Device(proc)
        assert int(dev.read_zigbee_attr(1, LEVEL, ATTR_DEFAULT_MOVE_RATE)) == 80
        assert int(dev.read_zigbee_attr(2, LEVEL, ATTR_DEFAULT_MOVE_RATE)) == 0xFF   # per channel
        dev.call_zigbee_cmd(1, LEVEL, MOVE_WITH_ONOFF, bytes([UP, 0xFF]))
        for _ in range(3):
            dev.step_time(RAMP_PERIOD_MS + 10)
        assert level(dev) == 1 + 3 * 24
        dev.call_zigbee_cmd(1, LEVEL, ZCL_CMD_LEVEL_STOP_WITH_ON_OFF)
        dev.call_zigbee_cmd(2, LEVEL, MOVE_WITH_ONOFF, bytes([UP, 0xFF]))
        for _ in range(3):
            dev.step_time(RAMP_PERIOD_MS + 10)
        assert level(dev, 2) == 1 + 3 * 12


def ramp_writes(proc, device, steps: int) -> list[list[tuple[int, int]]]:
    """The DP writes of each ramp step (each acked as the MCU does)."""
    out = []
    for _ in range(steps):
        device.step_time(RAMP_PERIOD_MS)
        out.append(ack_writes(proc, device))
    return out


def test_a_factory_mcu_gets_the_frames_it_always_got_only_the_step_differs():
    """Stock MCU: one level write per 300 ms step whatever the rate, nothing more."""
    proc, device = start()
    try:
        set_level(proc, device, 30)
        device.call_zigbee_cmd(1, LEVEL, MOVE, bytes([UP, 100]))
        steps = ramp_writes(proc, device, 4)
        assert [[dp for dp, _ in s] for s in steps] == [[DP_LEVEL_L1]] * 4
        assert level(device) == 30 + 4 * 30
    finally:
        proc.stop()


@pytest.mark.parametrize("build, timed", [(400, True), (300, False), (None, False)])
def test_open_dimmer_takes_each_step_in_the_step_period(build, timed):
    """From build 400 each step is written with a transition time of the step period (DP 160),
    so the MCU moves evenly instead of fading per step."""
    proc, device = start()
    try:
        mcu = own_mcu_contact(proc, device, build=build)
        device.call_zigbee_cmd(1, LEVEL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([60, 0, 0]))
        mcu.serve(4000)
        mcu.writes.clear()
        device.call_zigbee_cmd(1, LEVEL, MOVE, bytes([UP, 0xFF]))
        for _ in range(3):
            device.step_time(RAMP_PERIOD_MS)
            mcu.serve()
        writes = [w for w in mcu.writes if w[0] in (DP_LEVEL_L1, DP_TRANSITION_L1)]
        if timed:
            assert [dp for dp, _ in writes] == [DP_TRANSITION_L1, DP_LEVEL_L1] * 3
            assert all(v == 300 for dp, v in writes if dp == DP_TRANSITION_L1)
        else:
            assert [dp for dp, _ in writes] == [DP_LEVEL_L1] * 3
        assert level(device) == 60 + 3 * 12
    finally:
        proc.stop()
