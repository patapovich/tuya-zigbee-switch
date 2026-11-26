#include "tclk_check.h"
#include "hal/tasks.h"
#include "hal/zigbee.h"
#include "zigbee/consts.h"
#include "zigbee/nwk_sec.h"

/* One struct, zeroed at start-up (the task's handler is set where it is scheduled): one address
 * to load for all of it and no initial values in flash. */
static struct {
    hal_task_t task;
    uint8_t    waiting;       /* an Update Device waits for its tunnel */
    uint8_t    misses;        /* windows in a row without one */
    uint8_t    before;        /* the state NWK_SEC_TC_KEY_STALE stands in for */
    uint8_t    network_wide;  /* the last Mgmt_Permit_Joining opened joining network-wide (0 at a
                               * start-up: not known until the next one) */
} tclk;

/* The state, reported to the Basic cluster's bindings (the converter binds it to the
 * coordinator): Zigbee2MQTT reads the attribute only at a configure, and children that cannot
 * join are what makes someone look. The Basic cluster is on endpoint 1 (endpoints[0]). */
static void tclk_tell(uint8_t state) {
    g_nwk_sec_state = state;
    hal_zigbee_send_report_attr(1, ZCL_CLUSTER_BASIC, ZCL_ATTR_BASIC_NWK_SECURITY,
                                ZCL_DATA_TYPE_ENUM8, &g_nwk_sec_state, 1);
}

static void tclk_window_end(void *arg) {
    tclk.waiting = 0;
    /* the count before this miss (no extension of the new one), so TCLK_CHECK_MISSES or more */
    if (tclk.misses++ >= TCLK_CHECK_MISSES - 1 && g_nwk_sec_state != NWK_SEC_TC_KEY_STALE) {
        tclk.before = g_nwk_sec_state;
        tclk_tell(NWK_SEC_TC_KEY_STALE);
    }
}

void tclk_check_update_device(unsigned status, unsigned tc_link_key_type, unsigned permitting) {
    /* (status | 2) == 3: 1 or 3; only while joining is open network-wide (tclk_check.h) */
    if (tc_link_key_type == 1 || (status | 2u) != 3u || tclk.waiting || !permitting ||
        !tclk.network_wide) {
        return;
    }
    tclk.waiting      = 1;
    tclk.task.handler = tclk_window_end;
    hal_tasks_schedule(&tclk.task, TCLK_CHECK_WINDOW_MS);
}

void tclk_check_permit_joining(unsigned network_wide) {
    tclk.network_wide = network_wide != 0;
}

void tclk_check_ok(void) {
    hal_tasks_unschedule(&tclk.task);
    tclk.waiting = 0;
    tclk.misses  = 0;
    if (g_nwk_sec_state == NWK_SEC_TC_KEY_STALE) {
        tclk_tell(tclk.before);
    }
}
