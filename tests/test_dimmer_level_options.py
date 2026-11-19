"""ZTU-side level settings of a dimmer, for any MCU:

- ExecuteIfOff (Level Control Options 0x000F bit 0, and the OptionsMask / OptionsOverride of
  a command): a MoveToLevel / Move / Step without OnOff while off sets the level the light
  comes on at and leaves it off. Open Dimmer keeps a level written while off, so it gets that
  write; a stock MCU would forget it, so it gets nothing and the module writes the level at
  the next on. Options 0 (the default): ignored while off, as before.
- The wall switch's own OnLevel (Level Control 0xff11): with Open Dimmer 2.2.0 a wall switch
  on goes there, a network On to OnLevel; 0xFF (the default): OnLevel for both, as before.
  Any other MCU gets what it got before, whatever is set.
Both are kept in NV like OnLevel."""
import pytest
from own_mcu import own_mcu_contact
from test_dimmer_mcu import (
    DP_LEVEL_L1,
    DP_ON_L1,
    ack_writes,
    dump_range,
    known_off,
    level,
    onoff,
    scaled_to_tuya,
    set_on_level,
    start,
)
from tuya_mcu import bool_dp, mcu_report, pump, take_tx

LEVEL, ON_OFF = 0x0008, 0x0006
ATTR_OPTIONS, ATTR_ON_LEVEL_LOCAL = 0x000F, 0xFF11
MOVE_TO_LEVEL, MOVE, STEP = 0x00, 0x01, 0x02
MOVE_TO_LEVEL_WITH_ONOFF = 0x04
CMD_OFF, CMD_ON = 0x00, 0x01
UP, DOWN = 0x00, 0x01
MN, MX = 276, 591                 # the MCU's range (dump)


def stock_off(options: int = 0):
    proc, device = start()
    dump_range(proc, MN, MX)
    known_off(proc)
    if options:
        device.write_zigbee_attr(1, LEVEL, ATTR_OPTIONS, options)
    return proc, device


def writes_after(proc, device) -> list[tuple[int, int]]:
    return ack_writes(proc, device)


def to_level(device, lvl: int, *options: int) -> None:
    device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL, bytes([lvl, 0, 0, *options]))


# ---- ExecuteIfOff, stock MCU ------------------------------------------------------------

def test_options_default_to_0_and_a_level_while_off_is_ignored():
    proc, device = stock_off()
    try:
        assert int(device.read_zigbee_attr(1, LEVEL, ATTR_OPTIONS)) == 0
        before = level(device)
        to_level(device, 100)
        assert writes_after(proc, device) == []
        assert level(device) == before and onoff(device) == 0
    finally:
        proc.stop()


def test_a_level_while_off_is_kept_by_the_module_for_a_stock_mcu():
    proc, device = stock_off(options=1)
    try:
        to_level(device, 100)
        assert writes_after(proc, device) == []          # it would forget it: nothing sent
        assert level(device) == 100 and onoff(device) == 0
        device.call_zigbee_cmd(1, ON_OFF, CMD_ON)
        assert writes_after(proc, device) == [(DP_ON_L1, 1), (DP_LEVEL_L1, scaled_to_tuya(100, MN, MX))]
        device.call_zigbee_cmd(1, ON_OFF, CMD_OFF)
        writes_after(proc, device)
        device.call_zigbee_cmd(1, ON_OFF, CMD_ON)          # applied once: an On as before
        assert writes_after(proc, device) == [(DP_ON_L1, 1)]
    finally:
        proc.stop()


def test_the_wall_switch_also_takes_a_level_kept_for_a_stock_mcu():
    proc, device = stock_off(options=1)
    try:
        to_level(device, 90)
        writes_after(proc, device)
        mcu_report(proc, bool_dp(DP_ON_L1, 1))           # wall switch
        assert writes_after(proc, device) == [(DP_LEVEL_L1, scaled_to_tuya(90, MN, MX))]
        assert onoff(device) == 1 and level(device) == 90
    finally:
        proc.stop()


def test_on_level_wins_over_a_level_set_while_off():
    proc, device = stock_off(options=1)
    try:
        set_on_level(device, 60)
        to_level(device, 100)
        device.call_zigbee_cmd(1, ON_OFF, CMD_ON)
        assert writes_after(proc, device) == [(DP_ON_L1, 1), (DP_LEVEL_L1, scaled_to_tuya(60, MN, MX))]
    finally:
        proc.stop()


@pytest.mark.parametrize("options, mask_override, executed", [
    (0, (1, 1), True),            # the command asks for it
    (1, (1, 0), False),           # the command takes it back
    (1, (0, 0), True),            # no mask: the attribute
    (1, (2, 0), True),            # bit 1 only: bit 0 from the attribute
    (0, (2, 3), False),
])
def test_the_commands_options_override_the_attribute(options, mask_override, executed):
    proc, device = stock_off(options=options)
    try:
        to_level(device, 120, *mask_override)
        assert writes_after(proc, device) == []
        assert (level(device) == 120) == executed
        assert onoff(device) == 0
    finally:
        proc.stop()


def test_step_and_move_while_off_with_execute_if_off_change_the_level_only():
    proc, device = stock_off(options=1)
    try:
        to_level(device, 100)
        device.call_zigbee_cmd(1, LEVEL, STEP, bytes([UP, 30, 0, 0]))
        assert level(device) == 130
        device.call_zigbee_cmd(1, LEVEL, STEP, bytes([DOWN, 200, 0, 0]))
        assert level(device) == 1
        device.call_zigbee_cmd(1, LEVEL, MOVE, bytes([UP, 0xFF]))
        for _ in range(3):
            device.step_time(310)
        assert level(device) == 1 + 3 * 12
        assert writes_after(proc, device) == [] and onoff(device) == 0
    finally:
        proc.stop()


def test_commands_with_onoff_are_not_affected():
    proc, device = stock_off(options=1)
    try:
        device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([100, 0, 0]))
        assert writes_after(proc, device) == [(DP_ON_L1, 1), (DP_LEVEL_L1, scaled_to_tuya(100, MN, MX))]
    finally:
        proc.stop()


def test_options_survive_a_restart():
    proc, device = stock_off(options=1)
    proc.stop()
    proc, device = start()
    try:
        assert int(device.read_zigbee_attr(1, LEVEL, ATTR_OPTIONS)) == 1
        assert int(device.read_zigbee_attr(2, LEVEL, ATTR_OPTIONS)) == 0      # per channel
    finally:
        proc.stop()


# ---- ExecuteIfOff, Open Dimmer -------------------------------------------------------------

def test_open_dimmer_keeps_a_level_written_while_off():
    proc, device = start()
    try:
        mcu = own_mcu_contact(proc, device, build=500)
        take_tx(proc)
        device.write_zigbee_attr(1, LEVEL, ATTR_OPTIONS, 1)
        mcu.writes.clear()
        to_level(device, 100)
        mcu.serve()
        value = scaled_to_tuya(100, 200, 1000)
        assert mcu.writes == [(DP_LEVEL_L1, value)]         # the level only, the light stays off
        assert mcu.ch[0]["on"] == 0 and mcu.ch[0]["level"] == value
        assert onoff(device) == 0 and level(device) == 100
        mcu.writes.clear()
        device.call_zigbee_cmd(1, ON_OFF, CMD_ON)
        mcu.serve()
        assert mcu.writes == [(DP_ON_L1, 1)]                # it comes on at the kept level
    finally:
        proc.stop()


# ---- the wall switch's OnLevel (Open Dimmer 2.2.0 only) ------------------------------------

OD_MIN, OD_MAX = 200, 1000          # OwnMcu's range


def od_wall_on(proc, device, mcu) -> list[tuple[int, int]]:
    """Open Dimmer: off, then the wall switch turns channel 1 on (an active report)."""
    device.call_zigbee_cmd(1, ON_OFF, CMD_OFF)
    mcu.serve()
    mcu.writes.clear()
    mcu.ch[0]["on"] = 1
    mcu.send([mcu._report([mcu._dp(DP_ON_L1)], passive=False)])
    mcu.serve()
    return list(mcu.writes)


def od(build: int | None = 500):
    proc, device = start()
    mcu = own_mcu_contact(proc, device, build=build)
    take_tx(proc)
    return proc, device, mcu


def test_the_wall_switch_on_level_defaults_to_on_level():
    proc, device, mcu = od()
    try:
        assert int(device.read_zigbee_attr(1, LEVEL, ATTR_ON_LEVEL_LOCAL)) == 0xFF
        set_on_level(device, 100)
        assert od_wall_on(proc, device, mcu) == [(DP_LEVEL_L1, scaled_to_tuya(100, OD_MIN, OD_MAX))]
    finally:
        proc.stop()


def test_the_wall_switch_and_the_network_have_their_own_on_levels():
    proc, device, mcu = od()
    try:
        set_on_level(device, 60)
        device.write_zigbee_attr(1, LEVEL, ATTR_ON_LEVEL_LOCAL, 200)
        assert od_wall_on(proc, device, mcu) == [(DP_LEVEL_L1, scaled_to_tuya(200, OD_MIN, OD_MAX))]
        device.call_zigbee_cmd(1, ON_OFF, CMD_OFF)
        mcu.serve()
        mcu.writes.clear()
        device.call_zigbee_cmd(1, ON_OFF, CMD_ON)
        mcu.serve()
        assert mcu.writes == [(DP_ON_L1, 1), (DP_LEVEL_L1, scaled_to_tuya(60, OD_MIN, OD_MAX))]
    finally:
        proc.stop()


def test_a_wall_switch_on_level_alone_leaves_the_network_on_to_the_mcu():
    proc, device, mcu = od()
    try:
        device.write_zigbee_attr(1, LEVEL, ATTR_ON_LEVEL_LOCAL, 200)
        assert od_wall_on(proc, device, mcu) == [(DP_LEVEL_L1, scaled_to_tuya(200, OD_MIN, OD_MAX))]
        device.call_zigbee_cmd(1, ON_OFF, CMD_OFF)
        mcu.serve()
        mcu.writes.clear()
        device.call_zigbee_cmd(1, ON_OFF, CMD_ON)
        mcu.serve()
        assert mcu.writes == [(DP_ON_L1, 1)]
    finally:
        proc.stop()


@pytest.mark.parametrize("build", [400, None])
def test_an_older_open_dimmer_ignores_the_wall_switch_on_level(build):
    proc, device, mcu = od(build)
    try:
        set_on_level(device, 60)
        device.write_zigbee_attr(1, LEVEL, ATTR_ON_LEVEL_LOCAL, 200)
        assert od_wall_on(proc, device, mcu) == [(DP_LEVEL_L1, scaled_to_tuya(60, OD_MIN, OD_MAX))]
    finally:
        proc.stop()


def test_a_stock_mcu_gets_what_it_got_before_whatever_the_wall_switch_on_level():
    for local in (0xFF, 200):
        proc, device = start()
        try:
            dump_range(proc, MN, MX)
            set_on_level(device, 100)
            if local != 0xFF:
                device.write_zigbee_attr(1, LEVEL, ATTR_ON_LEVEL_LOCAL, local)
            assert wall_on(proc, device) == [(DP_LEVEL_L1, scaled_to_tuya(100, MN, MX))], local
            known_off(proc)
            device.call_zigbee_cmd(1, ON_OFF, CMD_ON)
            assert writes_after(proc, device) == [(DP_ON_L1, 1), (DP_LEVEL_L1, scaled_to_tuya(100, MN, MX))]
        finally:
            proc.stop()


def wall_on(proc, device) -> list[tuple[int, int]]:
    known_off(proc)
    mcu_report(proc, bool_dp(DP_ON_L1, 1))
    return writes_after(proc, device)


def test_the_wall_switch_on_level_survives_a_restart_and_0_reads_as_none():
    proc, device = start()
    device.write_zigbee_attr(1, LEVEL, ATTR_ON_LEVEL_LOCAL, 150)
    device.write_zigbee_attr(2, LEVEL, ATTR_ON_LEVEL_LOCAL, 0)
    proc.stop()
    proc, device = start()
    try:
        assert int(device.read_zigbee_attr(1, LEVEL, ATTR_ON_LEVEL_LOCAL)) == 150
        assert int(device.read_zigbee_attr(2, LEVEL, ATTR_ON_LEVEL_LOCAL)) == 0xFF
        pump(proc)
    finally:
        proc.stop()
