# home-dimmer builds

Short summary per module firmware build of this branch: the AVATTO ZDMS16-2 build with Open
Dimmer (the open-source firmware of its PY32F002A dimming MCU) carried, built on
`home-dimmer_build`. Each heading is the build as Zigbee2MQTT shows it (`swBuildId`) with its
date and the Open Dimmer version it carries. "Next" collects what the next build gets.

The fork's py32-bump workflow turns "Next" into the section of the build it starts, with the
carried Open Dimmer version, and opens an empty "Next"
(`helper_scripts/home_dimmer_changelog.py`). A build by hand gets its section the same way:
`home_dimmer_changelog.py stamp --build 1.1.3-<id> --carries X.Y.Z`. The upstream feature
changelog is [changelog_fw.md](changelog_fw.md); Open Dimmer's own changes are in the
zdms16-py32 release notes.

## Next

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
