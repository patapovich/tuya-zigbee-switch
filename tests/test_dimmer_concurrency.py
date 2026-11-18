"""Wall switch used while Zigbee commands are in progress (ZDMS16-2).

The MCU handles the wall switch itself and only reports the result (DP
reports, 0x06). These tests inject such reports while a Zigbee write waits
for its ack, while a ramp runs and during the fade hold-off, and check that
the firmware ends up showing what the MCU reported last, without fighting it.
"""
import pytest
from conftest import Device
from zcl_consts import ZCL_CMD_ONOFF_OFF, ZCL_CMD_ONOFF_ON
from test_dimmer_mcu import (
    DP_LEVEL_L1,
    DP_ON_L1,
    DP_ON_L2,
    HOLDOFF_MS,
    TUYA_CMD_REPORT,
    TUYA_CMD_REPORT_PASSIVE,
    dp_units,
    dump_range,
    known_off,
    scaled_to_tuya,
    set_on_level,
    RAMP_PERIOD_MS,
    ZCL_ATTR_ONOFF,
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF,
    ZCL_LEVEL_MOVE_UP,
    bool_dp,
    dp_writes,
    level,
    mcu_report,
    onoff,
    pump,
    set_level,
    start,
    take_tx,
    tuya_to_zcl,
    value_dp,
    zcl_to_tuya,
)


def _writes_after(proc, device, ms: int, steps: int = 4) -> list[tuple[int, int]]:
    out = []
    for _ in range(steps):
        device.step_time(ms)
        pump(proc)
        out += dp_writes(take_tx(proc))
    return out


def test_wall_off_while_zigbee_level_write_waits_for_ack():
    proc, device = start()
    try:
        set_level(proc, device, 50)
        # Zigbee: brightness 150 while on. "on" is acked at once, the level
        # write waits (the MCU answers it only after the fade).
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL,
                               ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([150, 0, 0]))
        pump(proc)
        first = dp_writes(take_tx(proc))
        assert first == [(DP_ON_L1, 1)]
        mcu_report(proc, bool_dp(DP_ON_L1, 1))           # ack of on -> level goes out
        assert dp_writes(take_tx(proc)) == [(DP_LEVEL_L1, zcl_to_tuya(150))]
        mcu_report(proc, bool_dp(DP_ON_L1, 0))           # wall switch: off
        assert onoff(device) == 0
        later = _writes_after(proc, device, 300)
        assert (DP_ON_L1, 1) not in later, f"firmware switched the light back on: {later}"
    finally:
        proc.stop()


def test_wall_off_stops_zigbee_ramp():
    proc, device = start()
    try:
        set_level(proc, device, 60)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF,
                               bytes([ZCL_LEVEL_MOVE_UP, 0xFF]))
        _writes_after(proc, device, RAMP_PERIOD_MS + 10, steps=2)
        mcu_report(proc, bool_dp(DP_ON_L1, 0))           # wall switch: off mid-ramp
        assert onoff(device) == 0
        later = _writes_after(proc, device, RAMP_PERIOD_MS + 10, steps=5)
        assert not [w for w in later if w[0] == DP_LEVEL_L1], f"ramp kept writing: {later}"
    finally:
        proc.stop()


def test_wall_dimming_during_hold_off_wins_after_it():
    proc, device = start()
    try:
        set_level(proc, device, 200)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL,
                               ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([180, 0, 0]))
        _writes_after(proc, device, 50, steps=2)
        # wall switch held: the MCU dims to 300 and reports while our fade
        # hold-off still runs
        mcu_report(proc, value_dp(DP_LEVEL_L1, 300))
        assert level(device) == 180                       # held back for now
        device.step_time(HOLDOFF_MS + 100)
        pump(proc)
        assert level(device) == tuya_to_zcl(300), "MCU's level must win after the hold-off"
    finally:
        proc.stop()


def test_zigbee_on_racing_wall_off_ends_off():
    proc, device = start()
    try:
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, 0x01)   # Zigbee: on
        pump(proc)
        assert dp_writes(take_tx(proc)) == [(DP_ON_L1, 1)]
        mcu_report(proc, bool_dp(DP_ON_L1, 1))                # MCU did it
        mcu_report(proc, bool_dp(DP_ON_L1, 0))                # wall switch right after
        later = _writes_after(proc, device, 200)
        assert onoff(device) == 0
        assert not later, f"unexpected writes after the wall switch: {later}"
    finally:
        proc.stop()


def test_state_converges_to_last_mcu_report():
    """Interleave Zigbee commands and wall switch reports; when things settle
    the attributes equal what the MCU reported last."""
    proc, device = start()
    try:
        set_level(proc, device, 100)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL,
                               ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([220, 0, 0]))
        mcu_report(proc, value_dp(DP_LEVEL_L1, 450))          # wall dims meanwhile
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, 0x00)   # Zigbee: off
        mcu_report(proc, bool_dp(DP_ON_L1, 1))                # wall: on again
        mcu_report(proc, value_dp(DP_LEVEL_L1, 520))          # wall: dims to 520
        _writes_after(proc, device, 200, steps=3)
        device.step_time(HOLDOFF_MS + 200)
        pump(proc)
        take_tx(proc)
        mcu_report(proc, bool_dp(DP_ON_L1, 1))                # MCU's final word
        mcu_report(proc, value_dp(DP_LEVEL_L1, 520))
        device.step_time(HOLDOFF_MS + 200)
        pump(proc)
        assert onoff(device) == 1
        assert level(device) == tuya_to_zcl(520)
    finally:
        proc.stop()


def test_wall_switch_change_is_reported_to_coordinator():
    proc, device = start()
    try:
        device.clear_events()
        mcu_report(proc, bool_dp(DP_ON_L1, 1))                # wall: on
        device.wait_for_attr_change(1, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF)
    finally:
        proc.stop()


# ---------------------------------------------------------------- late answers
#
# The MCU answers every DP write with a report of that DP: the stock ZDMS16-2
# with 0x06 (the same command as a wall switch report), our own firmware with
# 0x05. An answer can come after the 150 ms ack timeout, or while a newer
# write of the same DP waits in the queue. Such an answer is outdated: it must
# neither be applied as the current state nor count as a wall switch press
# (which would replace the requested level with OnLevel). A report with the
# other value that answers no write of ours is the MCU's last word: shown
# when our write times out, as no wall switch press either.

ACK_TIMEOUT_MS = 150  # firmware TX_ACK_TIMEOUT_MS
ANSWER_CMDS = [pytest.param(TUYA_CMD_REPORT, id="0x06"),
               pytest.param(TUYA_CMD_REPORT_PASSIVE, id="0x05")]
TIMING = [pytest.param(False, id="before-timeout"), pytest.param(True, id="after-timeout")]


def _answer_writes(proc, device, cmd: int, unanswered=(), rounds: int = 8):
    """Like ack_writes, answering with `cmd`. `unanswered`: DP units already
    taken off the line that still need their answer."""
    seen = []
    pending = list(unanswered)
    for _ in range(rounds):
        pump(proc)
        tx = take_tx(proc)
        if not dp_units(tx) and not pending:
            device.step_time(50)
            pump(proc)
            tx = take_tx(proc)
            if not dp_units(tx):
                break
        seen += dp_writes(tx)
        for unit in pending + dp_units(tx):
            mcu_report(proc, unit, cmd=cmd)
        pending = []
    return seen


def _onoff_changes(device) -> int:
    return len([e for e in device._events if e.kind == "zcl_attr_change"
                and int(e.payload["ep"]) == 1
                and int(e.payload["cluster"], 16) == ZCL_CLUSTER_ON_OFF
                and int(e.payload["attr"], 16) == ZCL_ATTR_ONOFF])


def _until_sent(proc, device, write, max_ms: int = 1000):
    """Step time until `write` (dpid, value) has gone out on the line: a frame
    of the link to the MCU (e.g. a version question) may hold it back for an
    ack timeout. Returns what went out with it."""
    for _ in range(max_ms // 50):
        pump(proc)
        tx = take_tx(proc)
        if write in dp_writes(tx):
            return tx
        device.step_time(50)
    raise AssertionError(f"{write} was not sent")


def _lit_with_on_level(proc, device):
    dump_range(proc, 276, 591)
    set_on_level(device, 60)
    known_off(proc)
    set_level(proc, device, 150)
    assert (onoff(device), level(device)) == (1, 150)


@pytest.mark.parametrize("late", TIMING)
@pytest.mark.parametrize("answer", ANSWER_CMDS)
def test_late_answer_to_off_does_not_undo_a_newer_on(answer, late):
    """Off, then at once on at 200. The answer to the off comes while the on
    waits in the queue, or (after the ack timeout) while the on is on the
    line. It must not switch the light off in Zigbee, and the answer to the
    on must not be taken for the wall switch (-> OnLevel 60 instead of 200)."""
    proc, device = start()
    try:
        _lit_with_on_level(proc, device)
        device.clear_events()
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        pump(proc)
        tx_off = take_tx(proc)
        assert dp_writes(tx_off) == [(DP_ON_L1, 0)]
        if late:
            device.step_time(ACK_TIMEOUT_MS + 50)
            pump(proc)
            assert dp_writes(take_tx(proc)) == []
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL,
                               ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([200, 0, 0]))
        pump(proc)
        tx_on = take_tx(proc)
        assert dp_writes(tx_on) == ([(DP_ON_L1, 1)] if late else [])
        changes = _onoff_changes(device)                         # off, on

        for unit in dp_units(tx_off):                            # the late answer
            mcu_report(proc, unit, cmd=answer)
        assert onoff(device) == 1, "the answer to the off undid the newer on"

        writes = dp_writes(tx_on) + _answer_writes(proc, device, answer, dp_units(tx_on))
        device.step_time(HOLDOFF_MS + 200)
        pump(proc)
        assert writes == [(DP_ON_L1, 1), (DP_LEVEL_L1, scaled_to_tuya(200, 276, 591))]
        assert (onoff(device), level(device)) == (1, 200)
        assert _onoff_changes(device) == changes == 2, "extra off/on pair in Zigbee"
    finally:
        proc.stop()


@pytest.mark.parametrize("answer", ANSWER_CMDS)
def test_late_answer_to_on_after_off_is_not_a_wall_press(answer):
    """On at 200, its answer comes only after the ack timeout, when the off
    that followed is on the line already. The late "on" is not the wall
    switch: no OnLevel write, the light stays off in Zigbee."""
    proc, device = start()
    try:
        _lit_with_on_level(proc, device)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        _answer_writes(proc, device, answer)
        assert onoff(device) == 0
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL,
                               ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([200, 0, 0]))
        pump(proc)
        tx_on = take_tx(proc)
        assert dp_writes(tx_on) == [(DP_ON_L1, 1)]
        device.step_time(ACK_TIMEOUT_MS + 50)                    # no answer in time
        pump(proc)
        tx_level = take_tx(proc)
        assert dp_writes(tx_level) == [(DP_LEVEL_L1, scaled_to_tuya(200, 276, 591))]
        for unit in dp_units(tx_level):
            mcu_report(proc, unit, cmd=answer)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        pump(proc)
        tx_off = take_tx(proc)
        assert dp_writes(tx_off) == [(DP_ON_L1, 0)]
        device.clear_events()

        for unit in dp_units(tx_on):                             # the late answer
            mcu_report(proc, unit, cmd=answer)
        assert onoff(device) == 0, "the late answer to the on was applied"

        writes = _answer_writes(proc, device, answer, dp_units(tx_off))
        assert writes == [], f"late answer taken for the wall switch: {writes}"
        assert onoff(device) == 0
        assert _onoff_changes(device) == 0
    finally:
        proc.stop()


def test_on_off_answer_must_carry_the_value_written():
    """A report of the DP in flight with another value is not its answer: the
    next frame waits for the real one. A level write is answered with
    wherever the MCU's fade ended, so the DP alone matches it."""
    proc, device = start()
    try:
        _lit_with_on_level(proc, device)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        _answer_writes(proc, device, TUYA_CMD_REPORT)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL,
                               ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([200, 0, 0]))
        pump(proc)
        assert dp_writes(take_tx(proc)) == [(DP_ON_L1, 1)]
        mcu_report(proc, bool_dp(DP_ON_L1, 0))                   # not the answer
        assert dp_writes(take_tx(proc)) == []
        assert onoff(device) == 1
        mcu_report(proc, bool_dp(DP_ON_L1, 1))                   # the answer
        level_write = dp_writes(take_tx(proc))
        assert level_write == [(DP_LEVEL_L1, scaled_to_tuya(200, 276, 591))]
        # Brightness: the MCU's own clamp/fade end answers the write too.
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL,
                               ZCL_CMD_LEVEL_MOVE_TO_LEVEL, bytes([100, 0, 0, 0, 0]))
        mcu_report(proc, value_dp(DP_LEVEL_L1, level_write[0][1] - 3))
        assert dp_writes(take_tx(proc)) == [(DP_LEVEL_L1, scaled_to_tuya(100, 276, 591))]
    finally:
        proc.stop()


@pytest.mark.parametrize("written", [pytest.param(1, id="on-answered-with-off"),
                                     pytest.param(0, id="off-answered-with-on")])
@pytest.mark.parametrize("answer", ANSWER_CMDS)
def test_answer_with_the_other_value_is_shown_after_the_timeout(answer, written):
    """The MCU answers our on with off, or our off with on: it kept its state,
    or the wall switch was used and the answer to our write is lost. Held
    back while our write may still be answered, then shown when it times
    out (the stock MCU reports nothing more by itself, Zigbee must not keep
    showing our value), and never as the wall switch (no OnLevel write)."""
    proc, device = start()
    try:
        _lit_with_on_level(proc, device)
        if written:
            device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
            _answer_writes(proc, device, answer)
            assert onoff(device) == 0
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF,
                               ZCL_CMD_ONOFF_ON if written else ZCL_CMD_ONOFF_OFF)
        pump(proc)
        assert dp_writes(take_tx(proc)) == [(DP_ON_L1, written)]
        mcu_report(proc, bool_dp(DP_ON_L1, 1 - written), cmd=answer)   # not the answer
        assert onoff(device) == written, "shown before our write could be answered"
        later = _writes_after(proc, device, ACK_TIMEOUT_MS + 50, steps=1)
        assert onoff(device) == 1 - written, "the MCU's state was never shown"
        later += _writes_after(proc, device, 2500)                # 10 s
        assert onoff(device) == 1 - written
        # On from off goes to OnLevel 60 by itself (queued behind the on).
        expected = [(DP_LEVEL_L1, scaled_to_tuya(60, 276, 591))] if written else []
        assert later == expected, f"fights the MCU or took it for the wall switch: {later}"
    finally:
        proc.stop()


@pytest.mark.parametrize("answer", ANSWER_CMDS)
def test_late_answer_after_the_mcu_said_off_is_not_a_wall_press(answer):
    """As above, but the answer to our on at 200 comes after all, after the
    off was shown: the light is on again, and as the answer to our own on it
    is not the wall switch (no OnLevel 60 instead of 200)."""
    proc, device = start()
    try:
        _lit_with_on_level(proc, device)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        _answer_writes(proc, device, answer)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL,
                               ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([200, 0, 0]))
        pump(proc)
        tx_on = take_tx(proc)
        assert dp_writes(tx_on) == [(DP_ON_L1, 1)]
        mcu_report(proc, bool_dp(DP_ON_L1, 0), cmd=answer)       # not the answer
        device.step_time(ACK_TIMEOUT_MS + 50)
        pump(proc)
        tx_level = take_tx(proc)
        assert dp_writes(tx_level) == [(DP_LEVEL_L1, scaled_to_tuya(200, 276, 591))]
        assert onoff(device) == 0

        for unit in dp_units(tx_on):                             # the late answer
            mcu_report(proc, unit, cmd=answer)
        assert onoff(device) == 1
        writes = _answer_writes(proc, device, answer, dp_units(tx_level))
        device.step_time(HOLDOFF_MS + 200)
        pump(proc)
        assert writes == [], f"late answer taken for the wall switch: {writes}"
        assert (onoff(device), level(device)) == (1, 200)
    finally:
        proc.stop()


def test_report_held_back_is_dropped_when_a_newer_write_of_that_dp_waits():
    """On, off and on again in a row. While the first on waits for its
    answer the MCU reports off; the on that waits in the queue is newer than
    that report, so it is not shown when the first on times out."""
    proc, device = start()
    try:
        _lit_with_on_level(proc, device)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        _answer_writes(proc, device, TUYA_CMD_REPORT)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
        pump(proc)
        assert dp_writes(take_tx(proc)) == [(DP_ON_L1, 1)]
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON)
        pump(proc)
        mcu_report(proc, bool_dp(DP_ON_L1, 0))                   # not the answer
        device.step_time(ACK_TIMEOUT_MS + 50)
        pump(proc)
        assert onoff(device) == 1, "a report older than our newest on was shown"
        _answer_writes(proc, device, TUYA_CMD_REPORT, dp_units(take_tx(proc)))
        assert onoff(device) == 1
    finally:
        proc.stop()


@pytest.mark.parametrize("wait_ms,wall_press", [
    pytest.param(1000, False, id="late-answer"),
    pytest.param(3500, True, id="wall-switch-after-3s"),
])
def test_late_answer_to_an_on_that_timed_out(wait_ms, wall_press):
    """An on gets no answer in time; the off that follows does. An "on"
    report (0x06) up to 3 s after the timeout is the late answer to our on:
    shown, but not the wall switch. One later is the wall switch again and
    goes to OnLevel."""
    proc, device = start()
    try:
        _lit_with_on_level(proc, device)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        _answer_writes(proc, device, TUYA_CMD_REPORT)
        device.step_time(HOLDOFF_MS + 200)
        pump(proc)
        take_tx(proc)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL,
                               ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([200, 0, 0]))
        assert dp_writes(_until_sent(proc, device, (DP_ON_L1, 1))) == [(DP_ON_L1, 1)]
        device.step_time(ACK_TIMEOUT_MS + 50)                    # no answer in time
        tx_level = _until_sent(proc, device, (DP_LEVEL_L1, scaled_to_tuya(200, 276, 591)))
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        writes = _answer_writes(proc, device, TUYA_CMD_REPORT, dp_units(tx_level))
        assert writes == [(DP_ON_L1, 0)]
        assert onoff(device) == 0
        device.step_time(wait_ms)
        pump(proc)
        take_tx(proc)

        mcu_report(proc, bool_dp(DP_ON_L1, 1))
        assert onoff(device) == 1
        writes = _answer_writes(proc, device, TUYA_CMD_REPORT)
        on_level = [(DP_LEVEL_L1, scaled_to_tuya(60, 276, 591))]
        assert writes == (on_level if wall_press else []), writes
    finally:
        proc.stop()


def test_wall_switch_while_the_other_channel_waits_for_its_answer():
    """Only our own writes of the same DP make a report an answer or outdated:
    the wall switch of channel 1, while channel 2's on waits for its answer,
    still goes to channel 1's OnLevel."""
    proc, device = start()
    try:
        _lit_with_on_level(proc, device)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF)
        _answer_writes(proc, device, TUYA_CMD_REPORT)
        device.step_time(HOLDOFF_MS + 200)
        pump(proc)
        assert onoff(device) == 0
        device.call_zigbee_cmd(2, ZCL_CLUSTER_ON_OFF, 0x01)      # channel 2: on
        tx = _until_sent(proc, device, (DP_ON_L2, 1))            # waits for its answer
        assert dp_writes(tx) == [(DP_ON_L2, 1)]
        mcu_report(proc, bool_dp(DP_ON_L1, 1))                   # wall switch, channel 1
        assert onoff(device) == 1
        mcu_report(proc, bool_dp(DP_ON_L2, 1))                   # channel 2's answer
        writes = _answer_writes(proc, device, TUYA_CMD_REPORT)
        assert writes == [(DP_LEVEL_L1, scaled_to_tuya(60, 276, 591))]
    finally:
        proc.stop()
