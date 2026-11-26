"""Whether the module's own NWK commands are sent NWK-secured (src/zigbee/nwk_sec.h), and the
self-heal of the stack's "secure all" flag.

The Telink stack secures its own NWK commands (link status, route request / reply / record,
network status, leave) only while ss_ib.secureAllFresh is set; data frames look at the security
level alone. It sets the flag only at a factory-new start-up and otherwise restores the security
record from NV as it is, so a record saved once with the flag 0 kept the module's NWK commands
unsecured through every restart, leave and join, and routers of this firmware drop those. The
HAL now checks the flag after bdb_init() and after every join or rejoin, puts it back and saves
the record; Basic 0xff5d tells what it found.

Here:
- the decision (nwk_sec_check_state) on the host, every input;
- the Telink HAL's check and the debug builds' clear (telink/hal/zigbee_network.c), cut out and
  built for the host over a stand-in of the security record with the SDK's layout of byte 62:
  only the flag changes, the record is saved exactly when it has to be;
- where the HAL calls it (after the floor in bdb_init, first in a join's SUCCESS);
- the stub's model of the stack (src/stub/hal/zigbee.c): start-up, join, leave, the attribute,
  the debug builds' test hook (Basic 0xff5e), a build without the hook;
- the test hook only in debug builds (MCU_FLASHER_DUMP), in the source and in the symbols;
- Basic attribute ids unique in every build variant, and none taken on another branch."""
import ctypes
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from client import StubProc
from conftest import Device

ROOT = Path(__file__).resolve().parent.parent
NETWORK_C = ROOT / "src" / "telink" / "hal" / "zigbee_network.c"
NWK_SEC_H = ROOT / "src" / "zigbee" / "nwk_sec.h"
CONSTS_H = ROOT / "src" / "zigbee" / "consts.h"
SECURITY_H = ROOT / "telink_tools" / "sdk" / "zigbee" / "ss" / "security_service.h"
BASIC = 0x0000
ATTR_NWK_SEC = 0xFF5D
ATTR_NWK_SEC_TEST = 0xFF5E
UNSECURED, SECURED, REPAIRED_AT_START, REPAIRED_AT_JOIN = range(4)
TEST_RAM, TEST_NV, TEST_RESET = 0xA1, 0xA2, 0xA3
SEC_ALL = 0x08           # byte 62, bit 3: secureAllFresh
FRESH = 0x4D             # a factory-new start-up: level 5, the flag, slot 0, link key flag 1
JOINED = 0x5D            # ... and after a join (the network key in slot 1)
# A factory record's byte 62 taken raw (the first W4c test builds), in this SDK's order: level 5,
# flag 0, slot 3 (past the two slots), link key flag 0. The factory firmware's own reading: level
# 5, "secure all", slot 1.
FACTORY = 0x35
# The fault as it was found on a unit: after that raw take, a reset in place and a join (the join
# set the slot to 1 and kept the rest): level 5, flag 0, slot 1, link key flag 0.
FIELD = 0x15
RELAY = "A;B;RB0;"


def function_text(text: str, name: str) -> str:
    """The definition of C function `name` in `text`, from its head to its closing brace."""
    m = re.search(r"^[A-Za-z][^\n;]*\b" + name + r"\([^)]*\)\s*\{", text, re.M)
    assert m, name
    i, depth = m.end(), 1
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.start():i]


def board(name: str) -> dict:
    return yaml.safe_load((ROOT / "device_db.yaml").read_text())[name]


def dp_config_of(b: dict) -> str:
    return "".join(a["dp"] + a["type"] + a["attr"] + ";" for a in b.get("dp_attributes") or [])


# --- the decision ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def decision(tmp_path_factory):
    """nwk_sec_check_state() (inline in nwk_sec.h) and the state, built for the host."""
    d = tmp_path_factory.mktemp("nwk_sec")
    (d / "h.c").write_text('#include "zigbee/nwk_sec.h"\n'
                           "uint8_t h_check(uint8_t l, uint8_t s, uint8_t r) {\n"
                           "    return nwk_sec_check_state(l, s, r);\n}\n"
                           "uint8_t *h_state(void) { return &g_nwk_sec_state; }\n")
    so = d / "nwk_sec.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Werror", "-I", str(ROOT / "src"),
                    str(ROOT / "src" / "zigbee" / "nwk_sec.c"), str(d / "h.c"), "-o", str(so)],
                   check=True)
    lib = ctypes.CDLL(str(so))
    lib.h_check.restype = ctypes.c_uint8
    lib.h_check.argtypes = [ctypes.c_uint8] * 3
    lib.h_state.restype = ctypes.POINTER(ctypes.c_uint8)
    return lib


def expected(level: int, secure_all: int, repaired: int, before: int) -> tuple[int, int]:
    """(repair, state after) as nwk_sec.h describes it."""
    if level == 0:
        return 0, UNSECURED
    if secure_all:
        return 0, SECURED if before == UNSECURED else before
    return 1, repaired


def test_the_state_starts_as_secured(decision):
    assert decision.h_state().contents.value == SECURED


@pytest.mark.parametrize("repaired", [REPAIRED_AT_START, REPAIRED_AT_JOIN])
def test_the_decision_for_every_input(decision, repaired):
    state = decision.h_state()
    for before in range(4):
        for level in range(8):
            for secure_all in (0, 1):
                state.contents.value = before
                got = decision.h_check(level, secure_all, repaired)
                assert (got, state.contents.value) == expected(level, secure_all, repaired, before), \
                    (before, level, secure_all)


def test_a_repair_stays_told_until_the_next_start_up(decision):
    state = decision.h_state()
    state.contents.value = SECURED                       # a start-up
    assert decision.h_check(5, 0, REPAIRED_AT_START) == 1 and state.contents.value == 2
    assert decision.h_check(5, 1, REPAIRED_AT_JOIN) == 0 and state.contents.value == 2   # a join
    assert decision.h_check(5, 0, REPAIRED_AT_JOIN) == 1 and state.contents.value == 3   # lost again
    assert decision.h_check(5, 1, REPAIRED_AT_JOIN) == 0 and state.contents.value == 3


# --- the Telink HAL's check, on the host ------------------------------------------------------

# What the cut-out HAL code needs: the security record (ss_info_base_t, 81 bytes on the TLSR8258:
# byte 62 after 3 u32, a pointer, 2 x 18 bytes of key material, a u16 and an 8-byte address; the
# bit fields as security_service.h has them), the factory-new state and the save.
GLUE = r"""
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "zigbee/nwk_sec.h"
typedef uint8_t u8;
typedef struct __attribute__((packed)) {
    u8 head[62];
    u8 securityLevel:3;
    u8 secureAllFresh:1;
    u8 activeSecureMaterialIndex:2;
    u8 reserved:2;
    u8 activeKeySeqNum;
    u8 tail[17];
} ss_info_base_t;
typedef char ss_info_base_is_81_bytes[sizeof(ss_info_base_t) == 81 ? 1 : -1];
enum { SS_GLB_LK_PRECONF = 0, SS_GLB_LK_TC_DEFAULT = 1, SS_GLB_LK_MASTER = 2 };
ss_info_base_t ss_ib;
static bool factory_new;
static int  saves, key_fixes, key_fix_arg;
static u8   saved[81];
bool zb_isDeviceFactoryNew(void) { return factory_new; }
void zdo_ssInfoSaveToFlash(void) { saves++; memcpy(saved, &ss_ib, sizeof(ss_ib)); }
/* the SDK's: points the trust centre link key by the flag in bits 6-7 (the record as it is then) */
static u8 fixed_with[81];
void bdb_globalLinkKeyFix(bool fn) { key_fixes++; key_fix_arg = fn; memcpy(fixed_with, &ss_ib, 81); }
GLUE_SOURCE
uint8_t g_nwk_sec_state = NWK_SEC_SECURED;
void h_check(u8 repaired) { nwk_sec_check(repaired); }
void h_clear(u8 in_nv) { hal_zigbee_nwk_sec_clear(in_nv); }
void h_set(const u8 *rec, int fn) { memcpy(&ss_ib, rec, 81); factory_new = fn; saves = key_fixes = 0; key_fix_arg = -1; }
int h_key_fixes(void) { return key_fixes; }
int h_key_fix_arg(void) { return key_fix_arg; }
const u8 *h_fixed_with(void) { return fixed_with; }
const u8 *h_rec(void) { return (const u8 *)&ss_ib; }
const u8 *h_saved(void) { return saved; }
int h_saves(void) { return saves; }
uint8_t *h_state(void) { return &g_nwk_sec_state; }
"""


def debug_block(text: str, name: str) -> str:
    """The definition of `name`, which must be in a part only MCU_FLASHER_DUMP builds have."""
    body = function_text(text, name)
    first = text[:text.index(body)].count("\n") + 1
    lines = debug_only_lines(text)[first - 1:first - 1 + body.count("\n") + 1]
    assert all(debug for _, _, debug in lines), f"{name} outside #ifdef MCU_FLASHER_DUMP"
    return body


@pytest.fixture(scope="module")
def glue(tmp_path_factory):
    text = NETWORK_C.read_text()
    source = "\n".join([byte_62_block(text), function_text(text, "nwk_sec_check"),
                        debug_block(text, "hal_zigbee_nwk_sec_clear")])
    d = tmp_path_factory.mktemp("glue")
    (d / "glue.c").write_text(GLUE.replace("GLUE_SOURCE", source))
    so = d / "glue.so"
    # nwk_sec.c is not linked: the state is the harness's (nwk_sec.c's hook needs the HAL)
    subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Werror", "-DMCU_FLASHER_DUMP",
                    "-I", str(ROOT / "src"), str(d / "glue.c"), "-o", str(so)], check=True)
    lib = ctypes.CDLL(str(so))
    lib.h_check.argtypes = [ctypes.c_uint8]
    lib.h_clear.argtypes = [ctypes.c_uint8]
    lib.h_set.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.h_rec.restype = ctypes.POINTER(ctypes.c_uint8 * 81)
    lib.h_saved.restype = ctypes.POINTER(ctypes.c_uint8 * 81)
    lib.h_fixed_with.restype = ctypes.POINTER(ctypes.c_uint8 * 81)
    lib.h_state.restype = ctypes.POINTER(ctypes.c_uint8)
    return lib


def byte_62_block(text: str) -> str:
    """The HAL's declaration of bdb_globalLinkKeyFix and its byte-62 masks, up to the static
    assert that byte 62 is the bit fields."""
    m = re.search(r"^void bdb_globalLinkKeyFix\(bool isFactoryNew\);\n.*?"
                  r"^typedef char ss_byte_62_is_the_bits\[[^\n]*\n", text, re.M | re.S)
    assert m, "the byte-62 block"
    return "#include <stddef.h>\n" + m.group(0)


def repaired_byte(b62: int) -> int | None:
    """What the check makes of byte 62 (None: nothing to do): with a level, a cleared flag or a
    slot past the two is the fault; the flag set, slot 2 or 3 made 1 (the factory byte's slot),
    and a link key flag of 0 made SS_GLB_LK_TC_DEFAULT."""
    if not b62 & 7 or (b62 & SEC_ALL and not b62 & 0x20):
        return None
    b = b62 | SEC_ALL
    if b & 0x20:
        b = (b & ~0x30) | 0x10
    if not b & 0xC0:
        b |= 0x40
    return b


def record(b62: int) -> bytes:
    """A record with a pattern around byte 62, so a change anywhere else shows."""
    rec = bytearray((i * 37 + 11) & 0xFF for i in range(81))
    rec[62] = b62
    return bytes(rec)


@pytest.mark.skipif(not SECURITY_H.is_file(), reason="no SDK checkout at telink_tools/sdk")
def test_the_stand_in_has_the_sdks_byte_62():
    text = SECURITY_H.read_text()
    struct = text[:text.index("} ss_info_base_t;")]
    struct = struct[struct.rindex("typedef struct"):]
    fields = re.findall(r"^\s*(\w+\s*\*?)\s*(\w+)(\[\w+\])?(?::(\d))?;", struct, re.M)
    names = [f[1] for f in fields]
    i = names.index("securityLevel")
    assert names[i - 4:i] == ["keyPairSetNew", "nwkSecurMaterialSet", "devKeyPairNum",
                              "trust_center_address"]
    assert [(f[1], f[3]) for f in fields[i:i + 4]] == [
        ("securityLevel", "3"), ("secureAllFresh", "1"), ("activeSecureMaterialIndex", "2"),
        ("reserved", "2")]
    assert names[:3] == ["ssTimeoutPeriod", "outgoingFrameCounter", "prevOutgoingFrameCounter"]


@pytest.mark.parametrize("factory_new", [0, 1])
@pytest.mark.parametrize("repaired", [REPAIRED_AT_START, REPAIRED_AT_JOIN])
def test_the_hal_check_repairs_byte_62_only_and_saves_on_a_network(glue, factory_new, repaired):
    """Every byte 62: with a level, a cleared flag or a slot past the two is put right (the flag,
    the slot, the link key flag; nothing else of the record changes), the SDK points the link
    key by the flag then, and the record is saved unless factory new; otherwise nothing is
    written or called."""
    for b62 in range(256):
        rec = record(b62)
        glue.h_set(rec, factory_new)
        glue.h_state().contents.value = SECURED
        glue.h_check(repaired)
        after = bytes(glue.h_rec().contents)
        want = repaired_byte(b62)
        if want is not None:
            assert after == rec[:62] + bytes([want]) + rec[63:], hex(b62)
            assert (glue.h_key_fixes(), glue.h_key_fix_arg()) == (1, 0), hex(b62)
            assert bytes(glue.h_fixed_with().contents) == after, hex(b62)   # after the flag
            assert glue.h_saves() == (0 if factory_new else 1), hex(b62)
            if not factory_new:
                assert bytes(glue.h_saved().contents) == after
            assert glue.h_state().contents.value == repaired
        else:
            assert after == rec and glue.h_saves() == 0 and glue.h_key_fixes() == 0, hex(b62)
            assert glue.h_state().contents.value == (SECURED if b62 & 7 else UNSECURED)


@pytest.mark.parametrize("b62", [FIELD, FACTORY, 0x25, 0x3D, 0x05, 0x45, 0x3D | 0x40],
                         ids=["field_fault", "raw_factory_slot_3", "raw_factory_slot_2",
                              "slot_3_with_the_flag", "slot_0", "slot_0_link_key_flag_kept",
                              "slot_3_link_key_flag_kept"])
def test_the_faults_come_out_as_a_join_of_this_firmware_leaves_them(glue, b62):
    """The fault found on a unit (0x15: after the raw take, a reset in place and a join) and a raw
    factory byte still in NV (0x35: slot 3, past the two; 0x25: slot 2) come out as this
    firmware's own record after a join (0x5D: level 5, the flag, slot 1, link key flag 1), as
    does a slot 3 that kept the flag; a slot 0 or 1 stays, and a link key flag already set too."""
    glue.h_set(record(b62), 0)
    glue.h_check(REPAIRED_AT_START)
    want = {FIELD: JOINED, FACTORY: JOINED, 0x25: JOINED, 0x3D: JOINED, 0x05: FRESH, 0x45: FRESH,
            0x7D: JOINED}[b62]
    assert glue.h_rec().contents[62] == want and glue.h_saves() == 1
    assert glue.h_state().contents.value == REPAIRED_AT_START


def test_a_slot_past_the_two_is_a_fault_with_the_flag_set(glue):
    """A raw byte repaired by the flag alone (0x3D, as an earlier build of the check left it) is
    found again: its slot points past the two key slots."""
    glue.h_set(record(0x3D), 0)
    glue.h_state().contents.value = SECURED
    glue.h_check(REPAIRED_AT_JOIN)
    assert glue.h_rec().contents[62] == JOINED
    assert glue.h_state().contents.value == REPAIRED_AT_JOIN
    glue.h_check(REPAIRED_AT_START)                       # repaired: nothing again
    assert glue.h_saves() == 1 and glue.h_state().contents.value == REPAIRED_AT_JOIN


@pytest.mark.parametrize("in_nv", [0, 1])
def test_the_debug_clear_clears_only_the_flag(glue, in_nv):
    for b62 in (FRESH, 0x3D, 0xFF):
        rec = record(b62)
        glue.h_set(rec, 0)
        glue.h_clear(in_nv)
        after = bytes(glue.h_rec().contents)
        assert after == rec[:62] + bytes([b62 & ~SEC_ALL]) + rec[63:]
        assert glue.h_saves() == in_nv


# --- where the HAL calls it ------------------------------------------------------------------

def test_the_start_up_check_comes_after_bdb_init_and_the_floor():
    bdb = function_text(NETWORK_C.read_text(), "telink_zigbee_hal_bdb_init")
    assert (bdb.index("bdb_init(") < bdb.index("nwk_frame_counter_apply_floor();")
            < bdb.index("nwk_sec_check(NWK_SEC_REPAIRED_AT_START);"))


def test_a_join_or_rejoin_checks_first():
    """BDB_COMMISSION_STA_SUCCESS is what the stack tells after a join (network steering) and
    after a rejoin (bdb_zdoStartDevCnf in the idle state)."""
    cb = function_text(NETWORK_C.read_text(), "bdb_commissioning_callback")
    m = re.search(r"case BDB_COMMISSION_STA_SUCCESS:\s*\n\s*(\S[^\n]*)", cb)
    assert m and m.group(1) == "nwk_sec_check(NWK_SEC_REPAIRED_AT_JOIN);"


def test_the_check_runs_at_those_two_places_only():
    text = NETWORK_C.read_text()
    assert len(re.findall(r"^\s+nwk_sec_check\(", text, re.M)) == 2


# --- the stub's model of the stack ------------------------------------------------------------

def ssib(proc: StubProc, *args: str) -> dict:
    res = proc.exec(" ".join(("ssib",) + args))
    assert res.ok, res
    nv = res.payload["nv"]
    return {"ram": int(res.payload["ram"], 16), "nv": None if nv == "none" else int(nv, 16),
            "state": int(res.payload["state"])}


def nwk_sec(device: Device) -> int:
    return int(device.read_zigbee_attr(1, BASIC, ATTR_NWK_SEC))


def stub(config: str = RELAY, dp_config: str | None = None, joined: bool = True,
         binary: str | None = None) -> StubProc:
    return StubProc(cmd=[binary] if binary else None, device_config=config, dp_config=dp_config,
                    joined=joined).start()


BOARDS = [(n, board(n)["config_str"], dp_config_of(board(n)))
          for n in ("MODULE_AVATTO_ZDMS16_2", "MODULE_AVATTO_ZDMS16_2_TZE204",
                    "MODULE_AVATTO_ZDMS16_2_O9GYSZW2", "MODULE_TUYA_TS110E_3MPWQZUU")]


@pytest.mark.parametrize("config, dp_config", [(c, d) for _, c, d in BOARDS] + [(RELAY, "")],
                         ids=[n for n, _, _ in BOARDS] + ["relay"])
def test_the_attribute_reads_secured_and_is_read_only(config, dp_config):
    proc = stub(config, dp_config or None)
    try:
        device = Device(proc)
        assert nwk_sec(device) == SECURED
        res = proc.exec(f"zcl_write 1 0x0000 0x{ATTR_NWK_SEC:04X} 0")
        assert not res.ok and "read_only" in res.payload
        assert nwk_sec(device) == SECURED
    finally:
        proc.stop()


def test_it_is_an_enum8_on_endpoint_1_only(tmp_path):
    out = subprocess.run([os.environ.get("STUB_DEVICE", str(ROOT / "build/stub/stub_device")),
                          "--device-config", BOARDS[0][1], "--dp-config", BOARDS[0][2],
                          "--freeze-time"], input="zcl_list_attrs\nq\n", capture_output=True,
                         text=True, timeout=10, cwd=tmp_path, check=True).stdout
    found = re.findall(r"Attr 0xff5d \(type 0x([0-9a-f]{2}), size (\d+)\)", out)
    assert found == [("30", "1")]
    tables = attribute_lists(out)
    assert [ep for (ep, cl), ids in tables.items() if ATTR_NWK_SEC in ids] == [1]


@pytest.mark.parametrize("b62", [FIELD, FACTORY], ids=["field_fault", "raw_factory"])
def test_a_record_saved_without_the_flag_is_repaired_at_the_next_start_up(b62):
    """The fault as it was found (0x15: flag 0, slot 1, link key flag 0), and a raw factory byte
    still in NV (0x35: slot 3 too). The start-up repairs it to this firmware's own joined record,
    in RAM and in NV, and says so until the next start-up."""
    proc = stub()
    ssib(proc, "nv", f"{b62:02X}")
    proc.stop()
    proc = stub()
    try:
        assert ssib(proc) == {"ram": JOINED, "nv": JOINED, "state": REPAIRED_AT_START}
        assert nwk_sec(Device(proc)) == REPAIRED_AT_START
    finally:
        proc.stop()
    proc = stub()
    try:
        assert ssib(proc) == {"ram": JOINED, "nv": JOINED, "state": SECURED}
    finally:
        proc.stop()


def test_a_good_record_is_not_written_at_a_start_up():
    proc = stub()
    try:
        assert ssib(proc) == {"ram": FRESH, "nv": None, "state": SECURED}   # no record, as new
        ssib(proc, "nv", f"{JOINED:02X}")
    finally:
        proc.stop()
    proc = stub()
    try:
        assert ssib(proc) == {"ram": JOINED, "nv": JOINED, "state": SECURED}
    finally:
        proc.stop()


def test_a_factory_new_start_up_takes_the_fresh_record():
    proc = stub()
    ssib(proc, "nv", f"{FACTORY:02X}")
    proc.stop()
    proc = stub(joined=False)
    try:
        assert ssib(proc) == {"ram": FRESH, "nv": FACTORY, "state": SECURED}
    finally:
        proc.stop()


def test_the_leave_and_join_of_the_fault_repair_it_at_the_join():
    """The old path: the flag 0 in RAM, a leave that empties the NV but keeps RAM (no restart),
    then a join whose commissioning save writes RAM's record: the join's check repairs it and
    saves it again."""
    proc = stub()
    try:
        device = Device(proc)
        ssib(proc, "ram", "05")
        proc.exec("zcl_leave")
        assert ssib(proc) == {"ram": 0x05, "nv": None, "state": SECURED}   # not checked yet
        device.clear_events()
        device.set_network(1)
        assert [e.payload for e in device._events if e.kind == "nwk_sec_repaired"] == [
            {"state": "3", "ssib": f"0x{FRESH:02X}"}]
        assert ssib(proc) == {"ram": FRESH, "nv": FRESH, "state": REPAIRED_AT_JOIN}
        assert nwk_sec(device) == REPAIRED_AT_JOIN
        device.set_network(0)
        device.set_network(1)                       # a later join: the flag is there
        assert ssib(proc)["state"] == REPAIRED_AT_JOIN
    finally:
        proc.stop()


def test_a_join_with_a_good_record_says_secured():
    proc = stub(joined=False)
    try:
        device = Device(proc)
        device.set_network(1)
        assert ssib(proc) == {"ram": FRESH, "nv": FRESH, "state": SECURED}
        assert nwk_sec(device) == SECURED
    finally:
        proc.stop()


def test_a_network_without_nwk_security_is_unsecured_and_left_alone():
    proc = stub()
    ssib(proc, "nv", "00")
    proc.stop()
    proc = stub()
    try:
        assert ssib(proc) == {"ram": 0x00, "nv": 0x00, "state": UNSECURED}
        assert nwk_sec(Device(proc)) == UNSECURED
    finally:
        proc.stop()


# --- the debug builds' test hook ---------------------------------------------------------------

def test_hook_ram_clears_until_the_next_start_up():
    proc = stub()
    try:
        device = Device(proc)
        ssib(proc, "nv", f"{FRESH:02X}")
        device.write_zigbee_attr(1, BASIC, ATTR_NWK_SEC_TEST, TEST_RAM)
        assert ssib(proc) == {"ram": FRESH & ~SEC_ALL, "nv": FRESH, "state": UNSECURED}
        assert nwk_sec(device) == UNSECURED
        assert int(device.read_zigbee_attr(1, BASIC, ATTR_NWK_SEC_TEST)) == 0
    finally:
        proc.stop()
    proc = stub()
    try:
        assert ssib(proc) == {"ram": FRESH, "nv": FRESH, "state": SECURED}
    finally:
        proc.stop()


def test_hook_nv_makes_the_fault_the_next_start_up_repairs():
    proc = stub()
    try:
        device = Device(proc)
        device.write_zigbee_attr(1, BASIC, ATTR_NWK_SEC_TEST, TEST_NV)
        assert ssib(proc) == {"ram": FRESH & ~SEC_ALL, "nv": FRESH & ~SEC_ALL, "state": UNSECURED}
    finally:
        proc.stop()
    proc = stub()
    try:
        assert ssib(proc) == {"ram": FRESH, "nv": FRESH, "state": REPAIRED_AT_START}
    finally:
        proc.stop()


def test_hook_reset_leaves_a_moment_later_and_the_join_repairs_it():
    proc = stub()
    try:
        device = Device(proc)
        device.write_zigbee_attr(1, BASIC, ATTR_NWK_SEC_TEST, TEST_RESET)
        assert device.is_joined() and ssib(proc)["state"] == UNSECURED
        device.step_time(499)
        assert device.is_joined()                   # the write's answer first
        device.step_time(1)
        device.status()
        assert not device.is_joined()
        assert ssib(proc) == {"ram": FRESH & ~SEC_ALL, "nv": None, "state": UNSECURED}
        device.set_network(1)
        assert ssib(proc) == {"ram": FRESH, "nv": FRESH, "state": REPAIRED_AT_JOIN}
    finally:
        proc.stop()


@pytest.mark.parametrize("value", [0x00, 0x01, 0xA0, 0xA4, 0xFF])
def test_hook_other_values_do_nothing(value):
    proc = stub()
    try:
        device = Device(proc)
        before = ssib(proc)
        device.write_zigbee_attr(1, BASIC, ATTR_NWK_SEC_TEST, value)
        device.step_time(1000)
        device.status()
        assert ssib(proc) == before and device.is_joined()
        assert int(device.read_zigbee_attr(1, BASIC, ATTR_NWK_SEC_TEST)) == 0
    finally:
        proc.stop()


# --- the hook only in debug builds -------------------------------------------------------------

DEBUG_NAMES = re.compile(r"nwk_sec_test|NWK_SEC_TEST|hal_zigbee_nwk_sec_clear")


def debug_only_lines(text: str) -> list[tuple[int, str, bool]]:
    """(line number, line, inside a part only MCU_FLASHER_DUMP builds have) of every line."""
    stack, out = [], []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if m := re.match(r"#\s*(ifdef|ifndef|if)\b(.*)", s):
            cond = m.group(2).strip()
            kind = m.group(1)
            positive = "MCU_FLASHER_DUMP" in cond and (
                (kind == "ifdef") or (kind == "if" and not re.search(r"!\s*defined\s*\(?\s*MCU_FLASHER_DUMP", cond)
                                      and "||" not in cond))
            negative = "MCU_FLASHER_DUMP" in cond and not positive
            stack.append([positive, negative])
        elif re.match(r"#\s*elif\b", s):
            stack[-1] = [False, False]
        elif re.match(r"#\s*else\b", s):
            stack[-1] = [stack[-1][1], False]
        elif re.match(r"#\s*endif\b", s):
            stack.pop()
        out.append((n, line, any(p for p, _ in stack)))
    return out


def test_the_hook_is_only_in_debug_builds_in_the_source():
    """Every mention of the hook in src/ is in an #ifdef MCU_FLASHER_DUMP part (its attribute id
    in consts.h aside: a number, no code)."""
    bad = []
    for path in sorted((ROOT / "src").rglob("*.[ch]")):
        if path == CONSTS_H or "build" in path.parts:
            continue
        for n, line, debug in debug_only_lines(path.read_text(errors="replace")):
            if DEBUG_NAMES.search(line) and not debug and not line.lstrip().startswith(("/*", "*")):
                bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert bad == []


def test_the_debug_part_finder():
    text = ("#ifdef MCU_FLASHER_DUMP\na\n#else\nb\n#endif\n#ifndef MCU_FLASHER_DUMP\nc\n#else\nd\n"
            "#endif\n#if defined(X) && defined(MCU_FLASHER_DUMP)\ne\n#endif\n"
            "#if defined(X) || defined(MCU_FLASHER_DUMP)\nf\n#endif\n#ifdef X\ng\n#endif\n")
    debug = {line: d for _, line, d in debug_only_lines(text) if len(line) == 1}
    assert debug == {"a": True, "b": False, "c": False, "d": True, "e": True, "f": False,
                     "g": False}


NM = shutil.which("nm")


@pytest.fixture(scope="module")
def release_stub(tmp_path_factory) -> str:
    """The stub without MCU_FLASHER_DUMP (as a release build)."""
    binary = tmp_path_factory.mktemp("release") / "stub_device"
    subprocess.run(["make", "-s", "-C", str(ROOT / "src/stub"), "build", f"BINARY={binary}",
                    "EXTRA_CFLAGS=-UMCU_FLASHER_DUMP"], check=True, capture_output=True)
    return str(binary)


@pytest.mark.skipif(NM is None, reason="no host nm")
def test_the_hook_is_not_in_a_release_build(release_stub):
    def symbols(binary):
        out = subprocess.run([NM, binary], capture_output=True, text=True, check=True).stdout
        return {line.split()[-1] for line in out.splitlines() if line.strip()}
    assert {"nwk_sec_test_on_write", "g_nwk_sec_test_cmd", "hal_zigbee_nwk_sec_clear"} <= \
        symbols(str(ROOT / "build/stub/stub_device"))
    release = symbols(release_stub)
    assert "g_nwk_sec_state" in release
    assert [s for s in release if DEBUG_NAMES.search(s)] == []


def test_a_release_build_has_the_state_but_not_the_hook(release_stub):
    proc = stub(binary=release_stub)
    try:
        device = Device(proc)
        assert nwk_sec(device) == SECURED
        for cmd in (f"zcl_read 1 0x0000 0x{ATTR_NWK_SEC_TEST:04X}",
                    f"zcl_write 1 0x0000 0x{ATTR_NWK_SEC_TEST:04X} {TEST_NV}"):
            res = proc.exec(cmd)
            assert not res.ok and "attr_not_found" in res.payload
    finally:
        proc.stop()
    proc = stub(binary=release_stub)
    ssib(proc, "nv", f"{FACTORY:02X}")
    proc.stop()
    proc = stub(binary=release_stub)
    try:
        assert ssib(proc) == {"ram": JOINED, "nv": JOINED, "state": REPAIRED_AT_START}
    finally:
        proc.stop()


# --- Basic attribute ids ----------------------------------------------------------------------

# Basic ids other branches use (git grep of every branch, 2026-11): an id here must not be one of
# them unless it is that attribute (the branch's code merged). sdk-backport's restart exception
# (0xff5c) is merged here: ATTR_RESTART_EXCEPTION below.
OTHER_BRANCHES = {0xFF5A: "ZCL_ATTR_BASIC_ROUTER_DIAG_PAGE_NO",    # ROUTER_DIAG builds
                  0xFF5B: "ZCL_ATTR_BASIC_ROUTER_DIAG_PAGE"}
ATTR_RESTART_EXCEPTION = 0xFF5C


def basic_defines() -> dict[str, int]:
    return {name: int(v, 16) for name, v in
            re.findall(r"^#define (ZCL_ATTR_BASIC_\w+)\s+(0x[0-9a-fA-F]+)", CONSTS_H.read_text(), re.M)}


def test_every_basic_id_of_consts_h_is_its_own():
    """All Basic ids are in consts.h, those of every build variant (ROUTER_DIAG,
    MCU_FLASHER_DUMP, SECONDARY_MCU_IMAGE, HK_DIMMER ...): no two names share one, none is
    another branch's, and the MCU diag's log pages (a range) hit none."""
    ids = basic_defines()
    assert ids["ZCL_ATTR_BASIC_NWK_SECURITY"] == ATTR_NWK_SEC
    assert ids["ZCL_ATTR_BASIC_NWK_SEC_TEST"] == ATTR_NWK_SEC_TEST
    by_id = {}
    for name, v in ids.items():
        by_id.setdefault(v, []).append(name)
    assert {hex(v): n for v, n in by_id.items() if len(n) > 1} == {}
    assert ids["ZCL_ATTR_BASIC_RESTART_EXCEPTION"] == ATTR_RESTART_EXCEPTION
    assert {hex(v): n for n, v in ids.items() if v in OTHER_BRANCHES and OTHER_BRANCHES[v] != n} == {}
    assert [hex(v) for v in OTHER_BRANCHES if v in by_id and by_id[v] != [OTHER_BRANCHES[v]]] == []
    pages = int(re.search(r"#define TUYA_MCU_DIAG_LOG_PAGES\s+(\d+)", CONSTS_H.read_text()).group(1))
    first = ids["ZCL_ATTR_BASIC_MCU_DIAG_LOG_FIRST"]
    assert [n for n, v in ids.items() if first < v < first + pages] == []


def test_no_boards_dp_attribute_is_a_fixed_basic_id():
    """The dp attributes (dp_config: Basic 0xff00 | attr) of every board in device_db."""
    db = yaml.safe_load((ROOT / "device_db.yaml").read_text())
    fixed = set(basic_defines().values()) | set(OTHER_BRANCHES)
    hits = {name: a["attr"] for name, b in db.items() for a in b.get("dp_attributes") or []
            if 0xFF00 | int(a["attr"], 16) in fixed}
    assert hits == {}


def attribute_lists(listing: str) -> dict[tuple[int, int], list[int]]:
    """(endpoint, server cluster) -> its attribute ids in order, from zcl_list_attrs."""
    tables, key, ep = {}, None, None
    for line in listing[listing.index("=== Zigbee Attributes ==="):].splitlines():
        if m := re.match(r"Endpoint (\d+):", line):
            ep = int(m[1])
        elif m := re.match(r"\s+Cluster 0x([0-9a-f]{4}) \((server|client)\)", line):
            key = (ep, int(m[1], 16)) if m[2] == "server" else None
            if key:
                tables.setdefault(key, [])
        elif (m := re.match(r"\s+Attr 0x([0-9a-f]{4})", line)) and key:
            tables[key].append(int(m[1], 16))
    return tables


# The stub's build variants (src/stub/Makefile builds with SECONDARY_MCU_IMAGE, MCU_FLASHER_DUMP,
# HK_DIMMER, DIMMER_SCENES, DIMMER_BINDINGS). ROUTER_DIAG is Telink only: its ids are covered by
# consts.h above.
VARIANTS = {
    "debug": "",
    "release": "-UMCU_FLASHER_DUMP",
    "ts110e": "-DDIMMER_ONLY -USECONDARY_MCU_IMAGE -UMCU_FLASHER_DUMP",
    "ts110e_debug": "-DDIMMER_ONLY -USECONDARY_MCU_IMAGE",
    "plain": "-USECONDARY_MCU_IMAGE -UHK_DIMMER -UMCU_FLASHER_DUMP",
    "plain_debug": "-USECONDARY_MCU_IMAGE -UHK_DIMMER",
}


@pytest.fixture(scope="module")
def variants(tmp_path_factory) -> dict[str, str]:
    d = tmp_path_factory.mktemp("variants")

    def build(item):
        name, flags = item
        if not flags:
            return name, str(ROOT / "build/stub/stub_device")
        binary = d / name
        subprocess.run(["make", "-s", "-C", str(ROOT / "src/stub"), "build", f"BINARY={binary}",
                        f"EXTRA_CFLAGS={flags}"], check=True, capture_output=True)
        return name, str(binary)

    with ThreadPoolExecutor(len(VARIANTS)) as pool:
        return dict(pool.map(build, VARIANTS.items()))


def telink_boards() -> list[tuple[str, str, str]]:
    db = yaml.safe_load((ROOT / "device_db.yaml").read_text())
    return sorted((n, b["config_str"], dp_config_of(b)) for n, b in db.items()
                  if b.get("build", True) and b.get("mcu_family") == "Telink")


def basic_ids(binary: str, cwd: Path, config: str, dp_config: str) -> list[int]:
    cwd.mkdir(parents=True, exist_ok=True)
    args = [binary, "--device-config", config, "--freeze-time"] + (["--dp-config", dp_config] if dp_config else [])
    out = subprocess.run(args, input="zcl_list_attrs\nq\n", capture_output=True, text=True,
                         timeout=20, cwd=cwd, check=True).stdout
    return attribute_lists(out)[(1, BASIC)]


def test_basic_ids_are_unique_in_every_variant_and_board(variants, tmp_path):
    """Every Telink board on the full stub, the ZDMS16-2s, the TS110E and a relay on every
    variant: endpoint 1's Basic cluster has no id twice; 0xff5d in all, 0xff5e in the debug
    variants only, 0xff5c (the restart exception) in the ZDMS16-2 and TS110E variants only."""
    key_boards = BOARDS + [("relay", RELAY, "")]
    jobs = [("debug", *b) for b in telink_boards()]
    jobs += [(v, *b) for v in VARIANTS if v != "debug" for b in key_boards]

    def run(job):
        variant, name, config, dp_config = job
        return job, basic_ids(variants[variant], tmp_path / variant / name, config, dp_config)

    with ThreadPoolExecutor(os.cpu_count() or 4) as pool:
        results = list(pool.map(run, jobs))
    assert len(results) > 100
    dup = {f"{j[0]}:{j[1]}": sorted({hex(a) for a in ids if ids.count(a) > 1})
           for j, ids in results if len(ids) != len(set(ids))}
    assert dup == {}
    assert [f"{j[0]}:{j[1]}" for j, ids in results if ATTR_NWK_SEC not in ids] == []
    hook = {f"{j[0]}:{j[1]}" for j, ids in results if ATTR_NWK_SEC_TEST in ids}
    assert hook == {f"{j[0]}:{j[1]}" for j, _ in results if j[0] in ("debug", "ts110e_debug", "plain_debug")}
    assert [f"{j[0]}:{j[1]}" for j, ids in results if set(ids) & set(OTHER_BRANCHES)] == []
    restart = {f"{j[0]}:{j[1]}" for j, ids in results if ATTR_RESTART_EXCEPTION in ids}
    assert restart == {f"{j[0]}:{j[1]}" for j, _ in results if j[0] not in ("plain", "plain_debug")}


# --- the converter ----------------------------------------------------------------------------

CONVERTER = """
const named = (d) => d.extend.filter((x) => !x.fake).flatMap((x) => x.exposes ?? [])
    .filter((x) => typeof x !== "function" && x.name === "nwk_command_security");
const ext = (d) => d.extend.find((x) => !x.fake && (x.exposes ?? []).some(
    (e) => typeof e !== "function" && e.name === "nwk_command_security"));
"""


def test_the_converter_shows_it_on_every_zdms16_2_and_ts110e_definition():
    import json

    from generated_converter import run_node
    out = json.loads(run_node(CONVERTER + """
console.log(JSON.stringify(definitions.filter((d) => d.extend).map((d) => [d.model, named(d).map(
    (x) => ({type: x.type, access: x.access, values: x.values, category: x.category,
             endpoint: x.endpoint ?? null, label: x.label}))])));
"""))
    shown = {model for model, exposes in out if exposes}
    assert shown == {"ZDMS16-2", "TS110E_2gang_2"}          # the modules converted with W4c
    assert sorted(model for model, exposes in out if exposes) == ["TS110E_2gang_2"] + ["ZDMS16-2"] * 3
    for model, exposes in out:
        if exposes:
            assert exposes == [{"type": "enum", "access": 5, "values": [
                "unsecured", "secured", "repaired_at_start", "repaired_at_join",
                "tc_link_key_stale"],                          # test_tclk_check.py
                "category": "diagnostic", "endpoint": None, "label": "Network command security"}]


def test_the_converter_publishes_the_state_of_endpoint_1_and_reads_it_there():
    import json

    from generated_converter import run_node
    out = json.loads(run_node(CONVERTER + """
const def = definitions.find((d) => d.model === "ZDMS16-2");
const fz = ext(def).fromZigbee;
const conv = (ep, data) => fz.map((f) => f.convert({}, {endpoint: {ID: ep}, data, device: h.device()}, () => {}, {}, {}))
    .filter((r) => r !== undefined);
(async () => {
    const dev = h.device();
    h.log.length = 0;
    const tz = ext(def).toZigbee.find((t) => t.key.includes("nwk_command_security"));
    await tz.convertGet(dev.getEndpoint(2), "nwk_command_security", {device: dev, message: {}});
    console.log(JSON.stringify({
        values: [0, 1, 2, 3, 4].map((v) => conv(1, {[0xff5d]: v})),
        unknown: conv(1, {[0xff5d]: 5}), otherEndpoint: conv(2, {[0xff5d]: 2}),
        otherAttribute: conv(1, {[0xff59]: "x"}),
        types: fz.map((f) => [f.cluster, f.type]),
        get: h.log,
    }));
})();
"""))
    assert out["values"] == [[{"nwk_command_security": v}] for v in
                             ("unsecured", "secured", "repaired_at_start", "repaired_at_join",
                              "tc_link_key_stale")]
    assert out["unknown"] == out["otherEndpoint"] == out["otherAttribute"] == []
    assert out["types"] == [["genBasic", ["attributeReport", "readResponse"]]]
    assert out["get"] == [["read", 1, "genBasic", [ATTR_NWK_SEC]]]
