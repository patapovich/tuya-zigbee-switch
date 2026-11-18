"""The UART under the secondary-MCU link: how the Telink HAL hands received
bytes over (DMA chunks), the stub's TX capture, and recovery from a wedged
UART (src/telink/hal/uart.c, src/stub/hal/uart.c)."""
from test_dimmer_mcu import start
from test_mcu_ota import IMAGE, OTA_BLOCK, SIZE
from tuya_mcu import parse_frames, pump, take_tx, tuya_frame


def frame(cmd: int, data: bytes, seq: int) -> bytes:
    return bytes.fromhex(tuya_frame(cmd, data, seq))


def uart_info(proc) -> dict[str, int]:
    res = proc.exec("uart_info")
    assert res.ok
    return {k: int(v) for k, v in res.payload.items()}


def inject(proc, chunk: bytes) -> None:
    res = proc.exec(f"uart_rx {chunk.hex()}")
    assert res.ok and int(res.payload["injected"]) == len(chunk)


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
