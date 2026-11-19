"""Transition time (ZCL, 1/10 s) to Open Dimmer: from build 400 the module writes its DP 160 / 161
(the ms of the change that follows) right before the on / off / level writes; an older Open
Dimmer, a 2.1.x app without a build and a factory MCU get nothing more than before.
0xFFFF (none given; the converter sends it when Zigbee2MQTT has no transition) is the fade
settings, 0 is at once (DP 1 ms), X tenths X * 100 ms."""
import pytest

from own_mcu import own_mcu_contact
from test_dimmer_mcu import (
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CMD_LEVEL_STEP_WITH_ON_OFF,
    ZCL_LEVEL_MOVE_DOWN,
    ack_writes,
    start,
)
from tuya_mcu import take_tx

DP_ON_L1, DP_LEVEL_L1, DP_ON_L2 = 1, 2, 7
DP_TRANSITION_L1, DP_TRANSITION_L2 = 160, 161


def tt(tenths: int) -> bytes:
    return tenths.to_bytes(2, "little")


def to_level(device, level: int, tenths: int, ep: int = 1, with_onoff: bool = True) -> None:
    cmd = ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF if with_onoff else ZCL_CMD_LEVEL_MOVE_TO_LEVEL
    device.call_zigbee_cmd(ep, ZCL_CLUSTER_LEVEL_CONTROL, cmd, bytes([level]) + tt(tenths))


def contact(build):
    proc, device = start()
    own_mcu_contact(proc, device, build=build)
    take_tx(proc)
    return proc, device


def dps(writes):
    return [dp for dp, _ in writes]


def test_on_at_a_level_with_a_transition_sends_the_time_first():
    proc, device = contact(400)
    try:
        to_level(device, 200, 25)                       # 2.5 s
        w = ack_writes(proc, device)
        assert w[0] == (DP_TRANSITION_L1, 2500)
        assert dps(w)[1:3] == [DP_ON_L1, DP_LEVEL_L1]
        device.step_time(2000)
        to_level(device, 0, 10)                          # off in 1 s
        w = ack_writes(proc, device)
        assert w[:2] == [(DP_TRANSITION_L1, 1000), (DP_ON_L1, 0)]
    finally:
        proc.stop()


def test_channel_two_has_its_own_dp():
    proc, device = contact(400)
    try:
        to_level(device, 120, 5, ep=2)
        w = ack_writes(proc, device)
        assert w[0] == (DP_TRANSITION_L2, 500)
        assert DP_ON_L2 in dps(w) and DP_TRANSITION_L1 not in dps(w)
    finally:
        proc.stop()


@pytest.mark.parametrize("build", [300, None])
def test_an_open_dimmer_before_build_400_gets_no_time(build):
    proc, device = contact(build)
    try:
        to_level(device, 200, 25)
        w = ack_writes(proc, device)
        assert DP_TRANSITION_L1 not in dps(w) and DP_ON_L1 in dps(w)
    finally:
        proc.stop()


def test_a_factory_mcu_gets_no_time():
    proc, device = start()                               # never identified
    try:
        to_level(device, 200, 25)
        w = ack_writes(proc, device)
        assert DP_TRANSITION_L1 not in dps(w) and DP_ON_L1 in dps(w)
    finally:
        proc.stop()


NONE = 0xFFFF   # no time given: the fade settings


def test_no_transition_sends_nothing_unless_an_earlier_time_could_still_apply():
    proc, device = contact(400)
    try:
        to_level(device, 200, NONE)                      # Zigbee2MQTT without a transition
        assert DP_TRANSITION_L1 not in dps(ack_writes(proc, device))
        to_level(device, 150, 30)
        ack_writes(proc, device)
        device.step_time(500)
        to_level(device, 180, NONE)                      # within 1.5 s: the 3 s must not apply
        w = ack_writes(proc, device)
        assert w[0] == (DP_TRANSITION_L1, 0)
        device.step_time(200)
        to_level(device, 190, NONE)                      # cancelled already: nothing
        assert DP_TRANSITION_L1 not in dps(ack_writes(proc, device))
        to_level(device, 150, 30)
        ack_writes(proc, device)
        device.step_time(1600)
        to_level(device, 200, NONE)                      # long after: nothing
        assert DP_TRANSITION_L1 not in dps(ack_writes(proc, device))
    finally:
        proc.stop()


@pytest.mark.parametrize("with_onoff", [True, False])
def test_a_transition_of_0_is_at_once(with_onoff):
    """An explicit 0: DP 1 ms (the MCU's 0 is the fade settings), every time."""
    proc, device = contact(400)
    try:
        to_level(device, 200, NONE)
        ack_writes(proc, device)
        device.step_time(2000)
        for lvl in (90, 140):
            to_level(device, lvl, 0, with_onoff=with_onoff)
            w = ack_writes(proc, device)
            assert w[0] == (DP_TRANSITION_L1, 1) and DP_LEVEL_L1 in dps(w)
            device.step_time(2000)
        to_level(device, 0, 0)                           # off at once
        assert ack_writes(proc, device)[:2] == [(DP_TRANSITION_L1, 1), (DP_ON_L1, 0)]
    finally:
        proc.stop()


def test_step_and_level_without_onoff_take_it_and_long_times_are_capped():
    proc, device = contact(400)
    try:
        to_level(device, 200, 0)
        ack_writes(proc, device)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_STEP_WITH_ON_OFF,
                               bytes([ZCL_LEVEL_MOVE_DOWN, 50]) + tt(15))
        w = ack_writes(proc, device)
        assert w[0] == (DP_TRANSITION_L1, 1500) and DP_LEVEL_L1 in dps(w)
        device.step_time(2000)
        to_level(device, 100, 1000, with_onoff=False)    # 100 s: capped to 65.535 s
        w = ack_writes(proc, device)
        assert w[0] == (DP_TRANSITION_L1, 0xFFFF) and DP_LEVEL_L1 in dps(w)
    finally:
        proc.stop()


def test_a_level_change_while_off_sends_nothing():
    proc, device = contact(400)
    try:
        to_level(device, 0, 0)                           # off
        ack_writes(proc, device)
        device.step_time(2000)
        to_level(device, 120, 20, with_onoff=False)      # ZCL: no effect while off
        assert ack_writes(proc, device) == []
    finally:
        proc.stop()


ONOFF_CLUSTER, ONOFF_OFF, ONOFF_ON, ONOFF_TOGGLE = 0x0006, 0x00, 0x01, 0x02


@pytest.mark.parametrize("cmd", [ONOFF_OFF, ONOFF_TOGGLE])
def test_an_onoff_command_right_after_a_timed_one_takes_the_time_back(cmd):
    proc, device = contact(400)
    try:
        to_level(device, 200, 30)                        # on in 3 s
        ack_writes(proc, device)
        device.step_time(500)
        device.call_zigbee_cmd(1, ONOFF_CLUSTER, cmd, b"")
        w = ack_writes(proc, device)
        assert w[:2] == [(DP_TRANSITION_L1, 0), (DP_ON_L1, 0)]
        device.step_time(2000)
        device.call_zigbee_cmd(1, ONOFF_CLUSTER, ONOFF_ON, b"")   # long after: nothing more
        w = ack_writes(proc, device)
        assert DP_TRANSITION_L1 not in dps(w) and DP_ON_L1 in dps(w)
    finally:
        proc.stop()


@pytest.mark.parametrize("after_ms, sends_zero", [(1400, True), (1500, False)])
def test_the_take_back_window_is_1500_ms(after_ms, sends_zero):
    proc, device = contact(400)
    try:
        to_level(device, 200, 30)
        ack_writes(proc, device)                         # the acks take no stub time
        device.step_time(after_ms)
        to_level(device, 220, NONE)
        w = ack_writes(proc, device)
        assert ((DP_TRANSITION_L1, 0) in w) == sends_zero
    finally:
        proc.stop()


# The three kinds of time to every class of MCU: Open Dimmer 2.2 (build 500) and 2.1.14 (400) get
# DP 160; an older Open Dimmer (300, a 2.1.x app without a build) and a factory MCU nothing more.
@pytest.mark.parametrize("build", [500, 400, 300, None, "stock"])
@pytest.mark.parametrize("tenths, dp160", [(NONE, None), (0, 1), (12, 1200)])
def test_none_zero_and_a_time_to_every_mcu(build, tenths, dp160):
    if build == "stock":
        proc, device = start()                           # never identified
    else:
        proc, device = contact(build)
    try:
        to_level(device, 200, tenths)
        w = ack_writes(proc, device)
        timed = build not in (300, None, "stock")
        if timed and dp160 is not None:
            assert w[0] == (DP_TRANSITION_L1, dp160)
            assert dps(w)[1:3] == [DP_ON_L1, DP_LEVEL_L1]
        else:
            assert dps(w)[:2] == [DP_ON_L1, DP_LEVEL_L1]   # what it always got
    finally:
        proc.stop()


def test_a_factory_mcu_gets_the_same_frames_for_none_zero_and_a_time():
    """The stock MCU's UART traffic does not depend on the time at all."""
    frames = []
    for tenths in (NONE, 0, 12):
        proc, device = start()
        try:
            to_level(device, 200, tenths)
            frames.append(take_tx(proc))
        finally:
            proc.stop()
    assert frames[0] == frames[1] == frames[2] and frames[0]
