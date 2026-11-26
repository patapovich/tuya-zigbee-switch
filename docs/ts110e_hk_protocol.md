# TS110E `_TZ3210_3mpwqzuu`: the dimmer MCU link

Found in the stock ZT3L firmware 1.0.0 (Tuya product key `zhzg_double_morerst_dimmer`) and matching the
Wi-Fi siblings of the same board family (ZH-Z06: OXT Wi-Fi 2C, GIRIER/Aubess; see
<https://github.com/PrawilnyAlo/esphome-oxt-dimmer> and elektroda topic 3985978).

## Hardware

- Radio: ZT3L (TLSR8258), pin-position compatible with the Wi-Fi CB3S.
- Dimmer MCU: HK32F030M (TSSOP-20) on the controller board, stock firmware, never answers.
- Pins (ZT3L): UART TX to the MCU = **B1** (pin 16), RX B7 (pin 15, unused by the MCU);
  wall switch S1 = **B4** (pin 13), S2 = **C0** (pin 10), both active low with pull-up;
  module button = **C3** (pin 6, pull-up); LED = **C2** (pin 7, active high: measured, lit with the pin high).
  B1/C3/C2 are confirmed in the stock firmware; S1 = B4 confirmed on a unit (2026-11-24), S2 = C0 follows from the CB3S mapping.

## Level frame (UART 9600 8N1, ZT3L -> MCU only)

12 bytes, one channel per frame (template in the stock .data):

| byte | value | meaning |
|---|---|---|
| 0-1 | `00 00` | preamble |
| 2-3 | `FF 55` | header |
| 4 | `01` / `02` | channel |
| 5 | level | L1 (`00` off .. `FF` max), used when byte 4 = 1 |
| 6 | level | L2, used when byte 4 = 2 |
| 7-8 | `05 DC` / `00 00` | MCU ramp: `05 DC` smooth (about 1.5 s from bright to dark, by eye), `00 00` immediate |
| 9 | `0A` | end |
| 10-11 | `00 00` | trailer |

The Wi-Fi boards send the 9-byte form `0A FF 55 ch L1 L2 05 DC 0A`; the leading `0A` of one frame is the
end byte of the previous one. A frame for channel 2 does not change channel 1. The MCU forgets the levels at a
power loss: send them again after start-up (this firmware: 1.5 s and 4 s after it). This firmware also sends each
channel's level again (ramp `00 00`) after 60 s without a frame for it, for an MCU that lost power alone. Such a
repeat of the same level at ramp `00 00` (the keep-alive, the start-up's second sending) goes only once the ramp
of the channel's last frame has run out (taken as ms), so it never cuts that ramp short. The range command goes
only at a power-up of the module (not after a reset of the module alone, when the MCU kept driving the light),
and not with these repeats (whether the MCU writes it to its flash is not known).

Stock use of the ramp: on/off and level commands with `05 DC`; hold dimming in +4 steps, each with `05 DC`;
`00 00` only in a test mode. Seen by eye on a unit (no instrument): `00 00` is at once, on and off; `05 DC`
(1500) takes about 1.5 s from bright to dark, and up it looks shorter, because an LED lamp stays dark through
the low part of the ramp (hence this firmware's minimum first from dark, at ramp 0, `kick` in `hk_dimmer.c`);
15000 looked the same as 1500, and 300 could not be told from it. So the MCU either caps the value at about
1500 or takes any value above 0 as one ramp of its own: not known yet (see "To check on hardware"). This
firmware's fades and holds send every step at `00 00`; `mcu_ramp` only goes with a change made at once.

## Range command (TuyaMCU-style)

`55 AA 00 FE 00 05 <channel mask> <min1> <max1> <min2> <max2> <sum>`, sum = 8-bit sum of all previous bytes.
The stock firmware sends it at start-up with the per-channel min/max (percent 1..100 scaled to 0..255,
kept in its NV). Assumed: the MCU maps a level into [min, max].

## The virtual Tuya MCU (this firmware, `hk_dimmer.c`)

The module's dimmer endpoints talk to `hk_dimmer.c` as to a ZDMS16-2 MCU, never over the wire: DPs 1..5 /
7..11 (on/off, level, min, switch type, max; levels 10..1000), power-on DP 14, and this firmware's own
settings DPs 0x70..0x79 (Basic 0xff30..0xff39) and transition time 0x7A / 0x7B. It follows the Open Dimmer
DP contract: a write is answered by a passive report (0x05) with the value now held (a refusal answers the
old value; a DP it does not keep gets no answer at all); a min or max write's answer also has the other limit
and the level (a min above the max pushes the max along, a max below the min the min; equal is allowed);
wall switch and button changes and the gestures of a wall switch (a momentary one's; in the detached mode any
type's) are active reports (0x06). The gestures are Open Dimmer's DP 105 / 106 (0x69 / 0x6A, enum: 1 single,
2 double, 3 triple, 4 hold, 5 release, 6 press (the fast press); never in the dump), each after what the light did. The module takes the virtual MCU as an Open Dimmer of build
500 (2.2.0, `HK_OD_BUILD` and `dimmer_own_mcu()` in `dimmer_cluster.c`) without it identifying as one (version
1.0.0), and the virtual MCU has Open Dimmer 2.2.0's DPs as its `src/dimmer.h` describes them: the countdown DP 6 / 12
(s, RAM only; written while off it stays 0; at its end an active report of on 0 and the countdown 0), the
per-channel power-on 103 / 104 (DP 14 still sets both and is answered with channel 1's and 103 / 104), and per
channel 162 + 2k + ch (values): k 0 wall switch mode (0 normal, 1 detached, 2 off), 1 level after a power cut (0 the
last one; kept in steps of 4), 2 identify (s, at most 255, not saved), 3 start boost (ms, steps of 50 up to 750), 4
fast press (0 / 1), 5 the lamp compensation (of 1000, kept in steps of 4, at most 252; Open Dimmer 2.2.8's DP 172 /
173); and 2.2.8's DP 180, both channels' switch options (bits set = off: 0 / 1 the long press from off's rise, 2 / 3
the double tap to full; written as mask << 8 | bits). The dump has, per channel, DPs 1..6 / 7..12, 103 / 104 and
162..173 (not identify); DP 14, DP 180 and the settings 0x70.. follow in a frame of their own. Open Dimmer 2.2.8's
custom curve DPs (174..179) and DP 181 it does not have: its custom curves are Basic 0xff3a / 0xff3b, and it counts
the wall switch presses of a network reset itself, from the module's `multi_press_reset_count` (as Open Dimmer's
DP 181 acts: closures of one switch within 10 s of the first, 0 never, 1..7 act as 8; the channel blinks 3 s, then
the reset request 0x03 goes). Its slow zones (Open Dimmer's DPs 152 / 157 and 154 / 159) are Basic attributes of the
module itself, 0xff3c / 0xff3d (`fade_off_slow_zone` L1 / L2) and 0xff3e / 0xff3f (`hold_slow_zone`), % of min..max.

The fades, holds and boosts are Open Dimmer's fader (`hk_fader.c`, a port of open-dimmer `dim_core.c`, its times
in ms): ticked every 10 ms (a 50 Hz half cycle, as its rates are per one) and sampled once per 20 ms frame. A fade
from dark starts with its first frame (the fader waits for it: the frame gap, a UART that refuses frames). The
wall switch logic is Open Dimmer's too (`controls.c`, `dimmer.c`): a momentary press from dark turns on at the
minimum at once, a double tap goes to full, the way of a lit hold is learnt per quarter of min..max and kept, a
switch closed at the start is no press, and at a power-up a state switch says what the light is (not after a reset
of the module alone: the MCU kept the light as it was, as a restart of the Zigbee module leaves Open Dimmer's). This
module's own: `hold_time` is the hold's pace over min..max (Open Dimmer's: over the whole 0..100 %), as before the
fader, so a narrowed range holds as long; a change the fade settings make at once (fade time 0)
goes in one frame with the MCU's own ramp (`mcu_ramp`; from dark the minimum first at ramp 0); the lamp
compensation takes the HK32's output as linear in the conduction time (not measured) and never sends a level below
the range's minimum (3).

The module writes one change in parts (the network's time, the on, then the level; tuya_secondary_mcu.c
sends one write at a time, each after the virtual MCU's answer): no frame goes to the dimming MCU while such
writes wait (40 ms at most), and a network time of 0 puts the fader there at once, lit or from dark, so the
frame after the level has it. Before, a Move to Level at once from off showed the level the channel had
before (full on a new unit) for one frame (20 ms), then the new one.

While the MCU runs its own ramp of a change made at once (`mcu_ramp` ms from that change's frame, taken as
ms; also while that frame still waits), only the MCU knows where the lamp is on it: a frame at ramp 0
would jump the lamp from there. So a change then goes at once too, in one frame with that ramp (assumed:
the MCU turns round from where it is, as the factory firmware's hold steps, each with `05 DC`, suggest),
whatever its fade settings or network time (a network time of 0 still at once, ramp 0), without the
minimum first (the lamp is lit on its way); a momentary press from dark sends its minimum with that ramp;
a hold stays where the lamp is going until that ramp's end, then moves (a hold from dark at least its
0.7 s). Before, an off 40 ms after an on at once (`fade_on_time` 0) went near full first, then faded
down, and an on 300 ms after an off at once dropped to the minimum first. With no fade time of 0 (or
`mcu_ramp` 0) no frame has the MCU's ramp, and nothing of this applies.

Its NV item (`NV_ITEM_HK_DIMMER`, layout 5) is laid out by the compiler: the Telink build aligns a struct to 4
bytes, so the deployed layout 4 item is 40 bytes there (36 in the stub) and layout 5 is 56 (54). A layout is read by
its own size, the largest first (a Telink read takes an item whose size is a multiple of the length asked for).

## Updating a unit that runs an earlier build

- Converter first: put the regenerated external converter into Zigbee2MQTT and restart it before the OTA
  (or with it), then Reconfigure after the OTA. With an older converter the device's `power_on_behavior` goes to
  endpoint 1 only, which this firmware takes as L1's (DP 103) and no longer as DP 14 for both channels.
- The wall switch presses of a network reset count from the update on, with the `multi_press_reset_count` the
  unit holds (default 10, stored but unused before): set it first (0: never) and read it back.
- Check on a spare unit, as the stub cannot (contact bounce): a toggle and a state switch count one closure per
  closing (N changes, N / 2 closures; the 50 ms debounce), none twice before the count is reached.
- The fades and the momentary switch change to Open Dimmer's (changelog): an off is slower at its end, a tap
  from dark lights at the press, a quick second tap goes to full (`double_tap_full` off for the old toggles), a
  hold from off waits 0.7 s at the minimum (`long_press_from_off`), a lit hold learns its way. A state switch
  closed at a power-up turns its channel on whatever the power-on setting; the OTA itself (a reset of the module
  alone) leaves every light as it was, whatever its state switch shows. `hold_time` still covers min..max.

## To check on hardware

None of this can be seen in the stub; each item says what the firmware assumes now. The board is not
isolated: no scope, logic analyzer or USB serial adapter on a unit on mains (only with an isolation
transformer, or on the bench without mains, the HK32 then fed from its own rail; on 3V3 alone it stays in
reset).

Output (the dimming MCU and the power board):

- [ ] **S2 = C0.** S1 = B4 is confirmed; S2 = C0 follows from the CB3S mapping of the Wi-Fi siblings. Wire a
  wall switch to S2 and check that L2 toggles and dims (L2 needs no lamp: watch it in Zigbee2MQTT). A wrong pin
  does no harm (an input with the pull-up); the fix is the HK token in `device_db.yaml` (`HKB4C0C3C2`) and
  `TS110E` in `tests/test_hk_dimmer.py`.
- [ ] **Level against conduction time.** Assumed linear (level 255 = full conduction), so Open Dimmer's curves
  and `lamp_compensation` are taken over as they are. One point is known: level 128 gave half power. Measure
  the low end, where it matters: a filament or halogen lamp on a power-metering plug, levels 1..255 (as
  `dimming_curve` linear), inverting P(a) = 1 - a/pi + sin(2a)/(2 pi) for the conduction angle; or the same
  lamp's light on a ZDMS16-2 with Open Dimmer and on the TS110E, both linear (Open Dimmer's output is linear in
  conduction time). A lux meter alone cannot tell the HK32 from the lamp.
- [ ] **Leading or trailing edge.** `device_db.yaml` says `output: triac` (the schema has no MOSFET value); the
  power board has a MOSFET part (Q2, DPAK), which suggests trailing edge. A photodiode on a filament lamp shows
  it, without contact. It decides which LED lamps dim well, not the firmware.
- [ ] **What the ramp value means.** See above: 0, 300, 1500, 5000, 15000, 65535, up and down with a few seconds
  between, filmed at 240 fps and the brightness per frame read out (e.g. ffmpeg signalstats); or a photodiode
  into an ADC. If any value above 0 is one fixed ramp, `mcu_ramp` (0..65535) is in effect on / off and its label
  should say so, and `hk_dimmer.c` should take that fixed time where it takes the value as ms (the keep-alive,
  `last_ramp_left`: how long a later change goes with the MCU's ramp and a hold waits).
- [ ] **A frame during the MCU's own ramp.** With `fade_on_time` 0: on, then off 40 ms and 300 ms later
  (two separate commands); with `fade_off_time` 0 and the default `fade_on_time`: off, then on 300 ms later;
  a momentary tap from dark with `fade_on_time` 0, then a hold at once. Each: no jump (the assumption: a
  frame with a ramp turns the MCU's ramp round from where the lamp is).
- [ ] **The range command.** Sent once at a power-up as 3..255 for both channels (min / max are done in the
  module). Assumed: the MCU maps a level into that range, so only levels 1..2 depend on it. Look whether level
  1 and 2 light at all and differ from 3; whether the MCU keeps the range in its flash is not known (it is not
  sent again with the keep-alive).
- [ ] **A long transition right after a wall switch hold.** Seen on a unit: a hold down, let go on its way, and
  0.6 s later a network change up over 120 s (Adaptive Lighting's): the lamp jumped bright at once, while a Stop
  10 s later read the fade's even position (the module was fading). Not seen without the hold, nor 1.5 s or 3 s
  after a hold that ended at the minimum. The stub sends nothing from the release until the fade's first level
  step, and then one level at a time at ramp `00 00` (`tests/test_hk_dimmer_long_transitions.py`), so either the
  wire has something else or the HK32 (or the lamp) reacts to the hold's dense ramp-0 steps (some 30 a second, a
  level each: the factory firmware sends +4 steps with `05 DC`). A debug build (`MCU_DUMP=1`) logs every frame
  but a hold's own steps and why it went (`hk_dimmer.c` HK_TRACE): repeat the case, read Basic 0xff41..0xff45
  within 10 s of the command and decode them (`helper_scripts/decode_mcu_diag.py --log ...`); the reference is
  `tests/test_hk_dimmer_trace.py`. Also: the same hold with no command after it (does the release alone jump?),
  and the case with a filament or halogen lamp.
- [ ] **A same-level frame at ramp 0** (the keep-alive after 60 s, the second sending after a power-up): no blip
  of a lit lamp.
- [ ] **The HK32's start-up time** against the first sending 1.5 s after a power-up (`HK_BOOT_DELAY_MS`): a lamp
  with power-on `on` or `previous` must light at the first sending, not only at the second (4 s).
- [ ] **B1 during a restart of the ZT3L** (an OTA, a config write, the watchdog): no stray bytes the HK32 takes for
  a frame (the light must not change while the module restarts).

Inputs and start-up:

- [ ] **S1 / S2 electrically.** Read as buttons with the pull-up and a 50 ms debounce (Open Dimmer: 20 / 25 ms).
  How long a contact stays closed after a release (a mains-sensing input can ripple), and how quick a double tap
  can be: watch `action` (single / double / triple) for taps at different speeds.
- [ ] **No settle time before the first read of the pins.** Open Dimmer waits 30 ms for the pull-ups; this
  firmware does not. A toggle switch closed at a power-up must not toggle the light at the start.
- [ ] **A toggle or state switch and the network reset by presses** (`multi_press_reset_count`): N changes are
  N / 2 closures; none counted twice (contact bounce).

The module:

- [ ] **Analog register 0x3b** (a restart of the module alone keeps the light): kept through the watchdog, an
  OTA reboot and a config write; cleared by a power cut, also a short one under 1 s. If a short cut keeps it, a
  power cut there would look like a restart of the module alone (the light stays as it was, no power-on
  behaviour).
- [ ] **Main-loop stalls.** Every fade and hold is frames from the ZT3L's main loop: an NV write, the stack busy
  or an OTA block write stops them for a while. Watch a 2 s fade and a hold during an OTA download and right after
  a setting change (an NV write): a short stop is expected, a jump at its end is not.
- [ ] **The at-once paths of this build**: `transition: 0` from off to a mid level and to the minimum lights at
  that level only, no flash of the old level; `fade_on_time` 0 with an on and an off right after it (an automation
  that fires twice) stays dark or at the minimum.
