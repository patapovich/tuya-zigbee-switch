"""Update of an own secondary-MCU firmware from the image the module carries
(src/zigbee/tuya_mcu_ota.c). The stub build carries the real image."""

import re
import subprocess
import zlib
from pathlib import Path

import pytest
from client import StubProc
from conftest import Device
from test_dimmer_mcu import ATTR_MCU_VERSION, TUYA_CMD_MCU_VERSION, ZDMS16_2, ZDMS16_2_DP_CONFIG, start
from tuya_mcu import (
    TUYA_CMD_QUERY_ALL,
    TUYA_CMD_QUERY_NETWORK,
    TUYA_CMD_WRITE,
    parse_frames,
    pump,
    take_tx,
    tuya_frame,
)

OTA_NOTIFY = 0xA0
OTA_BLOCK = 0xA1
OTA_RESULT = 0xA2
OTA_IDENT = 0xA3

ROOT = Path(__file__).resolve().parent.parent
IMAGE_C = ROOT / "src/zigbee/secondary_mcu_image.c"
IMAGE_FLAG_SIM, IMAGE_FLAG_MEASURE = 0x01, 0x02


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
    assert SIZE == len(IMAGE) <= 12 * 1024           # app area behind the 4 KB bootloader
    assert VERSION >> 6 >= 2
    assert not IMAGE[0xCD] & (IMAGE_FLAG_SIM | IMAGE_FLAG_MEASURE)   # never a bench build
    zeroed = IMAGE[:0xC8] + bytes(4) + IMAGE[0xCC:]
    assert zlib.crc32(zeroed) == CRC
    sp, reset = int.from_bytes(IMAGE[0:4], "little"), int.from_bytes(IMAGE[4:8], "little")
    assert 0x08001000 <= reset < 0x08004000 and sp >> 20 == 0x200


def ident_answer(version: int) -> str:
    return f"uart_rx {tuya_frame(OTA_IDENT, b'ZDMS' + bytes([1, version, 1]))}"


def shown_version(device) -> str:
    return device.read_zigbee_attr(1, 0x0000, ATTR_MCU_VERSION)


# Version bytes: 2 bits major, 2 bits minor, 4 bits patch, compared as one
# byte (2.1.15 + 1 = 2.2.0). 0xBF is reserved for SIM bench builds, 0xFF is
# the ceiling.
VERSION_SIM = 0xBF


def older_version() -> int:
    """The carried version minus one (2.1.0 -> 2.0.15): still our own firmware (major 2)."""
    assert VERSION - 1 >= 0x80, "carried 2.0.0: no older own version to test with"
    return VERSION - 1 if VERSION - 1 != VERSION_SIM else VERSION - 2


def newer_version() -> int:
    """A version above the carried one, as a bench build may have."""
    newer = VERSION + 1 if VERSION + 1 != VERSION_SIM else VERSION + 2
    if newer >= 0xFF:
        pytest.skip("carried version at the ceiling: none newer")
    return newer


def test_version_helpers_stay_in_the_valid_range():
    assert older_version() < VERSION < newer_version() < 0xFF
    assert VERSION_SIM not in (older_version(), newer_version())
    if VERSION < 0xBE:
        assert newer_version() == VERSION + 1


def test_stock_1x_mcu_is_not_even_asked():
    proc, device = start()
    try:
        cmds = [cmd for cmd, _ in answer_version(proc, device, 0x47)]  # stock 1.0.7
        assert OTA_IDENT not in cmds and OTA_NOTIFY not in cmds
        assert shown_version(device) == "1.0.7"
    finally:
        proc.stop()


def test_stock_mcu_with_a_2x_version_is_never_offered_an_update():
    """A vendor MCU that happens to say 2.x does not answer the identity
    query: shown as it is, never offered our firmware."""
    proc, device = start()
    try:
        cmds = [cmd for cmd, _ in answer_version(proc, device, newer_version())]
        assert OTA_IDENT in cmds
        assert OTA_NOTIFY not in cmds
        shown = shown_version(device)
        assert shown and not shown.endswith("own")
    finally:
        proc.stop()


def test_version_is_shown_only_once_the_mcu_said_who_it_is():
    proc, device = start()
    try:
        device.step_time(2100)
        pump(proc)
        take_tx(proc)
        proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_MCU_VERSION, bytes([VERSION]))}")
        pump(proc)
        assert shown_version(device) == ""          # not without "own"
        proc.exec(ident_answer(VERSION))
        pump(proc)
        assert shown_version(device).endswith(" own")
    finally:
        proc.stop()


def contact_as_own(proc, device, running: int):
    """Version answered; once the module asks who it is, our firmware answers.
    Returns (commands up to the identity query, frames after the answer)."""
    device.step_time(2100)
    pump(proc)
    take_tx(proc)
    proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_MCU_VERSION, bytes([running]))}")
    cmds = []
    for _ in range(5):                   # behind the settings query in the queue
        cmds += [cmd for cmd, _ in cmds_after(proc, device, 100)]
        if OTA_IDENT in cmds:
            break
    assert OTA_IDENT in cmds, "the module must ask before our firmware answers"
    proc.exec(ident_answer(running))
    frames = []
    for _ in range(8):
        frames += cmds_after(proc, device, 200)
    return cmds, frames


def test_own_mcu_with_the_carried_version_is_left_alone():
    proc, device = start()
    try:
        _, frames = contact_as_own(proc, device, VERSION)
        assert OTA_NOTIFY not in [cmd for cmd, _ in frames]
    finally:
        proc.stop()


def test_own_mcu_with_an_older_version_is_offered_the_carried_one():
    proc, device = start()
    try:
        cmds, frames = contact_as_own(proc, device, older_version())
        assert OTA_IDENT in cmds
        offers = [data for cmd, data in frames if cmd == OTA_NOTIFY]
        assert offers == [bytes([VERSION]) + SIZE.to_bytes(4, "big") + CRC.to_bytes(4, "big")]
        assert shown_version(device).endswith(" own")
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


def test_own_mcu_with_a_newer_version_is_left_alone():
    """Only ever to a newer firmware: a newer one flashed on the bench stays."""
    proc, device = start()
    try:
        _, frames = contact_as_own(proc, device, newer_version())
        assert OTA_NOTIFY not in [cmd for cmd, _ in frames]
        assert shown_version(device).endswith(" own")
    finally:
        proc.stop()


def test_an_identity_answer_nobody_asked_for_is_ignored():
    proc, device = start()
    try:
        device.step_time(2100)
        pump(proc)
        take_tx(proc)
        proc.exec(ident_answer(older_version()))     # before any question
        cmds = [cmd for cmd, _ in cmds_after(proc, device, 400)]
        assert OTA_NOTIFY not in cmds
        assert not shown_version(device).endswith("own")
    finally:
        proc.stop()


def test_an_identity_with_another_protocol_is_not_ours():
    proc, device = start()
    try:
        device.step_time(2100)
        pump(proc)
        take_tx(proc)
        proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_MCU_VERSION, bytes([older_version()]))}")
        for _ in range(5):
            if OTA_IDENT in [cmd for cmd, _ in cmds_after(proc, device, 100)]:
                break
        bad = b"ZDMS" + bytes([1, older_version(), 2])
        proc.exec(f"uart_rx {tuya_frame(OTA_IDENT, bad)}")
        frames = []
        for _ in range(8):
            frames += cmds_after(proc, device, 200)
        assert OTA_NOTIFY not in [cmd for cmd, _ in frames]
        assert not shown_version(device).endswith("own")
    finally:
        proc.stop()


def served(proc, device) -> bool:
    proc.exec(f"uart_rx {tuya_frame(OTA_BLOCK, (0).to_bytes(4, 'big') + (64).to_bytes(2, 'big'))}")
    return OTA_BLOCK in [cmd for cmd, _ in cmds_after(proc, device)]


def test_after_three_failed_results_nothing_is_served():
    proc, device = start()
    try:
        for _ in range(3):
            proc.exec(f"uart_rx {tuya_frame(OTA_RESULT, bytes([1, VERSION]))}")
            cmds_after(proc, device)
        proc.exec(f"uart_rx {tuya_frame(OTA_BLOCK, (0).to_bytes(4, 'big') + (64).to_bytes(2, 'big'))}")
        assert OTA_BLOCK not in [cmd for cmd, _ in cmds_after(proc, device)]
    finally:
        proc.stop()


def flagged_stub(tmp_path: Path, flags: int) -> list[str]:
    """A stub that carries the image with header flags set (CRC still right,
    so the flags are the only thing wrong with it)."""
    img = bytearray(IMAGE)
    img[0xCD] |= flags
    img[0xC8:0xCC] = bytes(4)
    img[0xC8:0xCC] = zlib.crc32(bytes(img)).to_bytes(4, "little")
    body = ",\n".join(", ".join(f"0x{b:02x}" for b in img[i:i + 16]) for i in range(0, len(img), 16))
    src = tmp_path / "secondary_mcu_image.c"
    src.write_text('#include "zigbee/secondary_mcu_image.h"\n'
                   f"const uint8_t secondary_mcu_image[] = {{\n{body}\n}};\n"
                   "const uint32_t secondary_mcu_image_size = sizeof(secondary_mcu_image);\n")
    binary = tmp_path / "stub_device"
    subprocess.run(["make", "-s", "-C", str(ROOT / "src/stub"), "build", f"BINARY={binary}",
                    f"MCU_IMAGE_SRC={src}"], check=True, capture_output=True)
    return [str(binary)]


@pytest.mark.parametrize("flags", [IMAGE_FLAG_SIM, IMAGE_FLAG_MEASURE])
def test_a_bench_or_measurement_image_is_never_offered_or_served(tmp_path, flags):
    proc = StubProc(cmd=flagged_stub(tmp_path, flags), device_config=ZDMS16_2,
                    dp_config=ZDMS16_2_DP_CONFIG).start()
    device = Device(proc)
    try:
        pump(proc)
        take_tx(proc)
        cmds, frames = contact_as_own(proc, device, older_version())
        assert OTA_IDENT in cmds                     # asked who it is: ours
        assert OTA_NOTIFY not in [cmd for cmd, _ in frames]
        assert shown_version(device).endswith(" own")
        assert not served(proc, device)              # nor to a bootloader that asks
    finally:
        proc.stop()


def test_own_mcu_restarting_alone_is_noticed():
    """Our firmware numbers its network queries from 0 at every start."""
    proc, device = start()
    try:
        contact_as_own(proc, device, VERSION)
        proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_QUERY_NETWORK, seq=0)}")
        frames = []
        for _ in range(4):
            frames += cmds_after(proc, device, 200)
        assert TUYA_CMD_MCU_VERSION in [cmd for cmd, _ in frames]
    finally:
        proc.stop()


def test_lost_write_to_own_mcu_is_sent_again_then_everything_read():
    proc, device = start()
    try:
        contact_as_own(proc, device, VERSION)
        take_tx(proc)
        device.call_zigbee_cmd(1, 0x0006, 0x01)   # On
        frames = []
        for _ in range(8):                   # the MCU never answers
            frames += cmds_after(proc, device, 100)
        writes = [d for cmd, d in frames if cmd == TUYA_CMD_WRITE and d[:1] == bytes([1])]
        assert len(writes) == 3              # first try + 2 retries
        assert TUYA_CMD_QUERY_ALL in [cmd for cmd, _ in frames]
    finally:
        proc.stop()



def test_offers_stop_when_the_update_never_starts():
    """An MCU that accepts but never fetches (e.g. a layout mismatch) is not
    offered the update over and over."""
    proc, device = start()
    try:
        offers = 0
        for _ in range(5):
            _, frames = contact_as_own(proc, device, older_version())
            offers += [cmd for cmd, _ in frames].count(OTA_NOTIFY)
            proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_QUERY_NETWORK, seq=0)}")   # it restarts
            cmds_after(proc, device, 200)
        assert offers == 3
    finally:
        proc.stop()
