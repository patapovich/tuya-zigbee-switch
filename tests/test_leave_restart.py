"""The restart after the module's own leave (src/device_config/reset.c restart_after_leave), on
the ZDMS16-2 and the TS110E builds.

The Telink stack's own leave (a reset, the coordinator's remove) empties the network NV and sets
the stack up again in place, but its security record is set up only at a start-up (ss_zdoInit()
in bdb_init()). A join straight after the leave saved what RAM held: a record taken over from the
factory firmware as it was kept the module's NWK commands unsecured through every later restart
(zigbee/nwk_sec.h). The module now restarts 1 s after its leave, as the SDK's own router app does,
and steers no network until then, so the next join starts from a factory-new start-up.

The stack confirms a reset of a unit that is factory new already as the same leave (status 0):
no restart there (that would be a restart per reset with nothing to start fresh). A restart
comes up factory new, so repeated resets restart once.

Here:
- the Telink glue (telink/hal/zigbee_network.c, patch_sdk/drv_nv.c) and reset.c built for the
  host, driven through the library's sequences (from libzb_router 3.7.2.0): its own leave, a
  factory-new reset, a reset cut short and finished by zb_init(), a leave request that failed;
- the stub's model of it on the boards' configs: every reset source (the MCU's reset request from
  the module button or the wall switch presses, the TS110E's button and presses, the coordinator's
  remove, a GPIO reset button), unjoined resets, repeated resets, the debug hook's leave in place,
  the boards and builds without it;
- the order it needs: the frame-counter floor after the counter's load (no counter step back over
  the start-ups without a network) and the W4c marker for a module on a network of its own (the
  restart must not take an old factory network);
- the W4c take (cut out of zigbee_network.c and built for the host over a synthetic factory NV):
  a module converted before the marker and on a network of its own gets the marker, and after a
  reset the old factory network is not taken; in a debug build (which takes the copy again after
  a wipe) with the leave confirm, not after the module's own leave either."""
import ctypes
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
import yaml
from client import StubProc
from conftest import Device
from stock_nv_take_host import (LOG_JOIN, LOG_JOIN_LEFT, MARKER_ADDR, MARKER_MAGIC, NOT_1MB,
                                OWN_NETWORK, STALE, TAKEN, TAKEN_BEFORE, Diag, build_take,
                                factory_network, function_text, log_records, marker_head)
from tuya_mcu import TUYA_CMD_RESET_NETWORK, pump, take_tx, tuya_frame

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
NETWORK_C = SRC / "telink" / "hal" / "zigbee_network.c"
DRV_NV_C = SRC / "telink" / "patch_sdk" / "drv_nv.c"
RESET_C = SRC / "device_config" / "reset.c"
DELAY_MS = 1000                          # LEAVE_RESTART_DELAY_MS
BASIC = 0x0000
ATTR_NWK_SEC_TEST = 0xFF5E
TEST_RESET = 0xA3
ZDMS16_2_DP_CONFIG = "65E30;66E31;96V32;97V33;98V34;99V35;9AV36;9BV37;9CV38;9DV39;9EV3a;9FV3b;"
TS110E_DP_CONFIG = "70V30;71V31;72V32;73V33;74V34;75V35;76V36;77V37;78E38;79E39;"
TS110E_BUTTON, TS110E_S1 = "C3", "B4"
DIMMER_RESET_BUTTON = "A;B;DM1;M;BA0u;"   # an MCU dimmer with a GPIO reset button
RELAY_RESET_BUTTON = "A;B;RB0;BA0u;"      # a relay with one


def board(name: str) -> dict:
    return yaml.safe_load((ROOT / "device_db.yaml").read_text())[name]


# --- the Telink glue on the host --------------------------------------------------------------

# What the cut-out HAL code, the NV override and reset.c need: the stack's factory-new state and
# the calls they make, recorded. reset.c is built as is (a ZDMS16-2 build: SECONDARY_MCU_IMAGE,
# DIMMER_ONLY), its task and system calls recorded.
TL_COMMON_SHIM = r"""
#include <stdint.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int nv_sts_t;
#define NV_SUCC 0
#define NV_MAX_MODULS 8
#define NV_MODULE_NWK_FRAME_COUNT 6
#define NV_MODULE_APP 5
#define foreach(i, n) for (int i = 0; i < (n); i++)
u8   nv_facrotyNewRstFlagCheck(void);
void nv_facrotyNewRstFlagSet(void);
void nv_facrotyNewRstFlagClear(void);
nv_sts_t nv_resetModule(u8 module);
"""

GLUE = r"""
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "device_config/reset.h"
#include "hal/tasks.h"
typedef uint8_t u8;
typedef uint8_t extAddr_t[8];
typedef struct { extAddr_t deviceAddr; u8 status; } nlme_leave_cnf_t;
#define stock_nv_log(kind, a, b) ((void)0)
#define printf(...) ((void)0)

/* the stack, as far as these go */
static bool factory_new;
static u8   rst_flag;                 /* the SDK's factory-reset flag (CFG_FACTORY_RST_CNT) */
static int  modules_reset, notified, notified_pending, steer_starts;
static u8   steeringInProgress;
bool zb_isDeviceFactoryNew(void) { return factory_new; }
u8   nv_facrotyNewRstFlagCheck(void) { return rst_flag; }
void nv_facrotyNewRstFlagSet(void) { rst_flag = 1; }
void nv_facrotyNewRstFlagClear(void) { rst_flag = 0; }
int  nv_resetModule(u8 m) { modules_reset++; return 0; }
int  nv_resetToFactoryNew(void);
u8   bdb_networkSteerStart(void) { steer_starts++; return 0; }
static void notify_about_network_status_change(void) {
    notified++;
    notified_pending = restart_after_leave_pending();
}
/* zb_init(): the SDK's flag set (a reset cut short) is finished first, before the stack has
 * loaded anything (g_zbNwkCtx is .bss: not factory new yet), then the NV is loaded */
static bool fn_after_init;
static void zb_init(void) {
    if (nv_facrotyNewRstFlagCheck()) {
        nv_resetToFactoryNew();
    }
    factory_new = fn_after_init;
}
static void stock_nv_take_network(void) {}
static int zdo_callbacks;
static void zb_zdoCbRegister(int *cb) {}
#define POWER_MODE_RECEIVER_COMES_PERIODICALLY 0
static void af_powerDescPowerModeUpdate(int m) {}
/* the start-up's stack exception handler and network key wait (not under test here) */
#define RESTART_EXCEPTION_ANA_REG 0
uint8_t g_restart_exception;
static u8 analog_read(u8 reg) { return 0; }
static void stack_exception(void) {}
static void sys_exceptHandlerRegister(void (*handler)(void)) {}
static uint32_t TRANSPORT_NETWORK_KEY_WAIT_TIME;

/* reset.c's */
static int scheduled, sched_delay;
void hal_tasks_init(hal_task_t *task) {}
void hal_tasks_schedule(hal_task_t *task, uint32_t delay_ms) { scheduled++; sched_delay = delay_ms; }
void hal_tasks_unschedule(hal_task_t *task) {}
void hal_system_reset(void) { __builtin_trap(); }
void hal_factory_reset(void) {}
int  hal_nvm_clear_all(void) { return 0; }
uint8_t relays_cnt, dimmer_clusters_cnt;
#ifdef HK_DIMMER
void hk_dimmer_before_reboot(void) {}
#endif
#ifdef MCU_FLASHER_DUMP
uint8_t g_nwk_sec_test_keep_running;
#endif

GLUE_SOURCE

/* the harness */
void h_state(int fn, int rst) { factory_new = fn; rst_flag = rst; }
void h_boot(int fn_after) { fn_after_init = fn_after; telink_zigbee_hal_network_init(); }
/* the stack's own leave (zdo_selfLeaveProcessCb, rejoin 0): not joined any more but not factory
 * new yet, the frame counter saved, the NV emptied, zb_reset() (factory new), the confirm */
void h_self_leave(void) {
    nlme_leave_cnf_t c = { { 0 }, 0 };
    factory_new = 0;
    nv_resetToFactoryNew();
    factory_new = 1;
    zdo_leave_confirmation_callback(&c);
}
/* zb_factoryReset() of a factory-new unit: the NV emptied, then zb_factoryResetNotify(1) */
void h_reset_factory_new(void) {
    nlme_leave_cnf_t c = { { 0 }, 0 };
    factory_new = 1;
    nv_resetToFactoryNew();
    zdo_leave_confirmation_callback(&c);
}
/* zb_factoryReset() of a joined unit whose leave request failed: zb_factoryResetNotify(err) */
void h_leave_request_failed(void) {
    nlme_leave_cnf_t c = { { 0 }, 0xC2 };
    factory_new = 0;
    zdo_leave_confirmation_callback(&c);
}
void h_steer(void) { hal_zigbee_start_network_steering(); }
int  h_pending(void) { return restart_after_leave_pending(); }
int  h_scheduled(void) { return scheduled; }
int  h_delay(void) { return sched_delay; }
int  h_steer_starts(void) { return steer_starts; }
int  h_notified(void) { return notified; }
int  h_notified_pending(void) { return notified_pending; }
int  h_modules_reset(void) { return modules_reset; }
int  h_rst_flag(void) { return rst_flag; }
void h_outputs(int relays, int dimmers) { relays_cnt = relays; dimmer_clusters_cnt = dimmers; }
#ifdef MCU_FLASHER_DUMP
void h_keep_running(void) { g_nwk_sec_test_keep_running = 1; }
int  h_keep(void) { return g_nwk_sec_test_keep_running; }
#endif
"""


def leave_restart_block(text: str) -> str:
    """The flag and its setter (the #ifdef LEAVE_RESTART part before the leave confirm)."""
    m = re.search(r"^#ifdef LEAVE_RESTART\n(?:(?!^#endif).)*?static uint8_t g_nv_left_network;"
                  r".*?^#endif\n", text, re.M | re.S)
    assert m, "the left-network flag"
    return m.group(0)


def build_glue(d: Path, defines: list[str]) -> ctypes.CDLL:
    text = NETWORK_C.read_text()
    source = "\n".join([leave_restart_block(text),
                        function_text(text, "zdo_leave_confirmation_callback"),
                        function_text(text, "hal_zigbee_start_network_steering"),
                        function_text(text, "telink_zigbee_hal_network_init")])
    shim = d / "shim"
    shim.mkdir()
    (shim / "tl_common.h").write_text(TL_COMMON_SHIM)
    (d / "glue.c").write_text(GLUE.replace("GLUE_SOURCE", source))
    so = d / "glue.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Werror", "-Wno-unused-function",
                    "-DHAL_STUB", *defines, "-I", str(shim), "-I", str(SRC),
                    "-I", str(SRC / "device_config"), str(d / "glue.c"), str(DRV_NV_C),
                    str(RESET_C), "-o", str(so)], check=True)
    lib = ctypes.CDLL(str(so))
    for name in ("h_state", "h_outputs"):
        getattr(lib, name).argtypes = [ctypes.c_int, ctypes.c_int]
    lib.h_boot.argtypes = [ctypes.c_int]
    return lib


ZDMS16_2_BUILD = ["-DSECONDARY_MCU_IMAGE", "-DDIMMER_ONLY"]


@pytest.fixture
def glue(tmp_path):
    return build_glue(tmp_path, ZDMS16_2_BUILD)


@pytest.fixture
def glue_debug(tmp_path):
    return build_glue(tmp_path, ZDMS16_2_BUILD + ["-DMCU_FLASHER_DUMP"])


def joined_start(lib) -> None:
    lib.h_state(0, 0)
    lib.h_boot(0)          # a start-up on a network


def test_the_modules_own_leave_restarts_it_a_second_later(glue):
    joined_start(glue)
    glue.h_self_leave()
    assert glue.h_scheduled() == 1 and glue.h_delay() == DELAY_MS
    assert glue.h_pending() == 1
    assert glue.h_modules_reset() > 0                      # the override still empties the NV


def test_the_restart_is_on_its_way_before_the_status_is_told(glue):
    """The status callbacks (the MCU told "not connected", the app's steering) see it pending."""
    joined_start(glue)
    glue.h_self_leave()
    assert glue.h_notified() == 1 and glue.h_notified_pending() == 1


def test_no_steering_until_the_restart(glue):
    joined_start(glue)
    glue.h_steer()
    assert glue.h_steer_starts() == 1                      # on a network: steering as before
    glue.h_self_leave()
    for _ in range(5):
        glue.h_steer()
    assert glue.h_steer_starts() == 1


def test_a_reset_of_a_factory_new_unit_does_not_restart(glue):
    """zb_factoryReset() of a factory-new unit: the NV emptied and the same confirm, status 0."""
    glue.h_state(1, 0)
    glue.h_boot(1)
    for _ in range(5):
        glue.h_reset_factory_new()
        glue.h_steer()
    assert glue.h_scheduled() == 0 and glue.h_pending() == 0
    assert glue.h_steer_starts() == 5 and glue.h_notified() == 5


def test_a_failed_leave_request_does_not_restart(glue):
    joined_start(glue)
    glue.h_leave_request_failed()
    assert glue.h_scheduled() == 0 and glue.h_pending() == 0


def test_a_reset_cut_short_and_finished_at_the_start_up_is_no_left_network(glue):
    """zb_init() finishes it with nv_resetToFactoryNew() before the stack knows it is factory
    new: without the clear after zb_init() the next reset of the factory-new unit restarted."""
    glue.h_state(0, 1)
    glue.h_boot(1)
    assert glue.h_rst_flag() == 0 and glue.h_modules_reset() > 0   # finished at the start-up
    glue.h_reset_factory_new()
    assert glue.h_scheduled() == 0 and glue.h_pending() == 0


def test_the_left_network_flag_is_used_once(glue):
    """A leave that did not restart (below: the debug hook) leaves no flag for a later reset."""
    joined_start(glue)
    glue.h_outputs(1, 0)                                  # (not read in a DIMMER_ONLY build)
    glue.h_self_leave()
    glue.h_reset_factory_new()
    assert glue.h_scheduled() == 1


def test_the_debug_hooks_leave_stays_in_place_once(glue_debug):
    g = glue_debug
    joined_start(g)
    g.h_keep_running()
    g.h_self_leave()
    assert g.h_scheduled() == 0 and g.h_pending() == 0 and g.h_keep() == 0
    g.h_steer()
    assert g.h_steer_starts() == 1                         # in place: it steers at once
    g.h_self_leave()                                       # (joined again) the next leave
    assert g.h_scheduled() == 1 and g.h_pending() == 1


def test_the_debug_hooks_flag_is_taken_by_an_unjoined_reset_too(glue_debug):
    g = glue_debug
    g.h_state(1, 0)
    g.h_boot(1)
    g.h_keep_running()
    g.h_reset_factory_new()
    assert g.h_keep() == 0
    g.h_self_leave()
    assert g.h_scheduled() == 1


@pytest.mark.parametrize("relays, dimmers, restarts", [(0, 2, 1), (1, 2, 0), (2, 0, 0), (0, 0, 0)])
def test_without_dimmer_only_the_config_tells_whether_a_restart_keeps_the_outputs(
        tmp_path, relays, dimmers, restarts):
    """A board whose outputs are not all the MCU's dimmers keeps the leave in place: a relay
    drops for a restart and then takes its power-on setting (the stub's case: every board)."""
    lib = build_glue(tmp_path, ["-DSECONDARY_MCU_IMAGE"])
    lib.h_outputs(relays, dimmers)
    joined_start(lib)
    lib.h_self_leave()
    assert lib.h_scheduled() == restarts and lib.h_pending() == restarts


def test_a_build_without_it_leaves_in_place(tmp_path):
    """Neither SECONDARY_MCU_IMAGE nor HK_DIMMER (every other Telink board): no flag, no
    restart, the stack's leave in place as before."""
    lib = build_glue(tmp_path, [])
    joined_start(lib)
    lib.h_self_leave()
    lib.h_steer()
    assert lib.h_scheduled() == 0 and lib.h_steer_starts() == 1
    assert not hasattr(lib, "restart_after_leave") and not hasattr(lib, "telink_zigbee_nv_reset")


def test_the_ts110e_build_has_it_too(tmp_path):
    """HK_DIMMER (the TS110E): the same, the virtual MCU's state saved on the way out."""
    lib = build_glue(tmp_path, ["-DHK_DIMMER", "-DDIMMER_ONLY"])
    joined_start(lib)
    lib.h_self_leave()
    assert lib.h_scheduled() == 1 and lib.h_delay() == DELAY_MS and lib.h_pending() == 1


# --- where the Telink HAL has it (the order it needs) ----------------------------------------


def test_the_nv_override_tells_before_it_empties_anything():
    body = function_text(DRV_NV_C.read_text(), "nv_resetToFactoryNew")
    assert body.index("telink_zigbee_nv_reset();") < body.index("nv_resetModule")
    assert "#ifdef LEAVE_RESTART\n    telink_zigbee_nv_reset();\n#endif" in body


def test_the_flag_is_set_only_off_factory_new():
    block = leave_restart_block(NETWORK_C.read_text())
    body = function_text(block, "telink_zigbee_nv_reset")
    assert re.search(r"if \(!zb_isDeviceFactoryNew\(\)\) \{\s*g_nv_left_network = 1;", body)


def test_steering_asks_first():
    body = function_text(NETWORK_C.read_text(), "hal_zigbee_start_network_steering")
    first = body[body.index("{") + 1:].strip()
    assert first.startswith("if (restart_after_leave_pending()) {")


def test_the_start_up_clears_the_flag_after_zb_init():
    body = function_text(NETWORK_C.read_text(), "telink_zigbee_hal_network_init")
    assert body.index("zb_init();") < body.index("g_nv_left_network = 0;")


def test_the_floor_and_the_marker_it_needs_are_there():
    """The restart makes a start-up without a network after every reset: the frame-counter floor
    must come after the counter's load (in network_init, before it, it put the counter back below
    frames already sent at the second such start-up), and a W4c module converted before the marker
    must get the marker while on its own network (else the restart takes its old factory
    network instead of coming up factory new)."""
    text = NETWORK_C.read_text()
    assert "restart_after_leave(" in function_text(text, "zdo_leave_confirmation_callback")
    assert "nwk_frame_counter_apply_floor" not in function_text(text, "telink_zigbee_hal_network_init")
    bdb = function_text(text, "telink_zigbee_hal_bdb_init")
    assert bdb.index("bdb_init(") < bdb.index("nwk_frame_counter_apply_floor();")
    take = function_text(text, "stock_nv_take_network")
    own = take[take.index("g_stock_nv_diag.result = STOCK_NV_OWN_NETWORK;"):take.index("} else if")]
    assert re.search(r"if \(marker == 0 && has_info\) \{.*?stock_nv_marker_write\(\);", own, re.S)


# --- the W4c take on the host: the marker for a module on its own network ---------------------
# (tests/stock_nv_take_host.py builds it; tests/test_stock_nv.py has the take's checks)


@pytest.fixture
def take(tmp_path):
    return build_take(tmp_path)


def test_a_first_conversion_takes_the_network_once(take):
    factory_network(take)
    take.h_own(0)
    d = take.h_boot().contents
    assert d.result == TAKEN and take.h_own_info() and d.found & 0x80
    take.h_own(0)                                          # a reset: the own NV emptied
    assert take.h_boot().contents.result == TAKEN_BEFORE and take.h_own_writes() == 0


def test_a_module_converted_before_the_marker_gets_it_on_its_own_network(take):
    factory_network(take)
    take.h_own(1)                                          # joined under this firmware since
    assert marker_head(take) == b"\xFF" * 32
    d = take.h_boot().contents
    assert d.result == OWN_NETWORK and d.found & 0x80 and take.h_own_writes() == 0
    head = marker_head(take)
    assert struct.unpack_from("<I", head)[0] == MARKER_MAGIC
    told = Diag.from_buffer_copy(head[4:4 + ctypes.sizeof(Diag)])
    assert told.result == OWN_NETWORK and told.found & 3 == 3   # tells it from a take
    take.h_own(0)                                          # a reset, then the restart
    assert take.h_boot().contents.result == TAKEN_BEFORE and take.h_own_writes() == 0


def test_the_marker_is_written_once(take):
    factory_network(take)
    take.h_own(1)
    take.h_boot()
    head = marker_head(take)
    take.h_boot()
    assert marker_head(take) == head


def test_no_marker_without_a_factory_network(take):
    take.h_own(1)
    d = take.h_boot().contents
    assert d.result == OWN_NETWORK and not d.found & 0x80
    assert marker_head(take) == b"\xFF" * 32


def test_a_marker_sector_holding_something_else_is_left_alone(take):
    factory_network(take)
    flash = take.h_flash().contents
    flash[MARKER_ADDR:MARKER_ADDR + 4] = b"\x12\x34\x56\x78"
    take.h_own(1)
    assert take.h_boot().contents.result == OWN_NETWORK
    assert marker_head(take)[:4] == b"\x12\x34\x56\x78" and marker_head(take)[4:] == b"\xFF" * 28


def test_a_512_kb_chip_has_no_factory_nv(take):
    factory_network(take)
    take.h_mid(0x136085)
    take.h_own(1)
    assert take.h_boot().contents.result == NOT_1MB
    assert marker_head(take) == b"\xFF" * 32


@pytest.fixture
def take_debug(tmp_path):
    """A debug build's take (MCU_FLASHER_DUMP: it takes the copy again unless stale) with the
    HAL's leave confirm."""
    return build_take(tmp_path, debug=True, leave=True)


def joins_left(lib) -> int:
    return [r[1:3] for r in log_records(lib)].count((LOG_JOIN, LOG_JOIN_LEFT))


def test_debug_build_a_reset_after_the_take_restarts_factory_new(take_debug):
    """Taken, not joined anywhere since, then a reset (the module button, the wall switch presses,
    the coordinator's remove, a failed key exchange): the leave goes out and the coordinator may
    drop the module and its link key. The leave confirm logs the network left before the restart,
    so the restart does not take the copy again (stale): the module comes up factory new and
    steers, and every later reset and restart stays so. Before, the restart took the network
    that had just removed it, came up joined on it and never steered: only a programmer got it
    out."""
    lib = take_debug
    factory_network(lib)
    lib.h_own(0)
    assert lib.h_boot().contents.result == TAKEN and lib.h_own_info()
    lib.h_leave()
    assert (lib.h_restarts(), lib.h_restart_left()) == (1, 1) and not lib.h_own_info()
    assert joins_left(lib) == 1
    d = lib.h_boot().contents                              # the restart
    assert d.result == STALE and not lib.h_own_info() and lib.h_own_writes() == 0
    lib.h_leave_factory_new()                              # a reset of it, factory new
    assert (lib.h_restarts(), lib.h_restart_left()) == (2, 0) and joins_left(lib) == 1
    assert lib.h_boot().contents.result == STALE and not lib.h_own_info()


def test_debug_build_the_wipes_still_take_it_and_a_forced_one_after_a_leave(take_debug):
    """The bench's wipes (0xA5..0xA8) send no leave: the copy is taken again after them, as
    before. After a leave only a forced wipe (0xA7 / 0xA8) takes it."""
    lib = take_debug
    factory_network(lib)
    lib.h_own(0)
    assert lib.h_boot().contents.result == TAKEN
    lib.h_wipe(0)                                          # 0xA5: the NV emptied, no leave
    assert lib.h_boot().contents.result == TAKEN and joins_left(lib) == 0
    lib.h_leave()
    lib.h_wipe(0)
    assert lib.h_boot().contents.result == STALE
    lib.h_wipe(2)                                          # 0xA7: forced
    assert lib.h_boot().contents.result == TAKEN and lib.h_own_info()
    lib.h_leave()                                          # and a leave after that one again
    assert lib.h_boot().contents.result == STALE


def test_the_network_left_is_logged_before_the_restart():
    body = function_text(NETWORK_C.read_text(), "zdo_leave_confirmation_callback")
    assert re.search(r"if \(g_nv_left_network\) \{\s*stock_nv_log\(STOCK_NV_LOG_JOIN, "
                     r"STOCK_NV_LOG_JOIN_LEFT, 0\);\s*\}", body)
    assert body.index("STOCK_NV_LOG_JOIN_LEFT") < body.index("restart_after_leave(")


def test_a_release_build_logs_nothing_at_a_leave(tmp_path):
    """stock_nv_log is nothing in release builds: the leave confirm writes no flash there, and a
    reset after the take finds the marker (taken before)."""
    lib = build_take(tmp_path, leave=True)
    factory_network(lib)
    lib.h_own(0)
    assert lib.h_boot().contents.result == TAKEN
    lib.h_leave()
    assert lib.h_restarts() == 1 and log_records(lib) == []
    assert lib.h_boot().contents.result == TAKEN_BEFORE


# --- the stub: every reset source, on the boards' own configs ---------------------------------


def alive_until_the_restart(proc: StubProc, device: Device) -> None:
    """Just left: no steering, the process alive until 1 s after the leave, then gone."""
    events = len(device._events)
    device.step_time(DELAY_MS - 1)
    pump(proc, 3)
    assert proc.is_running() and not device.is_joined()
    assert not any(e.kind == "zcl_start_network_steering" for e in device._events[events:])
    device.step_time(1)
    assert proc.wait_for_exit(2.0), "no restart after the leave"


def still_running(proc: StubProc, device: Device, ms: int = 5000) -> None:
    for _ in range(ms // 500):
        device.step_time(500)
        pump(proc, 2)
    assert proc.is_running()


def zdms16_2(joined: bool = True) -> tuple[StubProc, Device]:
    proc = StubProc(device_config=board("MODULE_AVATTO_ZDMS16_2")["config_str"],
                    dp_config=ZDMS16_2_DP_CONFIG, joined=joined).start()
    device = Device(proc)
    pump(proc)
    take_tx(proc)
    return proc, device


def ts110e(joined: bool = True) -> tuple[StubProc, Device]:
    proc = StubProc(device_config=board("MODULE_TUYA_TS110E_3MPWQZUU")["config_str"],
                    dp_config=TS110E_DP_CONFIG, joined=joined).start()
    device = Device(proc)
    for _ in range(50):
        device.step_time(100)
        pump(proc, 1)
    take_tx(proc)
    return proc, device


def mcu_reset_request(proc: StubProc) -> None:
    """The MCU asks for the leave (0x03, rejoin): Open Dimmer's module button or wall switch
    presses (DP 181)."""
    proc.exec(f"uart_rx {tuya_frame(TUYA_CMD_RESET_NETWORK, bytes([0x01]))}")
    pump(proc)


def steered(device: Device) -> bool:
    """Steering started since the Device was made (the first pass's is before it)."""
    return any(e.kind == "zcl_start_network_steering" for e in device._events)


def joining(device: Device) -> bool:
    return device.status()["joined"] == "2"               # HAL_ZIGBEE_NETWORK_JOINING


@pytest.mark.parametrize("name", ["MODULE_AVATTO_ZDMS16_2", "MODULE_AVATTO_ZDMS16_2_TZE204",
                                  "MODULE_AVATTO_ZDMS16_2_O9GYSZW2"])
def test_zdms16_2_the_mcus_reset_request_restarts_after_the_leave(name):
    proc = StubProc(device_config=board(name)["config_str"], dp_config=ZDMS16_2_DP_CONFIG).start()
    try:
        device = Device(proc)
        pump(proc)
        assert device.is_joined()
        mcu_reset_request(proc)
        assert any(e.kind == "zcl_leave_network" for e in device._events)
        alive_until_the_restart(proc, device)
    finally:
        proc.stop()


def test_zdms16_2_the_coordinators_remove_restarts_it():
    proc, device = zdms16_2()
    try:
        assert proc.exec("zcl_leave").ok
        alive_until_the_restart(proc, device)
    finally:
        proc.stop()


def test_zdms16_2_comes_up_factory_new_and_steers():
    proc, device = zdms16_2()
    try:
        mcu_reset_request(proc)
        alive_until_the_restart(proc, device)
    finally:
        proc.stop()
    proc, device = zdms16_2(joined=False)                  # the start-up after it
    try:
        device.step_time(100)
        pump(proc)
        assert joining(device) and proc.is_running()
    finally:
        proc.stop()


def test_zdms16_2_the_reset_request_during_the_second_steers_nothing():
    """Not joined any more: a reset request is a steering request then (tuya_mcu_app.c)."""
    proc, device = zdms16_2()
    try:
        mcu_reset_request(proc)
        device.step_time(500)
        mcu_reset_request(proc)
        assert not steered(device)
        device.step_time(DELAY_MS - 500)
        assert proc.wait_for_exit(2.0)
    finally:
        proc.stop()


def test_zdms16_2_reset_requests_after_the_restart_only_steer():
    """Factory new after the restart: the MCU's reset requests are steering requests then."""
    proc, device = zdms16_2()
    try:
        mcu_reset_request(proc)
        alive_until_the_restart(proc, device)
    finally:
        proc.stop()
    proc, device = zdms16_2(joined=False)
    try:
        for _ in range(5):                                 # unjoined: a steering request each
            mcu_reset_request(proc)
            device.step_time(1500)
            pump(proc)
        assert steered(device)
        still_running(proc, device)
    finally:
        proc.stop()


def test_zdms16_2_every_join_gives_its_reset_one_restart():
    for _ in range(3):
        proc, device = zdms16_2(joined=False)
        try:
            device.set_network(1)                           # joined
            mcu_reset_request(proc)
            alive_until_the_restart(proc, device)
        finally:
            proc.stop()


def test_ts110e_the_module_button_restarts_it_with_the_light_kept():
    proc, device = ts110e()
    try:
        device.call_zigbee_cmd(1, 0x0006, 0x01)            # L1 on
        for _ in range(30):
            device.step_time(100)
            pump(proc, 1)
        assert device.is_joined()
        device.press_button(TS110E_BUTTON)
        device.step_time(3500)
        pump(proc, 2)
        device.release_button(TS110E_BUTTON)
        pump(proc, 3)
        assert not device.is_joined()
        alive_until_the_restart(proc, device)
    finally:
        proc.stop()
    proc, device = ts110e(joined=False)                    # a warm start: the light as it was
    try:
        assert int(device.read_zigbee_attr(1, 0x0006, 0x0000)) == 1
    finally:
        proc.stop()


def test_ts110e_the_wall_switch_presses_restart_it():
    proc, device = ts110e()
    try:
        for _ in range(10):
            device.press_button(TS110E_S1)
            device.step_time(100)
            device.release_button(TS110E_S1)
            device.step_time(100)
        for _ in range(40):                                # the channel blinks first
            device.step_time(100)
            pump(proc, 1)
            if not device.is_joined():
                break
        assert not device.is_joined()
        alive_until_the_restart(proc, device)
    finally:
        proc.stop()


def test_a_gpio_reset_button_of_an_mcu_dimmer_restarts_it_when_joined():
    proc = StubProc(device_config=DIMMER_RESET_BUTTON).start()
    try:
        device = Device(proc)
        device.press_button("A0")                          # held: the reset while held
        device.step_time(3000)
        pump(proc)
        assert any(e.kind == "zcl_leave_network" for e in device._events)
        alive_until_the_restart(proc, device)
    finally:
        proc.stop()


def test_a_gpio_reset_button_of_an_unjoined_mcu_dimmer_does_not_restart_it():
    """The Telink stack's reset of a factory-new unit (zb_factoryResetNotify(1)): the NV emptied
    and the leave confirm, status 0, but no network left."""
    proc = StubProc(device_config=DIMMER_RESET_BUTTON, joined=False).start()
    try:
        device = Device(proc)
        for _ in range(4):
            device.long_click_button("A0", 3000)
            pump(proc)
        assert sum(e.kind == "zcl_reset_factory_new" for e in device._events) == 4
        assert not any(e.kind == "zcl_leave_network" for e in device._events)
        still_running(proc, device)
        assert joining(device)
    finally:
        proc.stop()


def test_a_reset_while_joining_does_not_restart():
    """Steering is on its way (the stub's JOINING): still factory new on Telink."""
    proc = StubProc(device_config=DIMMER_RESET_BUTTON, joined=False).start()
    try:
        device = Device(proc)
        device.step_time(100)
        pump(proc)
        assert joining(device)
        device.long_click_button("A0", 3000)
        pump(proc)
        assert any(e.kind == "zcl_reset_factory_new" for e in device._events)
        still_running(proc, device)
    finally:
        proc.stop()


def test_a_relay_boards_reset_stays_in_place():
    """A GPIO relay would drop for the restart and take its power-on setting: no restart there,
    it steers again at once (as before)."""
    proc = StubProc(device_config=RELAY_RESET_BUTTON).start()
    try:
        device = Device(proc)
        device.long_click_button("A0", 3000)
        pump(proc)
        assert any(e.kind == "zcl_leave_network" for e in device._events)
        still_running(proc, device, 3000)
        assert steered(device)
    finally:
        proc.stop()


def test_the_debug_hooks_reset_leaves_in_place_once():
    """0xA3 (MCU_FLASHER_DUMP builds): its reset stays a leave in place, so the join after it
    shows the repair (test_nwk_sec.py); the next reset restarts again."""
    proc, device = zdms16_2()
    try:
        device.write_zigbee_attr(1, BASIC, ATTR_NWK_SEC_TEST, TEST_RESET)
        device.step_time(500)
        pump(proc, 3)
        assert not device.is_joined()
        still_running(proc, device, 3000)
        assert steered(device)
        device.set_network(1)
        mcu_reset_request(proc)
        alive_until_the_restart(proc, device)
    finally:
        proc.stop()


# --- the stub's build variants ----------------------------------------------------------------

VARIANTS = {
    "plain": "-USECONDARY_MCU_IMAGE -UHK_DIMMER -UMCU_FLASHER_DUMP",
    "ts110e": "-DDIMMER_ONLY -USECONDARY_MCU_IMAGE -UMCU_FLASHER_DUMP",
}


@pytest.fixture(scope="module")
def variants(tmp_path_factory) -> dict[str, str]:
    d = tmp_path_factory.mktemp("variants")
    out = {}
    for name, flags in VARIANTS.items():
        binary = d / name
        subprocess.run(["make", "-s", "-C", str(SRC / "stub"), "build", f"BINARY={binary}",
                        f"EXTRA_CFLAGS={flags}"], check=True, capture_output=True)
        out[name] = str(binary)
    return out


def test_a_build_without_it_leaves_the_dimmer_in_place(variants):
    """Another board's build (neither SECONDARY_MCU_IMAGE nor HK_DIMMER): the stack's leave in
    place, steering at once."""
    proc = StubProc(cmd=[variants["plain"]], device_config=DIMMER_RESET_BUTTON).start()
    try:
        device = Device(proc)
        device.long_click_button("A0", 3000)
        pump(proc)
        assert any(e.kind == "zcl_leave_network" for e in device._events)
        still_running(proc, device, 3000)
        assert steered(device)
    finally:
        proc.stop()


@pytest.mark.skipif(shutil.which("nm") is None, reason="no host nm")
def test_a_build_without_it_has_none_of_it(variants):
    out = subprocess.run(["nm", variants["plain"]], capture_output=True, text=True, check=True)
    assert "restart_after_leave" not in out.stdout
    out = subprocess.run(["nm", variants["ts110e"]], capture_output=True, text=True, check=True)
    assert "restart_after_leave" in out.stdout


def test_the_ts110e_build_restarts_after_the_module_button(variants):
    proc = StubProc(cmd=[variants["ts110e"]],
                    device_config=board("MODULE_TUYA_TS110E_3MPWQZUU")["config_str"],
                    dp_config=TS110E_DP_CONFIG).start()
    try:
        device = Device(proc)
        for _ in range(50):
            device.step_time(100)
            pump(proc, 1)
        assert device.is_joined()
        device.press_button(TS110E_BUTTON)
        device.step_time(3500)
        pump(proc, 2)
        device.release_button(TS110E_BUTTON)
        pump(proc, 3)
        assert not device.is_joined()
        alive_until_the_restart(proc, device)
    finally:
        proc.stop()
