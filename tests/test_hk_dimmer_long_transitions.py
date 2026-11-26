"""Long network transitions on the ZH-Z06 dimmers' virtual MCU (src/zigbee/hk_dimmer.c), as Adaptive
Lighting sends them: MoveToLevelWithOnOff to a lit channel with a transition of 20 s up to 600 s,
also right after a wall switch hold (a lamp seemed to jump there: a hold down, and 0.6 s after its
release the command to fade up over 120 s). The module fades it itself (hk_fader.c, frames at ramp
0), so a jump would be a frame ahead of the fade or one with the dimming MCU's own ramp: here every
frame is one level of the dimming MCU on from the one before, at ramp 0, a Stop reads the fade's
even position (the light is where the module says), and the move ends at its time. The times on
either side of 65.535 s (the most an Open Dimmer DP takes, in ms) and the 600 s cap
(HK_TIME_MAX_MS) are among them. CurrentLevel is the move's end from its start (by design: Home
Assistant shows the target). A known limit is marked xfail: a reset of the module alone during
such a move. tests/test_own_mcu_long_transition.py sends the same commands to Open Dimmer on the
ZDMS16-2; tests/test_hk_dimmer_ts110e_build.py runs these on the TS110E's flags."""
import pytest

from test_hk_dimmer import (ATTR_CURVE, CURVE_LED, S1, SWITCH_MOMENTARY, ZCL_ATTR_BALLAST_MIN,
                            ZCL_CLUSTER_BALLAST, hk_out, level, quiet, run_t, set_setting,
                            set_switch_type, start, zcl_value)

from tests.zcl_consts import (
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CMD_LEVEL_STOP,
    ZCL_CMD_ONOFF_ON,
)

ZCL_ATTR_BALLAST_MAX = 0x0011
MIN_LEVEL, MAX_LEVEL = 30, 160            # lightingBallastCfg: a narrowed range, as a user may set
MN, MX = 118, 630                         # ... as the virtual MCU's values (of 1000)
TIMES = (200, 600, 655, 660, 1200, 6000)  # tenths: 20, 60, 65.5, 66, 120 and 600 s
TARGET = 168


def move_z2m(device, lvl: int, tenths: int, ep: int = 1, with_onoff: bool = True) -> None:
    """MoveToLevel(WithOnOff) as Zigbee2MQTT sends it (options mask and override 0)."""
    cmd = ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF if with_onoff else ZCL_CMD_LEVEL_MOVE_TO_LEVEL
    device.call_zigbee_cmd(ep, ZCL_CLUSTER_LEVEL_CONTROL, cmd,
                           bytes([lvl]) + tenths.to_bytes(2, "little") + b"\x00\x00")


def end_of(tenths: int) -> int:
    """The ms a time takes on the virtual MCU (up to 600 s)."""
    return min(tenths * 100, 600000)


class Lamp:
    """L1 of a stub TS110E: a momentary wall switch, min 30 / max 160, the led curve. What went to
    the dimming MCU for L1 is kept: the level it has now (last), the ramps of every frame, and a
    clock (ms) for the times of the frames."""

    def __init__(self) -> None:
        self.proc, self.device, _ = start()
        self.t, self.last, self.ramps = 0, 0, []
        set_switch_type(self.proc, self.device, 1, SWITCH_MOMENTARY)
        self.device.write_zigbee_attr(1, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MIN, MIN_LEVEL)
        self.run(300)
        self.device.write_zigbee_attr(1, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MAX, MAX_LEVEL)
        self.run(300)
        set_setting(self.proc, self.device, ATTR_CURVE, CURVE_LED)

    def _take(self, sent) -> list[tuple[int, int]]:
        out = []
        for t, f in sent:
            if f[2:4] == b"\xFF\x55" and f[4] == 1:
                out.append((self.t + t, f[5]))
                self.ramps.append(f[7:9])
        if out:
            self.last = out[-1][1]
        return out

    def run(self, ms: int) -> list[tuple[int, int]]:
        """Time passes in 10 ms steps: L1's frames (when, level)."""
        out = self._take(run_t(self.proc, self.device, ms))
        self.t += ms
        return out

    def quiet(self, ms: int, step: int = 100) -> list[tuple[int, int]]:
        """Time passes in larger steps (a frame per step at most: levels may be skipped)."""
        out = self._take(quiet(self.proc, self.device, ms, step))
        self.t += ms
        return out

    def lit(self, lvl: int) -> None:
        """On at that level at once, settled and kept (NV)."""
        move_z2m(self.device, lvl, 0)
        self.run(6000)

    def hold(self, ms: int) -> None:
        """The wall switch held (ms after its debounce) and let go."""
        self.device.press_button(S1)
        self.run(ms)
        self.device.release_button(S1)
        self.run(100)

    def stop(self) -> None:
        self.device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_STOP, b"")


def end_level(lvl: int) -> int:
    return hk_out(zcl_value(lvl, MN, MX), CURVE_LED, MN, MX)


def steps_on(before: int, levels: list[int], up: bool) -> bool:
    """Each frame one level of the dimming MCU on from the one before (the first from where the
    lamp was): nothing ahead of the fade, nothing back."""
    d = 1 if up else -1
    return all(b == a + d for a, b in zip([before] + levels, levels))


def fades_in_its_time(lamp: Lamp, tenths: int, send=None, to: int = TARGET) -> None:
    """The command (send, else the AL form) fades from where the light is: CurrentLevel is the end
    at once; frames one level at a time at ramp 0; a Stop 10 s in (a minute in for 600 s, which
    moves less than a level of the dimming MCU in 10 s) reads the even position and leaves the
    light at the last frame."""
    stop_at = 10000 if end_of(tenths) <= 120000 else 60000
    frm, lamp_at = level(lamp.device), lamp.last
    (send or (lambda: move_z2m(lamp.device, to, tenths)))()
    lamp.ramps.clear()
    sent = lamp.run(stop_at)
    assert level(lamp.device) == to                              # the end, at once
    levels = [lv for _, lv in sent]
    assert levels and steps_on(lamp_at, levels, to > frm), (lamp_at, levels)
    assert set(lamp.ramps) == {b"\x00\x00"}
    expect = frm + (to - frm) * stop_at / end_of(tenths)
    lamp.stop()
    after = lamp.run(500)
    assert abs(level(lamp.device) - expect) <= 2, (frm, level(lamp.device), expect)
    assert len(after) <= 1 and all(abs(lv - levels[-1]) <= 1 for _, lv in after), (levels[-1], after)


def ends_at_its_time(lamp: Lamp, tenths: int, to: int = TARGET) -> None:
    """Left alone it gets to its end at its time, not before (the last level of the dimming MCU
    may come up to two values of 1000 early: it takes more than one value at the top of the
    curve)."""
    t0, lamp_at, frm = lamp.t, lamp.last, level(lamp.device)
    move_z2m(lamp.device, to, tenths)
    total = end_of(tenths)
    sent = lamp.run(10000)
    assert steps_on(lamp_at, [lv for _, lv in sent], True), (lamp_at, sent)
    sent += lamp.quiet(total - 10000 + 2000)
    levels = [lv for _, lv in sent]
    assert levels == sorted(levels) and levels[-1] == end_level(to), (levels[-1], end_level(to))
    done = next(t for t, lv in sent if lv == levels[-1]) - t0
    early = 2 * total // max(1, abs(zcl_value(to, MN, MX) - zcl_value(frm, MN, MX))) + 300
    assert total - early <= done <= total + 300, (total, done, early)
    assert set(lamp.ramps) == {b"\x00\x00"}


# ---- from a lit level -------------------------------------------------------------------------

@pytest.mark.parametrize("tenths", TIMES)
def test_a_long_transition_from_a_lit_level_fades_in_its_time(tenths):
    lamp = Lamp()
    lamp.lit(49)
    fades_in_its_time(lamp, tenths)


@pytest.mark.parametrize("tenths", TIMES)
def test_a_long_transition_from_a_lit_level_ends_at_its_time(tenths):
    lamp = Lamp()
    lamp.lit(49)
    ends_at_its_time(lamp, tenths)


@pytest.mark.parametrize("tenths", (600, 1200, 6000))
def test_a_long_transition_down_fades_in_its_time(tenths):
    lamp = Lamp()
    lamp.lit(TARGET)
    fades_in_its_time(lamp, tenths, to=49)


def test_a_time_over_600_s_is_capped_at_600_s():
    """0xFFFE tenths (6553.4 s, the most ZCL has): HK_TIME_MAX_MS."""
    lamp = Lamp()
    lamp.lit(49)
    frm = level(lamp.device)
    move_z2m(lamp.device, TARGET, 0xFFFE)
    lamp.run(10000)
    lamp.stop()
    lamp.run(500)
    assert abs(level(lamp.device) - (frm + (TARGET - frm) * 10 / 600)) <= 2


# ---- right after a wall switch hold -----------------------------------------------------------

@pytest.mark.parametrize("tenths", TIMES)
def test_a_long_transition_right_after_a_hold_fades_in_its_time(tenths):
    """A hold down from the lit level, let go on its way (not at the minimum), the command 0.6 s
    after the release: the fade goes on from where the hold left the light."""
    lamp = Lamp()
    lamp.lit(TARGET)
    lamp.hold(1600)
    assert 40 < level(lamp.device) < 130                         # let go on the way
    lamp.run(500)
    fades_in_its_time(lamp, tenths)


@pytest.mark.parametrize("tenths", (200, 660, 1200))
def test_a_long_transition_right_after_a_hold_ends_at_its_time(tenths):
    lamp = Lamp()
    lamp.lit(TARGET)
    lamp.hold(1600)
    lamp.run(500)
    ends_at_its_time(lamp, tenths)


@pytest.mark.parametrize("gap", (0, 100, 540, 900, 1400, 2900))
def test_a_long_transition_after_three_holds_fades_in_its_time(gap):
    """Down, up within 2 s of it (a correction), down again, let go at a low level; the command
    0.1 .. 3 s after the last release (the field case: 0.64 s)."""
    lamp = Lamp()
    lamp.lit(TARGET)
    lamp.hold(1600)
    lamp.run(2900)
    lamp.hold(1000)
    lamp.run(300)
    lamp.hold(1700)
    if gap:
        lamp.run(gap)
    fades_in_its_time(lamp, 1200)


def test_a_long_transition_right_after_a_hold_to_the_minimum_fades_in_its_time():
    """The hold reaches the minimum and waits there before the release."""
    lamp = Lamp()
    lamp.lit(TARGET)
    lamp.hold(4000)
    assert level(lamp.device) <= 2
    lamp.run(500)
    fades_in_its_time(lamp, 1200)


# ---- an on and a level to a lit channel -------------------------------------------------------

def test_an_on_then_a_level_with_a_long_time_on_a_lit_channel_fades_in_its_time():
    """genOnOff On (the channel is lit already), then MoveToLevel without OnOff in 120 s."""
    lamp = Lamp()
    lamp.lit(49)

    def send():
        lamp.device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
        lamp.run(200)
        move_z2m(lamp.device, TARGET, 1200, with_onoff=False)

    fades_in_its_time(lamp, 1200, send=send)


@pytest.mark.parametrize("after", (300, 5000))
def test_an_on_during_a_long_transition_leaves_it_going(after):
    """A plain On to the lit channel during the move (within the 1.5 s the module takes a time
    back in, and later): the on changes nothing, the move goes on in its time."""
    lamp = Lamp()
    lamp.lit(49)
    frm, lamp_at = level(lamp.device), lamp.last
    move_z2m(lamp.device, TARGET, 1200)
    sent = lamp.run(after)
    lamp.device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    sent += lamp.run(10000 - after)
    assert steps_on(lamp_at, [lv for _, lv in sent], True), (lamp_at, sent)
    assert level(lamp.device) == TARGET
    lamp.stop()
    lamp.run(500)
    assert abs(level(lamp.device) - (frm + (TARGET - frm) * 10 / 120)) <= 2


def test_the_same_command_again_during_a_long_transition_keeps_it():
    """Adaptive Lighting sends its target again at its next update (the same level and time, a
    minute later): the move goes on as it was, not again from where it is."""
    lamp = Lamp()
    lamp.lit(49)
    frm, lamp_at = level(lamp.device), lamp.last
    move_z2m(lamp.device, TARGET, 1200)
    sent = lamp.run(10000) + lamp.quiet(50000)
    before = lamp.last
    move_z2m(lamp.device, TARGET, 1200)
    again = lamp.run(10000)
    levels = [lv for _, lv in sent]
    assert levels == sorted(levels) and steps_on(before, [lv for _, lv in again], True)
    lamp.stop()
    lamp.run(500)
    assert abs(level(lamp.device) - (frm + (TARGET - frm) * 70 / 120)) <= 2
    assert lamp_at < before


# ---- a known limit --------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "known limit: a reset of the module alone (the watchdog) during a timed move warm-starts at "
    "the level NV has and sends it at ramp 0, while the dimming MCU still shows the last frame's: "
    "the hold's level is not saved yet (5 s), and nv_busy() puts every save off for the whole "
    "move, so NV has the level from before the hold (a save during the move would give the "
    "move's end instead). A planned restart saves where the fade is (hk_dimmer_before_reboot)."))
def test_a_reset_during_a_long_transition_after_a_hold_leaves_the_light_where_it_is():
    from test_hk_dimmer import hk
    lamp = Lamp()
    lamp.lit(TARGET)
    lamp.hold(1600)
    lamp.run(500)
    move_z2m(lamp.device, TARGET, 1200)
    lamp.run(500)
    assert lamp.proc.exec("reboot").ok and lamp.proc.wait_for_exit(2.0)
    _, _, boot = start()
    assert hk(boot) and all(abs(lv - lamp.last) <= 2 for lv in hk(boot)), (lamp.last, hk(boot))
