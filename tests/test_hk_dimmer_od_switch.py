"""The ZH-Z06 dimmers' virtual MCU (src/zigbee/hk_dimmer.c) with Open Dimmer's wall switch and fader
(open-dimmer controls.c, dimmer.c; the fader: src/zigbee/hk_fader.c): a momentary switch from dark
turns on at the press, a double tap takes the light to full, a long press from off rises from the
minimum after 0.7 s (or, with long_press_from_off off, comes on at its level), a lit hold's way is
learnt per band of min..max and saved, a switch closed at the start is no press (a state switch
says what the light is), the lamp compensation lowers the output after a dim-down, the slow zones
are settings of the module. Open Dimmer's host tests of these (tests/test_controls.c,
test_dimmer.c) ported where they apply. tests/test_hk_dimmer_ts110e_build.py runs them again on a
stub built with the TS110E's flags (no SECONDARY_MCU_IMAGE)."""
import contextlib
import os
import struct

import hk_fader_model as model
from test_hk_dimmer import (ATTR_HOLD, BUTTON, HOLD_AFTER, MIN, S1, SWITCH_MOMENTARY, SWITCH_STATE,
                            falling, frames, hk, hk_out, level, move, nv_item, onoff,
                            planned_restart, rising, run_t, set_setting, set_switch_type, start,
                            zcl_value)
from test_hk_dimmer_od_features import attr, put

from tests.zcl_consts import ZCL_CMD_ONOFF_OFF, ZCL_CMD_ONOFF_ON

BASIC, ON_OFF, SWITCH_CFG, LEVEL, BALLAST = 0x0000, 0x0006, 0x0007, 0x0008, 0x0301
ATTR_DARK_HOLD, ATTR_DOUBLE_FULL = 0xFF15, 0xFF16    # long_press_from_off, double_tap_full
ATTR_LAMP_COMP = 0xFF11                              # lightingBallastCfg
ATTR_MIN_LEVEL, ATTR_MAX_LEVEL = 0x0010, 0x0011      # lightingBallastCfg
ATTR_SOFT_END, ATTR_HOLD_END = 0xFF3C, 0xFF3E        # Basic: + 1 for L2
ATTR_CURVE = 0xFF38                                  # Basic: the dimming curve (DP 0x78)
CUSTOM, LINEAR = 6, 0
FULL = 255
NV_LRN_WAYS = 52                                     # (the stub's layout 5: hk_nv_t.lrn_ways)


def lit_at(proc, device, lvl: int) -> None:
    """On at that level at once, then 3 s: no hold's correction sequence runs after it."""
    move(device, lvl, tenths=0)
    frames(proc, device, 3000)


def momentary(proc, device):
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)


def tap(proc, device, gap: int = 100) -> list[int]:
    """A short press and release of S1, then gap ms; the L1 levels sent."""
    device.press_button(S1)
    device.release_button(S1)
    return hk(frames(proc, device, gap))


def hold_for(proc, device, ms: int) -> int:
    """A hold of S1 for ms past its hold time; the way it went (+1 up, -1 down) by the level."""
    before = level(device)
    device.press_button(S1)
    frames(proc, device, HOLD_AFTER - 60 + ms)
    device.release_button(S1)
    frames(proc, device, 200)
    after = level(device)
    assert after != before, (before, after)
    return 1 if after > before else -1


def off(proc, device, ms: int = 2500) -> None:
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_OFF)
    frames(proc, device, ms)


# ---- a momentary press from dark: on at the press (R24) --------------------------------------


def test_an_off_during_a_press_from_dark_leaves_its_release_nothing():
    """Open Dimmer's: the network's off ends the press's provisional hold; the release (a tap's,
    or a hold's) changes nothing."""
    proc, device, _ = start()
    momentary(proc, device)
    for held in (100, HOLD_AFTER + 500):
        device.press_button(S1)
        assert hk(frames(proc, device, 50)) == [MIN] and onoff(device) == 1
        frames(proc, device, held)
        off(proc, device, 500)
        device.release_button(S1)
        assert hk(frames(proc, device, 1500)) == [] and onoff(device) == 0, held
        frames(proc, device, 3000)


# ---- the double tap to full (R29) -------------------------------------------------------------


def test_a_double_tap_from_off_takes_the_light_to_full():
    """The second tap within 0.4 s of the first's release (Open Dimmer's GESTURE_GAP_MS): full at
    its release, the level reported (and kept)."""
    proc, device, _ = start()
    momentary(proc, device)
    lit_at(proc, device, 100)
    off(proc, device, 3000)
    first = tap(proc, device, 60)
    sent = tap(proc, device, 2000)
    assert first and first[0] == MIN, first                      # on at the press
    assert rising(sent) and sent[-1] == FULL, sent
    assert onoff(device) == 1 and level(device) == 254
    off(proc, device, 6000)
    assert struct.unpack_from("<H", nv_item(), 4)[0] == 1000     # the level kept: the max


def test_a_double_tap_from_on_takes_the_light_to_full():
    proc, device, _ = start()
    momentary(proc, device)
    lit_at(proc, device, 100)
    tap(proc, device, 60)                                        # off at the release
    sent = tap(proc, device, 2000)                               # on at the press, then full
    assert sent[-1] == FULL and onoff(device) == 1 and level(device) == 254, sent


def test_a_tap_and_a_hold_still_dim_and_a_third_tap_is_a_plain_one():
    """Open Dimmer's test_a_tap_and_a_hold_still_dim: the press within the gap and held is a hold
    from dark, from where the fade out has got to, rising; no jump to full."""
    proc, device, _ = start()
    momentary(proc, device)
    lit_at(proc, device, 150)
    fading = tap(proc, device, 60)                               # off
    device.press_button(S1)                                      # within the gap, held
    held = hk(frames(proc, device, HOLD_AFTER + 1500))
    device.release_button(S1)
    frames(proc, device, 300)
    low = held.index(min(held))
    assert FULL not in held and held[low] >= fading[-1] - 16 and held[low] > MIN + 30, (fading, held)
    assert rising(held[low:]), held
    assert onoff(device) == 1 and level(device) < 254
    off(proc, device, 3000)
    tap(proc, device, 60)                                        # on
    tap(proc, device, 60)                                        # full
    third = tap(proc, device, 2500)                              # a plain tap: off
    assert third[-1] == 0 and onoff(device) == 0, third


def test_double_tap_full_off_keeps_the_toggles():
    proc, device, _ = start()
    momentary(proc, device)
    assert attr(device, 1, SWITCH_CFG, ATTR_DOUBLE_FULL) == 1    # the dump told it: on
    put(proc, device, 1, SWITCH_CFG, ATTR_DOUBLE_FULL, 0)
    assert attr(device, 1, SWITCH_CFG, ATTR_DOUBLE_FULL) == 0
    assert attr(device, 2, SWITCH_CFG, ATTR_DOUBLE_FULL) == 1    # per channel
    lit_at(proc, device, 100)
    off(proc, device, 3000)
    tap(proc, device, 60)
    sent = tap(proc, device, 2500)
    assert FULL not in sent and sent[-1] == 0 and onoff(device) == 0, sent


# ---- a long press from off (R28) --------------------------------------------------------------


def test_long_press_from_off_off_comes_on_at_its_level_and_dims_from_there():
    """Open Dimmer's long_press_from_off false: no rise from the minimum; the light comes on at
    its level at the press (its fade), and at the hold time a hold begins there, its way by that
    level (down: above the middle)."""
    proc, device, _ = start()
    momentary(proc, device)
    put(proc, device, 1, SWITCH_CFG, ATTR_DARK_HOLD, 0)
    assert attr(device, 1, SWITCH_CFG, ATTR_DARK_HOLD) == 0
    lit_at(proc, device, 200)
    off(proc, device, 3000)
    device.press_button(S1)
    on = hk(frames(proc, device, HOLD_AFTER - 60))
    held = hk(frames(proc, device, 1500))
    device.release_button(S1)
    frames(proc, device, 300)
    assert rising(on) and on[-1] > MIN + 20, on                   # coming on to its level
    top = held.index(max(held))
    assert falling(held[top:]) and held[-1] < hk_out(zcl_value(200)) - 10, held
    assert onoff(device) == 1 and level(device) < 200


def test_the_switch_options_are_in_the_dump_and_kept():
    proc, device, _ = start()
    assert [attr(device, ep, SWITCH_CFG, a) for ep in (1, 2)
            for a in (ATTR_DARK_HOLD, ATTR_DOUBLE_FULL)] == [1, 1, 1, 1]
    put(proc, device, 2, SWITCH_CFG, ATTR_DARK_HOLD, 0)
    put(proc, device, 1, SWITCH_CFG, ATTR_DOUBLE_FULL, 0)
    frames(proc, device, 6000)
    proc.stop()
    proc, device, _ = start()
    assert [attr(device, ep, SWITCH_CFG, a) for ep in (1, 2)
            for a in (ATTR_DARK_HOLD, ATTR_DOUBLE_FULL)] == [1, 0, 0, 1]


# ---- the hold's way, learnt per band (R27; Open Dimmer's test_dimmer.c) ----------------------

BAND0, BAND2 = 37, 164         # levels in the bands 0 and 2 of the default 10..1000


def test_a_hold_learns_its_way():
    proc, device, _ = start()
    momentary(proc, device)
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 500) == 1          # band 0: the far end, up
    frames(proc, device, 300)
    assert hold_for(proc, device, 500) == -1         # turned round at once: the other way
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 500) == -1         # learnt: band 0 goes down first now
    lit_at(proc, device, BAND2)
    assert hold_for(proc, device, 500) == -1         # band 2 untouched: the far end, down


def test_a_long_hold_corrected_teaches_nothing():
    proc, device, _ = start()
    momentary(proc, device)
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 1500) == 1         # long: it went the right way, too far
    frames(proc, device, 300)
    assert hold_for(proc, device, 300) == -1
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 500) == 1          # still up


def test_a_confirmed_band_needs_two_misses():
    proc, device, _ = start()
    momentary(proc, device)
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 500) == 1
    lit_at(proc, device, BAND0)                      # kept: confirmed
    assert hold_for(proc, device, 500) == 1
    frames(proc, device, 300)
    assert hold_for(proc, device, 300) == -1         # one miss
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 500) == 1          # not enough
    frames(proc, device, 300)
    assert hold_for(proc, device, 300) == -1         # a second miss
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 500) == -1         # turned


def test_no_correction_across_an_off():
    proc, device, _ = start()
    momentary(proc, device)
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 500) == 1
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_OFF)
    move(device, BAND0, tenths=0)
    frames(proc, device, 300)
    assert hold_for(proc, device, 500) == 1          # a new start: learnt way, not the other one
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 500) == 1          # and nothing learnt


def test_the_learnt_way_is_saved():
    proc, device, _ = start()
    momentary(proc, device)
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 500) == 1
    frames(proc, device, 300)
    assert hold_for(proc, device, 500) == -1
    frames(proc, device, 6000)
    assert nv_item()[NV_LRN_WAYS] == 0x01            # band 0 of channel 1
    proc.stop()                                      # a power cut
    proc, device, _ = start()
    momentary(proc, device)
    lit_at(proc, device, BAND0)
    assert hold_for(proc, device, 500) == -1         # still learnt


def test_a_hold_soon_after_a_hold_from_dark_goes_the_other_way():
    """A hold from dark goes up (no band); one within 2 s of its end is a correction: down."""
    proc, device, _ = start()
    momentary(proc, device)
    device.press_button(S1)
    frames(proc, device, 700 + 1500)
    device.release_button(S1)
    frames(proc, device, 300)
    assert hold_for(proc, device, 500) == -1


# ---- a switch closed at the start; a type change to state (R23) ------------------------------


@contextlib.contextmanager
def closed_at_start(*pins: str):
    """A stub started meanwhile has these pins low from its start (STUB_GPIO_LOW, read by its
    GPIO init)."""
    os.environ["STUB_GPIO_LOW"] = ",".join(pins)
    try:
        yield
    finally:
        os.environ.pop("STUB_GPIO_LOW", None)


def test_a_state_switch_closed_at_the_start_turns_the_light_on():
    """Open Dimmer's controls_init: the contact says what the light is, over the power-on setting
    (off, the default): it fades in, and the module's query has it on; its opening turns it off."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_STATE)
    frames(proc, device, 6000)
    proc.stop()
    with closed_at_start(S1):
        proc, device, boot = start()
    levels = hk(boot)
    assert levels[0] == MIN and levels[-1] == FULL and onoff(device) == 1, levels
    device.release_button(S1)
    assert hk(frames(proc, device, 2500))[-1] == 0 and onoff(device) == 0


def test_a_restart_of_the_module_alone_leaves_a_state_switchs_light_as_it_was():
    """A warm start (an update, a config write, the watchdog): the dimming MCU kept the light, as
    a restart of the Zigbee module alone leaves Open Dimmer's (its controls_init does not run);
    the contact says nothing then, either way, and its next change acts as usual."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_STATE)
    device.press_button(S1)                           # closed: on
    frames(proc, device, 1500)
    off(proc, device, 6000)                           # the network: off, the switch still closed
    planned_restart(proc, device)
    with closed_at_start(S1):
        proc, device, boot = start()
    assert hk(boot) == [0, 0] and onoff(device) == 0, hk(boot)       # dark, sent as it is
    device.release_button(S1)                         # its opening: off, as it is
    assert hk(frames(proc, device, 1500)) == [] and onoff(device) == 0
    device.press_button(S1)                           # its closing: on, faded in from the minimum
    lit = hk(frames(proc, device, 2500))
    assert lit[0] == MIN and lit[-1] == FULL and onoff(device) == 1, lit
    device.release_button(S1)
    frames(proc, device, 2500)
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_ON)   # the network: on, the switch open
    frames(proc, device, 6000)
    planned_restart(proc, device)
    proc, device, boot = start()
    assert hk(boot) == [FULL, FULL] and onoff(device) == 1, hk(boot)  # lit, sent as it is


def test_a_momentary_switch_held_through_the_start_is_no_press():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    frames(proc, device, 6000)
    proc.stop()
    with closed_at_start(S1):
        proc, device, _ = start()
    frames(proc, device, 2000)                        # held past a hold time: nothing either
    device.release_button(S1)
    assert hk(frames(proc, device, 1500)) == [] and onoff(device) == 0
    tap(proc, device, 1500)                           # the next press is one
    assert onoff(device) == 1


def test_the_module_button_held_through_the_start_is_no_press():
    with closed_at_start(BUTTON):
        proc, device, _ = start()
    frames(proc, device, 4000)                        # past the re-pair's 3 s too
    device.release_button(BUTTON)
    assert hk(frames(proc, device, 1500)) == [] and onoff(device) == 0 and device.is_joined()
    device.click_button(BUTTON)
    assert hk(frames(proc, device, 1500))[-1] == FULL


def test_a_type_change_to_state_follows_the_contact():
    """Open Dimmer's controls_poll: a new type state says what the light is, reported as a local
    change; the same type written again changes nothing."""
    proc, device, _ = start()
    device.press_button(S1)                           # toggle (the default): on
    frames(proc, device, 1500)
    off(proc, device)                                 # the network: off, the switch still closed
    set_switch_type(proc, device, 1, SWITCH_STATE)
    assert onoff(device) == 1 and hk(frames(proc, device, 1500))[-1] == FULL
    off(proc, device)
    set_switch_type(proc, device, 1, SWITCH_STATE)    # again: no change
    assert onoff(device) == 0 and hk(frames(proc, device, 1500)) == []
    device.release_button(S1)                         # (a state switch's opening: off, as it is)
    frames(proc, device, 300)
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 200, tenths=0)
    frames(proc, device, 500)
    set_switch_type(proc, device, 1, SWITCH_STATE)    # open: off
    assert onoff(device) == 0 and hk(frames(proc, device, 2500))[-1] == 0


# ---- the lamp compensation (R19) --------------------------------------------------------------


def duty_out(duty: int) -> int:
    return max(1, min(255, (duty * 255 + 5000) // 10000))


def low_minimum(proc, device) -> int:
    """min_level 30 (a minimum the dimming MCU shows above its range's 1 %): its value."""
    put(proc, device, 1, BALLAST, ATTR_MIN_LEVEL, 30)
    return (30 * 1000 + 127) // 254


def test_lamp_compensation_lowers_the_output_after_a_dim_down_only():
    """Open Dimmer 2.2.8's: after the light came down from full, the minimum goes out lower by
    comp x 40 of the duty (the dimming MCU taken as linear); on from dark at the minimum, none."""
    proc, device, _ = start()
    mn = low_minimum(proc, device)
    assert attr(device, 1, BALLAST, ATTR_LAMP_COMP) == 0          # the dump told it: none
    put(proc, device, 1, BALLAST, ATTR_LAMP_COMP, 10)
    assert attr(device, 1, BALLAST, ATTR_LAMP_COMP) == 10
    comp4 = ((10 * 1000 + 127) // 254 + 2) // 4                   # the module's value, / 4
    lit_at(proc, device, 254)
    move(device, 1)                                               # down to the minimum
    down = hk(frames(proc, device, 3000))
    assert falling(down) and down[-1] == duty_out(mn * 10 - comp4 * 40), (down, mn)
    off(proc, device, 3000)
    move(device, 1)                                               # on from dark at the minimum
    up = hk(frames(proc, device, 3000))
    assert up[-1] == duty_out(mn * 10) == hk_out(mn, mn=mn), up


def test_lamp_compensation_never_goes_below_the_range_minimum():
    proc, device, _ = start()
    low_minimum(proc, device)
    put(proc, device, 1, BALLAST, ATTR_LAMP_COMP, 63)
    lit_at(proc, device, 254)
    move(device, 1)
    down = hk(frames(proc, device, 3000))
    assert down[-1] == MIN and min(down) == MIN, down


def test_lamp_compensation_is_kept_per_channel():
    proc, device, _ = start()
    put(proc, device, 2, BALLAST, ATTR_LAMP_COMP, 20)
    frames(proc, device, 6000)
    proc.stop()
    proc, device, _ = start()
    assert [attr(device, ep, BALLAST, ATTR_LAMP_COMP) for ep in (1, 2)] == [0, 20]


# ---- the slow zones (R14, R26) ----------------------------------------------------------------


def test_the_slow_zones_are_settings_kept_per_channel():
    proc, device, _ = start()
    assert [attr(device, 1, BASIC, a) for a in (0xFF3C, 0xFF3D, 0xFF3E, 0xFF3F)] == [15, 15, 25, 25]
    put(proc, device, 1, BASIC, ATTR_SOFT_END + 1, 30)            # L2's off
    put(proc, device, 1, BASIC, ATTR_HOLD_END, 0)                 # L1's hold
    put(proc, device, 1, BASIC, ATTR_HOLD_END + 1, 150)           # at most 100
    assert [attr(device, 1, BASIC, a) for a in (0xFF3C, 0xFF3D, 0xFF3E, 0xFF3F)] == [15, 30, 0, 100]
    frames(proc, device, 6000)
    proc.stop()
    proc, device, _ = start()
    assert [attr(device, 1, BASIC, a) for a in (0xFF3C, 0xFF3D, 0xFF3E, 0xFF3F)] == [15, 30, 0, 100]


def test_a_hold_from_dark_with_no_slow_zone_rises_at_the_holds_rate():
    proc, device, _ = start()
    momentary(proc, device)
    put(proc, device, 1, BASIC, ATTR_HOLD_END, 0)
    device.press_button(S1)
    sent = run_t(proc, device, 1990)
    device.release_button(S1)
    levels = hk([f for _, f in sent])
    assert abs(levels[-1] - hk_out(model.hold_from_dark_at(2000, hold_pct=0))) <= 2, levels[-1]
    assert levels[-1] > hk_out(model.hold_from_dark_at(2000)) + 10


def hold_as_model(sent, start: int, mn: int = 10, mx: int = 1000, **kw) -> tuple[int, int]:
    """A lit hold up from value start (the L1 frames sent during it): ms from its second level's
    frame to its last one, and the model's (tests/hk_fader_model.py) between the same levels."""
    pts = [(t, f[5]) for t, f in sent if f[4] == 1]
    f = model.Fader(mn=mn, mx=mx, **kw)
    f.current = start
    f.hold(1)
    first: dict[int, int] = {}
    for i in range(3000):
        first.setdefault(hk_out(f.tick(), mn=mn, mx=mx), i * 10)
    (t0, l0), (t1, l1) = pts[1], pts[-1]
    return t1 - t0, first[l1] - first[l0]


def test_hold_time_is_for_the_range_from_min_to_max():
    """This module's meaning of hold_time (as in its builds before the fader; Open Dimmer's is for
    0..1000, hk_fader.h): a lit hold up over a narrowed range takes hold_time from its minimum to
    its maximum, not that range's share of it (here about half)."""
    proc, device, _ = start()
    momentary(proc, device)
    put(proc, device, 1, BALLAST, ATTR_MIN_LEVEL, 30)
    put(proc, device, 1, BALLAST, ATTR_MAX_LEVEL, 155)
    mn, mx = (30 * 1000 + 127) // 254, (155 * 1000 + 127) // 254
    lit_at(proc, device, 1)                           # at the minimum: the hold goes up
    device.press_button(S1)
    sent = run_t(proc, device, 6000)
    device.release_button(S1)
    assert hk([f for _, f in sent])[-1] == hk_out(mx, mn=mn, mx=mx)          # at the top
    took, expect = hold_as_model(sent, mn, mn=mn, mx=mx)                      # hold_time 4 s
    assert abs(took - expect) <= 40 and took > 3500, (took, expect)


def test_a_hold_time_written_while_lit_is_the_next_lit_holds():
    """The fader takes a setting at its write (Open Dimmer's apply), not at the next on / off or
    level: a lit hold after a hold_time write goes at the new pace."""
    proc, device, _ = start()
    momentary(proc, device)
    lit_at(proc, device, 40)                          # low: the hold goes up
    set_setting(proc, device, ATTR_HOLD, 10000)
    device.press_button(S1)
    sent = run_t(proc, device, HOLD_AFTER + 2000)
    device.release_button(S1)
    frames(proc, device, 300)
    took, expect = hold_as_model(sent, zcl_value(40), hold=10000)
    assert abs(took - expect) <= 40, (took, expect)


def hold_down_ms(proc, device, lvl: int) -> int:
    """From lit at lvl, a hold down to the minimum (the second of two, a correction): ms of it."""
    lit_at(proc, device, lvl)
    hold_for(proc, device, 300)                        # up (band 0: the far end)
    device.press_button(S1)                            # within 2 s: down
    sent = run_t(proc, device, 6000)
    device.release_button(S1)
    frames(proc, device, 300)
    lows = [t for t, f in sent if f[4] == 1 and f[5] == min(hk([g for _, g in sent]))]
    return lows[0]


def test_a_lit_hold_down_slows_near_the_minimum_but_not_with_the_custom_curve():
    """Going down a hold slows in hold_slow_zone (Open Dimmer's hold_fine); the custom curve is
    even, so its lit hold keeps its rate (open-dimmer phase.c phase_hold_start)."""
    proc, device, _ = start()
    momentary(proc, device)
    put(proc, device, 1, BASIC, ATTR_HOLD, 2000)
    slow = hold_down_ms(proc, device, 50)
    put(proc, device, 1, BASIC, ATTR_CURVE, CUSTOM)
    even = hold_down_ms(proc, device, 50)
    assert slow > even + 300, (slow, even)
