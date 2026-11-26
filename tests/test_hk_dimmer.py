"""The ZH-Z06 dimmers (TS110E _TZ3210_3mpwqzuu): the virtual MCU (src/zigbee/hk_dimmer.c) between the
dimmer endpoints and the factory dimming MCU, which only takes one-way 12-byte level frames at 9600
baud (docs/ts110e_hk_protocol.md). The stub's UART capture is what goes to that MCU. The module
fades itself with Open Dimmer's fader (src/zigbee/hk_fader.c: frames with ramp 0, a frame gap apart,
tests/hk_fader_model.py for the timings); a fade time of 0 changes at once with the MCU's own
ramp."""
import os
import struct

import hk_fader_model as model

from client import StubProc
from conftest import DEBOUNCE_MS, Device
from tuya_mcu import pump, take_tx

from tests.zcl_consts import (
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CMD_ONOFF_OFF,
    ZCL_CMD_ONOFF_ON,
)

ZCL_ATTR_CURRENT_LEVEL = 0x0000
ZCL_ATTR_ONOFF = 0x0000
ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG = 0x0007
ZCL_CLUSTER_BALLAST = 0x0301
ZCL_CLUSTER_BASIC = 0x0000
ZCL_ATTR_SWITCH_TYPE = 0x0000
ZCL_ATTR_BALLAST_MIN = 0x0010
ZCL_ATTR_START_UP_ON_OFF = 0x4003
SWITCH_STATE, SWITCH_MOMENTARY = 1, 2

TS110E = "3mpwqzuu;TS110E-ZH-Z06;DM2;WB1B7;Y9600;HKB4C0C3C2;"
# The settings per channel, as device_db builds them: ramp, fade on, fade off, hold.
TS110E_DP_CONFIG = "70V30;71V31;72V32;73V33;74V34;75V35;76V36;77V37;78E38;79E39;"
ATTR_RAMP, ATTR_FADE_ON, ATTR_FADE_OFF, ATTR_HOLD = 0xFF30, 0xFF32, 0xFF34, 0xFF36  # + 1: L2
ATTR_SOFT_END, ATTR_HOLD_END = 0xFF3C, 0xFF3E      # the slow zones, the module's own (+ 1: L2)
S1, S2, BUTTON, LED = "B4", "C0", "C3", "C2"
MIN = 3                                   # the dimming MCU's level of the minimum, 10 of 1000

HOLD_AFTER = 400                          # ms a momentary press is held before it dims (Open Dimmer's)
RANGE = bytes.fromhex("55AA00FE000503" "03FF03FF")
RANGE += bytes([sum(RANGE) % 256])
STEP = 10                                 # ms between looks at the UART
PRESS_STEP = DEBOUNCE_MS + 10             # ms Device.press_button / release_button step


def level_frame(ch: int, level: int, other: int = 0, ramp: int = 0) -> bytes:
    """A frame for channel ch; both levels are in it (other = the other channel's)."""
    f = bytearray([0x00, 0x00, 0xFF, 0x55, ch, 0x00, 0x00, ramp >> 8, ramp & 0xFF, 0x0A, 0x00, 0x00])
    f[4 + ch] = level
    f[4 + (3 - ch)] = other
    return bytes(f)


def split(raw: bytes) -> list[bytes]:
    out, i = [], 0
    while i + 12 <= len(raw):
        out.append(raw[i:i + 12])
        i += 12
    assert i == len(raw), f"stray bytes: {raw[i:].hex()}"
    return out


def run_t(proc: StubProc, device: Device, ms: int) -> list[tuple[int, bytes]]:
    """Let time pass; the frames to the dimming MCU with the time (ms from now) they went."""
    out, t = [], 0
    for _ in range(max(1, ms // STEP)):
        device.step_time(STEP)
        t += STEP
        pump(proc, 2)
        out += [(t, f) for f in split(take_tx(proc))]
    return out


def frames(proc: StubProc, device: Device, ms: int) -> list[bytes]:
    return [f for _, f in run_t(proc, device, ms)]


def hk(sent: list[bytes], ch: int = 1) -> list[int]:
    return [f[4 + ch] for f in sent if f[2:4] == b"\xFF\x55" and f[4] == ch]


def ramps(sent: list[bytes], ch: int = 1) -> list[bytes]:
    return [f[7:9] for f in sent if f[2:4] == b"\xFF\x55" and f[4] == ch]


def start(dp_config: str = TS110E_DP_CONFIG) -> tuple[StubProc, Device, list[bytes]]:
    proc = StubProc(device_config=TS110E, dp_config=dp_config).start()
    device = Device(proc)
    boot = frames(proc, device, 5000)
    return proc, device, boot


def onoff(device: Device, ep: int = 1) -> int:
    return int(device.read_zigbee_attr(ep, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF))


def level(device: Device, ep: int = 1) -> int:
    return int(device.read_zigbee_attr(ep, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_ATTR_CURRENT_LEVEL))


def setting(device: Device, attr: int, ch: int = 1) -> int:
    return int(device.read_zigbee_attr(1, ZCL_CLUSTER_BASIC, attr + ch - 1))


def set_setting(proc: StubProc, device: Device, attr: int, value: int, ch: int = 1) -> None:
    device.write_zigbee_attr(1, ZCL_CLUSTER_BASIC, attr + ch - 1, value)
    run_t(proc, device, 300)


def move(device: Device, lvl: int, ep: int = 1, tenths: int = 0xFFFF) -> None:
    """Move to Level with On/Off; tenths: its transition time (0xFFFF: none given, the module's
    fade settings; 0: at once)."""
    device.call_zigbee_cmd(ep, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
                           bytes([lvl]) + tenths.to_bytes(2, "little"))


def set_switch_type(proc: StubProc, device: Device, ep: int, value: int) -> None:
    device.write_zigbee_attr(ep, ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG, ZCL_ATTR_SWITCH_TYPE, value)
    run_t(proc, device, 300)
    assert int(device.read_zigbee_attr(ep, ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG, ZCL_ATTR_SWITCH_TYPE)) == value


# ---- the output, as hk_dimmer.c computes it (Open Dimmer's dim_curve / dim_output) ----------

CURVES = {
    1: [0, 35, 69, 105, 148, 203, 269, 348, 442, 550, 676, 819, 981, 1163, 1367, 1592, 1842, 2116,
        2416, 2744, 3099, 3484, 3900, 4347, 4828, 5342, 5892, 6479, 7103, 7766, 8469, 9213, 10000],
    2: [0, 39, 118, 227, 359, 513, 687, 879, 1088, 1314, 1555, 1811, 2082, 2366, 2664, 2975, 3299,
        3635, 3983, 4343, 4714, 5097, 5491, 5896, 6311, 6737, 7173, 7620, 8076, 8543, 9019, 9505, 10000],
}
LINEAR, CURVE_LED, LED_SOFT, CUSTOM = 0, 1, 2, 6


def dim_curve(curve: int, x: int, custom=None) -> int:
    if x == 0:
        return 0
    if x >= 1000:
        return 10000
    if curve == LINEAR:
        return x * 10
    k = custom if (curve == CUSTOM and custom) else CURVES[LED_SOFT if curve == CUSTOM else curve]
    n = len(k)
    pos = x * (n - 1)
    seg, frac = pos // 1000, pos % 1000
    v = k[seg] + ((k[seg + 1] - k[seg]) * frac + 500) // 1000
    return max(v, 1)


def hk_out(value: int, curve: int = LED_SOFT, mn: int = 10, mx: int = 1000, custom=None) -> int:
    value = min(max(value, mn), mx)
    if curve == LINEAR or mx == mn:
        duty = value * 10
    else:
        span = mx - mn
        pos = ((value - mn) * 1000 + span // 2) // span
        shape = dim_curve(curve, pos, custom) if pos else 0
        duty = mn * 10 + (shape * span + 500) // 1000
    return max(1, min(255, (duty * 255 + 5000) // 10000))


def zcl_value(level: int, mn: int = 10, mx: int = 1000) -> int:
    """The dimmer endpoint's level (1..254) as the virtual MCU gets it (min..max scaled)."""
    return mn + ((max(1, min(254, level)) - 1) * (mx - mn) + 126) // 253


def rising(levels: list[int]) -> bool:
    return levels == sorted(levels) and len(set(levels)) > 2


def falling(levels: list[int]) -> bool:
    return levels == sorted(levels, reverse=True) and len(set(levels)) > 2


# ---- the link -------------------------------------------------------------------------------


def test_nothing_but_hk_frames_goes_on_the_wire():
    proc, device, boot = start()
    # The Tuya link to the virtual MCU (version query, query-all) stays inside: the dimming MCU
    # only ever gets the range command and level frames.
    assert all(f == RANGE or f[2:4] == b"\xFF\x55" for f in boot)


def test_boot_sends_the_range_and_both_channels_twice():
    proc, device, boot = start()
    # Power-on default: off. Once after the dimming MCU is up, once more a little later.
    expected = [RANGE, level_frame(1, 0), level_frame(2, 0)]
    assert boot == expected + expected


def test_every_frame_carries_the_other_channels_level():
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    device.call_zigbee_cmd(2, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    sent = [f for f in frames(proc, device, 1500) if f[4] == 2]
    assert sent and all(f[5] == 255 for f in sent) and sent[-1] == level_frame(2, 255, other=255)


# ---- fades ----------------------------------------------------------------------------------


def test_on_fades_in_from_the_minimum():
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    sent = run_t(proc, device, 1500)
    levels = hk([f for _, f in sent])
    assert levels[0] == MIN and rising(levels) and levels[-1] == 255
    assert all(r == b"\x00\x00" for r in ramps([f for _, f in sent]))
    full_at = next(t for t, f in sent if f[4] == 1 and f[5] == 255)
    assert 900 <= full_at <= 1150, full_at            # fade_on_time 1 s, dark to full
    assert onoff(device) == 1


def test_off_fades_out_to_the_minimum_then_dark():
    """Open Dimmer's off: fade_off_time for the whole range above the slow end, which slows down
    to 1 step per 10 ms at the minimum (fade_off_slow_zone 15 % of min..max), then 80 ms at the
    minimum, then dark."""
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    sent = [(t, f) for t, f in run_t(proc, device, 2500) if f[4] == 1]
    levels = hk([f for _, f in sent])
    assert falling(levels) and levels[-2] == MIN and levels[-1] == 0
    dark_at = next(t for t, f in sent if f[5] == 0)
    min_at = next(t for t, f in sent if f[5] == MIN)
    assert abs(dark_at - model.off_ms()) <= 30, (dark_at, model.off_ms())   # 1.72 s, full to dark
    assert dark_at - min_at >= 80, (min_at, dark_at)       # (led_soft: the min's level from ~30)
    assert onoff(device) == 0


def test_the_off_fades_slow_end_is_slow_at_the_minimum():
    """The lowest part slows down (not rushed: more frames there than a plain fade has), and with
    fade_off_slow_zone 0 the off is linear, 80 ms at the minimum included."""
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    slow = run_t(proc, device, 2500)
    low = hk_out(10 + 148)                            # the top of the slow end (15 % of 990)
    entered = next(t for t, f in slow if f[4] == 1 and f[5] < low)
    dark_at = next(t for t, f in slow if f[4] == 1 and f[5] == 0)
    assert dark_at - entered >= 400, (entered, dark_at)    # ~450 ms; the plain rate: ~300
    device.write_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_SOFT_END, 0)
    frames(proc, device, 300)
    assert setting(device, ATTR_SOFT_END) == 0
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    sent = run_t(proc, device, 2500)
    dark_at = next(t for t, f in sent if f[4] == 1 and f[5] == 0)
    entered = next(t for t, f in sent if f[4] == 1 and f[5] < low)
    assert abs(dark_at - model.off_ms(soft_pct=0)) <= 30, dark_at          # 1.49 s + 80 ms
    assert dark_at - entered <= 330, (entered, dark_at)


def test_a_level_change_while_lit_takes_open_dimmers_rate():
    """Lit to lit with no time (brightness_fade_time unset): Open Dimmer's own rate, the whole
    range in 2 s (5 steps per 10 ms), whatever the fade settings."""
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    move(device, 127)                       # full to about half: down, half the way
    sent = run_t(proc, device, 1500)
    levels = hk([f for _, f in sent])
    assert falling(levels) and abs(levels[-1] - hk_out(zcl_value(127))) <= 1
    done_at = next(t for t, f in sent if f[4] == 1 and f[5] == levels[-1])
    assert 950 <= done_at <= 1050, done_at            # (1000 - 503) / 5 ticks of 10 ms
    assert abs(level(device) - 127) <= 1


def test_channel_two_has_its_own_frames():
    proc, device, _ = start()
    device.call_zigbee_cmd(2, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    sent = frames(proc, device, 1500)
    assert hk(sent, 2)[-1] == 255 and hk(sent, 1) == []
    assert onoff(device, 1) == 0


def test_a_level_is_scaled_to_the_dimming_mcu():
    proc, device, _ = start()
    move(device, 127)
    assert hk(frames(proc, device, 1500))[-1] == hk_out(zcl_value(127))   # led_soft, the default
    assert onoff(device) == 1


def test_the_minimum_is_the_lowest_level_sent():
    proc, device, _ = start()
    device.write_zigbee_attr(1, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MIN, 50)
    run_t(proc, device, 300)
    assert int(device.read_zigbee_attr(1, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MIN)) == 50
    move(device, 1)
    levels = hk(frames(proc, device, 800))
    dp_min = (50 * 1000 + 127) // 254
    assert abs(levels[-1] - (dp_min * 255 + 500) // 1000) <= 1 and min(levels) == levels[-1]


def test_fade_times_are_settings_per_channel():
    proc, device, _ = start()
    assert [setting(device, a) for a in (ATTR_FADE_ON, ATTR_FADE_OFF, ATTR_HOLD, ATTR_RAMP)] == \
        [1000, 1500, 4000, 1500]
    set_setting(proc, device, ATTR_FADE_ON, 3000)
    assert setting(device, ATTR_FADE_ON) == 3000 and setting(device, ATTR_FADE_ON, 2) == 1000
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    sent = run_t(proc, device, 3500)
    full_at = next(t for t, f in sent if f[4] == 1 and f[5] == 255)
    assert 2800 <= full_at <= 3200, full_at


def test_a_fade_time_of_0_changes_at_once_with_the_mcus_ramp():
    proc, device, _ = start()
    set_setting(proc, device, ATTR_FADE_ON, 0)
    set_setting(proc, device, ATTR_FADE_OFF, 0)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    sent = frames(proc, device, 300)
    # from dark: the minimum first at once (an LED lamp stays dark through the low part of the
    # MCU's ramp), then the level with the MCU's own ramp
    assert sent == [level_frame(1, MIN), level_frame(1, 255, ramp=1500)]
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    assert frames(proc, device, 300) == [level_frame(1, 0, ramp=1500)]


def test_on_while_fading_out_turns_back_from_where_it_is():
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    down = hk(frames(proc, device, 500))
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    up = hk(frames(proc, device, 1500))
    assert down[-1] > MIN and up[0] >= down[-1] - 8 and rising(up) and up[-1] == 255


# ---- wall switches --------------------------------------------------------------------------


def test_a_wall_switch_toggles_and_is_reported():
    proc, device, _ = start()
    device.press_button(S1)          # default switch type: toggle (latching), every change toggles
    levels = hk(frames(proc, device, 1500))
    assert levels[0] == MIN and levels[-1] == 255
    assert onoff(device) == 1
    device.release_button(S1)
    assert hk(frames(proc, device, 2000))[-1] == 0
    assert onoff(device) == 0


def test_the_second_wall_switch_drives_channel_two():
    proc, device, _ = start()
    device.press_button(S2)
    sent = frames(proc, device, 1500)
    assert hk(sent, 2)[-1] == 255 and hk(sent, 1) == []
    assert onoff(device, 2) == 1
    assert onoff(device, 1) == 0


def test_a_state_switch_follows_its_contact():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_STATE)
    device.press_button(S1)
    assert hk(frames(proc, device, 1500))[-1] == 255
    device.press_button(S1)             # still closed: no change
    assert hk(frames(proc, device, 300)) == []
    device.release_button(S1)
    assert hk(frames(proc, device, 2000))[-1] == 0
    assert onoff(device) == 0


def test_a_momentary_tap_from_dark_turns_on_at_the_press():
    """Open Dimmer's: on at once at the minimum when pressed (reported on at the press), nothing
    more while the finger is on it; the release of a tap goes on quickly to the level; a tap while
    lit turns off at its release."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    device.press_button(S1)
    pressed = run_t(proc, device, 200)
    assert hk([f for _, f in pressed]) == [MIN] and pressed[0][0] <= 70, pressed
    assert onoff(device) == 1 and level(device) == 254         # the level kept, not the minimum
    device.release_button(S1)
    sent = run_t(proc, device, 1500)
    levels = hk([f for _, f in sent])
    assert rising(levels) and levels[-1] == 255
    full_at = next(t for t, f in sent if f[4] == 1 and f[5] == 255)
    assert full_at <= 1100, full_at                             # the on rate, fade_on_time
    assert onoff(device) == 1
    device.press_button(S1)
    assert hk(frames(proc, device, 200)) == []                  # lit: nothing at the press
    device.release_button(S1)
    assert hk(frames(proc, device, 2500))[-1] == 0
    assert onoff(device) == 0


def test_a_hold_from_dark_rises_from_the_minimum_at_the_holds_pace():
    """At the minimum from the press, 0.7 s there (let go then, it stays there), then rising,
    slowly at first (hold_slow_zone 25 % of min..max) and then at the hold's rate (hold_time for
    the whole range)."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    device.press_button(S1)
    sent = run_t(proc, device, 2050)        # (the press: 50 ms debounce, then 2 s)
    device.release_button(S1)
    after = hk(frames(proc, device, 500))
    levels = hk([f for _, f in sent])
    wait = [f[5] for t, f in sent if t <= 50 + 700]
    assert levels[0] == MIN and set(wait) == {MIN} and rising(levels), levels
    assert all(r == b"\x00\x00" for r in ramps([f for _, f in sent]))
    assert abs(levels[-1] - hk_out(model.hold_from_dark_at(2000))) <= 2, levels[-1]
    assert after == [] or max(after) <= levels[-1] + 2     # it stops where it was let go
    assert onoff(device) == 1
    assert 1 < level(device) < 100             # the level the hold ended at is reported


def test_the_first_hold_when_lit_goes_to_the_far_end_then_they_alternate():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 200)
    frames(proc, device, 1500)
    device.press_button(S1)                 # above the middle: down
    down = hk(frames(proc, device, 1200))
    device.release_button(S1)
    frames(proc, device, 300)
    assert falling(down) and down[-1] < 200
    device.press_button(S1)                 # the next hold: back up
    up = hk(frames(proc, device, 1200))
    device.release_button(S1)
    assert rising(up) and up[-1] > down[-1]


def test_below_the_middle_the_first_hold_goes_up():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 60)
    frames(proc, device, 1500)
    device.press_button(S1)
    up = hk(frames(proc, device, 1200))
    device.release_button(S1)
    assert rising(up) and up[0] >= hk_out(zcl_value(60)) - 1


def test_the_hold_time_is_a_setting():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    set_setting(proc, device, ATTR_HOLD, 1000)
    device.press_button(S1)
    sent = run_t(proc, device, 3000)        # 0.7 s at the minimum, then the whole range in 1 s
    device.release_button(S1)
    top_at = next(t for t, f in sent if f[4] == 1 and f[5] == 255)
    assert abs(top_at - 50 - model.hold_from_dark_top_ms(hold=1000)) <= 50, top_at


# ---- module button, LED, power-on, NV -------------------------------------------------------


def test_the_module_button_switches_both_channels():
    """As Open Dimmer's: any channel on, a press switches all off; else all on."""
    proc, device, _ = start()
    device.click_button(BUTTON)
    sent = frames(proc, device, 1500)
    assert hk(sent, 1)[-1] == 255 and hk(sent, 2)[-1] == 255
    assert onoff(device, 1) == 1 and onoff(device, 2) == 1
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    frames(proc, device, 2000)
    device.click_button(BUTTON)                     # L2 still on: everything off
    assert hk(frames(proc, device, 2000), 2)[-1] == 0
    assert onoff(device, 1) == 0 and onoff(device, 2) == 0


def test_the_module_button_held_and_let_go_resets_the_device():
    """3 s and more, at the release (Open Dimmer's): the LED lights up meanwhile (let go now)."""
    proc, device, _ = start()
    assert device.is_joined()
    device.press_button(BUTTON)
    assert hk(frames(proc, device, 3500)) == []
    assert device.is_joined() and device.get_gpio(LED, refresh=True)    # held: not yet
    device.release_button(BUTTON)
    assert hk(frames(proc, device, 300)) == []      # the release toggles nothing
    assert not device.is_joined()


def test_the_module_button_held_less_than_3_s_switches():
    proc, device, _ = start()
    device.press_button(BUTTON)
    assert hk(frames(proc, device, 2500)) == []
    device.release_button(BUTTON)
    assert hk(frames(proc, device, 1500), 1)[-1] == 255
    assert device.is_joined() and onoff(device, 1) == 1 and onoff(device, 2) == 1


def led_samples(proc: StubProc, device: Device, n: int) -> list[bool]:
    out = []
    for _ in range(n):
        frames(proc, device, 250)
        out.append(device.get_gpio(LED, refresh=True))
    return out


def test_the_led_is_dark_on_a_network_and_blinks_off_it():
    proc, device, _ = start()
    assert device.is_joined()
    assert not any(led_samples(proc, device, 6))      # active high: low = dark
    device.set_network(0)
    samples = led_samples(proc, device, 8)
    assert True in samples and False in samples
    device.set_network(1)
    frames(proc, device, 600)
    assert not any(led_samples(proc, device, 6))


def test_power_on_previous_brings_the_level_back_fading_in():
    proc, device, _ = start()
    device.write_zigbee_attr(1, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_START_UP_ON_OFF, 0xFF)
    move(device, 127)
    frames(proc, device, 6000)                # kept in NV 5 s after the last change
    proc.stop()

    proc, device, boot = start()
    levels = hk(boot)
    assert levels[0] == MIN and abs(levels[-1] - hk_out(zcl_value(127))) <= 1 and levels == sorted(levels)
    # the second sending repeats the level, nothing fades again
    second = boot[boot.index(RANGE, 1):]
    assert [f[5] for f in second if f[4] == 1] == [levels[-1]]
    assert onoff(device) == 1


def test_power_on_off_is_the_default():
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 6000)
    proc.stop()
    proc, device, boot = start()
    assert boot[1] == level_frame(1, 0)


def test_settings_are_kept():
    proc, device, _ = start()
    set_setting(proc, device, ATTR_FADE_OFF, 700, ch=2)
    set_setting(proc, device, ATTR_RAMP, 500)
    frames(proc, device, 6000)
    proc.stop()
    proc, device, _ = start()
    assert setting(device, ATTR_FADE_OFF, 2) == 700 and setting(device, ATTR_RAMP) == 500
    assert setting(device, ATTR_FADE_OFF, 1) == 1500


def write_nv_item(item: bytes) -> None:
    os.makedirs("./stub_nvm_data", exist_ok=True)
    with open("./stub_nvm_data/item_43.bin", "wb") as f:      # NV_ITEM_HK_DIMMER = 67
        f.write(item)


V1 = bytes([1, 1]) + struct.pack("<BBHHH", 1, 2, 400, 10, 1000) + struct.pack("<BBHHH", 0, 0, 1000, 10, 1000)


def test_a_layout_1_nv_item_keeps_its_settings():
    """The first build kept no ramp: its item (18 bytes) is taken over, the rest defaults."""
    write_nv_item(V1)                        # power-on on; L1 on, momentary, value 400
    proc, device, boot = start()
    assert int(device.read_zigbee_attr(1, ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG, ZCL_ATTR_SWITCH_TYPE)) == SWITCH_MOMENTARY
    assert onoff(device) == 1
    assert hk(boot)[-1] == hk_out(400)
    assert [setting(device, a) for a in (ATTR_RAMP, ATTR_FADE_ON, ATTR_FADE_OFF, ATTR_HOLD)] == \
        [1500, 1000, 1500, 4000]


def test_a_layout_2_nv_item_keeps_its_ramp():
    """The ramp build's item (22 bytes): its ramps kept, the fade and hold times defaults."""
    write_nv_item(bytes([2]) + V1[1:] + struct.pack("<HH", 500, 1500))
    proc, device, _ = start()
    assert setting(device, ATTR_RAMP) == 500 and setting(device, ATTR_RAMP, 2) == 1500
    assert [setting(device, a) for a in (ATTR_FADE_ON, ATTR_FADE_OFF, ATTR_HOLD)] == [1000, 1500, 4000]
    assert onoff(device) == 1


# ---- dimming curves -------------------------------------------------------------------------

ATTR_CURVE = 0xFF38                       # + 1: L2 (an enum, DP 0x78 / 0x79)
ATTR_CUSTOM_CURVE = 0xFF3A                # + 1: L2 (17 little-endian u16, an octet string)
CUSTOM_TABLE = [0, 100, 250, 450, 700, 1000, 1400, 1900, 2500, 3200, 4000, 4900, 5900, 7000, 8100,
                9100, 10000]


def le_hex(points) -> str:
    return "".join(f"{p & 0xFF:02x}{p >> 8:02x}" for p in points)


def lit_at(proc: StubProc, device: Device, lvl: int) -> int:
    move(device, lvl)
    return hk(frames(proc, device, 2500))[-1]


def test_the_default_curve_is_led_soft_and_each_curve_is_open_dimmers():
    proc, device, _ = start()
    assert setting(device, ATTR_CURVE) == LED_SOFT and setting(device, ATTR_CURVE, 2) == LED_SOFT
    assert lit_at(proc, device, 127) == hk_out(zcl_value(127), LED_SOFT)
    for curve in (LINEAR, CURVE_LED, LED_SOFT):
        set_setting(proc, device, ATTR_CURVE, curve)
        # the output follows the curve at once, no new level needed
        assert hk(frames(proc, device, 300))[-1:] in ([hk_out(zcl_value(127), curve)], [])
        for lvl in (2, 60, 200):
            assert lit_at(proc, device, lvl) == hk_out(zcl_value(lvl), curve), (curve, lvl)
    assert setting(device, ATTR_CURVE, 2) == LED_SOFT          # per channel


def test_a_curve_keeps_the_minimum_and_the_maximum():
    proc, device, _ = start()
    device.write_zigbee_attr(1, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MIN, 15)
    frames(proc, device, 300)
    mn = (15 * 1000 + 127) // 254
    for curve in (LINEAR, CURVE_LED, LED_SOFT):
        set_setting(proc, device, ATTR_CURVE, curve)
        assert lit_at(proc, device, 1) == hk_out(mn, LINEAR, mn=mn)   # the minimum as it is
        assert lit_at(proc, device, 254) == 255


def test_the_custom_curve_is_taken_kept_and_used():
    proc, device, _ = start()
    assert device.read_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE).lower() == "-"
    set_setting(proc, device, ATTR_CURVE, CUSTOM)
    # no table yet: led_soft
    assert lit_at(proc, device, 127) == hk_out(zcl_value(127), LED_SOFT)
    device.write_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE, le_hex(CUSTOM_TABLE))
    assert device.read_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE).lower() == le_hex(CUSTOM_TABLE)
    assert hk(frames(proc, device, 300))[-1] == hk_out(zcl_value(127), CUSTOM, custom=CUSTOM_TABLE)
    assert lit_at(proc, device, 60) == hk_out(zcl_value(60), CUSTOM, custom=CUSTOM_TABLE)
    # a falling table or a short one is refused: the one kept stays
    bad = CUSTOM_TABLE[:5] + [50] + CUSTOM_TABLE[6:]
    device.write_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE, le_hex(bad))
    assert device.read_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE).lower() == le_hex(CUSTOM_TABLE)
    device.write_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE, le_hex(CUSTOM_TABLE[:16]))
    assert device.read_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE).lower() == le_hex(CUSTOM_TABLE)
    # L2 has none of its own
    assert device.read_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE + 1).lower() == "-"
    frames(proc, device, 6000)              # the curve setting is kept 5 s after the change
    proc.stop()
    proc, device, _ = start()
    assert device.read_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE).lower() == le_hex(CUSTOM_TABLE)
    assert setting(device, ATTR_CURVE) == CUSTOM
    # none again: led_soft
    device.write_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE, "-")
    assert device.read_zigbee_attr(1, ZCL_CLUSTER_BASIC, ATTR_CUSTOM_CURVE).lower() == "-"
    assert lit_at(proc, device, 127) == hk_out(zcl_value(127), LED_SOFT)


def test_a_layout_3_nv_item_gets_the_default_curve():
    """The fade build's item: its settings kept, the curve led_soft."""
    write_nv_item(bytes([3]) + V1[1:] + struct.pack("<HHHHHHHH", 500, 1500, 700, 1000, 1500, 1500, 4000, 4000))
    proc, device, _ = start()
    assert setting(device, ATTR_FADE_ON) == 700 and setting(device, ATTR_RAMP) == 500
    assert setting(device, ATTR_CURVE) == LED_SOFT


# ---- transition times from the network, OnLevel, reports for Adaptive Lighting -------------

ZCL_ATTR_ON_LEVEL = 0x0011


def test_a_transition_time_is_the_fades_length():
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    move(device, 127, tenths=50)                     # 5 s
    sent = run_t(proc, device, 6000)
    levels = hk([f for _, f in sent])
    assert falling(levels) and levels[-1] == hk_out(zcl_value(127))
    done_at = next(t for t, f in sent if f[4] == 1 and f[5] == levels[-1])
    assert 4700 <= done_at <= 5300, done_at
    assert all(r == b"\x00\x00" for r in ramps([f for _, f in sent]))


def test_a_transition_time_of_0_is_at_once():
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    move(device, 127, tenths=0)
    sent = frames(proc, device, 500)
    assert sent == [level_frame(1, hk_out(zcl_value(127)))]   # one frame, no ramp


def test_an_off_with_a_transition_time_fades_out_for_it():
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    move(device, 0, tenths=30)                       # off in 3 s
    sent = run_t(proc, device, 4000)
    levels = hk([f for _, f in sent])
    assert falling(levels) and levels[-1] == 0
    dark_at = next(t for t, f in sent if f[4] == 1 and f[5] == 0)
    assert 2800 <= dark_at <= 3300, dark_at
    assert onoff(device) == 0


def test_a_long_transition_from_dark_starts_at_the_minimum():
    proc, device, _ = start()
    move(device, 200, tenths=100)                    # from dark, 10 s
    sent = run_t(proc, device, 11000)
    levels = hk([f for _, f in sent])
    assert levels[0] == MIN and rising(levels) and levels[-1] == hk_out(zcl_value(200))
    done_at = next(t for t, f in sent if f[4] == 1 and f[5] == levels[-1])
    assert 9500 <= done_at <= 10500, done_at


def test_a_wall_switch_on_goes_to_the_on_level():
    proc, device, _ = start()
    move(device, 200)
    frames(proc, device, 1500)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    frames(proc, device, 2000)
    device.write_zigbee_attr(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_ATTR_ON_LEVEL, 60)
    frames(proc, device, 6000)                       # past the quick re-on window after an off
    device.press_button(S1)                          # toggle: on
    levels = hk(frames(proc, device, 2500))
    assert levels[-1] == hk_out(zcl_value(60)), levels[-5:]
    assert level(device) == 60


def test_a_wall_switch_hold_is_reported_as_a_level_change():
    """What Adaptive Lighting takes for a manual change (take_over_control, detect_non_ha_changes):
    the level the hold ended at reaches the level attribute."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 60)
    frames(proc, device, 1500)
    before = level(device)
    device.press_button(S1)
    frames(proc, device, 1800)
    device.release_button(S1)
    frames(proc, device, 300)
    assert level(device) > before + 20


def test_channel_two_can_follow_the_first_wall_switch():
    """L2 (e.g. a mirror cabinet) goes with L1's wall switch inside the module."""
    proc, device, _ = start()
    for attr, value in ((0xFF17, 1), (0xFF18, 254), (0xFF19, 60)):
        device.write_zigbee_attr(2, 0x0007, attr, value)
    device.press_button(S1)                                  # L1 on by its wall switch
    sent = frames(proc, device, 2000)
    assert onoff(device, 2) == 1 and level(device, 2) == 254
    assert hk(sent, ch=2)[-1] == hk_out(zcl_value(254))
    device.release_button(S1)                                # (toggle switch) L1 off: L2 dims to 60
    sent = frames(proc, device, 2500)
    assert onoff(device, 1) == 0
    assert onoff(device, 2) == 1 and level(device, 2) == 60
    assert hk(sent, ch=2)[-1] == hk_out(zcl_value(60))


def test_a_level_from_dark_with_no_time_fades_in_from_the_minimum():
    """What the converter sends for a brightness without a transition (0xFFFF): the module's
    fade, the minimum first, frames at ramp 0 (the MCU's own ramp only with fade_on_time 0)."""
    proc, device, _ = start()
    move(device, 200)
    sent = run_t(proc, device, 1500)
    levels = hk([f for _, f in sent])
    assert levels[0] == MIN and rising(levels) and levels[-1] == hk_out(zcl_value(200))
    assert all(r == b"\x00\x00" for r in ramps([f for _, f in sent]))
    done_at = next(t for t, f in sent if f[4] == 1 and f[5] == levels[-1])
    assert 600 <= done_at <= 1000, done_at              # its share of fade_on_time


# ---- holds: the threshold, reports on the way, their end (network, button, cap) -------------

SWITCH_TOGGLE = 0
LEVEL_CTRL = ZCL_CLUSTER_LEVEL_CONTROL


def test_a_press_shorter_than_the_threshold_is_a_tap_a_longer_one_dims():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    device.press_button(S1)                           # (60 ms: past the debounce)
    frames(proc, device, HOLD_AFTER - 120)
    device.release_button(S1)                         # let go before HOLD_AFTER: a tap
    assert hk(frames(proc, device, 1500))[-1] == 255 and onoff(device) == 1
    device.press_button(S1)
    down = hk(frames(proc, device, HOLD_AFTER + 40))  # let go at 500 ms (the old threshold: 600)
    device.release_button(S1)
    down += hk(frames(proc, device, 300))
    assert falling(down) and onoff(device) == 1      # held past it: dims (down from full)
    assert 200 < level(device) < 254


def test_channel_two_dims_by_its_own_hold_at_its_own_hold_time():
    proc, device, _ = start()
    set_switch_type(proc, device, 2, SWITCH_MOMENTARY)
    set_setting(proc, device, ATTR_HOLD, 1000, ch=2)
    device.press_button(S2)
    sent = run_t(proc, device, 3000)
    device.release_button(S2)
    frames(proc, device, 300)
    assert hk([f for _, f in sent], 1) == [] and hk([f for _, f in sent], 2)[0] == MIN
    assert rising(hk([f for _, f in sent], 2))
    top_at = next(t for t, f in sent if f[4] == 2 and f[6] == 255)
    assert abs(top_at - 50 - model.hold_from_dark_top_ms(hold=1000)) <= 50, top_at
    assert onoff(device, 2) == 1 and onoff(device, 1) == 0


def test_a_hold_reports_its_level_on_the_way():
    """Every 0.5 s (as Open Dimmer 2.2.4 and the factory MCU): Home Assistant and Adaptive
    Lighting see it move, not only at the release."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 40)
    frames(proc, device, 1500)
    seen = []
    device.press_button(S1)
    for _ in range(6):
        frames(proc, device, 500)
        seen.append(level(device))
    device.release_button(S1)
    frames(proc, device, 300)
    assert seen == sorted(seen) and len(set(seen)) >= 5, seen
    assert seen[0] <= 45 and level(device) >= seen[-1]


def test_a_hold_from_dark_with_an_on_level_rises_from_the_minimum_and_shows_it():
    """The wall switch's on at the press writes OnLevel (for a tap), but the light stays at the
    minimum under the finger and rises from there, never to OnLevel; from the hold time on the
    level shown follows the hold's reports (none held back for 3 s), its end at once."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    device.write_zigbee_attr(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_ATTR_ON_LEVEL, 200)
    frames(proc, device, 300)
    device.press_button(S1)
    sent = frames(proc, device, 2550)
    shown = level(device)
    device.release_button(S1)
    frames(proc, device, 200)
    end = level(device)
    levels = hk(sent)
    assert levels[0] == MIN and rising(levels) and max(levels) < hk_out(zcl_value(200))
    assert 10 < shown < 100, shown                  # on the hold's way, never OnLevel 200
    assert shown <= end < 100, end                  # its end, at once
    after = hk(frames(proc, device, 3000))
    assert after == [] or max(after) <= levels[-1] + 2    # it stays there


def test_a_tap_from_dark_with_an_on_level_goes_on_to_it():
    """The OnLevel the module writes after the on at the press is taken for the tap (the light
    waits at the minimum meanwhile): the release goes on to it, and the level shown is it."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    device.write_zigbee_attr(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_ATTR_ON_LEVEL, 100)
    frames(proc, device, 300)
    device.press_button(S1)
    pressed = hk(frames(proc, device, 250))
    device.release_button(S1)
    levels = hk(frames(proc, device, 1500))
    assert pressed == [MIN] and rising(levels) and levels[-1] == hk_out(zcl_value(100)), levels
    assert level(device) == 100 and onoff(device) == 1


def test_a_hold_goes_to_the_bound_lights_as_a_move_and_its_end():
    from test_dimmer_bindings import MOVE_TO_LEVEL_WITH_ONOFF, MOVE_WITH_ONOFF, STOP_WITH_ONOFF
    from test_dimmer_bindings import sent as bound
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 40)
    frames(proc, device, 1500)
    bound(device)
    device.press_button(S1)
    frames(proc, device, HOLD_AFTER + 1200)
    during = bound(device)
    device.release_button(S1)
    frames(proc, device, 1500)
    after = bound(device)
    assert during and all(c[:4] == (3, LEVEL_CTRL, MOVE_WITH_ONOFF, c[3]) and c[3][0] == 0
                          for c in during), during          # up, while the finger is on it
    assert [c[:3] for c in after[-2:]] == [(3, LEVEL_CTRL, STOP_WITH_ONOFF),
                                          (3, LEVEL_CTRL, MOVE_TO_LEVEL_WITH_ONOFF)], after
    assert after[-1][3][0] == level(device)


def test_an_off_during_a_hold_ends_it_and_its_release_changes_nothing():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 60)
    frames(proc, device, 1500)
    device.press_button(S1)
    frames(proc, device, HOLD_AFTER + 600)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    assert hk(frames(proc, device, 2000))[-1] == 0
    device.release_button(S1)
    assert hk(frames(proc, device, 1500)) == [] and onoff(device) == 0    # no toggle back on
    assert abs(level(device) - 60) <= 1             # the level kept is the one before the hold
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    assert hk(frames(proc, device, 1500))[-1] == hk_out(zcl_value(60))


def test_an_on_or_a_level_to_the_lit_channel_does_not_end_a_hold():
    """Home Assistant's turn_on of a lit light (On, or a level) must not take the level from the
    finger: the level written is refused under the hold."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 40)
    frames(proc, device, 1500)
    device.press_button(S1)
    first = hk(frames(proc, device, HOLD_AFTER + 400))
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    move(device, 40)
    more = hk(frames(proc, device, 800))
    device.release_button(S1)
    frames(proc, device, 300)
    assert rising(first + more), first + more
    assert level(device) > 60 and onoff(device) == 1


def test_the_module_button_during_a_hold_ends_it():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 60)
    frames(proc, device, 1500)
    device.press_button(S1)
    frames(proc, device, HOLD_AFTER + 600)
    device.click_button(BUTTON)                      # any on: all off
    assert hk(frames(proc, device, 2000))[-1] == 0
    device.release_button(S1)
    assert hk(frames(proc, device, 1500)) == [] and onoff(device) == 0
    assert abs(level(device) - 60) <= 1


def test_a_switch_type_change_during_a_hold_ends_it():
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 60)
    frames(proc, device, 1500)
    device.press_button(S1)
    frames(proc, device, HOLD_AFTER + 600)
    device.write_zigbee_attr(1, ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG, ZCL_ATTR_SWITCH_TYPE, SWITCH_TOGGLE)
    assert hk(frames(proc, device, 1500))[-1] == hk_out(zcl_value(60))    # back as it was (Open Dimmer)
    device.release_button(S1)
    frames(proc, device, 300)
    move(device, 120)                                 # level writes work again
    assert hk(frames(proc, device, 1500))[-1] == hk_out(zcl_value(120))
    assert onoff(device) == 1


def test_a_switch_stuck_closed_ends_its_hold_at_the_cap():
    """2 x hold_time + 2 s from the press (Open Dimmer's): the level is taken there, a level from
    the network is applied again, and the late release changes nothing."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    set_setting(proc, device, ATTR_HOLD, 1000)        # the cap: 4 s from the press
    device.press_button(S1)                           # from dark: up in 1 s, then held at the top
    frames(proc, device, 3000)
    move(device, 100)
    assert hk(frames(proc, device, 500)) == [] and level(device) == 254    # refused: still held
    frames(proc, device, 1000)
    move(device, 100)
    assert hk(frames(proc, device, 1500))[-1] == hk_out(zcl_value(100))
    device.release_button(S1)
    assert hk(frames(proc, device, 1500)) == [] and level(device) == 100 and onoff(device) == 1


# ---- the wall switch and the button never take a time the network sent --------------------


def test_a_wall_switch_right_after_a_timed_command_takes_the_fade_settings():
    """Open Dimmer's apply_local: the network's time is for its own changes only."""
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    move(device, 127, tenths=50)                      # 5 s
    frames(proc, device, 200)
    device.press_button(S1)                           # toggle: off, within the network's second
    sent = run_t(proc, device, 2500)
    dark_at = next(t for t, f in sent if f[4] == 1 and f[5] == 0)
    assert dark_at <= 1700, dark_at                   # its share of fade_off_time, not 5 s


def test_a_wall_switch_after_an_at_once_command_still_fades():
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    move(device, 127, tenths=0)
    frames(proc, device, 100)
    device.press_button(S1)
    levels = hk(frames(proc, device, 1500))
    assert falling(levels) and levels[-1] == 0        # several frames, not one dark frame


def test_the_module_button_right_after_a_timed_off_takes_fade_on_time():
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    move(device, 0, tenths=30)                        # off in 3 s
    frames(proc, device, 300)
    device.click_button(BUTTON)                       # on again (no OnLevel: no take-back)
    sent = run_t(proc, device, 2000)
    full_at = next(t for t, f in sent if f[4] == 1 and f[5] == 255)
    assert full_at <= 500, full_at                    # the rest of fade_on_time, not 3 s


# ---- a restart of the module alone, NV ------------------------------------------------------

ZCL_ATTR_DP_CONFIG = 0xFF11
NV_FILE = "./stub_nvm_data/item_43.bin"              # NV_ITEM_HK_DIMMER = 67


def nv_item() -> bytes:
    with open(NV_FILE, "rb") as f:
        return f.read()


def planned_restart(proc: StubProc, device: Device) -> None:
    """A config write: the module saves its state and restarts (the stub's process ends)."""
    device.write_zigbee_attr(1, ZCL_CLUSTER_BASIC, ZCL_ATTR_DP_CONFIG, TS110E_DP_CONFIG)
    device.step_time(400)
    assert proc.wait_for_exit(2.0)


def test_a_restart_of_the_module_alone_leaves_the_light_as_it_was():
    """An update or a config write: the dimming MCU kept driving the light. Neither the power-on
    setting (off by default) nor a fade from dark: the levels as they are, at ramp 0, no range;
    the level written just before (not saved while lit) is saved on the way out."""
    proc, device, _ = start()
    move(device, 150)
    frames(proc, device, 1500)
    planned_restart(proc, device)
    proc, device, boot = start()
    lit = hk_out(zcl_value(150))
    assert boot == [level_frame(1, lit), level_frame(2, 0, other=lit)] * 2
    assert onoff(device, 1) == 1 and abs(level(device, 1) - 150) <= 1 and onoff(device, 2) == 0


def test_after_a_watchdog_reset_the_light_stays_on_at_the_level_saved_within_a_minute():
    """Nothing is saved first: on / off come from the byte kept through the reset, the level from
    NV. A level written while lit is saved a minute after it at the latest (HK_NV_LIT_MS), so
    the light can only go back to a level of the last minute: here 80, saved, not 40, written
    1.5 s before the reset."""
    proc, device, _ = start()
    move(device, 150)
    frames(proc, device, 6000)
    move(device, 80)
    quiet(proc, device, 61000)
    move(device, 40)
    frames(proc, device, 1500)
    assert proc.exec("reboot").ok and proc.wait_for_exit(2.0)
    proc, device, boot = start()
    lit = hk_out(zcl_value(80))
    assert boot == [level_frame(1, lit), level_frame(2, 0, other=lit)] * 2
    assert onoff(device, 1) == 1 and abs(level(device, 1) - 80) <= 1


def restart_soon(proc: StubProc, device: Device) -> list[bytes]:
    """A planned restart (a config write: the module restarts 300 ms after it); the frames sent
    until just before it."""
    device.write_zigbee_attr(1, ZCL_CLUSTER_BASIC, ZCL_ATTR_DP_CONFIG, TS110E_DP_CONFIG)
    sent = frames(proc, device, 290)
    device.step_time(20)
    assert proc.wait_for_exit(2.0)
    return sent


def test_a_planned_restart_during_a_hold_keeps_the_light_where_the_hold_is():
    """As Open Dimmer's dimmer_prepare_restart: the hold's level is taken before the save, so
    the light does not jump back to the level it had before the hold."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    move(device, 40)
    frames(proc, device, 1500)
    device.press_button(S1)                   # below the middle: up
    held = hk(frames(proc, device, HOLD_AFTER + 1500))
    held += hk(restart_soon(proc, device))
    assert rising(held) and held[-1] > hk_out(zcl_value(40)) + 30, held
    proc, device, boot = start()
    assert len(hk(boot)) == 2 and all(abs(lv - held[-1]) <= 2 for lv in hk(boot)), (held[-1], hk(boot))
    assert onoff(device) == 1 and level(device) > 60


def test_a_planned_restart_during_a_hold_from_dark_keeps_the_light_where_the_hold_is():
    """Not the level kept from before (full here): the one the hold has come up to."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    device.press_button(S1)
    held = hk(frames(proc, device, HOLD_AFTER + 800))
    held += hk(restart_soon(proc, device))
    assert held[0] == MIN and rising(held) and held[-1] < 100, held
    proc, device, boot = start()
    assert len(hk(boot)) == 2 and all(abs(lv - held[-1]) <= 2 for lv in hk(boot)), (held[-1], hk(boot))
    assert onoff(device) == 1 and level(device) < 120


def test_a_planned_restart_during_a_long_fade_keeps_the_light_where_the_fade_is():
    """Not the fade's target at once: the light stays where the fade was and shows that level."""
    proc, device, _ = start()
    move(device, 20, tenths=0)
    frames(proc, device, 500)
    move(device, 254, tenths=600)             # 60 s
    held = hk([f for _, f in quiet(proc, device, 30000)])
    held += hk(restart_soon(proc, device))
    assert rising(held) and held[-1] < 200, held
    proc, device, boot = start()
    assert len(hk(boot)) == 2 and all(abs(lv - held[-1]) <= 1 for lv in hk(boot)), (held[-1], hk(boot))
    assert onoff(device) == 1 and 60 < level(device) < 200


def test_a_warm_start_saves_the_on_off_it_kept_for_a_later_power_cut():
    """Power-on previous, an on and a watchdog reset before its save was due: the light stays on
    (the kept byte), and that is saved then (as Open Dimmer's dimmer_init after a restore), so a
    later power cut brings it back on too, not off as NV had it."""
    proc, device, _ = start()
    device.write_zigbee_attr(1, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_START_UP_ON_OFF, 0xFF)
    frames(proc, device, 6000)
    assert nv_item()[1] == 2 and nv_item()[2] == 0        # previous; L1 off
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 2000)                            # inside the 5 s before its save
    assert proc.exec("reboot").ok and proc.wait_for_exit(2.0)
    proc, device, _ = start()
    assert onoff(device) == 1
    frames(proc, device, 3000)
    assert nv_item()[2] == 1                              # L1 on
    proc.stop()                                           # a power cut
    proc, device, boot = start()
    assert onoff(device) == 1 and hk(boot)[-1] == 255


def test_a_power_cut_after_a_restart_takes_the_power_on_setting_again():
    proc, device, _ = start()
    move(device, 150)
    frames(proc, device, 1500)
    planned_restart(proc, device)
    proc, device, _ = start()
    proc.stop()                                       # a power cut: the kept byte is gone
    proc, device, boot = start()
    assert boot[:3] == [RANGE, level_frame(1, 0), level_frame(2, 0)] and onoff(device, 1) == 0


def test_a_plain_on_and_off_under_power_on_off_write_no_flash():
    """The on / off state is kept only for the power-on setting that uses it (previous)."""
    proc, device, _ = start()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 6000)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    frames(proc, device, 6000)
    assert not os.path.exists(NV_FILE)
    device.write_zigbee_attr(1, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_START_UP_ON_OFF, 0xFF)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 6000)
    assert nv_item()[1] == 2 and nv_item()[2] == 1     # previous; L1 on


def test_a_level_written_while_lit_is_saved_at_the_off():
    """Open Dimmer's rule: an automation's steps on a lit light wear no flash; the off keeps the
    level."""
    proc, device, _ = start()
    move(device, 200)
    frames(proc, device, 6000)
    saved = nv_item()
    move(device, 90)
    frames(proc, device, 6000)
    assert nv_item() == saved
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    frames(proc, device, 7000)
    assert struct.unpack_from("<H", nv_item(), 4)[0] == zcl_value(90)


def test_a_level_written_while_lit_is_saved_a_minute_after_the_first_change():
    """For a reset of the module alone (the light is not left at a level hours old): once, a
    minute after the first level written while lit, not put off by the next ones, so an
    automation's steps write at most once a minute."""
    proc, device, _ = start()
    move(device, 200)
    frames(proc, device, 6000)
    saved = nv_item()
    for lvl in (180, 160, 140, 120):                  # at 0, 15, 30 and 45 s
        move(device, lvl)
        quiet(proc, device, 15000 if lvl != 120 else 14000)
    assert nv_item() == saved                         # 59 s
    quiet(proc, device, 2000)
    assert struct.unpack_from("<H", nv_item(), 4)[0] == zcl_value(120)


def test_no_flash_write_while_a_fade_runs():
    """A flash write stalls the CPU (and so the fade's frames): a save waits for the fade's end."""
    proc, device, _ = start()
    set_setting(proc, device, ATTR_FADE_ON, 20000)
    frames(proc, device, 6000)
    saved = nv_item()
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)      # 20 s from dark
    frames(proc, device, 500)
    set_setting(proc, device, ATTR_HOLD, 2000, ch=2)                     # a setting to save
    frames(proc, device, 9000)
    assert nv_item() == saved                                            # due, not while fading
    frames(proc, device, 12000)
    assert nv_item() != saved and struct.unpack_from("<H", nv_item(), 32)[0] == 2000   # L2's hold


def test_a_layout_4_nv_item_keeps_everything():
    """The item the deployed units have (36 bytes, layout 4) is taken as it is."""
    item = bytes([4, 1]) + V1[2:] + struct.pack("<HHHHHHHHBB", 500, 1500, 700, 1000, 1500, 900,
                                                 4000, 3000, 1, 3)
    assert len(item) == 36
    write_nv_item(item)
    proc, device, boot = start()
    assert setting(device, ATTR_FADE_ON) == 700 and setting(device, ATTR_FADE_OFF, 2) == 900
    assert setting(device, ATTR_HOLD, 2) == 3000 and setting(device, ATTR_RAMP) == 500
    assert setting(device, ATTR_CURVE) == CURVE_LED and setting(device, ATTR_CURVE, 2) == 3
    assert onoff(device) == 1                         # power-on on
    assert hk(boot)[-1] == hk_out(400, CURVE_LED)


# ---- the link to the dimming MCU ------------------------------------------------------------


def test_a_frame_the_uart_did_not_take_goes_again_as_it_was():
    from test_mcu_diag import counters
    proc, device, _ = start()
    set_setting(proc, device, ATTR_FADE_ON, 0)
    failed = counters(device)["tx_fail"]
    assert proc.exec("uart_fail_writes 1").ok
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    sent = run_t(proc, device, 800)
    assert [f for _, f in sent] == [level_frame(1, MIN), level_frame(1, 255, ramp=1500)]
    assert sent[0][0] >= 200                          # the retry, HK_TX_RETRY_MS later
    assert counters(device)["tx_fail"] == failed + 1


def test_frames_that_keep_failing_set_the_uart_up_again_and_wait():
    from test_uart_hal import uart_info
    proc, device, _ = start()
    inits = uart_info(proc)["inits"]
    assert proc.exec("uart_fail_writes 1000").ok
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 3000)
    assert uart_info(proc)["inits"] == inits + 1     # 3 tries 0.2 s apart, then once per 5 s
    assert proc.exec("uart_fail_writes 0").ok
    sent = frames(proc, device, 5000)
    assert hk(sent)[0] == MIN and hk(sent)[-1] == 255


def test_settings_are_saved_while_the_uart_refuses_frames():
    """A change from dark whose first frame waits for the link is no fade a flash write could
    hold up (no frame goes): a setting is saved as usual, not put off for as long as the UART
    refuses."""
    proc, device, _ = start()
    assert proc.exec("uart_fail_writes 1000").ok
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)     # from dark
    frames(proc, device, 500)
    device.write_zigbee_attr(1, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_START_UP_ON_OFF, 0x01)  # power-on on
    frames(proc, device, 6000)
    assert nv_item()[1] == 1


def quiet(proc: StubProc, device: Device, ms: int, step: int = 500) -> list[tuple[int, bytes]]:
    """Let a long time pass in large steps; the frames with the time they went (to a step)."""
    out, t = [], 0
    while t < ms:
        device.step_time(step)
        t += step
        pump(proc, 2)
        out += [(t, f) for f in split(take_tx(proc))]
    return out


def test_a_quiet_channel_gets_its_level_again_every_minute():
    """The dimming MCU forgets its levels at a power loss of its own and never says so: a
    channel's level goes again after 60 s without a frame (a dark one's too), at ramp 0."""
    proc, device, _ = start()
    move(device, 150)
    frames(proc, device, 1500)                        # L1's last frame here; L2's at the boot
    lit = hk_out(zcl_value(150))
    sent = quiet(proc, device, 62000)
    assert [f for _, f in sent] == [level_frame(2, 0, other=lit), level_frame(1, lit)]
    assert 56000 <= sent[0][0] <= 60000 and 58500 <= sent[1][0] <= 61000, sent


def test_the_keep_alive_never_cuts_the_mcus_own_ramp_short():
    proc, device, _ = start()
    set_setting(proc, device, ATTR_FADE_ON, 0)
    set_setting(proc, device, ATTR_RAMP, 30000)       # the MCU's own ramp, 30 s
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    assert frames(proc, device, 300) == [level_frame(1, MIN), level_frame(1, 255, ramp=30000)]
    sent = [(t, f) for t, f in quiet(proc, device, 92000) if f[4] == 1]
    assert [f for _, f in sent] == [level_frame(1, 255)] and sent[0][0] >= 89500, sent


def test_the_boot_resend_never_cuts_the_mcus_own_ramp_short():
    """Power-on on, fade on time 0 and a 30 s MCU ramp: the level goes 1.5 s after a power-up
    with that ramp; the second sending (at 4 s) waits for the ramp's end, as the keep-alive
    does, instead of cutting it short at ramp 0."""
    proc, device, _ = start()
    set_setting(proc, device, ATTR_FADE_ON, 0)
    set_setting(proc, device, ATTR_RAMP, 30000)
    device.write_zigbee_attr(1, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_START_UP_ON_OFF, 0x01)
    frames(proc, device, 6000)
    proc.stop()                                       # a power cut
    proc, device, boot = start()                      # its first 5 s
    assert hk(boot) == [MIN, 255] and ramps(boot) == [b"\x00\x00", (30000).to_bytes(2, "big")]
    later = [(t, f) for t, f in quiet(proc, device, 30000) if f[2:4] == b"\xFF\x55" and f[4] == 1]
    assert [f[5] for _, f in later] == [255] and later[0][0] >= 26500, later   # 1.5 + 30 s


def test_a_dp_the_virtual_mcu_does_not_keep_gets_no_answer():
    """As Open Dimmer: no frame at all (it would hold nothing the module could take)."""
    from test_mcu_diag import counters
    proc, device, _ = start(TS110E_DP_CONFIG + "7CV2C;")          # (0xff3c.. are the module's own)
    frames(proc, device, 300)
    before = counters(device)["rx_frames"]
    device.write_zigbee_attr(1, ZCL_CLUSTER_BASIC, 0xFF2C, 5)
    frames(proc, device, 300)
    assert counters(device)["rx_frames"] == before
    set_setting(proc, device, ATTR_FADE_ON, 700)      # the link goes on
    assert setting(device, ATTR_FADE_ON) == 700


# ---- paths of the TS110E build no test had covered -----------------------------------------

# Open Dimmer's curves (src/curves.h of open-dimmer 2.2.8; hk_dimmer.c's k_curves copies them):
# incandescent, led_deep, led_deeper.
INCANDESCENT, LED_DEEP, LED_DEEPER = 3, 4, 5
CURVES.update({
    INCANDESCENT: [0, 811, 1024, 1178, 1326, 1476, 1627, 1779, 1933, 2089, 2247, 2408, 2571, 2737,
                   2907, 3080, 3257, 3439, 3626, 3819, 4019, 4227, 4444, 4673, 4914, 5171, 5449,
                   5753, 6094, 6486, 6966, 7627, 10000],
    LED_DEEP: [0, 2, 6, 11, 18, 29, 44, 65, 93, 129, 176, 234, 307, 397, 505, 635, 790, 973, 1188,
               1437, 1725, 2057, 2435, 2866, 3354, 3905, 4523, 5215, 5986, 6844, 7794, 8844, 10000],
    LED_DEEPER: [0, 0, 0, 1, 2, 4, 7, 12, 19, 30, 46, 67, 96, 135, 187, 254, 339, 448, 584, 753,
                 960, 1214, 1521, 1890, 2331, 2854, 3472, 4198, 5045, 6031, 7173, 8489, 10000],
})


def test_the_incandescent_and_the_deep_curves_are_open_dimmers():
    proc, device, _ = start()
    out = 0

    def out_at(lvl: int) -> int:     # the level sent last (no frame: the output did not change)
        nonlocal out
        move(device, lvl)
        out = (hk(frames(proc, device, 2500)) or [out])[-1]
        return out

    for curve in (INCANDESCENT, LED_DEEP, LED_DEEPER):
        set_setting(proc, device, ATTR_CURVE, curve)
        assert setting(device, ATTR_CURVE) == curve
        for lvl in (2, 60, 127, 200):
            assert out_at(lvl) == hk_out(zcl_value(lvl), curve), (curve, lvl)
        assert out_at(254) == 255 and out_at(1) == MIN       # the range kept
    # the deep ones leave more room at the dim end than led_soft, incandescent less
    quarter = zcl_value(64)
    assert hk_out(quarter, LED_DEEPER) < hk_out(quarter, LED_DEEP) < hk_out(quarter, LED_SOFT) < \
        hk_out(quarter, INCANDESCENT)
    assert setting(device, ATTR_CURVE, 2) == LED_SOFT


def test_the_mcu_ramp_setting_is_the_ramp_of_a_change_at_once():
    """mcu_ramp per channel goes in the frame of a change the settings make at once (fade time 0):
    500 as 01 F4, 0 as none; the minimum of a change from dark goes first at ramp 0."""
    proc, device, _ = start()
    for ch in (1, 2):
        set_setting(proc, device, ATTR_FADE_ON, 0, ch=ch)
        set_setting(proc, device, ATTR_FADE_OFF, 0, ch=ch)
    set_setting(proc, device, ATTR_RAMP, 500)
    set_setting(proc, device, ATTR_RAMP, 0, ch=2)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    assert frames(proc, device, 300) == [level_frame(1, MIN), level_frame(1, 255, ramp=500)]
    device.call_zigbee_cmd(2, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    sent = frames(proc, device, 300)
    assert sent[-1] == level_frame(2, 255, other=255) and set(ramps(sent, 2)) == {b"\x00\x00"}
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    assert frames(proc, device, 300) == [level_frame(1, 0, other=255, ramp=500)]


def test_a_move_at_once_from_dark_shows_only_its_level():
    """Move to Level with On/Off, time 0, from off: the module writes the on (the virtual MCU
    goes to the level it had) and then the level; no frame goes between them, so the lamp never
    shows the old level (full here) for a frame first."""
    for lvl in (127, 1):
        proc, device, _ = start()
        move(device, lvl, tenths=0)
        sent = frames(proc, device, 500)
        assert sent == [level_frame(1, hk_out(zcl_value(lvl)))], (lvl, [f.hex() for f in sent])
        assert onoff(device) == 1 and abs(level(device) - lvl) <= 1
        proc.stop()


def test_an_off_right_after_an_on_at_once_never_lights_the_lamp_up():
    """fade_on_time 0: an on, then an off before the on's level went (one frame gap): the off goes
    from where the lamp is (the minimum of the kick at most), not from the level it never got."""
    proc, device, _ = start()
    set_setting(proc, device, ATTR_FADE_ON, 0)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    levels = hk(frames(proc, device, 2500))
    assert max(levels, default=0) <= MIN and (not levels or levels[-1] == 0), levels
    assert onoff(device) == 0
    # and on at once, then a new level in the same gap: the kick, then that level
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    move(device, 60)
    sent = frames(proc, device, 300)
    assert sent == [level_frame(1, MIN), level_frame(1, hk_out(zcl_value(60)), ramp=1500)], \
        [f.hex() for f in sent]


def test_a_change_during_the_mcus_own_ramp_goes_with_that_ramp():
    """The dimming MCU's own ramp of a change at once (1.5 s) still runs: only the MCU knows where
    the lamp is on it, so a change then goes in one frame with that ramp (it turns round from
    there), whatever its fade settings; a frame at ramp 0 from the fader jumped the lamp (an off
    40 ms after an on at once: near full first, then down). Once the ramp has run out, the fade
    settings again; a network time of 0 is still at once."""
    proc, device, _ = start()
    set_setting(proc, device, ATTR_FADE_ON, 0)              # fade_off_time stays 1.5 s
    for gap in (40, 300, 1000):
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
        on = frames(proc, device, gap)
        assert on == [level_frame(1, MIN), level_frame(1, 255, ramp=1500)], (gap, [f.hex() for f in on])
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        off = frames(proc, device, 2500)
        assert off == [level_frame(1, 0, ramp=1500)], (gap, [f.hex() for f in off])
    # the ramp run out: the off fades again (fade_off_time, ramp 0)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 2000)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    off = frames(proc, device, 2500)
    assert falling(hk(off)) and hk(off)[-1] == 0 and set(ramps(off)) == {b"\x00\x00"}
    # a level without a time and one with a network time during the ramp: that ramp too; time 0:
    # at once, ramp 0 (the network asked for the jump)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 300)
    move(device, 60)
    assert frames(proc, device, 300) == [level_frame(1, hk_out(zcl_value(60)), ramp=1500)]
    move(device, 200, tenths=30)
    assert frames(proc, device, 300) == [level_frame(1, hk_out(zcl_value(200)), ramp=1500)]
    move(device, 127, tenths=0)
    assert frames(proc, device, 300) == [level_frame(1, hk_out(zcl_value(127)))]
    # the other way: fade_off_time 0, an off, then an on 300 ms later (fade_on_time 1 s): no
    # minimum first (the lamp is lit on its way down), the level with the MCU's ramp
    set_setting(proc, device, ATTR_FADE_ON, 1000)
    set_setting(proc, device, ATTR_FADE_OFF, 0)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    assert frames(proc, device, 300) == [level_frame(1, 0, ramp=1500)]
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    on = frames(proc, device, 2500)
    assert on == [level_frame(1, hk_out(zcl_value(127)), ramp=1500)], [f.hex() for f in on]
    assert onoff(device) == 1


def test_a_hold_during_the_mcus_own_ramp_waits_for_its_end():
    """A lit hold that begins while the MCU's own ramp of a change at once runs stays where the
    lamp is going until that ramp's end, then dims from there (its first frame at ramp 0 jumped
    the lamp from where it was on the ramp)."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    set_setting(proc, device, ATTR_FADE_ON, 0)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    on = run_t(proc, device, 50)
    assert [f for _, f in on] == [level_frame(1, MIN), level_frame(1, 255, ramp=1500)]
    device.press_button(S1)                             # full: the hold goes down
    ramp_end = on[-1][0] + 1500 - 50 - PRESS_STEP       # from the end of the press below
    sent = run_t(proc, device, 3000)
    device.release_button(S1)
    frames(proc, device, 300)
    levels = hk([f for _, f in sent])
    first = sent[0][0]
    assert first >= ramp_end - 20, (first, ramp_end)    # not at the hold time (400 ms)
    assert levels[0] >= 250 and falling(levels), levels
    assert set(ramps([f for _, f in sent])) == {b"\x00\x00"}
    assert onoff(device) == 1 and level(device) < 254


def test_a_press_from_dark_during_the_mcus_own_ramp_turns_it_round():
    """fade_off_time 0, an off: a momentary press from dark while the MCU still ramps down sends
    the minimum with that ramp (not at once: the lamp, lit on its way, dropped to it); a tap then
    goes on to the level with that ramp; a hold rises only once that ramp has run out."""
    proc, device, _ = start()
    set_switch_type(proc, device, 1, SWITCH_MOMENTARY)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    frames(proc, device, 1500)
    set_setting(proc, device, ATTR_FADE_OFF, 0)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    assert frames(proc, device, 300) == [level_frame(1, 0, ramp=1500)]
    device.press_button(S1)
    pressed = frames(proc, device, 150)
    device.release_button(S1)
    tapped = frames(proc, device, 2500)
    assert pressed == [level_frame(1, MIN, ramp=1500)], [f.hex() for f in pressed]
    assert tapped == [level_frame(1, 255, ramp=1500)], [f.hex() for f in tapped]
    assert onoff(device) == 1
    # a tap off (at once: the MCU's ramp), a hold from dark during that ramp
    device.press_button(S1)
    frames(proc, device, 150)
    device.release_button(S1)
    assert frames(proc, device, 300) == [level_frame(1, 0, ramp=1500)]
    device.press_button(S1)
    sent = run_t(proc, device, 3000)
    device.release_button(S1)
    frames(proc, device, 300)
    assert sent[0][1] == level_frame(1, MIN, ramp=1500), sent[0][1].hex()
    rise = [(t, f) for t, f in sent[1:]]
    assert rise and rise[0][0] >= sent[0][0] + 1500 - 20, (sent[0][0], rise[0][0])
    assert rising(hk([f for _, f in rise])) and set(ramps([f for _, f in rise])) == {b"\x00\x00"}


SCENES, SCENE_ADD, SCENE_STORE, SCENE_RECALL = 0x0005, 0x00, 0x04, 0x05


def scene_add(device: Device, scene: int, seconds: int, on: int, lvl: int, ep: int = 1) -> None:
    fields = (0x0006).to_bytes(2, "little") + bytes([1, on]) + (0x0008).to_bytes(2, "little") + bytes([1, lvl])
    device.call_zigbee_cmd(ep, SCENES, SCENE_ADD, (0).to_bytes(2, "little") + bytes([scene]) +
                           seconds.to_bytes(2, "little") + b"\x00" + fields)


def test_a_scene_recalled_fades_the_channel_in_its_time():
    """The Scenes server on a TS110E channel: a recall is the command it stands for, with the
    scene's time (3 s, from dark at the minimum first) or, with 0, the fade settings."""
    proc, device, _ = start()
    scene_add(device, 1, 3, 1, 150)
    device.call_zigbee_cmd(1, SCENES, SCENE_RECALL, (0).to_bytes(2, "little") + b"\x01")
    sent = [(t, f) for t, f in run_t(proc, device, 4000) if f[4] == 1]
    levels = hk([f for _, f in sent])
    assert levels[0] == MIN and rising(levels) and levels[-1] == hk_out(zcl_value(150))
    done_at = next(t for t, f in sent if f[5] == levels[-1])
    assert 2800 <= done_at <= 3200, done_at
    assert onoff(device) == 1 and level(device) == 150
    # stored lit at 150, recalled from off with time 0: the fade on setting (1 s)
    device.call_zigbee_cmd(1, SCENES, SCENE_STORE, (0).to_bytes(2, "little") + b"\x02")
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    frames(proc, device, 2500)
    device.call_zigbee_cmd(1, SCENES, SCENE_RECALL, (0).to_bytes(2, "little") + b"\x02")
    sent = [(t, f) for t, f in run_t(proc, device, 2000) if f[4] == 1]
    assert hk([f for _, f in sent])[-1] == hk_out(zcl_value(150))
    done_at = next(t for t, f in sent if f[5] == hk_out(zcl_value(150)))
    assert done_at <= 1100, done_at
    assert onoff(device) == 1
