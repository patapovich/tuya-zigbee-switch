"""The ZH-Z06 dimmers' virtual MCU (src/zigbee/hk_dimmer.c) as an Open Dimmer of build 500 (2.2.0,
dimmer_cluster.c HK_OD_BUILD, dimmer_own_mcu() for its channels): the settings of Open Dimmer
2.2.0 with its DPs and their meaning (open-dimmer src/dimmer.h): the countdown (DP 6 / 12, ZCL
OnWithTimedOff), the per-channel power-on (103 / 104), the wall switch's gestures as the action
(DP 105 / 106 -> genMultistateInput), the wall switch mode (162 / 163), the level after a power
cut (164 / 165), identify (166 / 167), the start boost (168 / 169), the fast press (170 / 171);
and the network reset by wall switch presses (multi_press_reset_count, Open Dimmer's DP 181 as it
acts). NV layout 5 keeps them; layouts 1..4 are taken over. tests/test_hk_dimmer_ts110e_build.py
runs these again on a stub built with the TS110E's flags (no SECONDARY_MCU_IMAGE)."""
import struct

from test_hk_dimmer import (ATTR_FADE_OFF, BUTTON, HOLD_AFTER, MIN, S1, S2, STEP, SWITCH_MOMENTARY,
                            SWITCH_STATE, TS110E_DP_CONFIG, V1, falling, frames, hk, hk_out, level,
                            move, nv_item, onoff, planned_restart, ramps, rising, run_t,
                            set_setting, set_switch_type, split, start, write_nv_item, zcl_value)
from tuya_mcu import pump, take_tx

from tests.zcl_consts import ZCL_CMD_ONOFF_OFF, ZCL_CMD_ONOFF_ON

IDENTIFY, ON_OFF, SWITCH_CFG, LEVEL, BALLAST, MULTISTATE = 0x0003, 0x0006, 0x0007, 0x0008, 0x0301, 0x0012
ATTR_COUNTDOWN = 0xFF10          # genOnOff
ATTR_START_UP_ON_OFF = 0x4003    # genOnOff
ATTR_INPUT_MODE = 0xFF10         # genOnOffSwitchCfg
ATTR_FAST_PRESS = 0xFF11         # genOnOffSwitchCfg
ATTR_BIND_MODE = 0xFF12          # genOnOffSwitchCfg
ATTR_STARTUP_LEVEL = 0x4000      # genLevelCtrl
ATTR_MAX_LEVEL = 0x0011          # lightingBallastCfg
ATTR_BOOST = 0xFF10              # lightingBallastCfg
ATTR_LAMP_COMP = 0xFF11          # lightingBallastCfg
ATTR_DARK_HOLD = 0xFF15          # genOnOffSwitchCfg: long_press_from_off
ATTR_DOUBLE_FULL = 0xFF16        # genOnOffSwitchCfg: double_tap_full
ATTR_IDENTIFY_TIME = 0x0000
ATTR_PRESENT_VALUE = 0x0055      # genMultistateInput
ATTR_MULTI_PRESS_RESET = 0xFF02  # genBasic (endpoint 1)
CMD_ON_WITH_TIMED_OFF, CMD_IDENTIFY = 0x42, 0x00
NORMAL, DETACHED, OFF = 0, 1, 2
SINGLE, DOUBLE, TRIPLE, HOLD, RELEASE, PRESS = 1, 2, 3, 4, 5, 6
GAP = 400                        # ms after the last release: single / double / triple
FULL = 255


def attr(device, ep: int, cluster: int, attr_id: int) -> int:
    return int(device.read_zigbee_attr(ep, cluster, attr_id))


def put(proc, device, ep: int, cluster: int, attr_id: int, value: int) -> None:
    device.write_zigbee_attr(ep, cluster, attr_id, value)
    frames(proc, device, 300)


def run_g(proc, device, ms: int, ep: int = 1) -> tuple[list[bytes], list[int]]:
    """Let time pass: the frames to the dimming MCU and the gestures reported (each one a new
    presentValue, sequence << 4 | gesture), in order."""
    sent, out = [], []
    last = attr(device, ep, MULTISTATE, ATTR_PRESENT_VALUE)
    for _ in range(max(1, ms // STEP)):
        device.step_time(STEP)
        pump(proc, 2)
        sent += split(take_tx(proc))
        pv = attr(device, ep, MULTISTATE, ATTR_PRESENT_VALUE)
        if pv != last:
            assert (pv >> 4) == ((last >> 4) + 1) & 0x0F, (last, pv)    # one at a time
            out.append(pv & 0x0F)
            last = pv
    return sent, out


def tap(proc, device, pin: str = S1, gap: int = 100, ep: int = 1) -> tuple[list[bytes], list[int]]:
    device.press_button(pin)
    a = run_g(proc, device, 60, ep)
    device.release_button(pin)
    b = run_g(proc, device, gap, ep)
    return a[0] + b[0], a[1] + b[1]


def countdown(device, ep: int = 1) -> int:
    return attr(device, ep, ON_OFF, ATTR_COUNTDOWN)


def lit(proc, device, lvl: int, ep: int = 1) -> None:
    move(device, lvl, ep=ep, tenths=0)
    frames(proc, device, 500)


def cold_start(dp_config: str = TS110E_DP_CONFIG):
    """A power cut: the kept byte gone (the stub's q), NV as it was."""
    return start(dp_config)


def one_pass(proc, *steps, skew: int = 0) -> None:
    """The steps with no main loop pass between them (as frames that one pass of the stack
    handles), then the passes again; skew: the clock moves on 1 ms at each of the first that many
    reads after them (on hardware it runs during a pass; the stub's frozen clock stands still)."""
    proc.exec("hold_polls 1")
    try:
        for step in steps:
            step()
        if skew:
            proc.exec(f"millis_skew {skew}")
    finally:
        proc.exec("hold_polls 0")


# ---- the countdown (R45) --------------------------------------------------------------------


def test_a_countdown_turns_the_channel_off_and_reports_its_end():
    """Written while on: off that many seconds later, by the fade settings, reported as a wall
    switch's off would be (on 0 and the countdown 0)."""
    proc, device, _ = start()
    lit(proc, device, 200)
    put(proc, device, 1, ON_OFF, ATTR_COUNTDOWN, 3)
    assert countdown(device) == 3
    before = hk(frames(proc, device, 2400))
    assert before == [] and onoff(device) == 1
    out = hk(frames(proc, device, 2500))
    assert out and out[-1] == 0 and out[0] < hk_out(zcl_value(200)), out
    assert onoff(device) == 0 and countdown(device) == 0


def test_a_countdown_written_while_off_stays_0():
    proc, device, _ = start()
    put(proc, device, 2, ON_OFF, ATTR_COUNTDOWN, 5)
    assert countdown(device, 2) == 0
    device.call_zigbee_cmd(2, ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 7000)
    assert onoff(device, 2) == 1


def test_any_off_ends_the_countdown():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_STATE)
    for how in ("network", "wall switch"):
        if how == "network":
            lit(proc, device, 150)
        else:
            device.press_button(S1)                  # a state switch: closed, on
            frames(proc, device, 1500)
        put(proc, device, 1, ON_OFF, ATTR_COUNTDOWN, 2)
        if how == "network":
            device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_OFF)
        else:
            device.release_button(S1)
        frames(proc, device, 2000)
        assert onoff(device) == 0 and countdown(device) == 0, how
        device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_ON)
        frames(proc, device, 3000)
        assert onoff(device) == 1, how                 # no countdown left to end it
        device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_OFF)
        frames(proc, device, 2000)


def test_on_with_timed_off_turns_on_and_off_after_its_time_the_longer_one_wins():
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ON_OFF, CMD_ON_WITH_TIMED_OFF, bytes([0]) + (40).to_bytes(2, "little") +
                           bytes(2))                    # 4 s
    frames(proc, device, 1500)
    assert onoff(device) == 1 and countdown(device) == 4
    device.call_zigbee_cmd(1, ON_OFF, CMD_ON_WITH_TIMED_OFF, bytes([0]) + (10).to_bytes(2, "little") +
                           bytes(2))                    # 1 s: shorter, the 4 s stays
    frames(proc, device, 2000)
    assert onoff(device) == 1
    frames(proc, device, 2500)
    assert onoff(device) == 0 and countdown(device) == 0


def test_the_countdowns_off_fades_out_though_the_clock_moved_on_in_its_pass():
    """Its off begins in the virtual MCU's poll, after the poll's first look at the clock: the
    fade's start, a ms or more later than that look, is not taken for a fade that has ended (the
    off at once, one frame 0). The same holds for identify's end and a state switch's change."""
    proc, device, _ = start()
    lit(proc, device, 200)
    put(proc, device, 1, ON_OFF, ATTR_COUNTDOWN, 3)
    assert hk(frames(proc, device, 2690)) == [] and onoff(device) == 1      # 10 ms before its end
    one_pass(proc, lambda: device.step_time(20), skew=40)
    out = hk(frames(proc, device, 2500))
    assert falling(out) and out[0] < hk_out(zcl_value(200)) and out[-1] == 0, out
    assert onoff(device) == 0 and countdown(device) == 0


def test_countdown_writes_answered_late_keep_the_end_of_the_latest():
    """Two writes before the first one's answer (one pass of the stack), the second answered a
    while after the virtual MCU took it: the module's end stays the one of its write, so at the
    virtual MCU's end none is left and that off is the countdown's (no mirror off to the bound
    lights). A write and an off in one pass: the write's answer brings back no countdown."""
    from test_dimmer_bindings import sent as bound
    proc, device, _ = start()
    lit(proc, device, 200)
    bound(device)
    proc.exec("hold_polls 1")
    try:
        device.write_zigbee_attr(1, ON_OFF, ATTR_COUNTDOWN, 5)
        device.write_zigbee_attr(1, ON_OFF, ATTR_COUNTDOWN, 2)
        proc.exec("poll 1")                     # the 5's answer; the 2 goes to the virtual MCU
        proc.exec("step_time 100")              # its answer comes 0.1 s later
    finally:
        proc.exec("hold_polls 0")
    frames(proc, device, 1000)
    assert countdown(device) in (1, 2) and onoff(device) == 1
    frames(proc, device, 2500)
    assert onoff(device) == 0 and countdown(device) == 0
    assert bound(device) == []

    lit(proc, device, 200)
    bound(device)
    one_pass(proc, lambda: device.write_zigbee_attr(1, ON_OFF, ATTR_COUNTDOWN, 5),
             lambda: device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_OFF))
    frames(proc, device, 2000)
    assert onoff(device) == 0 and countdown(device) == 0
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 6500)
    assert onoff(device) == 1 and countdown(device) == 0                 # none left to end it


def test_a_follower_goes_off_with_its_leaders_countdown():
    """The countdown's end is the channel's own off (as Open Dimmer's): the channel that follows
    it goes along (dimmer_cluster's follow), not a mirror's wall switch off to the bindings."""
    from test_dimmer_bindings import sent as bound
    proc, device, _ = start()
    for attr_id, value in ((0xFF17, 1), (0xFF18, 120), (0xFF19, 0)):
        device.write_zigbee_attr(2, SWITCH_CFG, attr_id, value)
    device.click_button(BUTTON)                                         # both on, L2 at 120
    frames(proc, device, 2000)
    assert onoff(device, 1) == onoff(device, 2) == 1
    put(proc, device, 1, ON_OFF, ATTR_COUNTDOWN, 2)
    bound(device)
    frames(proc, device, 4000)
    assert onoff(device, 1) == 0 and onoff(device, 2) == 0
    assert bound(device) == []


# ---- the per-channel power-on (R40) -----------------------------------------------------------


def test_each_channel_has_its_own_power_on():
    proc, device, _ = start()
    put(proc, device, 2, ON_OFF, ATTR_START_UP_ON_OFF, 0xFF)       # L2: previous
    assert attr(device, 2, ON_OFF, ATTR_START_UP_ON_OFF) == 0xFF
    assert attr(device, 1, ON_OFF, ATTR_START_UP_ON_OFF) == 0      # L1: off (the default)
    lit(proc, device, 120, ep=1)
    lit(proc, device, 180, ep=2)
    frames(proc, device, 6000)
    proc.stop()
    proc, device, boot = cold_start()
    assert onoff(device, 1) == 0 and onoff(device, 2) == 1
    assert hk(boot, 1)[-1] == 0 and hk(boot, 2)[-1] == hk_out(zcl_value(180))
    assert attr(device, 2, ON_OFF, ATTR_START_UP_ON_OFF) == 0xFF
    put(proc, device, 1, ON_OFF, ATTR_START_UP_ON_OFF, 1)          # L1: on, L2 stays previous
    assert attr(device, 1, ON_OFF, ATTR_START_UP_ON_OFF) == 1
    assert attr(device, 2, ON_OFF, ATTR_START_UP_ON_OFF) == 0xFF


def test_a_layout_4_item_gives_both_channels_its_power_on_and_is_saved_as_layout_5():
    """The deployed units' item (one power-on for both; 36 bytes in the stub, 40 on the Telink
    build): both channels take it; the next save is a layout 5 item (54 bytes in the stub, 56
    there) with the rest as it was, and Open Dimmer's defaults for what is new (the slow zones 15 /
    25 %, no lamp compensation, the switch options on, no hold way learnt)."""
    item = bytes([4, 1]) + V1[2:] + struct.pack("<HHHHHHHHBB", 500, 1500, 700, 1000, 1500, 900,
                                                 4000, 3000, 1, 3)
    write_nv_item(item)
    proc, device, boot = start()
    assert onoff(device, 1) == 1 and onoff(device, 2) == 1           # power-on on: both
    assert attr(device, 1, ON_OFF, ATTR_START_UP_ON_OFF) == 1
    assert attr(device, 2, ON_OFF, ATTR_START_UP_ON_OFF) == 1
    put(proc, device, 1, ON_OFF, ATTR_COUNTDOWN, 0)                  # (no setting, RAM only)
    put(proc, device, 2, BALLAST, ATTR_BOOST, 100)
    frames(proc, device, 6000)
    saved = nv_item()
    kept = bytearray(item)
    kept[0], kept[2] = 5, 0                                          # (on is kept for previous)
    assert len(saved) == 54 and saved[:36] == bytes(kept), saved.hex()
    assert saved[36] == 1                                            # L2's power-on
    assert saved[41:43] == bytes([0, 2])                             # the boost / 50 ms
    assert saved[45:54] == bytes([15, 15, 25, 25, 0, 0, 0, 0, 0])    # zones, comp, opts, ways


def test_a_layout_5_item_keeps_every_setting():
    base = bytes([5, 2]) + V1[2:] + struct.pack("<HHHHHHHHBB", 1500, 1500, 1000, 1000, 1500, 1500,
                                                 4000, 4000, 2, 2)
    # power-on L2, modes, power-cut levels / 4, boosts / 50, fast press, the slow zones (off L1,
    # L2, hold L1, L2), the lamp compensations / 4, the switch options, the ways learnt, spare
    item = base + bytes([0, 1, 2, 25, 0, 3, 15, 0, 1, 7, 0, 40, 100, 5, 63, 0x06, 0x21, 0])
    assert len(item) == 54
    write_nv_item(item)
    proc, device, _ = start()
    assert attr(device, 1, ON_OFF, ATTR_START_UP_ON_OFF) == 0xFF     # previous
    assert attr(device, 2, ON_OFF, ATTR_START_UP_ON_OFF) == 0
    assert [attr(device, ep, SWITCH_CFG, ATTR_INPUT_MODE) for ep in (1, 2)] == [DETACHED, OFF]
    assert [attr(device, ep, BALLAST, ATTR_BOOST) for ep in (1, 2)] == [150, 750]
    assert [attr(device, ep, SWITCH_CFG, ATTR_FAST_PRESS) for ep in (1, 2)] == [0, 1]
    assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == 24            # 100 of 10..1000: 24
    assert attr(device, 2, LEVEL, ATTR_STARTUP_LEVEL) == 0xFF          # the last one
    assert [attr(device, 1, 0x0000, a) for a in (0xFF3C, 0xFF3D, 0xFF3E, 0xFF3F)] == [7, 0, 40, 100]
    assert [attr(device, ep, BALLAST, ATTR_LAMP_COMP) for ep in (1, 2)] == [5, 63]   # (of 254)
    assert [attr(device, ep, SWITCH_CFG, a) for ep in (1, 2) for a in (ATTR_DARK_HOLD, ATTR_DOUBLE_FULL)] \
        == [1, 0, 0, 1]                                  # L2's rise and L1's double tap off


# ---- the wall switch's gestures as the action (R34) ------------------------------------------


def test_a_momentary_switchs_gestures_are_the_action():
    """single / double / triple after the gap, hold and its release at once; the light acts on
    each press as before (normal mode)."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    assert attr(device, 1, MULTISTATE, 0x004A) == 7                    # NumberOfStates
    _, g = tap(proc, device, gap=GAP + 100)
    assert g == [SINGLE] and onoff(device) == 1
    g = tap(proc, device)[1] + tap(proc, device, gap=GAP + 100)[1]
    assert g == [DOUBLE]
    g = tap(proc, device)[1] + tap(proc, device)[1] + tap(proc, device, gap=GAP + 100)[1]
    assert g == [TRIPLE]
    device.press_button(S1)
    held = run_g(proc, device, HOLD_AFTER + 600)[1]
    device.release_button(S1)
    released = run_g(proc, device, GAP + 100)[1]
    assert held == [HOLD] and released == [RELEASE]
    _, g2 = tap(proc, device, S2, gap=GAP + 100, ep=2)                 # a toggle switch: none
    assert g2 == []


# ---- the wall switch mode (R33) and the fast press (R35) --------------------------------------


def test_detached_a_momentary_switch_only_reports_its_gestures():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, DETACHED)
    assert attr(device, 1, SWITCH_CFG, ATTR_INPUT_MODE) == DETACHED
    sent, g = tap(proc, device, gap=GAP + 100)
    assert g == [SINGLE] and hk(sent) == [] and onoff(device) == 0
    device.press_button(S1)
    a = run_g(proc, device, HOLD_AFTER + 1000)
    device.release_button(S1)
    b = run_g(proc, device, GAP + 100)
    assert a[1] + b[1] == [HOLD, RELEASE] and hk(a[0] + b[0]) == [] and onoff(device) == 0
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_ON)                # the network's light
    frames(proc, device, 1500)
    assert onoff(device) == 1


def test_detached_a_toggle_or_state_switchs_every_change_is_a_single():
    proc, device, _ = start()
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, DETACHED)
    device.press_button(S1)
    a = run_g(proc, device, 300)
    device.release_button(S1)
    b = run_g(proc, device, 300)
    assert a[1] == [SINGLE] and b[1] == [SINGLE] and hk(a[0] + b[0]) == [] and onoff(device) == 0


def test_off_the_wall_switch_is_ignored():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, OFF)
    sent, g = tap(proc, device, gap=GAP + 100)
    device.press_button(S1)
    a = run_g(proc, device, HOLD_AFTER + 600)
    device.release_button(S1)
    b = run_g(proc, device, GAP + 100)
    assert g == [] and a[1] + b[1] == [] and hk(sent + a[0] + b[0]) == [] and onoff(device) == 0


def test_the_fast_press_reports_the_press_at_once_and_no_multi_taps():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, DETACHED)
    put(proc, device, 1, SWITCH_CFG, ATTR_FAST_PRESS, 1)
    assert attr(device, 1, SWITCH_CFG, ATTR_FAST_PRESS) == 1
    device.set_gpio(S1, 0)
    press = run_g(proc, device, 100)[1]                                 # within the debounce
    device.set_gpio(S1, 1)
    after = run_g(proc, device, GAP + 200)[1]
    assert press == [PRESS] and after == []
    g = tap(proc, device)[1] + tap(proc, device, gap=GAP + 100)[1]
    assert g == [PRESS, PRESS]                                          # no double
    device.press_button(S1)
    a = run_g(proc, device, HOLD_AFTER + 500)[1]
    device.release_button(S1)
    b = run_g(proc, device, GAP + 100)[1]
    assert a == [PRESS, HOLD] and b == [RELEASE]
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, NORMAL)           # normal: no fast press
    assert tap(proc, device, gap=GAP + 100)[1] == [SINGLE]


def test_a_mode_change_during_a_hold_leaves_the_light_where_it_is_and_skips_the_release():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    lit(proc, device, 40)
    device.press_button(S1)                                             # below the middle: up
    held = hk(frames(proc, device, HOLD_AFTER + 1200))
    device.write_zigbee_attr(1, SWITCH_CFG, ATTR_INPUT_MODE, DETACHED)
    after = hk(frames(proc, device, 1000))
    shown = level(device)
    device.release_button(S1)
    released, g = run_g(proc, device, GAP + 200)
    assert rising(held) and len(after) <= 1 and all(abs(x - held[-1]) <= 3 for x in after), (held, after)
    assert abs(shown - 40) > 10 and hk(released) == [] and g == [] and onoff(device) == 1
    assert abs(hk_out(zcl_value(shown)) - held[-1]) <= 3


def test_a_press_begun_in_another_mode_is_not_acted_on_at_its_release():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, DETACHED)
    device.press_button(S1)
    run_g(proc, device, 100)
    device.write_zigbee_attr(1, SWITCH_CFG, ATTR_INPUT_MODE, NORMAL)
    run_g(proc, device, 600)                                            # (no hold: skipped too)
    device.release_button(S1)
    sent, g = run_g(proc, device, GAP + 200)
    assert hk(sent) == [] and g == [] and onoff(device) == 0
    assert tap(proc, device, gap=GAP + 100)[1] == [SINGLE] and onoff(device) == 1


def test_back_in_normal_a_state_switch_says_what_the_light_is():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_STATE)
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, DETACHED)
    device.press_button(S1)                                             # closed: nothing
    frames(proc, device, 1500)
    assert onoff(device) == 0
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, NORMAL)
    frames(proc, device, 1500)
    assert onoff(device) == 1


def test_the_wall_switch_mode_and_the_fast_press_are_kept_and_in_the_dump():
    proc, device, _ = start()
    put(proc, device, 2, SWITCH_CFG, ATTR_INPUT_MODE, OFF)
    put(proc, device, 1, SWITCH_CFG, ATTR_FAST_PRESS, 1)
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, 3)                # none such: as it was
    assert attr(device, 1, SWITCH_CFG, ATTR_INPUT_MODE) == NORMAL
    frames(proc, device, 6000)
    proc.stop()
    proc, device, _ = cold_start()
    assert attr(device, 2, SWITCH_CFG, ATTR_INPUT_MODE) == OFF
    assert attr(device, 1, SWITCH_CFG, ATTR_FAST_PRESS) == 1
    assert attr(device, 1, SWITCH_CFG, ATTR_INPUT_MODE) == NORMAL


def test_detached_auto_bind_mode_lets_the_gestures_drive_the_bound_lights():
    from test_dimmer_bindings import sent as bound
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, DETACHED)
    bound(device)
    tap(proc, device, gap=GAP + 100)
    assert bound(device) == [(3, ON_OFF, 0x02, b"")] and onoff(device) == 0     # Toggle


# ---- the level after a power cut (R41) --------------------------------------------------------


def test_the_level_after_a_power_cut_is_taken_at_a_cold_start_only():
    proc, device, _ = start()
    put(proc, device, 1, ON_OFF, ATTR_START_UP_ON_OFF, 1)                 # on after a power cut
    put(proc, device, 1, LEVEL, ATTR_STARTUP_LEVEL, 60)
    assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == 60
    lit(proc, device, 200)
    frames(proc, device, 6000)
    planned_restart(proc, device)                                        # warm: as it was
    proc, device, _ = start()
    assert onoff(device) == 1 and abs(level(device) - 200) <= 1
    move(device, 220)
    frames(proc, device, 6000)
    proc.stop()                                                          # a power cut
    proc, device, boot = cold_start()
    assert onoff(device) == 1 and abs(level(device) - 60) <= 1
    assert abs(hk(boot)[-1] - hk_out(zcl_value(60))) <= 1


def test_the_level_after_a_power_cut_minimum_and_previous():
    proc, device, _ = start()
    put(proc, device, 1, ON_OFF, ATTR_START_UP_ON_OFF, 1)
    put(proc, device, 1, LEVEL, ATTR_STARTUP_LEVEL, 0)                    # the minimum
    assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == 0
    lit(proc, device, 200)
    frames(proc, device, 6000)
    proc.stop()
    proc, device, boot = cold_start()
    assert hk(boot)[-1] <= MIN + 1 and level(device) <= 2
    put(proc, device, 1, LEVEL, ATTR_STARTUP_LEVEL, 0xFF)                 # previous
    lit(proc, device, 150)
    frames(proc, device, 6000)
    proc.stop()
    proc, device, boot = cold_start()
    assert attr(device, 1, LEVEL, ATTR_STARTUP_LEVEL) == 0xFF and abs(level(device) - 150) <= 1


# ---- identify (R46) -------------------------------------------------------------------------


def identify(device, ep: int, s: int) -> None:
    device.call_zigbee_cmd(ep, IDENTIFY, CMD_IDENTIFY, s.to_bytes(2, "little"))


def test_identify_blinks_the_channel_dark_and_full_then_it_is_as_it_was():
    proc, device, _ = start()
    lit(proc, device, 100)
    identify(device, 1, 3)
    assert attr(device, 1, IDENTIFY, ATTR_IDENTIFY_TIME) == 3
    blink = run_t(proc, device, 2900)          # (its end, 3 s from the write, not in it: from a
    levels = hk([f for _, f in blink])         # dark half its fade starts at the minimum)
    assert set(levels) == {0, FULL} and len(levels) >= 5, levels
    assert all(r == b"\x00\x00" for r in ramps([f for _, f in blink]))
    times = [t for t, f in blink if f[4] == 1]
    assert all(400 <= b - a <= 600 for a, b in zip(times[1:], times[2:])), times
    after = hk(frames(proc, device, 2100))
    assert after[-1] == hk_out(zcl_value(100)) and onoff(device) == 1
    assert attr(device, 1, IDENTIFY, ATTR_IDENTIFY_TIME) == 0


def test_identify_of_a_dark_channel_ends_dark_and_0_stops_it():
    proc, device, _ = start()
    identify(device, 2, 10)
    assert set(hk(frames(proc, device, 1500), 2)) == {0, FULL}
    identify(device, 2, 0)
    after = hk(frames(proc, device, 2500), 2)
    assert FULL not in after and (after == [] or after[-1] == 0), after   # from full: fades out
    assert onoff(device, 2) == 0 and attr(device, 2, IDENTIFY, ATTR_IDENTIFY_TIME) == 0


def test_identify_is_capped_at_255_s():
    proc, device, _ = start()
    identify(device, 1, 600)
    assert attr(device, 1, IDENTIFY, ATTR_IDENTIFY_TIME) == 255


# ---- the start boost (R20) --------------------------------------------------------------------


def boost(proc, device, ms: int, ep: int = 1) -> None:
    put(proc, device, ep, BALLAST, ATTR_BOOST, ms)


def test_the_start_boost_is_full_from_dark_then_the_level():
    """From dark: the channel's full first (at ramp 0) for the boost, then straight at the level
    (no fade from the minimum after it), at ramp 0."""
    proc, device, _ = start()
    boost(proc, device, 200)
    assert attr(device, 1, BALLAST, ATTR_BOOST) == 200
    move(device, 120)
    sent = [(t, f) for t, f in run_t(proc, device, 1500) if f[4] == 1]
    assert sent[0][1][5] == FULL and sent[1][1][5] == hk_out(zcl_value(120)), sent
    # 19 ticks at full, the 20th at the level (Open Dimmer's); the first frame went at the
    # command, before the first step of 10 ms
    assert 180 <= sent[1][0] - sent[0][0] <= 230 and len(sent) == 2, sent
    assert all(f[7:9] == b"\x00\x00" for _, f in sent)
    lit(proc, device, 200)                                               # lit: no boost
    assert FULL not in hk(frames(proc, device, 500))


def test_the_start_boost_is_the_channels_max_and_in_steps_of_50():
    proc, device, _ = start()
    boost(proc, device, 130)
    assert attr(device, 1, BALLAST, ATTR_BOOST) == 100
    put(proc, device, 1, BALLAST, ATTR_MAX_LEVEL, 150)
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_ON)
    first = hk(frames(proc, device, 2000))[0]
    assert first == hk_out(1000, mx=zcl_value(150)) < 200, first


def test_a_transition_time_keeps_its_end_through_the_boost():
    proc, device, _ = start()
    boost(proc, device, 300)
    move(device, 254, tenths=20)                                          # 2 s from dark
    sent = [(t, f[5]) for t, f in run_t(proc, device, 3000) if f[4] == 1]
    assert sent[0][1] == FULL
    after = sent[1:]
    # After it where the fade is by then (300 ms of 2 s), not at its start; its end at 2 s.
    assert after[0][0] - sent[0][0] >= 290 and after[0][1] >= hk_out(10 + 990 * 250 // 2000), after
    assert rising([v for _, v in after])
    top = next(t for t, v in after if v == FULL)
    assert 1940 <= top - sent[0][0] <= 2100, (top, sent[0])


def test_an_off_during_the_boost_goes_out_from_the_minimum():
    """Open Dimmer's: the boost ends, the light goes out the usual way from the minimum (its 80 ms
    there), at ramp 0."""
    proc, device, _ = start()
    boost(proc, device, 500)
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_ON)
    first = hk(frames(proc, device, 100))
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_OFF)
    after = run_t(proc, device, 1500)
    assert first == [FULL] and hk([f for _, f in after]) == [MIN, 0], after
    assert 70 <= after[1][0] - after[0][0] <= 100, after
    assert all(f[7:9] == b"\x00\x00" for _, f in after)


def test_a_hold_from_dark_boosts_then_rises_from_the_minimum_a_release_in_the_boost_keeps_it():
    """From the press: the boost at full, then the minimum (its 0.7 s wait), then the rise; a
    hold let go while the boost still runs (one longer than the hold time) ends at the minimum."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    boost(proc, device, 300)
    device.press_button(S1)
    held = hk(frames(proc, device, 300 + 700 + 1500))
    device.release_button(S1)
    frames(proc, device, 500)
    assert held[0] == FULL and held[1] == MIN and rising(held[1:]), held
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_OFF)
    frames(proc, device, 3000)
    boost(proc, device, 750)
    device.press_button(S1)
    frames(proc, device, HOLD_AFTER + 50 + 100)                          # the hold, then let go
    device.release_button(S1)                                            # within the boost
    out = hk(frames(proc, device, 1000))
    assert out[-1] == MIN and onoff(device) == 1 and level(device) == 1, out


def test_a_hold_from_off_while_the_light_still_fades_out_has_no_boost():
    """Open Dimmer boosts only from a dark output: a hold from off during the fade out goes on
    from where the light is (its 0.7 s wait there, then up), with no flash to full first."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    set_setting(proc, device, ATTR_FADE_OFF, 4000)
    boost(proc, device, 500)
    lit(proc, device, 150)
    device.press_button(S1)                                              # a tap: off
    frames(proc, device, 60)
    device.release_button(S1)
    fading = hk(frames(proc, device, 300))
    device.press_button(S1)                                              # held
    held = hk(frames(proc, device, HOLD_AFTER + 1000))
    device.release_button(S1)
    frames(proc, device, 300)
    assert falling(fading) and fading[-1] > MIN + 5, fading              # not dark yet
    low = held.index(min(held))                      # (the fade out goes on for the debounce)
    assert FULL not in held and held[low] >= fading[-1] - 8 and held[low] > MIN + 30, held
    assert rising(held[low:]), held
    assert onoff(device) == 1


def test_no_boost_after_a_restart_of_the_module_alone():
    proc, device, _ = start()
    boost(proc, device, 300)
    lit(proc, device, 150)
    frames(proc, device, 6000)
    planned_restart(proc, device)
    proc, device, boot = start()
    assert FULL not in hk(boot) and onoff(device) == 1
    proc.stop()
    proc, device, boot = cold_start()                                    # power-on off: dark
    assert hk(boot)[-1] == 0


# ---- the network reset by wall switch presses (R47) ------------------------------------------


LEAVE_RESTART_MS = 1000    # device_config/reset.h LEAVE_RESTART_DELAY_MS


def left_and_restarted(proc, device, within_ms: int = 4000) -> None:
    """The reset: the leave (after the channel's blink), then the module's restart 1 s after it
    (device_config/reset.c restart_after_leave): the stub's process ends."""
    for _ in range(within_ms // 100):
        frames(proc, device, 100)
        if not device.is_joined():
            break
    assert not device.is_joined()
    frames(proc, device, LEAVE_RESTART_MS - 100)
    assert proc.is_running()
    device.step_time(100)
    assert proc.wait_for_exit(2.0), "no restart after the leave"


def presses(proc, device, n: int, pin: str = S1) -> None:
    for _ in range(n):
        device.press_button(pin)
        frames(proc, device, 100)
        device.release_button(pin)
        frames(proc, device, 100)


def test_ten_presses_of_one_wall_switch_blink_its_channel_then_reset_the_network():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    assert device.is_joined()
    presses(proc, device, 9)
    assert device.is_joined()
    device.press_button(S1)                                              # the 10th
    frames(proc, device, 100)
    device.release_button(S1)
    blink = hk(frames(proc, device, 2400))
    assert device.is_joined() and FULL in blink and 0 in blink, blink
    frames(proc, device, 1000)
    assert not device.is_joined()


def test_presses_further_apart_than_10_s_or_of_two_switches_are_no_reset():
    proc, device, _ = start()
    presses(proc, device, 6)
    frames(proc, device, 9000)                                           # the window from the 1st
    presses(proc, device, 6)
    presses(proc, device, 5, S2)
    presses(proc, device, 5)
    frames(proc, device, 4000)
    assert device.is_joined()


def test_a_state_switch_counts_its_closures_not_its_changes():
    """(A toggle switch, the default type, in the tests above.)"""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_STATE)
    presses(proc, device, 9)                                             # 18 changes
    frames(proc, device, 4000)
    assert device.is_joined()
    frames(proc, device, 7000)
    presses(proc, device, 10)
    left_and_restarted(proc, device)


def test_the_reset_count_0_never_and_1_to_7_act_as_8():
    proc, device, _ = start()
    device.write_zigbee_attr(1, 0x0000, ATTR_MULTI_PRESS_RESET, 0)
    presses(proc, device, 12)
    frames(proc, device, 4000)
    assert device.is_joined()
    device.write_zigbee_attr(1, 0x0000, ATTR_MULTI_PRESS_RESET, 3)
    frames(proc, device, 11000)
    presses(proc, device, 7)
    frames(proc, device, 4000)
    assert device.is_joined()
    frames(proc, device, 10000)
    presses(proc, device, 8)
    left_and_restarted(proc, device)


def test_a_wall_switch_set_off_counts_no_presses_a_detached_one_does():
    proc, device, _ = start()
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, OFF)
    presses(proc, device, 12)
    frames(proc, device, 4000)
    assert device.is_joined()
    put(proc, device, 1, SWITCH_CFG, ATTR_INPUT_MODE, DETACHED)
    frames(proc, device, 10000)
    presses(proc, device, 10)
    left_and_restarted(proc, device)


# ---- the dump -------------------------------------------------------------------------------


def test_the_settings_come_back_with_the_dump_after_a_restart():
    """Nothing but the virtual MCU's dump tells the module the settings after a restart (it
    keeps none of them): every one of them is shown again as it was."""
    proc, device, _ = start()
    put(proc, device, 2, ON_OFF, ATTR_START_UP_ON_OFF, 0xFF)
    put(proc, device, 2, SWITCH_CFG, ATTR_INPUT_MODE, DETACHED)
    put(proc, device, 2, SWITCH_CFG, ATTR_FAST_PRESS, 1)
    put(proc, device, 2, LEVEL, ATTR_STARTUP_LEVEL, 100)
    put(proc, device, 2, BALLAST, ATTR_BOOST, 250)
    frames(proc, device, 6000)
    planned_restart(proc, device)
    proc, device, _ = start()
    assert attr(device, 2, ON_OFF, ATTR_START_UP_ON_OFF) == 0xFF
    assert attr(device, 2, SWITCH_CFG, ATTR_INPUT_MODE) == DETACHED
    assert attr(device, 2, SWITCH_CFG, ATTR_FAST_PRESS) == 1
    assert abs(attr(device, 2, LEVEL, ATTR_STARTUP_LEVEL) - 100) <= 1
    assert attr(device, 2, BALLAST, ATTR_BOOST) == 250
    assert [attr(device, ep, ON_OFF, ATTR_START_UP_ON_OFF) for ep in (1,)] == [0]
