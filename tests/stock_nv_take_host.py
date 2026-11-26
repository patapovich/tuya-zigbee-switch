"""The W4c take (src/telink/hal/zigbee_network.c stock_nv_take_network) built for the host: the
Telink functions cut out of the source, over stand-in SDK types, a 1 MB NOR flash and a model of
this firmware's own NV, with the real src/zigbee/stock_nv.c (the factory NV parser, the take's
security-record check stock_nv_take_ss, the debug log scan) and crc32_nibble.c.

A release build (the marker stops any second take) and a debug build (MCU_FLASHER_DUMP: the log,
the wipe hook, a take again unless the module has been on a network of its own or left one since)
are built from the same source, optionally with the HAL's leave confirm (the restart after a
leave, LEAVE_RESTART, recorded). The synthetic factory NV is made here; no factory dump is read."""
import ctypes
import re
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
NETWORK_C = SRC / "telink" / "hal" / "zigbee_network.c"

STOCK_NV_BASE = 0xD8000
MODULE_BYTES = 0x2000
MARKER_ADDR = 0xD0000
MARKER_MAGIC = 0x32433457
LOG_START = 32
(OWN_NETWORK, NOT_1MB, NO_ZB_INFO, NO_SECURITY, IMPLAUSIBLE, TAKEN, TAKEN_BEFORE, MARKER_IN_USE,
 STALE) = range(9)
LOG_BOOT, LOG_LEAVE, LOG_JOIN = 1, 5, 8
LOG_JOIN_LEFT = 3                                       # STOCK_NV_LOG_JOIN's a: a network left

# ss_info_base_t as the module has it (81 bytes, 4-byte pointers), byte offsets
SS_BYTES, PAIR_BYTES = 81, 36
SS_KEY_PAIR_SET_NEW, SS_MATERIAL, SS_DEV_KEY_PAIR_NUM, SS_TC_ADDRESS = 0x0C, 0x10, 0x34, 0x36
SS_BITS, SS_ACTIVE_SEQ, SS_PRECONF, SS_TC_POLICY, SS_TCLK_TYPE = 0x3E, 0x3F, 0x40, 0x41, 0x4C
TC = bytes.fromhex("1122334455667788")                  # an invented trust centre address
NWK_KEY = bytes(range(0x30, 0x40))                      # an invented network key
LINK_KEY = bytes(range(0x50, 0x60))                     # an invented TC link key


def function_text(text: str, name: str) -> str:
    """The definition of C function `name` in `text`, from its head to its closing brace."""
    m = re.search(r"^[A-Za-z][^\n;]*\b" + name + r"\([^)]*\)\s*\{", text, re.M)
    assert m, name
    i, depth = m.end(), 1
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.start():i]


def log_vars_block(text: str) -> str:
    """The debug builds' log state (the #ifdef MCU_FLASHER_DUMP block with g_log_at)."""
    m = re.search(r"^#ifdef MCU_FLASHER_DUMP\nstatic uint32_t g_log_at;.*?^#endif\n", text, re.M | re.S)
    assert m, "the log's state"
    return m.group(0)


def factory_ss(bits: int = 0x35, tclk_type: int = 0, slot: int = 1, tc: bytes = TC) -> bytearray:
    """A factory security record: the network key in `slot` (sequence number 0, the active one),
    the factory byte 0x3E, the trust centre, its link key type; counters and the rest made up."""
    ss = bytearray(SS_BYTES)
    ss[4:8] = struct.pack("<I", 0x00012345)            # outgoing frame counter
    ss[SS_KEY_PAIR_SET_NEW:SS_KEY_PAIR_SET_NEW + 4] = b"\x10\x5c\x84\x00"   # a factory RAM address
    m = SS_MATERIAL + 18 * slot
    ss[m:m + 18] = NWK_KEY + bytes([0, 1])
    ss[SS_DEV_KEY_PAIR_NUM:SS_DEV_KEY_PAIR_NUM + 2] = b"\x01\x00"
    ss[SS_TC_ADDRESS:SS_TC_ADDRESS + 8] = tc
    ss[SS_BITS] = bits
    ss[SS_ACTIVE_SEQ] = 0
    ss[SS_PRECONF] = 1
    ss[SS_TC_POLICY:SS_TC_POLICY + 3] = b"\x00\x01\x01"
    ss[0x44:0x4C] = b"\x00\x40\x04\x00\x10\x40\x04\x00"  # factory key pointers
    ss[SS_TCLK_TYPE] = tclk_type
    ss[0x4D:0x51] = b"\x20\x40\x04\x00"
    return ss


def factory_pair(address: bytes = TC, key_attr: int = 2, key_type: int = 0) -> bytes:
    """A factory key pair (ss_dev_pair_set_t): the device, its link key, the key's attributes."""
    return address + LINK_KEY + bytes([key_attr, key_type, 1, 0]) + struct.pack("<II", 7, 9)


TAKE_GLUE = r"""
#include <stdint.h>
#include <string.h>
#include "zigbee/stock_nv.h"
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define NV_SUCC 0
#define NV_ITEM_NOT_FOUND 1
#define NV_MODULE_ZB_INFO 0
#define NV_MODULE_APS 2
#define NV_MODULE_KEYPAIR 7
#define NV_ITEM_ZB_INFO 1
#define NV_ITEM_APS_SSIB 4
#define NV_ITEM_SS_KEY_PAIR 8
#define FLASH_SIZE_1M 0x14
#define CFG_FACTORY_RST_CNT 0x79000

/* stand-ins with the fields the take uses (the network information's sizes are the stand-in's
 * own: the synthetic NV's items have them); the security record's first 81 bytes are the
 * module's layout (what stock_nv_take_ss reads and writes), this build's key pointers after it */
typedef struct __attribute__((packed)) {
    struct __attribute__((packed)) { u8 nodeIsOnANetwork; u8 nodeCommissioningCapability; } bdbAttr;
    struct __attribute__((packed)) { u8 phyChannelCur; } macPib;
    struct __attribute__((packed)) { u16 panId; u16 nwkAddr; u8 extPANId[8]; } nwkNib;
    u8 rest[192 - 15];
} zb_info_t;
typedef struct __attribute__((packed)) {
    u8 raw[81];
    u8 *touchLinkKey;
    u8 *distributeLinkKey;
    u8 *tcLinkKey;
} ss_info_base_t;
typedef struct __attribute__((packed)) { u8 b[36]; } ss_dev_pair_set_t;
static u8 keys[3][16];
static struct { struct { struct { u8 *key; } tcLinkKey, distributeLinkKey, touchLinkKey; } linkKey; }
    bdb_commission_setting = { { { keys[0] }, { keys[1] }, { keys[2] } } };
static struct { u8 nodeCommissioningCapability; } g_bdbAttrs;

/* the 1 MB flash (NOR: a write only clears bits) and this firmware's NV */
static u8  flash[0x100000];
static u32 mid = 0x146085;
static int own_writes, rst, reboots, reboot_ms;
static u32 millis;
static struct { int there; u8 data[256]; u16 len; } own[3];   /* ZB_INFO, APS_SSIB, KEY_PAIR */
static int own_slot(u8 module) { return module == NV_MODULE_ZB_INFO ? 0 : module == NV_MODULE_APS ? 1 : 2; }
void flash_read(u32 addr, u32 len, u8 *buf) { memcpy(buf, flash + addr, len); }
void flash_write_page(u32 addr, u32 len, u8 *buf) {
    for (u32 i = 0; i < len; i++) flash[addr + i] &= buf[i];
}
u32 flash_read_mid(void) { return mid; }
int nv_flashReadNew(u8 single, u8 module, u8 item, u16 len, u8 *buf) {
    int s = own_slot(module);
    if (!own[s].there || own[s].len != len) return NV_ITEM_NOT_FOUND;
    memcpy(buf, own[s].data, len);
    return NV_SUCC;
}
int nv_flashWriteNew(u8 single, u8 module, u8 item, u16 len, u8 *buf) {
    int s = own_slot(module);
    own_writes++;
    own[s].there = 1;
    own[s].len = len;
    memcpy(own[s].data, buf, len);
    return NV_SUCC;
}
/* (keeps the frame counter and NV_MODULE_APP, as the SDK's; with the leave confirm, the HAL's
 * hook of patch_sdk/drv_nv.c first, as there) */
#ifdef LEAVE_RESTART
void telink_zigbee_nv_reset(void);
#endif
int  nv_resetToFactoryNew(void) {
#ifdef LEAVE_RESTART
    telink_zigbee_nv_reset();
#endif
    memset(own, 0, sizeof(own));
    return 0;
}
u8   nv_facrotyNewRstFlagCheck(void) { return rst; }
void nv_facrotyNewRstFlagClear(void) { rst = 0; }
u32  hal_millis(void) { return millis; }
void schedule_reboot(u32 ms) { reboots++; reboot_ms = ms; }

#ifdef MCU_FLASHER_DUMP
static void stock_nv_log(uint8_t kind, uint8_t a, uint8_t b);
#else
#define stock_nv_log(kind, a, b) ((void)0)
#endif

#ifdef LEAVE_RESTART
/* the leave confirm's world: the stack's factory-new state, the restart and the status */
typedef struct { u8 deviceAddr[8]; u8 status; } nlme_leave_cnf_t;
static int factory_new, restarts, restart_left;
static int zb_isDeviceFactoryNew(void) { return factory_new; }
static void restart_after_leave(u8 left) { restarts++; restart_left = left; }
static void notify_about_network_status_change(void) {}
#endif

TAKE_SOURCE

#ifdef LEAVE_RESTART
/* the stack's own leave (a reset, the coordinator's remove): on a network, the NV emptied (the
 * HAL's hook tells it), factory new, the confirm */
void h_leave(void) {
    nlme_leave_cnf_t c = { { 0 }, 0 };
    factory_new = 0;
    nv_resetToFactoryNew();
    factory_new = 1;
    zdo_leave_confirmation_callback(&c);
}
/* the same of a unit that is factory new already (zb_factoryReset, confirmed as a leave) */
void h_leave_factory_new(void) {
    nlme_leave_cnf_t c = { { 0 }, 0 };
    factory_new = 1;
    nv_resetToFactoryNew();
    zdo_leave_confirmation_callback(&c);
}
int h_restarts(void) { return restarts; }
int h_restart_left(void) { return restart_left; }
#endif

u8  *h_flash(void) { return flash; }
/* 0: this firmware's NV emptied (a reset); 1: on a network of its own (joined under this
 * firmware: network information that is not the factory copy's) */
void h_own(int info) {
    memset(own, 0, sizeof(own));
    own[0].there = info;
    own[0].len = sizeof(zb_info_t);
    own_writes = 0;
}
int  h_own_info(void) { return own[0].there; }
int  h_own_writes(void) { return own_writes; }
/* the own NV's item (0 network information, 1 security record, 2 key pair): NULL if not there */
u8  *h_own_item(int s) { return own[s].there ? own[s].data : NULL; }
void h_mid(u32 m) { mid = m; }
stock_nv_diag_t *h_boot(void) {
    memset(&g_stock_nv_diag.result, 0, sizeof(g_stock_nv_diag) - 2);
    millis = 0;
    own_writes = 0;                    /* this start-up's */
#ifdef LEAVE_RESTART
    g_nv_left_network = 0;             /* RAM after a restart */
    factory_new = !own[0].there;
#endif
    stock_nv_take_network();
#ifdef LEAVE_RESTART
    factory_new = !own[0].there;       /* zb_init() then loads what the take left */
#endif
    return &g_stock_nv_diag;
}
int h_sizes(int which) { return which == 0 ? sizeof(zb_info_t) : which == 1 ? sizeof(ss_info_base_t)
                                         : sizeof(ss_dev_pair_set_t); }
int h_reboots(void) { return reboots; }
#ifdef MCU_FLASHER_DUMP
/* the debug wipe (Basic 0xff58: the magic - 0xA5) and the steering join's log record */
void h_wipe(int kind) { g_stock_nv_wipe((u8)kind); }
void h_log_join(void) { stock_nv_log(STOCK_NV_LOG_JOIN, 1, 0); }
int  h_log_stale(void) { return g_log_stale; }
#endif
"""

TAKE_FUNCTIONS = ("stock_nv_read", "stock_nv_diag_network", "stock_nv_area", "stock_nv_marker",
                  "stock_nv_marker_write", "stock_nv_take_network")
DEBUG_FUNCTIONS = ("stock_nv_log", "stock_nv_raw", "stock_nv_wipe")


class Diag(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("len", ctypes.c_uint8), ("format", ctypes.c_uint8), ("result", ctypes.c_uint8),
                ("flash_cap", ctypes.c_uint8), ("area", ctypes.c_uint8), ("found", ctypes.c_uint8),
                ("on_network", ctypes.c_uint8), ("channel", ctypes.c_uint8),
                ("pan_id", ctypes.c_uint16), ("nwk_addr", ctypes.c_uint16),
                ("ext_pan_id", ctypes.c_uint8 * 8)]


def leave_restart_block(text: str) -> str:
    """The HAL's left-network flag and its setter (the #ifdef LEAVE_RESTART part before the leave
    confirm)."""
    m = re.search(r"^#ifdef LEAVE_RESTART\n(?:(?!^#endif).)*?static uint8_t g_nv_left_network;"
                  r".*?^#endif\n", text, re.M | re.S)
    assert m, "the left-network flag"
    return m.group(0)


def build_take(d: Path, debug: bool = False, leave: bool = False) -> ctypes.CDLL:
    """leave: with the HAL's leave confirm (zdo_leave_confirmation_callback) and its flag."""
    text = NETWORK_C.read_text()
    names = TAKE_FUNCTIONS[:-1] + (DEBUG_FUNCTIONS if debug else ()) + TAKE_FUNCTIONS[-1:]
    parts = ([log_vars_block(text)] if debug else []) + [function_text(text, n) for n in names]
    if leave:
        parts += [leave_restart_block(text), function_text(text, "zdo_leave_confirmation_callback")]
    (d / "take.c").write_text(TAKE_GLUE.replace("TAKE_SOURCE", "\n".join(parts)))
    so = d / "take.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Werror", "-Wno-unused-function",
                    *(["-DMCU_FLASHER_DUMP"] if debug else []),
                    *(["-DLEAVE_RESTART"] if leave else []), "-I", str(SRC), str(d / "take.c"),
                    str(SRC / "zigbee" / "stock_nv.c"), str(SRC / "zigbee" / "crc32_nibble.c"),
                    "-o", str(so)], check=True)
    lib = ctypes.CDLL(str(so))
    lib.h_flash.restype = ctypes.POINTER(ctypes.c_uint8 * 0x100000)
    lib.h_boot.restype = ctypes.POINTER(Diag)
    lib.h_mid.argtypes = [ctypes.c_uint32]
    lib.h_sizes.argtypes = [ctypes.c_int]
    lib.h_own_item.restype = ctypes.POINTER(ctypes.c_uint8 * 256)
    lib.h_own_item.argtypes = [ctypes.c_int]
    if debug:
        lib.h_wipe.argtypes = [ctypes.c_int]
    flash = lib.h_flash().contents
    ctypes.memset(flash, 0xFF, len(flash))
    return lib


def put_item(flash, module: int, item: int, payload: bytes, sector: int = 0) -> None:
    """One intact copy in the module's sector, as the factory firmware writes it: the sector
    header, one index entry, the item (byte-sum check) after the index (the SDK's 1 MB layout:
    the key pairs' sectors are 16 KB, their index 3 KB)."""
    keypair = module == 7
    sect = STOCK_NV_BASE + module * MODULE_BYTES + sector * (0x4000 if keypair else 0x1000)
    at = sect + (0xC00 if keypair else 0x400)
    hdr = struct.pack("<HBB", 0x5A5A, module, 0)
    idx = struct.pack("<IHBB", at, len(payload) + 8, item, 0x5A)
    item_hdr = struct.pack("<IHBB", sum(payload) & 0xFFFFFFFF, len(payload), item, 0x5A)
    for off, data in ((sect, hdr), (sect + 4, idx), (at, item_hdr + payload)):
        flash[off:off + len(data)] = data


NWK_ADDR, PAN_ID = 0x3C4D, 0x1A2B


def factory_network(lib, ss: bytes | None = None, pair: bytes | None = b"") -> None:
    """A factory NV with a network (PAN 0x1A2B, address 0x3C4D) and its security material: by
    default a unique TC link key backed by this trust centre's pair. pair None: no pair item."""
    flash = lib.h_flash().contents
    info = bytearray(lib.h_sizes(0))
    info[0], info[2] = 1, 15
    info[3:7] = struct.pack("<HH", PAN_ID, NWK_ADDR)
    info[7:15] = bytes(range(1, 9))
    put_item(flash, 0, 1, bytes(info))
    record = bytearray(lib.h_sizes(1))
    record[:SS_BYTES] = factory_ss() if ss is None else ss
    put_item(flash, 2, 4, bytes(record))
    if pair is not None:
        put_item(flash, 7, 8, pair or factory_pair())


def marker_head(lib) -> bytes:
    return bytes(lib.h_flash().contents[MARKER_ADDR:MARKER_ADDR + 32])


def log_records(lib) -> list[tuple[int, int, int, int]]:
    """The debug log after the marker: (boot, kind, a, b) each."""
    flash = lib.h_flash().contents
    recs = []
    for at in range(MARKER_ADDR + LOG_START, MARKER_ADDR + 0x1000 - 7, 8):
        boot, kind, a, b = flash[at:at + 4]
        if boot == 0xFF and kind == 0xFF:
            break
        recs.append((boot, kind, a, b))
    return recs
