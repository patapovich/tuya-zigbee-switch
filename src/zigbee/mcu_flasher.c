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
#include "zigbee/secondary_mcu_boot.h"
#include "zigbee/tuya_mcu_app.h"
#include "zigbee/tuya_mcu_ota.h"

/* The ZDMS16-2's PY32F002A as read with an external probe (the identity gate:
 * the plan's section 0 item 6). */
#define PY32_DPIDR             0x0BC11477u
#define PY32_AP_IDR            0x04770031u
#define PY32_CPUID             0x410CC601u
#define PY32_IDCODE            0x60001000u
#define PY32_ROM_CRC           0xA52232EAu

#define ADDR_CPUID             0xE000ED00u
#define ADDR_DBGMCU_IDCODE     0x40015800u
#define ADDR_FLASH_OPTR        0x40022020u
#define ADDR_FLASH_WRPR        0x4002202Cu
#define ADDR_OPTION_AREA       0x1FFF0E80u
#define ADDR_ROM               0x1FFF0000u
#define ROM_WORDS              (0xE00u / 4u)
#define ADDR_FLASH             0x08000000u
#define SECTOR0_WORDS          (0x1000u / 4u)
#define ADDR_BOOT_ID           0x08000010u
#define ADDR_APP_HEADER        0x080010C0u

/* Core debug (ARMv6-M) */
#define ADDR_DHCSR             0xE000EDF0u
#define ADDR_DCRSR             0xE000EDF4u
#define ADDR_DCRDR             0xE000EDF8u
#define ADDR_DEMCR             0xE000EDFCu
#define ADDR_AIRCR             0xE000ED0Cu
#define DHCSR_KEY              0xA05F0000u
#define DHCSR_C_DEBUGEN        0x00000001u
#define DHCSR_S_REGRDY         0x00010000u
#define DHCSR_S_HALT           0x00020000u
#define AIRCR_SYSRESETREQ      0x05FA0004u
#define DEMCR_VC_CORERESET     0x00000001u
#define DCRSR_WRITE            0x00010000u
#define REG_PC                 15u
#define REG_XPSR               16u
#define REG_MSP                17u
#define XPSR_T                 0x01000000u

/* PY32F002A peripherals (py32f002ax5.h) */
#define ADDR_RCC_ICSCR         0x40021004u
#define ADDR_RCC_IOPENR        0x40021034u
#define ADDR_RCC_APBENR1       0x4002103Cu
#define RCC_APBENR1_DBGEN      0x08000000u
#define ADDR_DBGMCU_APB_FZ1    0x40015808u
#define DBGMCU_IWDG_STOP       0x00001000u
#define ADDR_GPIOA_MODER       0x50000000u
#define ADDR_GPIOA_BRR         0x50000028u
#define ADDR_FLASH_KEYR        0x40022008u
#define ADDR_FLASH_SR          0x40022010u
#define ADDR_FLASH_CR          0x40022014u
#define ADDR_FLASH_TS0         0x40022100u
#define FLASH_KEY1             0x45670123u
#define FLASH_KEY2             0xCDEF89ABu
#define FLASH_CR_PG            0x00000001u
#define FLASH_CR_PER           0x00000002u
#define FLASH_CR_PGSTRT        0x00080000u
#define FLASH_CR_LOCK          0x80000000u
#define FLASH_SR_EOP           0x00000001u
#define FLASH_SR_WRPERR        0x00000010u
#define FLASH_SR_OPTVERR       0x00008000u
#define FLASH_SR_BSY           0x00010000u
#define FLASH_PAGE_WORDS       32u
#define FLASH_PAGES_SECTOR0    32u
#define FLASH_BUSY_POLLS       200u       /* SR reads: ~25 ms at 400 kHz, ~7 page erases */
#define HALT_POLLS             50u

/* The mailbox's boot word and its complement (zdms16-py32 docs/BOOTLOADER.md
 * section 3): written 0 before every reset this issues, as tools/swd/install.sh
 * does, so the bootloader starts with fresh tries. */
#define ADDR_BOOT_WORD         0x20000BF8u
#define ADDR_BOOT_WORD_INV     0x20000BFCu

#define BOOT_ID_MAGIC          0x4C42444Fu /* "ODBL" */
#define APP_MAGIC              0x5A445041u /* "APDZ" */

/* One frame per read response or report: the Telink gcc has no _Static_assert. */
typedef char mcu_flasher_status_fits_one_frame[
        (sizeof(mcu_flasher_status_t) - 1 <= MCU_FLASHER_STATUS_MAX) ? 1 : -1];

#define SLICE_WORDS        32u    /* per main-loop pass: a few ms of SWD */
#define TRIES_PER_STEP     3u
#define REPORT_EVERY_MS    1000u

/* Steps; survey_dp stops after STEP_CONNECT. update_bootloader: as
 * survey_full up to STEP_SECTOR0, then (unless sector 0 already holds the
 * carried bootloader and the job is not _force) STEP_HALT .. STEP_RUN. */
enum {
    STEP_BEGIN = 0,
    STEP_CONNECT,
    STEP_POWER,
    STEP_IDENTITY,
    STEP_ROM,
    STEP_SECTOR0,
    STEP_HEADERS,
    STEP_END,
    STEP_HALT,
    STEP_UNLOCK,
    STEP_ERASE0,
    STEP_PROGRAM,
    STEP_VERIFY,
    STEP_RUN,
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
static uint8_t        g_page;      /* STEP_PROGRAM: next page, 1..31 then 0 */
static uint8_t        g_rewrites;  /* STEP_VERIFY: sector 0 written again */
static uint32_t       g_confirm_at;

/* Confirm attribute (Basic 0xff52, char string): a writing job's own name. */
static struct __attribute__((packed)) {
    uint8_t len;
    char    text[23];
} g_confirm;

#define CONFIRM_MS    60000u

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
    g_status.format = 2;
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
    a[2].attribute_id = ZCL_ATTR_BASIC_MCU_FLASHER_CONFIRM;
    a[2].data_type_id = ZCL_DATA_TYPE_CHAR_STR;
    a[2].flag         = ATTR_WRITABLE;
    a[2].size         = (uint8_t)sizeof(g_confirm);
    a[2].value        = (uint8_t *)&g_confirm;
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
    report();
    if (g_status.step == STEP_RUN && state == MCU_FLASHER_STATE_DONE) {
        tuya_mcu_app_mcu_restarted(); /* its next query is a first contact */
    }
}

static uint8_t is_update(void) {
    return g_status.job == MCU_FLASHER_JOB_UPDATE_BOOT ||
           g_status.job == MCU_FLASHER_JOB_UPDATE_FORCE;
}

/* The confirm text names this job and was written in the last minute; used
 * up either way. */
static uint8_t confirmed(uint8_t job) {
    const char *name = job == MCU_FLASHER_JOB_UPDATE_FORCE ? "update_bootloader_force" :
                       "update_bootloader";
    uint8_t n  = (uint8_t)strlen(name);
    uint8_t ok = g_confirm.len == n && memcmp(g_confirm.text, name, n) == 0 &&
                 hal_millis() - g_confirm_at <= CONFIRM_MS;

    g_confirm.len = 0;
    return ok;
}

void mcu_flasher_on_write(uint16_t attribute_id) {
    if (!g_enabled) {
        return;
    }
    if (attribute_id == ZCL_ATTR_BASIC_MCU_FLASHER_CONFIRM) {
        g_confirm_at = hal_millis();
        return;
    }
    if (attribute_id != ZCL_ATTR_BASIC_MCU_FLASHER_CONTROL) {
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
    if (job < MCU_FLASHER_JOB_SURVEY_DP || job > MCU_FLASHER_JOB_UPDATE_FORCE) {
        g_status.error = MCU_FLASHER_ERR_BAD_JOB;
        report();
        return;
    }
    if (job >= MCU_FLASHER_JOB_UPDATE_BOOT && !confirmed(job)) {
        g_status.error = MCU_FLASHER_ERR_NOT_CONFIRMED;
        report();
        return;
    }
    uint8_t len = g_status.len, format = g_status.format;

    memset(&g_status, 0, sizeof(g_status));
    g_status.len    = len;
    g_status.format = format;
    g_status.state  = MCU_FLASHER_STATE_RUNNING;
    g_status.job    = job;
    g_status.step   = STEP_BEGIN;
    g_started       = hal_millis();
    g_abort         = 0;
    g_tries         = 0;
    g_rewrites      = 0;
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

/* AP IDR, CPUID, IDCODE, OPTR (in this order in the status), WRPR, the option
 * bytes' CRC. */
static swd_err_t identity(void) {
    static const uint32_t addr[] = {
        ADDR_CPUID, ADDR_DBGMCU_IDCODE, ADDR_FLASH_OPTR, ADDR_FLASH_WRPR,
    };
    uint32_t  v[6];
    swd_err_t err = swd_ap_read(SWD_AP_IDR, &v[0]);

    for (uint8_t i = 0; err == SWD_OK && i < sizeof(addr) / sizeof(addr[0]); i++) {
        err = swd_mem_read32(addr[i], &v[1 + i]);
    }
    if (err == SWD_OK) {
        err = read_crc16(ADDR_OPTION_AREA, &v[5]);
    }
    if (err == SWD_OK) {
        memcpy(STATUS_AT(ap_idr), v, 4 * sizeof(v[0]));
        g_status.wrpr = (uint16_t)v[4];
        memcpy(STATUS_AT(option_crc), &v[5], sizeof(v[5]));
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
    g_status.step = step;
    g_tries       = 0;
}

/* ---- writing the bootloader (update_bootloader) ------------------------- */

static swd_err_t set_bits(uint32_t addr, uint32_t bits) {
    uint32_t  v;
    swd_err_t err = swd_mem_read32(addr, &v);

    return err == SWD_OK ? swd_mem_write32(addr, v | bits) : err;
}

/* The core's own reset with the debugger's hand on it: debug on, catch at the
 * reset vector, the watchdog frozen while halted, the mailbox's boot word 0,
 * SYSRESETREQ; then wait for the halt. All peripherals are reset by it, so
 * the gate outputs float until gates_low(). */
static swd_err_t reset_halt(uint8_t *halted) {
    uint32_t  dhcsr = 0;
    swd_err_t err   = swd_mem_write32(ADDR_DHCSR, DHCSR_KEY | DHCSR_C_DEBUGEN);

    if (err == SWD_OK) err = set_bits(ADDR_DEMCR, DEMCR_VC_CORERESET);
    if (err == SWD_OK) err = set_bits(ADDR_RCC_APBENR1, RCC_APBENR1_DBGEN);
    if (err == SWD_OK) err = set_bits(ADDR_DBGMCU_APB_FZ1, DBGMCU_IWDG_STOP);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_BOOT_WORD, 0);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_BOOT_WORD_INV, 0);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_AIRCR, AIRCR_SYSRESETREQ);
    *halted = 0;
    for (uint8_t i = 0; err == SWD_OK && i < HALT_POLLS && !*halted; i++) {
        err     = swd_mem_read32(ADDR_DHCSR, &dhcsr);
        *halted = (dhcsr & DHCSR_S_HALT) != 0;
    }
    return err;
}

/* PA0 / PA1 (the gate drives) output low, as the bootloader's first
 * instructions do (pins_safe): a halted or blank MCU on the mains never leaves
 * the output stage to its pull-downs. */
static swd_err_t gates_low(void) {
    uint32_t  moder;
    swd_err_t err = set_bits(ADDR_RCC_IOPENR, 1u);     /* GPIOAEN */

    if (err == SWD_OK) err = swd_mem_write32(ADDR_GPIOA_BRR, 3u);
    if (err == SWD_OK) err = swd_mem_read32(ADDR_GPIOA_MODER, &moder);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_GPIOA_MODER, (moder & ~0xFu) | 0x5u);
    /* the reset cleared DBGEN: the watchdog freeze needs it again */
    if (err == SWD_OK) err = set_bits(ADDR_RCC_APBENR1, RCC_APBENR1_DBGEN);
    if (err == SWD_OK) err = set_bits(ADDR_DBGMCU_APB_FZ1, DBGMCU_IWDG_STOP);
    return err;
}

/* Factory flash timing for the HSI setting in use (flash_drv.c
 * flash_set_timing), then the flags cleared and the controller unlocked. */
static swd_err_t flash_unlock(uint8_t *unlocked) {
    static const uint32_t table[5] = { 0x1FFF0F1Cu, 0x1FFF0F30u, 0x1FFF0F44u, 0x1FFF0F58u,
                                       0x1FFF0F6Cu };
    uint32_t  icscr, t[5], cr = FLASH_CR_LOCK;
    swd_err_t err = swd_mem_read32(ADDR_RCC_ICSCR, &icscr);
    uint32_t  fs  = (icscr >> 13) & 7u;

    if (err == SWD_OK) err = swd_mem_read_block(table[fs < 5u ? fs : 0u], t, 5);
    if (err == SWD_OK) {
        /* TS0, TS1, TS2P, TPS3, TS3, PERTPE, SMERTPE, PRGTPE, PRETPE */
        const uint32_t ts[9] = {
            t[0] & 0xFFu,           (t[0] >> 16) & 0x1FFu, t[1] & 0xFFu,
            (t[1] >> 16) & 0x7FFu,
            (t[0] >> 8) & 0xFFu,    t[2] & 0x1FFFFu,       t[3] & 0x1FFFFu,
            t[4] & 0xFFFFu,
            (t[4] >> 16) & 0x3FFFu,
        };
        err = swd_mem_write_block(ADDR_FLASH_TS0, ts, 9);
    }
    if (err == SWD_OK) err = swd_mem_write32(ADDR_FLASH_SR,
                                             FLASH_SR_EOP | FLASH_SR_WRPERR | FLASH_SR_OPTVERR);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_FLASH_KEYR, FLASH_KEY1);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_FLASH_KEYR, FLASH_KEY2);
    if (err == SWD_OK) err = swd_mem_read32(ADDR_FLASH_CR, &cr);
    *unlocked = (cr & FLASH_CR_LOCK) == 0;
    return err;
}

/* Wait for the controller; ok = finished without a write protection error. */
static swd_err_t flash_wait(uint8_t *ok) {
    uint32_t  sr  = FLASH_SR_BSY;
    swd_err_t err = SWD_OK;

    for (uint8_t i = 0; err == SWD_OK && (sr & FLASH_SR_BSY) && i < FLASH_BUSY_POLLS; i++) {
        err = swd_mem_read32(ADDR_FLASH_SR, &sr);
    }
    *ok = err == SWD_OK && !(sr & (FLASH_SR_BSY | FLASH_SR_WRPERR));
    if (err == SWD_OK) {
        err = swd_mem_write32(ADDR_FLASH_SR, FLASH_SR_EOP | FLASH_SR_WRPERR | FLASH_SR_OPTVERR);
    }
    return err;
}

static swd_err_t erase_page(uint32_t addr, uint8_t *ok) {
    swd_err_t err = swd_mem_write32(ADDR_FLASH_CR, FLASH_CR_PER);

    if (err == SWD_OK) err = swd_mem_write32(addr, 0xFFu);    /* starts the erase */
    if (err == SWD_OK) err = flash_wait(ok);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_FLASH_CR, 0);
    return err;
}

/* Page p of the carried bootloader, 0xFF past its end; 1 if it is all 0xFF. */
static uint8_t boot_page(uint8_t p, uint32_t *w) {
    uint16_t at    = (uint16_t)(p * FLASH_PAGE_WORDS * 4u);
    uint8_t  blank = 1;

    memset(w, 0xFF, FLASH_PAGE_WORDS * 4u);
    if (at < secondary_mcu_boot_size) {
        uint16_t n = (uint16_t)(secondary_mcu_boot_size - at);
        memcpy(w, secondary_mcu_boot + at, n < FLASH_PAGE_WORDS * 4u ? n : FLASH_PAGE_WORDS * 4u);
        blank = 0;
    }
    return blank;
}

/* Erase page p and program it (HAL FLASH_Program_Page, flash_drv.c): 31 words
 * into the page buffer, PGSTRT, the 32nd word. With the core halted the AP is
 * the only bus master: nothing else touches the flash in between. */
static swd_err_t write_page(uint8_t p, uint8_t *ok) {
    uint32_t  w[FLASH_PAGE_WORDS];
    uint32_t  addr  = ADDR_FLASH + p * FLASH_PAGE_WORDS * 4u;
    uint8_t   blank = boot_page(p, w);
    swd_err_t err   = erase_page(addr, ok);

    if (err != SWD_OK || !*ok || blank) {
        return err;
    }
    err = swd_mem_write32(ADDR_FLASH_CR, FLASH_CR_PG);
    if (err == SWD_OK) err = swd_mem_write_block(addr, w, FLASH_PAGE_WORDS - 1u);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_FLASH_CR, FLASH_CR_PG | FLASH_CR_PGSTRT);
    if (err == SWD_OK) err = swd_mem_write32(addr + 4u * (FLASH_PAGE_WORDS - 1u),
                                             w[FLASH_PAGE_WORDS - 1u]);
    if (err == SWD_OK) err = flash_wait(ok);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_FLASH_CR, 0);
    return err;
}

static swd_err_t write_core_reg(uint8_t reg, uint32_t value) {
    uint32_t  dhcsr = 0;
    swd_err_t err   = swd_mem_write32(ADDR_DCRDR, value);

    if (err == SWD_OK) err = swd_mem_write32(ADDR_DCRSR, DCRSR_WRITE | reg);
    for (uint8_t i = 0; err == SWD_OK && !(dhcsr & DHCSR_S_REGRDY) && i < HALT_POLLS; i++) {
        err = swd_mem_read32(ADDR_DHCSR, &dhcsr);
    }
    return err;
}

/* Start the new firmware: reset-halt again, then MSP / PC / xPSR from its
 * vectors written by hand (the reported wrong-MSP-at-reset erratum of the
 * PY32), the reset catch off, the core run with debug off. */
static swd_err_t run_new(uint8_t *halted) {
    uint32_t  vec[2];
    swd_err_t err = reset_halt(halted);

    if (err != SWD_OK || !*halted) {
        return err;
    }
    err = gates_low();
    if (err == SWD_OK) err = swd_mem_read_block(ADDR_FLASH, vec, 2);
    if (err == SWD_OK) err = write_core_reg(REG_MSP, vec[0]);
    if (err == SWD_OK) err = write_core_reg(REG_PC, vec[1] & ~1u);
    if (err == SWD_OK) err = write_core_reg(REG_XPSR, XPSR_T);
    if (err == SWD_OK) err = swd_mem_read32(ADDR_DEMCR, &vec[0]);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_DEMCR, vec[0] & ~DEMCR_VC_CORERESET);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_DHCSR, DHCSR_KEY);
    return err;
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
    uint8_t   fail     = MCU_FLASHER_ERR_NONE; /* a refusal or a failed check */
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
            fail = MCU_FLASHER_ERR_WRONG_TARGET;
            break;
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
                fail = MCU_FLASHER_ERR_WRONG_TARGET;
                break;
            }

            /* A writing job: RDP level 0, user byte 0xF0 (factory) or 0xF9 (Open
             * Dimmer), sector 0 not write-protected. */
            uint8_t user = (uint8_t)(g_status.optr >> 8);
            if (is_update() && ((uint8_t)g_status.optr != 0xAA || (user != 0xF0 && user != 0xF9) ||
                                !(g_status.wrpr & 1u))) {
                fail = MCU_FLASHER_ERR_PROTECTED;
                break;
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
        if (err == SWD_OK && complete && rom && is_update() && g_status.rom_crc != PY32_ROM_CRC) {
            fail = MCU_FLASHER_ERR_WRONG_TARGET;
            break;
        }
        if (err == SWD_OK && complete && !rom && is_update()) {
            /* the carried bootloader there already: nothing to write */
            if (g_status.sector0_crc == secondary_mcu_boot_crc &&
                g_status.job != MCU_FLASHER_JOB_UPDATE_FORCE) {
                g_status.boot_version = secondary_mcu_boot_version;
                finish(MCU_FLASHER_STATE_DONE, MCU_FLASHER_ERR_NONE, SWD_OK);
                return;
            }
            next_step(STEP_HALT);
            break;
        }
        if (err == SWD_OK && complete) {
            /* Flash reads only at RDP level 0: at level 1 they are bus errors. */
            next_step(!rom ? STEP_HEADERS : (uint8_t)g_status.optr ==
                      0xAA ? STEP_SECTOR0 : STEP_END);
        }
        break;
    }

    case STEP_HALT: {
        uint8_t halted;

        err = reset_halt(&halted);
        if (err == SWD_OK && !halted) {
            fail = MCU_FLASHER_ERR_HALT;
            break;
        }
        if (err == SWD_OK) {
            err = gates_low();
        }
        if (err == SWD_OK) {
            next_step(STEP_UNLOCK);
        }
        break;
    }

    case STEP_UNLOCK: {
        uint8_t unlocked;

        err = flash_unlock(&unlocked);
        if (err == SWD_OK && !unlocked) {
            fail = MCU_FLASHER_ERR_FLASH;
            break;
        }
        if (err == SWD_OK) {
            next_step(STEP_ERASE0);
        }
        break;
    }

    case STEP_ERASE0: {
        uint8_t ok;

        /* The vectors go first: from here a reset ends in a lockup (the SWD
         * pins stay reachable), never in half a bootloader. */
        err = erase_page(ADDR_FLASH, &ok);
        if (err == SWD_OK && !ok) {
            fail = MCU_FLASHER_ERR_FLASH;
            break;
        }
        if (err == SWD_OK) {
            g_page = 1;
            next_step(STEP_PROGRAM);
        }
        break;
    }

    case STEP_PROGRAM: {
        uint8_t ok;

        /* one page per pass: 1..31, then page 0 (the vectors) last */
        g_status.page = g_page;
        err           = write_page(g_page, &ok);
        if (err == SWD_OK && !ok) {
            fail = MCU_FLASHER_ERR_FLASH;
            break;
        }
        if (err == SWD_OK) {
            if (g_page == 0) {
                err    = swd_mem_write32(ADDR_FLASH_CR, FLASH_CR_LOCK);
                g_crc  = 0;
                g_word = 0;
                if (err == SWD_OK) {
                    next_step(STEP_VERIFY);
                }
            } else {
                g_page  = (uint8_t)((g_page + 1u) % FLASH_PAGES_SECTOR0);
                g_tries = 0;
            }
        }
        break;
    }

    case STEP_VERIFY:
        err = crc_slice(ADDR_FLASH, SECTOR0_WORDS, STATUS_AT(sector0_crc), &complete);
        if (err == SWD_OK && complete) {
            if (g_status.sector0_crc == secondary_mcu_boot_crc) {
                g_status.boot_version = secondary_mcu_boot_version;
                next_step(STEP_RUN);
            } else if (g_rewrites++ == 0) {
                next_step(STEP_UNLOCK);   /* once more from the erase */
            } else {
                /* the core stays halted, the gates low */
                fail = MCU_FLASHER_ERR_VERIFY;
                break;
            }
        }
        break;

    case STEP_RUN: {
        uint8_t halted;

        err = run_new(&halted);
        if (err == SWD_OK && !halted) {
            fail = MCU_FLASHER_ERR_HALT;
            break;
        }
        if (err == SWD_OK) {
            finish(MCU_FLASHER_STATE_DONE, MCU_FLASHER_ERR_NONE, SWD_OK);
            return;
        }
        break;
    }

    case STEP_HEADERS:
        /* boot_id (magic, version...) and the app header (magic, size, crc,
         * version / flags / build) */
        err = swd_mem_read_block(ADDR_BOOT_ID, &word[0], 2);
        if (err == SWD_OK) {
            err = swd_mem_read_block(ADDR_APP_HEADER, &word[2], 4);
        }
        if (err == SWD_OK) {
            if (word[0] == BOOT_ID_MAGIC) {
                g_status.boot_version = (uint8_t)word[1];
            }
            if (word[2] == APP_MAGIC) {
                g_status.app_version = (uint8_t)word[5];
                g_status.app_build   = (uint16_t)(word[5] >> 16);
            }
            next_step(STEP_END);
        }
        break;

    default:
        finish(MCU_FLASHER_STATE_DONE, MCU_FLASHER_ERR_NONE, SWD_OK);
        return;
    }

    if (fail != MCU_FLASHER_ERR_NONE) {
        finish(MCU_FLASHER_STATE_FAILED, fail, SWD_OK);
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
