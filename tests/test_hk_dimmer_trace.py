"""The debug builds' trace of the ZH-Z06 dimmers' output (hk_dimmer.c HK_TRACE; make MCU_DUMP=1, and
the stub): in the link's event log (Basic 0xff41..0xff45, helper_scripts/decode_mcu_diag.py) every
frame to the dimming MCU but a hold's own steps (TUYA_MCU_DIAG_HK_FRAME: the channel, | 0x80 when
the UART did not take it; level << 16 | ramp) and why it went (TUYA_MCU_DIAG_HK_EVENT: what << 4 |
channel). The first test is the reference a bench unit's log is held against when a lamp does what
its frames should not make it do: a wall switch hold down, its release, and 0.64 s later Adaptive
Lighting's fade up over 120 s. The log keeps its newest 30 entries: read it within about 10 s of
the command. A release build has none of it (tests/test_hk_dimmer_ts110e_build.py runs the HK
tests on a stub built without MCU_FLASHER_DUMP)."""
import sys
from pathlib import Path

from test_hk_dimmer import ATTR_FADE_ON, ATTR_FADE_OFF, MIN, run_t, set_setting, start
from test_hk_dimmer_long_transitions import TARGET, Lamp, move_z2m

from tests.zcl_consts import ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF, ZCL_CMD_ONOFF_ON

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "helper_scripts"))
from decode_mcu_diag import decode_log, format_log, to_bytes  # noqa: E402

BASIC = 0x0000
ATTR_LOG = [0xFF41 + i for i in range(5)]
ATTR_CONTROL = 0xFF4F
NONE = 0xFFFFFFFF
RAMP_LEFT, AT_ONCE_ON, AT_ONCE_OFF, FADER, FADER_AT, HOLD_BEGIN, HOLD_END = range(1, 8)


def clear(device) -> None:
    device.write_zigbee_attr(1, BASIC, ATTR_CONTROL, 1)


def log(device) -> list[dict]:
    return decode_log([to_bytes(device.read_zigbee_attr(1, BASIC, a)) for a in ATTR_LOG])


def events(entries, what: int, ch: int = 0) -> list[int]:
    return [e["value"] for e in entries if e["kind"] == "HK_EVENT" and e["id"] == what << 4 | ch]


def hk_frames(entries, ch: int = 1) -> list[tuple[int, int, int]]:
    """(when, level, ramp) of the frames logged for the channel (1, 2)."""
    return [(e["t_ms"], e["value"] >> 16, e["value"] & 0xFFFF) for e in entries
            if e["kind"] == "HK_FRAME" and e["id"] == ch]


def test_the_reference_a_hold_then_a_long_transition():
    lamp = Lamp()
    lamp.lit(TARGET)
    clear(lamp.device)
    lamp.hold(2400)
    lamp.run(540)                                               # 0.64 s after the release
    at = lamp.last
    t_cmd = lamp.t
    move_z2m(lamp.device, TARGET, 1200)
    lamp.run(5000)
    entries = log(lamp.device)
    print(format_log(entries))
    assert len(events(entries, HOLD_BEGIN)) == 1 and events(entries, HOLD_BEGIN)[0] >> 16 == 0xFF
    assert len(events(entries, HOLD_END)) == 1
    # the release: no time (the hold's level kept, the fader at rest there); the command: 120 s
    assert events(entries, FADER) == [NONE, 120000]
    rest, timed = events(entries, FADER_AT)
    assert rest >> 16 == rest & 0xFFFF == events(entries, HOLD_END)[0]
    assert timed >> 16 == events(entries, HOLD_END)[0] and timed & 0xFFFF > timed >> 16
    assert not any(events(entries, w) for w in (RAMP_LEFT, AT_ONCE_ON, AT_ONCE_OFF))
    # the frames: none of the hold's steps; after the command one level at a time, at ramp 0
    sent = hk_frames(entries)
    assert sent and all(ramp == 0 for _, _, ramp in sent)
    levels = [lv for _, lv, _ in sent]
    assert levels == list(range(at + 1, at + 1 + len(levels))), (at, levels)
    assert len(entries) < 30                                    # all of it still in the log
    assert lamp.t - t_cmd == 5000


def test_a_frame_the_uart_did_not_take_is_logged_and_the_one_that_went():
    proc, device, _ = start()
    set_setting(proc, device, ATTR_FADE_ON, 0)
    clear(device)
    assert proc.exec("uart_fail_writes 1").ok
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    run_t(proc, device, 800)
    entries = log(device)
    refused = [e["value"] for e in entries if e["kind"] == "HK_FRAME" and e["id"] == 0x81]
    assert refused == [MIN << 16]                               # the kick at the minimum
    assert [(lv, r) for _, lv, r in hk_frames(entries)] == [(MIN, 0), (255, 1500)]
    assert events(entries, AT_ONCE_ON) == [1000]


def test_the_at_once_paths_are_logged():
    """fade_off_time 0: an off right after an on at once goes while the MCU's own ramp runs."""
    proc, device, _ = start()
    set_setting(proc, device, ATTR_FADE_ON, 0)
    set_setting(proc, device, ATTR_FADE_OFF, 0)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
    run_t(proc, device, 300)
    clear(device)
    device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
    run_t(proc, device, 300)
    entries = log(device)
    assert len(events(entries, RAMP_LEFT)) == 1 and 0 < events(entries, RAMP_LEFT)[0] <= 1500
    assert hk_frames(entries) == [(hk_frames(entries)[0][0], 0, 1500)]


def test_the_decoder_spells_them_out():
    entries = [{"t_ms": 10, "kind": "HK_FRAME", "id": 0x81, "value": 3 << 16},
               {"t_ms": 20, "kind": "HK_FRAME", "id": 0x02, "value": 40 << 16 | 1500},
               {"t_ms": 30, "kind": "HK_EVENT", "id": FADER << 4, "value": NONE},
               {"t_ms": 40, "kind": "HK_EVENT", "id": FADER << 4 | 1, "value": 120000},
               {"t_ms": 50, "kind": "HK_EVENT", "id": FADER_AT << 4, "value": 173 << 16 | 443},
               {"t_ms": 60, "kind": "HK_EVENT", "id": HOLD_BEGIN << 4, "value": 0xFF << 16 | 443},
               {"t_ms": 70, "kind": "HK_EVENT", "id": HOLD_END << 4, "value": 173}]
    text = format_log(entries)
    for part in ("L1: level 3, ramp 0, the UART did not take it", "L2: level 40, ramp 1500",
                 "L1: to the fader, no time", "L2: to the fader, 120000 ms",
                 "L1: the fader at 173, to 443", "L1: hold begin down from 443",
                 "L1: hold end: 173"):
        assert part in text, (part, text)
