"""A channel following another channel (DIMMER_BINDINGS, src/zigbee/dimmer_cluster.c,
genOnOffSwitchCfg 0xff17..0xff19 of the follower, kept in NV): at the leader's wall switch on the
follower goes to its follow on level (default 254), at its off or its countdown's end to its
follow off level (0, the default: off; above 0 also from off), inside the module, after the
leader's own writes. Not our commands, their answers, a query's answer, the first report after a
boot. The follower's change is ours: it never goes on to its own bindings or followers (no loop
when two channels follow each other). Settings out of range (or the channel itself) are their
defaults; a setting written again unchanged is not written to NV again."""
import pytest
from own_mcu import FEATURES_BUILD
from test_dimmer_bindings import ON, ON_OFF, OFF, ON_WITH_TIMED_OFF, od, sent, settle
from test_dimmer_bindings import wall as od_wall
from test_dimmer_mcu import (DP_ON_L1, DP_ON_L2, NV_DIR, ack_writes, known_off, level, onoff, pump,
                             start, take_tx)
from tuya_mcu import TUYA_CMD_REPORT_PASSIVE, bool_dp, mcu_report, parse_frames

SWITCH_CFG = 0x0007
FOLLOW, FOLLOW_ON, FOLLOW_OFF = 0xFF17, 0xFF18, 0xFF19


def cfg(device, ch: int = 2) -> list[int]:
    return [int(device.read_zigbee_attr(ch, SWITCH_CFG, a)) for a in (FOLLOW, FOLLOW_ON, FOLLOW_OFF)]


def setup(device, follow: int = 1, on: int = 254, off: int = 0, ch: int = 2) -> None:
    device.write_zigbee_attr(ch, SWITCH_CFG, FOLLOW, follow)
    device.write_zigbee_attr(ch, SWITCH_CFG, FOLLOW_ON, on)
    device.write_zigbee_attr(ch, SWITCH_CFG, FOLLOW_OFF, off)


@pytest.fixture
def unit():
    proc, device = start()
    device.set_network(1)
    known_off(proc)
    mcu_report(proc, bool_dp(DP_ON_L2, 0))
    settle(proc, device)
    yield proc, device
    proc.stop()


def wall(proc, device, on: int, dp: int = DP_ON_L1) -> None:
    mcu_report(proc, bool_dp(dp, on))
    settle(proc, device)
    ack_writes(proc, device)


def test_the_defaults_follow_nothing(unit):
    proc, device = unit
    assert cfg(device) == [0, 254, 0] == cfg(device, 1)
    wall(proc, device, 1)
    assert onoff(device, 2) == 0


def test_the_follower_goes_on_and_off_with_the_leaders_wall_switch(unit):
    proc, device = unit
    setup(device)
    wall(proc, device, 1)
    assert (onoff(device, 2), level(device, 2)) == (1, 254)
    wall(proc, device, 0)
    assert onoff(device, 2) == 0


def test_the_follow_levels(unit):
    proc, device = unit
    setup(device, on=200, off=40)
    wall(proc, device, 1)
    assert (onoff(device, 2), level(device, 2)) == (1, 200)
    wall(proc, device, 0)
    assert (onoff(device, 2), level(device, 2)) == (1, 40)      # stays dim
    device.write_zigbee_attr(2, SWITCH_CFG, FOLLOW_OFF, 0)
    wall(proc, device, 1)
    wall(proc, device, 0)
    assert onoff(device, 2) == 0


def test_our_commands_and_a_querys_answer_are_not_followed(unit):
    proc, device = unit
    setup(device)
    device.call_zigbee_cmd(1, ON_OFF, ON)
    settle(proc, device)
    ack_writes(proc, device)
    assert onoff(device, 2) == 0
    device.call_zigbee_cmd(1, ON_OFF, OFF)
    settle(proc, device)
    ack_writes(proc, device)
    wall(proc, device, 1)                                       # L2 follows: on
    assert onoff(device, 2) == 1
    mcu_report(proc, bool_dp(DP_ON_L1, 0), cmd=TUYA_CMD_REPORT_PASSIVE)   # a query's answer
    settle(proc, device)
    assert onoff(device, 2) == 1


def test_the_first_report_after_a_boot_is_not_followed():
    proc, device = start()
    try:
        device.set_network(1)
        setup(device)
        mcu_report(proc, bool_dp(DP_ON_L2, 0))
        settle(proc, device)
        wall(proc, device, 1)                                   # L1's first report: on
        assert onoff(device, 2) == 0
    finally:
        proc.stop()


def test_the_leaders_own_writes_go_out_before_the_followers(unit):
    """The leader's OnLevel at its wall switch on is written first (one frame in flight; a
    factory MCU answers a level write only after its fade)."""
    proc, device = unit
    setup(device)
    device.write_zigbee_attr(1, 0x0008, 0x0011, 100)           # L1 OnLevel
    mcu_report(proc, bool_dp(DP_ON_L1, 1))
    pump(proc)
    first = [data for cmd, data in parse_frames(take_tx(proc)) if cmd == 0x04]   # DP writes
    assert first and first[0][0] == 2, first                   # L1's level DP, not L2's DP 7


def test_the_followers_change_does_not_go_out_to_its_bindings(unit):
    proc, device = unit
    setup(device)
    sent(device)
    wall(proc, device, 1)
    assert sent(device) == [(3, ON_OFF, ON, b"")]               # the leader's mirror only


def test_the_settings_check_and_survive_a_restart():
    proc, device = start()
    try:
        setup(device, follow=2, on=0, off=255)                 # itself, out of range: defaults
        assert cfg(device) == [0, 254, 0]
        setup(device, follow=9)
        assert cfg(device)[0] == 0
        setup(device, follow=1, on=120, off=30)
        setup(device, follow=2, on=90, off=10, ch=1)
        assert cfg(device) == [1, 120, 30]
    finally:
        proc.stop()
    proc, device = start()
    try:
        assert cfg(device) == [1, 120, 30]
        assert cfg(device, 1) == [2, 90, 10]
    finally:
        proc.stop()


def test_two_channels_following_each_other_do_not_loop(unit):
    proc, device = unit
    setup(device)                                               # L2 follows L1
    setup(device, follow=2, ch=1)                              # and L1 follows L2
    wall(proc, device, 1)
    assert onoff(device, 2) == 1
    for _ in range(5):
        settle(proc, device)
        ack_writes(proc, device)
    assert (onoff(device, 1), onoff(device, 2)) == (1, 1)
    wall(proc, device, 0, dp=DP_ON_L2)                         # L2's wall switch off: L1 follows
    assert (onoff(device, 1), onoff(device, 2)) == (0, 0)


def test_an_off_level_brings_an_off_follower_on(unit):
    """A night light: an off level above 0 also switches a follower on that was off."""
    proc, device = unit
    setup(device, off=40)
    wall(proc, device, 1)
    device.call_zigbee_cmd(2, ON_OFF, OFF)                     # the hub switched the follower off
    settle(proc, device)
    ack_writes(proc, device)
    wall(proc, device, 0)
    assert (onoff(device, 2), level(device, 2)) == (1, 40)


def test_an_unchanged_setting_is_not_written_again():
    item = NV_DIR / f"item_{70:02x}.bin"                        # NV_ITEM_DIMMER_FOLLOW(1)
    proc, device = start()
    try:
        device.write_zigbee_attr(2, SWITCH_CFG, FOLLOW_OFF, 40)
        assert item.read_bytes() == bytes([0, 254, 40])
        item.unlink()
        device.write_zigbee_attr(2, SWITCH_CFG, FOLLOW_OFF, 40)
        assert not item.exists(), "the same follow setting was written to NV again"
        device.write_zigbee_attr(2, SWITCH_CFG, FOLLOW_OFF, 0)
        assert item.read_bytes() == bytes([0, 254, 0])
    finally:
        proc.stop()


def test_open_dimmer_wall_switch_and_countdown_end_are_followed():
    proc, device, mcu = od(0)
    try:
        setup(device)
        od_wall(mcu, 1)
        assert mcu.ch[1]["on"] == 1                             # L2 on in the MCU too
        assert onoff(device, 2) == 1
        od_wall(mcu, 0)
        assert onoff(device, 2) == 0 and mcu.ch[1]["on"] == 0
        device.call_zigbee_cmd(1, ON_OFF, ON_WITH_TIMED_OFF, bytes([0]) + (300).to_bytes(2, "little")
                               + bytes(2))
        mcu.serve()
        device.write_zigbee_attr(2, SWITCH_CFG, FOLLOW_OFF, 30)  # L2 lit by the off level below
        device.step_time(31_000)
        od_wall(mcu, 0)                                         # L1's countdown ends
        mcu.serve()
        assert onoff(device, 2) == 1 and level(device, 2) == 30
    finally:
        proc.stop()

