#include "reset.h"
#include "hal/nvm.h"
#include "hal/printf_selector.h"
#include "hal/system.h"
#include "hal/tasks.h"
#include <stdint.h>
#ifdef HK_DIMMER
#include "zigbee/hk_dimmer.h"
#endif
#ifdef LEAVE_RESTART
#include "config_parser.h"
#ifdef MCU_FLASHER_DUMP
#include "zigbee/nwk_sec.h"
#endif
#endif

static hal_task_t reset_task;

__attribute__((noreturn)) void reset_all() {
    printf("RESET ALL!\r\n");
    hal_nvm_clear_all();
    hal_factory_reset();
    hal_system_reset();
}

void reset_all_handler(void *arg) {
    reset_all();
}

void reboot_handler(void *arg) {
#ifdef HK_DIMMER
    hk_dimmer_before_reboot();  /* its state saved now (the light stays as it is) */
#endif
    hal_system_reset();
}

void schedule_full_reset(uint16_t delay_ms) {
    reset_task.handler = reset_all_handler;
    hal_tasks_init(&reset_task);
    hal_tasks_schedule(&reset_task,
                       delay_ms != 0 ? delay_ms : DEFAULT_RESET_DELAY_MS);
}

void schedule_reboot(uint16_t delay_ms) {
    reset_task.handler = reboot_handler;
    hal_tasks_init(&reset_task);
    hal_tasks_schedule(&reset_task,
                       delay_ms != 0 ? delay_ms : DEFAULT_RESET_DELAY_MS);
}

#ifdef LEAVE_RESTART

uint8_t g_leave_restart;

/* Whether a restart leaves the outputs alone: they are all the secondary MCU's dimmers, which it
 * keeps driving. Every board of these builds (DIMMER_ONLY); the stub, built with every feature
 * for every board, tells it from the parsed config (a relay drops for a restart and then takes
 * its power-on setting). */
#ifdef DIMMER_ONLY
#define RESTART_KEEPS_THE_OUTPUTS()    1
#else
extern uint8_t relays_cnt;
#define RESTART_KEEPS_THE_OUTPUTS()    (relays_cnt == 0 && dimmer_clusters_cnt != 0)
#endif

/* The stack's own leave empties the network NV and sets the stack up again in place, but the
 * security record is set up only at the start-up (ss_zdoInit() in bdb_init(): the "secure all"
 * flag, the pre-configured key type, the key pairs, the TC link key pointers), so a join straight
 * after the leave saved whatever RAM held (zigbee/nwk_sec.h). The module restarts first, as the
 * SDK's own router app does after a leave (apps/sampleLight/zb_appCb.c, 2 s), and joins from a
 * factory-new start-up. Only after a network was left: a reset of a unit already factory new is
 * confirmed as the same leave, and there is nothing to start fresh. A restart comes up factory
 * new, so the next one needs a join before it: repeated resets restart once, never in a loop. */
void restart_after_leave(uint8_t left_network) {
#ifdef MCU_FLASHER_DUMP
    uint8_t keep = g_nwk_sec_test_keep_running;

    g_nwk_sec_test_keep_running = 0;   /* the test hook's leave in place: this leave only */
    if (keep) {
        return;
    }
#endif
    if (!left_network || !RESTART_KEEPS_THE_OUTPUTS()) {
        return;
    }
    g_leave_restart = 1;
    schedule_reboot(LEAVE_RESTART_DELAY_MS);
}

#endif
