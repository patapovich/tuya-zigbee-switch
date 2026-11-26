"""The ZH-Z06 dimmers' virtual MCU (src/zigbee/hk_dimmer.c) on the dimmer endpoints' Open Dimmer
paths (dimmer_cluster.c DIMMER_OD_PATHS): dimmer_od_from() takes it as an Open Dimmer of build
HK_OD_BUILD (2.2.0's, 500; its settings: test_hk_dimmer_od_features.py) without it being one (no
identity, not "own"). So a
Stop, Step or Move during any of its fades acts from where the light is (the module asks it,
hk_dimmer_fading(); brightness_fade_time from there too), a level set while off (ExecuteIfOff) is
written for it to keep, the wall switch takes its own OnLevel, and its gestures drive the bindings
(remote mode, double / triple press actions; a hold's release ends the mirror's follow at once).
The virtual MCU takes min / max as Open Dimmer does: the other limit pushed along, both and the
level in the answer. tests/test_hk_dimmer_ts110e_build.py runs the main ones again on a stub
built with the TS110E's flags (no SECONDARY_MCU_IMAGE)."""
from test_hk_dimmer import (BUTTON, HOLD_AFTER, MIN, S1, S2, SWITCH_MOMENTARY, SWITCH_STATE, falling,
                            frames, hk, hk_out, level, move, onoff, quiet, rising, run_t,
                            set_switch_type, start, zcl_value)

from tests.zcl_consts import (
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_LEVEL_MOVE,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
    ZCL_CMD_LEVEL_STEP,
    ZCL_CMD_LEVEL_STOP,
    ZCL_CMD_ONOFF_OFF,
    ZCL_CMD_ONOFF_ON,
    ZCL_LEVEL_MOVE_DOWN,
)

LEVEL_CTRL = ZCL_CLUSTER_LEVEL_CONTROL
SWITCH_CFG = 0x0007
BALLAST = 0x0301
ATTR_OPTIONS, ATTR_ON_LEVEL, ATTR_ON_LEVEL_LOCAL, ATTR_FADE_TIME = 0x000F, 0x0011, 0xFF11, 0xFF12
ATTR_MIN, ATTR_MAX = 0x0010, 0x0011
ATTR_BIND_MODE, ATTR_DOUBLE, ATTR_TRIPLE = 0xFF12, 0xFF13, 0xFF14
BIND_OFF, BIND_MIRROR, BIND_REMOTE = 1, 2, 3
ACT_OFF = 2
ON_OFF = ZCL_CLUSTER_ON_OFF
OFF, ON, TOGGLE = 0x00, 0x01, 0x02
MOVE, MOVE_TO_LEVEL_WITH_ONOFF, MOVE_WITH_ONOFF, STOP_WITH_ONOFF = 0x01, 0x04, 0x05, 0x07
GAP = 400                                 # ms after the last release: single / double / triple


def lit(proc, device, lvl: int, ep: int = 1) -> None:
    """On at that level at once, settled."""
    move(device, lvl, ep=ep, tenths=0)
    frames(proc, device, 500)


def stop(device, ep: int = 1) -> None:
    device.call_zigbee_cmd(ep, LEVEL_CTRL, ZCL_CMD_LEVEL_STOP, b"")


def step_down(device, size: int, ep: int = 1, tenths: int = 0xFFFF) -> None:
    device.call_zigbee_cmd(ep, LEVEL_CTRL, ZCL_CMD_LEVEL_STEP,
                           bytes([ZCL_LEVEL_MOVE_DOWN, size]) + tenths.to_bytes(2, "little"))


def near(a: int, b: int, tol: int) -> bool:
    return abs(a - b) <= tol


# ---- a Stop, Step or Move during a fade, from where the light is (R09) ----------------------


def test_a_stop_during_a_timed_move_stops_the_light_where_it_is():
    """The light is halfway at 2 s of a 4 s move from 50 to 250: a Stop leaves it there (time 1
    and that level to the virtual MCU: at once, within a level of the frame before), and the
    level shown is where it stopped, not the move's end."""
    proc, device, _ = start()
    lit(proc, device, 50)
    move(device, 250, tenths=40)
    up = hk(frames(proc, device, 2000))
    stop(device)
    after = hk(frames(proc, device, 3000))
    assert rising(up) and up[-1] < hk_out(zcl_value(250)) - 20, up
    assert len(after) <= 1 and all(near(x, up[-1], 3) for x in after), (up[-1], after)
    assert near(level(device), 150, 6), level(device)


def test_a_step_during_a_timed_move_steps_from_where_the_light_is():
    """A Step down 20 at the middle of a move up to 250 goes down from there (about 130), never up
    towards the move's end (230 from its end)."""
    proc, device, _ = start()
    lit(proc, device, 50)
    move(device, 250, tenths=40)
    up = hk(frames(proc, device, 2000))
    step_down(device, 20)
    after = hk(frames(proc, device, 2000))
    assert near(level(device), 130, 6), level(device)
    assert after and max(after) <= up[-1] + 2 and falling(after), (up[-1], after)
    assert after[-1] == hk_out(zcl_value(level(device)))


def test_a_move_during_a_timed_move_sets_out_from_where_the_light_is():
    """Its first step (one step period after it, the timed move runs on until then) goes down
    from where the light is then, not from the timed move's end (up)."""
    proc, device, _ = start()
    lit(proc, device, 50)
    move(device, 250, tenths=40)
    up = hk(frames(proc, device, 2000))
    device.call_zigbee_cmd(1, LEVEL_CTRL, ZCL_CMD_LEVEL_MOVE, bytes([ZCL_LEVEL_MOVE_DOWN, 40]))
    first = hk(frames(proc, device, 300))
    after = hk(frames(proc, device, 1200))
    device.call_zigbee_cmd(1, LEVEL_CTRL, ZCL_CMD_LEVEL_STOP, b"")
    assert rising(up) and after and falling(after) and max(after) <= max(first + up), (first, after)
    assert after[-1] < up[-1] and level(device) < 150, (up[-1], after[-1], level(device))


def test_a_stop_in_a_move_longer_than_65_s_stops_it_too():
    """The virtual MCU takes times up to 600 s: the module keeps track of them as long."""
    proc, device, _ = start()
    lit(proc, device, 20)
    move(device, 254, tenths=1000)                          # 100 s
    up = hk([f for _, f in quiet(proc, device, 70000)])
    stop(device)
    after = hk(frames(proc, device, 2000))
    assert rising(up) and len(after) <= 1 and all(near(x, up[-1], 3) for x in after), (up[-1], after)
    assert near(level(device), 20 + 234 * 7 // 10, 6), level(device)


def test_a_stop_after_a_hold_in_a_timed_move_leaves_the_light_where_the_hold_left_it():
    """A wall switch hold takes the light from the timed move (its reports are the virtual MCU's
    word): a Stop after it has nothing to stop and moves nothing."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    lit(proc, device, 40)
    move(device, 254, tenths=200)                           # 20 s
    frames(proc, device, 500)
    device.press_button(S1)
    held = hk(frames(proc, device, HOLD_AFTER + 1000))
    device.release_button(S1)
    held += hk(frames(proc, device, 2000))                  # the hold's last bit (the release's
    shown = level(device)                                   # debounce), then at rest
    stop(device)
    after = hk(frames(proc, device, 2000))
    assert len(set(held)) > 2, held                         # the hold moved the light
    assert all(near(x, held[-1], 1) for x in after), (held[-1], after)
    assert near(level(device), shown, 1)


def test_a_hold_during_a_slow_timed_rise_dims_from_where_the_light_is():
    """Its way is picked by where the light is, still low on a rise to the top: up (Open Dimmer's
    dimmer_hold_begin(), phase_current()), not down from the rise's end."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    lit(proc, device, 40)
    move(device, 254, tenths=200)                           # 20 s
    before = hk(frames(proc, device, 500))
    device.press_button(S1)
    held = hk(frames(proc, device, HOLD_AFTER + 1000))
    device.release_button(S1)
    frames(proc, device, 500)
    assert before[-1] < hk_out(zcl_value(100)), before      # below the middle at the press
    assert rising(held) and held[0] >= before[-1], (before[-1], held)
    assert level(device) > 100, level(device)


# A fade the module did not time (no time given: the fade settings; from dark), then a timed move
# begun during it: the virtual MCU sets out from where its fade is, and a Stop, Step or Move in
# the timed move acts from where the light is (hk_dimmer_fading()), with no jump.


def timed_during_a_fade(proc, device, from_dark: bool) -> list[int]:
    """From dark at the fade settings to 200, or lit at 254 down to 20 by them; 0.3 s later a
    10 s move to 254, 2 s of it. The frames of the timed move."""
    if not from_dark:
        lit(proc, device, 254)
    move(device, 200 if from_dark else 20)
    frames(proc, device, 300)
    move(device, 254, tenths=100)
    return hk(frames(proc, device, 2000))


def test_a_stop_in_a_timed_move_begun_during_a_fade_of_the_settings_stops_it_where_it_is():
    proc, device, _ = start()
    up = timed_during_a_fade(proc, device, from_dark=False)
    stop(device)
    after = hk(frames(proc, device, 1500))
    assert rising(up) and hk_out(zcl_value(20)) + 20 < up[0] < hk_out(1000) - 20, up  # mid-fade
    assert all(near(x, up[-1], 2) for x in after), (up[-1], after)
    assert near(hk_out(zcl_value(level(device))), up[-1], 2), (level(device), up[-1])


def test_a_stop_in_a_timed_move_begun_during_a_fade_from_dark_stops_it_where_it_is():
    proc, device, _ = start()
    up = timed_during_a_fade(proc, device, from_dark=True)
    stop(device)
    after = hk(frames(proc, device, 1500))
    assert rising(up) and up[-1] < hk_out(zcl_value(150)), up    # still low, from the fade in
    assert all(near(x, up[-1], 2) for x in after), (up[-1], after)
    assert near(hk_out(zcl_value(level(device))), up[-1], 2), (level(device), up[-1])


def level_at(out: int) -> int:
    """The level whose HK level is nearest to out."""
    return min(range(1, 255), key=lambda lvl: abs(hk_out(zcl_value(lvl)) - out))


def test_a_step_in_a_timed_move_begun_during_a_fade_of_the_settings_steps_from_where_it_is():
    proc, device, _ = start()
    up = timed_during_a_fade(proc, device, from_dark=False)
    shown = level(device)                                         # the move's end, 254
    step_down(device, 20)
    after = hk(frames(proc, device, 2000))
    assert shown == 254 and near(level(device), level_at(up[-1]) - 20, 2), (up[-1], level(device))
    assert after and falling(after) and after[0] <= up[-1] + 1, (up[-1], after)
    assert after[-1] == hk_out(zcl_value(level(device)))


def test_a_stop_in_a_fade_of_the_settings_stops_it_too():
    """No timed move at all: the virtual MCU's own fade (fade_on_time from dark) stops as well."""
    proc, device, _ = start()
    device.write_zigbee_attr(1, 0x0000, 0xFF32, 10000)            # fade_on_time 10 s
    frames(proc, device, 300)
    move(device, 254)
    up = hk(frames(proc, device, 3000))
    stop(device)
    after = hk(frames(proc, device, 2000))
    assert rising(up) and up[-1] < hk_out(800), up
    assert all(near(x, up[-1], 2) for x in after), (up[-1], after)
    assert near(hk_out(zcl_value(level(device))), up[-1], 2), (level(device), up[-1])


# The same level again during a timed move (a scene or an automation that fires twice): the
# virtual MCU keeps the move as it runs (as Open Dimmer's dim_fader_set_timed()), and so do a Stop
# and a Move after its end: nothing to stop, a Move from its end.


def same_level_twice(proc, device) -> list[int]:
    """50 -> 250 in 10 s, the same command again at 5 s; at 12 s. The frames of the move."""
    lit(proc, device, 50)
    move(device, 250, tenths=100)
    up = hk(frames(proc, device, 5000))
    move(device, 250, tenths=100)
    return up + hk(frames(proc, device, 7000))


def test_the_same_level_again_in_a_timed_move_keeps_it_and_a_stop_after_it_moves_nothing():
    proc, device, _ = start()
    up = same_level_twice(proc, device)
    stop(device)
    after = hk(frames(proc, device, 2000))
    assert rising(up) and up[-1] == hk_out(zcl_value(250)), up     # done at 10 s, not 15 s
    assert after == [] and level(device) == 250, (after, level(device))


def test_the_same_level_again_in_a_timed_move_and_a_move_after_it_sets_out_from_its_end():
    proc, device, _ = start()
    up = same_level_twice(proc, device)
    device.call_zigbee_cmd(1, LEVEL_CTRL, ZCL_CMD_LEVEL_MOVE, bytes([ZCL_LEVEL_MOVE_DOWN, 40]))
    down = hk(frames(proc, device, 600))                           # two steps of 12
    stop(device)
    assert down and falling(down) and down[0] >= up[-1] - 2, (up[-1], down)
    assert 250 - 24 - 12 <= level(device) <= 250 - 12, level(device)


# ---- a network Move on the virtual MCU -------------------------------------------------------


def test_a_move_runs_at_its_rate_and_a_stop_ends_it_where_the_light_is():
    """A step of 12 every 0.3 s (40 levels/s), each a 0.3 s fade: the virtual MCU's fade of a step
    is no "timed move" the next step sets out from (it would lose a level now and then)."""
    proc, device, _ = start()
    lit(proc, device, 50)
    device.call_zigbee_cmd(1, LEVEL_CTRL, ZCL_CMD_LEVEL_MOVE, bytes([0x00, 40]))
    up = hk(frames(proc, device, 3000 + 200))                     # 10 steps, 2/3 of the last
    shown = level(device)                                         # the last step's end
    stop(device)
    after = hk(frames(proc, device, 1000))
    assert rising(up) and shown == 50 + 10 * 12, shown
    assert all(near(x, up[-1], 2) for x in after), (up[-1], after)
    assert 50 + 9 * 12 + 4 <= level(device) <= 50 + 10 * 12 - 2, level(device)


# ---- brightness_fade_time (R11) --------------------------------------------------------------


def test_brightness_fade_time_is_the_time_for_the_whole_range():
    proc, device, _ = start()
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_FADE_TIME, 40)       # 4 s
    lit(proc, device, 254)
    move(device, 127)
    sent = run_t(proc, device, 3000)
    levels = hk([f for _, f in sent])
    done_at = next(t for t, f in sent if f[4] == 1 and f[5] == levels[-1])
    assert falling(levels) and levels[-1] == hk_out(zcl_value(127))
    assert 1800 <= done_at <= 2200, done_at                          # half the range: 2 s


def test_a_change_during_a_brightness_fade_takes_its_share_from_where_the_light_is():
    """4 s for the whole range: on the way down from full, at 1 s (about 3/4 up), a change to 200
    (a little above) takes about 0.15 s, not the 3.2 s from the old target at the bottom."""
    proc, device, _ = start()
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_FADE_TIME, 40)
    lit(proc, device, 254)
    move(device, 1)
    frames(proc, device, 1000)
    move(device, 200)
    sent = run_t(proc, device, 3000)
    levels = hk([f for _, f in sent])
    done_at = next(t for t, f in sent if f[4] == 1 and f[5] == levels[-1])
    assert levels[-1] == hk_out(zcl_value(200)) and done_at <= 400, (done_at, levels)


def test_a_stop_during_a_brightness_fade_stops_it():
    proc, device, _ = start()
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_FADE_TIME, 100)      # 10 s
    lit(proc, device, 254)
    move(device, 1)
    down = hk(frames(proc, device, 3000))
    stop(device)
    after = hk(frames(proc, device, 3000))
    assert falling(down) and len(after) <= 1 and all(near(x, down[-1], 3) for x in after)
    assert 150 < level(device) < 200, level(device)


def test_unset_brightness_fade_time_is_open_dimmers_own_rate():
    """0xFFFF (none): no time goes out, the change takes Open Dimmer's own rate (the whole range
    in 2 s), as on a ZDMS16-2 with Open Dimmer."""
    proc, device, _ = start()
    lit(proc, device, 254)
    move(device, 127)
    sent = run_t(proc, device, 2000)
    levels = hk([f for _, f in sent])
    done_at = next(t for t, f in sent if f[4] == 1 and f[5] == levels[-1])
    assert 950 <= done_at <= 1050, done_at


# ---- a level set while off (ExecuteIfOff, R07) ------------------------------------------------


def off_at(proc, device, lvl: int) -> None:
    """Lit at lvl, then off and dark; ExecuteIfOff set."""
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_OPTIONS, 1)
    lit(proc, device, lvl)
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_OFF)
    frames(proc, device, 2000)


def move_while_off(device, lvl: int) -> None:
    device.call_zigbee_cmd(1, LEVEL_CTRL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL, bytes([lvl, 0xFF, 0xFF]))


def test_a_level_while_off_is_kept_and_the_light_stays_dark():
    proc, device, _ = start()
    off_at(proc, device, 200)
    move_while_off(device, 60)
    assert all(x == 0 for x in hk(frames(proc, device, 2000)))
    assert onoff(device) == 0 and level(device) == 60
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_ON)
    levels = hk(frames(proc, device, 2000))
    assert levels[0] == MIN and rising(levels) and max(levels) == hk_out(zcl_value(60)), levels


def test_a_level_set_while_off_survives_a_power_cut():
    """Written to the virtual MCU, which keeps it in its NV, not only in the module's RAM."""
    proc, device, _ = start()
    off_at(proc, device, 200)
    move_while_off(device, 60)
    frames(proc, device, 6000)                                # its save (5 s)
    proc.stop()                                               # a power cut
    proc, device, _ = start()
    assert onoff(device) == 0 and level(device) == 60
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_ON)
    assert hk(frames(proc, device, 2000))[-1] == hk_out(zcl_value(60))


def test_the_wall_switch_comes_on_at_the_level_set_while_off():
    proc, device, _ = start()
    off_at(proc, device, 200)
    frames(proc, device, 6000)                                # past the quick re-on window
    move_while_off(device, 60)
    frames(proc, device, 300)
    device.press_button(S1)                                   # toggle switch: on
    levels = hk(frames(proc, device, 2000))
    assert max(levels) == levels[-1] == hk_out(zcl_value(60)) and level(device) == 60


def test_without_execute_if_off_a_level_while_off_is_ignored():
    proc, device, _ = start()
    off_at(proc, device, 200)
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_OPTIONS, 0)
    move_while_off(device, 60)
    frames(proc, device, 300)
    assert level(device) == 200
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_ON)
    assert hk(frames(proc, device, 2000))[-1] == hk_out(zcl_value(200))


# ---- the wall switch's OnLevel (R05) ---------------------------------------------------------


def test_the_wall_switch_goes_to_its_own_on_level_and_a_network_on_to_on_level():
    proc, device, _ = start()
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_ON_LEVEL, 60)
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_ON_LEVEL_LOCAL, 200)
    frames(proc, device, 300)
    device.press_button(S1)                                   # toggle switch: on
    assert hk(frames(proc, device, 2500))[-1] == hk_out(zcl_value(200)) and level(device) == 200
    device.release_button(S1)                                 # off
    frames(proc, device, 2500)
    device.call_zigbee_cmd(1, ON_OFF, ZCL_CMD_ONOFF_ON)
    assert hk(frames(proc, device, 2500))[-1] == hk_out(zcl_value(60)) and level(device) == 60


def test_a_quick_re_on_at_the_wall_switch_brings_the_level_back_not_its_on_level():
    proc, device, _ = start()
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_ON_LEVEL_LOCAL, 200)
    lit(proc, device, 120)
    device.press_button(S1)                                   # off by the wall switch
    frames(proc, device, 2000)
    device.release_button(S1)                                 # on again within 5 s
    assert hk(frames(proc, device, 2000))[-1] == hk_out(zcl_value(120)) and level(device) == 120


def test_the_module_button_goes_to_the_wall_switchs_on_level():
    proc, device, _ = start()
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_ON_LEVEL_LOCAL, 200)
    device.press_button(BUTTON)
    device.release_button(BUTTON)
    frames(proc, device, 2500)
    assert onoff(device, 1) == onoff(device, 2) == 1 and level(device, 1) == 200


def test_a_hold_from_dark_with_the_wall_switchs_on_level_rises_from_the_minimum():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_ON_LEVEL_LOCAL, 200)
    device.press_button(S1)
    sent = frames(proc, device, HOLD_AFTER + 1000)
    device.release_button(S1)
    frames(proc, device, 300)
    levels = hk(sent)
    assert levels[0] == MIN and rising(levels) and max(levels) < hk_out(zcl_value(200))
    assert level(device) < 100


# ---- min / max as Open Dimmer takes them (R03) ------------------------------------------------


def limits(device, ep: int = 1) -> tuple[int, int]:
    return (int(device.read_zigbee_attr(ep, BALLAST, ATTR_MIN)),
            int(device.read_zigbee_attr(ep, BALLAST, ATTR_MAX)))


def set_limit(proc, device, attr: int, value: int, ep: int = 1, ms: int = 300) -> list[int]:
    """The HK levels sent for the next ms."""
    device.write_zigbee_attr(ep, BALLAST, attr, value)
    return hk(frames(proc, device, ms))


def test_a_minimum_above_the_maximum_pushes_the_maximum_along_and_back():
    proc, device, _ = start()
    set_limit(proc, device, ATTR_MAX, 100)
    set_limit(proc, device, ATTR_MIN, 200)
    assert limits(device) == (200, 200)
    set_limit(proc, device, ATTR_MAX, 50)
    assert limits(device) == (50, 50)


def test_equal_limits_are_a_fixed_level():
    proc, device, _ = start()
    set_limit(proc, device, ATTR_MIN, 100)
    set_limit(proc, device, ATTR_MAX, 100)
    assert limits(device) == (100, 100)
    fixed = hk_out(394, mn=394, mx=394)                       # level 100 of 254: 394 of 1000
    lit(proc, device, 10)
    assert hk(frames(proc, device, 500)) in ([], [fixed]) and level(device) in (10, 100)
    move(device, 254, tenths=0)
    assert hk(frames(proc, device, 500)) in ([], [fixed])


def test_a_minimum_raised_over_the_level_and_back_shows_the_level_the_light_has():
    """Raised over the level the light goes up to it (kept there after); lowered again it stays,
    and the level shown is that one, not the one from before the raise."""
    proc, device, _ = start()
    lit(proc, device, 100)
    up = set_limit(proc, device, ATTR_MIN, 200, ms=2000)       # 787 of 1000
    assert up and up[-1] == hk_out(787, mn=787) and level(device) == 1
    assert set_limit(proc, device, ATTR_MIN, 3, ms=1000) in ([], [hk_out(787)])
    assert near(level(device), 200, 1), level(device)


def test_a_maximum_lowered_under_the_level_takes_the_light_down():
    proc, device, _ = start()
    lit(proc, device, 254)
    down = set_limit(proc, device, ATTR_MAX, 100, ms=2000)     # 394 of 1000
    assert down and down[-1] == hk_out(394, mx=394) and level(device) == 254


# ---- the wall switch's bindings (R36: mirror, R37: remote) -----------------------------------


def bound(device) -> list[tuple]:
    from test_dimmer_bindings import sent
    return sent(device)


def bind_setup(proc, device, mode: int | None = None, momentary: bool = True) -> None:
    if momentary:
        set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    if mode is not None:
        device.write_zigbee_attr(1, SWITCH_CFG, ATTR_BIND_MODE, mode)
    frames(proc, device, 300)
    bound(device)


def tap(proc, device, pin: str = S1, gap: int = 100) -> None:
    device.press_button(pin)
    frames(proc, device, 60)
    device.release_button(pin)
    frames(proc, device, gap)


def test_the_wall_switch_and_the_module_button_mirror_on_and_off():
    proc, device, _ = start()
    bind_setup(proc, device, momentary=False)
    device.press_button(S1)                                   # toggle switch: on
    frames(proc, device, 100)
    assert bound(device) == [(3, ON_OFF, ON, b"")]
    device.release_button(S1)
    frames(proc, device, 100)
    assert bound(device) == [(3, ON_OFF, OFF, b"")]
    device.press_button(S2)
    frames(proc, device, 100)
    assert bound(device) == [(4, ON_OFF, ON, b"")]
    tap(proc, device, BUTTON)                                 # L2 on: both off (L1 is)
    assert bound(device) == [(4, ON_OFF, OFF, b"")]
    tap(proc, device, BUTTON)                                 # none on: both on
    assert sorted(bound(device)) == [(3, ON_OFF, ON, b""), (4, ON_OFF, ON, b"")]
    move(device, 200)                                         # a command of ours: nothing
    device.call_zigbee_cmd(2, ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    assert bound(device) == []


def test_a_holds_release_ends_the_bound_lights_follow_at_once():
    """Its release gesture after the level: Stop and the level at once, not 0.8 s later."""
    proc, device, _ = start()
    bind_setup(proc, device)
    lit(proc, device, 40)
    bound(device)
    device.press_button(S1)
    frames(proc, device, HOLD_AFTER + 1200)
    during = bound(device)
    device.release_button(S1)
    frames(proc, device, 100)
    after = bound(device)
    assert during and all(c[:3] == (3, LEVEL_CTRL, MOVE_WITH_ONOFF) and c[3][0] == 0 for c in during)
    assert [c[:3] for c in after] == [(3, LEVEL_CTRL, STOP_WITH_ONOFF),
                                      (3, LEVEL_CTRL, MOVE_TO_LEVEL_WITH_ONOFF)], after
    assert after[-1][3][0] == level(device)
    frames(proc, device, 1500)
    assert bound(device) == []


def test_a_hold_from_dark_with_an_on_level_goes_to_the_bound_lights_from_the_bottom():
    proc, device, _ = start()
    bind_setup(proc, device)
    device.write_zigbee_attr(1, LEVEL_CTRL, ATTR_ON_LEVEL, 200)
    frames(proc, device, 300)
    device.press_button(S1)
    frames(proc, device, HOLD_AFTER + 1500)
    device.release_button(S1)
    frames(proc, device, 100)
    out = bound(device)
    assert out[0] == (3, ON_OFF, ON, b"")
    moves = [c for c in out if c[1] == LEVEL_CTRL and c[2] in (MOVE, MOVE_WITH_ONOFF)]
    assert moves and all(c[2] == MOVE_WITH_ONOFF and c[3][0] == 0 for c in moves), out
    assert [c[:3] for c in out[-2:]] == [(3, LEVEL_CTRL, STOP_WITH_ONOFF),
                                        (3, LEVEL_CTRL, MOVE_TO_LEVEL_WITH_ONOFF)], out
    assert out[-1][3][0] == level(device) < 200


def test_a_hold_soon_after_a_level_command_is_followed_too():
    proc, device, _ = start()
    bind_setup(proc, device)
    lit(proc, device, 200)
    move(device, 60)
    frames(proc, device, 1000)                                # inside 3 s of the command
    bound(device)
    device.press_button(S1)
    frames(proc, device, HOLD_AFTER + 1200)
    device.release_button(S1)
    frames(proc, device, 100)
    out = bound(device)
    assert any(c[2] == MOVE_WITH_ONOFF for c in out) and out[-1][2] == MOVE_TO_LEVEL_WITH_ONOFF


def test_remote_a_single_press_toggles_the_bound_lights_and_the_channel_acts_too():
    proc, device, _ = start()
    bind_setup(proc, device, BIND_REMOTE)
    tap(proc, device)
    assert bound(device) == [] and onoff(device) == 1         # the gap is not over yet
    frames(proc, device, GAP)
    assert bound(device) == [(3, ON_OFF, TOGGLE, b"")]
    tap(proc, device, gap=GAP + 100)
    assert bound(device) == [(3, ON_OFF, TOGGLE, b"")] and onoff(device) == 0


def test_remote_a_double_press_sends_full_and_a_triple_its_action():
    proc, device, _ = start()
    bind_setup(proc, device, BIND_REMOTE)
    tap(proc, device)
    tap(proc, device, gap=GAP + 100)
    assert bound(device) == [(3, LEVEL_CTRL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([254, 4, 0]))]
    for _ in range(3):
        tap(proc, device)
    frames(proc, device, GAP)
    assert bound(device) == []                                # triple: none (the default)
    device.write_zigbee_attr(1, SWITCH_CFG, ATTR_TRIPLE, ACT_OFF)
    for _ in range(3):
        tap(proc, device)
    frames(proc, device, GAP)
    assert bound(device) == [(3, ON_OFF, OFF, b"")]


def test_remote_a_hold_moves_the_bound_lights_and_its_release_stops_them():
    proc, device, _ = start()
    bind_setup(proc, device, BIND_REMOTE)
    lit(proc, device, 120)
    bound(device)
    for direction in (0, 1):                                  # up first, then down by turns
        device.press_button(S1)
        frames(proc, device, HOLD_AFTER + 600)
        held = bound(device)
        device.release_button(S1)
        frames(proc, device, 100)
        assert held == [(3, LEVEL_CTRL, MOVE_WITH_ONOFF if direction == 0 else MOVE,
                         bytes([direction, 50]))], held
        assert bound(device) == [(3, LEVEL_CTRL, STOP_WITH_ONOFF, b"")]
        frames(proc, device, GAP + 100)
        assert bound(device) == []                            # no single after a hold


def test_bind_mode_off_sends_nothing():
    proc, device, _ = start()
    bind_setup(proc, device, BIND_OFF)
    tap(proc, device, gap=GAP + 100)
    device.press_button(S1)
    frames(proc, device, HOLD_AFTER + 600)
    device.release_button(S1)
    frames(proc, device, GAP + 100)
    assert bound(device) == [] and onoff(device) == 1


def test_remote_with_a_toggle_switch_sends_nothing_mirror_its_on_and_off():
    """A toggle or state switch has no gestures in the normal mode (as Open Dimmer)."""
    proc, device, _ = start()
    bind_setup(proc, device, BIND_REMOTE, momentary=False)
    device.press_button(S1)
    frames(proc, device, GAP + 100)
    assert bound(device) == [] and onoff(device) == 1
    device.write_zigbee_attr(1, SWITCH_CFG, ATTR_BIND_MODE, BIND_MIRROR)
    device.release_button(S1)
    frames(proc, device, 100)
    assert bound(device) == [(3, ON_OFF, OFF, b"")]


# ---- following the other channel's wall switch (R38) ------------------------------------------


def follow_l1(device, on_level: int = 120, off_level: int = 0) -> None:
    for attr, value in ((0xFF17, 1), (0xFF18, on_level), (0xFF19, off_level)):
        device.write_zigbee_attr(2, SWITCH_CFG, attr, value)


def test_a_follower_goes_to_its_on_level_at_a_hold_from_dark_and_not_along_the_hold():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    follow_l1(device)
    device.press_button(S1)
    sent = frames(proc, device, HOLD_AFTER + 1500)
    device.release_button(S1)
    sent += frames(proc, device, 1500)
    l2 = hk(sent, ch=2)
    assert rising(hk(sent, ch=1)) and l2[-1] == hk_out(zcl_value(120)) and max(l2) == l2[-1]
    assert onoff(device, 2) == 1 and level(device, 2) == 120


def test_the_module_button_is_followed_like_the_wall_switch():
    proc, device, _ = start()
    follow_l1(device, off_level=0)
    tap(proc, device, BUTTON, gap=2000)                        # both on: L2 at its follow level
    assert onoff(device, 1) == onoff(device, 2) == 1 and level(device, 2) == 120
    tap(proc, device, BUTTON, gap=2000)                        # both off
    assert onoff(device, 1) == onoff(device, 2) == 0


def test_a_state_switch_is_followed():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_STATE)
    follow_l1(device, off_level=30)
    device.press_button(S1)                                    # closed: on
    frames(proc, device, 2000)
    assert onoff(device, 2) == 1 and level(device, 2) == 120
    device.release_button(S1)                                  # open: off; L2 dims to 30
    frames(proc, device, 2000)
    assert onoff(device, 1) == 0 and onoff(device, 2) == 1 and level(device, 2) == 30
