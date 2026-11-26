"""Attribute reports with an APS ACK (src/telink/patch_sdk/zigbee/zcl/zcl_reporting.c.patch and
hal_zigbee_send_report_attr in src/telink/hal/zigbee_zcl.c).

The SDK sent every report without one (dstEpInfo.txOptions 0), so a report lost on one hop of
the mesh (seen: an onOff report after a wall switch press, lost on the last of three hops) left
the hub's state wrong until the next change. Reports now ask for an APS ACK, which the APS layer
answers with up to APS_MAX_FRAME_RETRIES (3) resends; at most 4 frames of the node wait for an
ACK at a time (REPORT_ACKS_MAX), so a burst of level reports to a hub that does not answer
cannot fill the 16-entry APS TX cache (the library drops every new data frame then). A resend is
the frame as it was built, so a newer report of an endpoint's cluster stops the resends of that
cluster's older reports (retries 0), the gestures' (events) excepted: a late copy of an older
value cannot reach the hub after the newer one.

The stack cannot run on the host, so:
- the SDK's own zcl_reporting.c, patched as sdk.mk does it, built for the host over stand-in
  timers, attributes and APS TX cache (the SDK's own structs, cut out of its headers): every
  report path (a change, the maximum interval, several frames in one pass), the limit, and the
  stop of an older report's resends (with a model of the library's retries: lost, newer, timeout);
- hal_zigbee_send_report_attr cut out of zigbee_zcl.c, built for the host;
- the library's part (libzb_router / libzb_ed 3.7.2.0 aps_data_request, bindingTxBack): a model
  of what each destination of a binding gets (the option is kept for a unicast one, cleared for
  a group or a broadcast), pinned to the two libraries by their sha256 and, with the Telink
  toolchain, by the instructions that do it; the retries (byte 19: counted down, 0 = given up)
  and the entry's mode, endpoint and cluster as the library fills them; and the firmware's view
  of a cache entry (byte 18: used bit 0, ackNeed bit 2; bytes 12, 14, 19, 24-25) against the
  library's, compiled with tc32-gcc.
Skipped where the SDK is not checked out at telink_tools/sdk (or the toolchain is missing)."""
import ctypes
import hashlib
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "telink_tools" / "sdk"
REPORTING_C = SDK / "zigbee" / "zcl" / "zcl_reporting.c"
PATCH = ROOT / "src" / "telink" / "patch_sdk" / "zigbee" / "zcl" / "zcl_reporting.c.patch"
ZCL_HAL_C = ROOT / "src" / "telink" / "hal" / "zigbee_zcl.c"
TC32 = Path.home() / "tools/tc32/tc32/bin"

ACK_TX = 0x04                   # APS_TX_OPT_ACK_TX
BINDINGS = 0                    # APS_DSTADDR_EP_NOTPRESETNT: to the bindings of the cluster
ACKS_MAX = 4                    # REPORT_ACKS_MAX
CACHE = 16                      # APS_TX_CACHE_TABLE_NUM
ON_OFF, LEVEL, MULTISTATE, BASIC = 0x0006, 0x0008, 0x0012, 0x0000
BOOL, UINT8, UINT16 = 0x10, 0x20, 0x21

# the libraries this was read from (aps_data.o of each)
LIBS = {
    "libzb_router.a": "bc677ec60a233ba72d55e56799a6644966ca5f2b96797049a79a8f20f1f003b7",
    "libzb_ed.a": "1f77643cdba06c874abd502a8a9858537ea25770a76f1f27bf7e4557c2268f7d",
}


def function_text(text: str, name: str) -> str:
    """The definition of C function `name` in `text`, from its head (the line with its name: a
    return type on the line before is left out) to its closing brace."""
    m = re.search(r"^(?:[A-Za-z][^\n;]*\b)?" + name + r"\([^)]*\)\s*\{", text, re.M)
    assert m, name
    i, depth = m.end(), 1
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.start():i]


def sdk_text(rel: str) -> str:
    return (SDK / rel).read_bytes().decode("latin-1").replace("\r", "")


def typedef_text(text: str, name: str) -> str:
    """`typedef struct|union ... } name;` of an SDK header."""
    end = text.index("} " + name + ";") + len(name) + 3
    start = text.rfind("typedef", 0, end)
    assert text.count("{", start, end) == text.count("}", start, end), name
    return text[start:end]


def need_sdk():
    if not REPORTING_C.is_file():
        pytest.skip("no SDK checkout at telink_tools/sdk")


# --- the SDK's zcl_reporting.c on the host -----------------------------------------------------

def shim() -> str:
    """zcl_include.h for the host: the SDK's own structs and constants, cut out of its headers."""
    zcl, aps, af = sdk_text("zigbee/zcl/zcl.h"), sdk_text("zigbee/aps/aps_api.h"), \
        sdk_text("zigbee/af/zb_af.h")
    assert re.search(r"APS_TX_OPT_ACK_TX\s+= BIT\(2\)", aps)
    assert re.search(r"APS_DSTADDR_EP_NOTPRESETNT\s+= 0x00, /\* for bind.*\n\s+APS_SHORT_GROUPADDR_NOEP,"
                     r".*\n\s+APS_SHORT_DSTADDR_WITHEP,.*\n\s+APS_LONG_DSTADDR_WITHEP,", aps)
    multistate = re.search(r"^#define ZCL_CLUSTER_GEN_MULTISTATE_INPUT_BASIC\s+0x0012$",
                           sdk_text("zigbee/zcl/zcl_const.h"), re.M).group(0)
    cache_num = re.search(r"^#define APS_TX_CACHE_TABLE_NUM\s+\d+$", aps, re.M).group(0)
    send = re.search(r"^#define zcl_sendReportAttrsCmd\(.*$", zcl, re.M).group(0)
    data_types = "\n".join(re.findall(r"^#define ZCL_DATA_TYPE_\w+\s+0x[0-9a-fA-F]+$",
                                      sdk_text("zigbee/zcl/zcl_const.h"), re.M))
    structs = [typedef_text(zcl, n) for n in ("zclAttrInfo_t", "zclCfgReportRec_t", "zclReport_t",
                                              "zclReportCmd_t", "reportCfgInfo_t",
                                              "zcl_reportingTab_t")]
    return "\n".join([r"""
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int8_t s8;
typedef int16_t s16;
typedef int32_t s32;
typedef uint32_t u24;
typedef int32_t s24;
typedef u8 addrExt_t[8];
typedef int status_t;
#define TRUE 1
#define FALSE 0
#define _CODE_ZCL_
#define _attribute_packed_ __attribute__((packed))
#define DEBUG(...) ((void)0)
#define BUILD_U16(a, b) ((u16)((a) | ((b) << 8)))
#define BUILD_S16(a, b) ((s16)BUILD_U16(a, b))
#define BUILD_U24(a, b, c) ((u32)((a) | ((b) << 8) | ((c) << 16)))
#define BUILD_S24(a, b, c) ((s32)BUILD_U24(a, b, c))
#define BUILD_U32(a, b, c, d) ((u32)((a) | ((b) << 8) | ((c) << 16) | ((u32)(d) << 24)))
#define BUILD_S32(a, b, c, d) ((s32)BUILD_U32(a, b, c, d))
#define NV_SUCC 0
#define ZB_EXCEPTION_POST(e) ((void)0)
#define ZCL_REPORTING_TABLE_NUM 12          /* src/telink/configs/stack_cfg.h */
#define REPORTABLE_CHANGE_MAX_ANALOG_SIZE 8
#define ZCL_FRAME_SERVER_CLIENT_DIR 0x01
#define ZCL_SEQ_NUM 0
#define MANUFACTURER_CODE_NONE 0
#define APS_DSTADDR_EP_NOTPRESETNT 0x00
#define APS_LONG_DSTADDR_WITHEP 0x03
#define APS_TX_OPT_ACK_TX 0x04
#define TL_SETSTRUCTCONTENT(s, v) memset(&(s), (v), sizeof(s))
typedef union { u16 shortAddr; addrExt_t extAddr; } tl_zb_addr_t;
typedef int (*ev_timer_callback_t)(void *data);
""", data_types, cache_num, multistate, send, typedef_text(sdk_text("proj/os/ev_timer.h"), "ev_timer_event_t"),
        *structs, typedef_text(af, "epInfo_t"), typedef_text(aps, "aps_tx_cache_list_t"), r"""
extern aps_tx_cache_list_t aps_txCache_tbl[];
u8 zcl_reportingTab_restore(void);
zclAttrInfo_t *zcl_findAttribute(u8 endpoint, u16 clusterId, u16 attrId);
u8 zcl_getAttrSize(u8 dataType, u8 *pData);
bool zcl_analogDataType(u8 dataType);
u8 zcl_getDataTypeLen(u8 dataType);
bool zb_bindingTblSearched(u16 clusterID, u8 endpoint);
bool zb_isDeviceJoinedNwk(void);
bool ev_timer_exist(const ev_timer_event_t *evt);
void ev_on_timer(ev_timer_event_t *evt, u32 t_ms);
void ev_unon_timer(ev_timer_event_t *evt);
status_t zcl_reportAttrs(u8 srcEp, epInfo_t *pDstEpInfo, u8 disableDefaultRsp, u8 direction,
                         u8 seqNo, u16 manuCode, u16 clusterId, zclReportCmd_t *pReportAttrs);
"""])


# The rest of the stack, recorded: attributes, timers (run by the harness), the APS TX cache
# (aps_txCache_tbl, zb_config.c's) and every report handed to the APS.
HARNESS = r"""
#include "zcl_include.h"
extern zcl_reportingTab_t reportingTab;
void zcl_reportingTabInit(void);
void report_handler(void);
void zcl_reportCfgInfoEntryUpdate(reportCfgInfo_t *, u8, u16, u16, zclCfgReportRec_t *);

aps_tx_cache_list_t aps_txCache_tbl[APS_TX_CACHE_TABLE_NUM];

#define ATTRS 8
static u8 values[ATTRS][2];
static zclAttrInfo_t attrs[ATTRS];
static u8 attr_ep[ATTRS];
static u16 attr_cluster[ATTRS];
static int nattrs;

u8 zcl_reportingTab_restore(void) { return 1; }
zclAttrInfo_t *zcl_findAttribute(u8 ep, u16 cluster, u16 id) {
    for (int i = 0; i < nattrs; i++)
        if (attr_ep[i] == ep && attr_cluster[i] == cluster && attrs[i].id == id)
            return &attrs[i];
    return 0;
}
u8 zcl_getAttrSize(u8 t, u8 *d) { return t == 0x21 ? 2 : 1; }
bool zcl_analogDataType(u8 t) { return t >= 0x20 && t <= 0x2f; }
u8 zcl_getDataTypeLen(u8 t) { return t == 0x21 ? 2 : 1; }
bool zb_bindingTblSearched(u16 c, u8 e) { return 1; }
bool zb_isDeviceJoinedNwk(void) { return 1; }

#define TIMERS 32
static ev_timer_event_t *timers[TIMERS];
static u32 timer_ms[TIMERS];
bool ev_timer_exist(const ev_timer_event_t *t) {
    for (int i = 0; i < TIMERS; i++)
        if (timers[i] == t)
            return 1;
    return 0;
}
void ev_on_timer(ev_timer_event_t *t, u32 ms) {
    for (int i = 0; i < TIMERS; i++)
        if (!timers[i]) {
            timers[i] = t;
            timer_ms[i] = ms;
            return;
        }
}
void ev_unon_timer(ev_timer_event_t *t) {
    for (int i = 0; i < TIMERS; i++)
        if (timers[i] == t)
            timers[i] = 0;
}

#define SENT 64
static int sent;
static u8 sent_ep[SENT], sent_mode[SENT], sent_opts[SENT], sent_attrs[SENT], sent_val[SENT];
static u16 sent_cluster[SENT];
status_t zcl_reportAttrs(u8 ep, epInfo_t *dst, u8 ddr, u8 dir, u8 seq, u16 manu, u16 cluster,
                         zclReportCmd_t *r) {
    if (sent < SENT) {
        sent_ep[sent] = ep;
        sent_cluster[sent] = cluster;
        sent_mode[sent] = dst->dstAddrMode;
        sent_opts[sent] = dst->txOptions;
        sent_attrs[sent] = r->numAttr;
        sent_val[sent] = r->numAttr ? r->attrList[0].attrData[0] : 0;
    }
    sent++;
    return 0;
}

/* the harness */
void h_init(void) {
    memset(&reportingTab, 0, sizeof(reportingTab));
    memset(aps_txCache_tbl, 0, sizeof(aps_txCache_tbl));
    memset(timers, 0, sizeof(timers));
    memset(values, 0, sizeof(values));
    nattrs = sent = 0;
    zcl_reportingTabInit();
}
/* an attribute and its reporting configuration (min, max in s, reportable change) */
void h_attr(u8 ep, u16 cluster, u16 id, u8 type, u16 min, u16 max, u8 change) {
    int i = nattrs++;
    attrs[i].id = id;
    attrs[i].type = type;
    attrs[i].data = values[i];
    attr_ep[i] = ep;
    attr_cluster[i] = cluster;
    u8 rc[2] = { change, 0 };
    zclCfgReportRec_t rec = { 0, id, type, min, max, 0, rc };
    zcl_reportCfgInfoEntryUpdate(&reportingTab.reportCfgInfo[i], ep, 0x0104, cluster, &rec);
}
/* a change, told as hal_zigbee_notify_attribute_changed() does */
void h_set(int i, u8 v) { values[i][0] = v; report_handler(); }
/* ms on: the main loop's report_handler() (telink/main.c), then the timers due within ms (each
 * once: the SDK's callbacks return -1), in the order set */
void h_run(u32 ms) {
    ev_timer_event_t *due[TIMERS];
    int n = 0;
    report_handler();
    for (int i = 0; i < TIMERS; i++)
        if (timers[i] && timer_ms[i] <= ms) {
            due[n++] = timers[i];
            timers[i] = 0;
        }
    for (int i = 0; i < n; i++)
        due[i]->cb(due[i]->data);
}
/* APS TX cache entry i: in use, waiting for an APS ACK */
void h_cache(int i, int used, int ack) { aps_txCache_tbl[i].used = used; aps_txCache_tbl[i].ackNeed = ack; }
/* entry i as apsTxDataPost leaves it for a frame of (ep, cluster) to a destination of address
 * mode `mode` (3: a binding's IEEE address, 2: a NWK address, 1: a group), retries
 * APS_MAX_FRAME_RETRIES */
void h_frame(int i, int ack, int mode, int ep, int cluster) {
    aps_tx_cache_list_t *e = &aps_txCache_tbl[i];
    memset(e, 0, sizeof(*e));
    e->used = 1;
    e->ackNeed = ack;
    e->dstAddrMode = mode;
    e->ep = ep;
    e->clusterId = cluster;
    e->retries = 3;
}
int h_retries(int i) { return aps_txCache_tbl[i].retries; }
void h_set_retries(int i, int r) { aps_txCache_tbl[i].retries = r; }
void h_free(int i) { aps_txCache_tbl[i].used = 0; }
int h_sent_val(int i) { return sent_val[i]; }
int h_sent(void) { return sent; }
void h_clear(void) { sent = 0; }
int h_sent_opts(int i) { return sent_opts[i]; }
int h_sent_mode(int i) { return sent_mode[i]; }
int h_sent_ep(int i) { return sent_ep[i]; }
int h_sent_cluster(int i) { return sent_cluster[i]; }
"""


def build_reporting(d: Path, patched: bool) -> ctypes.CDLL:
    """The SDK's zcl_reporting.c, its CRs dropped and (patched) the patch applied as sdk.mk does,
    with the harness, as a shared library."""
    d.mkdir()
    orig = d / "orig.c"
    orig.write_text(sdk_text("zigbee/zcl/zcl_reporting.c"))
    src = d / "zcl_reporting.c"
    if patched:
        with PATCH.open("rb") as p, src.open("wb") as out:
            subprocess.run(["patch", "-s", "-o", "-", str(orig)], stdin=p, stdout=out, check=True)
    else:
        src.write_bytes(orig.read_bytes())
    (d / "zcl_include.h").write_text(shim())
    (d / "harness.c").write_text(HARNESS)
    so = d / "rep.so"
    subprocess.run(["cc", "-O1", "-fPIC", "-shared", "-w", "-I", str(d), str(src),
                    str(d / "harness.c"), "-o", str(so)], check=True)
    lib = ctypes.CDLL(str(so))
    lib.h_attr.argtypes = [ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint8,
                           ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint8]
    lib.h_run.argtypes = [ctypes.c_uint32]
    return lib


@pytest.fixture(scope="module")
def rep(tmp_path_factory):
    need_sdk()
    return build_reporting(tmp_path_factory.mktemp("rep") / "patched", True)


@pytest.fixture(scope="module")
def rep_sdk(tmp_path_factory):
    need_sdk()
    return build_reporting(tmp_path_factory.mktemp("rep") / "sdk", False)


def frames(lib) -> list[tuple[int, int, int, int]]:
    """(endpoint, cluster, destination mode, txOptions) of each report handed to the APS."""
    return [(lib.h_sent_ep(i), lib.h_sent_cluster(i), lib.h_sent_mode(i), lib.h_sent_opts(i))
            for i in range(lib.h_sent())]


def dimmer(lib) -> None:
    """Two dimmer channels as the converter configures them: onOff, currentLevel (change 1),
    the gestures (maximum interval 0: events), all with minimum interval 0. The first pass after
    a configuration reports every entry (the SDK's): done here and not recorded."""
    lib.h_init()
    for ep in (1, 2):
        lib.h_attr(ep, ON_OFF, 0x0000, BOOL, 0, 62000, 0)
        lib.h_attr(ep, LEVEL, 0x0000, UINT8, 0, 62000, 1)
        lib.h_attr(ep, MULTISTATE, 0x0055, UINT16, 0, 0, 1)
    lib.h_set(0, 1)
    lib.h_run(100)
    lib.h_set(0, 0)
    lib.h_run(100)
    lib.h_clear()


def press(lib, attr: int = 0, value: int = 1) -> list[tuple[int, int, int, int]]:
    """A change of attribute `attr` and the 100 ms report pass after it."""
    before = lib.h_sent()
    lib.h_set(attr, value)
    lib.h_run(100)
    return frames(lib)[before:]


def test_a_report_asks_for_an_aps_ack(rep):
    dimmer(rep)
    assert press(rep) == [(1, ON_OFF, BINDINGS, ACK_TX)]


def test_the_sdks_own_reports_did_not(rep_sdk):
    """The SDK as it is: txOptions 0 (what the patch changes)."""
    dimmer(rep_sdk)
    assert press(rep_sdk) == [(1, ON_OFF, BINDINGS, 0)]


def test_a_level_report_too(rep):
    dimmer(rep)
    assert press(rep, attr=1, value=120) == [(1, LEVEL, BINDINGS, ACK_TX)]


@pytest.mark.parametrize("waiting", range(ACKS_MAX + 3))
def test_no_ack_while_four_frames_wait_for_one(rep, waiting):
    dimmer(rep)
    for i in range(waiting):
        rep.h_cache(CACHE - 1 - 5 * i, 1, 1)       # anywhere in the cache, the last entry too
    want = ACK_TX if waiting < ACKS_MAX else 0
    assert press(rep) == [(1, ON_OFF, BINDINGS, want)]


def test_only_frames_waiting_for_an_ack_count(rep):
    """Frames sent without one (a group's, a report past the limit) leave the cache at their NWK
    confirm; an entry's flags are counted only while it is in use."""
    dimmer(rep)
    for i in range(CACHE):
        rep.h_cache(i, 1, 0)                        # the whole cache busy, nothing waits for an ACK
    assert press(rep, value=1) == [(1, ON_OFF, BINDINGS, ACK_TX)]
    for i in range(CACHE):
        rep.h_cache(i, 0, 1)                        # free entries with a stale ackNeed bit
    assert press(rep, value=0) == [(1, ON_OFF, BINDINGS, ACK_TX)]


@pytest.mark.parametrize("waiting", [0, 1, 3, 4])
def test_a_burst_in_one_pass_takes_at_most_the_room(rep, waiting):
    """Every report due in one pass goes out in that pass, before the APS has taken any of them
    into its cache: the pass counts its own (at a restart all of them are due at once)."""
    dimmer(rep)
    for i in range(waiting):
        rep.h_cache(i, 1, 1)
    for a in range(6):
        rep.h_set(a, 1 + a)
    rep.h_run(100)
    sent = frames(rep)
    assert sorted((ep, c) for ep, c, _, _ in sent) == sorted(
        (ep, c) for ep in (1, 2) for c in (ON_OFF, LEVEL, MULTISTATE))
    room = max(0, ACKS_MAX - waiting)
    assert [o for *_, o in sent] == [ACK_TX] * room + [0] * (len(sent) - room)


def test_each_pass_counts_the_cache_again(rep):
    """A dimming move: a level report each pass. While four of them wait for their ACK (a hub
    that does not answer) the next ones go without; once the ACKs are in, with one again."""
    dimmer(rep)
    opts = []
    for step in range(8):
        opts += [o for *_, o in press(rep, attr=1, value=10 + step)]
        rep.h_cache(step, 1, 1)                     # the APS took it, no ACK comes
    assert opts == [ACK_TX] * ACKS_MAX + [0] * 4
    for i in range(8):
        rep.h_cache(i, 0, 0)                        # acked or given up
    assert press(rep, attr=1, value=99) == [(1, LEVEL, BINDINGS, ACK_TX)]


def test_a_maximum_interval_report_asks_too(rep):
    rep.h_init()
    rep.h_attr(1, ON_OFF, 0x0000, BOOL, 0, 10, 0)
    rep.h_set(0, 1)
    rep.h_run(100)
    rep.h_clear()
    rep.h_run(100)
    assert frames(rep) == []                        # no change, the maximum interval not over
    rep.h_run(10000)                                # the maximum interval's timer, then the pass
    rep.h_run(100)
    assert frames(rep) == [(1, ON_OFF, BINDINGS, ACK_TX)]


# --- a newer report stops the resends of an older one -------------------------------------------
#
# A resend is the frame as it was built (apsTxDataSendStart copies the cached buffer), and
# nothing in the library holds a newer frame to the same destination back: an older report lost
# on a hop past the first, resent 1-2 s later, would reach the hub after a newer one, and the hub
# would keep the older value. zcl_reportAckSupersede sets the older entries' retries to 0, which
# apsTxEventPost takes for "given up" at the next timeout (pinned below).

ACK_RETRIES = 3                 # APS_MAX_FRAME_RETRIES: apsTxDataPost's start value


class Aps:
    """The library's part for the reports to the hub's binding (libzb 3.7.2.0 apsTxDataPost,
    apsAckPeriodic, apsTxEventPost, pinned below): a frame that asks for an ACK takes a free TX
    cache entry (mode 3, its source endpoint and cluster, retries 3) after its pass; the ACK frees
    it; when none comes in time, apsTxEventPost counts the retries down and sends the frame as
    it was built again, or with none left gives it up. `hub`: the last value the hub got of
    each (endpoint, cluster), `heard`: all of them in order."""

    def __init__(self, lib):
        self.lib, self.seen, self.waiting, self.hub, self.heard = lib, lib.h_sent(), {}, {}, []

    def deliver(self, ep: int, cluster: int, value: int) -> None:
        self.hub[(ep, cluster)] = value
        self.heard.append((ep, cluster, value))

    def take(self, lost: bool = False) -> None:
        """The frames of the passes since the last call: into the cache (those that ask), then
        delivered and acked, or lost (no ACK comes)."""
        lib = self.lib
        for i in range(self.seen, lib.h_sent()):
            frame = (lib.h_sent_ep(i), lib.h_sent_cluster(i), lib.h_sent_val(i))
            if lib.h_sent_opts(i) & ACK_TX:
                entry = next(e for e in range(CACHE) if e not in self.waiting)
                lib.h_frame(entry, 1, UNICAST_EXT, frame[0], frame[1])
                if lost:
                    self.waiting[entry] = frame
                else:
                    lib.h_free(entry)
            if not lost:
                self.deliver(*frame)
        self.seen = lib.h_sent()

    def timeout(self) -> None:
        """No ACK in time for the waiting frames: sent again (delivered now) while retries are
        left, else given up."""
        for entry, frame in sorted(self.waiting.items()):
            left = self.lib.h_retries(entry)
            if left:
                self.lib.h_set_retries(entry, left - 1)
                self.deliver(*frame)
            self.lib.h_free(entry)
        self.waiting.clear()


def test_a_late_resend_cannot_bring_back_an_older_level(rep):
    """The review's case: a hold dims to 80; the report of 95 is lost on the last hop, the final
    80 is delivered, and the resend of 95 would come 1-2 s after it."""
    dimmer(rep)
    aps = Aps(rep)
    press(rep, attr=1, value=95)
    aps.take(lost=True)
    [(entry, _)] = aps.waiting.items()
    assert rep.h_retries(entry) == ACK_RETRIES
    press(rep, attr=1, value=80)
    assert rep.h_retries(entry) == 0                # the newer one went out: no resend of 95
    aps.take()
    aps.timeout()
    assert aps.hub[(1, LEVEL)] == 80
    assert aps.heard == [(1, LEVEL, 80)]


def test_on_then_off_stays_off(rep):
    """On (lost), off 0.8 s later (delivered): the resend of on must not follow."""
    dimmer(rep)
    aps = Aps(rep)
    press(rep, attr=0, value=1)
    aps.take(lost=True)
    press(rep, attr=0, value=0)
    aps.take()
    aps.timeout()
    assert aps.heard == [(1, ON_OFF, 0)]


def test_a_lost_report_with_nothing_newer_is_still_sent_again(rep):
    """What the ACK is for: the last report of a move, lost on a hop past the first."""
    dimmer(rep)
    aps = Aps(rep)
    press(rep, attr=1, value=80)
    aps.take(lost=True)
    press(rep, attr=0, value=1)                     # another cluster: no effect on it
    aps.take()
    aps.timeout()
    assert aps.hub == {(1, ON_OFF): 1, (1, LEVEL): 80}


def test_only_the_reports_of_that_endpoint_and_cluster(rep):
    """A frame of another cluster or endpoint, a ZCL response (to a NWK address: a read of
    currentLevel answered) and a group's frame keep their resends."""
    dimmer(rep)
    pending = {0: (UNICAST_EXT, 1, LEVEL), 3: (UNICAST_EXT, 1, ON_OFF), 6: (UNICAST_EXT, 2, LEVEL),
               9: (UNICAST_SHORT, 1, LEVEL), 12: (GROUP, 1, LEVEL), 15: (UNICAST_EXT, 1, LEVEL)}
    for entry, (mode, ep, cluster) in pending.items():
        rep.h_frame(entry, 1, mode, ep, cluster)
    press(rep, attr=1, value=33)
    assert {e: rep.h_retries(e) for e in pending} == {0: 0, 3: ACK_RETRIES, 6: ACK_RETRIES,
                                                      9: ACK_RETRIES, 12: ACK_RETRIES, 15: 0}


def test_also_when_the_newer_one_goes_without_an_ack(rep):
    """Four frames wait (no room): the newer report goes without an ACK, and still the older one
    is not sent again after it."""
    dimmer(rep)
    for entry in range(1, 5):
        rep.h_frame(entry, 1, UNICAST_EXT, 2, ON_OFF)
    rep.h_frame(0, 1, UNICAST_EXT, 1, LEVEL)
    assert press(rep, attr=1, value=44) == [(1, LEVEL, BINDINGS, 0)]
    assert rep.h_retries(0) == 0
    assert [rep.h_retries(e) for e in range(1, 5)] == [ACK_RETRIES] * 4


def test_the_gestures_keep_their_resends(rep):
    """Each gesture report is an event of its own (sequence << 4 | gesture): a newer one does not
    stand for an older one, so a lost one is still sent again (the converter drops a copy or a
    late one by its sequence)."""
    dimmer(rep)
    aps = Aps(rep)
    press(rep, attr=2, value=0x14)                  # hold, lost
    aps.take(lost=True)
    [(entry, _)] = aps.waiting.items()
    press(rep, attr=2, value=0x25)                  # release, delivered
    assert rep.h_retries(entry) == ACK_RETRIES
    aps.take()
    aps.timeout()
    assert aps.heard == [(1, MULTISTATE, 0x25), (1, MULTISTATE, 0x14)]


# --- hal_zigbee_send_report_attr (the firmware's own reports: mcu_version, mcu_build, the
# health record, the flasher status, nwk_command_security) ---------------------------------------

HAL_HARNESS = r"""
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef u8 addrExt_t[8];
typedef enum { HAL_ZIGBEE_OK = 0 } hal_zigbee_status_t;
typedef union { u16 shortAddr; addrExt_t extAddr; } tl_zb_addr_t;
typedef int bool;
EPINFO
ATTRINFO
#define HA_PROFILE_ID 0x0104
#define APS_DSTADDR_EP_NOTPRESETNT 0x00
#define APS_TX_OPT_ACK_TX 0x04
#define TRUE 1
#define ZCL_FRAME_SERVER_CLIENT_DIR 0x01
#define TL_SETSTRUCTCONTENT(s, v) memset(&(s), (v), sizeof(s))
#define zcl_sendReportCmd(a, b, c, d, e, f, g, h) sent_report((a), (b), (e), (f))

static int joined, room, sent, sent_mode, sent_opts, sent_ep, sent_cluster, sent_attr;
static zclAttrInfo_t attr = { 0xff5f, 0x21, 1, 0 };
int zb_isDeviceJoinedNwk(void) { return joined; }
int zcl_reportAckRoom(void) { return room; }
zclAttrInfo_t *zcl_findAttribute(u8 ep, u16 cluster, u16 id) { return &attr; }
static void sent_report(u8 ep, epInfo_t *dst, u16 cluster, u16 id) {
    sent++;
    sent_ep = ep;
    sent_cluster = cluster;
    sent_attr = id;
    sent_mode = dst->dstAddrMode;
    sent_opts = dst->txOptions;
}

hal_zigbee_status_t
SOURCE

void h_set(int j, int r) { joined = j; room = r; sent = 0; sent_opts = -1; }
int h_report(void) { hal_zigbee_send_report_attr(1, 0x0000, 0xff5f, 0x21, 0, 2); return sent; }
int h_opts(void) { return sent_opts; }
int h_mode(void) { return sent_mode; }
int h_where(void) { return sent_ep << 16 | sent_cluster; }
int h_attr(void) { return sent_attr; }
"""


@pytest.fixture(scope="module")
def hal(tmp_path_factory):
    need_sdk()
    d = tmp_path_factory.mktemp("hal")
    zcl, af = sdk_text("zigbee/zcl/zcl.h"), sdk_text("zigbee/af/zb_af.h")
    src = (HAL_HARNESS.replace("EPINFO", typedef_text(af, "epInfo_t"))
           .replace("ATTRINFO", typedef_text(zcl, "zclAttrInfo_t"))
           .replace("SOURCE", function_text(ZCL_HAL_C.read_text(), "hal_zigbee_send_report_attr")))
    (d / "hal.c").write_text(src)
    so = d / "hal.so"
    subprocess.run(["cc", "-O1", "-fPIC", "-shared", "-Wall", "-Werror", "-Wno-unused-function",
                    str(d / "hal.c"), "-o", str(so)], check=True)
    return ctypes.CDLL(str(so))


@pytest.mark.parametrize("room, want", [(4, ACK_TX), (1, ACK_TX), (0, 0), (-3, 0)])
def test_the_firmwares_own_reports_ask_while_there_is_room(hal, room, want):
    hal.h_set(1, room)
    assert hal.h_report() == 1
    assert (hal.h_mode(), hal.h_opts(), hal.h_where(), hal.h_attr()) == (
        BINDINGS, want, 1 << 16 | BASIC, 0xFF5F)


def test_not_joined_no_report(hal):
    hal.h_set(0, 4)
    assert hal.h_report() == 0


# --- the library: what each destination of a binding gets ---------------------------------------

UNICAST_EXT, GROUP, UNICAST_SHORT = 3, 1, 2      # aps_dst_addr_mode of a request
SECURITY = 0x01                                  # APS_TX_OPT_SECURITY_ENABLED


def aps_ack_requested(mode: int, addr: int, tx_options: int, use_multicast: bool = False) -> bool:
    """aps_data_request (libzb 3.7.2.0): whether the frame to this destination asks for an APS
    ACK (frame control bit 0x40, and the TX cache entry's ackNeed: the retries).
    - group: NWK broadcast to 0xFFFD with group delivery, the ACK option cleared (and APS
      security); with nwkUseMulticast (never set: zb_config.c .useMulticast = 0, nothing in src/
      sets it) the library would keep it;
    - a unicast to a broadcast address (0xFFF8..0xFFFF): ACK and APS security cleared;
    - any other unicast, by IEEE address (a binding's) or NWK address: kept."""
    if mode == GROUP and not use_multicast:
        tx_options &= ~(ACK_TX | SECURITY)
    elif mode == UNICAST_SHORT and addr & 0xFFF8 == 0xFFF8:
        tx_options &= ~(ACK_TX | SECURITY)
    return bool(tx_options & ACK_TX)


def bindings_frames(bindings: list[tuple[int, int]], tx_options: int) -> list[bool]:
    """bindingTxBack: one request per binding entry of the cluster (100 ms apart), the report's
    request copied whole (195 bytes: its txOptions with it) and its destination mode and address
    set from the entry (a unicast binding: IEEE address and endpoint; a group binding: group)."""
    return [aps_ack_requested(mode, addr, tx_options) for mode, addr in bindings]


def test_the_hubs_binding_acks_a_groups_does_not():
    coordinator, group = (UNICAST_EXT, 0x00124B0000000001), (GROUP, 0x1234)
    assert bindings_frames([coordinator, group], ACK_TX) == [True, False]
    assert bindings_frames([coordinator, group], 0) == [False, False]


@pytest.mark.parametrize("mode, addr, want", [
    (UNICAST_EXT, 0x00124B0000000001, True), (UNICAST_SHORT, 0x0000, True),
    (UNICAST_SHORT, 0x4A21, True), (UNICAST_SHORT, 0xFFFD, False), (UNICAST_SHORT, 0xFFFC, False),
    (GROUP, 0x0001, False)])
def test_per_destination(mode, addr, want):
    assert aps_ack_requested(mode, addr, ACK_TX) is want


def test_no_build_turns_nwk_multicast_on():
    """The one library path that would keep the option for a group."""
    need_sdk()
    assert re.search(r"\.useMulticast\s*=\s*0,", sdk_text("zigbee/common/zb_config.c"))
    for p in (ROOT / "src").rglob("*.[ch]"):
        assert not re.search(r"useMulticast|NIB_SET_USE_MULTICAST|NIB_ATTRIBUTE_USE_MULTICAST",
                             p.read_text(errors="replace")), p


@pytest.mark.parametrize("lib", LIBS)
def test_the_model_is_read_from_these_libraries(lib):
    need_sdk()
    path = SDK / "zigbee" / "lib" / "tc32" / lib
    assert hashlib.sha256(path.read_bytes()).hexdigest() == LIBS[lib]


def lib_function(tmp_path: Path, lib: str, obj: str, name: str) -> list[str]:
    """The instructions of `name` in `obj` of the library (mnemonic and operands; branch targets
    and pc-relative comments dropped)."""
    if not (TC32 / "tc32-elf-objdump").is_file():
        pytest.skip("no Telink toolchain")
    subprocess.run([str(TC32 / "tc32-elf-ar"), "x", str(SDK / "zigbee/lib/tc32" / lib), obj],
                   cwd=tmp_path, check=True)
    dis = subprocess.run([str(TC32 / "tc32-elf-objdump"), "-d", str(tmp_path / obj)],
                         capture_output=True, text=True, check=True).stdout
    body = dis.split(f"<{name}>:\n", 1)[1].split("\n\n", 1)[0]
    out = []
    for line in body.splitlines():
        f = line.split("\t")
        if len(f) >= 3:
            out.append(" ".join(" ".join(f[2:]).split(";")[0].split()))
    return out


def has_run(code: list[str], run: list[str]) -> bool:
    return any(code[i:i + len(run)] == run for i in range(len(code)))


@pytest.mark.parametrize("lib, nib_flags", [("libzb_router.a", 121), ("libzb_ed.a", 109)])
def test_the_libraries_do_what_the_model_says(tmp_path, lib, nib_flags):
    need_sdk()
    code = lib_function(tmp_path, lib, "aps_data.o", "aps_data_request")
    # group: the NIB's useMulticast (bit 3 of its flags byte, g_zbInfo + 76 + 45; + 33 on an end
    # device, whose NIB has no router fields) tested; without it the NWK destination is
    # GROUP_MESSAGE_SEND_ADDRESS and the ACK option cleared, group delivery (0x0C)
    assert has_run(code, [f"tmovs r3, #{nib_flags}"])
    assert has_run(code, ["tloadrb r3, [r1, r3]", "tshftls r1, r3, #28"])
    assert has_run(code, ["tloadrh r3, [r3, #0]", "tstorerh r3, [r5, #8]", "tmovs r3, #4",
                          "tbclrs r2, r3", "tshftls r2, r2, #24", "tshftrs r2, r2, #24",
                          "tmovs r7, #12"])
    # a unicast to a broadcast address: ACK and APS security cleared, broadcast delivery (0x08)
    assert has_run(code, ["tmovs r2, #5", "tbclrs r1, r2", "tstorerb r1, [r4, #10]",
                          "tmovs r7, #8"])
    # what is left of the option: ackNeed (bit 2) and the frame control's ACK request (0x40)
    assert has_run(code, ["tshftls r3, r1, #29", "tshftrs r3, r3, #31", "tstorer r3, [sp, #4]",
                          "tcmp r3, #0"])
    assert has_run(code, ["tmovs r3, #64", "tbclrs r7, r3", "tors r7, r3"])
    # bindingTxBack: the request copied whole for each binding entry
    back = lib_function(tmp_path, lib, "aps_data.o", "bindingTxBack")
    assert has_run(back, ["tadds r0, r5, #0", "tmovs r2, #195"])


def lib_code(tmp_path: Path, lib: str, obj: str, name: str) -> list[tuple[int, str, str]]:
    """(address, instruction, the call's relocation target or "") of `name` in `obj`."""
    if not (TC32 / "tc32-elf-objdump").is_file():
        pytest.skip("no Telink toolchain")
    subprocess.run([str(TC32 / "tc32-elf-ar"), "x", str(SDK / "zigbee/lib/tc32" / lib), obj],
                   cwd=tmp_path, check=True)
    dis = subprocess.run([str(TC32 / "tc32-elf-objdump"), "-dr", str(tmp_path / obj)],
                         capture_output=True, text=True, check=True).stdout
    body = dis.split(f"<{name}>:\n", 1)[1].split("\n\n", 1)[0]
    out: list[tuple[int, str, str]] = []
    for line in body.splitlines():
        f = line.split("\t")
        if "R_TC32_CALL" in line and out:
            out[-1] = (out[-1][0], out[-1][1], line.split()[-1])
        elif len(f) >= 3 and f[0].strip().endswith(":"):
            text = " ".join(" ".join(f[2:]).split(";")[0].split())
            out.append((int(f[0].strip()[:-1], 16), re.sub(r" <.*>$", "", text), ""))
    return out


def run_at(code: list[tuple[int, str, str]], run: list[str]) -> int:
    """The index in `code` where the instructions `run` follow each other (one place only)."""
    texts = [t for _, t, _ in code]
    found = [i for i in range(len(texts)) if texts[i:i + len(run)] == run]
    assert len(found) == 1, (run, found)
    return found[0]


def at(code: list[tuple[int, str, str]], address: int) -> int:
    return next(i for i, (a, _, _) in enumerate(code) if a == address)


@pytest.mark.parametrize("lib", LIBS)
def test_the_library_gives_up_a_frame_whose_retries_are_0(tmp_path, lib):
    """What zcl_reportAckSupersede relies on, in both libraries:
    - af_dataSend puts the source endpoint in request byte 8, the cluster in 6-7 and the
      destination mode in 12; bindingTxBack sets each binding's mode there (3: IEEE address);
      aps_data_request hands them on (its request for the cache: 10, 18-19, 8), and
      apsTxDataPost writes them to the entry: mode byte 12, ep 14, clusterId 24-25, and
      retries (19) = APS_MAX_FRAME_RETRIES;
    - apsAckPeriodic: an entry in state 3 (waiting for its ACK) whose time ran out gets event 1
      (0xA7); aps_nwk_data_confirm_cb gives a NWK failure but a failed route discovery (0xD0) and
      0xF0 event 1 too;
    - apsTxEventPost, event 1 in state 2, 3 or 4 (sent, waiting, resent): retries counted down,
      the frame sent again unless they were 0, then it is confirmed (given up)."""
    need_sdk()
    send = lib_code(tmp_path, lib, "zb_af_data.o", "af_dataSend")
    run_at(send, ["tshftls r6, r2, #16", "tshftrs r6, r6, #16"])     # clusterId
    run_at(send, ["tstorerb r0, [r4, #8]", "tloadrb r3, [r1, #14]", "tstorerb r3, [r4, #12]"])
    run_at(send, ["tstorerh r6, [r4, #6]"])
    back = lib_code(tmp_path, lib, "aps_data.o", "bindingTxBack")
    i = run_at(back, ["tloadrb r2, [r3, #17]", "tstorerb r2, [r5, #12]", "tloadrb r2, [r3, #17]",
                      "tcmp r2, #3"])
    request = lib_code(tmp_path, lib, "aps_data.o", "aps_data_request")
    run_at(request, ["tloadrb r3, [r0, #8]", "tstorerb r3, [r6, #10]", "tloadrb r7, [r0, #12]",
                     "tstorerb r7, [r6, #8]", "tloadrb r2, [r0, #6]", "tloadrb r3, [r0, #7]",
                     "tshftls r3, r3, #8", "tors r3, r2", "tstorerh r3, [r6, #18]"])
    i = run_at(request, ["tstorer r6, [sp, #0]", "tloadr r0, [sp, #4]", "tloadr r1, [sp, #8]",
                         "tmov r2, sl", "tadds r3, r4, #0"])
    assert request[i + 5][2] == "apsTxDataPost"
    post = lib_code(tmp_path, lib, "aps_data.o", "apsTxDataPost")
    run_at(post, ["tloadrb r3, [r1, #8]", "tstorerb r3, [r4, #12]"])                    # mode
    run_at(post, ["tloadrb r3, [r2, #10]", "tstorerb r3, [r4, #14]"])                   # ep
    run_at(post, ["tloadrb r2, [r2, #18]", "tmov r1, r8", "tloadrb r3, [r1, #19]",
                  "tshftls r3, r3, #8", "tors r3, r2", "tstorerb r3, [r4, #24]",
                  "tshftrs r3, r3, #8", "tstorerb r3, [r4, #25]"])                      # clusterId
    run_at(post, ["tloadrb r2, [r3, #0]", "tstorerb r2, [r4, #19]"])                    # retries
    dis = subprocess.run([str(TC32 / "tc32-elf-objdump"), "-r", "-j", ".text.apsTxDataPost",
                          str(tmp_path / "aps_data.o")], capture_output=True, text=True).stdout
    assert "APS_MAX_FRAME_RETRIES" in dis
    ack = lib_code(tmp_path, lib, "aps_data.o", "apsAckPeriodic")
    i = run_at(ack, ["tshftrs r3, r3, #4", "tcmp r3, #3"])                              # state 3
    i = run_at(ack, ["tmovs r1, #1", "tmovs r2, #167"])
    assert ack[i + 2][2] == "apsTxEventPost"
    cnf = lib_code(tmp_path, lib, "aps_data.o", "aps_nwk_data_confirm_cb")
    i = run_at(cnf, ["tcmp r2, #208"])
    assert cnf[i + 2][1] == "tcmp r2, #240" and cnf[i + 7][1] == "tmovs r1, #1"
    assert cnf[i + 8][2] == "apsTxEventPost"
    ev = lib_code(tmp_path, lib, "aps_data.o", "apsTxEventPost")
    i = run_at(ev, ["tcmp r0, #1"])                 # event 1
    retry = ev[i][0]
    assert ev[i + 2][1] == "tshftls r3, r2, #29"    # ackNeed (bit 2)
    j = run_at(ev, ["tloadrb r3, [r4, #19]", "tsubs r2, r3, #1", "tstorerb r2, [r4, #19]",
                    "tcmp r3, #0"])
    assert j == i + 4
    given_up = int(ev[j + 4][1].split()[1], 16)     # tjeq: retries were 0
    assert ev[j + 4][1].startswith("tjeq ")
    k = at(ev, given_up)
    assert ev[k][1] == "tadds r0, r4, #0" and ev[k + 1][2] == "aps_txCacheConfirm"
    assert ev[j + 6][2] == "apsTxDataSendStart"     # else sent again
    table = subprocess.run([str(TC32 / "tc32-elf-objdump"), "-s", "-j", ".rodata.apsTxEventPost",
                            str(tmp_path / "aps_data.o")], capture_output=True, text=True).stdout
    words = [w for l in table.splitlines() if l.startswith(" 00")
             for w in l.split()[1:5] if re.fullmatch(r"[0-9a-f]{8}", w)]
    targets = [int.from_bytes(bytes.fromhex(w), "little") for w in words]   # by state, 0..5
    assert len(targets) == 6
    assert targets[2] == targets[3] == targets[4] == retry      # sent, waiting, resent: event 1


def test_the_firmware_stops_the_resends_where_the_library_reads_them(tmp_path):
    """zcl_reportAckSupersede as tc32-gcc builds it (-Os as the dimmer boards, -O2 as the
    others): it reads the entries' mode (byte 12, compared with 3), ep (14) and clusterId (24,
    25: the cache entry is packed) and writes 0 to retries (19)."""
    need_sdk()
    for opt in ("-Os", "-O2"):
        code = firmware_code(tmp_path, opt)
        stores = [c for c in code if re.match(r"tstorerb r\d, \[r\d, #19\]$", c)]
        assert len(stores) == 1, (opt, stores)
        for byte in (12, 14, 24, 25):
            assert any(re.match(rf"tloadrb r\d, \[r\d, #{byte}\]$", c) for c in code), (opt, byte)
        assert any(re.match(r"tcmp r\d, #3$", c) for c in code), opt
        assert any(re.match(r"tcmp r\d, #18$", c) for c in code), opt   # genMultistateInput


def firmware_code(tmp_path: Path, opt: str) -> list[str]:
    """The instructions of the patched zcl_reporting.c's reportingTimerCb (reportAttrs inlined)
    and zcl_reportAckSupersede (inlined or not), compiled with tc32-gcc."""
    if not (TC32 / "tc32-elf-gcc").is_file():
        pytest.skip("no Telink toolchain")
    d = tmp_path / opt.lstrip("-")
    d.mkdir()
    orig = d / "orig.c"
    orig.write_text(sdk_text("zigbee/zcl/zcl_reporting.c"))
    src = d / "zcl_reporting.c"
    with PATCH.open("rb") as p, src.open("wb") as out:
        subprocess.run(["patch", "-s", "-o", "-", str(orig)], stdin=p, stdout=out, check=True)
    obj = d / "zcl_reporting.o"
    flags = [opt] + (["-fno-jump-tables"] if opt == "-Os" else [])
    subprocess.run([str(TC32 / "tc32-elf-gcc"), *flags, *TC32_FLAGS, *tc32_includes(),
                    "-c", str(src), "-o", str(obj)], check=True, capture_output=True)
    dis = subprocess.run([str(TC32 / "tc32-elf-objdump"), "-d", str(obj)], capture_output=True,
                         text=True, check=True).stdout
    code = []
    for name in ("reportingTimerCb", "zcl_reportAckSupersede"):
        if f"<{name}>:" in dis:
            body = dis.split(f"<{name}>:\n", 1)[1].split("\n\n", 1)[0]
            code += [" ".join(" ".join(f.split("\t")[2:]).split(";")[0].split())
                     for f in body.splitlines() if len(f.split("\t")) >= 3]
    assert code
    return code


TC32_FLAGS = ["-fpack-struct", "-ffunction-sections", "-fdata-sections", "-fshort-enums",
              "-std=gnu99", "-funsigned-char", "-fshort-wchar", "-fms-extensions", "-nostdlib",
              "-DMCU_CORE_8258=1", "-DROUTER=1", "-DMCU_STARTUP_8258=1"]


def tc32_includes() -> list[str]:
    t = ROOT / "src" / "telink"
    inc = [f"-I{x}" for x in (SDK / "zigbee/zcl", t, t / "configs", t / "libc_polyfills",
                              t / "custom_zcl", ROOT / "src")]
    return inc + [f"-I{SDK / x}" for x in ("platform", "platform/chip_8258", "proj",
                                           "proj/common", "zigbee", "zigbee/af", "zigbee/include",
                                           "zigbee/bdb/includes", "zigbee/common/includes",
                                           "zigbee/ota", "zigbee/zbapi", "zigbee/zbhci",
                                           "zigbee/zdo")]


def test_the_firmware_reads_the_cache_entry_where_the_library_writes_it(tmp_path):
    """apsTxDataPost sets an entry's byte 18: used bit 0, addrReqNeed bit 1, ackNeed bit 2,
    interPAN bit 3, the state above. zcl_reportAckRoom (built with tc32-gcc as the firmware's
    zcl_reporting.o) reads used and ackNeed there through the SDK's aps_tx_cache_list_t."""
    need_sdk()
    post = lib_function(tmp_path, "libzb_router.a", "aps_data.o", "apsTxDataPost")
    assert has_run(post, ["tmovs r3, #1", "tors r3, r2", "tstorerb r3, [r4, #18]"])        # used
    assert has_run(post, ["tshftls r2, r2, #2", "tloadrb r3, [r4, #18]", "tmovs r1, #4",
                          "tbclrs r3, r1", "tors r3, r2", "tstorerb r3, [r4, #18]"])        # ackNeed
    orig = tmp_path / "orig.c"
    orig.write_text(sdk_text("zigbee/zcl/zcl_reporting.c"))
    src = tmp_path / "zcl_reporting.c"
    with PATCH.open("rb") as p, src.open("wb") as out:
        subprocess.run(["patch", "-s", "-o", "-", str(orig)], stdin=p, stdout=out, check=True)
    obj = tmp_path / "zcl_reporting.o"
    subprocess.run([str(TC32 / "tc32-elf-gcc"), "-Os", "-fno-jump-tables", *TC32_FLAGS,
                    *tc32_includes(), "-c", str(src), "-o", str(obj)], check=True,
                   capture_output=True)
    dis = subprocess.run([str(TC32 / "tc32-elf-objdump"), "-d", str(obj)], capture_output=True,
                         text=True, check=True).stdout
    room = dis.split("<zcl_reportAckRoom>:\n", 1)[1].split("\n\n", 1)[0]
    code = [" ".join(" ".join(f.split("\t")[2:]).split(";")[0].split())
            for f in room.splitlines() if len(f.split("\t")) >= 3]
    load = [c for c in code if c.startswith("tloadrb")]
    assert len(load) == 1 and load[0].endswith(", #18]")
    r = load[0].split()[1].rstrip(",")
    i = code.index(load[0])
    # bit 2 (>> 2) and bit 0 (<< 31, >> 31) of that byte, and-ed
    assert re.match(rf"tshftrs r\d, {r}, #2", code[i + 1])
    assert code[i + 2:i + 4] == [f"tshftls {r}, {r}, #31", f"tshftrs {r}, {r}, #31"]
    assert code[i + 4].startswith("tands")
