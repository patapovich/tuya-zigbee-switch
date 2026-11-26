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

- The NWK frame counter's floor (0x10000000, since 1.1.3-ab06b9a0) comes after the counter is
  loaded from flash (in `bdb_init()`). Before it ran on a counter not loaded yet and wrote
  0x10000000 at every start-up: two start-ups in a row off the network (a reset, then a power
  cut before the join) put the counter back to 0x10000000, below frames already sent, so the
  routers around dropped the module's frames as replays until it caught up; and a module still
  below the floor was not raised. Now a counter below it is raised once (written twice: the
  SDK's load takes a lone jump for a bad write and goes back), and above it nothing is written
  at a start-up, as in the SDK (no more sector change at each start-up).
- The module's own NWK commands (link status, route requests / replies / records, network
  status, leave) are secured again when its saved security record lost the stack's "secure
  all" flag: the stack sets it only at a factory-new start-up and otherwise restores the record
  as it is, so a record saved once without it (an early W4c test build took the factory record
  over unchanged; the release take sets the flag) kept those commands unsecured through every
  restart, leave and join while data stayed secured, and routers of this firmware drop them:
  the coordinator lost its routes to the module and its downlink became intermittent. The flag
  is checked after every start-up and every join or rejoin, put back and saved (once). Such a
  record also kept two more fields of the same byte wrong, and they are put right with the flag:
  an active key slot past the two key slots (a factory byte still as it was taken: the stack
  then read the network key from past the record) becomes slot 1, the factory one; and the
  global link key flag 0 becomes this firmware's default (without it the start-up never pointed
  the trust centre link key at this build's). So the record found on a unit (0x15) and a factory
  byte still in NV (0x35) both become 0x5D, as a join of this firmware leaves it.
  `nwk_command_security` (Basic 0xff5d, diagnostic, read at configure): unsecured, secured,
  repaired_at_start, repaired_at_join (the last two until the next restart). A unit paired
  before needs a Reconfigure once: it shows no value until then (the module reports the
  attribute only for `tc_link_key_stale`, which this build does not have). Debug builds
  (MCU_DUMP=1): Basic 0xff5e makes the fault on purpose for the bench (0xA1 RAM, 0xA2 RAM and
  NV, 0xA3 RAM then a network reset).
- A network reset or the coordinator's remove restarts the module 1 s after its leave (the
  lamp stays as it is: the MCU drives it), and it steers no network until then, so the next
  join starts from a factory-new start-up, as the SDK's own router app does after a leave.
  Before, the stack's leave emptied the NV and set the stack up again in place, but the
  security record is set up only at a start-up, so a join straight after the leave saved
  whatever RAM held (how the record without the "secure all" flag above stayed after resets).
  Every reset source ends there: the module button and the wall switch presses (the MCU's reset
  request), the coordinator's remove, a failed trust centre link key exchange after a join. A
  reset of a module that is on no network does not restart it; a restart comes up factory new,
  so repeated resets restart once. Debug builds: the 0xA3 test reset stays a leave in place
  (its join shows the repair). Only the ZDMS16-2 and TS110E builds (their lamps stay through a
  restart of the module); other boards keep the leave in place.
- W4c: a module converted before the marker came (an intact factory NV, no marker) and on a
  network of its own since gets the marker at a start-up. Without it a reset, then any start-up
  before the join (now the restart after the leave), took the old factory network back instead
  of coming up factory new.
- W4c: the take checks the factory security record before it writes anything, and maps it.
  - The factory firmware keeps the security level in bits 0-3 of byte 0x3E, "secure all" in
    bit 4 and the active key slot in bits 5-6; this SDK in bits 0-2, 3 and 4-5. The take read
    the slot from this SDK's bits and so always took slot 1 (the factory "secure all" bit): a
    module whose network key sat in slot 0 came up with no usable key. Now the slot is the
    factory one, "secure all" is set, and the record is refused (the module joins again) when
    that slot does not hold the active network key, the level is not 5 or the record names no
    trust centre (zero or all FF).
  - The trust centre link key type is checked against the key pair. A unique key (after the
    factory firmware's key exchange) needs this trust centre's pair, unverified or verified;
    without one the stack sent a joining child's Update Device only encrypted with a key the
    trust centre does not have, and joins through the module failed without a word. Then the
    global type is taken (Update Device also with the network key alone) and the pair is not;
    an unknown type, which sent none, too. Basic 0xff55's `found` bit 4 says so.
  - The factory NV's key pairs are read in the SDK's 1 MB layout: two 16 KB sectors (0xE6000,
    0xEA000) and a 3 KB index. The parser read 4 KB sectors and a 1 KB index there, so it saw
    only the first copies of the pair (up to 0xE6FFF) and never the second sector: it could
    take an older pair or none.
  - Debug builds (MCU_DUMP=1), which take the factory copy again after a wipe, do not take it
    once the module has been on a network of its own since (a join by steering, or a start-up
    on another address or PAN than the copy, also after a release build's join): the join
    exchanged a new link key with the trust centre, which may no longer accept the copy's (a
    module so re-taken relayed no joins; whether that key is why is not settled). Nor after a
    leave of its own (a reset, the coordinator's remove, which may drop the module and its key):
    the restart after the leave took the network that had just removed it, came up joined on it
    and never steered, so only a programmer got the module out. Result 8 (stale) in 0xff55. The
    bench's wipe 0xA7 (0xA8 with the factory-reset flag) forces the next take.
  - Debug builds of the ZDMS16-2 do not fit the OTA limit (MCU_DUMP=1: 215874 bytes, 2882
    over): the bench checks of 0xff5e, the wipes 0xA7 / 0xA8 and the stale result on a ZDMS16-2
    need a flash over SWD or a programmer, or are done on the TS110E, whose debug build fits
    (13374 bytes left).
- TS110E builds from this branch (not the ZDMS16-2: its image has no room left for it): joins
  through a module that the trust centre no longer reads are told. After its own key exchange
  the module's trust centre link key is unique, and the stack sends a joining child's Update
  Device only encrypted with it; a trust centre that holds another key for the module (it gave
  it a new one at a later join, a debug W4c take brought an older one back) drops it, never
  sends the network key back, and no device joins through the module, without a word. Nothing
  is sent to find out: after such an Update Device the module waits 2 s for the trust centre's
  tunnel, and 3 in a row without one make `nwk_command_security` say `tc_link_key_stale`
  (Basic 0xff5d value 4), reported to the Basic cluster's bindings. Only while joining is open
  to the whole network (the coordinator's broadcast, Zigbee2MQTT's "permit join" for all, and
  the module's permit of it still runs): a coordinator that does not allow joins itself drops
  the Update Devices whatever the key, as with "permit join" via one device, and a removed
  device's trust centre rejoins go on whether anything permits or not. A tunnel or a join of
  its own ends it, a restart starts over (RAM only). If devices still cannot join through the
  module, re-pair it. A TS110E paired before needs a Reconfigure once: the converter binds its
  Basic cluster at configure, and without that bind the report never reaches Zigbee2MQTT.
  Bench builds (`UPDDEV_PLAIN_COPY=1`, only with `MCU_DUMP=1`: make stops otherwise; off by
  default, not tried on hardware) also send the Update Device with the network key alone, as
  with a global key.
- W4c (a conversion keeps the network): the take clears the SDK's factory-reset flag
  (`CFG_FACTORY_RST_CNT`, 0x79000 in the 512 KB map) when it is not blank. A factory firmware's
  OTA client can download the conversion image over it (the TS110E's stores the image at
  0x70000..0x9C000), and `zb_init()` takes any byte there for a factory reset cut short and
  finishes it, emptying the network just taken: the module came up factory new and needed
  Permit join (seen on a TS110E; a ZDMS16-2 only if its factory firmware stages the image there).
- The same level again during a transition (a scene or an automation that fires twice) leaves
  the module's idea of the transition as it runs, as Open Dimmer keeps the move: a Stop, Step
  or Move after it acts from where the light is, no longer from a transition started again.
- Channel follow: L2 can follow L1's wall switch, or L1 L2's (`follow`, `follow_on_level`,
  `follow_off_level`; genOnOffSwitchCfg 0xff17..0xff19 of the follower, kept in NV). At the
  leader's wall switch on the follower goes to its follow on level (default 254), at its off or
  its countdown's end to its follow off level (0, the default: off). Not on the network's
  commands. With Open Dimmer 2.2 configure reads the settings: a unit on an earlier build
  needs a Reconfigure, else they show as unknown until read.
- Release tooling only (no change in the image): the release step can publish one board's
  images (`--bin-dir`), so a TS110E build next to the ZDMS16-2 one gets its own release instead
  of stopping the step.
- Build tooling: a build of a tree that differs from its commit in what goes into the image
  (changed or new files under `src/`, the board database, `board.mk`, `VERSION`,
  `NVM_MIGRATIONS_VERSION`) gets a `+` after the hash in its build id (`swBuildId`, e.g.
  `1.1.3-8b465267+`) and its file names, so a unit running it does not show the commit's id.
  CI builds a clean checkout: no `+`.
- `mcu_build` (Basic 0xff5f, uint16, diagnostic): the build Open Dimmer said in its identity
  answer, reported to the Basic cluster's bindings right after the version at every such answer
  and read at configure; shown for any Open Dimmer, no value for a 2.1.x app (it says none) or
  a stock MCU. A unit paired before shows it after a Reconfigure, or at the next identity
  answer (a restart of the module or the MCU) through the Basic bindings it has. Two builds of one version (a test build of 2.2.8 and the 2.2.8 release both show
  `mcu_version` 2.2.8) differ only there. `mcu_version` stays as it was. The update decision
  already went by the build and is now pinned by tests with the carried image: a newer carried
  build is offered with the same version, never the same or an older one, whatever the version
  bytes.
- Fixes of the Telink Zigbee SDK 4.0.x taken into this firmware, which stays on SDK 3.7.2.0
  (4.0.x does not support the 512 KB flash map):
  - the ACK of a received frame no earlier than 160 us after the frame's end (SDK 4.0.2.0,
    "optimize ACK sending timing to improve compatibility"); the old wait after the switch to
    TX stays too, so an ACK never goes earlier than before;
  - the ACK to a sleepy child's Data Request has the pending bit while the child's frame is
    already being sent (SDK 4.0.x's library): before, the child could go back to sleep with a
    frame still queued for it;
  - 5 s instead of 2 s for the network key after an association join or a secure rejoin
    (SDK 4.0.1.0);
  - NWK status indications handled as the SDK's sample apps do (3.7.2.0 and later; the module
    left them unhandled): a neighbour whose frame counter started over (a factory reset, a
    reflash) is heard again after 10 dropped frames instead of never until the network key
    changes, and a frame under a network key the module lacks starts a trust-centre rejoin (at
    most every 15 minutes).
  - NV: a failed write of the outgoing frame counter is retried in the other sector; the retry
    took "sector 0xff" before, so it erased sector 0 whether it was the live one or not and
    marked an address in another NV module's sector header invalid. A setting whose last write
    step failed is reported as failed instead of its previous copy being dropped (it was lost);
  - a network join the stack cannot start (NWK busy, no buffer) ends the pairing attempt
    instead of leaving it in progress, which refused every later pairing until a restart.
  - the receiver settles 85 us before a clear channel assessment (SDK 4.0.2.0): the channel's
    level was sampled at once, a stale reading right after a send;
  - a Confirm Key from the trust centre counts only while pairing (SDK 4.0.x): one that came late
    or twice cleared the joined module's "authenticated" and "on a network" flags;
  - Mgmt_Rtg_req is answered from the routing table (SDK 4.0.x), so Zigbee2MQTT's network map
    shows these routers' routes (it was "not supported"). Not in ZDMS16-2 builds: no room left
    in its image (below the OTA limit), so the ZDMS16-2 still answers "not supported" and the
    network map shows no routes from it; the TS110E and every other router answer it;
  - stack exceptions: the SDK's sample apps restart on every one, the module went on after it.
    It now restarts on those that leave the stack broken (a task the full task queue dropped
    with its buffer freed, a buffer freed twice or outside its pool, a timer that could not be
    added, a stack overflow, the MAC's own state checks). On the ZDMS16-2 the lights stay as they
    are (Open Dimmer keeps the level; the module asks for it after the restart). The last such
    exception since the power-up is Basic 0xff5c (its event code, 0: none), on the ZDMS16-2 and
    the TS110E. The TS110E's virtual MCU keeps the lights' on/off state for a warm start in
    another retained register, so that state survives an exception restart; a level changed in
    the last minute, and a fade or hold that was running, come back as last saved (as after a
    watchdog reset: the level is saved 60 s after a change while lit).
  The SDK files are taken with small patches (src/telink/patch_sdk/<path>.patch, sdk.mk
  SDK_PATCHED); everything else in them is compiled as before. SDK 4.0.2.0's key exchange
  timeout fix mends a 4.0.1.0 regression that 3.7.2.0 does not have. The NV driver is built for
  size (about 730 bytes less on the ZDMS16-2, with the NV fixes) to make room.
- The dimmer chip's health report also goes out at the second round after a restart (~2 min
  later), then every 10 min as before: the first one comes seconds after a restart, and after an
  OTA update Zigbee2MQTT was still configuring the module then and dropped it (the module sent it
  and the coordinator acknowledged it, seen with a sniffer), so the hub waited 10 min.
- Attribute reports ask for an APS acknowledgement (every Telink build). The SDK sent them
  without one, so a report lost on a hop past the first (seen: an on/off report after a wall
  switch press, lost on the last of three hops) left the hub with the old state until the next
  change. The coordinator acknowledges each one; when no acknowledgement comes, the module sends
  the report again 1-2 s later, up to 3 times (a send the next hop did not take goes again at
  once; a route the module could not find gets no second try). Only reports to the coordinator
  or another single device ask; reports bound to a group do not (the stack clears it for
  groups). A report asks only while fewer than 4 of the module's frames wait for an
  acknowledgement, so a dimming move to a hub that does not answer cannot fill the stack's
  16-frame send queue (when it is full, every new frame of the module is dropped); the reports
  past them go out as before. "About 4": counted per report pass and per firmware report, and a
  report takes one entry per device it is bound to (the converter binds the coordinator alone;
  a device that answers frees its entry at once).
  A resend is the report as it was, so it could reach the hub after a newer one and bring back
  the older value (a level from the middle of a hold after the final one, "on" after a quick
  "off"): a newer report of a channel's on/off or level stops the resends of the older ones of
  that cluster. A newer report that lacks an attribute of the older one (a report carries up to
  two) stops that one's resend too: it waits for its next change, as before this change. The
  wall switch gestures keep their resends (each one is an event); the converter takes a copy or
  a late one (within 10 s of the last gesture: the same value, or a sequence 1..7 behind) as no
  new gesture. The firmware's own reports (MCU version and build, health record, flasher status,
  nwk_command_security) are not stopped: they are of different attributes of one cluster (a late
  flasher status during a job can still come after a newer one). On end-device builds each
  acknowledgement waits at the parent until the module polls for it. The SDK's zcl_reporting.c
  is taken with a patch for it (src/telink/patch_sdk/zigbee/zcl/zcl_reporting.c.patch).

## 1.1.3-d903f5ea (2026-11-24), carries Open Dimmer 2.2.8

- Carries Open Dimmer 2.2.8.
- The `_TZE204_o9gyszw2` variant of the AVATTO ZDMS16-2 is a board of its own
  (`MODULE_AVATTO_ZDMS16_2_O9GYSZW2`, the same firmware as the other two): the converter's stock
  definition gets OTA for it and the OTA index offers it the conversion, so the fork's converter
  alone converts it (tested on one unit, which needed a temporary converter before). Its factory
  MCU is not read-protected; the SWD install of this build takes it (below).
- OTA index: an image is also offered to the stock names of the boards with the same
  `config_str` (the same image). CI builds the ZDMS16-2 board only, so its conversion entry
  listed `_TZE284_jtbgusdc` alone and Zigbee2MQTT never offered the conversion to a stock
  `_TZE204_jtbgusdc` or `_TZE204_o9gyszw2` module, though the converter gave them OTA.
- A wall switch on within 5 s of the dimmer's own off (tapped off and on again) brings the light
  back at the level it had: no OnLevel then, so an automation's night level kept in OnLevel no
  longer replaces a level set by hand a moment before. After an off from the network the OnLevel
  as before.
- Open Dimmer 2.2.8: `fade_off_slow_zone` and `hold_slow_zone` are a share of the channel's
  min..max (before: of the whole scale); the labels say so. Defaults 15 / 25 %.
- `multi_press_reset_count` works on the ZDMS16-2 with Open Dimmer 2.2.8: the dimmer chip counts
  the closures of one wall switch (each within 10 s of the first, as the factory MCU does with its
  fixed 8) and asks for the leave and pairing at that count; the module writes the count to it
  (DP 181) whenever the dimmer chip's dump says another (it is not saved there: 8 at its every
  start) and at every write of the setting. 0 turns it off; 1..7 act as 8. A factory MCU keeps
  its own 8.
- A module converted from the factory Zigbee firmware keeps its network: the conversion moves
  only the image, and the factory NV (Tuya's 1 MB layout, at 0xD8000) is still there while this
  firmware's NV is at the 512 KB layout, so the module came up factory new and joined again only
  while joining was allowed (a Permit join after the conversion's power cycle). Now, when this
  firmware's NV has no network information and the factory NV has an intact one (byte sum or CRC
  checked, the structure sizes this SDK has), its network information, security material and
  trust centre link key are taken before the stack starts; the factory network information is
  then erased, so a factory reset later never brings it back.

- A factory dimmer chip that answers the module's "not connected" with a leave and pairing
  request no longer resets the module: after a restart the module tells the chip "not
  connected" until its network is back, and a request that comes within 3 s of that, once the
  network is back, is refused (the chip is told the module is connected). The module button
  resets as before.
- Release builds carry no debug parts: the MCU link diagnostics (the event log and the Basic
  attributes 0xff40..0xff45 and 0xff4f, read by helper_scripts/decode_mcu_diag.py) are built
  only with `make MCU_DUMP=1`; the counters the MCU update and the SWD master use stay.
- The module keeps its factory identity over the conversion: the manufacturer name of its
  factory Zigbee firmware (from the factory NV, Tuya's module 10, e.g. `_TZE284_jtbgusdc`; also
  on modules converted before this build, while their factory NV is there) and the version of
  the factory dimmer chip as the module first heard it (before Open Dimmer replaced it). Kept
  in this firmware's NV, shown as Basic 0xff59 (character string, "_TZE284_jtbgusdc, MCU
  1.0.7") and in Zigbee2MQTT as the diagnostic `factory_identity`, read at configure.

- The SWD job `install` (and the automatic install of the first start-up with the wires) takes
  any factory dimmer chip, whatever its firmware: also one that left its flash open (read
  protection level 0, seen on a `_TZE204_o9gyszw2`). There no protection change erases the
  flash, so the install erases its pages itself (page by page, the hidden 12 KB too; kept over a
  power cut like the steps past the option bytes), then writes the option bytes and the
  bootloader as for a read-protected chip. A chip with Open Dimmer's bootloader or app is never
  taken for a factory one; a write-protected sector refuses it before anything is erased. The
  install no longer needs a factory version on the UART (it refuses only a chip that told an
  Open Dimmer version), and the chip is identified by its debug port, AP, CPUID and IDCODE: the
  ROM's CRC is told, not checked (ROM revisions differ: 0xA52232EA, 0x62889B8E seen).
- SWD job `dump` (control 6, debug builds only: `make MCU_DUMP=1`): reads a dimmer chip at read protection level 0 over Zigbee, without
  writing it: a factory chip that left its flash open (seen on a `_TZE204_o9gyszw2`) or one
  running Open Dimmer. It attaches at once or, when the factory firmware holds the SWD pins, at
  the next power-up (armed like `install`); the core is halted, the CRC-32 of the 32 KB flash
  (the hidden 12 KB included) goes to `sector0_crc`, and then each write of the dump address
  (Basic 0xff53, uint32) fills the dump data (0xff54: the address and the 44 bytes there; flash
  0x08000000..0x08007FFF, system memory 0x1FFF0000..0x1FFF0FFF). The abort job, or 10 minutes
  without a read, ends it with a reset of the chip, so its own firmware starts again. A
  read-protected chip is refused; reading its protection has already closed its flash to the
  running core, so it is held halted with the gate outputs low until the next power cycle.

- Carries Open Dimmer's bootloader v3 for the SWD job `update_bootloader`: images up to 26 KB,
  their part past 14 KB in the dimmer chip's hidden flash (open-dimmer docs/BOOTLOADER.md
  section 18). The carried image may now be up to 26 KB (`IMAGE_MAX`); a chip under bootloader
  v2 declines an image over 14 KB and keeps its app.
- Open Dimmer 2.2.8's wall switch options per channel, from its DP 180 (both channels' bits):
  `long_press_from_off` (genOnOffSwitchCfg 0xff15, on by default; off, a long press from off
  brings the light on at its level and the hold goes on from there instead of rising from the
  minimum) and `double_tap_full` (0xff16, on by default: two short taps take the light to its
  maximum). Shown and written only for an MCU that has them (its dump tells); a write changes
  its own bit only.
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
