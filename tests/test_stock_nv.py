"""The factory Zigbee firmware's NV, read after a conversion to keep the network (W4c,
src/zigbee/stock_nv.c; the Telink side in src/telink/hal/zigbee_network.c): against the
factory dump of a _TZE284_jtbgusdc module (bin/_factory/telink), and edited copies of it."""
import re
import struct
import zlib
from pathlib import Path

import pytest

from client import StubProc
from conftest import Device
from stock_nv_take_host import (IMPLAUSIBLE, LOG_BOOT, LOG_JOIN, LOG_LEAVE, LOG_START,
                                MARKER_ADDR, MARKER_MAGIC, NETWORK_C, NWK_ADDR, OWN_NETWORK, PAN_ID,
                                SS_ACTIVE_SEQ, SS_BITS, SS_BYTES, SS_MATERIAL, SS_PRECONF,
                                SS_TC_ADDRESS, SS_TCLK_TYPE, STALE, TAKEN, TAKEN_BEFORE,
                                build_take, factory_network, factory_pair, factory_ss,
                                function_text, log_records, marker_head)
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
    """Basic 0xff55 (stock_nv_diag_t without its length byte): format 2; the stub has no factory
    NV and no Telink start-up, so the rest is zero (own network). The debug build's log pages
    (0xff56 written, 0xff57 read) are empty there: no flash to read."""
    with StubProc(device_config=ZDMS16_2) as proc:
        device = Device(proc)
        raw = bytes.fromhex(device.read_zigbee_attr(1, 0x0000, 0xFF55))
        assert len(raw) == 19 and raw[0] == 2 and raw[1:] == bytes(18)
        device.write_zigbee_attr(1, 0x0000, 0xFF56, 2)
        page = bytes.fromhex(device.read_zigbee_attr(1, 0x0000, 0xFF57))
        assert page == bytes([2, 0, 0])            # page 2, no records


# --- the take's check and mapping of the security record (stock_nv_take_ss) ------------------

TAKE, PAIR, GLOBAL_TCLK = 0x01, 0x02, 0x04
OURS_SLOT_1 = 0x5D          # this SDK's byte 0x3E: level 5, "secure all", slot 1, the GLK flag 1


def take_ss(proc: StubProc, ss: bytes, pair: bytes | None = None) -> tuple[int, bytes]:
    res = proc.exec(f"stock_nv_take_ss {ss.hex()}" + (f" {pair.hex()}" if pair is not None else ""))
    assert res.ok, res
    return int(res.payload["r"]), bytes.fromhex(res.payload["ss"])


def ours(byte: int) -> tuple[int, int, int, int]:
    """This SDK's byte 0x3E: (level, secure all, active slot, global link key flag)."""
    return byte & 7, byte >> 3 & 1, byte >> 4 & 3, byte >> 6


@pytest.mark.parametrize("factory, slot", [(0x35, 1), (0x15, 0)])
def test_the_factory_byte_0x3e_in_this_sdks_bit_order(stub, factory, slot):
    """The factory firmware has the level in bits 0-3, "secure all" in bit 4 and the slot in bits
    5-6; this SDK 0-2, 3 and 4-5. The take read the slot from this SDK's bits 4-5 and kept bit
    4 (`&= 1`): the factory "secure all" bit, so always slot 1, wrong for a key in slot 0."""
    r, out = take_ss(stub, bytes(factory_ss(factory, slot=slot)), factory_pair())
    assert r == TAKE | PAIR
    assert ours(out[SS_BITS]) == (5, 1, slot, 1)
    if slot == 1:
        assert out[SS_BITS] == OURS_SLOT_1         # what a fresh join of this firmware saves


def test_only_what_is_the_factory_builds_own_changes(stub):
    """The keys, their numbers, the trust centre, its policy, the counters and the pair count
    are the network's: as they were. The pre-configured key type is the factory build's: none.
    (The pointers are the caller's; the stack sets keyPairSetNew at every load.)"""
    ss = factory_ss()
    r, out = take_ss(stub, bytes(ss), factory_pair())
    assert out[SS_PRECONF] == 0
    assert {i for i in range(SS_BYTES) if out[i] != ss[i]} == {SS_BITS, SS_PRECONF}


@pytest.mark.parametrize("bits, key_in, taken", [
    (0x35, 1, True), (0x15, 0, True),       # the factory slot holds it
    (0x35, 0, False), (0x15, 1, False),     # the other one does: an inconsistent record
    (0x55, 1, False), (0x75, 1, False),     # slots 2, 3: none such
])
def test_the_active_slot_must_hold_the_active_key(stub, bits, key_in, taken):
    """The stack secures its own NWK commands only with a key in the active slot: a record whose
    factory slot holds no key is not taken (the module joins again) rather than repaired."""
    r, out = take_ss(stub, bytes(factory_ss(bits, slot=key_in)), factory_pair())
    assert bool(r) == taken
    if taken:
        assert ours(out[SS_BITS])[2] == key_in


def test_the_active_key_has_the_active_number(stub):
    ss = factory_ss(0x35, slot=1)
    ss[SS_ACTIVE_SEQ] = 1                      # a key switch the slot's number does not follow
    assert take_ss(stub, bytes(ss), factory_pair())[0] == 0
    ss[SS_MATERIAL + 18 + 16] = 1              # its number: 1 as well
    assert take_ss(stub, bytes(ss), factory_pair())[0] == TAKE | PAIR


@pytest.mark.parametrize("why", ["no key", "another number", "level 0", "level 4", "this sdk's",
                                 "tc zero", "tc distributed"])
def test_a_record_that_does_not_check_out_is_not_taken(stub, why):
    """Refused (STOCK_NV_IMPLAUSIBLE: the module comes up factory new and joins again): no
    network key with the active number, a level other than Zigbee 3.0's 5 (in the factory bits:
    a record in another layout, e.g. this SDK's 0x5D, reads as 13), no trust centre."""
    ss = factory_ss()
    if why == "no key":
        ss[SS_MATERIAL + 18:SS_MATERIAL + 34] = bytes(16)
    elif why == "another number":
        ss[SS_ACTIVE_SEQ] = 3
    elif why == "level 0":
        ss[SS_BITS] = 0x30
    elif why == "level 4":
        ss[SS_BITS] = 0x34
    elif why == "this sdk's":
        ss[SS_BITS] = OURS_SLOT_1
    elif why == "tc zero":
        ss[SS_TC_ADDRESS:SS_TC_ADDRESS + 8] = bytes(8)
    else:
        ss[SS_TC_ADDRESS:SS_TC_ADDRESS + 8] = b"\xFF" * 8
    r, out = take_ss(stub, bytes(ss), factory_pair())
    assert r == 0


@pytest.mark.parametrize("tclk_type, pair, r, out_type", [
    (0, factory_pair(), TAKE | PAIR, 0),                             # unique, backed: as it was
    (0, None, TAKE | GLOBAL_TCLK, 1),                                # no pair
    (0, factory_pair(address=bytes(range(8))), TAKE | GLOBAL_TCLK, 1),   # another device's
    (0, factory_pair(key_attr=0), TAKE | GLOBAL_TCLK, 1),            # provisional
    (0, factory_pair(key_attr=1), TAKE | PAIR, 0),                   # unverified: the stack uses it
    (0, factory_pair(key_attr=3), TAKE | GLOBAL_TCLK, 1),
    (0, factory_pair(key_type=1), TAKE | GLOBAL_TCLK, 1),            # a global-type entry
    (1, factory_pair(), TAKE | PAIR, 1),                             # global, the pair too
    (1, None, TAKE, 1),                                              # global: as it was
    (7, factory_pair(), TAKE | PAIR | GLOBAL_TCLK, 1),               # no such type: none sent
])
def test_the_tc_link_key_type_the_pair_backs_up(stub, tclk_type, pair, r, out_type):
    """Unique (0) sends a joining child's Update Device only APS-encrypted with this trust
    centre's pair; without one the stack falls back to the global key, the trust centre cannot
    decrypt it and the join through this router fails without a word. Global (1) sends it also
    with the network key alone. The pair goes only if it is this trust centre's unique key."""
    got_r, out = take_ss(stub, bytes(factory_ss(tclk_type=tclk_type)), pair)
    assert (got_r, out[SS_TCLK_TYPE]) == (r, out_type)


# --- debug builds: not over a network of its own since (stock_nv_log_scan) ------------------

def marker_sector(head_result: int, recs: list[tuple[int, int, int, int]],
                  full: bool = False) -> bytes:
    """A 1 MB flash, blank but the marker sector: the head (magic, stock_nv_diag_t with its
    result), then the records (boot, kind, a, b, ms)."""
    d = bytearray(b"\xFF" * 0x100000)
    head = struct.pack("<I", MARKER_MAGIC) + bytes([20, 2, head_result]) + bytes(17)
    d[MARKER_ADDR:MARKER_ADDR + len(head)] = head
    at = MARKER_ADDR + LOG_START
    if full:
        recs = [(1, 6, 1, 0)] * ((0x1000 - LOG_START) // 8 - len(recs)) + recs
    for boot, kind, a, b in recs:
        d[at:at + 8] = bytes([boot, kind, a, b]) + struct.pack("<I", 100)
        at += 8
    return bytes(d)


def log_scan(proc: StubProc, tmp_path: Path, image: bytes) -> dict:
    path = tmp_path / "marker.bin"
    path.write_bytes(image)
    res = proc.exec(f"stock_nv_log {path}")
    assert res.ok, res
    return {k: int(v) for k, v in res.payload.items()}


WIPE, WIPE_FLAG, WIPE_FORCED, WIPE_FLAG_FORCED = 9, 10, 11, 12
TAKEN_AT_BOOT = (1, LOG_BOOT, TAKEN, 1)


@pytest.mark.parametrize("head, recs, stale", [
    (TAKEN, [TAKEN_AT_BOOT], 0),                                          # taken, nothing since
    (TAKEN, [TAKEN_AT_BOOT, (1, LOG_JOIN, 1, 0)], 1),                     # joined by steering
    (TAKEN, [TAKEN_AT_BOOT, (2, LOG_JOIN, 2, 0)], 1),                     # on another network at a start-up
    (TAKEN, [TAKEN_AT_BOOT, (1, LOG_JOIN, 1, 0), (1, LOG_LEAVE, WIPE, 0)], 1),
    (TAKEN, [TAKEN_AT_BOOT, (1, LOG_JOIN, 1, 0), (1, LOG_LEAVE, WIPE_FLAG, 0)], 1),
    (TAKEN, [TAKEN_AT_BOOT, (1, LOG_JOIN, 1, 0), (1, LOG_LEAVE, WIPE_FORCED, 0)], 0),
    (TAKEN, [TAKEN_AT_BOOT, (1, LOG_JOIN, 1, 0), (1, LOG_LEAVE, WIPE_FLAG_FORCED, 0)], 0),
    (TAKEN, [(1, LOG_JOIN, 1, 0), (1, LOG_LEAVE, WIPE_FORCED, 0), (2, LOG_BOOT, TAKEN, 1),
             (2, LOG_JOIN, 1, 0)], 1),                                    # forced, then joined again
    (TAKEN, [(1, LOG_JOIN, 1, 0), (2, LOG_BOOT, STALE, 1), (2, LOG_LEAVE, 2, 0)], 1),
    (OWN_NETWORK, [], 1),               # the marker came for a network of its own (converted before it)
    (OWN_NETWORK, [(1, LOG_LEAVE, WIPE_FORCED, 0)], 0),
])
def test_a_factory_copy_older_than_a_network_of_its_own_is_stale(stub, tmp_path, head, recs, stale):
    got = log_scan(stub, tmp_path, marker_sector(head, recs))
    assert got["stale"] == stale
    assert got["at"] == MARKER_ADDR + LOG_START + 8 * len(recs)
    assert got["boot"] == (recs[-1][0] if recs else 0)


def test_a_full_log_is_stale_even_forced(stub, tmp_path):
    """A join after the sector filled up is not in it: the take cannot know, so it does not."""
    for last in ([TAKEN_AT_BOOT], [(1, LOG_LEAVE, WIPE_FORCED, 0)]):
        got = log_scan(stub, tmp_path, marker_sector(TAKEN, last, full=True))
        assert got == {"stale": 1, "at": 0, "boot": 1}


# --- the factory NV's geometry: the key pairs' 16 KB sectors and 3 KB index -------------------

KEYPAIR_SECT = (0xE6000, 0xEA000)


def keypair_image(copies: list[bytes], sector: int = 0, valid: bool = True,
                  other: bytes | None = None) -> bytes:
    """A blank 1 MB flash with the key pair module in the SDK's 1 MB layout: copies appended
    from the content start (sector + 3 KB), each 4-aligned, one index entry each. other: a
    sector header put at the other sector's start (as Tuya's module 9 at 0xEA000)."""
    d = bytearray(b"\xFF" * 0x100000)
    sect = KEYPAIR_SECT[sector]
    d[sect:sect + 4] = struct.pack("<HBB", 0x5A5A if valid else 0x5050, 7, sector)
    at = sect + 0xC00
    for i, payload in enumerate(copies):
        item = item_header(payload, 8) + payload
        d[at:at + len(item)] = item
        d[sect + 4 + 8 * i:sect + 12 + 8 * i] = struct.pack("<IHBB", at, len(item), 8, 0x5A)
        at = (at + len(item) + 3) & ~3
    if other is not None:
        osect = KEYPAIR_SECT[1 - sector]
        d[osect:osect + len(other)] = other
    return bytes(d)


def pair_copy(i: int) -> bytes:
    return factory_pair()[:8] + bytes([i]) * 16 + factory_pair()[24:]


def test_the_newest_of_many_key_pair_copies(stub, tmp_path):
    """30 copies (a write each time the pair changes): the 4 KB sectors the parser assumed saw
    the first 23 of them (items up to 0xE6FFF) and took the 23rd."""
    path = tmp_path / "pairs.bin"
    path.write_bytes(keypair_image([pair_copy(i) for i in range(30)]))
    assert stock_nv(stub, path, *SS_KEY_PAIR) == pair_copy(29)


def test_a_key_pair_in_the_second_sector(stub, tmp_path):
    """After the module moved on to its second sector (0xEA000, the first one marked invalid):
    the 4 KB layout looked for it at 0xE7000."""
    path = tmp_path / "second.bin"
    path.write_bytes(keypair_image([pair_copy(1), pair_copy(2)], sector=1,
                                   other=struct.pack("<HBB", 0x5050, 7, 0)))
    assert stock_nv(stub, path, *SS_KEY_PAIR) == pair_copy(2)


def test_tuyas_module_9_where_the_second_key_pair_sector_would_be(stub, tmp_path):
    """While the key pairs never moved on, 0xEA000 holds Tuya's own module 9: its header names
    it, so it is not read as a key pair sector, whatever its items look like."""
    path = tmp_path / "nine.bin"
    d = bytearray(keypair_image([pair_copy(3)]))
    item = item_header(pair_copy(9), 8) + pair_copy(9)
    d[0xEA000:0xEA00C] = struct.pack("<HBB", 0x5A5A, 9, 0) + struct.pack("<IHBB", 0xEA400, 44, 8, 0x5A)
    d[0xEA400:0xEA400 + len(item)] = item
    path.write_bytes(bytes(d))
    assert stock_nv(stub, path, *SS_KEY_PAIR) == pair_copy(3)


def test_an_item_past_a_4_kb_sector_is_not_read(stub, tmp_path):
    """An index entry pointing past its 4 KB sector (into the next module's) is not taken; the
    key pairs' 16 KB sector holds items up to 0xE9FFF, and their 3 KB index 383 entries (200
    copies: the parser read 127 entries)."""
    d = bytearray(b"\xFF" * 0x100000)
    info = bytes(range(192))
    d[0xD8000:0xD8004] = struct.pack("<HBB", 0x5A5A, 0, 0)
    d[0xD8004:0xD800C] = struct.pack("<IHBB", 0xD8FC0, 200, 1, 0x5A)   # crosses 0xD9000
    d[0xD8FC0:0xD8FC0 + 200] = item_header(info, 1) + info
    path = tmp_path / "past.bin"
    path.write_bytes(bytes(d))
    assert stock_nv(stub, path, *ZB_INFO) is None
    path.write_bytes(keypair_image([pair_copy(i) for i in range(200)]))   # up to 0xE8C00..
    assert stock_nv(stub, path, *SS_KEY_PAIR) == pair_copy(199)


# --- the take itself (zigbee_network.c built for the host, tests/stock_nv_take_host.py) -------

@pytest.fixture
def release(tmp_path):
    return build_take(tmp_path)


@pytest.fixture
def debug(tmp_path):
    return build_take(tmp_path, debug=True)


def own_ss(lib) -> bytes:
    item = lib.h_own_item(1)
    return bytes(item.contents[:SS_BYTES]) if item else None


def test_a_first_conversion_writes_the_mapped_record_and_the_pair(release):
    factory_network(release)
    release.h_own(0)
    d = release.h_boot().contents
    assert d.result == TAKEN and d.found == 0x80 | 7
    ss = own_ss(release)
    assert ss[SS_BITS] == OURS_SLOT_1 and ss[SS_TCLK_TYPE] == 0 and ss[SS_PRECONF] == 0
    assert bytes(release.h_own_item(2).contents[:36]) == factory_pair()


def test_a_unique_tc_link_key_without_its_pair_is_taken_as_the_global_one(release):
    """The pair belongs to another device: not taken, the type global (found bit 4), so a
    joining child's Update Device also goes with the network key alone."""
    factory_network(release, pair=factory_pair(address=bytes(range(8))))
    release.h_own(0)
    d = release.h_boot().contents
    assert d.result == TAKEN and d.found & 0x14 == 0x14
    assert own_ss(release)[SS_TCLK_TYPE] == 1 and not release.h_own_item(2)


@pytest.mark.parametrize("tc", [bytes(8), b"\xFF" * 8])
def test_no_take_without_a_trust_centre(release, tc):
    factory_network(release, ss=bytes(factory_ss(tc=tc)))
    release.h_own(0)
    d = release.h_boot().contents
    assert d.result == IMPLAUSIBLE and release.h_own_writes() == 0
    assert marker_head(release) == b"\xFF" * 32          # comes up factory new; nothing marked


def test_a_record_taken_raw_cannot_come_back():
    """The take writes the security record only after stock_nv_take_ss, and sets no field of it
    by hand but this build's key pointers: the raw take of the first W4c test builds (the
    factory byte 0x3E as it was: "secure all" cleared in this SDK's order) cannot be rebuilt by
    a slip in the take."""
    take = function_text(NETWORK_C.read_text(), "stock_nv_take_network")
    assert take.index("stock_nv_take_ss(") < take.index("NV_ITEM_APS_SSIB, sizeof(ss), (u8 *)&ss")
    assert take.index("if (!take) {") < take.index("nv_flashWriteNew(")
    fields = set(re.findall(r"\bss\.(\w+)\s*=", take))
    assert fields == {"touchLinkKey", "distributeLinkKey", "tcLinkKey"}
    assert "take & STOCK_NV_SS_PAIR" in take


def test_debug_builds_take_it_again_after_a_wipe(debug):
    factory_network(debug)
    debug.h_own(0)
    assert debug.h_boot().contents.result == TAKEN
    debug.h_wipe(0)                                        # 0xA5
    assert not debug.h_own_info() and debug.h_reboots() == 1
    assert debug.h_boot().contents.result == TAKEN and debug.h_own_info()


@pytest.mark.parametrize("how", ["steering", "start-up"])
def test_debug_builds_refuse_a_copy_older_than_a_join_of_their_own(debug, how):
    """Joined under this firmware since the take (by steering, or found at a start-up on another
    address: a release build's join): the join exchanged a newer link key with the trust centre,
    so a wipe or a leave must not bring the copy back (a module so re-taken relayed no joins)."""
    factory_network(debug)
    debug.h_own(0)
    assert debug.h_boot().contents.result == TAKEN
    if how == "steering":
        debug.h_log_join()
    else:
        debug.h_own(1)                                     # its own network, another address
        assert debug.h_boot().contents.result == OWN_NETWORK and debug.h_log_stale()
        debug.h_boot()
        assert [r[1:3] for r in log_records(debug) if r[1] == LOG_JOIN] == [(LOG_JOIN, 2)]  # once
    debug.h_wipe(0)
    d = debug.h_boot().contents
    assert d.result == STALE and not debug.h_own_info() and debug.h_own_writes() == 0
    debug.h_wipe(1)                                        # 0xA6: the same
    assert debug.h_boot().contents.result == STALE


def test_debug_builds_on_the_taken_network_still_take_it_again(debug):
    factory_network(debug)
    debug.h_own(0)
    debug.h_boot()
    info = debug.h_own_item(0)
    assert struct.unpack_from("<HH", bytes(info.contents[3:7])) == (PAN_ID, NWK_ADDR)
    assert debug.h_boot().contents.result == OWN_NETWORK and not debug.h_log_stale()
    debug.h_wipe(0)
    assert debug.h_boot().contents.result == TAKEN


def test_the_bench_forces_a_take(debug):
    """0xA7 / 0xA8 (forced): the next take ignores the join since; the one after again not."""
    factory_network(debug)
    debug.h_own(0)
    debug.h_boot()
    debug.h_log_join()
    debug.h_wipe(2)                                        # 0xA7
    assert debug.h_boot().contents.result == TAKEN
    debug.h_wipe(0)                                        # no join since the forced take
    assert debug.h_boot().contents.result == TAKEN
    debug.h_log_join()
    debug.h_wipe(1)
    assert debug.h_boot().contents.result == STALE
    debug.h_wipe(3)                                        # 0xA8
    assert debug.h_boot().contents.result == TAKEN


def test_debug_builds_marked_on_a_network_of_their_own_do_not_take_it(debug):
    """A module converted before the marker and on a network of its own gets the marker then
    (its head says so): the factory copy is older from the start."""
    factory_network(debug)
    debug.h_own(1)
    assert debug.h_boot().contents.result == OWN_NETWORK
    debug.h_wipe(0)
    assert debug.h_boot().contents.result == STALE
    debug.h_wipe(2)
    assert debug.h_boot().contents.result == TAKEN


def test_release_builds_take_it_once_whatever_the_log(release):
    factory_network(release)
    release.h_own(0)
    assert release.h_boot().contents.result == TAKEN
    for own in (0, 1, 0):
        release.h_own(own)
        assert release.h_boot().contents.result in (TAKEN_BEFORE, OWN_NETWORK)
        assert release.h_own_writes() == 0


def test_a_join_of_its_own_is_logged_when_steering_ends_in_it():
    """bdb_commissioning_callback: the record while steeringInProgress still says it was a join
    of its own (not a rejoin), before it is cleared."""
    body = function_text(NETWORK_C.read_text(), "bdb_commissioning_callback")
    case = body[body.index("case BDB_COMMISSION_STA_SUCCESS:"):body.index("break;")]
    assert re.search(r"if \(steeringInProgress\) \{[^}]*stock_nv_log\(STOCK_NV_LOG_JOIN, 1, 0\);",
                     case)
    assert case.index("STOCK_NV_LOG_JOIN") < case.index("steeringInProgress = 0;")
