_Open the **Outline** (table of contents) from the top right._

# Firmware Changelog

<!------------------------------------------------------

Please describe what you are working on, under ## Upcoming

------------------------------------------------------->

## Upcoming

- **Secondary MCU** (Tuya MCU protocol over UART) for boards where a second microcontroller
  does the switching or dimming: relays, touch keys, dimmers, power-on behaviour, datapoints
  written at boot. Config tokens are listed in [porting.md](contribute/porting.md#secondary-mcu)
  - MCU firmware version shown in Z2M (`mcu_version`)
  - An on/off the MCU answers late or with the other state: Zigbee shows the MCU's state and
    does not take the answer for a key press
  - A lost version answer is asked again (twice, 2 s apart); routers receive bursts of up to
    508 bytes from the MCU (was 252)
  - The settings dump is asked once more (once per start) when the first one got no answer
    (an MCU that started to listen late kept its min/max brightness unknown until the next
    restart)
  - Frames to a factory MCU never add up to more than 23 bytes within 2 ms (the later one
    waits 2 ms, in order), and none is longer than 29 bytes
  - Board builds check that only boards that carry an MCU image have Open Dimmer support
- **AVATTO ZDMS16-2 2-gang dimmer module** (`TS0601`, `_TZE284_jtbgusdc`, `_TZE204_jtbgusdc`,
  `_TZE204_o9gyszw2`)
  - Level Control (on, off, move, step, move to level, OnLevel), levels scaled to the MCU's
    min/max brightness, wall switch type and power-on behaviour
  - OnLevel is kept across restarts; On to a channel that is already on keeps its level
  - The module button resets the device
  - Link diagnostics as Basic cluster attributes
  - Open Dimmer, the open-source firmware for its PY32F002A dimming MCU: carried in the image,
    which updates an MCU that runs an older Open Dimmer version (a stock MCU is never touched)
  - Open Dimmer bootloader v2: offers by build number, the carried image checked as the
    bootloader checks it, its power-on probe, rescue and link-probe requests served and shown
    in the link diagnostics with the MCU's build, bootloader version and boot timing; the
    promises the frozen bootloader relies on are written down in `tuya_mcu_ota.h`
  - Z2M: `mcu_firmware` says Open Dimmer or Tuya (stock), `mcu_version` shows the version alone;
    the Open Dimmer settings (dimming curve, fade and hold, per-channel power-on behaviour, wall
    switch gestures) follow the device option `open_dimmer_settings` (auto / show / hide; the
    old name `own_mcu_settings` still works)
- **Tuya TS110E 2-gang dimmer module** (`_TZ3210_3mpwqzuu`, ZT3L + ZH-Z06): converted by OTA
  from stock, the factory dimming MCU (HK32F030M) is kept. It only takes level frames on a
  one-way UART, so the module runs a virtual Tuya dimmer MCU (`HK` token, `hk_dimmer.c`): the
  dimmer endpoints work as on the ZDMS16-2 (levels scaled to min/max, switch type, power-on
  behaviour kept in NV), the wall switches S1/S2 (toggle, state, momentary with hold dimming),
  the module button and the network LED are read and driven by the module. Protocol:
  [ts110e_hk_protocol.md](ts110e_hk_protocol.md)
  - Z2M brightness changes without a transition fade by the module's fade settings (the
    converter sends "no time", 0xFFFF, as for Open Dimmer); the `transition` option up to 600 s
  - The fades, holds and boosts are Open Dimmer's own (its fader, `dim_core.c`, ported as
    `hk_fader.c`, ticked every 10 ms and sampled at each 20 ms frame; Open Dimmer's host tests
    of it ported too). What changes for a unit that runs an earlier build of this support:
    - an off slows down in its lowest part (`fade_off_slow_zone`, default 15 % of min..max) and
      stays 80 ms at the minimum before dark: from full about 1.72 s at the default 1.5 s (was
      1.5 s); a network time keeps its length (the 80 ms taken out of it, linear, no slow end);
      an off at once from the network goes to the minimum, then dark 80 ms later
    - a level change while lit takes Open Dimmer's own rate, 2 s for the whole range
      (brightness_fade_time unset; was fade_on_time's / fade_off_time's share); fade_on_time is
      the fade from dark, fade_off_time the off's; a fade time of 0 (an on from dark, an off, an
      on after a tap from dark) still goes at once with the dimming MCU's own ramp
    - a momentary switch from dark turns on at once at the minimum at the press (reported then;
      was at the release); a tap goes on from there to the level (or the OnLevel the module
      writes after the on); held, the light waits 0.7 s at the minimum, then rises, slowly at
      first (`hold_slow_zone`, default 25 % of min..max; also going down, not for a lit hold up
      nor a lit hold with the custom curve); a full rise from dark at the default 4 s takes
      about 5.3 s (was 4.4 s). `hold_time` is still the pace over min..max (Open Dimmer's:
      over 0..100 %), so a narrowed range holds as long as before outside the slow zone. A
      press from off while the light still fades out holds from where it is (Open Dimmer's)
    - a double tap of a momentary switch (the second within 0.4 s of the first's release) takes
      the light to max_level, from off and from on (`double_tap_full`, on by default; a tap and
      a hold still dim, a third tap is a plain one)
    - `long_press_from_off` (on by default): off, a long press from off comes on at the level
      and the hold dims from there
    - a lit hold's way: within 2 s of the last hold's end the other way, else the way learnt for
      the quarter of min..max it starts in (at first the far end; a hold let go within 1.2 s and
      turned round at once teaches its quarter the other way, a confirmed one needs two such
      misses); kept in NV (was: strict alternation since the light came on)
    - a start boost ends after 190 ms of a 200 ms setting (Open Dimmer's: 19 ticks at full); an
      off during a boost goes out the usual way from the minimum (80 ms there; was: dark at once)
  - A switch closed at the start is no press (Open Dimmer's): a momentary switch or the module
    button held through a power-up does nothing at its release (was: a toggle); a state switch
    says what the light is at a power-up, over the power-on setting (not after a restart of the
    module alone: the light stays as it was), and when its type is set to state (with an active
    report); a toggle switch closed at the start toggles at its first change as before
  - `lamp_compensation` (Open Dimmer 2.2.8's, its DP 172 / 173 and the module's
    lightingBallastCfg 0xff11): after the light came down, up to that many levels less at the
    minimum (the HK32's output taken as linear, which has not been measured; never below its
    range's minimum, 3 of 255); 0 (the default) changes nothing
  - Z2M: `lamp_compensation`, `long_press_from_off`, `double_tap_full`, `fade_off_slow_zone` and
    `hold_slow_zone` per channel with any MCU version (the slow zones are Basic 0xff3c..0xff3f of
    the module itself, the others the dimmer endpoints' as on the ZDMS16-2); configure reads them
  - A momentary wall switch dims after 400 ms held (Open Dimmer's; was 600 ms), its way picked
    by where the light is, also during a fade (not by the fade's end). The hold's level is
    reported every 0.5 s (Home Assistant, Adaptive Lighting and bound lights follow it; a hold
    from dark from its hold time on); a hold from dark never goes to OnLevel and is shown at
    once (no 3 s holdoff); an off, the module button or a switch type change ends a hold (its
    release changes nothing), and so does a switch stuck closed, after 2 x hold_time + 2 s
  - The wall switch and the module button never take a transition time the network sent
  - Module button: a press switches both channels (any on: all off, else all on); held 3 s and
    let go: reset (was: L1 only, reset at 5 s while held); the LED lights up at 3 s
  - A restart of the module alone (update, config write, watchdog) leaves the light as it was
    instead of applying the power-on behaviour; the state is saved before a planned restart,
    a hold or a fade on its way stopping where it is (no jump back to the level from before
    the hold, nor on to the fade's end). After a watchdog reset the level is the one saved
    last (see NV), and with power-on `previous` the on/off kept through the reset is saved, so
    a later power cut brings that back
  - Every level is sent again to the dimming MCU after a minute without a frame (it forgets
    them at a power loss of its own); a frame the UART refuses is sent again, the UART set up
    again after 3 failures. These repeats and the second sending after a power-up wait for the
    end of the MCU's own ramp of the last frame (they go at ramp 0 and would cut it short)
  - NV: the on/off state is saved only with power-on `previous`; a level written while lit is
    saved at the off or a minute after the first such change, whichever comes first (at most
    one flash write a minute for an automation's steps; a watchdog reset can take a lit light
    back to a level of up to a minute before, at once). No save while a fade runs (a change
    from dark waiting for a UART that refuses frames does not count), a failed save is tried
    again
  - The dimmer endpoints take the virtual MCU as an Open Dimmer of build 500 (2.2.0) without it
    being one (no identity, still "1.0.0", no update, health or SWD logic):
    a Stop, Step or Move during a fade acts from where the light is (the module asks the virtual
    MCU: a transition, a scene's time, brightness_fade_time up to 600 s, the fade settings' own
    fades, a fade from dark, the same level sent again during one); a level set while off
    (ExecuteIfOff) is written to the virtual MCU, which keeps it in its NV (a power cut does not
    lose it)
  - Z2M: `execute_if_off`, `on_level_local` (the wall switch's and the module button's OnLevel),
    `brightness_fade_time` (unset: the module's own rate; no 2 s fold), `bind_mode`,
    `double_press_action` and `triple_press_action` per channel, with any MCU version; configure
    reads them; a min_level / max_level write reads both limits back
  - The wall switches' bindings: a momentary switch's gestures (single, double, triple, hold,
    release, as Open Dimmer's gesture_core) drive the bound lights in bind_mode remote (the
    channel acts as before); in mirror (auto) a hold's release ends their follow at once instead
    of 0.8 s later; a hold stops a network Move that runs
  - min / max as Open Dimmer takes them: one past the other pushes it along (equal: a fixed
    level), the answer has both limits and the level (the level shown no longer goes stale)
  - Open Dimmer 2.2.0's settings, with its DPs and meaning, per channel and with any version:
    - `countdown` (auto-off, ZCL OnWithTimedOff too: the longer time wins), RAM only; any off
      ends it; its end is an off of the channel's own (a follower goes along, no mirror)
    - `power_on_behavior_l1` / `_l2`: one per channel; the device's `power_on_behavior` stays
      (written to both channels, it shows L1's), so the converted units lose no entity. An
      older converter writes it to endpoint 1 only, which now sets L1 alone (see the update
      order below)
    - the wall switch gestures as Z2M `action` (single, double, triple, hold, release, press;
      genMultistateInput, configure binds it, maximum interval 0)
    - `switch_mode`: normal, detached (the light is the network's; a momentary switch reports
      its gestures, a toggle or state switch each change as a single; bind_mode auto acts as
      remote) or off (ignored). A hold under way ends where the light is, a momentary press
      begun in the other mode is not acted on at its release, back in normal a state switch
      says what the light is; the module button works in every mode
    - `fast_press`: detached momentary, the press at once as `press` (no multi-taps)
    - `current_level_startup`: the level after a power cut (steps of 4 of 1000; minimum,
      previous), not after a restart of the module alone
    - `identify` (and ZCL Identify): the channel blinks dark / max_level every 0.5 s, then goes
      to its state by its fade
    - `start_boost` (0..0.75 s, steps of 0.05): from dark at max_level first, then straight at
      the level, or where a transition is by then (its end stays); before a hold from dark too
      (let go in the boost: the minimum); an off in it goes out from the minimum; none after a
      restart of the module alone, nor for a hold from off while the light still fades out
      (Open Dimmer boosts from a dark output only)
  - Network reset by wall switch presses: `multi_press_reset_count` closures of one wall switch
    (any type, not one whose switch_mode is off; a toggle or state switch's closures, not its
    changes), each within 10 s of the first (0: never; 1..7 act as 8): its channel blinks 3 s,
    then the module leaves its network and pairs again. A behaviour change for the units that
    run an earlier build of this support: they held the setting (default 10) without counting,
    and count from the update on, with no action of the user
  - Updating a unit that runs an earlier build of this support: the regenerated converter into
    Zigbee2MQTT first (or together with the OTA) and Zigbee2MQTT restarted, then the OTA, then
    Reconfigure; do not change `power_on_behavior` while the old converter is in. Before the
    OTA set `multi_press_reset_count` to the count wanted (0 if the wall switches must never
    reset the network) and read it back
  - NV layout 5 (54 bytes in the stub, 56 on the TS110E's build: the per-channel power-on, the
    settings above, the slow zones, the lamp compensation, the switch options and the learnt
    hold ways); a layout 1..4 item is taken over (both channels get its power-on, the new ones
    Open Dimmer's defaults; the layout 4 item is 40 bytes on the TS110E, the padding after its
    curve bytes is not taken)
  - Z2M: `mcu_version` says it is the virtual MCU's (the HK32 tells no version); no text calls
    it Open Dimmer
  - A change made at once (a network time of 0) goes out whole: a Move to Level at once from
    off showed the channel's earlier level (full on a new unit) for one frame (20 ms) before the
    new one. No frame goes to the dimming MCU while the module's writes of a change (the time,
    the on, the level) still wait for the virtual MCU (40 ms at most), and the fader is at a
    level at once without waiting for its next tick
  - A change while the dimming MCU still runs its own ramp of a change made at once (the
    `mcu_ramp`, 1.5 s by default, from that change's frame; or before that frame has gone) goes
    at once too, in one frame with that ramp: only the MCU knows where the lamp is on it, and
    the module's fade frames (ramp 0) jumped the lamp from there (an off 40 ms after an on with
    `fade_on_time` 0 went near full first, then faded down; an on 300 ms after an off with
    `fade_off_time` 0 dropped to the minimum first). Its fade settings or network time do not
    apply then (a network time of 0 still goes at once, ramp 0); no minimum first then (the
    lamp is lit on its way); a momentary press from dark sends its minimum with that ramp, and
    a hold waits for that ramp's end before it moves (a hold from dark at least its 0.7 s).
    With no fade time of 0 (or `mcu_ramp` 0) nothing changes (no frame then has the MCU's ramp)
  - `factory_identity` (Basic 0xff59): the manufacturer name the stock firmware kept in its NV
    (its item id differs from the TS0601's: the first 33-byte item of module 10 that holds a
    Tuya name, `_T` + 4..5 of A-Z0-9 + `_` + 8 of a-z0-9), kept in this firmware's NV; never an
    MCU version (the virtual MCU's 1.0.0 is not kept as one). The ZDMS16-2 reads its item 3
    as before
  - Z2M: configure interviews the device first when Zigbee2MQTT does not know its endpoint 4
    (the stock firmware's endpoints were still in its database after the conversion: no wall
    switch endpoints, no new clusters); a `device_config` without the HK token is refused (the
    light would stop); the definition reads "Custom dimmer"; the `mcu_ramp` text says what is
    known of the value (0 none, 1500 about 1.5 s, 15000 the same by eye). User docs:
    [ts110e.md](ts110e.md); what is still to be checked on hardware:
    [ts110e_hk_protocol.md](ts110e_hk_protocol.md#to-check-on-hardware)
  - Releases (home-dimmer_build): `ota_release_assets.py --bin-dir` publishes one board's
    images (every board's files have the same names; the index entries are matched by name and
    sha512), and build.yml runs it for the ZDMS16-2 (`zdms16-2-<build>`) and the TS110E
    (`ts110e-3mpwqzuu-<build>`, no carried MCU image); a board the build left out publishes
    nothing
  - Tests: the HK tests (all of `test_hk_dimmer.py` but its two debug-counter tests) and the
    factory identity's TS110E cases also run on a stub built with the TS110E's flags
    (DIMMER_ONLY + HK_DIMMER, no SECONDARY_MCU_IMAGE); everything the TS110E's converter reads,
    writes, binds and reports is checked against that build's ZCL tables, and the reverse
- **Channel follow** (dimmer boards with two channels, DIMMER_BINDINGS: the ZDMS16-2 and the
  TS110E): a channel follows the other one's wall switch (`follow`, `follow_on_level`,
  `follow_off_level`, genOnOffSwitchCfg 0xff17..0xff19 of the follower, NV): at the leader's
  wall switch on the follower goes to its follow on level (default 254), at its off or its
  countdown's end to its follow off level (0, the default: off), inside the module. Not on the
  network's commands; the follower's change never goes on to its own bindings or followers
- **NovaDigital 6 gang** (`_TZE204_tdhnhhiy`) and **TPZ-2 / Moes SFL02-Z-2** (`_TZE200_hktk6hze`)
  touch switches with a secondary MCU
- Telink: an attribute write the stack refused (read-only, unknown attribute, wrong type) is no
  longer acted on as if it had been written
- Time checks no longer go wrong once per wrap of the system timer (every 268 s on Telink,
  36.4 h on Silabs): long and multi presses, cover travel times, the end of fast polling
- Telink: the build refuses an OTA file over the SDK's OTA size limit (0x34000 bytes), and
  release builds leave out printf (no console there), 3.7 KB less flash
- Telink: the module's own NWK commands (link status, route discovery, leave) are secured
  again when its saved security record had lost the stack's "secure all" flag. The stack sets
  the flag only at a factory-new start-up and otherwise restores the record as it is, so a
  record saved once without it (e.g. taken over from a factory NV without normalising it) kept
  those commands unsecured through every restart, leave and join, while data stayed secured;
  routers that drop unsecured NWK commands (this firmware's, Z-Stack) then lost their routes to
  the module. The flag is checked after every start-up and every join or rejoin, put back and
  saved, with what else of the same byte such a record left wrong: an active key slot past the
  two key slots (a factory byte taken raw: the stack read the key from past the record) becomes
  slot 1, the factory one, and a global link key flag of 0 becomes this firmware's default
  (without it the start-up never points the trust centre link key at this build's). Basic
  0xff5d (enum8, read only) tells what was found: 0 unsecured (a network without NWK security),
  1 secured, 2 repaired at this start-up, 3 repaired at a join (until the next restart); Z2M
  shows it as the diagnostic `nwk_command_security` on the ZDMS16-2 and the TS110E, read at
  configure (a unit paired before: no value until a Reconfigure). Debug builds (MCU_DUMP=1)
  have a test hook, Basic 0xff5e: 0xA1 clears the flag in RAM, 0xA2 in RAM and NV, 0xA3 in RAM
  and then resets the network
- Telink, ZDMS16-2 and TS110E builds: the module restarts 1 s after its own leave (a network
  reset, the coordinator's remove) and steers no network until then, as the SDK's own router
  app does, so the next join starts from a factory-new start-up. The stack's leave sets it up
  again in place, without the start-up's set-up of the security record, and a join straight
  after it saved whatever RAM held. A reset of a module on no network does not restart it.
  Other boards keep the leave in place (a relay would drop for the restart and take its
  power-on setting)
- Telink routers (not the ZDMS16-2: no room in its image): joins through a router that the trust
  centre no longer reads are told. With a unique trust centre link key (after a key exchange)
  the stack sends a joining child's Update Device only APS-encrypted, and a trust centre that
  holds another key for the router drops it: no device joins through the router, and nothing
  else shows it. Nothing is sent to find out: after such an Update Device the router waits 2 s
  for the trust centre's tunnel; 3 in a row without one make Basic 0xff5d say 4
  (`tc_link_key_stale` in Z2M), reported to the Basic cluster's bindings; a tunnel or a join of
  its own ends it (RAM only). Only while joining is open to the whole network (the last
  Mgmt_Permit_Joining was the coordinator's broadcast and the router's permit of it runs): a
  coordinator that does not allow joins itself drops the Update Devices whatever the key
  (joining allowed via one router only, a removed device's trust centre rejoins). On the
  TS110E the converter binds the Basic cluster at configure for the report: a unit paired
  before needs a Reconfigure. `UPDDEV_PLAIN_COPY=1` builds (bench only: make stops without
  `MCU_DUMP=1`; not tried on hardware) also send the Update Device with the network key alone,
  as the stack does with a global key
- ZDMS16-2 (Open Dimmer): the MCU's build is Basic 0xff5f (uint16, read only), what Open Dimmer
  said in its identity answer, reported to the Basic cluster's bindings after the version; Z2M
  shows it as the diagnostic `mcu_build` (a unit paired before: after a Reconfigure or the next
  identity answer). Two builds of one version show the same `mcu_version`, and the module
  updates the MCU by the build (a newer carried build also with the same version, never an
  older one)
- Telink, debug builds (MCU_DUMP=1) of a module converted with W4c: a leave of its own (a reset,
  the coordinator's remove) makes the factory copy stale, as a join of its own does. The
  restart after the leave took the copy again and came up on the network that had just removed
  the module, which then never steered
- Telink end devices build again: the W4c take (and its checks of the record sizes, which are
  the router library's) is in router builds only
- Builds of a tree that differs from its commit in what goes into the image (files under
  `src/`, the board database, `board.mk`, `VERSION`, `NVM_MIGRATIONS_VERSION`) get a `+` after
  the hash in the build id (`swBuildId`) and the file names

## 1.1.3

### Features

- **Cover cluster** (window covering) for controlling the motor of curtains, blinds, and shutters.
  Supports open, close, stop, and position control commands with configurable opening/closing
  travel times and deadzone configuration for precise calibration.
- **Cover switch cluster** for handling user input from window covering switches.
  Supports toggle/momentary switches, stop-on-repeat, stop button, local control, and remote device binding.
- Relays now respond to *MoveToLevelWithOnOff*
  - Level = 0 -> Turn off relay
  - Level > 0 -> Turn on  relay
- Configurable multi-press factory reset count (set 1-255 or 0 to disable)
- **Indicator LED press confirmation**:  LEDs briefly flash on button press/release as visual feedback.
- **Push-button without relay** support for battery-powered scene switches
  - Switches auto-detect absence of relays and default to detached mode
  - New device: Moes 4-gang scene switch (`REMOTE_MOES_SWITCH_TS0044`)
- **Battery measurement & reporting** (Zigbee `genPowerCfg` cluster) for Telink devices
- **Deep retention sleep** for Telink end devices
- **Configurable pulse duration** for latching relays

### Changes

- Add `D<N>` config option to customize button debounce delay in milliseconds
- **Bi-stable (latching) relays** have been reworked
  - They now use proper pulses instead of continuously driving the coil
  - Pressing multiple buttons will toggle the relays with small delays in-between (safe)
  - Add `SLP;` to the config string for simultaneous toggles (risky, might damage the device)
- **Power management for battery end devices**
  - Reduced TX power (~3 dBm instead of ~10 dBm) to save battery
  - Poll rate controller: fast polling + slow polling support
  - PowerCfg cluster for battery level monitoring
  - PollCtrl cluster to allow settings poll rate via ZCL
  - Add `BT<pin>` config option to enable battery mode

### Bugs

- **Fixed**
  - Latching relays not working with off_pin A0
  - Silabs version updates not working
  - Telink End_device unreachable from Z2M after a while ([#217](https://github.com/romasku/tuya-zigbee-switch/issues/217))
  - AC noise affecting Telink GPIO
  - Changing device type breaks Silabs NVM data
  - Reset needed 11 presses instead of 10
  - ZHA quirk silently failed to apply on relay-less devices with LED indicators (scene remotes), leaving firmware attributes unreachable from the UI
  - Silabs device dead after flashing when its SPI flash chip's JEDEC id was not in the bootloader's table (e.g. a ZBMINIL2 hardware variant with a different flash). The spiflash bootloader now falls back to a generic profile for any standard JEDEC SPI-NOR flash instead of failing storage init, so unknown-but-standard chips no longer need a per-chip bootloader build ([#422](https://github.com/romasku/tuya-zigbee-switch/issues/422))
  - Silabs: bindings to Zigbee groups ignored when emitting commands
- **New**
  - SONOFF ZBMINIL2 version updates broken?

## v1.1.2

_Bug-fix update_

### Bugs

- **Fixed**
  - Setting 'long press duration' to 0ms crashes device
  - Can't change device imageType in config string
  - Option 'Relay indicator - manual on' is not kept after reboot
  - Relay indicators sometimes go out-of-sync ([#38](https://github.com/romasku/tuya-zigbee-switch/issues/38))

## v1.1.1

_Critical bug-fix update for previous version_

### Changes

- Added a **hardware watchdog** on Telink devices that automatically reboots the device when it's stuck

### Bugs

- **Fixed:** Floating pin (or other device conditions) freezes the device

## v1.1.0

_Contains **substantial restructuring** of the firmware architecture, but doesn't bring new features_

### Changes

- Updated **Telink SDK** to v3.7.2.0 for **stability**
- Added support for **Silabs chips** by introducing **HAL middleware**
- Replaced GPIO polling with GPIO interrupts for **power efficiency**
- Improve **versioning** to include commit hash
- Many more technical improvements

### Bugs

- **Fixed**
  - Old SDK freezes device in some network conditions
- **New**
  - Floating pin (or other device conditions) **freezes device**
  - Setting 'long press duration' to 0ms **crashes device**
  - Can't change device imageType in config string
  - Option 'Relay indicator - manual on' is not kept after reboot
  - Relay indicators sometimes go out-of-sync ([#38](https://github.com/romasku/tuya-zigbee-switch/issues/38))
  - Telink End_device unreachable from Z2M after a while ([#217](https://github.com/romasku/tuya-zigbee-switch/issues/217))
  - Switch randomly toggles on TLSR8253 512KB devices ([#289](https://github.com/romasku/tuya-zigbee-switch/issues/289))
    (HOBEIAN and Zbeacon)
  - _Power-on behavior_ doesn't fully work on some devices
  - _momentary_nc_ not working after power loss.  
    (Apply the setting again)

## v1.0.21

### Changes

- Keep device configuration (user settings) when it is removed from the network

### New features

- Add support for Zigbee commands:
  - **off_with_effect** (0x40)
  - **on_with_recall_global_scene** (0x41)
- Add support for **normally-closed momentary buttons**
- Add **action states for toggle buttons**: position_on and position_off

## v1.0.20

### Changes

- (technical) Updated memory map: moved NV items from ZCL to APP.  
  **Due to this change, device configuration (user settings) may reset after OTA update.**

### Bugs

- **Fixed**
  - Canging config string crashed 3-4 gang devices
  - Detached mode didn't work for Toggle switches

## v1.0.19

### New features

- Add support for the **levelCtrl** cluster
  - This enables brightness control of compatible Zigbee bulbs via Zigbee binding.
  - The feature works only for momentary switches using long press: once a long press is detected, brightness will begin to slowly change. Each subsequent long press reverses the direction (increase/decrease).
  - Requires manual update of converters and reconfiguration.

### Changes

- Increase the number of **presses required to reset the device to 10.**
- Update manufacturer names to match the stock firmware.  
  (requires interview; but it's not mandatory, as backwards compatibility is kept)

### Bugs

- New bug: detached mode doesn't work for Toggle switches

## v1.0.18

- Partly fix an issue where setting the config string could brick the device.
- Technical: introduce a method to update data stored in NVRAM in new releases.

## v1.0.17

- Fix once again power on behavior = OFF not working if toggle in pressed state during boot.

## v1.0.16

- Add new toggle modes: TOGGLE_SMART_SYNC/TOGGLE_SMART_OPPOSITE (requires re-download of `switch_custom.js`).

## v1.0.15

- Add support for Zigbee groups. Read [doc](/docs/usage/endpoints.md) for details about endpoints.

## v1.0.14

- Improve code logic for Indicator LED on for switches.

## v1.0.13

- Fix power on behavior = OFF not working if toggle in pressed state during boot.
- Add way to control network state led state (requires re-download of `switch_custom.js`).

## v1.0.12

- Fix led indicator state in manual mode not preserved after reboot.
- Add forced device announcement after boot to make sure device is seen as "available" as soon as it boots.
- Restored device pictures in z2m (requires re-download of `switch_custom.js`).
- Cleaned-up z2m converter (fix typos, inconsistent names, etc.). **Warning!** This may break your automations as it changes .
  property names (requires re-download of `switch_custom.js`).

## v1.0.11

- Improve join behaviour by decreasing timeout between tries to join.
- Fix leave network: now device will send LeaveNetwork command properly.
- Display firmware version in a human-readable form.

## v1.0.10

- Add support for bi-stable relays controlled by 2 pins.
- Fix Led indicator mode not preserved after reboot.

## v1.0.9

- Fix reporting of indicator led status.

## v1.0.8

- Add support for indicator leds.
- Add way to force momentary mode as default via config.

## v1.0.7

- Add SUSPEND-based sleep to EndDevice firmware to decrease power usage ~10x.

## v1.0.6

- Add way to change device pinout on the fly, to allow easier porting of firmware .

## v1.0.5

- Keep status LED on when device is connected.
- Add separate firmwares for End Device/Router.
- Improve device boot time significantly by removing unnecessary logs .

## v1.0.4

- Fix bug that caused report to be sent every second.

## v1.0.3

- Add support of startup behaviour: ON, OFF, TOGGLE, PREVIOUS.
- Add support of button actions: 'released', 'press', 'long_press'. This is only useful for momentary (doorbell-like) switches.

## v1.0.2

- Add way to reset the device by pressing any switch button 5 times in a row .
- Fix support for ON_OFF, OFF_ON actions.
