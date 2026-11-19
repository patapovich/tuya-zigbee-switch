/* Only in builds that carry an MCU image (src/telink/Makefile; the stub is
 * also built without, for tests/test_open_dimmer_symbols.py). */
#ifdef SECONDARY_MCU_IMAGE

#include "mcu_flasher.h"

#include <stddef.h>
#include <string.h>

#include "base_components/swd.h"
#include "device_config/config_parser.h"
#include "hal/gpio.h"
#include "hal/uart.h"
#include "hal/timer.h"
#include "zigbee/consts.h"
#include "zigbee/tuya_mcu_ota.h"

/* The ZDMS16-2's PY32F002A as read with an external probe (the identity gate:
 * the plan's section 0 item 6). */
#define PY32_DPIDR            0x0BC11477u
#define PY32_AP_IDR           0x04770031u
#define PY32_CPUID            0x410CC601u
#define PY32_IDCODE           0x60001000u

#define ADDR_CPUID            0xE000ED00u
#define ADDR_DBGMCU_IDCODE    0x40015800u
#define ADDR_FLASH_OPTR       0x40022020u
#define ADDR_FLASH_SDKR       0x40022024u
#define ADDR_FLASH_WRPR       0x4002202Cu
#define ADDR_OPTION_AREA      0x1FFF0E80u
#define ADDR_UID              0x1FFF0E00u
#define ADDR_ROM              0x1FFF0000u
#define ROM_WORDS             (0xE00u / 4u)
#define ADDR_FLASH            0x08000000u
#define SECTOR0_WORDS         (0x1000u / 4u)
#define ADDR_BOOT_ID          0x08000010u
#define ADDR_APP_HEADER       0x080010C0u

#define SLICE_WORDS           32u /* per main-loop pass: a few ms of SWD */
#define TRIES_PER_STEP        3u
#define REPORT_EVERY_MS       1000u

/* Steps; survey_dp stops after STEP_CONNECT. */
enum {
    STEP_BEGIN = 0,
    STEP_CONNECT,
    STEP_POWER,
    STEP_IDENTITY,
    STEP_ROM,
    STEP_SECTOR0,
    STEP_HEADERS,
    STEP_END,
};

extern hal_uart_config_t mcu_uart_config; /* config_parser.c: the W token */

static hal_gpio_pin_t g_clk = HAL_INVALID_PIN;
static hal_gpio_pin_t g_dio = HAL_INVALID_PIN;
static uint8_t        g_enabled;
static uint8_t        g_control;
static uint8_t        g_abort;
static uint8_t        g_tries;
static uint16_t       g_word;      /* words done in the current CRC step */
static uint32_t       g_crc;
static uint32_t       g_started;
static uint32_t       g_last_report;

static mcu_flasher_status_t g_status;

static uint16_t sat16(uint32_t v) {
    return v > 0xFFFFu ? 0xFFFFu : (uint16_t)v;
}

void mcu_flasher_parse_token(const char *entry) {
    /* SW<clk><dio>, e.g. SWD3D7. Never the MCU link's default pins (B1, B7)
     * or the Telink single-wire debug pad (A7), never twice the same pin:
     * else the feature stays off. */
    static const char forbidden[] = "B1B7A7";

    if (strlen(entry) != 6 || memcmp(entry + 2, entry + 4, 2) == 0) {
        return;
    }
    for (uint8_t i = 0; i < sizeof(forbidden) - 1; i += 2) {
        if (memcmp(entry + 2, forbidden + i, 2) == 0 || memcmp(entry + 4, forbidden + i, 2) == 0) {
            return;
        }
    }
    g_clk = hal_gpio_parse_pin(entry + 2);
    g_dio = hal_gpio_parse_pin(entry + 4);
}

uint8_t mcu_flasher_register_attrs(hal_zigbee_attribute *table, uint8_t start_index) {
    /* A W token's UART pins (0 without one) are not ours either. */
    hal_gpio_pin_t tx = (hal_gpio_pin_t)mcu_uart_config.tx_pin;
    hal_gpio_pin_t rx = (hal_gpio_pin_t)mcu_uart_config.rx_pin;

    g_enabled = g_clk != HAL_INVALID_PIN && g_dio != HAL_INVALID_PIN &&
                !(tx != 0 && (tx == g_clk || tx == g_dio)) &&
                !(rx != 0 && (rx == g_clk || rx == g_dio));
    if (!g_enabled) {
        return 0;
    }
    g_status.len    = (uint8_t)(sizeof(g_status) - 1);
    g_status.format = 1;
    hal_zigbee_attribute *a = &table[start_index];

    a[0].attribute_id = ZCL_ATTR_BASIC_MCU_FLASHER_STATUS;
    a[0].data_type_id = ZCL_DATA_TYPE_OCTET_STR;
    a[0].flag         = ATTR_READONLY;
    a[0].size         = (uint8_t)sizeof(g_status);
    a[0].value        = (uint8_t *)&g_status;
    a[1].attribute_id = ZCL_ATTR_BASIC_MCU_FLASHER_CONTROL;
    a[1].data_type_id = ZCL_DATA_TYPE_UINT8;
    a[1].flag         = ATTR_WRITABLE;
    a[1].size         = (uint8_t)sizeof(g_control);
    a[1].value        = &g_control;
    return MCU_FLASHER_ATTRS;
}

static void report(void) {
    g_last_report = hal_millis();
    hal_zigbee_send_report_attr(endpoints[0].endpoint, ZCL_CLUSTER_BASIC,
                                ZCL_ATTR_BASIC_MCU_FLASHER_STATUS, ZCL_DATA_TYPE_OCTET_STR,
                                &g_status, (uint8_t)sizeof(g_status));
}

static void finish(uint8_t state, uint8_t error, swd_err_t swd_err) {
    if (g_status.step > STEP_POWER && swd_err == SWD_OK) {
        swd_power_down();
    }
    swd_end();
    g_status.state       = state;
    g_status.error       = error;
    g_status.swd_err     = (uint8_t)swd_err;
    g_status.duration_ms = sat16(hal_millis() - g_started);
    if (state == MCU_FLASHER_STATE_DONE) {
        g_status.progress = 100;
    }
    report();
}

void mcu_flasher_on_write(uint16_t attribute_id) {
    if (!g_enabled || attribute_id != ZCL_ATTR_BASIC_MCU_FLASHER_CONTROL) {
        return;
    }
    uint8_t job = g_control;

    g_control = 0;
    if (job == MCU_FLASHER_JOB_ABORT) {
        g_abort = g_status.state == MCU_FLASHER_STATE_RUNNING;
        return;
    }
    if (g_status.state == MCU_FLASHER_STATE_RUNNING) {
        return; /* one job at a time; the running one is not disturbed */
    }
    if (job != MCU_FLASHER_JOB_SURVEY_DP && job != MCU_FLASHER_JOB_SURVEY_FULL) {
        g_status.error = MCU_FLASHER_ERR_BAD_JOB;
        report();
        return;
    }
    uint8_t len = g_status.len, format = g_status.format;

    memset(&g_status, 0, sizeof(g_status));
    g_status.len        = len;
    g_status.format     = format;
    g_status.state      = MCU_FLASHER_STATE_RUNNING;
    g_status.job        = job;
    g_status.step       = STEP_BEGIN;
    g_started           = hal_millis();
    g_status.started_ms = sat16(g_started);
    g_abort             = 0;
    g_tries             = 0;
    report();
}

static swd_err_t read_crc16(uint32_t addr, uint32_t *crc) {
    uint32_t  w[4];
    swd_err_t err = swd_mem_read_block(addr, w, 4);

    if (err == SWD_OK) {
        *crc = tuya_mcu_crc32_update(0, (const uint8_t *)w, sizeof(w));
    }
    return err;
}

/* The status is packed (an attribute): its words are filled by copy, never
 * through a uint32_t pointer (unaligned on the TC32). */
#define STATUS_AT(field)    ((uint8_t *)&g_status + offsetof(mcu_flasher_status_t, field))

/* AP IDR, CPUID, IDCODE, OPTR, SDKR, WRPR, option bytes CRC, UID CRC: in this
 * order in the status. */
static swd_err_t identity(void) {
    static const uint32_t addr[] = {
        ADDR_CPUID, ADDR_DBGMCU_IDCODE, ADDR_FLASH_OPTR, ADDR_FLASH_SDKR, ADDR_FLASH_WRPR,
    };
    uint32_t  v[8];
    swd_err_t err = swd_ap_read(SWD_AP_IDR, &v[0]);

    for (uint8_t i = 0; err == SWD_OK && i < sizeof(addr) / sizeof(addr[0]); i++) {
        err = swd_mem_read32(addr[i], &v[1 + i]);
    }
    if (err == SWD_OK) {
        err = read_crc16(ADDR_OPTION_AREA, &v[6]);
    }
    if (err == SWD_OK) {
        err = read_crc16(ADDR_UID, &v[7]);
    }
    if (err == SWD_OK) {
        memcpy(STATUS_AT(ap_idr), v, sizeof(v));
        g_status.rdp = (uint8_t)(v[3] & 0xFFu);
    }
    return err;
}

/* One slice of a CRC over count words from base; 1 when it is complete. */
static swd_err_t crc_slice(uint32_t base, uint16_t count, uint8_t *result, uint8_t *complete) {
    uint32_t w[SLICE_WORDS];
    uint16_t n = (uint16_t)(count - g_word);

    if (n > SLICE_WORDS) {
        n = SLICE_WORDS;
    }
    swd_err_t err = swd_mem_read_block(base + 4u * g_word, w, n);

    if (err != SWD_OK) {
        return err;
    }
    g_crc     = tuya_mcu_crc32_update(g_crc, (const uint8_t *)w, 4u * n);
    g_word    = (uint16_t)(g_word + n);
    *complete = g_word >= count;
    if (*complete) {
        memcpy(result, &g_crc, sizeof(g_crc));
        g_crc  = 0;
        g_word = 0;
    }
    return SWD_OK;
}

static void next_step(uint8_t step) {
    g_status.step     = step;
    g_status.progress = (uint8_t)(step * (100u / STEP_END));
    g_tries           = 0;
}

void mcu_flasher_task(void) {
    if (!g_enabled || g_status.state != MCU_FLASHER_STATE_RUNNING) {
        return;
    }
    if (g_abort) {
        finish(MCU_FLASHER_STATE_ABORTED, MCU_FLASHER_ERR_NONE, SWD_OK);
        return;
    }
    swd_err_t err      = SWD_OK;
    uint8_t   complete = 0;
    uint32_t  word[6];

    switch (g_status.step) {
    case STEP_BEGIN:
        if (swd_begin(g_clk, g_dio) != SWD_OK) {
            finish(MCU_FLASHER_STATE_FAILED, MCU_FLASHER_ERR_PINS_DRIVEN, SWD_ERR_PINS);
            return;
        }
        next_step(STEP_CONNECT);
        break;

    case STEP_CONNECT:
        err = swd_connect(&word[0]);
        if (err == SWD_OK) {
            g_status.dpidr = word[0];
        }
        if (err == SWD_OK && word[0] != PY32_DPIDR) {
            finish(MCU_FLASHER_STATE_FAILED, MCU_FLASHER_ERR_WRONG_TARGET, SWD_OK);
            return;
        }
        if (err == SWD_OK) {
            if (g_status.job == MCU_FLASHER_JOB_SURVEY_DP) {
                finish(MCU_FLASHER_STATE_DONE, MCU_FLASHER_ERR_NONE, SWD_OK);
                return;
            }
            next_step(STEP_POWER);
        }
        break;

    case STEP_POWER:
        err = swd_power_up();
        if (err == SWD_OK) {
            next_step(STEP_IDENTITY);
        }
        break;

    case STEP_IDENTITY:
        err = identity();
        if (err == SWD_OK) {
            if (g_status.ap_idr != PY32_AP_IDR || g_status.cpuid != PY32_CPUID ||
                g_status.idcode != PY32_IDCODE) {
                finish(MCU_FLASHER_STATE_FAILED, MCU_FLASHER_ERR_WRONG_TARGET, SWD_OK);
                return;
            }
            g_crc  = 0;
            g_word = 0;
            next_step(STEP_ROM);
        }
        break;

    case STEP_ROM:
    case STEP_SECTOR0: {
        uint8_t rom = g_status.step == STEP_ROM;

        err = crc_slice(rom ? ADDR_ROM : ADDR_FLASH, rom ? ROM_WORDS : SECTOR0_WORDS,
                        rom ? STATUS_AT(rom_crc) : STATUS_AT(sector0_crc), &complete);
        if (err == SWD_OK && complete) {
            /* Flash reads only at RDP level 0: at level 1 they are bus errors. */
            next_step(!rom ? STEP_HEADERS : g_status.rdp == 0xAA ? STEP_SECTOR0 : STEP_END);
        }
        break;
    }

    case STEP_HEADERS:
        /* boot_id + the next word, then the app header: in this order in the status */
        err = swd_mem_read_block(ADDR_BOOT_ID, &word[0], 2);
        if (err == SWD_OK) {
            err = swd_mem_read_block(ADDR_APP_HEADER, &word[2], 4);
        }
        if (err == SWD_OK) {
            memcpy(STATUS_AT(boot_id), word, 6 * sizeof(word[0]));
            next_step(STEP_END);
        }
        break;

    default:
        finish(MCU_FLASHER_STATE_DONE, MCU_FLASHER_ERR_NONE, SWD_OK);
        return;
    }

    if (err != SWD_OK) {
        /* A failed transfer: line reset and the step again, a few times. */
        if (++g_tries >= TRIES_PER_STEP) {
            finish(MCU_FLASHER_STATE_FAILED,
                   err == SWD_ERR_NO_TARGET ? MCU_FLASHER_ERR_NO_TARGET : MCU_FLASHER_ERR_SWD,
                   err);
            return;
        }
        uint32_t dpidr;
        if (swd_connect(&dpidr) == SWD_OK && g_status.step > STEP_POWER) {
            swd_power_up();
        }
        return;
    }
    if (hal_millis() - g_last_report >= REPORT_EVERY_MS) {
        report();
    }
}

#endif /* SECONDARY_MCU_IMAGE */
