#include "zigbee/tuya_mcu_app.h"
#include "device_config/config_parser.h"
#include "hal/printf_selector.h"
#include "hal/system.h"
#include "hal/tasks.h"
#include "hal/uart.h"
#include "hal/zigbee.h"
#include "zigbee/consts.h"
#include "zigbee/dp_attr.h"
#include "zigbee/tuya_dp_relay.h"
#include "zigbee/tuya_mcu_diag.h"
#include "zigbee/tuya_secondary_mcu.h"

extern hal_uart_config_t mcu_uart_config;

static bool g_in_use = false;

bool tuya_mcu_app_in_use(void) {
    return dimmer_clusters_cnt > 0 || tuya_dp_relay_count() > 0 || dp_attrs_cnt > 0;
}

uint8_t tuya_mcu_power_on_from_startup(uint8_t startup_mode) {
    switch (startup_mode) {
    case ZCL_START_UP_ONOFF_SET_ONOFF_TO_OFF:
        return 0;

    case ZCL_START_UP_ONOFF_SET_ONOFF_TO_ON:
        return 1;

    default:
        return 2;
    }
}

uint8_t tuya_mcu_startup_from_power_on(uint8_t power_on) {
    switch (power_on) {
    case 0:
        return ZCL_START_UP_ONOFF_SET_ONOFF_TO_OFF;

    case 1:
        return ZCL_START_UP_ONOFF_SET_ONOFF_TO_ON;

    default:
        return ZCL_START_UP_ONOFF_SET_ONOFF_TO_PREVIOUS;
    }
}

void tuya_mcu_app_query_all(void) {
    dp_attr_query_all();
}

/* ---- first contact ----------------------------------------------------------
 *
 * Apply the IT-declared DP values and ask for a full DP dump once the MCU
 * listens. Not from app init: writing the config string reboots only the
 * Zigbee side, the MCU keeps running and drops what it gets before it is back
 * in sync. The MCU's own first network status query is the reliable signal.
 * Some MCUs never send one (the ZDMS16-2's does not), so a timer shortly after
 * boot does it too; if the MCU's query still comes later, it is done again,
 * since what the timer sent may have been dropped.
 */
#define CONTACT_NONE              0
#define CONTACT_TIMER             1
#define CONTACT_MCU               2
#define FIRST_CONTACT_DELAY_MS    2000

static uint8_t    g_contact = CONTACT_NONE;
static hal_task_t g_contact_timer;

static void first_contact(uint8_t source) {
    if (g_contact == CONTACT_MCU || (source == CONTACT_TIMER && g_contact != CONTACT_NONE)) {
        return;
    }
    g_contact = source;
    tuya_dp_apply_inits();

    /* Dimmers keep min/max/switch type/power-on in the MCU: the dump is the
     * only way to learn them after a reboot. */
    if (dp_attrs_cnt > 0 || dimmer_clusters_cnt > 0) {
        dp_attr_query_all();
    }
}

static void first_contact_timer(void *arg) {
    (void)arg;
    first_contact(CONTACT_TIMER);
}

/* Relays and DP attributes driven by the MCU mean its keys are the switch's
 * user interface (touch panels). A dimmer module has no keys on the MCU: the
 * wall switch input dims and toggles, only the module's button asks for a
 * reset. */
static bool mcu_keys_are_the_ui(void) {
    return tuya_dp_relay_count() > 0 || dp_attrs_cnt > 0;
}

static uint8_t network_status(void) {
    return hal_zigbee_get_network_status() == HAL_ZIGBEE_NETWORK_JOINED
           ? TUYA_MCU_NETWORK_CONNECTED
           : TUYA_MCU_NETWORK_NOT_CONNECTED;
}

static void on_mcu_command(uint8_t cmd, uint16_t seq, const uint8_t *data, uint16_t data_len) {
    switch (cmd) {
    case TUYA_MCU_CMD_RESET_NETWORK:
        if (data_len != 1 || data[0] != TUYA_MCU_RESET_NETWORK_REJOIN) {
            return;
        }
        if (hal_zigbee_get_network_status() == HAL_ZIGBEE_NETWORK_JOINED) {
            if (mcu_keys_are_the_ui()) {
                /* On a touch switch a held key makes the MCU ask us to leave
                 * and re-pair. Those keys are what people touch every day,
                 * and an accidental long press is the most common way a
                 * switch drops off a working network, so we refuse and tell
                 * the MCU we are connected, which also stops its pairing
                 * blink. */
                uint8_t status = TUYA_MCU_NETWORK_CONNECTED;
                printf("Ignoring MCU leave request: already joined\r\n");
                tuya_secondary_mcu_queue_cmd(TUYA_MCU_CMD_NETWORK_STATUS, &status, 1);
            } else {
                /* Without MCU keys (a dimmer module) the request can only
                 * come from the module's own reset button: do what a reset
                 * button does on every other device. */
                printf("MCU reset button: factory reset\r\n");
                hal_factory_reset();
            }
        } else {
            printf("MCU leave request while unjoined: restarting steering\r\n");
            hal_zigbee_start_network_steering();
        }
        return;

    case TUYA_MCU_CMD_QUERY_NETWORK: {
        /* Unanswered, the MCU assumes the link is broken (key LEDs blink). */
        uint8_t status = network_status();
        tuya_secondary_mcu_send_reply(cmd, seq, &status, 1);
        first_contact(CONTACT_MCU);
        return;
    }

    case TUYA_MCU_CMD_QUERY_GATEWAY: {
        /* We are the gateway as far as the MCU knows. */
        uint8_t status = network_status();
        tuya_secondary_mcu_send_reply(cmd, seq, &status, 1);
        return;
    }

    case TUYA_MCU_CMD_SYNC_TIME: {
        /* Eight bytes, UTC then local time. We have no clock: zeros, so the
         * MCU gets its answer. */
        uint8_t t[8] = { 0 };
        tuya_secondary_mcu_send_reply(cmd, seq, t, sizeof(t));
        return;
    }

    default:
        return;
    }
}

void tuya_mcu_app_init(void) {
    /* Gated on the config: starting the UART would take over pins that many
     * boards use as GPIOs. */
    g_in_use = tuya_mcu_app_in_use();
    if (!g_in_use) {
        return;
    }
    tuya_mcu_diag_clear();
    tuya_secondary_mcu_init(&mcu_uart_config);
    tuya_dp_relay_init();
    dp_attr_init();
    tuya_secondary_mcu_set_command_callback(on_mcu_command);

    g_contact_timer.handler = first_contact_timer;
    g_contact_timer.arg     = NULL;
    hal_tasks_init(&g_contact_timer);
    hal_tasks_schedule(&g_contact_timer, FIRST_CONTACT_DELAY_MS);
}

void tuya_mcu_app_task(void) {
    if (g_in_use) {
        tuya_secondary_mcu_poll();
    }
}
