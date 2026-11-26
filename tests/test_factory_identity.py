"""The module's factory identity, kept over the conversion (src/zigbee/factory_identity.c):
the factory Zigbee firmware's manufacturer name from its NV (module 10, item 3) and the factory
MCU's version as the module first heard it, in this firmware's NV and as Basic 0xff59. The stub
reads the factory NV from STUB_FACTORY_FLASH (a module's 1 MB flash dump); its NV directory
lasts over a restart within a test."""
import shutil
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


# ---- the TS110E (HK token): its factory firmware keeps the name under another item id ---------

FACTORY = DUMP.parent
TS130F = FACTORY / "MODULE_GIRIER_TS130F_1GANG.bin"   # _TZ3210_dwytrmda in item 13
OOC8 = FACTORY / "_TZ3000_ooc8illt.bin"                # a TS110E sibling: item 14
NAME_RECORD = 33


def hk_identity(env_flash: Path | None, monkeypatch, fresh: bool = False) -> str:
    """Start the stub on the TS110E (the virtual MCU answers the version query with 1.0.0) and
    read Basic 0xff59 once that exchange is over; fresh: this firmware's NV emptied first (a
    name found is kept there)."""
    from test_hk_dimmer import TS110E, TS110E_DP_CONFIG
    if fresh:
        shutil.rmtree("./stub_nvm_data", ignore_errors=True)
    if env_flash is None:
        monkeypatch.delenv("STUB_FACTORY_FLASH", raising=False)
    else:
        monkeypatch.setenv("STUB_FACTORY_FLASH", str(env_flash))
    with StubProc(device_config=TS110E, dp_config=TS110E_DP_CONFIG) as proc:
        device = Device(proc)
        for _ in range(30):
            device.step_time(100)
            pump(proc, 2)
        assert device.read_zigbee_attr(1, 0x0000, 0xFF12) == "1.0.0"   # the virtual MCU's
        return device.read_zigbee_attr(1, 0x0000, ATTR)


def name_items(d: bytes) -> list[tuple[int, int]]:
    """(item id, absolute offset) of module 10's 41-byte items (8-byte header + the record)."""
    out = []
    for s in range(2):
        sect = 0xEC000 + s * 0x1000
        flag, mod = struct.unpack_from("<HB", d, sect)
        if flag not in (0x5A5A, 0x7A7A) or mod != 10:
            continue
        for i in range(127):
            off, size, item, state = struct.unpack_from("<IHBB", d, sect + 4 + 8 * i)
            if off == 0xFFFFFFFF:
                break
            if size == 8 + NAME_RECORD and state in (0x5A, 0x7A):
                out.append((item, off))
    return out


def with_record(src: Path, tmp_path: Path, text: bytes) -> Path:
    """The dump with its name record replaced by text (its byte sum fixed: an intact copy)."""
    d = bytearray(src.read_bytes())
    (_, at), = name_items(bytes(d))
    assert d[at + 7] == 0x5A                            # a byte-sum checked item
    rec = bytes([len(text)]) + text + bytes(NAME_RECORD - 1 - len(text))
    d[at + 8:at + 8 + NAME_RECORD] = rec
    struct.pack_into("<I", d, at, sum(rec) & 0xFFFFFFFF)
    path = tmp_path / f"changed-{src.name}"
    path.write_bytes(bytes(d))
    return path


def test_hk_the_name_is_found_under_any_item_id(monkeypatch):
    """No TS110E dump is in the repo: firmwares of other boards keep the name under item 13 or
    14 (and the TS0601 under 3); the TS110E takes the one item of the record's size."""
    assert [i for i, _ in name_items(TS130F.read_bytes())] == [13]
    assert [i for i, _ in name_items(OOC8.read_bytes())] == [14]
    assert hk_identity(TS130F, monkeypatch) == "_TZ3210_dwytrmda"


def test_hk_the_name_of_item_14_and_of_item_3(monkeypatch):
    assert hk_identity(OOC8, monkeypatch) == "_TZ3000_ooc8illt"
    assert hk_identity(DUMP, monkeypatch, fresh=True) == "_TZE284_jtbgusdc"


def test_hk_the_name_is_kept_and_no_mcu_version_is_added(monkeypatch):
    """Kept in this firmware's NV; the virtual MCU's 1.0.0 is no factory MCU's version (the
    TS110E's factory MCU never answers)."""
    assert hk_identity(TS130F, monkeypatch) == "_TZ3210_dwytrmda"
    assert hk_identity(None, monkeypatch) == "_TZ3210_dwytrmda"


def test_hk_an_item_of_that_size_without_a_tuya_name_is_not_taken(monkeypatch, tmp_path):
    """Any 33-byte item could be something else (a product key, a key of 32 characters): only a
    Tuya manufacturer name (_T + 4 or 5 of A-Z 0-9 + _ + 8 of a-z 0-9) is taken."""
    for text in (b"zhzg_double_morerst_dimmer", b"A" * 32, b"_TZ3210_DWYTRMDA", b"_TZ3210_dwytrmd",
                 b"_tz3210_dwytrmda", b"_TZ321_0dwytrmda"):
        assert hk_identity(with_record(TS130F, tmp_path, text), monkeypatch, fresh=True) == "", text


def test_hk_the_prefix_has_4_or_5_characters(monkeypatch, tmp_path):
    for text, taken in ((b"_TZ321_abcd1234", True), (b"_TYZB01_abcd1234", True),
                        (b"_TZ32_abcd1234", False), (b"_TYZB012_abcd1234", False)):
        got = hk_identity(with_record(TS130F, tmp_path, text), monkeypatch, fresh=True)
        assert got == (text.decode() if taken else ""), text


def test_hk_nothing_known_without_a_factory_nv(monkeypatch):
    assert hk_identity(None, monkeypatch) == ""
