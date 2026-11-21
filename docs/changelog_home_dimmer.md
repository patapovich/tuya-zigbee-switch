# home-dimmer builds

Short summary per module firmware build of this branch: the AVATTO ZDMS16-2 build with Open
Dimmer (the open-source firmware of its PY32F002A dimming MCU) carried, built on
`home-dimmer_build`. Each heading is the build as Zigbee2MQTT shows it (`swBuildId`) with its
date and the Open Dimmer version it carries. "Next" collects what the next build gets.

The fork's py32-bump workflow turns "Next" into the section of the build it starts, with the
carried Open Dimmer version, and opens an empty "Next"
(`helper_scripts/home_dimmer_changelog.py`). A build by hand gets its section the same way:
`home_dimmer_changelog.py stamp --build 1.1.3-<id> --carries X.Y.Z`. The upstream feature
changelog is [changelog_fw.md](changelog_fw.md); Open Dimmer's own changes are in its
[release notes](https://github.com/patapovich/open-dimmer/releases).

## Next

- Carries Open Dimmer's bootloader v3 for the SWD job `update_bootloader`: images up to 26 KB,
  their part past 14 KB in the dimmer chip's hidden flash (open-dimmer docs/BOOTLOADER.md
  section 18). The carried image may now be up to 26 KB (`IMAGE_MAX`); a chip under bootloader
  v2 declines an image over 14 KB and keeps its app.
- Open Dimmer 2.2.8's long press from off per channel (genOnOffSwitchCfg 0xff15, DP 180 / 181;
  Zigbee2MQTT `long_press_from_off`, on by default): off, a long press from off brings the light
  on at its level and the hold goes on from there instead of rising from the minimum. Shown and
  written only for an MCU that has it (its dump tells).
- Open Dimmer 2.2.8's custom dimming curve per channel: `dimming_curve` `custom` and its table
  `custom_curve` (lightingBallastCfg 0xff12, 17 points 0..10000): passed on to the dimmer chip
  (DPs 174..179), which saves it, and shown as the chip reports it; made for a lamp by
  open-dimmer's `tools/custom_curve.py`.
- Open Dimmer 2.2.8's lamp compensation per channel (lightingBallastCfg 0xff11, in levels
  0..63, DP 172 / 173; Zigbee2MQTT `lamp_compensation`): for LED lamps that are brighter
  dimmed down than switched on at the same level. Shown and written only for an MCU that has
  it (its dump tells; any other one: none, a write sends nothing).

- The dimmer chip's health report reaches the hub seconds after a restart of the module or of
  the chip (Open Dimmer 2.2.7 sends it 3 s after the two made contact; it came up to 2 min
  after the module's start). A report made while the module is not on a network is not
  counted as the one sent.

## 1.1.3-d983b68a (2026-11-21), carries Open Dimmer 2.2.6

- A wall switch hold with Open Dimmer is followed from its hold gesture to its release: a level
  command that arrives during the hold (a hub's periodic brightness update) no longer holds
  the hold's level back for 3 s, a Move command stops for the hold, and lights bound to the
  switch end at the level the hold ended at.
- Stop stops a transition (a level command with a time) where the light is, and a Move or
  Step during one starts from there instead of from the transition's end (Open Dimmer 2.1.14
  or newer; lit-to-lit transitions).
- A wall switch on, or a MoveWithOnOff up from off, right after a command with a transition
  time no longer runs in that command's time.
- An auto-off countdown survives a restart of the dimmer chip (its update, a watchdog reset):
  the rest of the time is written again, or the light goes off if the time has passed.
- Open Dimmer updates: the wait after the light went off is 70 s (was a minute; a transition
  to off can take 65.5 s) and it also runs from the module's start when the light is off then.
  An update is offered only to a running Open Dimmer, not while the chip sits in its
  bootloader.
- The dimmer chip's health report (uptime, reset cause, temperature) is sent to the hub at
  the first round after the chip restarted alone (an update, a watchdog reset), then every
  10 min as before. It could wait up to 10 min for its turn.
- Two-wire install: `abort` after the read protection was removed keeps the install armed
  while the dimmer chip is silent (status error 16; it goes on at the next power-up, and
  abort drops it once the chip answers), and `install` is refused while such an install waits.
  An install taken up after a power cut during the option bytes keeps its relaxed checks over
  three more power-ups. An install taken up later identifies the chip before it writes, and
  ends at once when the bootloader is already in place.

## 1.1.3-09a180fa (2026-11-20), carries Open Dimmer 2.2.6

- Open Dimmer updates wait for the light to be off: the MCU's bootloader keeps the light dark
  for the few seconds it writes, so an update while the light was on made it go off and on
  again. The update now goes a minute after the light was switched off (at once if it was off
  already), a day later at the latest.

## 1.1.3-8ea2f5f2 (2026-11-20), carries Open Dimmer 2.2.6

- Carries Open Dimmer 2.2.6.
- Every build's OTA images are also the assets of its own release (tag
  `zdms16-2-<build>`), and the index files point there: GitHub counts those downloads. Same
  files and sha512, same index address.

## 1.1.3-2b74dd50 (2026-11-20), carries Open Dimmer 2.2.5

- No "off" at a restart: after an update (or any restart) the stack reported the dimmer's
  defaults (off, level 0) before the MCU had told the light's state, and a hub took that as the
  light going off (Adaptive Lighting then dropped a level set by hand). The reports now wait
  until every dimmer knows its state (10 s at most).

## 1.1.3-69efbba2 (2026-11-20), carries Open Dimmer 2.2.5

- Carries Open Dimmer 2.2.5.

## 1.1.3-aa9ab5d4 (2026-11-20), carries Open Dimmer 2.2.4

- A wall switch hold from dark is followed in real time: the module ends its 3 s level holdoff
  (taken after writing the wall switch's OnLevel) when Open Dimmer reports the hold, so the
  hub and the bound lights get the hold's level every 0.5 s instead of the OnLevel during the
  hold and the real level about 2 s after the release.

## 1.1.3-796e77e9 (2026-11-20), carries Open Dimmer 2.2.4

- Rebuilt, nothing new; carries Open Dimmer 2.2.4.

## 1.1.3-c47efc61 (2026-11-20), carries Open Dimmer 2.2.4

- The two-wire install is on by default: a module whose stored config has no SWD token (written
  before the board default had it, e.g. by hand) gets the board's `SWD3D7;` once with this
  update. Soldering the two wires then installs Open Dimmer at the next power-up; a module
  without the wires only reads one pin at start-up. A token removed by hand later stays removed.
- Every change of home-dimmer gets its module firmware built (the OTA images on
  home-dimmer_build), not only a new Open Dimmer release.

## 1.1.3-8bf3a03c (2026-11-20), carries Open Dimmer 2.2.4

- Scenes are forgotten when the module leaves its network (group 0's too, which no group
  removal drops): a module paired again no longer recalls what the old network stored.
- `brightness_fade_time` shows 2 (s) when unset instead of nothing ("unknown" in Home
  Assistant), and a written 2 is stored as unset: the module's default is the whole range in
  2 s; the preset default sets it.

## 1.1.3-3a64905e (2026-11-20), carries Open Dimmer 2.2.4

- The module firmware is built for size (its own code; the Zigbee SDK as before): about 5 KB
  more room in the OTA image for later features. A build check refuses a switch the Telink
  compiler would turn into a wrong jump (it made the Scenes commands restart the module).
- MCU link diagnostics: a factory MCU's late answer to a brightness write (it answers after its
  fade, ~1 s) is no longer logged as an ACK timeout.

## 1.1.3-a1b1d4ec (2026-11-20), carries Open Dimmer 2.2.4

- Rebuilt, nothing new; carries Open Dimmer 2.2.4.

## 1.1.3-2973d678 (2026-11-20), carries Open Dimmer 2.2.4

- Rebuilt, nothing new; carries Open Dimmer 2.2.4.

## 1.1.3-b9e411c1 (2026-11-20), carries Open Dimmer 2.2.4

- Carries Open Dimmer 2.2.4.

## 1.1.3-ee5a9c9f (2026-11-20), carries Open Dimmer 2.2.3

- Open Dimmer without a probe: solder two wires from the module's free pads D3 and D7 to the
  MCU's SWD pads (CN2 SWC and SWD), power the module on, and it installs Open Dimmer by itself
  (a factory MCU: read protection removed, which erases it; the bootloader written; the app
  sent over the UART; Open Dimmer's default settings). The SWD master is on by default
  (`SWD3D7` in the board's config); without the wires it reads one pin per start-up and nothing
  else. It installs once, at the first start-up that sees the wires; an MCU that runs Open
  Dimmer already is left alone. The wires need no resistors: the module drives SWDIO
  open-drain and SWCLK only after finding it free. With the device option `mcu_flasher: show`, `mcu_flasher_status` tells what it
  did, and `mcu_flasher` starts the manual jobs: `survey_dp`, `survey_full` (identity, option
  bytes, checksums, app header; read only), `update_bootloader` / `update_bootloader_force`
  and `install` (writing jobs: the job's name into `mcu_flasher_confirm` first), `abort`.
  Wires soldered before the conversion: the factory MCU holds its SWD pins until the module
  is power-cycled; `mcu_flasher_status` is not needed to see it, `mcu_firmware` then says
  "Tuya (stock): power-cycle the module to install Open Dimmer" (also while the install runs
  or after it failed), and configure binds the Basic cluster for the SWD master's reports.
- Z2M converter: the stock module's definition (AVATTO ZDMS16-2, the `_TZE284_jtbgusdc` and
  `_TZE204_jtbgusdc` manufacturer names only) is in `switch_custom.js` with OTA, so the conversion
  is offered with this one converter file (`tuya_with_ota.js` is not needed for it). Configure
  interviews a converted module first when Zigbee2MQTT still has the stock firmware's endpoints
  (1 and 242): its interview right after the OTA can answer from its database before the module
  is back, and the reconfigure then failed on the missing endpoint 2.
- Green Power is left out of the ZDMS16-2 build (`src/telink/patch_sdk/gp_stub.c`): this
  firmware never starts it, so a GP frame never got past the stack; now the frame is dropped at
  once. The OTA image is 6.2 KB smaller (room for the SWD master and Scenes). The OTA files are
  now linked with the board's own flags (they were linked again without `DIMMER_ONLY`), and the
  OTA step runs the symbol checks on that link.
- The OTA server's side is left out of the ZDMS16-2 build (the module only ever receives
  images): the SDK's `ota.c` and `zcl_ota.c` are compiled through
  `src/telink/patch_sdk/ota_client_only.c` and `zcl_ota_client_only.c`, with the SDK's flags.
  A client-to-server OTA command (which only a server receives) is answered as unsupported.
  The OTA image is 2.3 KB smaller.
- Zigbee Scenes on both channels (genScenes: Zigbee2MQTT's `scene_store`, `scene_add`,
  `scene_recall`, `scene_remove`, also for groups). A scene keeps the channel's on/off state and
  brightness; a recall fades in the scene's transition time, or with the channel's fade
  settings when the scene has none (a stored scene, one added without a transition). 16 scenes
  for the module, kept over power cuts; a group's scenes go when the channel leaves the group.
  Zigbee2MQTT lists the Scenes cluster after a re-interview of the device. Other boards: no
  change.
- Direct control from the wall switch: each channel's wall switch has an endpoint of its own
  (3 and 4, Dimmer Switch with On/Off and Level Control clients); lights or groups bound to it
  in Zigbee2MQTT (Bind, source endpoint 3 / 4) follow it without the coordinator. Normal wall
  switch mode: its on and off go out as On / Off, and a hold dims the bound lights along with
  it: one move in the hold's direction at its measured speed (the module reports the level
  every 0.5 s through a hold; Open Dimmer from 2.2.4), then a stop and a move to the level
  the hold ended at. Detached mode (Open Dimmer): a press
  toggles, a hold dims (up and down by turns, at DefaultMoveRate or 50 levels/s), a double
  press sets full brightness. On, off, toggle and move-to-level are sent with an APS
  acknowledgement, so a bound light that misses one gets it again; groups are sent without
  one. Nothing is sent without a binding; the light entities stay as they were.
- Settings for that, per channel (Open Dimmer settings in Zigbee2MQTT): `bind_mode` auto (as
  above, by the wall switch mode), off, mirror (only following the wall switch) or remote (only
  its gestures, also in the normal mode where the channel acts too); `double_press_action` and
  `triple_press_action`: none, full, off, or scene_1 / scene_2 = Recall Scene 1 / 2 of the
  group the wall switch's Scenes binding goes to (store the scene with Zigbee2MQTT's
  scene_store). Defaults: auto, full, none. The wall switch endpoints also have a Scenes client
  for that binding.

## 1.1.3-ab06b9a0 (2026-11-19), carries Open Dimmer 2.2.3

- Conversion from the stock firmware: the outgoing NWK frame counter starts at 0x10000000 once
  (it lived in flash at a much lower value). Routers still on stock Tuya firmware remembered
  the counter the stock firmware had reached, including the thousands of frames of the
  conversion OTA itself, and dropped the module's frames as replays until it caught up
  (romasku/tuya-zigbee-switch#255, upstream PR #557). A module converted before keeps its
  counter if it is already higher.

## 1.1.3-781f0ced (2026-11-19), carries Open Dimmer 2.2.3

- Rebuilt, nothing new; carries Open Dimmer 2.2.3.

## 1.1.3-b8057066 (2026-11-19), carries Open Dimmer 2.2.3

- Carries Open Dimmer 2.2.3.

## 1.1.3-e45f44da (2026-11-19), carries Open Dimmer 2.2.2

- Z2M converter: the channels are `l1` / `l2` (were `dimmer_left` / `dimmer_right`), as the
  stock definition of zigbee-herdsman-converters names them; `mcu_version`,
  `multi_press_reset_count`, `device_config` and `device_config_ext` are on the device, not on
  the first channel. Home Assistant gets new entities for them (unique ids change): rename the
  old entities to the new ids first to keep their history.
- Open Dimmer times in seconds (`fade_on_time`, `fade_off_time`, `hold_time`, `start_boost`;
  the module keeps ms); `soft_dim_end` / `hold_dim_end` are `fade_off_slow_zone` /
  `hold_slow_zone`; labels say what a setting is (Wall switch type / mode / fast press, On
  level (wall switch), Power-on level); `on_level` has a preset previous (255);
  `mcu_temperature_calibration` is no longer shown.
- The module's rescue paths (crash loop, button window, power-on check) can install Open
  Dimmer images over 12 KB again: a bootloader run of the MCU (power-on or link probe, rescue,
  button) over an app whose settings are already in the upper place (RING_HIGH, 2.1.14 and
  later) gets the image without waiting for the app's health report. So such a unit also gets
  a newer image at the power-on check, without the ~60 s wait. Offers to the app still wait
  for the report.

## 1.1.3-6598b921 (2026-11-19), carries Open Dimmer 2.2.2

- A plain on (no brightness) is always the module's own on (`fade_on_time`, the level it
  kept), also with the Z2M option `transition` set or after an off with a transition; the
  option times brightness changes and off. Factory MCUs get on and no level for it, as before.
- No transition while the MCU version is not known yet (just paired): the module's fade, not
  at once (Open Dimmer jumped until the version was read).
- `brightness_fade_time`: a change during a running fade (a slider drag) is timed from where
  the light is, not from the last target, so the speed stays even.

## 1.1.3-bf2e8f2b (2026-11-19), carries Open Dimmer 2.2.2

- Transitions (Open Dimmer 2.1.14 and later): transition 0 is instant; without a transition the
  module's own fade; the Z2M device option `transition` (e.g. 1.2) sets a default.
- `brightness_fade_time` per channel: the fade of brightness changes while the light is on, as
  the time for the whole range (a smaller change is quicker); empty: the module's 2 s.

## 1.1.3-888bbad4 (2026-11-19), carries Open Dimmer 2.2.2

- Carries Open Dimmer 2.2.2.

## 1.1.3-d15ab6b1 (2026-11-19), carries Open Dimmer 2.2.1

- Carries Open Dimmer 2.2.1.
- The light comes on at once again after a power cut (Open Dimmer on the v2 bootloader): while
  the module carries an image it may not send yet, the bootloader's start-up check now gets an
  answer that there is nothing new, instead of no answer and a 3 s wait.
- Zigbee2MQTT learns a new Open Dimmer version after an update of the dimming MCU alone: the
  module's version report reaches it for every Open Dimmer (Reconfigure once). Modules with the
  factory MCU: no change.

## 1.1.3-f596756a (2026-11-19), carries Open Dimmer 2.2.0

- Wall switch actions (single, double, hold, ...) reach Zigbee2MQTT again: the builds
  1.1.3-5bb263e7 and 1.1.3-522e1825 left the gesture cluster out.
- After a restart of the dimming MCU an identify still running is reported as ended, like the
  countdown; an impossible countdown from the MCU is taken as a day at most.
- An Open Dimmer update that starts over is seen as such also during the pause after a failed
  one.

## 1.1.3-522e1825 (2026-11-19), carries Open Dimmer 2.2.0

- Carries Open Dimmer 2.2.0.
- Open Dimmer 2.2.0 settings (shown in Zigbee2MQTT for Open Dimmer 2.2 and later only; a module
  with the factory MCU keeps its entities as they were): `countdown` (auto-off, also ZCL
  OnWithTimedOff: the longer time wins), `switch_mode` (normal / detached / off), `fast_press`
  (in the detached mode a press is the action `press` at once), `current_level_startup`
  (brightness after a power cut), `start_boost` (for LED drivers that need a kick), `identify`.
- Health of the dimming MCU as diagnostic sensors (Open Dimmer 2.2): uptime, reset cause,
  zero-cross and UART losses, temperature, its sensor's calibration, whether its settings have
  moved.
- `execute_if_off` per channel: a brightness change while off sets the brightness the light comes
  on at (the factory MCU too: the module keeps it). `on_level_local`: the wall switch's own on
  level, apart from `on_level` (Open Dimmer 2.2). Both shown for Open Dimmer 2.2.
- Dimming by a held button (ZCL Move) follows the remote's rate; Open Dimmer moves smoothly
  between the steps.
- The dimmer endpoints are Dimmable Lights (device id 0x0101).
- Ready for Open Dimmer images over 12 KB (up to 14 KB): such an image is offered and sent only to
  a module whose MCU has reported that its settings moved (2.1.14 and later).

## 1.1.3-5bb263e7 (2026-11-19), carries Open Dimmer 2.1.14

- Carries Open Dimmer 2.1.14.
- Transition time: a Zigbee transition (Home Assistant's `transition`) is honoured by Open
  Dimmer from 2.1.14 (build 400): the change takes exactly that long at one even rate, on, off
  and level alike. Older Open Dimmer versions and factory MCUs fade by their own settings, as
  before (nothing more is sent to them).
- Smaller firmware: the ZDMS16-2 build leaves out what only other boards use (switch, relay
  and cover endpoints, GPIO buttons, LEDs and battery), about 14 KB of the OTA file, so new
  features and a larger Open Dimmer image fit below the SDK's size limit. The device works as
  before; tokens of those features in a hand-written device config are ignored.

## 1.1.3-db24e888 (2026-11-19), carries Open Dimmer 2.1.13

- Carries Open Dimmer 2.1.13.
- Open Dimmer bootloader v2 support: update offers go by build number, the carried image is
  checked as the bootloader checks it, the bootloader's power-on probe, rescue and link-probe
  requests are answered and shown in the link diagnostics; the contract the bootloader relies
  on is fixed in `tuya_mcu_ota.h`.
- Factory MCUs: frames never add up to more than 23 bytes within 2 ms and none is longer than
  29 bytes; the settings dump is asked once more per start when the first one got no answer.
- Board builds check that only boards that carry an MCU image have Open Dimmer support.

## 1.1.3-ed1016b5 (2026-11-18), carries Open Dimmer 2.1.12

- Carries Open Dimmer 2.1.12; the module code is that of 1.1.3-194952ee.

## 1.1.3-194952ee (2026-11-18), carries Open Dimmer 2.1.11

- OnLevel is kept across restarts; On to a channel that is already on keeps its level.
- An on/off the MCU answers late is matched by its value, not taken for a key press.
- The MCU is asked again when its version or identity answer is lost; routers receive MCU
  bursts of up to 512 bytes; time checks no longer go wrong when the system timer wraps.
- Z2M: `mcu_firmware` (Open Dimmer / Tuya (stock)), `mcu_version` shows the version alone,
  labels "MCU version" and "MCU firmware", device option `open_dimmer_settings`.
- The build refuses an OTA file over the SDK limit (0x34000 bytes); release builds leave out
  printf (3.7 KB less flash).

## 1.1.3-c28a892e (2026-11-17), carries no Open Dimmer image

- The secondary MCU support of upstream PR #558 with the ZDMS16-2 dimmer, plus the MCU link
  diagnostics as Basic cluster attributes (fork builds only).
