"""The factory Zigbee firmware's NV, read after a conversion to keep the network (W4c,
src/zigbee/stock_nv.c; the Telink side in src/telink/hal/zigbee_network.c): against the
factory dump of a _TZE284_jtbgusdc module (bin/_factory/telink), and edited copies of it."""
import struct
import zlib
from pathlib import Path

import pytest

from client import StubProc
from conftest import Device
from test_dimmer_mcu import ZDMS16_2

DUMP = Path(__file__).resolve().parent.parent / "bin" / "_factory" / "telink" / "_TZE284_jtbgusdc.bin"
ZB_INFO = (0, 1, 192)       # module, item, payload size (zb_info_t)
APS_SSIB = (2, 4, 81)       # ss_info_base_t
SS_KEY_PAIR = (7, 8, 36)    # ss_dev_pair_set_t
ZB_INFO_ITEM = 0xD8200      # the dump's one copy: its header, then the payload


@pytest.fixture(scope="module")
def stub():
    with StubProc() as proc:
        yield proc


def stock_nv(proc: StubProc, path: Path, module: int, item: int, size: int) -> bytes | None:
    res = proc.exec(f"stock_nv {path} {module} {item} {size}")
    assert res.ok, res
    return bytes.fromhex(res.payload["data"]) if res.payload["found"] == "1" else None


def item_header(payload: bytes, item: int, kind: int = 0x5A) -> bytes:
    if kind == 0x5A:                       # a byte sum
        check = sum(payload) & 0xFFFFFFFF
    else:                                  # CRC-32 of the payload and size/id/kind, low half
        tail = struct.pack("<HBB", len(payload), item, kind)
        check = ~zlib.crc32(payload + tail) & 0xFFFF
    return struct.pack("<IHBB", check, len(payload), item, kind)


def test_the_network_items_of_the_factory_dump(stub):
    d = DUMP.read_bytes()
    for module, item, size in (ZB_INFO, APS_SSIB, SS_KEY_PAIR):
        data = stock_nv(stub, DUMP, module, item, size)
        assert data is not None and len(data) == size, (module, item)
    assert stock_nv(stub, DUMP, *ZB_INFO) == d[ZB_INFO_ITEM + 8:ZB_INFO_ITEM + 8 + 192]


def test_another_size_or_item_is_not_taken(stub):
    assert stock_nv(stub, DUMP, 0, 1, 191) is None       # not the structure this build has
    assert stock_nv(stub, DUMP, 0, 2, 192) is None
    assert stock_nv(stub, DUMP, 1, 1, 192) is None       # another module


def test_a_damaged_item_is_not_taken(stub, tmp_path):
    d = bytearray(DUMP.read_bytes())
    d[ZB_INFO_ITEM + 8 + 20] ^= 0x01
    path = tmp_path / "damaged.bin"
    path.write_bytes(bytes(d))
    assert stock_nv(stub, path, *ZB_INFO) is None


def test_a_blank_or_short_flash_has_none(stub, tmp_path):
    blank = tmp_path / "blank.bin"
    blank.write_bytes(b"\xFF" * 0x100000)
    short = tmp_path / "short.bin"                       # a 512 KB chip: nothing up there
    short.write_bytes(DUMP.read_bytes()[:0x80000])
    for path in (blank, short):
        assert stock_nv(stub, path, *ZB_INFO) is None


@pytest.mark.parametrize("kind", [0x5A, 0x7A])
def test_the_newest_intact_copy_wins(stub, tmp_path, kind):
    """A write appends a copy and its index entry; the last intact one is the newest (a
    byte-sum item as the factory firmware writes them, or a CRC one as later SDKs do)."""
    d = bytearray(DUMP.read_bytes())
    old = d[ZB_INFO_ITEM + 8:ZB_INFO_ITEM + 200]
    new = bytes([old[0] ^ 0xFF]) + old[1:]
    at = ZB_INFO_ITEM + 200                              # after the first copy, 4-aligned
    d[at:at + 200] = item_header(new, 1, kind) + new
    d[0xD800C:0xD8014] = struct.pack("<IHBB", at, 200, 1, 0x5A)   # index entry 1
    path = tmp_path / "two.bin"
    path.write_bytes(bytes(d))
    assert stock_nv(stub, path, *ZB_INFO) == new
    d[at + 8 + 5] ^= 0x01                                # the newer one damaged: the older
    path.write_bytes(bytes(d))
    assert stock_nv(stub, path, *ZB_INFO) == old


def test_the_start_up_look_is_an_attribute_of_the_zdms16_2():
    """Basic 0xff55 (stock_nv_diag_t without its length byte): format 1; the stub has no factory
    NV and no Telink start-up, so the rest is zero (own network)."""
    with StubProc(device_config=ZDMS16_2) as proc:
        raw = bytes.fromhex(Device(proc).read_zigbee_attr(1, 0x0000, 0xFF55))
        assert len(raw) == 19 and raw[0] == 1 and raw[1:] == bytes(18)
