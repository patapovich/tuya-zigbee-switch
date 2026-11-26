"""The outgoing NWK frame counter's floor (src/telink/hal/zigbee_network.c,
romasku/tuya-zigbee-switch#255) over start-ups, leaves and joins, against the SDK's store of the
counter (SDK 3.7.2.0 proj/drivers/drv_nv.c:836-1017).

The stack cannot run on the host, so this is a model test:
- the order in the source: the floor after bdb_init(), whose ss_zdoInit() loads the counter
  (it ran after zb_init(), which does not), written twice, only below the floor;
- `Unit` plays the stack's part at start-up and on the air (libzb_router 3.7.2.0, see there)
  over a store of the counter: `ModelStore`, a Python model of drv_nv.c, or, where the SDK is
  checked out (telink_tools/sdk), `SdkStore`: the SDK's own drv_nv.c, with the patch the image
  takes it with (src/telink/patch_sdk/proj/drivers/drv_nv.c.patch), and the floor function
  cut out of zigbee_network.c, built for the host over a RAM flash. Both run the same
  sequences; on the SDK's store the model must give the same counters and the same entries.

No counter may go back to a value already sent: the routers around drop such frames as
replays (a router keeps the last counter it saw from each neighbour)."""
import ctypes
import random
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NETWORK_C = ROOT / "src" / "telink" / "hal" / "zigbee_network.c"
SDK = ROOT / "telink_tools" / "sdk"
DRV_NV_C = SDK / "proj" / "drivers" / "drv_nv.c"
# the image compiles drv_nv.c with this patch applied (sdk.mk SDK_PATCHED)
DRV_NV_PATCH = ROOT / "src" / "telink" / "patch_sdk" / "proj" / "drivers" / "drv_nv.c.patch"

M = 0xFFFFFFFF
FLOOR = 0x10000000
PER_SECTOR = 1023        # FRAMECOUNT_NUM_PER_SECT(): (4096 - 4) / 4 entries a sector
THRES = 1024             # UPDATE_FRAMECOUNT_THRES, drv_nv.c:42
ABOVE = 0x10012345       # a unit that has run with the floor
BELOW = 0x000E5001       # one still below it (converted before the floor existed)


def function_text(text: str, name: str) -> str:
    """The definition of C function `name` in `text`, from its head to its closing brace."""
    m = re.search(r"^[A-Za-z][^\n;]*\b" + name + r"\([^)]*\)\s*\{", text, re.M)
    assert m, name
    i, depth = m.end(), 1
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.start():i]


# --- the order in the source ---------------------------------------------------------------

def test_the_floor_comes_after_the_load_in_bdb_init():
    text = NETWORK_C.read_text()
    calls = re.findall(r"\bnwk_frame_counter_apply_floor\(\);", text)
    assert len(calls) == 1, "one call, after bdb_init()"
    init = function_text(text, "telink_zigbee_hal_network_init")
    assert "nwk_frame_counter_apply_floor" not in init, "before the load"
    bdb = function_text(text, "telink_zigbee_hal_bdb_init")
    assert bdb.index("bdb_init(") < bdb.index("nwk_frame_counter_apply_floor();")


def test_the_floor_is_written_twice_and_only_below_it():
    body = function_text(NETWORK_C.read_text(), "nwk_frame_counter_apply_floor")
    inner = re.search(r"if \(ss_ib\.outgoingFrameCounter < NWK_FRAME_COUNTER_FLOOR\) \{(.*?)\}",
                      body, re.S)
    assert inner, body
    assert inner.group(1).count("nv_nwkFrameCountSaveToFlash(NWK_FRAME_COUNTER_FLOOR + 1u)") == 2
    assert body.count("nv_nwkFrameCountSaveToFlash") == 2           # nothing outside the if


# --- the stores ----------------------------------------------------------------------------

class ModelStore:
    """drv_nv.c's frame-counter module: two sectors, entries appended to the valid one."""

    def __init__(self, per_sector: int = PER_SECTOR):
        self.n = per_sector
        self.sect: list[list[int]] = [[], []]
        self.valid: int | None = None          # a blank module
        self.erases = 0
        self.writes = 0

    def _write(self, force: bool, err: int, value: int) -> None:
        """nv_nwkFrameCountSaveToFlashHandler, drv_nv.c:894-973."""
        if force:                              # to the other sector, erased, as its first entry
            op = (err + 1) & 1
            self.sect[op] = []
            self.erases += 1
        elif self.valid is None:               # no valid sector: both erased, the first one
            self.sect = [[], []]
            self.erases += 2
            op = 0
        else:
            op = self.valid
        moved = False
        if len(self.sect[op]) == self.n:       # full: on in the other one, the last entry first
            last = self.sect[op][-1]
            op ^= 1
            self.sect[op] = [last]
            self.erases += 1
            moved = True
        self.sect[op].append(value & M)
        self.writes += 1
        if force or moved or len(self.sect[op]) == 1:
            self.valid = op

    def save(self, value: int) -> None:
        """nv_nwkFrameCountSaveToFlash."""
        self._write(False, 0xFF, value)

    def load(self) -> int | None:
        """nv_nwkFrameCountFromFlash, drv_nv.c:985-1017: the last entry; with 3 or more, a last
        one more than 1024 above the one before it is taken for a bad write: that one + 1024,
        written alone to the other sector."""
        if self.valid is None or not self.sect[self.valid]:
            return None
        e = self.sect[self.valid]
        if len(e) >= 3 and (e[-1] - e[-2]) & M > THRES:
            value = (e[-2] + THRES) & M
            self._write(True, self.valid, value)
            return value
        return e[-1]

    def floor(self, counter: int) -> int:
        """zigbee_network.c nwk_frame_counter_apply_floor() (SdkStore runs the code itself)."""
        if counter < FLOOR:
            counter = FLOOR + 1
            self.save(counter)
            self.save(counter)
        return counter

    def entries(self) -> list[int]:
        return [] if self.valid is None else list(self.sect[self.valid])

    def state(self):
        return (self.sect[0], self.sect[1], self.valid, self.erases, self.writes)


HARNESS = r"""
#include <stdlib.h>
#include "tl_common.h"

static u8  flash[0x80000];      /* the 512 KB map: the counter's module at 0x3C000..0x3E000 */
static u32 n_writes, n_erases;

void flash_write(u32 addr, u32 len, u8 *buf) {
    for (u32 i = 0; i < len; i++) {
        flash[addr + i] &= buf[i];                  /* NOR: a write only clears bits */
    }
    n_writes++;
}
bool flash_writeWithCheck(u32 addr, u32 len, u8 *buf) {
    flash_write(addr, len, buf);
    return memcmp(flash + addr, buf, len) == 0;
}
void flash_read(u32 addr, u32 len, u8 *buf) { memcpy(buf, flash + addr, len); }
void flash_erase(u32 addr) { memset(flash + (addr & ~0xFFFu), 0xFF, 4096); n_erases++; }
u8 *ev_buf_allocate(u16 size) { return malloc(size); }
int ev_buf_free(u8 *buf) { free(buf); return 0; }

static struct { u32 outgoingFrameCounter; } ss_ib;     /* the stack's: the counter only */

FLOOR_SOURCE

void h_blank(void) { memset(flash, 0xFF, sizeof(flash)); n_writes = n_erases = 0; }
void h_save(u32 value) { nv_nwkFrameCountSaveToFlash(value); }
int h_load(u32 *value) { return nv_nwkFrameCountFromFlash(value) == NV_SUCC; }
u32 h_floor(u32 counter) {
    ss_ib.outgoingFrameCounter = counter;
    nwk_frame_counter_apply_floor();
    return ss_ib.outgoingFrameCounter;
}
const u8 *h_module(void) { return flash + 0x3C000; }
u32 h_writes(void) { return n_writes; }
u32 h_erases(void) { return n_erases; }
"""

# What drv_nv.c needs of tl_common.h (the SDK's pulls in the whole SDK).
SHIM = r"""
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int8_t s8;
typedef int16_t s16;
typedef int32_t s32;
#define _attribute_packed_ __attribute__((packed))
#define TRUE 1
#define FALSE 0
#define MCU_CORE_8258 1
#define NV_ENABLE 1
#define min2(a, b) ((a) < (b) ? (a) : (b))
#define foreach(i, n) for (int i = 0; i < (n); ++i)
#define OFFSETOF(s, m) __builtin_offsetof(s, m)
#define ZB_EXCEPTION_POST(e) ((void)0)
#include "proj/drivers/drv_nv.h"
#include "proj/drivers/drv_flash.h"
unsigned int xcrc32(const unsigned char *buf, int len, unsigned int init);
u8 *ev_buf_allocate(u16 size);
int ev_buf_free(u8 *buf);
"""


@pytest.fixture(scope="module")
def sdk_lib(tmp_path_factory):
    if not DRV_NV_C.is_file():
        pytest.skip("no SDK checkout at telink_tools/sdk")
    d = tmp_path_factory.mktemp("fc")
    (d / "drivers").mkdir()
    # drv_nv.c as the image has it: the SDK's, its CRs dropped, the patch applied as sdk.mk does
    # (its "../tl_common.h": SHIM)
    (d / "drv_nv.c.orig").write_bytes(DRV_NV_C.read_bytes().replace(b"\r", b""))
    with DRV_NV_PATCH.open("rb") as p, (d / "drivers" / "drv_nv.c").open("wb") as out:
        subprocess.run(["patch", "-s", "-o", "-", str(d / "drv_nv.c.orig")], stdin=p, stdout=out,
                       check=True)
    (d / "tl_common.h").write_text(SHIM)
    text = NETWORK_C.read_text()
    floor = re.search(r"^#define NWK_FRAME_COUNTER_FLOOR\s+\S+$", text, re.M).group(0)
    (d / "harness.c").write_text(HARNESS.replace(
        "FLOOR_SOURCE", floor + "\n" + function_text(text, "nwk_frame_counter_apply_floor")))
    inc = ["-I", str(d), "-I", str(SDK.resolve())]
    subprocess.run(["cc", "-O1", "-fPIC", "-w", *inc, "-c", str(d / "drivers" / "drv_nv.c"),
                    "-o", str(d / "drv_nv.o")], check=True)
    subprocess.run(["cc", "-O1", "-fPIC", "-Wall", "-Werror", *inc, "-c", str(d / "harness.c"),
                    "-o", str(d / "harness.o")], check=True)
    so = d / "fc.so"
    subprocess.run(["cc", "-shared", str(d / "drv_nv.o"), str(d / "harness.o"),
                    str(ROOT / "src" / "zigbee" / "crc32_nibble.c"), "-I", str(ROOT / "src"),
                    "-fPIC", "-o", str(so)], check=True)
    lib = ctypes.CDLL(str(so))
    lib.h_save.argtypes = [ctypes.c_uint32]
    lib.h_load.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
    lib.h_floor.argtypes = [ctypes.c_uint32]
    lib.h_floor.restype = ctypes.c_uint32
    lib.h_module.restype = ctypes.POINTER(ctypes.c_uint8 * 0x2000)
    lib.h_writes.restype = ctypes.c_uint32
    lib.h_erases.restype = ctypes.c_uint32
    return lib


class SdkStore:
    """The SDK's drv_nv.c over a RAM flash, and the firmware's own floor function."""

    def __init__(self, lib):
        self.lib = lib
        lib.h_blank()

    def save(self, value: int) -> None:
        self.lib.h_save(value & M)

    def load(self) -> int | None:
        v = ctypes.c_uint32()
        return v.value if self.lib.h_load(ctypes.byref(v)) else None

    def floor(self, counter: int) -> int:
        return self.lib.h_floor(counter)

    @property
    def erases(self) -> int:
        return self.lib.h_erases()

    @property
    def writes(self) -> int:
        return self.lib.h_writes()

    def module(self) -> bytes:
        return bytes(self.lib.h_module().contents)

    def entries(self) -> list[int]:
        """The valid sector's entries (a sector: a 4-byte header, then 4-byte counts)."""
        m = self.module()
        valid = [s for s in (0, 1)
                 if int.from_bytes(m[s * 4096:s * 4096 + 2], "little") in (0x5A5A, 0x7A7A, 0xFAFA)]
        if not valid:
            return []
        assert len(valid) == 1, "two valid sectors"
        out = []
        for at in range(valid[0] * 4096 + 4, valid[0] * 4096 + 4096, 4):
            v = int.from_bytes(m[at:at + 4], "little")
            if v == M:
                break
            out.append(v)
        return out

    def state(self):
        return (self.module(), self.erases, self.writes)


@pytest.fixture(params=["model", "sdk"])
def store(request):
    """A blank store of the counter, per test, on either side."""
    if request.param == "model":
        return ModelStore
    lib = request.getfixturevalue("sdk_lib")
    return lambda: SdkStore(lib)


# --- the stack ------------------------------------------------------------------------------

class Unit:
    """The stack's part (libzb_router 3.7.2.0; SDK zigbee/bdb/bdb.c) in the counter's life.

    order: "sdk" no floor at all; "old" the floor after zb_init() (on ss_ib still zero, once,
    0x10000000); "new" the floor after bdb_init() (zigbee_network.c now).
    `counter` is the next value to send; `sent` the highest one sent."""

    def __init__(self, store, order: str, joined: bool = False):
        self.store, self.order, self.joined = store, order, joined
        self.counter = 0
        self.sent: int | None = None

    def seed(self, last: int, count: int = 40) -> "Unit":
        """A unit that has been on the air up to `last`: its periodic saves, 1024 apart."""
        count = min(count, last // THRES + 1)
        for i in range(count):
            self.store.save(last - THRES * (count - 1 - i))
        self.counter = last
        self.sent = last - 1
        return self

    def boot(self, init_done: bool = True) -> int:
        c = 0                                          # ss_ib: its zero .data image
        if self.order == "old" and c < FLOOR:          # telink_zigbee_hal_network_init, before
            c = FLOOR
            self.store.save(c)
        loaded = self.store.load()                     # bdb_init -> ss_zdoInit (+0x22..0x90):
        c = 0 if loaded is None else loaded            # NV, or 0 where none is
        if self.joined:                                # bdb_outgoingFrameCountUpdate(1)
            c = (c + THRES) & M
            self.store.save(c)
        if self.order == "new":
            c = self.store.floor(c)
        if self.joined and init_done:                  # bdb_task BDB_EVT_INIT_DONE, bdb.c:1094
            self.store.save(c)
        self.counter = c
        return c

    def send(self, frames: int) -> None:
        """Frames on the air, and zdo_ssInfoUpdate() in each main loop between them: a counter
        on a multiple of 1024 goes 1 on and is saved."""
        assert self.joined
        c = self.counter
        if c % THRES == 0:
            c += 1
            self.store.save(c)
        while frames:
            n = min(frames, THRES - c % THRES)
            self.sent = max(self.sent or 0, c + n - 1)
            c += n
            frames -= n
            if c % THRES == 0:
                c += 1
                self.store.save(c)
        self.counter = c

    def leave(self) -> None:
        """zdo_selfLeaveProcessCb (+0x38..0x4e), no rejoin: the counter saved, no restart."""
        self.store.save(self.counter)
        self.joined = False

    def join(self, frames: int = 5) -> None:
        """Association, keys, announce, then bdb_commissioningInfoSave (bdb.c:184-196)."""
        self.joined = True
        self.send(frames)
        self.store.save(self.counter)

    def stepped_back(self) -> bool:
        return self.sent is not None and self.counter <= self.sent


# --- sequences -----------------------------------------------------------------------------

def test_a_new_unit_starts_at_the_floor(store):
    s = store()
    u = Unit(s, "new")
    assert u.boot() == FLOOR + 1
    assert s.entries() == [FLOOR + 1, FLOOR + 1]
    writes, erases = s.writes, s.erases
    assert u.boot() == FLOOR + 1                       # once only
    assert (s.writes, s.erases) == (writes, erases)
    u.join()
    assert u.boot() > u.sent and u.counter > FLOOR


@pytest.mark.parametrize("boots", [1, 2, 3, 5])
def test_boots_off_the_network_keep_the_counter(store, boots):
    """A reset (a leave) and then start-ups before the join: the counter of the leave."""
    u = Unit(store(), "new", joined=True).seed(ABOVE)
    u.boot()
    u.send(3000)
    u.leave()
    at_leave = u.counter
    for _ in range(boots):
        assert u.boot() == at_leave
    u.join()
    assert not u.stepped_back()
    assert u.boot() > u.sent


def test_the_old_order_went_back_to_the_floor(store):
    """What the floor before the load did (documents the bug): the second start-up off the
    network took the floor's 0x10000000 below the counters already sent."""
    u = Unit(store(), "old", joined=True).seed(ABOVE)
    u.boot()
    u.send(3000)
    u.leave()
    u.boot()
    assert u.boot() == FLOOR and u.stepped_back()


def test_a_unit_below_the_floor_is_raised_at_its_first_start(store):
    s = store()
    u = Unit(s, "new", joined=True).seed(BELOW)
    assert u.boot() == FLOOR + 1
    assert s.entries()[-3:] == [FLOOR + 1] * 3          # two of the floor, INIT_DONE's
    u.send(5000)
    for _ in range(4):
        before = len(s.entries())
        assert u.boot() > u.sent
        assert len(s.entries()) - before == 2           # the SDK's own two saves, no floor
        u.send(10)


def test_the_old_order_never_raised_a_unit_below_the_floor(store):
    """(documents the bug) each start-up wrote the floor and the load undid it."""
    u = Unit(store(), "old", joined=True).seed(BELOW)
    for _ in range(6):
        assert u.boot() < FLOOR
        u.send(100)


@pytest.mark.parametrize("room", [0, 1, 2, 3])
def test_the_floor_across_a_sector_change(store, room):
    """The two floor entries with `room` entries left in the sector."""
    s = store()
    u = Unit(s, "new").seed(0x08000001, count=PER_SECTOR - room)
    assert u.boot() == FLOOR + 1
    assert s.entries()[-2:] == [FLOOR + 1, FLOOR + 1]
    assert u.boot() == FLOOR + 1
    assert not u.stepped_back()


def random_ops(rnd: random.Random, long: bool) -> list[tuple]:
    sends = [0, 1, 5, 300, 1022, 1023, 1024, 1025, 3000]
    if long:                                # past the end of a sector: 1023 saves
        sends += [THRES * 400, THRES * 1100]
    ops = []
    for _ in range(rnd.randint(1, 14)):
        r = rnd.random()
        if r < 0.4:
            ops.append(("boot", rnd.random() < 0.8))
        elif r < 0.7:
            ops.append(("send", rnd.choice(sends)))
        elif r < 0.85:
            ops.append(("leave",))
        else:
            ops.append(("join",))
    return ops


def unit_at(store, order: str, start: int | None) -> Unit:
    """A new unit (start None), or one on a network with a history up to `start`."""
    u = Unit(store, order, joined=start is not None)
    return u.seed(start) if start is not None else u


def play(u: Unit, ops: list[tuple]) -> tuple[list, list]:
    """The ops on the unit: what each left, and every step back."""
    log, back = [], []
    for op in ops:
        if op[0] == "boot":
            u.boot(op[1])
            if u.stepped_back():
                back.append((op, hex(u.sent), hex(u.counter)))
            if u.order == "new":
                assert u.counter >= FLOOR, (op, hex(u.counter))
        elif op[0] == "send" and u.joined:
            u.send(op[1])
        elif op[0] == "leave" and u.joined:
            u.leave()
        elif op[0] == "join" and not u.joined:
            u.join()
        e = u.store.entries()
        log.append((op, u.counter, len(e), e[-3:], u.store.erases))
    return log, back


STARTS = [None, 25000, BELOW, 0x0FFFFF00, ABOVE, 0x7F000001]


@pytest.mark.parametrize("per_sector", [3, 4, 5, 8, PER_SECTOR])
def test_random_sequences_never_step_back(per_sector):
    rnd = random.Random(per_sector)
    old_back = 0
    for start in STARTS:
        for _ in range(150):
            ops = random_ops(rnd, long=False)
            _, back = play(unit_at(ModelStore(per_sector), "new", start), ops)
            assert not back, (start, ops, back)
            old_back += bool(play(unit_at(ModelStore(per_sector), "old", start), ops)[1])
    assert old_back > 0                     # the old order did (documents the bug)


def test_random_sequences_never_step_back_on_the_sdk_store(sdk_lib):
    rnd = random.Random(7)
    for start in STARTS:
        for _ in range(150):
            ops = random_ops(rnd, long=True)
            _, back = play(unit_at(SdkStore(sdk_lib), "new", start), ops)
            assert not back, (start, ops, back)


@pytest.mark.parametrize("per_sector", [3, 5, PER_SECTOR, "sdk"])
def test_an_update_from_the_old_order_never_steps_back(request, per_sector):
    """An OTA from a build with the floor before the load: the new order from what the old one
    left in flash. (Where the old one stepped back already, nothing can undo it.)"""
    if per_sector == "sdk":
        lib = request.getfixturevalue("sdk_lib")
        make = lambda: SdkStore(lib)                                 # noqa: E731
    else:
        make = lambda: ModelStore(per_sector)                        # noqa: E731
    rnd = random.Random(str(per_sector))
    updated = 0
    for start in STARTS:
        for _ in range(150):
            u = unit_at(make(), "old", start)
            if play(u, random_ops(rnd, long=False))[1]:
                continue
            u.order = "new"
            ops = [("boot", True)] + random_ops(rnd, long=False)    # the update's restart
            _, back = play(u, ops)
            assert not back, (start, ops, back)
            updated += 1
    assert updated > 300


def test_above_the_floor_the_sdks_own_handling(store):
    """A unit above the floor: no write of the floor, no sector change at a start-up; the flash
    exactly as without any floor. The old order wrote and changed sectors at every start-up."""
    rnd = random.Random(3)
    old_more = 0
    for start in (ABOVE, 0x7F000001):
        for _ in range(30):
            ops = random_ops(rnd, long=False)
            runs = {}
            for order in ("sdk", "new", "old"):
                s = store()
                log, _ = play(unit_at(s, order, start), ops)
                runs[order] = (log, s.state(), s.erases)
            assert runs["new"][:2] == runs["sdk"][:2], ops
            old_more += runs["old"][2] > runs["sdk"][2]
    assert old_more > 0


def test_the_model_is_the_sdks_store(sdk_lib):
    """The model's store against the SDK's own code, step by step, sector changes included."""
    rnd = random.Random(11)
    for start in STARTS:
        for _ in range(20):
            ops = random_ops(rnd, long=True)
            for order in ("sdk", "old", "new"):
                model = play(unit_at(ModelStore(), order, start), ops)
                sdk = play(unit_at(SdkStore(sdk_lib), order, start), ops)
                assert model == sdk, (order, start, ops)
