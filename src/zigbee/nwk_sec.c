#include "nwk_sec.h"
#ifdef MCU_FLASHER_DUMP
#include "hal/system.h"
#include "hal/tasks.h"
#include "hal/zigbee.h"
#endif

uint8_t g_nwk_sec_state = NWK_SEC_SECURED;

#ifdef MCU_FLASHER_DUMP
uint8_t           g_nwk_sec_test_cmd;
uint8_t           g_nwk_sec_test_keep_running;
static hal_task_t nwk_sec_test_task;

static void nwk_sec_test_reset(void *arg) {
    hal_factory_reset();
}

void nwk_sec_test_on_write(void) {
    uint8_t cmd = g_nwk_sec_test_cmd;

    g_nwk_sec_test_cmd = 0;
    if (cmd < NWK_SEC_TEST_RAM || cmd > NWK_SEC_TEST_RESET) {
        return;
    }
    hal_zigbee_nwk_sec_clear(cmd == NWK_SEC_TEST_NV);
    g_nwk_sec_state = NWK_SEC_UNSECURED;
    if (cmd == NWK_SEC_TEST_RESET) {
        g_nwk_sec_test_keep_running = 1;
        nwk_sec_test_task.handler   = nwk_sec_test_reset;
        hal_tasks_init(&nwk_sec_test_task);
        hal_tasks_schedule(&nwk_sec_test_task, NWK_SEC_TEST_RESET_DELAY_MS);
    }
}

#endif
