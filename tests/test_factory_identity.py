"""The module's factory identity, kept over the conversion (src/zigbee/factory_identity.c):
the factory Zigbee firmware's manufacturer name from its NV (module 10, item 3) and the factory
MCU's version as the module first heard it, in this firmware's NV and as Basic 0xff59. The stub
reads the factory NV from STUB_FACTORY_FLASH (a module's 1 MB flash dump); its NV directory
lasts over a restart within a test."""
import struct
from pathlib import Path

import pytest

from client import StubProc
from conftest import Device
from test_dimmer_mcu import ZDMS16_2
from tuya_mcu import pump, tuya_frame

DUMP = Path(__file__).resolve().parent.parent / "bin" / "_factory" / "telink" / "_TZE284_jtbgusdc.bin"
ATTR = 0xFF59
NAMES_INDEX = 0xEC00C       # module 10's index entries for item 3, 8 bytes each (6 copies)


def identity(env_flash: Path | None, monkeypatch, mcu_versions: tuple[int, ...] = ()) -> str:
    """Start the stub on the ZDMS16-2 (the factory flash from env_flash, or none), let the MCU
    tell the given versions (0x0B), and read Basic 0xff59."""
    if env_flash is None:
        monkeypatch.delenv("STUB_FACTORY_FLASH", raising=False)
    else:
        monkeypatch.setenv("STUB_FACTORY_FLASH", str(env_flash))
    with StubProc(device_config=ZDMS16_2) as proc:
        device = Device(proc)
        for seq, v in enumerate(mcu_versions, start=1):
            assert proc.exec(f"uart_rx {tuya_frame(0x0B, bytes([v]), seq)}").ok
            pump(proc, 2)
        return device.read_zigbee_attr(1, 0x0000, ATTR)


def name_copies(d: bytes) -> list[int]:
    """The absolute offsets of the name records (the index's order: oldest first)."""
    out = []
    for k in range(6):
        off, size, item, used = struct.unpack_from("<IHBB", d, NAMES_INDEX + 8 * k)
        assert (size, item, used) == (41, 3, 0x5A)
        out.append(off)
    return out


def test_nothing_known_without_a_factory_nv(monkeypatch):
    assert identity(None, monkeypatch) == ""


def test_the_factory_name_is_taken_and_kept(monkeypatch):
    """The newest copy (_TZE284_jtbgusdc; the first is the factory firmware's built-in default
    _TZE284_sbntsuwb); kept in this firmware's NV, so a start-up without the factory NV still
    tells it."""
    assert identity(DUMP, monkeypatch) == "_TZE284_jtbgusdc"
    assert identity(None, monkeypatch) == "_TZE284_jtbgusdc"


def test_the_newest_intact_copy_counts(monkeypatch, tmp_path):
    """Damaged newer copies (their byte sum no longer matches): an older one; with only the
    first intact, the factory firmware's default name."""
    d = bytearray(DUMP.read_bytes())
    copies = name_copies(bytes(d))
    for at in copies[1:]:
        d[at + 8 + 5] ^= 0x01
    path = tmp_path / "damaged.bin"
    path.write_bytes(bytes(d))
    assert identity(path, monkeypatch) == "_TZE284_sbntsuwb"


def test_an_unprintable_name_is_not_taken(monkeypatch, tmp_path):
    d = bytearray(DUMP.read_bytes())
    at = name_copies(bytes(d))[-1]
    d[at + 8 + 3] = 0x01                               # a control character in the name
    check = sum(d[at + 8:at + 8 + 33]) & 0xFFFFFFFF    # its byte sum fixed: an intact copy
    struct.pack_into("<I", d, at, check)
    path = tmp_path / "control.bin"
    path.write_bytes(bytes(d))
    assert identity(path, monkeypatch) == ""


def test_the_factory_mcu_version_is_kept(monkeypatch):
    """A factory MCU (below 2.x) as first heard; Open Dimmer (2.x) later changes nothing, and
    it is kept over a restart."""
    assert identity(DUMP, monkeypatch, (0x47,)) == "_TZE284_jtbgusdc, MCU 1.0.7"
    assert identity(None, monkeypatch, (0xA8,)) == "_TZE284_jtbgusdc, MCU 1.0.7"


def test_the_first_factory_version_stays(monkeypatch):
    assert identity(None, monkeypatch, (0x4D, 0x47)) == "MCU 1.0.13"


def test_open_dimmer_alone_is_no_factory_version(monkeypatch):
    assert identity(DUMP, monkeypatch, (0xA8,)) == "_TZE284_jtbgusdc"


@pytest.mark.parametrize("packed, text", [(0x40, "MCU 1.0.0"), (0x7F, "MCU 1.3.15"),
                                          (0x0A, "MCU 0.0.10")])
def test_version_text(monkeypatch, packed, text):
    assert identity(None, monkeypatch, (packed,)) == text
