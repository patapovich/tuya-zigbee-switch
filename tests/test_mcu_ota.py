"""Update of an own secondary-MCU firmware from the image the module carries
(src/zigbee/tuya_mcu_ota.c). The stub build carries the real image."""

import re
from pathlib import Path

from test_dimmer_mcu import TUYA_CMD_MCU_VERSION, start
from tuya_mcu import TUYA_CMD_QUERY_NETWORK, parse_frames, pump, take_tx, tuya_frame

OTA_NOTIFY = 0xA0
OTA_BLOCK = 0xA1
OTA_RESULT = 0xA2

IMAGE_C = Path(__file__).resolve().parent.parent / "src/zigbee/secondary_mcu_image.c"


def carried_image() -> bytes:
    text = IMAGE_C.read_text()
    body = text[text.index("secondary_mcu_image[] = {") : text.index("};")]
    return bytes(int(h, 16) for h in re.findall(r"0x([0-9a-f]{2})", body))


IMAGE = carried_image()
SIZE = int.from_bytes(IMAGE[0xC4:0xC8], "little")
CRC = int.from_bytes(IMAGE[0xC8:0xCC], "little")
VERSION = IMAGE[0xCC]


def cmds_after(proc, device, ms: int = 0) -> list[tuple[int, bytes]]:
    if ms:
        device.step_time(ms)
    pump(proc)
    return parse_frames(take_tx(proc))


def answer_version(proc, device, packed: int) -> list[tuple[int, bytes]]:
    """First contact: the module asks the version; the MCU answers packed.
    Returns what the module sends in the next second (queue drained)."""
    device.step_time(2100)
    pump(proc)
    take_tx(proc)
    proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_MCU_VERSION, bytes([packed]))}")
    frames = []
    for _ in range(8):
        frames += cmds_after(proc, device, 200)
    return frames


def test_carried_image_is_an_own_firmware():
    assert IMAGE[0xC0:0xC4] == b"APDZ"
    assert SIZE == len(IMAGE)
    assert VERSION >> 6 >= 2


def test_stock_mcu_is_never_offered_an_update():
    proc, device = start()
    try:
        frames = answer_version(proc, device, 0x47)  # stock 1.0.7
        assert OTA_NOTIFY not in [cmd for cmd, _ in frames]
    finally:
        proc.stop()


def test_own_mcu_with_the_carried_version_is_left_alone():
    proc, device = start()
    try:
        frames = answer_version(proc, device, VERSION)
        assert OTA_NOTIFY not in [cmd for cmd, _ in frames]
    finally:
        proc.stop()


def test_own_mcu_with_another_version_is_offered_the_carried_one():
    proc, device = start()
    try:
        other = VERSION + 1 if (VERSION & 0x0F) != 0x0F else VERSION - 1
        frames = answer_version(proc, device, other)
        offers = [data for cmd, data in frames if cmd == OTA_NOTIFY]
        assert offers == [bytes([VERSION]) + SIZE.to_bytes(4, "big") + CRC.to_bytes(4, "big")]
    finally:
        proc.stop()


def request(proc, device, offset: int, want: int) -> bytes:
    data = offset.to_bytes(4, "big") + want.to_bytes(2, "big")
    proc.exec(f"uart_rx {tuya_frame(OTA_BLOCK, data)}")
    blocks = [d for cmd, d in cmds_after(proc, device) if cmd == OTA_BLOCK]
    assert len(blocks) == 1
    block = blocks[0]
    assert block[0:4] == offset.to_bytes(4, "big")
    assert block[4:8] == SIZE.to_bytes(4, "big")
    assert block[8:12] == CRC.to_bytes(4, "big")
    assert block[12] == VERSION
    return block[13:]


def test_blocks_are_served_from_the_carried_image():
    proc, device = start()
    try:
        assert request(proc, device, 0, 64) == IMAGE[0:64]
        assert request(proc, device, 128, 128) == IMAGE[128:192]  # capped at 64
        assert request(proc, device, SIZE - 10, 128) == IMAGE[SIZE - 10 :]
        assert request(proc, device, SIZE, 64) == b""  # past the end
    finally:
        proc.stop()


def test_whole_image_can_be_fetched():
    proc, device = start()
    try:
        got = b""
        while len(got) < SIZE:
            got += request(proc, device, len(got), 128 - len(got) % 128)
        assert got == IMAGE
    finally:
        proc.stop()


def mcu_query(proc, device) -> list[int]:
    """The MCU asks the network state; returns the commands of the next 0.8 s."""
    proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_QUERY_NETWORK)}")
    frames = []
    for _ in range(4):
        frames += cmds_after(proc, device, 200)
    return [cmd for cmd, _ in frames]


def contact_by_mcu(proc, device) -> None:
    """First contact started by the MCU's own query, version answered."""
    assert TUYA_CMD_MCU_VERSION in mcu_query(proc, device)
    proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_MCU_VERSION, bytes([VERSION]))}")
    cmds_after(proc, device, 400)


def test_a_second_query_is_not_a_first_contact():
    proc, device = start()
    try:
        contact_by_mcu(proc, device)
        assert TUYA_CMD_MCU_VERSION not in mcu_query(proc, device)
    finally:
        proc.stop()


def test_after_a_good_result_the_next_query_is_a_first_contact():
    proc, device = start()
    try:
        contact_by_mcu(proc, device)
        proc.exec(f"uart_rx {tuya_frame(OTA_RESULT, bytes([0, VERSION]))}")
        cmds_after(proc, device)
        # The updated app starts and asks for the network state: the module
        # asks its version again (and dumps the settings).
        assert TUYA_CMD_MCU_VERSION in mcu_query(proc, device)
    finally:
        proc.stop()


def test_after_a_failed_result_nothing_is_reset():
    proc, device = start()
    try:
        contact_by_mcu(proc, device)
        proc.exec(f"uart_rx {tuya_frame(OTA_RESULT, bytes([1, VERSION]))}")
        cmds_after(proc, device)
        assert TUYA_CMD_MCU_VERSION not in mcu_query(proc, device)
    finally:
        proc.stop()
