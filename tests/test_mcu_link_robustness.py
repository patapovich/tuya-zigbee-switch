"""Robustness of the secondary-MCU link: framing, TX queue order, first
contact, relay echo window, and endpoints that share clusters."""
from client import StubProc
from conftest import Device
from zcl_consts import ZCL_ATTR_ONOFF
from test_dimmer_mcu import (
    DP_LEVEL_L1,
    DP_ON_L1,
    TUYA_CMD_REPORT,
    TUYA_TYPE_BOOL,
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZDMS16_2,
    bool_dp,
    dp_payload,
    dp_writes,
    mcu_report,
    onoff,
    parse_frames,
    pump,
    set_level,
    start,
    take_tx,
    tuya_frame,
    value_dp,
)


# ---------------------------------------------------------------- RX framing

def test_dlen_fff7_does_not_hang_rx_loop():
    """frame_len = 8 + 0xFFF7 + 1 wraps to 0 in uint16_t: checksum reads 64 KiB
    past g_rx_assembly, index [-1], memmove of 0 bytes, `continue` forever."""
    proc, device = start()
    try:
        proc.exec("uart_rx 55AA0200000" + "6FFF7")
        res = proc.exec("s", timeout=2.0)   # main loop runs the RX assembly
        assert res.ok
    finally:
        proc.stop()


def test_truncated_frame_does_not_eat_a_later_report():
    """No inter-byte timeout: a partial frame stays in the assembly buffer and
    swallows the next real frame, however much later it comes."""
    proc, device = start()
    try:
        # a frame cut short (dropped DMA chunk / ring overflow / glitch)
        proc.exec("uart_rx " + tuya_frame(TUYA_CMD_REPORT, bool_dp(DP_ON_L1, 1))[:18])
        pump(proc)
        device.step_time(60_000)
        pump(proc)
        # a minute later the wall switch turns channel 1 on
        mcu_report(proc, bool_dp(DP_ON_L1, 1))
        assert onoff(device, 1) == 1, "real report lost behind a stale partial frame"
    finally:
        proc.stop()


# ---------------------------------------------------------------- TX queue

def test_coalescing_must_not_move_level_behind_off():
    """Off / on-at-80 / off while a frame is in flight. In order the MCU would
    see on, level 80, off (and remember 80). Coalescing the second off into the
    queued 'on' slot sends: off, level 80 -> a level written while the channel
    is off, which the ZDMS16-2 acks but does not keep."""
    proc, device = start()
    try:
        set_level(proc, device, 50)   # channel on
        # level write while on: the MCU does not answer for ~0.9 s -> in flight
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
                               bytes([60, 0, 0, 0, 0]))
        pump(proc)
        assert [dp for dp, _ in dp_writes(take_tx(proc))] == [DP_LEVEL_L1]
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, 0x00)                       # off
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL,
                               ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([80, 0, 0]))
        device.call_zigbee_cmd(1, ZCL_CLUSTER_ON_OFF, 0x00)                       # off
        writes = []
        for _ in range(6):
            device.step_time(200)       # in-flight write times out
            pump(proc)
            w = dp_writes(take_tx(proc))
            for dpid, v in w:
                writes.append((dpid, v))
                mcu_report(proc, dp_payload(dpid, TUYA_TYPE_BOOL, bytes([v]))
                           if dpid == DP_ON_L1 else value_dp(dpid, v))
        # MCU state walk: is the last level write done while the channel is on?
        on = True
        level_kept_while_on = None
        for dpid, v in writes:
            if dpid == DP_ON_L1:
                on = bool(v)
            elif dpid == DP_LEVEL_L1:
                level_kept_while_on = on
        assert level_kept_while_on is not False, f"level written while off: {writes}"
    finally:
        proc.stop()


def test_query_all_respects_one_frame_in_flight():
    """IT init in flight, then 0x28 goes out at once (send_cmd bypasses the
    queue): two frames outstanding at first contact."""
    cfg = "hktk6hze;TS0601-TPZ2;Y9600;ST18;ST19;RT181E;RT191F;IT13400;IT12400;M;"
    proc = StubProc(device_config=cfg, dp_config="12E20;").start()
    try:
        Device(proc)
        pump(proc)
        take_tx(proc)
        proc.exec(f"uart_rx {tuya_frame(0x20)}")
        pump(proc)
        frames = [c for c, _ in parse_frames(take_tx(proc)) if c in (0x04, 0x28)]
        assert frames.count(0x04) + frames.count(0x28) <= 1, frames
    finally:
        proc.stop()


# ---------------------------------------------------------------- first contact

def test_it_inits_follow_the_mcus_first_network_query():
    """Base PR waited for the MCU's 0x20 (comment: before that the MCU 'drops
    whatever we send'). The 2 s timer now fires first and the later 0x20 no
    longer triggers the inits."""
    cfg = "hktk6hze;TS0601-TPZ2;Y9600;ST18;ST19;RT181E;RT191F;IT13400;M;"
    proc = StubProc(device_config=cfg).start()
    try:
        device = Device(proc)
        pump(proc)
        take_tx(proc)
        device.step_time(2500)     # MCU still booting / resyncing: nothing heard
        pump(proc)
        take_tx(proc)              # whatever went out here was not heard
        proc.exec(f"uart_rx {tuya_frame(0x20)}")
        pump(proc)
        tx = take_tx(proc)
        device.step_time(200)      # the timer's unanswered IT write times out first
        pump(proc)
        tx += take_tx(proc)
        for _ in range(2):         # frames written less than 2 ms apart wait (burst gate)
            device.step_time(2)
            pump(proc)
            tx += take_tx(proc)
        assert any(dp == 0x13 for dp, _ in dp_writes(tx)), "IT not sent after the MCU's 0x20"
    finally:
        proc.stop()


# ---------------------------------------------------------------- echo window vs queue

TPZ2 = "hktk6hze;TS0601-TPZ2;Y9600;ST18;ST19;RT181E;RT191F;PT26;M;"
DP_CONFIG = "12E20;13E21;24B22;25E23;67B24;68E25;69V26;6AV27;"


def test_relay_echo_after_queue_delay_is_not_a_key_press():
    """Echo window (700 ms) is armed when the relay write is QUEUED. Five
    unanswered config writes ahead of it (5 x 150 ms) push the real write past
    the window; the MCU's immediate echo is then taken for a touch."""
    proc = StubProc(device_config=TPZ2, dp_config=DP_CONFIG).start()
    try:
        device = Device(proc)
        pump(proc)
        take_tx(proc)
        for attr, val in ((0xFF20, 1), (0xFF21, 1), (0xFF22, 1), (0xFF23, 1), (0xFF24, 1)):
            device.write_zigbee_attr(1, 0x0000, attr, val)
        device.zcl_relay_on(3)            # relay DP 0x18 (= key DP of switch ep 1)
        pump(proc)
        device.clear_events()
        echoed = False
        for _ in range(12):
            device.step_time(150)
            pump(proc)
            for dpid, v in dp_writes(take_tx(proc)):
                if dpid == 0x18:          # MCU echoes the relay at once
                    mcu_report(proc, bool_dp(0x18, v))
                    echoed = True
        assert echoed
        presses = [e for e in device._events if e.kind == "zcl_attr_change"
                   and int(e.payload["ep"]) == 1 and int(e.payload["cluster"], 16) == 0x0012]
        assert not presses, "relay echo became a synthetic key press"
    finally:
        proc.stop()


def test_report_handed_on_after_a_relay_write_timed_out_is_not_a_key_press():
    """The MCU answers our relay on with off (0x06) and nothing else comes:
    the relay shows off, and when that report is handed on again after the
    write's ack timeout it is no key press either."""
    proc = StubProc(device_config=TPZ2, dp_config=DP_CONFIG).start()
    try:
        device = Device(proc)
        pump(proc)
        take_tx(proc)
        for _ in range(4):                # first-contact frames time out
            device.step_time(200)
            pump(proc)
            take_tx(proc)
        device.zcl_relay_on(3)            # relay DP 0x18 (= key DP of switch ep 1)
        pump(proc)
        assert dp_writes(take_tx(proc)) == [(0x18, 1)]
        device.clear_events()
        mcu_report(proc, bool_dp(0x18, 0))
        for _ in range(4):
            device.step_time(100)
            pump(proc)
            take_tx(proc)
        assert int(device.read_zigbee_attr(3, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF)) == 0
        presses = [e for e in device._events if e.kind == "zcl_attr_change"
                   and int(e.payload["ep"]) == 1 and int(e.payload["cluster"], 16) == 0x0012]
        assert not presses, "report handed on after the timeout became a key press"
    finally:
        proc.stop()


# ---------------------------------------------------------------- shared clusters

def test_switch_type_write_on_dimmer_endpoint():
    """genOnOffSwitchCfg lives on dimmer endpoints too; writing it went
    through the switch cluster path with a NULL cluster (crash)."""
    proc, device = start()
    try:
        device.write_zigbee_attr(1, 0x0007, 0x0000, 2)
        pump(proc)
        assert (0x04, 2) in dp_writes(take_tx(proc))  # DP 4 = switch type L1
        assert proc.exec("s").ok
    finally:
        proc.stop()


def test_no_time_cluster_and_time_sync_answered_with_zeros():
    """No genTime: nothing would ever write it (z2m does not push the time).
    The MCU's time request (0x24) still gets its reply, eight zero bytes."""
    proc, device = start()
    try:
        assert not proc.exec("zcl_read 1 0x000A 0x0000").ok
        proc.exec(f"uart_rx {tuya_frame(0x24, seq=0x1234)}")
        pump(proc)
        replies = [(cmd, data) for cmd, data in parse_frames(take_tx(proc)) if cmd == 0x24]
        assert replies == [(0x24, bytes(8))]
    finally:
        proc.stop()


# ---------------------------------------------------------------- burst gate

def _quiet_stock_link(proc, device) -> None:
    """First contact done with a stock MCU (1.0.7), then its first query
    (the contact once more): from here on a query gets only its reply."""
    from test_dimmer_mcu import TUYA_CMD_MCU_VERSION, bool_dp
    device.step_time(2100)
    pump(proc)
    take_tx(proc)
    proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_MCU_VERSION, bytes([0x47]))} "
              f"{tuya_frame(0x06, bool_dp(1, 0))}")
    for seq in (1, 2):
        proc.exec(f"uart_rx {tuya_frame(0x20, seq=seq)}")
        for _ in range(3):
            device.step_time(200)
            pump(proc)
            take_tx(proc)
            proc.exec(f"uart_rx {tuya_frame(0x0B, bytes([0x47]))} "
                      f"{tuya_frame(0x06, bool_dp(1, 0))}")


def _replies(proc, device, steps_ms: list[int]) -> list[list[tuple[int, int]]]:
    """(cmd, seq) of what went out at once, then after each step."""
    out = []
    for ms in [0] + steps_ms:
        if ms:
            device.step_time(ms)
        pump(proc)
        raw = take_tx(proc)
        frames, i = [], 0
        for cmd, data in parse_frames(raw):
            frames.append((cmd, int.from_bytes(raw[i + 3:i + 5], "big")))
            i += 9 + len(data)
        out.append(frames)
    return out


def test_two_replies_to_a_factory_mcu_go_2_ms_apart():
    """Contract item 6: the network and time queries in one burst get 10 + 17
    bytes back, more than a stock MCU's 23-byte ring: the second reply waits
    2 ms, the order and the sequence numbers stay."""
    from test_dimmer_mcu import ZDMS16_2
    proc = StubProc(device_config=ZDMS16_2).start()
    try:
        device = Device(proc)
        pump(proc)
        _quiet_stock_link(proc, device)
        proc.exec(f"uart_rx {tuya_frame(0x20, seq=5)} {tuya_frame(0x24, seq=6)}")
        assert _replies(proc, device, [1, 1]) == [[(0x20, 5)], [], [(0x24, 6)]]
        device.step_time(5)
        proc.exec(f"uart_rx {tuya_frame(0x20, seq=7)} {tuya_frame(0x25, seq=8)}")
        assert _replies(proc, device, []) == [[(0x20, 7), (0x25, 8)]]     # 20 bytes: at once
    finally:
        proc.stop()


def test_three_held_replies_keep_their_order():
    from test_dimmer_mcu import ZDMS16_2
    proc = StubProc(device_config=ZDMS16_2).start()
    try:
        device = Device(proc)
        pump(proc)
        _quiet_stock_link(proc, device)
        proc.exec("uart_rx " + " ".join(tuya_frame(cmd, seq=seq) for cmd, seq in
                                        ((0x24, 1), (0x24, 2), (0x20, 3), (0x24, 4))))
        got = [f for step in _replies(proc, device, [2, 2, 2, 2]) for f in step]
        assert got == [(0x24, 1), (0x24, 2), (0x20, 3), (0x24, 4)]
    finally:
        proc.stop()


def test_open_dimmer_gets_its_replies_at_once():
    """It said who it is: its UART takes any burst."""
    from test_mcu_ota import VERSION, contact_as_own, start
    proc, device = start()
    try:
        contact_as_own(proc, device, VERSION)
        take_tx(proc)
        proc.exec(f"uart_rx {tuya_frame(0x20, seq=5)} {tuya_frame(0x24, seq=6)}")
        assert _replies(proc, device, []) == [[(0x20, 5), (0x24, 6)]]
    finally:
        proc.stop()


def test_the_queued_frame_a_reply_starts_waits_2_ms():
    """The burst the stock-compatibility gate found (a touch switch's factory
    MCU): its first network query gets the reply (10 bytes) and starts the
    first contact, whose IT write (14 bytes) now leaves 2 ms later."""
    cfg = "hktk6hze;TS0601-TPZ2;Y9600;ST18;ST19;RT181E;RT191F;IT13400;M;"
    proc = StubProc(device_config=cfg).start()
    try:
        device = Device(proc)
        pump(proc)
        take_tx(proc)
        proc.exec(f"uart_rx {tuya_frame(0x20, seq=0)}")
        steps = _replies(proc, device, [1, 1])
        assert [cmd for cmd, _ in steps[0]] == [0x20]
        assert steps[1] == [] and [cmd for cmd, _ in steps[2]] == [0x04]
    finally:
        proc.stop()
