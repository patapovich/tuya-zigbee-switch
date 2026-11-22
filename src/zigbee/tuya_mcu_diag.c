#include "zigbee/tuya_mcu_diag.h"
#include "zigbee/consts.h"
#include "hal/zigbee_limits.h"
#include "hal/timer.h"

#include <stddef.h>
#include <string.h>

#define DIAG_FORMAT          1
#define DIAG_ENTRY_SIZE      8
#define DIAG_PAGE_ENTRIES    6 /* 48 bytes: one read response fits one frame */
#define DIAG_LOG_ENTRIES     (DIAG_PAGE_ENTRIES * TUYA_MCU_DIAG_LOG_PAGES)

tuya_mcu_diag_counters_t tuya_mcu_diag;
uint16_t hal_zigbee_tables_dropped;

uint32_t tuya_mcu_diag_number(const uint8_t *data, uint16_t len) {
    uint32_t value = 0;

    for (uint16_t i = 0; data != NULL && i < len && i < 4; i++) {
        value = (value << 8) | data[i];
    }
    return value;
}

uint16_t tuya_mcu_diag_ms(uint32_t ms) {
    return ms > 0xFFFF ? 0xFFFF : ms == 0 ? 1 : (uint16_t)ms;
}

#ifdef MCU_FLASHER_DUMP   /* debug builds: the attributes and the event log */
typedef struct {
    uint8_t len;
    uint8_t data[DIAG_PAGE_ENTRIES * DIAG_ENTRY_SIZE];
} tuya_mcu_diag_page_t;

static tuya_mcu_diag_page_t g_pages[TUYA_MCU_DIAG_LOG_PAGES];
static uint8_t g_log[DIAG_LOG_ENTRIES * DIAG_ENTRY_SIZE];
static uint8_t g_log_count = 0;
static uint8_t g_control   = 0;

/* The fields from ota_failures up to rescues are state, not counters: a clear
 * keeps them. */
#define DIAG_STATE_START    offsetof(tuya_mcu_diag_counters_t, ota_failures)
#define DIAG_STATE_END      offsetof(tuya_mcu_diag_counters_t, rescues)

void tuya_mcu_diag_clear(void) {
    uint8_t state[DIAG_STATE_END - DIAG_STATE_START];

    memcpy(state, (uint8_t *)&tuya_mcu_diag + DIAG_STATE_START, sizeof(state));
    memset(&tuya_mcu_diag, 0, sizeof(tuya_mcu_diag));
    tuya_mcu_diag.len    = (uint8_t)(sizeof(tuya_mcu_diag) - 1);
    tuya_mcu_diag.format = DIAG_FORMAT;
    memcpy((uint8_t *)&tuya_mcu_diag + DIAG_STATE_START, state, sizeof(state));
    tuya_mcu_diag.zcl_tables_dropped = hal_zigbee_tables_dropped;   /* since the start */
    memset(g_pages, 0, sizeof(g_pages));
    g_log_count = 0;
}

void tuya_mcu_diag_log(uint8_t kind, uint8_t id, uint32_t value) {
    if (tuya_mcu_diag.len == 0) {
        tuya_mcu_diag_clear();
    }

    uint32_t now = hal_millis();
    tuya_mcu_diag.last_event_ms = now;
    tuya_mcu_diag.log_total++;

    if (g_log_count == DIAG_LOG_ENTRIES) {
        memmove(g_log, g_log + DIAG_ENTRY_SIZE, (DIAG_LOG_ENTRIES - 1) * DIAG_ENTRY_SIZE);
        g_log_count--;
    }
    uint8_t *e = &g_log[g_log_count * DIAG_ENTRY_SIZE];
    e[0] = (uint8_t)now;
    e[1] = (uint8_t)(now >> 8);
    e[2] = kind;
    e[3] = id;
    e[4] = (uint8_t)value;
    e[5] = (uint8_t)(value >> 8);
    e[6] = (uint8_t)(value >> 16);
    e[7] = (uint8_t)(value >> 24);
    g_log_count++;

    /* Rebuild the pages: oldest entry first, page 0 first. */
    uint16_t total = (uint16_t)(g_log_count * DIAG_ENTRY_SIZE);
    for (uint8_t k = 0; k < TUYA_MCU_DIAG_LOG_PAGES; k++) {
        uint16_t off = (uint16_t)(k * sizeof(g_pages[k].data));
        uint16_t n   = total > off ? (uint16_t)(total - off) : 0;
        if (n > sizeof(g_pages[k].data)) {
            n = sizeof(g_pages[k].data);
        }
        g_pages[k].len = (uint8_t)n;
        if (n > 0) {
            memcpy(g_pages[k].data, g_log + off, n);
        }
    }
}

static void diag_attr(hal_zigbee_attribute *attr, uint16_t id, uint8_t type,
                      uint8_t flag, void *value, uint8_t size) {
    attr->attribute_id = id;
    attr->data_type_id = type;
    attr->flag         = flag;
    attr->size         = size;
    attr->value        = (uint8_t *)value;
}

uint8_t tuya_mcu_diag_register(hal_zigbee_attribute *table, uint8_t start_index) {
    uint8_t i = start_index;

    if (tuya_mcu_diag.len == 0) {
        tuya_mcu_diag_clear();
    }
    diag_attr(&table[i++], ZCL_ATTR_BASIC_MCU_DIAG_COUNTERS, ZCL_DATA_TYPE_OCTET_STR,
              ATTR_READONLY, &tuya_mcu_diag, (uint8_t)sizeof(tuya_mcu_diag));
    for (uint8_t k = 0; k < TUYA_MCU_DIAG_LOG_PAGES; k++) {
        diag_attr(&table[i++], (uint16_t)(ZCL_ATTR_BASIC_MCU_DIAG_LOG_FIRST + k),
                  ZCL_DATA_TYPE_OCTET_STR, ATTR_READONLY, &g_pages[k],
                  (uint8_t)sizeof(g_pages[k]));
    }
    diag_attr(&table[i++], ZCL_ATTR_BASIC_MCU_DIAG_CONTROL, ZCL_DATA_TYPE_UINT8,
              ATTR_WRITABLE, &g_control, (uint8_t)sizeof(g_control));
    return (uint8_t)(i - start_index);
}

uint8_t tuya_mcu_diag_on_write(uint16_t attribute_id) {
    if (attribute_id != ZCL_ATTR_BASIC_MCU_DIAG_CONTROL) {
        return 0;
    }
    uint8_t command = g_control;
    if (command == TUYA_MCU_DIAG_CONTROL_CLEAR) {
        tuya_mcu_diag_clear();
    }
    g_control = 0;
    return command;
}

#endif /* MCU_FLASHER_DUMP */
