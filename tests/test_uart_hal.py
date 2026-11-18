"""The UART under the secondary-MCU link: how the Telink HAL hands received
bytes over (DMA chunks), the stub's TX capture, and recovery from a wedged
UART (src/telink/hal/uart.c, src/stub/hal/uart.c)."""
import re
from pathlib import Path

from test_dimmer_mcu import (
    DP_ON_L2,
    TUYA_CMD_MCU_VERSION,
    ZCL_ATTR_BALLAST_MIN,
    ZCL_CLUSTER_BALLAST,
    onoff,
    start,
    tuya_to_zcl,
)
from test_mcu_diag import counters
from test_mcu_ota import IMAGE, OTA_BLOCK, OTA_IDENT, SIZE, VERSION, cmds_after, shown_version
from tuya_mcu import (
    TUYA_CMD_REPORT,
    TUYA_CMD_REPORT_PASSIVE,
    bool_dp,
    enum_dp,
    parse_frames,
    pump,
    take_tx,
    tuya_frame,
    value_dp,
)

UART_C = Path(__file__).resolve().parent.parent / "src/telink/hal/uart.c"
ATTR_FADE_ON_L2 = 0xFF37        # dp 0x9B in the board's dp config


def telink_router_chunk_max() -> int:
    """Payload of the Telink RX DMA buffer on routers (the ZDMS16-2 build)."""
    text = UART_C.read_text()
    m = re.search(r"#ifdef END_DEVICE\s+#define UART_RX_DMA_BUF_SIZE\s+\d+\s+"
                  r"#else\s+#define UART_RX_DMA_BUF_SIZE\s+(\d+)\s+#endif", text)
    assert m, "UART_RX_DMA_BUF_SIZE block not found"
    return int(m.group(1)) - 4


def frame(cmd: int, data: bytes, seq: int) -> bytes:
    return bytes.fromhex(tuya_frame(cmd, data, seq))


def uart_info(proc) -> dict[str, int]:
    res = proc.exec("uart_info")
    assert res.ok
    return {k: int(v) for k, v in res.payload.items()}


def inject(proc, chunk: bytes) -> None:
    res = proc.exec(f"uart_rx {chunk.hex()}")
    assert res.ok and int(res.payload["injected"]) == len(chunk)


def own_mcu_dump(seq: int) -> bytes:
    """The status dump of the own PY32 firmware (dimmer_report_all): DP 14,
    then per channel its dimmer DPs + curve + power-on, then its profile."""
    def channel(ch: int, on: int, lvl: int, mn: int, mx: int, curve: int, pon: int) -> bytes:
        base = 6 * ch
        return (bool_dp(base + 1, on) + value_dp(base + 2, lvl) + value_dp(base + 3, mn)
                + enum_dp(base + 4, 0) + value_dp(base + 5, mx) + value_dp(base + 6, 0)
                + enum_dp(101 + ch, curve) + enum_dp(103 + ch, pon))

    def profile(ch: int, fade_on: int) -> bytes:
        dps = [fade_on, 950, 12, 3300, 20]
        return b"".join(value_dp(150 + 5 * ch + k, v) for k, v in enumerate(dps))

    units = [enum_dp(14, 0), channel(0, 1, 640, 120, 1000, 2, 0), profile(0, 950),
             channel(1, 0, 1000, 230, 900, 4, 1), profile(1, 1800)]
    return b"".join(frame(TUYA_CMD_REPORT_PASSIVE, u, seq + i) for i, u in enumerate(units))


def test_own_mcu_start_bursts_are_what_the_buffer_must_hold():
    dump = own_mcu_dump(0)
    assert len(dump) == 234
    assert len(dump + frame(TUYA_CMD_MCU_VERSION, bytes([VERSION]), 0)) == 244
    assert len(dump + frame(OTA_IDENT, b"ZDMS" + bytes([1, VERSION, 1]), 0)) == 250


def contact_in_bursts(proc, device, after_ident: bytes = b"") -> None:
    """MCU start as the Telink DMA delivers it: the dump and the version
    answer in one chunk, then the dump and the identity answer (and whatever
    the MCU sent right behind it) in another."""
    device.step_time(2100)
    pump(proc)
    take_tx(proc)                                   # version query, query-all
    inject(proc, own_mcu_dump(1) + frame(TUYA_CMD_MCU_VERSION, bytes([VERSION]), 0))
    cmds = []
    for _ in range(5):
        cmds += [cmd for cmd, _ in cmds_after(proc, device, 100)]
        if OTA_IDENT in cmds:
            break
    assert OTA_IDENT in cmds, "the version answer in the burst was lost"
    inject(proc, own_mcu_dump(6) + frame(OTA_IDENT, b"ZDMS" + bytes([1, VERSION, 1]), 11)
           + after_ident)
    pump(proc)


def test_mcu_start_bursts_fit_the_telink_rx_buffer_with_room_for_one_more_frame():
    """A wall switch press sent right behind the identity answer arrives in the
    same chunk (264 bytes): all of it must get through."""
    proc, device = start()
    try:
        assert proc.exec(f"uart_dma {telink_router_chunk_max()}").ok
        press = frame(TUYA_CMD_REPORT, bool_dp(DP_ON_L2, 1), 12)
        contact_in_bursts(proc, device, after_ident=press)
        assert uart_info(proc)["rx_dropped"] == 0
        assert shown_version(device).endswith(" own")
        assert onoff(device, 1) == 1
        assert int(device.read_zigbee_attr(2, ZCL_CLUSTER_BALLAST, ZCL_ATTR_BALLAST_MIN)) == tuya_to_zcl(230)
        assert int(device.read_zigbee_attr(1, 0x0000, ATTR_FADE_ON_L2)) == 1800
        assert onoff(device, 2) == 1                  # the press after the dump
    finally:
        proc.stop()


def test_chunk_over_the_dma_buffer_is_lost_whole():
    """What the old 256 byte buffer (252 usable) did with the same start."""
    proc, device = start()
    try:
        assert proc.exec("uart_dma 252").ok
        press = frame(TUYA_CMD_REPORT, bool_dp(DP_ON_L2, 1), 12)
        contact_in_bursts(proc, device, after_ident=press)
        assert uart_info(proc)["rx_dropped"] == 250 + len(press)
        assert counters(device)["rx_hal_dropped"] == 250 + len(press)
        assert not shown_version(device).endswith(" own")
        assert onoff(device, 2) == 0
    finally:
        proc.stop()


def test_tx_capture_keeps_everything_past_256_bytes():
    """Four 86 byte block answers before the test looks: the stub's TX ring
    used to take head == tail for empty at 256 bytes and lose all of it."""
    proc, device = start()
    try:
        for i in range(4):
            data = (64 * i).to_bytes(4, "big") + (64).to_bytes(2, "big")
            inject(proc, frame(OTA_BLOCK, data, i))
            pump(proc)
        blocks = [d for cmd, d in parse_frames(take_tx(proc)) if cmd == OTA_BLOCK]
        assert len(blocks) == 4
        for i, block in enumerate(blocks):
            assert block[4:8] == SIZE.to_bytes(4, "big")
            assert block[13:] == IMAGE[64 * i : 64 * (i + 1)]
    finally:
        proc.stop()


def test_uart_rx_refuses_what_it_cannot_inject():
    proc, _ = start()
    try:
        assert not proc.exec("uart_rx " + "00" * 2049).ok
        assert not proc.exec("uart_rx 0").ok
        res = proc.exec("uart_rx " + "00" * 300 + " " + "11" * 300)
        assert res.ok and int(res.payload["injected"]) == 600
        assert uart_info(proc)["rx_dropped"] == 600 - 511     # ring: 511 bytes
    finally:
        proc.stop()
