#include "zigbee/tuya_mcu_app.h"
#include "zigbee/tuya_mcu_ota.h"
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

/* Firmware version of the MCU (answer to 0x0B), a Basic cluster attribute.
 * Tuya packs it in one byte: 2 bits major, 2 bits minor, 4 bits patch
 * (0x40 = 1.0.0). Empty until the MCU has answered. */
static struct {
    uint8_t len;
    char    text[11]; /* "3.3.15 own" */
} g_mcu_version;

static uint8_t g_mcu_packed;    /* last version byte the MCU told */
static uint8_t g_mcu_known;
static uint8_t g_mcu_own;       /* it answered the identity query */

static void mcu_version_store(uint8_t packed) {
    uint8_t patch = packed & 0x0F;
    uint8_t n     = 0;

    g_mcu_packed = packed;
    g_mcu_known  = 1;

    g_mcu_version.text[n++] = (char)('0' + (packed >> 6));
    g_mcu_version.text[n++] = '.';
    g_mcu_version.text[n++] = (char)('0' + ((packed >> 4) & 0x03));
    g_mcu_version.text[n++] = '.';
    if (patch >= 10) {
        g_mcu_version.text[n++] = '1';
    }
    g_mcu_version.text[n++] = (char)('0' + patch % 10);
    if (g_mcu_own) {
        g_mcu_version.text[n++] = ' ';
        g_mcu_version.text[n++] = 'o';
        g_mcu_version.text[n++] = 'w';
        g_mcu_version.text[n++] = 'n';
    }
    g_mcu_version.len = n;

    hal_zigbee_send_report_attr(endpoints[0].endpoint, ZCL_CLUSTER_BASIC,
                                ZCL_ATTR_BASIC_MCU_VERSION, ZCL_DATA_TYPE_CHAR_STR,
                                &g_mcu_version, (uint8_t)(n + 1));
}

uint8_t tuya_mcu_app_register_attrs(hal_zigbee_attribute *table, uint8_t start_index) {
    hal_zigbee_attribute *attr = &table[start_index];

    attr->attribute_id = ZCL_ATTR_BASIC_MCU_VERSION;
    attr->data_type_id = ZCL_DATA_TYPE_CHAR_STR;
    attr->flag         = ATTR_READONLY;
    attr->size         = (uint8_t)sizeof(g_mcu_version);
    attr->value        = (uint8_t *)&g_mcu_version;
    return 1;
}

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

/* ---- version query ----------------------------------------------------------
 *
 * The answer can be lost (a dropped UART burst, the MCU busy writing its
 * flash): asked again a few times. The wait covers a full TX queue (8 frames
 * of up to 150 ms each) ahead of the question.
 */
#define VERSION_TIMEOUT_MS    2000
#define VERSION_RETRIES       2

static hal_task_t g_version_timer;
static uint8_t    g_version_waiting;
static uint8_t    g_version_retries;

static void version_ask(void) {
    g_version_waiting = 1;
    tuya_secondary_mcu_queue_cmd(TUYA_MCU_CMD_MCU_VERSION, NULL, 0);
    hal_tasks_schedule(&g_version_timer, VERSION_TIMEOUT_MS);
}

static void version_timeout(void *arg) {
    (void)arg;
    if (!g_version_waiting) {
        return;
    }
    if (g_version_retries >= VERSION_RETRIES) {
        g_version_waiting = 0;          /* an MCU that does not tell */
        return;
    }
    g_version_retries++;
    version_ask();
}

static void version_answered(void) {
    g_version_waiting = 0;
    hal_tasks_unschedule(&g_version_timer);
}

/* ---- first contact ----------------------------------------------------------
 *
 * Apply the IT-declared DP values and ask for a full DP dump once the MCU
 * listens. Not from app init: writing the config string reboots only the
 * Zigbee side, the MCU keeps running and drops what it gets before it is back
 * in sync. The MCU's own first network status query is the reliable signal.
 * Some MCUs never send one (the ZDMS16-2's does not), so a timer shortly after
 * boot does it too; if the MCU's query still comes later, it is done again,
 * since what the timer sent may have been dropped. Not for our own MCU once it
 * has said who it is: it got and answered everything (a restart of it shows
 * in the query, see query_means_restart()).
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
    if (source == CONTACT_MCU && g_contact == CONTACT_TIMER && g_mcu_own) {
        g_contact = CONTACT_MCU;
        return;
    }
    g_contact = source;
    g_mcu_own = 0;
    tuya_secondary_mcu_set_fast_acks(0, NULL);
    tuya_dp_apply_inits();
    g_version_retries = 0;
    version_ask();

    /* Dimmers keep min/max/switch type/power-on in the MCU: the dump is the
     * only way to learn them after a reboot. */
    if (dp_attrs_cnt > 0 || dimmer_clusters_cnt > 0) {
        dp_attr_query_all();
    }
}

static void resync_all(void) {
    dp_attr_query_all();
}

void tuya_mcu_app_mcu_restarted(void) {
    g_contact = CONTACT_NONE;
    g_mcu_own = 0;
    tuya_secondary_mcu_set_fast_acks(0, NULL);
    version_answered();                 /* asked again at the next contact */
    tuya_mcu_ota_mcu_restarted();
}

/* Our own MCU firmware numbers its network queries from 0 at every start, so
 * seq 0 again means it restarted alone (reflash, watchdog, update), unless it
 * follows 0xFFFF: then the counter wrapped (every ~23 days). A 2.x MCU that
 * has not said who it is yet may be ours too (its identity answer lost, or it
 * restarted while it was asked); a few times only, as a stock MCU may number
 * every query 0. */
#define UNIDENTIFIED_RESTARTS_MAX    3

static uint16_t g_last_query_seq;
static uint8_t  g_unidentified_restarts;

static uint8_t query_means_restart(uint16_t seq) {
    if (seq != 0 || g_last_query_seq == 0xFFFF) {
        return 0;
    }
    if (g_mcu_own) {
        return 1;
    }
    if (g_mcu_known && tuya_mcu_ota_may_be_own(g_mcu_packed) &&
        g_unidentified_restarts < UNIDENTIFIED_RESTARTS_MAX) {
        g_unidentified_restarts++;
        return 1;
    }
    return 0;
}

void tuya_mcu_app_show_mcu_version(void) {
    if (g_mcu_known) {
        mcu_version_store(g_mcu_packed);
    }
}

uint8_t tuya_mcu_app_mcu_is_own(void) {
    return g_mcu_own;
}

void tuya_mcu_app_set_mcu_own(void) {
    uint8_t was_own = g_mcu_own;

    g_mcu_own = 1;
    g_unidentified_restarts = 0;
    tuya_secondary_mcu_set_fast_acks(1, resync_all);
    tuya_mcu_app_show_mcu_version();

    /* The first-contact dump (and the MCU's own one after its start) comes
     * before the answer to the identity query, so it was read as a stock
     * MCU's: shared DP 14 set every channel's power-on and the per-channel
     * DPs were dropped. Read it all again, now as ours. */
    if (!was_own && dimmer_clusters_cnt > 0) {
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
    /* Settings DPs on a dimmer (e.g. the own firmware's dimming curves) do
     * not make its module button a user interface: it stays the reset. */
    return tuya_dp_relay_count() > 0 || (dp_attrs_cnt > 0 && dimmer_clusters_cnt == 0);
}

static uint8_t network_status(void) {
    return hal_zigbee_get_network_status() == HAL_ZIGBEE_NETWORK_JOINED
           ? TUYA_MCU_NETWORK_CONNECTED
           : TUYA_MCU_NETWORK_NOT_CONNECTED;
}

static void on_mcu_command(uint8_t cmd, uint16_t seq, const uint8_t *data, uint16_t data_len) {
    switch (cmd) {
    case TUYA_MCU_CMD_MCU_VERSION:
        /* The answer to our query, or sent on its own after pairing. */
        if (data_len >= 1) {
            version_answered();
            g_mcu_packed = data[0];
            g_mcu_known  = 1;

            /* 2.x or later may be our own firmware: shown once it has said
             * who it is, so ours never shows without "own". */
            if (!tuya_mcu_ota_on_version(data[0])) {
                mcu_version_store(data[0]);
            }
        }
        return;

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
        if (query_means_restart(seq)) {
            tuya_mcu_app_mcu_restarted();
        }
        g_last_query_seq = seq;
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
        tuya_mcu_ota_on_command(cmd, seq, data, data_len);
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

    g_version_timer.handler = version_timeout;
    g_version_timer.arg     = NULL;
    hal_tasks_init(&g_version_timer);

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
