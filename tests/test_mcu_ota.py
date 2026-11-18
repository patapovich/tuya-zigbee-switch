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


def offer(image: bytes = IMAGE) -> bytes:
    """The 0xA0 offer of an image: version byte, size, CRC, build (BE)."""
    return (image[0xCC:0xCD] + image[0xC4:0xC8][::-1] + image[0xC8:0xCC][::-1]
            + image[0xCE:0xD0][::-1])


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


def ident_answer(version: int, build: int | None = None, boot: int | None = None) -> str:
    """Open Dimmer's identity answer: 7 bytes from a 2.1.x app; a v2 app adds
    its build (BE) and its bootloader's version (10 bytes)."""
    data = b"ZDMS" + bytes([1, version, 1])
    if build is not None:
        data += build.to_bytes(2, "big")
    if boot is not None:
        data += bytes([boot])
    return f"uart_rx {tuya_frame(OTA_IDENT, data)}"


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
    query: shown as it is once asked 4 times, never offered our firmware."""
    proc, device = start()
    try:
        cmds = [cmd for cmd, _ in answer_version(proc, device, newer_version())]
        for _ in range(30):
            cmds += [cmd for cmd, _ in cmds_after(proc, device, 200)]
        assert cmds.count(OTA_IDENT) == 4
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


def contact_as_own(proc, device, running: int, build: int | None = None,
                   boot: int | None = None):
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
    proc.exec(ident_answer(running, build, boot))
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
        assert offers == [offer()]                   # a 2.1.x app reads the first 9 bytes
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


def test_a_bootloader_fetching_the_image_is_not_asked_its_version():
    """A bootloader without an app asks for the image by itself, here right
    after the module's contact timer asked the version. Its first block
    request means the MCU restarted: the version question is dropped (asked at
    the app's first query), not sent again between the blocks."""
    proc, device = start()
    try:
        device.step_time(2100)
        pump(proc)
        assert TUYA_CMD_MCU_VERSION in [cmd for cmd, _ in parse_frames(take_tx(proc))]
        sent = []
        for k in range(40):                          # 8 s of blocks
            data = (64 * k).to_bytes(4, "big") + (64).to_bytes(2, "big")
            proc.exec(f"uart_rx {tuya_frame(OTA_BLOCK, data)}")
            sent += [cmd for cmd, _ in cmds_after(proc, device, 200)]
        assert sent.count(OTA_BLOCK) == 40
        assert TUYA_CMD_MCU_VERSION not in sent
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


def test_an_identity_with_another_update_protocol_is_ours_but_not_offered():
    """The protocol byte is the update protocol's: a later own firmware with
    another one keeps its DPs (and its entities in Z2M), it only cannot take
    this image."""
    proc, device = start()
    try:
        device.step_time(2100)
        pump(proc)
        take_tx(proc)
        proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_MCU_VERSION, bytes([older_version()]))}")
        for _ in range(5):
            if OTA_IDENT in [cmd for cmd, _ in cmds_after(proc, device, 100)]:
                break
        other = b"ZDMS" + bytes([1, older_version(), 2])
        proc.exec(f"uart_rx {tuya_frame(OTA_IDENT, other)}")
        frames = []
        for _ in range(8):
            frames += cmds_after(proc, device, 200)
        assert OTA_NOTIFY not in [cmd for cmd, _ in frames]
        assert shown_version(device).endswith(" own")
    finally:
        proc.stop()


def test_an_identity_of_another_kind_is_not_ours():
    proc, device = start()
    try:
        device.step_time(2100)
        pump(proc)
        take_tx(proc)
        proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_MCU_VERSION, bytes([older_version()]))}")
        for _ in range(5):
            if OTA_IDENT in [cmd for cmd, _ in cmds_after(proc, device, 100)]:
                break
        proc.exec(f"uart_rx {tuya_frame(OTA_IDENT, b'ZDMS' + bytes([2, older_version(), 1]))}")
        frames = []
        for _ in range(8):
            frames += cmds_after(proc, device, 200)
        assert OTA_NOTIFY not in [cmd for cmd, _ in frames]
        assert shown_version(device) == ""          # still asking
    finally:
        proc.stop()


MINUTE = 60_000


def fail(proc, device) -> None:
    proc.exec(f"uart_rx {tuya_frame(OTA_RESULT, bytes([1, VERSION]))}")
    cmds_after(proc, device)


def served(proc, device) -> bool:
    proc.exec(f"uart_rx {tuya_frame(OTA_BLOCK, (0).to_bytes(4, 'big') + (64).to_bytes(2, 'big'))}")
    return OTA_BLOCK in [cmd for cmd, _ in cmds_after(proc, device)]


def ota_state(device) -> tuple[int, int]:
    """(failed results since the last good one, current pause in minutes)
    from the link diagnostics."""
    from test_mcu_diag import counters
    c = counters(device)
    return c["ota_failures"], c["ota_pause_min"]


def test_after_three_failed_results_nothing_is_served_for_a_while():
    proc, device = start()
    try:
        for _ in range(2):
            fail(proc, device)
            assert served(proc, device)              # the bootloader tries again
        fail(proc, device)
        assert not served(proc, device)
        assert ota_state(device) == (3, 15)
        device.write_zigbee_attr(1, 0x0000, 0xFF4F, 1)   # clearing the counters keeps it
        assert ota_state(device) == (3, 15)
        device.step_time(15 * MINUTE - 100)
        assert not served(proc, device)
        device.step_time(200)
        assert served(proc, device)                  # the MCU has no app: not for good
        assert ota_state(device) == (3, 0)
    finally:
        proc.stop()


def test_the_pause_doubles_up_to_a_day_and_a_good_result_ends_it():
    proc, device = start()
    try:
        for _ in range(3):
            fail(proc, device)
        pauses = [15]
        for _ in range(8):
            device.step_time(pauses[-1] * MINUTE)
            assert served(proc, device)
            fail(proc, device)
            assert not served(proc, device)
            pauses.append(ota_state(device)[1])
        assert pauses == [15, 30, 60, 120, 240, 480, 960, 1440, 1440]
        device.step_time(23 * 60 * MINUTE)
        assert not served(proc, device)
        device.step_time(60 * MINUTE)
        assert served(proc, device)
        proc.exec(f"uart_rx {tuya_frame(OTA_RESULT, bytes([0, VERSION]))}")
        cmds_after(proc, device)
        assert ota_state(device) == (0, 0)
        for _ in range(2):                           # counted from 0 again
            fail(proc, device)
            assert served(proc, device)
    finally:
        proc.stop()


def variant(flags: int = 0, build: int | None = None, sp: int | None = None,
            reset: int | None = None, pad_to: int | None = None, version: int | None = None,
            crc: int | None = None) -> bytes:
    """The carried image with header fields or vectors changed; the size and
    CRC fields are made right again (unless `crc` is given), so the change is
    the only thing wrong with it."""
    img = bytearray(IMAGE)
    if pad_to is not None:
        img += bytes(pad_to - len(img))
    img[0xCD] |= flags
    if build is not None:
        img[0xCE:0xD0] = build.to_bytes(2, "little")
    if version is not None:
        img[0xCC] = version
    if sp is not None:
        img[0:4] = sp.to_bytes(4, "little")
    if reset is not None:
        img[4:8] = reset.to_bytes(4, "little")
    img[0xC4:0xC8] = len(img).to_bytes(4, "little")
    img[0xC8:0xCC] = bytes(4)
    img[0xC8:0xCC] = (zlib.crc32(bytes(img)) if crc is None else crc).to_bytes(4, "little")
    return bytes(img)


def image_stub(tmp_path: Path, img: bytes, extra_cflags: str = "") -> list[str]:
    """A stub that carries `img`."""
    body = ",\n".join(", ".join(f"0x{b:02x}" for b in img[i:i + 16]) for i in range(0, len(img), 16))
    src = tmp_path / "secondary_mcu_image.c"
    src.write_text('#include "zigbee/secondary_mcu_image.h"\n'
                   f"const uint8_t secondary_mcu_image[] = {{\n{body}\n}};\n"
                   "const uint32_t secondary_mcu_image_size = sizeof(secondary_mcu_image);\n")
    binary = tmp_path / "stub_device"
    subprocess.run(["make", "-s", "-C", str(ROOT / "src/stub"), "build", f"BINARY={binary}",
                    f"MCU_IMAGE_SRC={src}", f"EXTRA_CFLAGS={extra_cflags}"],
                   check=True, capture_output=True)
    return [str(binary)]


def flagged_stub(tmp_path: Path, flags: int) -> list[str]:
    """A stub that carries the image with header flags set (CRC still right,
    so the flags are the only thing wrong with it)."""
    return image_stub(tmp_path, variant(flags=flags))


def start_with(cmd: list[str]) -> tuple[StubProc, Device]:
    proc = StubProc(cmd=cmd, device_config=ZDMS16_2, dp_config=ZDMS16_2_DP_CONFIG).start()
    device = Device(proc)
    pump(proc)
    take_tx(proc)
    return proc, device


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


# ---- phase 3: the frozen contract (tuya_mcu_ota.h) -----------------------
# zdms16-py32 docs/BOOTLOADER.md section 10: what the MCU's v2 bootloader,
# replaced only over SWD, needs from every later release of this firmware.

OTA_H = ROOT / "src/zigbee/tuya_mcu_ota.h"
APP_FLAG_DIAG, APP_FLAG_CONFIRMS, APP_FLAG_RING_HIGH = 0x04, 0x08, 0x10
APP_BASE = 0x08001000
MODE_NO_APP, MODE_PROBE, MODE_RESCUE, MODE_BUTTON, MODE_LINK = 2, 3, 4, 5, 6


def header_define(name: str) -> int:
    m = re.search(rf"^#define\s+{name}\s+(\S+)", OTA_H.read_text(), re.M)
    assert m, f"{name} is not in tuya_mcu_ota.h"
    return int(m.group(1), 0)


def test_the_contract_and_its_numbers_live_in_the_header():
    """Contract items 1-8 with their numbers in one place, next to the text,
    so that a later change cannot drop one quietly."""
    text = OTA_H.read_text()
    for item in range(1, 9):
        assert re.search(rf"^ \*  {item}\. ", text, re.M), f"contract item {item}"
    assert header_define("UNIDENTIFIED_RESTARTS_MAX") == 3
    assert header_define("IDENT_RETRIES") == 3
    assert header_define("IDENT_LATE_MAX") == 2
    assert header_define("TUYA_MCU_IDENT_ASK_FROM_MAJOR") == 2
    assert header_define("TUYA_MCU_OTA_FIRST_ANSWER_MS") == 1500
    assert header_define("TUYA_MCU_FIRST_CONTACT_MIN_MS") == 2000
    assert header_define("TUYA_MCU_FACTORY_FRAME_MAX") == 29
    assert header_define("TUYA_MCU_FACTORY_BURST_MAX") == 23
    for src in ("tuya_mcu_app.c", "tuya_mcu_ota.c", "tuya_secondary_mcu.c"):
        body = (ROOT / "src/zigbee" / src).read_text()
        for name in ("UNIDENTIFIED_RESTARTS_MAX", "IDENT_RETRIES", "IDENT_LATE_MAX"):
            assert not re.search(rf"^#define\s+{name}\b", body, re.M), f"{name} redefined in {src}"
    app = (ROOT / "src/zigbee/tuya_mcu_app.c").read_text()
    delay = int(re.search(r"#define FIRST_CONTACT_DELAY_MS\s+(\d+)", app).group(1))
    assert delay >= header_define("TUYA_MCU_FIRST_CONTACT_MIN_MS")


def block_request(offset: int, want: int, mode: int | None = None, installed: bytes = b"",
                  boot: int = 2) -> bytes:
    """6 bytes (v1 bootloader), or 16 with a mode (v2): bootloader version,
    mode, the installed header's bytes 8..15 (CRC, version, flags, build)."""
    data = offset.to_bytes(4, "big") + want.to_bytes(2, "big")
    if mode is not None:
        data += bytes([boot, mode]) + (installed or IMAGE[0xC8:0xD0])
    return data


def answers(proc, device, data: bytes, ms: int = 0) -> list[bytes]:
    proc.exec(f"uart_rx {tuya_frame(OTA_BLOCK, data)}")
    return [d for cmd, d in cmds_after(proc, device, ms) if cmd == OTA_BLOCK]


def header_answer(img: bytes = IMAGE) -> bytes:
    size = len(img)
    return ((0xC0).to_bytes(4, "big") + size.to_bytes(4, "big") + img[0xC8:0xCC][::-1]
            + img[0xCC:0xCD] + img[0xC0:0xD0])


# -- offers by build ---------------------------------------------------------

def built(tmp_path_factory, name: str, img: bytes) -> list[str]:
    return image_stub(tmp_path_factory.mktemp(name), img)


IMAGE_300 = variant(flags=APP_FLAG_CONFIRMS, build=300)


@pytest.fixture(scope="module")
def stub_300(tmp_path_factory) -> list[str]:
    """Carries a v2 image: build 300, the carried version byte."""
    return built(tmp_path_factory, "b300", IMAGE_300)


@pytest.mark.parametrize("running, build, offered", [
    (VERSION, 299, True),             # same byte, older build: the build decides
    (VERSION, 256, True),
    (VERSION, 300, False),
    (VERSION, 301, False),
    (VERSION, 0xFFFE, False),         # a SIM bench build: nothing is newer
    (0xBE, 299, True),                # a larger byte does not stop it
])
def test_a_v2_app_is_offered_a_carried_newer_build(stub_300, running, build, offered):
    proc, device = start_with(stub_300)
    try:
        _, frames = contact_as_own(proc, device, running, build=build, boot=2)
        offers = [d for cmd, d in frames if cmd == OTA_NOTIFY]
        assert offers == ([offer(IMAGE_300)] if offered else [])
        assert shown_version(device).endswith(" own")
    finally:
        proc.stop()


def test_an_identity_with_a_build_and_no_bootloader_byte_compares_builds(stub_300):
    proc, device = start_with(stub_300)
    try:
        _, frames = contact_as_own(proc, device, VERSION, build=299)       # 9 bytes
        assert [d for cmd, d in frames if cmd == OTA_NOTIFY] == [offer(IMAGE_300)]
    finally:
        proc.stop()


@pytest.mark.parametrize("running, offered", [(older_version(), True), (VERSION, False)])
def test_a_2_1_x_app_is_offered_a_v2_image_by_the_byte_rule(stub_300, running, offered):
    """Rollout step 3: a module with 2.1.12 (7-byte identity) takes the first
    v2 release by its version byte; the offer has 11 bytes, of which it reads
    9."""
    proc, device = start_with(stub_300)
    try:
        _, frames = contact_as_own(proc, device, running)
        offers = [d for cmd, d in frames if cmd == OTA_NOTIFY]
        assert offers == ([offer(IMAGE_300)] if offered else [])
        if offered:
            assert len(offers[0]) == 11 and offers[0][9:] == (300).to_bytes(2, "big")
    finally:
        proc.stop()


IMAGE_0 = variant(build=0)


@pytest.fixture(scope="module")
def stub_0(tmp_path_factory) -> list[str]:
    """Carries an image without a build, as a 2.1.x one (a rollback)."""
    return built(tmp_path_factory, "b0", IMAGE_0)


@pytest.mark.parametrize("build", [0, 256, 299])
def test_a_carried_image_without_a_build_is_never_offered_to_a_v2_app(stub_0, build):
    """A 2.1.x image (build 0) is served for a rollback, but a v2 app compares
    builds, and 0 is never newer: not even with an older byte."""
    proc, device = start_with(stub_0)
    try:
        _, frames = contact_as_own(proc, device, older_version(), build=build, boot=2)
        assert OTA_NOTIFY not in [cmd for cmd, _ in frames]
        assert answers(proc, device, block_request(0xC0, 16, MODE_PROBE)) == [header_answer(IMAGE_0)]
    finally:
        proc.stop()


def test_a_carried_image_without_a_build_is_offered_to_a_2_1_x_app_by_its_byte(stub_0):
    proc, device = start_with(stub_0)
    try:
        _, frames = contact_as_own(proc, device, older_version())
        assert [d for cmd, d in frames if cmd == OTA_NOTIFY] == [offer(IMAGE_0)]
    finally:
        proc.stop()


def test_the_identity_goes_to_the_link_diagnostics_and_the_display_keeps_its_format(stub_300):
    from test_mcu_diag import counters, log
    proc, device = start_with(stub_300)
    try:
        contact_as_own(proc, device, VERSION, build=301, boot=2)
        c = counters(device)
        assert (c["mcu_build"], c["mcu_boot"]) == (301, 2)
        ident = [e for e in log(device) if e["kind"] == "IDENT"]
        assert [(e["id"], e["value"]) for e in ident] == [(VERSION, (10 << 24) | (2 << 16) | 301)]
        major, minor, patch = VERSION >> 6, (VERSION >> 4) & 3, VERSION & 15
        assert shown_version(device) == f"{major}.{minor}.{patch} own"      # the converter parses it
    finally:
        proc.stop()


def test_a_2_1_x_identity_leaves_build_and_bootloader_at_0():
    from test_mcu_diag import counters
    proc, device = start()
    try:
        contact_as_own(proc, device, VERSION)
        c = counters(device)
        assert (c["mcu_build"], c["mcu_boot"]) == (0, 0)
    finally:
        proc.stop()


# -- what is carried: image_info mirrors the bootloader ----------------------

RESET = int.from_bytes(IMAGE[4:8], "little")
OVER_12K = 12 * 1024 + 128
LAST_ODD = (APP_BASE + len(IMAGE) - 1) | 1 if len(IMAGE) % 2 == 0 else APP_BASE + len(IMAGE) - 2

REFUSED = {
    "diag": variant(flags=APP_FLAG_DIAG),
    "sim": variant(flags=IMAGE_FLAG_SIM),
    "measure": variant(flags=IMAGE_FLAG_MEASURE),
    "build 1": variant(build=1),
    "build 255": variant(build=255, flags=APP_FLAG_CONFIRMS),
    "build 0xFFFE": variant(build=0xFFFE, flags=APP_FLAG_CONFIRMS),
    "build 0xFFFF": variant(build=0xFFFF, flags=APP_FLAG_CONFIRMS),
    "sp past the RAM": variant(sp=0x20000C00),
    "sp in the mailbox": variant(sp=0x20000BF0),
    "sp below the mailbox": variant(sp=0x20000BD0),
    "reset vector even": variant(reset=RESET & ~1),
    "reset vector past the image": variant(reset=(APP_BASE + len(IMAGE)) | 1),
    "reset vector in the bootloader": variant(reset=APP_BASE - 0xF00 + 1),
    "over 12 KB": variant(pad_to=OVER_12K),
    "over 12 KB with RING_HIGH": variant(flags=APP_FLAG_RING_HIGH, pad_to=OVER_12K),
    "crc": variant(crc=0x12345678),
}
SERVED = {
    "build 256": variant(flags=APP_FLAG_CONFIRMS, build=256),
    "build 0xFFFD": variant(flags=APP_FLAG_CONFIRMS, build=0xFFFD),
    "RING_HIGH within 12 KB": variant(flags=APP_FLAG_CONFIRMS | APP_FLAG_RING_HIGH, build=300),
    "12 KB": variant(flags=APP_FLAG_CONFIRMS, build=300, pad_to=12 * 1024),
    "last odd address is the reset vector": variant(reset=LAST_ODD),
}


@pytest.fixture(scope="module")
def variant_stubs(tmp_path_factory) -> dict:
    """One stub per image, built in parallel."""
    from concurrent.futures import ThreadPoolExecutor
    images = {**REFUSED, **SERVED}
    with ThreadPoolExecutor(8) as pool:
        futures = {name: pool.submit(image_stub, tmp_path_factory.mktemp("img"), img)
                   for name, img in images.items()}
        return {name: f.result() for name, f in futures.items()}


@pytest.mark.parametrize("name", list(REFUSED))
def test_an_image_the_bootloader_must_not_take_is_neither_offered_nor_served(variant_stubs, name):
    proc, device = start_with(variant_stubs[name])
    try:
        _, frames = contact_as_own(proc, device, older_version())   # a usable one would be offered
        assert OTA_NOTIFY not in [cmd for cmd, _ in frames]
        assert shown_version(device).endswith(" own")
        for data in (block_request(0xC0, 16), block_request(0xC0, 16, MODE_NO_APP),
                     block_request(0, 64, MODE_RESCUE)):
            assert answers(proc, device, data) == []
    finally:
        proc.stop()


@pytest.mark.parametrize("name", list(SERVED))
def test_an_image_the_bootloader_may_take_is_served_and_offered(variant_stubs, name):
    img = SERVED[name]
    proc, device = start_with(variant_stubs[name])
    try:
        _, frames = contact_as_own(proc, device, older_version())
        assert [d for cmd, d in frames if cmd == OTA_NOTIFY] == [offer(img)]
        assert answers(proc, device, block_request(0xC0, 16, MODE_PROBE)) == [header_answer(img)]
    finally:
        proc.stop()


def test_the_carried_image_is_served():
    """Whatever py32-bump carries (2.1.12 at build 0 today: valid, a unit
    without an app recovers from it)."""
    proc, device = start()
    try:
        assert answers(proc, device, block_request(0xC0, 16, MODE_NO_APP)) == [header_answer()]
    finally:
        proc.stop()


# -- block requests: contract items 1-3 --------------------------------------

@pytest.mark.parametrize("mode", [None, MODE_PROBE, MODE_LINK])
def test_a_header_request_gets_the_16_header_bytes_and_no_first_contact_reset(mode):
    """The probe's request (offset 0xC0, 16 bytes) of a v1 and a v2
    bootloader: the header, and the module's contact state stays (only
    offset 0 means the MCU restarted)."""
    proc, device = start()
    try:
        contact_by_mcu(proc, device)
        assert answers(proc, device, block_request(0xC0, 16, mode)) == [header_answer()]
        assert TUYA_CMD_MCU_VERSION not in mcu_query(proc, device)
    finally:
        proc.stop()


def test_a_header_request_during_the_pause_gets_no_answer():
    proc, device = start()
    try:
        for _ in range(3):
            fail(proc, device)
        for mode in (None, MODE_PROBE, MODE_RESCUE, MODE_LINK):
            assert answers(proc, device, block_request(0xC0, 16, mode)) == []
    finally:
        proc.stop()


def test_any_request_a_bootloader_can_send_gets_a_well_formed_answer():
    """Contract item 2: offsets 0..0xFFFFFFFF, max_len 0..0xFFFF, 6 to 16
    bytes with random tails, one request every 50 ms; every answer is the
    stateless block of contract item 1, and the module keeps running."""
    import random
    rng = random.Random(20261118)
    proc, device = start()
    try:
        for _ in range(500):
            off = rng.choice([0, 0xC0, SIZE - 1, SIZE, SIZE + 1, 0x7FFFFFFF, 0xFFFFFFFF,
                              rng.randrange(SIZE), rng.randrange(1 << 32)])
            want = rng.choice([0, 1, 16, 63, 64, 65, 128, 0xFFFF, rng.randrange(1 << 16)])
            data = (off.to_bytes(4, "big") + want.to_bytes(2, "big")
                    + bytes(rng.randrange(256) for _ in range(rng.randrange(11))))
            got = answers(proc, device, data, ms=50)
            n = min(want, 64, max(0, SIZE - off))
            assert got == [off.to_bytes(4, "big") + SIZE.to_bytes(4, "big")
                           + CRC.to_bytes(4, "big") + bytes([VERSION]) + IMAGE[off:off + n]]
        assert proc.is_running()
    finally:
        proc.stop()


def test_the_first_header_answer_and_the_uart_start_are_timed():
    """Contract item 3: the UART is up at the end of the link's init, a
    header request is answered from the first poll on, and both times go to
    the link diagnostics (kept by a clear: they happen once per start)."""
    from test_mcu_diag import ATTR_CONTROL, BASIC, counters
    proc, device = start()
    try:
        c = counters(device)
        assert c["uart_up_ms"] >= 1 and c["first_answer_ms"] == 0
        device.step_time(50)
        assert answers(proc, device, block_request(0xC0, 16, MODE_PROBE)) == [header_answer()]
        c = counters(device)
        assert c["uart_up_ms"] <= c["first_answer_ms"] == 50
        assert c["first_answer_ms"] <= header_define("TUYA_MCU_OTA_FIRST_ANSWER_MS")
        device.step_time(400)
        answers(proc, device, block_request(0xC0, 16, MODE_PROBE))
        device.write_zigbee_attr(1, BASIC, ATTR_CONTROL, 1)
        assert counters(device)["first_answer_ms"] == 50
    finally:
        proc.stop()


def test_requests_tell_the_bootloader_and_why_it_asks():
    """Change 5: diagnostics only. Each run of requests is logged once; a
    rescue and a link probe are counted."""
    from test_mcu_diag import counters, log
    proc, device = start()
    try:
        installed = IMAGE_300[0xC8:0xD0]                    # the installed build 300
        answers(proc, device, block_request(0xC0, 16))      # a v1 bootloader's 6 bytes
        c = counters(device)
        assert (c["request_boot_mode"], c["request_build"]) == (0x0100, 0)
        for _ in range(20):                                 # a counter rescue window
            answers(proc, device, block_request(0xC0, 16, MODE_RESCUE, installed), ms=500)
        c = counters(device)
        assert (c["request_boot_mode"], c["request_build"], c["rescues"]) == (0x0204, 300, 1)
        answers(proc, device, block_request(0xC0, 16, MODE_BUTTON, installed), ms=500)
        answers(proc, device, block_request(0xC0, 16, MODE_LINK, installed), ms=500)
        device.step_time(10_500)                            # the next link probe, 4 h later
        answers(proc, device, block_request(0xC0, 16, MODE_LINK, installed), ms=500)
        c = counters(device)
        assert (c["rescues"], c["link_probes"]) == (2, 2)
        runs = [(e["id"], e["value"]) for e in log(device) if e["kind"] == "BOOT_REQUEST"]
        v2 = (16 << 24) | (2 << 16) | 300
        assert runs == [(0, (6 << 24) | (1 << 16)), (MODE_RESCUE, v2), (MODE_BUTTON, v2),
                        (MODE_LINK, v2), (MODE_LINK, v2)]
    finally:
        proc.stop()


def test_a_v2_result_is_taken_like_a_v1_one():
    proc, device = start()
    try:
        contact_by_mcu(proc, device)
        proc.exec(f"uart_rx {tuya_frame(OTA_RESULT, bytes([0, VERSION, 1, 44]))}")
        cmds_after(proc, device)
        assert TUYA_CMD_MCU_VERSION in mcu_query(proc, device)       # it restarts now
        assert ota_state(device) == (0, 0)
    finally:
        proc.stop()


# -- identity by class of MCU: contract item 4 -------------------------------

def version_then_ident(proc, device, packed: int = VERSION) -> list[int]:
    """The version query is answered; returns what the module sends next."""
    proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_MCU_VERSION, bytes([packed]))}")
    cmds = []
    for _ in range(4):
        cmds += [cmd for cmd, _ in cmds_after(proc, device, 100)]
    return cmds


@pytest.mark.parametrize("sign", ["seq-0 query", "offset-0 request", "OK result"])
def test_an_identified_mcu_is_asked_again_after_every_restart_sign(sign):
    """Class (a): an MCU identified in this run is asked who it is again
    after a seq-0 query, a block request for offset 0 and an OK result."""
    proc, device = start()
    try:
        contact_as_own(proc, device, VERSION)
        if sign == "seq-0 query":
            cmds = mcu_query_seq(proc, device, 0)
        else:
            if sign == "offset-0 request":
                answers(proc, device, block_request(0, 64, MODE_PROBE))
            else:
                proc.exec(f"uart_rx {tuya_frame(OTA_RESULT, bytes([0, VERSION]))}")
                cmds_after(proc, device)
            cmds = mcu_query_seq(proc, device, 7)       # the new app's query
        assert TUYA_CMD_MCU_VERSION in cmds
        assert OTA_IDENT in version_then_ident(proc, device)
    finally:
        proc.stop()


def mcu_query_seq(proc, device, seq: int) -> list[int]:
    proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_QUERY_NETWORK, seq=seq)}")
    frames = []
    for _ in range(4):
        frames += cmds_after(proc, device, 200)
    return [cmd for cmd, _ in frames]


def unidentified_run(version: int, cmd=None, **kw):
    """An MCU that never answers 0xA3 and sends a seq-0 network query every
    30 s for 10 minutes (a stock MCU that numbers every query 0)."""
    from tuya_mcu import StockMcu
    proc = StubProc(cmd=cmd or ["./build/stub/stub_device"], **kw).start()
    try:
        device = Device(proc)
        pump(proc)
        mcu = StockMcu(proc, device, version=version, asked=frozenset({OTA_IDENT}))
        mcu.run(5000)
        for _ in range(20):
            mcu.send(TUYA_CMD_QUERY_NETWORK, seq=0)
            mcu.run(30_000, step_ms=50)
        return mcu
    finally:
        proc.stop()


def test_an_unidentified_2x_mcu_gets_bounded_questions_and_restarts():
    """Class (b): at most 3 seq-0 queries count as restarts per run (plus the
    timer's contact: 4 contacts), 4 questions per version answer and 2 late
    ones, never an offer; after that only the replies to its queries."""
    mcu = unidentified_run(0x80, device_config=ZDMS16_2, dp_config=ZDMS16_2_DP_CONFIG)
    cmds = mcu.cmds()
    assert cmds.count(TUYA_CMD_MCU_VERSION) <= 4 and cmds.count(TUYA_CMD_QUERY_ALL) <= 4
    assert cmds.count(OTA_IDENT) <= 18
    assert OTA_NOTIFY not in cmds
    assert mcu.faults == []
    last = max(t for t, cmd, _ in mcu.trace if cmd != TUYA_CMD_QUERY_NETWORK)
    assert last < 5000 + 6 * 30_000                     # the 3rd restart and its late questions
    tail = [cmd for t, cmd, _ in mcu.trace if t > last]
    assert tail and set(tail) == {TUYA_CMD_QUERY_NETWORK}


def test_a_stock_1x_mcu_numbering_every_query_0_is_never_asked_or_restarted():
    """Class (c): 0 frames with 0xA0..0xA3, and no restart handling beyond
    phase 1's: the timer's contact, repeated once at its first query."""
    mcu = unidentified_run(0x47, device_config=ZDMS16_2, dp_config=ZDMS16_2_DP_CONFIG)
    assert not [cmd for cmd in mcu.cmds() if 0xA0 <= cmd <= 0xA3]
    assert mcu.cmds().count(TUYA_CMD_MCU_VERSION) == 2
    assert mcu.faults == []


TPZ2 = "hktk6hze;TS0601-TPZ2;Y9600;ST18;ST19;RT181E;RT191F;IT13400;M;"


@pytest.fixture(scope="module")
def stub_without_image(tmp_path_factory) -> list[str]:
    """A board without Open Dimmer support: no SECONDARY_MCU_IMAGE."""
    return image_stub(tmp_path_factory.mktemp("noimg"), IMAGE,
                      extra_cflags="-USECONDARY_MCU_IMAGE")


def test_a_board_without_open_dimmer_support_never_asks_who_the_mcu_is(stub_without_image):
    """Rule 6: the factory MCU of a touch switch that says 2.x and numbers
    every query 0 gets no 0xA3 and no treated restart: first contact only by
    the timer and once more at its first query."""
    mcu = unidentified_run(0x80, cmd=stub_without_image, device_config=TPZ2)
    assert not [cmd for cmd in mcu.cmds() if 0xA0 <= cmd <= 0xA3]
    assert mcu.cmds().count(TUYA_CMD_MCU_VERSION) == 2
    assert mcu.faults == []


# -- heard: contract item 8 ---------------------------------------------------

def test_an_identified_mcu_gets_a_reply_to_every_network_query():
    """Also with other traffic in the same burst, with its bootloader's
    requests (a link probe among them) and during the failure pause."""
    proc, device = start()
    try:
        contact_as_own(proc, device, VERSION, build=300, boot=2)
        take_tx(proc)
        replies = []
        for k in range(30):
            seq = 100 + k
            burst = [tuya_frame(TUYA_CMD_QUERY_NETWORK, seq=seq)]
            if k % 3 == 0:
                burst.insert(0, tuya_frame(0x06, bytes([2, 2, 0, 4]) + (500 + k).to_bytes(4, "big")))
            if k % 3 == 1:
                burst.append(tuya_frame(OTA_BLOCK, block_request(0xC0, 16, MODE_LINK)))
            if k == 10:
                for _ in range(3):
                    burst.append(tuya_frame(OTA_RESULT, bytes([1, VERSION])))
            proc.exec("uart_rx " + " ".join(burst))
            device.step_time(100)
            pump(proc)
            for frame_seq, cmd in seqs(take_tx(proc)):
                if cmd == TUYA_CMD_QUERY_NETWORK:
                    replies.append(frame_seq)
        assert replies == [100 + k for k in range(30)]
        assert ota_state(device)[1] > 0                     # the pause ran meanwhile
    finally:
        proc.stop()


def seqs(raw: bytes) -> list[tuple[int, int]]:
    """(seq, cmd) of every frame in a TX capture."""
    out, i = [], 0
    for cmd, data in parse_frames(raw):
        out.append((int.from_bytes(raw[i + 3:i + 5], "big"), cmd))
        i += 9 + len(data)
    return out


def test_the_health_dps_of_a_v2_app_only_reach_the_link_log():
    """DPs 130..137 (DP 136: boot sector CRC, 137: boot word; v2 app health
    report) are logged and taken by nothing else: no frame to the MCU, no
    attribute, and the link goes on."""
    from test_mcu_diag import log
    proc, device = start()
    try:
        contact_as_own(proc, device, VERSION, build=300, boot=2)
        take_tx(proc)
        units = b"".join(bytes([dp, 2, 0, 4]) + (0xB2001001 + dp).to_bytes(4, "big")
                         for dp in range(130, 138))
        proc.exec(f"uart_rx {tuya_frame(0x06, units)}")
        assert cmds_after(proc, device, 200) == []
        logged = {e["id"]: e["value"] for e in log(device) if e["kind"] == "RX_DP"}
        assert {dp: logged.get(dp) for dp in (136, 137)} == {136: 0xB2001001 + 136,
                                                             137: 0xB2001001 + 137}
        assert TUYA_CMD_QUERY_NETWORK in mcu_query(proc, device)       # still answered
    finally:
        proc.stop()
