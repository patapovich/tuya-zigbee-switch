"""The trust centre link key check of a router (src/zigbee/tclk_check.h) and the Update Device's
copy with the network key alone (UPDDEV_PLAIN_COPY=1 bench builds, src/telink/hal/zigbee_security.c).

A child that joins through a router unsecured gets the network key only after the router's APS
Update Device reaches the trust centre and the trust centre's APS Tunnel comes back. With a unique
trust centre link key the Telink stack sends the Update Device only APS-encrypted, and a trust
centre that no longer holds the same key drops it without a word: no child joins through the
router. The check sends nothing: after such an Update Device it waits 2 s for a tunnel, and 3
windows in a row without one make Basic 0xff5d say tc_link_key_stale (4), reported to the Basic
cluster's bindings; a tunnel or a join of the router's own ends it. Update Devices count only
while joining is open network-wide (the coordinator's broadcast Mgmt_Permit_Joining, and the
router's permit of it runs): a trust centre that does not allow joins itself (joining allowed via
one router only, a removed device's trust centre rejoins) drops them whatever the key.

Here:
- the state machine (tclk_check.c with nwk_sec.c) on the host against a model, every input, and
  with the start-up / join check of the "secure all" flag (nwk_sec_check_state) in between;
- the Telink wraps cut out of zigbee_security.c and built for the host over stand-ins: what they
  pass on, that the request is read before the stack takes its buffer over, the type swap only
  with UPDDEV_PLAIN_COPY, which Mgmt_Permit_Joining opens joining network-wide;
- the build: the wraps, the source and TCLK_CHECK only in router builds but the ZDMS16-2's, the
  define only with UPDDEV_PLAIN_COPY=1 and MCU_DUMP=1 (no release build sets it), and tc32-gcc's
  code of the wrap writes nothing without it;
- where the HAL ends it at a join of its own;
- the stub (src/stub/hal/zigbee.c and the `tclk` command) on the TS110E and a relay: the
  attribute, the window, the permit, the report, the steering join, a restart;
- the converter: the new value, the TS110E's Basic bind."""
import ctypes
import json
import os
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from client import StubProc
from conftest import Device

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
SECURITY_C = SRC / "telink" / "hal" / "zigbee_security.c"
NETWORK_C = SRC / "telink" / "hal" / "zigbee_network.c"
TELINK_DIR = SRC / "telink"
SDK_SECURITY_H = ROOT / "telink_tools" / "sdk" / "zigbee" / "ss" / "security_service.h"
TC32_GCC = ROOT / "telink_tools" / "toolchain" / "tc32" / "bin" / "tc32-elf-gcc"
TC32_OBJDUMP = ROOT / "telink_tools" / "toolchain" / "tc32" / "bin" / "tc32-elf-objdump"
BASIC = 0x0000
ATTR_NWK_SEC = 0xFF5D
UNSECURED, SECURED, REPAIRED_AT_START, REPAIRED_AT_JOIN, STALE = range(5)
WINDOW_MS = 2000
MISSES = 3
UNSECURED_JOIN, TC_REJOIN = 1, 3
UNIQUE, GLOBAL = 0, 1
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


# --- the state machine on the host ----------------------------------------------------------

# The HAL it needs: a scheduler that runs a task when the test says (Telink's would cancel a task
# scheduled again; the check never does that, which the stub's scheduler needs), and the report.
HARNESS = r"""
#include <stdint.h>
#include <stddef.h>
#include "hal/tasks.h"
#include "hal/zigbee.h"
#include "zigbee/nwk_sec.h"
#include "zigbee/tclk_check.h"
static hal_task_t *sched;
static uint32_t    sched_delay;
static int         schedules, double_schedules;
void hal_tasks_init(hal_task_t *t) { (void)t; }
void hal_tasks_schedule(hal_task_t *t, uint32_t d) {
    if (sched == t) double_schedules++;
    sched = t; sched_delay = d; schedules++;
}
void hal_tasks_unschedule(hal_task_t *t) { if (sched == t) sched = NULL; }
static int      reports;
static uint8_t  r_ep, r_type, r_len, r_value, r_ptr_ok;
static uint16_t r_cluster, r_attr;
hal_zigbee_status_t hal_zigbee_send_report_attr(uint8_t ep, uint16_t cl, uint16_t attr,
                                                uint8_t type, const void *v, uint8_t len) {
    reports++; r_ep = ep; r_cluster = cl; r_attr = attr; r_type = type; r_len = len;
    r_value = *(const uint8_t *)v; r_ptr_ok = v == &g_nwk_sec_state;
    return HAL_ZIGBEE_OK;
}
static unsigned permitting = 1;
void h_upd(unsigned s, unsigned t) { tclk_check_update_device(s, t, permitting); }
void h_permitting(int p) { permitting = (unsigned)p; }
void h_permit(int network_wide) { tclk_check_permit_joining((unsigned)network_wide); }
void h_ok(void) { tclk_check_ok(); }
int h_fire(void) {
    hal_task_t *t = sched;
    if (!t) return 0;
    sched = NULL;
    t->handler(t->arg);
    return 1;
}
int h_scheduled(void) { return sched ? (int)sched_delay : -1; }
int h_schedules(void) { return schedules; }
int h_double_schedules(void) { return double_schedules; }
uint8_t h_f1(uint8_t l, uint8_t s, uint8_t r) { return nwk_sec_check_state(l, s, r); }
uint8_t *h_state(void) { return &g_nwk_sec_state; }
int h_reports(void) { return reports; }
void h_last_report(uint8_t *out) {
    out[0] = r_ep; out[1] = (uint8_t)r_cluster; out[2] = (uint8_t)(r_cluster >> 8);
    out[3] = (uint8_t)r_attr; out[4] = (uint8_t)(r_attr >> 8); out[5] = r_type; out[6] = r_len;
    out[7] = r_value; out[8] = r_ptr_ok;
}
"""


@pytest.fixture
def sm(tmp_path):
    """A fresh build per test: the state machine's statics start at zero, as at a start-up; then
    joining opened network-wide (the coordinator's broadcast), and the router permits."""
    (tmp_path / "h.c").write_text(HARNESS)
    so = tmp_path / "tclk.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Werror", "-DHAL_STUB", "-I", str(SRC),
                    str(SRC / "zigbee" / "tclk_check.c"), str(SRC / "zigbee" / "nwk_sec.c"),
                    str(tmp_path / "h.c"), "-o", str(so)], check=True)
    lib = ctypes.CDLL(str(so))
    lib.h_upd.argtypes = [ctypes.c_uint, ctypes.c_uint]
    lib.h_f1.argtypes = [ctypes.c_uint8] * 3
    lib.h_f1.restype = ctypes.c_uint8
    lib.h_state.restype = ctypes.POINTER(ctypes.c_uint8)
    lib.h_last_report.argtypes = [ctypes.c_char_p]
    lib.h_permit(1)
    return lib


def state(lib) -> int:
    return lib.h_state().contents.value


def last_report(lib) -> dict:
    buf = ctypes.create_string_buffer(9)
    lib.h_last_report(buf)
    b = buf.raw
    return {"ep": b[0], "cluster": b[1] | b[2] << 8, "attr": b[3] | b[4] << 8, "type": b[5],
            "len": b[6], "value": b[7], "points_at_the_state": b[8]}


class Model:
    """tclk_check.h as it describes itself, with nwk_sec_check_state."""

    def __init__(self) -> None:
        self.waiting = False
        self.misses = 0
        self.before = None
        self.state = SECURED
        self.scheduled = False
        self.reports: list[int] = []
        self.network_wide = True                # as the fixture leaves it
        self.permitting = True

    def upd(self, status: int, key_type: int) -> None:
        if key_type == GLOBAL or status not in (UNSECURED_JOIN, TC_REJOIN) or self.waiting:
            return
        if not (self.permitting and self.network_wide):
            return
        self.waiting = self.scheduled = True

    def permit(self, network_wide: int) -> None:
        self.network_wide = bool(network_wide)

    def fire(self) -> None:
        self.scheduled = self.waiting = False
        self.misses = (self.misses + 1) & 0xFF
        if (self.misses - 1) & 0xFF >= MISSES - 1 and self.state != STALE:
            self.before, self.state = self.state, STALE
            self.reports.append(STALE)

    def ok(self) -> None:
        self.scheduled = self.waiting = False
        self.misses = 0
        if self.state == STALE:
            self.state = self.before
            self.reports.append(self.state)

    def f1(self, level: int, secure_all: int, repaired: int) -> None:
        if level == 0:
            self.state = UNSECURED
        elif secure_all:
            self.state = SECURED if self.state == UNSECURED else self.state
        else:
            self.state = repaired


def miss(lib, status: int = UNSECURED_JOIN, key_type: int = UNIQUE) -> None:
    lib.h_upd(status, key_type)
    assert lib.h_scheduled() == WINDOW_MS
    assert lib.h_fire() == 1


def test_three_windows_without_a_tunnel_make_it_stale_and_report_it(sm):
    miss(sm)
    miss(sm, TC_REJOIN)
    assert state(sm) == SECURED and sm.h_reports() == 0
    miss(sm)
    assert state(sm) == STALE and sm.h_reports() == 1
    assert last_report(sm) == {"ep": 1, "cluster": BASIC, "attr": ATTR_NWK_SEC, "type": 0x30,
                               "len": 1, "value": STALE, "points_at_the_state": 1}
    sm.h_ok()                                   # the trust centre answers again
    assert state(sm) == SECURED and sm.h_reports() == 2 and last_report(sm)["value"] == SECURED
    assert sm.h_scheduled() == -1


def test_a_tunnel_in_the_window_is_no_miss(sm):
    for _ in range(20):
        sm.h_upd(UNSECURED_JOIN, UNIQUE)
        sm.h_ok()
        assert sm.h_scheduled() == -1 and sm.h_fire() == 0
    assert state(sm) == SECURED and sm.h_reports() == 0


def test_misses_count_in_a_row_only(sm):
    for _ in range(5):
        miss(sm)
        miss(sm)
        sm.h_ok()
    assert state(sm) == SECURED
    miss(sm)
    miss(sm)
    miss(sm)
    assert state(sm) == STALE


@pytest.mark.parametrize("key_type", [GLOBAL])
def test_a_global_key_is_not_watched(sm, key_type):
    """Its Update Device goes also with the network key alone, which any trust centre reads: no
    answer then is the trust centre's choice, not the key."""
    for status in range(256):
        sm.h_upd(status, key_type)
    assert sm.h_scheduled() == -1 and sm.h_schedules() == 0


def test_only_the_joins_that_wait_for_the_key_are_watched(sm):
    for status in range(256):
        for key_type in (UNIQUE, 2, 0xFF):
            sm.h_upd(status, key_type)
            watched = sm.h_scheduled() != -1
            assert watched == (status in (UNSECURED_JOIN, TC_REJOIN)), (status, key_type)
            sm.h_ok()


def test_update_devices_in_one_window_are_one_miss(sm):
    sm.h_upd(UNSECURED_JOIN, UNIQUE)
    for status in (UNSECURED_JOIN, TC_REJOIN, UNSECURED_JOIN):
        sm.h_upd(status, UNIQUE)
    assert sm.h_schedules() == 1 and sm.h_double_schedules() == 0
    assert sm.h_fire() == 1 and sm.h_fire() == 0
    miss(sm)
    assert state(sm) == SECURED                 # 2 windows, not 5 Update Devices


def test_stale_is_told_once_and_stays_through_any_number_of_misses(sm):
    for _ in range(300):                        # past the count's 8 bits
        miss(sm)
    assert state(sm) == STALE and sm.h_reports() == 1
    sm.h_ok()
    assert state(sm) == SECURED and sm.h_reports() == 2


def test_it_stands_in_for_a_repair_and_gives_it_back(sm):
    assert sm.h_f1(5, 0, REPAIRED_AT_START) == 1          # a start-up that repaired the flag
    for _ in range(MISSES):
        miss(sm)
    assert state(sm) == STALE
    assert sm.h_f1(5, 1, REPAIRED_AT_JOIN) == 0           # a rejoin: the flag is there
    assert state(sm) == STALE
    sm.h_ok()
    assert state(sm) == REPAIRED_AT_START


def test_a_repair_at_a_join_while_stale_is_told_and_the_next_miss_tells_stale_again(sm):
    for _ in range(MISSES):
        miss(sm)
    sm.h_f1(5, 0, REPAIRED_AT_JOIN)
    assert state(sm) == REPAIRED_AT_JOIN
    miss(sm)
    assert state(sm) == STALE and sm.h_reports() == 2
    sm.h_ok()
    assert state(sm) == REPAIRED_AT_JOIN


def test_ok_without_stale_tells_nothing(sm):
    miss(sm)
    sm.h_ok()
    sm.h_ok()
    assert sm.h_reports() == 0 and state(sm) == SECURED


def not_watched(lib) -> None:
    for status in (UNSECURED_JOIN, TC_REJOIN):
        lib.h_upd(status, UNIQUE)
        assert lib.h_scheduled() == -1, status


def test_nothing_is_watched_until_joining_is_open_network_wide(sm):
    """At a start-up the last Mgmt_Permit_Joining is not known: nothing is watched until the
    coordinator's broadcast opens joining."""
    sm.h_permit(0)                              # as at a start-up (the fixture opened it)
    for _ in range(2 * MISSES):
        not_watched(sm)
    assert state(sm) == SECURED and sm.h_schedules() == 0
    sm.h_permit(1)
    miss(sm)
    assert sm.h_schedules() == 1


def test_joining_via_this_router_alone_is_not_watched(sm):
    """Zigbee2MQTT's "permit join" via one device sends the request to that router alone: the
    coordinator does not permit itself, and its trust centre drops the Update Devices whatever the
    key. No misses then: the two before stay two."""
    miss(sm)
    miss(sm)
    sm.h_permit(0)                              # permit via this router alone
    for _ in range(5 * MISSES):
        not_watched(sm)
    assert state(sm) == SECURED and sm.h_reports() == 0
    sm.h_permit(1)
    miss(sm)
    assert state(sm) == STALE


def test_update_devices_after_the_permit_ran_out_are_not_watched(sm):
    """A removed device's trust centre rejoins through the router go on whether anything permits
    or not; once the router's permit (the broadcast's time) has run out, they are not watched."""
    sm.h_permitting(0)
    for _ in range(5 * MISSES):
        not_watched(sm)
    assert state(sm) == SECURED and sm.h_schedules() == 0
    sm.h_permitting(1)                          # the coordinator opens joining again
    for _ in range(MISSES):
        miss(sm)
    assert state(sm) == STALE


def test_a_close_while_one_waits_still_ends_that_window_as_a_miss(sm):
    """The window started while joining was open: it ends as it would (a miss, unless a tunnel
    comes)."""
    miss(sm)
    miss(sm)
    sm.h_upd(UNSECURED_JOIN, UNIQUE)
    sm.h_permit(0)
    assert sm.h_fire() == 1 and state(sm) == STALE


@pytest.mark.parametrize("seed", range(8))
def test_it_follows_the_model_for_any_sequence(sm, seed):
    rnd = random.Random(seed)
    model = Model()
    for step in range(3000):
        op = rnd.random()
        if op < 0.40:
            status = rnd.choice([0, 1, 1, 2, 3, 3, 0xFF, 5])
            key_type = rnd.choice([0, 0, 0, 1, 2, 0xFF])
            sm.h_upd(status, key_type)
            model.upd(status, key_type)
        elif op < 0.45:
            if rnd.random() < 0.5:
                network_wide = rnd.choice([0, 1, 1, 2, 0xFF])
                sm.h_permit(network_wide)
                model.permit(network_wide)
            else:
                permitting = rnd.choice([0, 1, 1])
                sm.h_permitting(permitting)
                model.permitting = bool(permitting)
        elif op < 0.80:
            fired = sm.h_fire()
            assert fired == model.scheduled, step
            if fired:
                model.fire()
        elif op < 0.95:
            sm.h_ok()
            model.ok()
        else:
            args = (rnd.choice([0, 5, 5, 5]), rnd.choice([0, 1, 1]),
                    rnd.choice([REPAIRED_AT_START, REPAIRED_AT_JOIN]))
            sm.h_f1(*args)
            model.f1(*args)
        assert state(sm) == model.state, step
        assert (sm.h_scheduled() != -1) == model.scheduled, step
        assert sm.h_reports() == len(model.reports), step
        if model.reports:
            assert last_report(sm)["value"] == model.reports[-1], step
    assert sm.h_double_schedules() == 0


# --- the Telink wraps on the host -------------------------------------------------------------

# Stand-ins: the request as security_service.h has it (the stack's buffer), the security
# record's tcLinkKeyType (byte 76: ss_apsmeUpdateDevReq reads ss_ib+0x4C), the APS indication's
# fields the permit wrap reads (aps_api.h), the MAC's association permit, and the stack's own
# functions, which log and take the request's buffer over as tl_bufInitalloc does.
WRAP_HARNESS = r"""
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
#define SS_GLOBAL_LINK_KEY 1
#define ZB_NWK_IS_ADDRESS_BROADCAST(addr) ( ((addr) & 0xFFF8) == 0xFFF8 )
typedef struct { u8 dstAddr[8]; u8 devAddr[8]; u16 devShortAddr; u8 status; } ss_apsmeUpdateDeviceReq_t;
typedef struct { u8 dst_addr_mode; u8 dst_ep; u16 dst_addr; u16 cluster_id; u8 *asdu; u16 src_short_addr; } aps_data_ind_t;
typedef struct __attribute__((packed)) { u8 head[76]; u8 tcLinkKeyType; u8 tail[4]; } ss_info_base_t;
typedef char ss_info_base_is_81_bytes[sizeof(ss_info_base_t) == 81 ? 1 : -1];
ss_info_base_t ss_ib;
static int log_n;
static int log_v[16][3];
static void *seen;
static void lg(int a, int b, int c) {
    log_v[log_n][0] = a; log_v[log_n][1] = b; log_v[log_n][2] = c; log_n++;
}
static int assoc_permit;
int zb_getMacAssocPermit(void) { return assoc_permit; }
void tclk_check_update_device(unsigned s, unsigned t, unsigned p) { lg('c', (int)s, (int)t); lg('p', (int)p, 0); }
void tclk_check_permit_joining(unsigned nw) { lg('j', (int)nw, 0); }
void tclk_check_ok(void) { lg('o', 0, 0); }
void __real_zdo_mgmtPermitJoinIndicate(void *buf) {
    lg('z', buf == seen, 0);
    memset(buf, 0xEE, sizeof(aps_data_ind_t));            /* the handler frees it */
}
void __real_ss_apsmeUpdateDevReq(void *req) {
    lg('r', ss_ib.tcLinkKeyType, req == seen);
    memset(req, 0xEE, sizeof(ss_apsmeUpdateDeviceReq_t));
}
void __real_ss_apsTunnelCmdHandle(void *buf) { lg('t', 0, buf == seen); }
WRAP_SOURCE
void h_assoc_permit(int p) { assoc_permit = p; }
void h_permit_req(u16 dst, u16 src, u8 duration) {
    static aps_data_ind_t ind;
    static u8 asdu[3];
    asdu[0] = 0x42; asdu[1] = duration; asdu[2] = 0;
    memset(&ind, 0, sizeof(ind));
    ind.dst_addr_mode = 2; ind.dst_addr = dst; ind.src_short_addr = src; ind.asdu = asdu;
    ind.cluster_id = 0x0036;
    seen = &ind;
    log_n = 0;
    __wrap_zdo_mgmtPermitJoinIndicate(&ind);
}
int h_upd(u8 status, u8 type) {
    static ss_apsmeUpdateDeviceReq_t req;
    memset(&req, 0x5A, sizeof(req));
    req.status = status;
    seen = &req;
    ss_ib.tcLinkKeyType = type;
    log_n = 0;
    __wrap_ss_apsmeUpdateDevReq(&req);
    return ss_ib.tcLinkKeyType;
}
void h_tunnel(void) { static u8 buf[64]; seen = buf; log_n = 0; __wrap_ss_apsTunnelCmdHandle(buf); }
int h_log(int *out) { memcpy(out, log_v, sizeof(log_v)); return log_n; }
"""


def build_wraps(tmp_path: Path, plain_copy: bool):
    text = SECURITY_C.read_text()
    source = "\n".join(function_text(text, n) for n in ("__wrap_ss_apsmeUpdateDevReq",
                                                        "__wrap_ss_apsTunnelCmdHandle",
                                                        "__wrap_zdo_mgmtPermitJoinIndicate"))
    d = tmp_path / ("plain" if plain_copy else "release")
    d.mkdir()
    (d / "w.c").write_text(WRAP_HARNESS.replace("WRAP_SOURCE", source))
    so = d / "w.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Werror",
                    *(["-DUPDDEV_PLAIN_COPY"] if plain_copy else []), str(d / "w.c"), "-o", str(so)],
                   check=True)
    lib = ctypes.CDLL(str(so))
    lib.h_upd.argtypes = [ctypes.c_uint8, ctypes.c_uint8]
    lib.h_permit_req.argtypes = [ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint8]
    lib.h_log.argtypes = [ctypes.POINTER(ctypes.c_int)]
    return lib


def wrap_log(lib) -> list[tuple]:
    out = (ctypes.c_int * 48)()
    n = lib.h_log(out)
    return [(chr(out[3 * i]), out[3 * i + 1], out[3 * i + 2]) for i in range(n)]


@pytest.mark.parametrize("plain_copy", [False, True], ids=["release", "UPDDEV_PLAIN_COPY"])
def test_the_update_device_wrap(tmp_path, plain_copy):
    """The stack sends it with the type as it is (release) or as global (the bench builds: also the
    copy with the network key alone), the type is back afterwards, and the check gets the
    request's status as it was before the stack took the buffer over, the real type and whether
    the router permits joining (the MAC's association permit)."""
    lib = build_wraps(tmp_path, plain_copy)
    for permit in (0, 1):
        lib.h_assoc_permit(permit)
        for key_type in range(256):
            for status in (0, 1, 2, 3, 0xFF):
                assert lib.h_upd(status, key_type) == key_type
                sent = GLOBAL if plain_copy else key_type
                assert wrap_log(lib) == [("r", sent, 1), ("c", status, key_type), ("p", permit, 0)], \
                    (status, key_type)


@pytest.mark.parametrize("dst, src, duration, network_wide", [
    (0xFFFC, 0x0000, 254, 1),       # Zigbee2MQTT's "permit join" (all): the coordinator's broadcast
    (0xFFFD, 0x0000, 60, 1),
    (0xFFFF, 0x0000, 255, 1),
    (0xFFFC, 0x0000, 0, 0),         # its close
    (0x1234, 0x0000, 254, 0),       # "permit join" via this router alone
    (0xFFFC, 0x4321, 180, 0),       # another router's broadcast (after its own join)
    (0x0000, 0x4321, 180, 0),
], ids=["all", "rx_on", "all_ffff", "close", "this_router", "other_router", "other_unicast"])
def test_the_permit_wrap_tells_whether_joining_is_open_network_wide(tmp_path, dst, src, duration,
                                                                    network_wide):
    """Read before the stack's handler, which gets the buffer as it was and frees it."""
    lib = build_wraps(tmp_path, False)
    lib.h_permit_req(dst, src, duration)
    assert wrap_log(lib) == [("j", network_wide, 0), ("z", 1, 0)]


def test_the_tunnel_wrap_tells_the_check_and_passes_the_buffer_on(tmp_path):
    lib = build_wraps(tmp_path, False)
    lib.h_tunnel()
    assert wrap_log(lib) == [("o", 0, 0), ("t", 0, 1)]


@pytest.mark.skipif(not SDK_SECURITY_H.is_file(), reason="no SDK checkout at telink_tools/sdk")
def test_the_stand_in_request_has_the_sdks_layout():
    """The wrap reads the status by the SDK's struct (a static assert holds it at byte 18, where
    ss_apsmeUpdateDevReqSend and ss_zdoChildAuthStart of libzb_router.a have it)."""
    text = SDK_SECURITY_H.read_text()
    m = re.search(r"typedef struct\s*\{([^{}]*)\}\s*ss_apsmeUpdateDeviceReq_t;", text)
    assert m
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", m.group(1), flags=re.S)
    fields = [f.split() for f in body.split(";") if f.strip()]
    assert fields == [["addrExt_t", "dstAddr"], ["addrExt_t", "devAddr"], ["u16", "devShortAddr"],
                      ["u8", "status"]]
    assert re.search(r"offsetof\(ss_apsmeUpdateDeviceReq_t, status\) == 18", SECURITY_C.read_text())


SDK_APS_API_H = ROOT / "telink_tools" / "sdk" / "zigbee" / "aps" / "aps_api.h"
SDK_ZDP_C = ROOT / "telink_tools" / "sdk" / "zigbee" / "zdo" / "zdp.c"
ZDP_PATCH = TELINK_DIR / "patch_sdk" / "zigbee" / "zdo" / "zdp.c.patch"


def patched_zdp_text(tmp_path: Path) -> str:
    """zdp.c as the image compiles it: the SDK's, its CRs dropped, with patch_sdk's patch
    applied (sdk.mk SDK_PATCHED)."""
    orig = tmp_path / "zdp.c.orig"
    orig.write_bytes(SDK_ZDP_C.read_bytes().replace(b"\r", b""))
    with ZDP_PATCH.open("rb") as p:
        return subprocess.run(["patch", "-s", "-o", "-", str(orig)], stdin=p, check=True,
                              capture_output=True).stdout.decode("latin-1")


@pytest.mark.skipif(not SDK_APS_API_H.is_file(), reason="no SDK checkout at telink_tools/sdk")
def test_the_permit_wrap_reads_the_sdks_indication(tmp_path):
    """The fields the wrap reads are the SDK's aps_data_ind_t's; the ZDP payload starts with the
    sequence number (zdp.c reads it as asdu[0]), so the permit time is asdu[1]; zdp.c's table is
    where the library's handler is called from (the wrap reaches it): zdp.c as the image has it,
    patched (test_mgmt_rtg_rsp.py: the row also in the compiled table of each router build)."""
    text = SDK_APS_API_H.read_text()
    m = re.search(r"typedef struct\s*\{(.*?)\}\s*aps_data_ind_t;", text, re.S)
    assert m
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", m.group(1), flags=re.S)
    names = re.findall(r"\b(\w+)\s*(?:\[\d+\])?;", body)
    for name in ("dst_addr_mode", "dst_addr", "asdu", "src_short_addr"):
        assert name in names, name
    zdp = patched_zdp_text(tmp_path)
    assert "zdpInd.seq_num = p->asdu[0];" in zdp
    assert re.search(r"\{MGMT_PERMIT_JOINING_REQ_CLID,\s*FALSE,\s*zdo_mgmtPermitJoinIndicate\}", zdp)
    wrap = function_text(SECURITY_C.read_text(), "__wrap_zdo_mgmtPermitJoinIndicate")
    assert "ind->asdu[1]" in wrap and wrap.index("tclk_check_permit_joining") < wrap.index("__real_")


# --- the build ---------------------------------------------------------------------------------

def make_vars(**overrides) -> dict:
    """The Telink Makefile's variables as it computes them (make's database, nothing built)."""
    args = [f"{k}={v}" for k, v in overrides.items()]
    out = subprocess.run(["make", "-s", "-C", str(TELINK_DIR), "-pn", "help", *args],
                         capture_output=True, text=True, timeout=60).stdout
    got = {}
    for line in out.splitlines():
        m = re.match(r"^(LNK_FLAGS|TELINK_SOURCES|DEVICE_DEFS) :?= (.*)$", line)
        if m:
            got[m.group(1)] = m.group(2).split()
    assert set(got) == {"LNK_FLAGS", "TELINK_SOURCES", "DEVICE_DEFS"}, got.keys()
    return got


WRAPS = ["--wrap=ss_apsmeUpdateDevReq", "--wrap=ss_apsTunnelCmdHandle",
         "--wrap=zdo_mgmtPermitJoinIndicate"]


@pytest.mark.parametrize("extra, checked", [({}, True), ({"DIMMER_ONLY": "1", "HK_DIMMER": "1"}, True),
                                            ({"DIMMER_ONLY": "1", "SECONDARY_MCU_IMAGE": "1"}, False)],
                         ids=["plain", "ts110e", "zdms16_2"])
def test_routers_but_the_zdms16_2_link_the_wraps_and_end_devices_not(extra, checked):
    """The ZDMS16-2 (SECONDARY_MCU_IMAGE) leaves the check out: no room in its image."""
    router = make_vars(DEVICE_TYPE="router", **extra)
    assert [f for f in router["LNK_FLAGS"] if f.startswith("--wrap=")] == (WRAPS if checked else [])
    assert ("hal/zigbee_security.c" in router["TELINK_SOURCES"]) == checked
    assert ("-DTCLK_CHECK" in router["DEVICE_DEFS"]) == checked
    ed = make_vars(DEVICE_TYPE="end_device", **extra)
    assert not [f for f in ed["LNK_FLAGS"] if f.startswith("--wrap")]
    assert "hal/zigbee_security.c" not in ed["TELINK_SOURCES"]
    assert "-DTCLK_CHECK" not in ed["DEVICE_DEFS"]


@pytest.mark.parametrize("value, defined", [(None, False), ("0", False), ("", False), ("1", True)])
def test_the_plain_copy_only_with_upddev_plain_copy_1_and_mcu_dump_1(value, defined):
    v = make_vars(MCU_DUMP="1", **({} if value is None else {"UPDDEV_PLAIN_COPY": value}))
    assert ("-DUPDDEV_PLAIN_COPY" in v["DEVICE_DEFS"]) == defined
    assert v["LNK_FLAGS"].count("--wrap=ss_apsmeUpdateDevReq") == 1     # the check's, both ways


@pytest.mark.parametrize("how", ["argument", "environment"])
@pytest.mark.parametrize("mcu_dump", [None, "0", ""])
def test_the_plain_copy_without_mcu_dump_1_stops_the_build(how, mcu_dump):
    """A UPDDEV_PLAIN_COPY=1 left in the environment (board.mk's sub-make passes it on) must not
    go into an image named and versioned as a release: make stops."""
    args = ["make", "-s", "-C", str(TELINK_DIR), "-pn", "help"]
    env = {k: v for k, v in os.environ.items() if k not in ("MCU_DUMP", "UPDDEV_PLAIN_COPY")}
    if how == "argument":
        args.append("UPDDEV_PLAIN_COPY=1")
    else:
        env["UPDDEV_PLAIN_COPY"] = "1"
    if mcu_dump is not None:
        args.append(f"MCU_DUMP={mcu_dump}")
    res = subprocess.run(args, capture_output=True, text=True, timeout=60, env=env)
    assert res.returncode != 0
    assert "UPDDEV_PLAIN_COPY=1 is for bench builds: it needs MCU_DUMP=1" in res.stderr


def test_the_source_refuses_the_plain_copy_without_mcu_flasher_dump():
    assert re.search(r"#if defined\(UPDDEV_PLAIN_COPY\) && !defined\(MCU_FLASHER_DUMP\)\n#error ",
                     SECURITY_C.read_text())


def test_no_release_build_sets_upddev_plain_copy():
    places = [ROOT / "board.mk", *sorted((ROOT / ".github").rglob("*.yml")),
              *sorted((ROOT / "helper_scripts").rglob("*.py")),
              *sorted((ROOT / "helper_scripts").rglob("*.sh"))]
    assert [str(p.relative_to(ROOT)) for p in places if "UPDDEV_PLAIN_COPY" in p.read_text()] == []
    assert re.search(r"^UPDDEV_PLAIN_COPY \?= 0$", (TELINK_DIR / "Makefile").read_text(), re.M)


def test_the_type_is_written_only_in_the_plain_copy_branch():
    """Every write of ss_ib in zigbee_security.c sits between #ifdef UPDDEV_PLAIN_COPY and its
    #endif; the release wrap only reads."""
    inside, writes = False, []
    for n, line in enumerate(SECURITY_C.read_text().splitlines(), 1):
        s = line.strip()
        if s.startswith("#ifdef UPDDEV_PLAIN_COPY"):
            inside = True
        elif s.startswith("#endif") or s.startswith("#else"):
            inside = False
        elif re.search(r"\bss_ib\.\w+\s*=[^=]", s):
            writes.append((n, inside))
    assert writes and all(inside for _, inside in writes), writes


def tc32_compile(tmp_path: Path, **overrides) -> str:
    """zigbee_security.c compiled by tc32-gcc with the Makefile's own command for it; its code."""
    args = [f"{k}={v}" for k, v in overrides.items()]
    obj = "../../build/telink/telink/hal/zigbee_security.o"
    out = subprocess.run(["make", "-s", "-C", str(TELINK_DIR), "-n", "-B", obj, *args],
                         capture_output=True, text=True, timeout=60).stdout
    cmd = [l for l in out.splitlines() if "tc32-elf-gcc" in l and " -c " in l]
    assert len(cmd) == 1, out
    o = tmp_path / ("w_" + "_".join(args or ["release"]) + ".o")
    subprocess.run(cmd[0].replace(f"-o {obj}", f"-o {o}"), shell=True, cwd=TELINK_DIR, check=True,
                   capture_output=True)
    return subprocess.run([str(TC32_OBJDUMP), "-d", str(o)], capture_output=True, text=True,
                          check=True).stdout


def disassembly_of(dump: str, name: str) -> list[str]:
    """The function's instructions, without the alignment filler (0x46c0, which objdump shows
    as a store)."""
    m = re.search(r"^[0-9a-f]+ <" + re.escape(name) + r">:\n(.*?)(?:\n\n|\Z)", dump, re.M | re.S)
    assert m, name
    return [l for l in m.group(1).splitlines() if not re.match(r"\s*[0-9a-f]+:\t46c0\s", l)]


@pytest.mark.skipif(not (TC32_GCC.is_file() and SDK_SECURITY_H.is_file()),
                    reason="no tc32 toolchain or SDK at telink_tools")
@pytest.mark.parametrize("extra", [{}, {"DIMMER_ONLY": "1"}], ids=["O2", "Os"])
def test_tc32_code_of_the_wrap_writes_nothing_without_the_flag(tmp_path, extra):
    stores = re.compile(r"\ttstore(r|rb|rh)\t")
    release = disassembly_of(tc32_compile(tmp_path, **extra), "__wrap_ss_apsmeUpdateDevReq")
    plain = disassembly_of(tc32_compile(tmp_path, UPDDEV_PLAIN_COPY=1, MCU_DUMP=1, **extra),
                           "__wrap_ss_apsmeUpdateDevReq")
    assert [l for l in release if stores.search(l)] == []
    assert len([l for l in plain if re.search(r"\ttstorerb\t", l)]) == 2      # set, put back
    for code in (release, plain):
        calls = [l.split("<")[-1].rstrip(">") for l in code if "\ttjl\t" in l]
        assert len(calls) == 3                     # real, the MAC's permit, then the check


def test_a_join_of_its_own_ends_it_in_the_hal():
    """In the SUCCESS of a commissioning, only after steering (a rejoin keeps the key), and only
    in builds with the check (TCLK_CHECK: routers but the ZDMS16-2)."""
    body = function_text(NETWORK_C.read_text(), "bdb_commissioning_callback")
    success = body[body.index("case BDB_COMMISSION_STA_SUCCESS:"):
                   body.index("case BDB_COMMISSION_STA_IN_PROGRESS:")]
    steering = success[success.index("if (steeringInProgress) {"):]
    steering = steering[:steering.index("\n        }\n")]
    assert re.search(r"#ifdef TCLK_CHECK\n(\s*/\*.*?\*/\n)?\s*tclk_check_ok\(\);\n#endif",
                     steering, re.S)
    assert body.count("tclk_check_ok()") == 1


# --- the stub ---------------------------------------------------------------------------------

# The boards with the check (the stub, built with every feature, has it on every config: the
# ZDMS16-2 build leaves it out, test_routers_but_the_zdms16_2_link_the_wraps_and_end_devices_not)
BOARDS = [(n, board(n)["config_str"], dp_config_of(board(n)))
          for n in ("MODULE_TUYA_TS110E_3MPWQZUU",)]
PERMIT_S = 254                          # Zigbee2MQTT's "permit join" time


def stub(config: str = RELAY, dp_config: str | None = None, joined: bool = True,
         permit: bool = True) -> StubProc:
    """permit: joining opened network-wide (the coordinator's broadcast, for PERMIT_S)."""
    proc = StubProc(device_config=config, dp_config=dp_config, joined=joined).start()
    if permit:
        assert proc.exec(f"tclk permit 1 {PERMIT_S}").ok
    return proc


def tclk(proc: StubProc, *args) -> int:
    res = proc.exec(" ".join(("tclk",) + tuple(str(a) for a in args)))
    assert res.ok, res
    return int(res.payload["state"])


def nwk_sec(device: Device) -> int:
    return int(device.read_zigbee_attr(1, BASIC, ATTR_NWK_SEC))


def reports(device: Device) -> list[dict]:
    """The reports of 0xff5d (a ZDMS16-2 also reports its SWD master's status)."""
    return [e.payload for e in device._events if e.kind == "zcl_report_send" and
            e.payload.get("attr") == "0xFF5D"]


def stub_miss(proc: StubProc, device: Device, status: int = UNSECURED_JOIN) -> None:
    tclk(proc, "upddev", status, UNIQUE)
    device.step_time(WINDOW_MS)
    assert proc.exec("poll 1").ok


@pytest.mark.parametrize("config, dp_config", [(c, d) for _, c, d in BOARDS] + [(RELAY, "")],
                         ids=[n for n, _, _ in BOARDS] + ["relay"])
def test_stub_three_unanswered_joins_report_stale_and_a_tunnel_ends_it(config, dp_config):
    proc = stub(config, dp_config or None)
    try:
        device = Device(proc)
        device.clear_events()
        stub_miss(proc, device)
        stub_miss(proc, device, TC_REJOIN)
        assert nwk_sec(device) == SECURED and reports(device) == []
        stub_miss(proc, device)
        assert nwk_sec(device) == STALE
        assert reports(device) == [{"ep": "1", "cluster": "0x0000", "attr": "0xFF5D"}]
        assert tclk(proc, "tunnel") == SECURED
        assert nwk_sec(device) == SECURED and len(reports(device)) == 2
    finally:
        proc.stop()


def test_stub_the_window_is_2_s_and_a_tunnel_in_it_is_no_miss():
    proc = stub()
    try:
        device = Device(proc)
        for _ in range(MISSES + 1):              # answered each time, at the last moment
            tclk(proc, "upddev", UNSECURED_JOIN, UNIQUE)
            device.step_time(WINDOW_MS - 1)
            assert proc.exec("poll 1").ok
            tclk(proc, "tunnel")
            device.step_time(1)
            assert proc.exec("poll 1").ok
        assert nwk_sec(device) == SECURED
        for _ in range(MISSES - 1):
            stub_miss(proc, device)
        tclk(proc, "upddev", UNSECURED_JOIN, UNIQUE)
        device.step_time(WINDOW_MS - 1)
        assert proc.exec("poll 1").ok and nwk_sec(device) == SECURED
        device.step_time(1)
        assert proc.exec("poll 1").ok and nwk_sec(device) == STALE
    finally:
        proc.stop()


def test_stub_a_global_key_a_secured_rejoin_and_a_leave_are_not_watched():
    proc = stub()
    try:
        device = Device(proc)
        for _ in range(2 * MISSES):
            for status, key_type in ((UNSECURED_JOIN, GLOBAL), (TC_REJOIN, GLOBAL), (0, UNIQUE),
                                     (2, UNIQUE)):
                tclk(proc, "upddev", status, key_type)
            device.step_time(WINDOW_MS)
            assert proc.exec("poll 1").ok
        assert nwk_sec(device) == SECURED
    finally:
        proc.stop()


def test_stub_a_steering_join_ends_it_and_a_restart_starts_over():
    proc = stub()
    try:
        device = Device(proc)
        for _ in range(MISSES):
            stub_miss(proc, device)
        assert nwk_sec(device) == STALE
        device.set_network(0)
        assert proc.exec("poll 1").ok
        assert device.status().get("joined") == "2"   # the app steers
        device.set_network(1)                    # joined by steering: a new key
        assert nwk_sec(device) == SECURED
        for _ in range(MISSES):
            stub_miss(proc, device)
        assert nwk_sec(device) == STALE
    finally:
        proc.stop()
    proc = stub()
    try:
        assert nwk_sec(Device(proc)) == SECURED  # RAM only
    finally:
        proc.stop()


def test_stub_a_join_without_steering_keeps_it():
    """The Telink HAL ends it only after steering: a rejoin (commissioning SUCCESS without
    steering) keeps the key. In the stub: back on the network before the app has steered."""
    proc = stub()
    try:
        device = Device(proc)
        for _ in range(MISSES):
            stub_miss(proc, device)
        assert proc.exec("hold_polls 1").ok
        device.set_network(0)
        device.set_network(1)
        assert proc.exec("hold_polls 0").ok
        assert nwk_sec(device) == STALE
    finally:
        proc.stop()


def test_stub_stale_gives_back_a_repair_at_start():
    proc = stub()
    assert proc.exec("ssib nv 35").ok            # a record without "secure all"
    proc.stop()
    proc = stub()
    try:
        device = Device(proc)
        assert nwk_sec(device) == REPAIRED_AT_START
        for _ in range(MISSES):
            stub_miss(proc, device)
        assert nwk_sec(device) == STALE
        assert tclk(proc, "tunnel") == REPAIRED_AT_START
    finally:
        proc.stop()


def test_stub_tclk_command_usage():
    proc = stub()
    try:
        for bad in ("tclk", "tclk upddev 1", "tclk upddev x 0", "tclk upddev 1 256", "tclk tunnel 1",
                    "tclk other", "tclk permit 1", "tclk permit 2 10", "tclk permit 1 256",
                    "tclk permit x 10", "tclk permit 1 10 0"):
            assert not proc.exec(bad).ok, bad
    finally:
        proc.stop()


def stub_not_watched(proc: StubProc, device: Device, status: int = UNSECURED_JOIN) -> None:
    """An Update Device and its window, unanswered (a miss only where it is watched)."""
    stub_miss(proc, device, status)


@pytest.mark.parametrize("how", ["start_up", "this_router", "closed", "ran_out"])
def test_stub_unanswered_joins_count_only_while_joining_is_open_network_wide(how):
    """At a start-up (nothing known), with joining allowed via this router alone, after the
    coordinator's close, and once the router's permit has run out (a removed device's trust
    centre rejoins go on): no misses, however many. The coordinator's broadcast again: 3 make it
    stale."""
    proc = stub(permit=how != "start_up")
    try:
        device = Device(proc)
        if how == "this_router":
            tclk(proc, "permit", 0, PERMIT_S)
        elif how == "closed":
            tclk(proc, "permit", 1, 0)
        elif how == "ran_out":
            device.step_time(PERMIT_S * 1000)
            assert proc.exec("poll 1").ok
        device.clear_events()
        for _ in range(3 * MISSES):
            stub_not_watched(proc, device)
            stub_not_watched(proc, device, TC_REJOIN)
        assert nwk_sec(device) == SECURED and reports(device) == []
        tclk(proc, "permit", 1, PERMIT_S)
        for _ in range(MISSES):
            stub_miss(proc, device, TC_REJOIN)
        assert nwk_sec(device) == STALE and len(reports(device)) == 1
    finally:
        proc.stop()


@pytest.mark.parametrize("late", [0, 1])
def test_stub_the_permit_runs_for_its_time(late):
    """The third Update Device 1 ms before the router's permit ends is watched, one at its end
    is not."""
    proc = stub()
    try:
        device = Device(proc)
        device.step_time(PERMIT_S * 1000 - (MISSES - 1) * WINDOW_MS - 1 + late)
        assert proc.exec("poll 1").ok
        for _ in range(MISSES):
            stub_miss(proc, device)
        assert nwk_sec(device) == (SECURED if late else STALE)
    finally:
        proc.stop()


# --- the converter -----------------------------------------------------------------------------

CONVERTER = """
const ext = (d) => d.extend.find((x) => !x.fake && (x.exposes ?? []).some(
    (e) => typeof e !== "function" && e.name === "nwk_command_security"));
"""


@pytest.mark.parametrize("model", ["ZDMS16-2", "TS110E_2gang_2"])
def test_the_converter_shows_stale_from_a_report(model):
    from generated_converter import run_node
    out = json.loads(run_node(CONVERTER + f"""
const def = definitions.find((d) => d.model === "{model}");
const fz = ext(def).fromZigbee;
const conv = (type, data) => fz.filter((f) => f.type.includes(type))
    .map((f) => f.convert({{}}, {{endpoint: {{ID: 1}}, data, device: h.device()}}, () => {{}}, {{}}, {{}}))
    .filter((r) => r !== undefined);
const expose = ext(def).exposes.find((e) => e.name === "nwk_command_security");
console.log(JSON.stringify({{
    report: conv("attributeReport", {{[0xff5d]: 4}}), read: conv("readResponse", {{[0xff5d]: 4}}),
    beyond: conv("attributeReport", {{[0xff5d]: 5}}), values: expose.values,
    description: expose.description,
}}));
"""))
    assert out["report"] == out["read"] == [{"nwk_command_security": "tc_link_key_stale"}]
    assert out["beyond"] == []
    assert out["values"] == ["unsecured", "secured", "repaired_at_start", "repaired_at_join",
                             "tc_link_key_stale"]
    if model == "ZDMS16-2":
        # its build has no check: the value stays (the entity's options), the text leaves it out
        assert "tc_link_key_stale" not in out["description"]
    else:
        text = out["description"]
        assert ("tc_link_key_stale: while joining was open to the whole network (not via one "
                "device), the coordinator answered none of the last 3 devices" in text)
        assert text.rstrip(".").endswith("re-pair the module")       # the last step
