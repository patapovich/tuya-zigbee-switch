/* Only in builds that carry an MCU image (src/telink/Makefile; the stub is
 * also built without, for tests/test_open_dimmer_symbols.py). */
#ifdef SECONDARY_MCU_IMAGE

#include "mcu_flasher.h"

#include <stddef.h>
#include <string.h>

#include "base_components/swd.h"
#include "device_config/config_parser.h"
#include "device_config/nvm_items.h"
#include "hal/gpio.h"
#include "hal/nvm.h"
#include "hal/swd_io.h"
#include "hal/uart.h"
#include "hal/zigbee.h"
#include "hal/timer.h"
#include "zigbee/consts.h"
#include "zigbee/secondary_mcu_boot.h"
#include "zigbee/tuya_mcu_app.h"
#include "zigbee/tuya_mcu_diag.h"
#include "zigbee/tuya_mcu_ota.h"

/* The ZDMS16-2's PY32F002A as read with an external probe (the identity gate:
 * the plan's section 0 item 6). */
#define PY32_DPIDR               0x0BC11477u
#define PY32_AP_IDR              0x04770031u
#define PY32_CPUID               0x410CC601u
#define PY32_IDCODE              0x60001000u
#define PY32_ROM_CRC             0xA52232EAu

#define ADDR_CPUID               0xE000ED00u
#define ADDR_DBGMCU_IDCODE       0x40015800u
#define ADDR_FLASH_OPTR          0x40022020u
#define ADDR_FLASH_WRPR          0x4002202Cu
#define ADDR_OPTION_AREA         0x1FFF0E80u
#define ADDR_ROM                 0x1FFF0000u
#define ROM_WORDS                (0xE00u / 4u)
#define ADDR_FLASH               0x08000000u
#define SECTOR0_WORDS            (0x1000u / 4u)
#define ADDR_BOOT_ID             0x08000010u
#define ADDR_APP_HEADER          0x080010C0u

/* Core debug (ARMv6-M) */
#define ADDR_DHCSR               0xE000EDF0u
#define ADDR_DCRSR               0xE000EDF4u
#define ADDR_DCRDR               0xE000EDF8u
#define ADDR_DEMCR               0xE000EDFCu
#define ADDR_AIRCR               0xE000ED0Cu
#define DHCSR_KEY                0xA05F0000u
#define DHCSR_C_DEBUGEN          0x00000001u
#define DHCSR_C_HALT             0x00000002u
#define DHCSR_S_REGRDY           0x00010000u
#define DHCSR_S_HALT             0x00020000u
#define AIRCR_SYSRESETREQ        0x05FA0004u
#define DEMCR_VC_CORERESET       0x00000001u
#define DCRSR_WRITE              0x00010000u
#define REG_PC                   15u
#define REG_XPSR                 16u
#define REG_MSP                  17u
#define XPSR_T                   0x01000000u

/* PY32F002A peripherals (py32f002ax5.h) */
#define ADDR_RCC_ICSCR           0x40021004u
#define ADDR_RCC_IOPENR          0x40021034u
#define ADDR_RCC_APBENR1         0x4002103Cu
#define RCC_APBENR1_DBGEN        0x08000000u
#define ADDR_DBGMCU_APB_FZ1      0x40015808u
#define DBGMCU_IWDG_STOP         0x00001000u
#define ADDR_GPIOA_MODER         0x50000000u
#define ADDR_GPIOA_BRR           0x50000028u
#define ADDR_FLASH_KEYR          0x40022008u
#define ADDR_FLASH_OPTKEYR       0x4002200Cu
#define ADDR_FLASH_SDKR          0x40022024u
#define ADDR_FLASH_OB_TRIGGER    0x40022080u
#define FLASH_OPTKEY1            0x08192A3Bu
#define FLASH_OPTKEY2            0x4C5D6E7Fu
#define FLASH_CR_OPTSTRT         0x00020000u
#define FLASH_CR_EOPIE           0x01000000u
#define FLASH_CR_OBL_LAUNCH      0x08000000u
#define FLASH_CR_OPTLOCK         0x40000000u

/* Open Dimmer's option bytes, the only ones this ever writes: RDP level 0,
 * user byte 0xF9 (brown-out reset at 2.5 V, NRST a GPIO, software watchdogs),
 * no SDK area, no write protection. Never 0xCC (level 2: permanent) and never
 * user byte 0 (hardware watchdogs): both checked at compile time. */
#define OB_OPTR                  0x0000F9AAu
#define OB_SDKR                  0x000000FFu
#define OB_WRPR                  0x0000FFFFu
typedef char   ob_optr_is_level_0[((OB_OPTR & 0xFFu) == 0xAAu) ? 1 : -1];
typedef char   ob_user_byte_is_open_dimmers[(((OB_OPTR >> 8) & 0xFFu) == 0xF9u) ? 1 : -1];
#define OB_PROGRAM_MS            2000u
#define REATTACH_MS              2000u
#define EARLY_TRIES              60u  /* mcu_flasher_early: attach tries ... */
#define EARLY_GAP_MS             20u  /* ... this far apart: ~1.2 s in all */
#define ADDR_FLASH_SR            0x40022010u
#define ADDR_FLASH_CR            0x40022014u
#define ADDR_FLASH_TS0           0x40022100u
#define FLASH_KEY1               0x45670123u
#define FLASH_KEY2               0xCDEF89ABu
#define FLASH_CR_PG              0x00000001u
#define FLASH_CR_PER             0x00000002u
#define FLASH_CR_PGSTRT          0x00080000u
#define FLASH_CR_LOCK            0x80000000u
#define FLASH_SR_EOP             0x00000001u
#define FLASH_SR_WRPERR          0x00000010u
#define FLASH_SR_OPTVERR         0x00008000u
#define FLASH_SR_BSY             0x00010000u
#define FLASH_PAGE_WORDS         32u
#define FLASH_PAGES_SECTOR0      32u
#define FLASH_BUSY_POLLS         200u     /* SR reads: ~25 ms at 400 kHz, ~7 page erases */
#define HALT_POLLS               50u

/* The mailbox's boot word and its complement (open-dimmer docs/BOOTLOADER.md
 * section 3): written 0 before every reset this issues, as tools/swd/install.sh
 * does, so the bootloader starts with fresh tries. */
#define ADDR_BOOT_WORD           0x20000BF8u
#define ADDR_BOOT_WORD_INV       0x20000BFCu

#define BOOT_ID_MAGIC            0x4C42444Fu /* "ODBL" */
#define APP_MAGIC                0x5A445041u /* "APDZ" */

/* One frame per read response or report: the Telink gcc has no _Static_assert. */
typedef char mcu_flasher_status_fits_one_frame[
        (sizeof(mcu_flasher_status_t) - 1 <= MCU_FLASHER_STATUS_MAX) ? 1 : -1];

#define SLICE_WORDS        32u    /* per main-loop pass: a few ms of SWD */
#define TRIES_PER_STEP     3u
#define REPORT_EVERY_MS    1000u

/* An armed install waits for the user's power cycle: its status goes to the
 * Basic bindings once the module is on the network, then this often (the
 * converter shows it in mcu_firmware). */
#define ARMED_REPORT_MS    600000u

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
    /* install: halt at once, then (after the identity and ROM) */
    STEP_STOP,
    STEP_OB,
    STEP_OB_WAIT,
    STEP_OB_CHECK,
    STEP_RELOAD,
    STEP_REATTACH,
};

/* An install armed in NV (NV_ITEM_MCU_FLASHER_JOB): attach at the next
 * power-ups, before (PHASE_ARMED) or after (PHASE_OB_WRITTEN) the option
 * bytes were written. PHASE_RETAKEN: taken up after them but found at level 1
 * (mcu_flasher_early): counted as PHASE_ARMED, every power-up though, and with
 * the gates relaxed for what a cut may have left. */
#define ARMED_MAGIC         0xA7u
#define PHASE_ARMED         1u
#define PHASE_OB_WRITTEN    2u
#define PHASE_RETAKEN       3u
#define ARMED_BOOTS         3u

/* PHASE_OB_WRITTEN: the factory firmware may be gone (the protection change
 * erased the flash): the install goes on at every power-up until it is done. */
#define RESUME_ALWAYS       0xFFu

/* g_resumed of an install taken up at level 0 with Open Dimmer's option bytes
 * (at a power-up, the chip not identified in this run): the identity, the ROM
 * and sector 0 are read before sector 0 is written. */
#define RESUMED_AT_0        2u

/* Start-ups that found the pins held at once (a restart of the module alone, the
 * MCU running on) do not count as power-ups; this many of them count as one, so
 * a pin held low at every real power-up (a short, a firmware that takes it at
 * once) still ends the job. */
#define HELD_STARTS         20u
typedef struct {
    uint8_t magic;
    uint8_t phase;
    uint8_t boots_left;
    uint8_t held;          /* start-ups with the pins held at once (HELD_STARTS) */
} armed_t;

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
static uint8_t        g_was_joined; /* ARMED: on the network at the last pass */
static uint8_t        g_page;       /* STEP_PROGRAM: next page, 1..31 then 0 */
static uint8_t        g_rewrites;   /* STEP_OB_CHECK / STEP_VERIFY: written again */
static uint8_t        g_resumed;    /* an install taken up after its option bytes (or RESUMED_AT_0) */
static uint8_t        g_keep;       /* this install stays armed when it stops: see finish() */
static uint32_t       g_confirm_at;
static uint32_t       g_step_at;    /* when the current wait step began */
static uint16_t       g_rx_mark;    /* the MCU's UART frames so far when a job was armed */

/* Confirm attribute (Basic 0xff52, char string): a writing job's own name.
 * The SDK copies a written string without checking its length against the
 * attribute (zcl_setAttrVal: len + 1 bytes): the buffer takes the longest one a
 * char string can be (255), whatever a network node sends. */
typedef struct __attribute__((packed)) {
    uint8_t len;
    char    text[255];
} confirm_str_t;
static confirm_str_t g_confirm;
typedef char mcu_flasher_confirm_takes_any_char_string[(sizeof(g_confirm) == 256) ? 1 : -1];

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
    a[2].size         = 0xFFu;   /* the field's most (uint8_t); the buffer has 256 */
    a[2].value        = (uint8_t *)&g_confirm;
    return MCU_FLASHER_ATTRS;
}

static void report(void) {
    g_last_report = hal_millis();
    hal_zigbee_send_report_attr(endpoints[0].endpoint, ZCL_CLUSTER_BASIC,
                                ZCL_ATTR_BASIC_MCU_FLASHER_STATUS, ZCL_DATA_TYPE_OCTET_STR,
                                &g_status, (uint8_t)sizeof(g_status));
}

static void arm(uint8_t phase, uint8_t boots_left);

static void finish(uint8_t state, uint8_t error, swd_err_t swd_err) {
    if (g_status.job == MCU_FLASHER_JOB_INSTALL && g_keep && state != MCU_FLASHER_STATE_DONE) {
        /* g_keep: RESUME_ALWAYS once the option bytes were started (or the
         * job was taken up after them): the factory firmware may be erased,
         * so a failure keeps the job, it goes on at the next power-up; an
         * abort only stops the run (armed, it can be dropped:
         * mcu_flasher_on_write). One taken up at level 1 (PHASE_RETAKEN) is
         * kept the same way while it has power-ups left (g_keep: how many). */
        g_status.error   = state == MCU_FLASHER_STATE_FAILED ? error : MCU_FLASHER_ERR_KEPT;
        g_status.swd_err = (uint8_t)swd_err;
        arm(g_keep == RESUME_ALWAYS ? PHASE_OB_WRITTEN : PHASE_RETAKEN, g_keep);
        return;
    }
    if (g_status.step > STEP_POWER) {
        swd_power_down();   /* best effort: the link may be gone */
    }
    swd_end();
    g_status.state       = state;
    g_status.error       = error;
    g_status.swd_err     = (uint8_t)swd_err;
    g_status.duration_ms = sat16(hal_millis() - g_started);
    report();
    if (g_status.job == MCU_FLASHER_JOB_INSTALL) {
        hal_nvm_delete(NV_ITEM_MCU_FLASHER_JOB);   /* ended either way */
    }
    if (g_status.step == STEP_RUN && state == MCU_FLASHER_STATE_DONE) {
        tuya_mcu_app_mcu_restarted(); /* its next query is a first contact */
    }
}

static uint8_t is_update(void) {
    return g_status.job == MCU_FLASHER_JOB_UPDATE_BOOT ||
           g_status.job == MCU_FLASHER_JOB_UPDATE_FORCE;
}

static uint8_t is_install(void) {
    return g_status.job == MCU_FLASHER_JOB_INSTALL;
}

/* Wait for the next power-up: the pins are released, the job is kept in NV. */
static void arm(uint8_t phase, uint8_t boots_left) {
    armed_t a = { ARMED_MAGIC, phase, boots_left, 0 };

    hal_nvm_write(NV_ITEM_MCU_FLASHER_JOB, sizeof(a), (uint8_t *)&a);
    if (g_status.step > STEP_POWER) {
        swd_power_down();   /* best effort: the link may be gone */
    }
    swd_end();
    g_rx_mark            = tuya_mcu_diag.rx_frames;
    g_status.state       = MCU_FLASHER_STATE_ARMED;
    g_status.page        = boots_left;     /* shown as the power-ups left */
    g_status.duration_ms = sat16(hal_millis() - g_started);
    report();
}

/* The confirm text names this job and was written in the last minute; used
 * up either way. */
static uint8_t confirmed(uint8_t job) {
    const char *name = job == MCU_FLASHER_JOB_UPDATE_FORCE ? "update_bootloader_force" :
                       job == MCU_FLASHER_JOB_INSTALL ? "install" : "update_bootloader";
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
        if (g_confirm.len > 23u) {
            g_confirm.len = 0;   /* no job has such a name; read back it would not fit a frame */
        }
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
        if (g_status.state == MCU_FLASHER_STATE_ARMED) {
            /* A job kept over failures (g_keep) is dropped only when the MCU
             * has sent a frame on the UART since the job was armed: then it
             * runs and nothing is lost. What it sent before does not tell (the
             * factory firmware tells its version before an install erases it);
             * a cleared or wrapped count reads as silent. */
            if (g_keep && tuya_mcu_diag.rx_frames <= g_rx_mark) {
                g_status.error = MCU_FLASHER_ERR_KEPT;
            } else {
                hal_nvm_delete(NV_ITEM_MCU_FLASHER_JOB);
                g_status.state = MCU_FLASHER_STATE_ABORTED;
            }
            report();
        }
        return;
    }
    if (g_status.state == MCU_FLASHER_STATE_RUNNING) {
        return; /* one job at a time; the running one is not disturbed */
    }
    if (job < MCU_FLASHER_JOB_SURVEY_DP || job > MCU_FLASHER_JOB_INSTALL) {
        g_status.error = MCU_FLASHER_ERR_BAD_JOB;
        report();
        return;
    }
    if (job >= MCU_FLASHER_JOB_UPDATE_BOOT && !confirmed(job)) {
        g_status.error = MCU_FLASHER_ERR_NOT_CONFIRMED;
        report();
        return;
    }

    /* install: only an MCU that told a factory version (1.x) on the UART in
     * this run; a silent one is refused too */
    if (job == MCU_FLASHER_JOB_INSTALL && !tuya_mcu_app_mcu_is_stock()) {
        g_status.error = MCU_FLASHER_ERR_WRONG_CLASS;
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
    g_resumed       = 0;
    g_keep          = 0;
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

    /* the key sequence only when locked: a key write to an unlocked
     * controller locks it until the next reset */
    if (err == SWD_OK) err = swd_mem_read32(ADDR_FLASH_CR, &cr);
    if (err == SWD_OK && (cr & FLASH_CR_LOCK)) {
        err = swd_mem_write32(ADDR_FLASH_KEYR, FLASH_KEY1);
        if (err == SWD_OK) err = swd_mem_write32(ADDR_FLASH_KEYR, FLASH_KEY2);
        if (err == SWD_OK) err = swd_mem_read32(ADDR_FLASH_CR, &cr);
    }
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

/* ---- install (a factory MCU, read-protected) ------------------------------ */

/* Stop the core where it is (the factory firmware may be in its start-up
 * delay), then the gates low and the watchdog frozen. Under read protection
 * the core must not run on with a debugger attached. */
static swd_err_t halt_now(uint8_t *halted) {
    uint32_t  dhcsr = 0;
    swd_err_t err   = swd_mem_write32(ADDR_DHCSR, DHCSR_KEY | DHCSR_C_DEBUGEN | DHCSR_C_HALT);

    for (uint8_t i = 0; err == SWD_OK && !(dhcsr & DHCSR_S_HALT) && i < HALT_POLLS; i++) {
        err = swd_mem_read32(ADDR_DHCSR, &dhcsr);
    }
    *halted = (dhcsr & DHCSR_S_HALT) != 0;
    if (err == SWD_OK && *halted) {
        err = gates_low();
    }
    return err;
}

/* HAL_FLASH_OBProgram: both locks open, the three registers staged, OPTSTRT,
 * the trigger write. Only the constants OB_*: nothing read from the target goes
 * into them. */
static swd_err_t ob_program(uint8_t *started) {
    static const uint32_t staged[3][2] = {
        { ADDR_FLASH_WRPR, OB_WRPR }, { ADDR_FLASH_SDKR, OB_SDKR }, { ADDR_FLASH_OPTR, OB_OPTR },
    };
    uint32_t  cr = FLASH_CR_OPTLOCK;
    uint8_t   unlocked;
    swd_err_t err = flash_unlock(&unlocked);

    *started = 0;
    if (err != SWD_OK || !unlocked) {
        return err;
    }
    err = swd_mem_read32(ADDR_FLASH_CR, &cr);
    if (err == SWD_OK && (cr & FLASH_CR_OPTLOCK)) {
        err = swd_mem_write32(ADDR_FLASH_OPTKEYR, FLASH_OPTKEY1);
        if (err == SWD_OK) err = swd_mem_write32(ADDR_FLASH_OPTKEYR, FLASH_OPTKEY2);
        if (err == SWD_OK) err = swd_mem_read32(ADDR_FLASH_CR, &cr);
    }
    if (err != SWD_OK || (cr & FLASH_CR_OPTLOCK)) {
        return err;
    }
    for (uint8_t i = 0; err == SWD_OK && i < 3; i++) {
        err = swd_mem_write32(staged[i][0], staged[i][1]);
    }
    if (err == SWD_OK) err = swd_mem_write32(ADDR_FLASH_CR, FLASH_CR_OPTSTRT | FLASH_CR_EOPIE);
    if (err == SWD_OK) err = swd_mem_write32(ADDR_FLASH_OB_TRIGGER, 0xFFu);
    *started = err == SWD_OK;
    return err;
}

/* The stored option bytes as ob_program writes them (open-dimmer
 * tools/swd/ob_bor25.bin: aa f9 55 06 ff 00 00 ff ff ff ff ff ff ff 00 00). */
static const uint32_t k_option_area[4] = { 0x0655F9AAu, 0xFF0000FFu, 0xFFFFFFFFu, 0x0000FFFFu };

#define AUTO_MAGIC    0xA5u

/* The automatic install (user decision 2026-11-19: soldering the two wires is
 * the consent, no Zigbee command, no opt-out): the first start-up that sees the
 * wires arms an install, once in the module's life (NV_ITEM_MCU_FLASHER_AUTO);
 * from then on only the manual jobs. A module without the wires costs one pin
 * read per start-up. Whether the MCU is a factory one is told by its read
 * protection, read before the core is touched (mcu_flasher_early). */
static uint8_t auto_arm(armed_t *a) {
    uint8_t used = 0;

    if ((hal_nvm_read(NV_ITEM_MCU_FLASHER_AUTO, 1, &used) == HAL_NVM_SUCCESS &&
         used == AUTO_MAGIC) ||
        !hal_swd_wired(g_clk)) {
        return 0;
    }
    *a = (armed_t){ ARMED_MAGIC, PHASE_ARMED, ARMED_BOOTS, 0 };
    hal_nvm_write(NV_ITEM_MCU_FLASHER_JOB, sizeof(*a), (uint8_t *)a);  /* the job first */
    used = AUTO_MAGIC;
    hal_nvm_write(NV_ITEM_MCU_FLASHER_AUTO, 1, &used);
    return 1;
}

/* Read protection, twice alike (a garbled read must not decide). */
static swd_err_t read_optr(uint32_t *optr) {
    uint32_t  again = 0;
    swd_err_t err   = swd_mem_read32(ADDR_FLASH_OPTR, optr);

    if (err == SWD_OK) err = swd_mem_read32(ADDR_FLASH_OPTR, &again);
    if (err == SWD_OK && again != *optr) err = SWD_ERR_PROTOCOL;
    return err;
}

/* The armed install of this power-up: attach before the factory firmware
 * takes the SWD pins, halt; the rest runs from the main loop. An install past
 * its option bytes (PHASE_OB_WRITTEN) is taken up the same way: at read
 * protection level 0 with Open Dimmer's option bytes it goes on with the
 * bootloader (STEP_REATTACH), else (still or again a factory or corrupt state,
 * level 1) from its start. */
void mcu_flasher_early(void) {
    armed_t a;

    if (!g_enabled) {
        return;
    }
    if ((hal_nvm_read(NV_ITEM_MCU_FLASHER_JOB, sizeof(a), (uint8_t *)&a) != HAL_NVM_SUCCESS ||
         a.magic != ARMED_MAGIC) &&
        !auto_arm(&a)) {
        return;
    }
    uint8_t resumed = a.phase == PHASE_OB_WRITTEN;

    g_status.state = MCU_FLASHER_STATE_RUNNING;
    g_status.job   = MCU_FLASHER_JOB_INSTALL;
    g_status.step  = STEP_BEGIN;
    g_started      = hal_millis();
    g_resumed      = resumed || a.phase == PHASE_RETAKEN;
    g_keep         = resumed ? RESUME_ALWAYS : g_resumed ? a.boots_left : 0;
    g_rewrites     = 0;
    if (!resumed && a.boots_left == 0) {
        finish(MCU_FLASHER_STATE_FAILED, MCU_FLASHER_ERR_NO_ATTACH, SWD_OK);
        return;
    }
    uint32_t  dpidr  = 0;
    uint32_t  optr   = 0;
    uint8_t   halted = 0;
    uint8_t   held   = 0;            /* the pins were taken at the first try */
    uint8_t   stage  = STEP_BEGIN;   /* how far it got, shown when it fails */
    swd_err_t err    = SWD_ERR_NO_TARGET;

    /* Tries 20 ms apart for ~1.2 s: on the bench the read-protected MCU's
     * debug port did not answer in its first ~0.3 s after power-up (the factory
     * firmware keeps the pins for ~1.5 s, a bootloader runs before it); given up
     * at once when the pins are taken */
    for (uint8_t attempt = 0; attempt < EARLY_TRIES && !(err == SWD_OK && halted); attempt++) {
        if (attempt > 0) {
            swd_end();
            for (uint32_t us = 0; us < EARLY_GAP_MS * 1000u; us++) {
                hal_swd_delay();   /* 1 us on the module (no clock in the stub) */
            }
        }
        stage = STEP_BEGIN;
        err   = swd_begin(g_clk, g_dio);
        if (err != SWD_OK) {
            held = attempt == 0;
            break;                 /* the pins are taken: no point in more tries */
        }
        if (err == SWD_OK) {
            stage = STEP_CONNECT;
            err   = swd_connect(&dpidr);
        }
        if (err == SWD_OK && dpidr != PY32_DPIDR) {
            err = SWD_ERR_PROTOCOL;
        }
        if (err == SWD_OK) {
            stage = STEP_POWER;
            err   = swd_power_up();
        }
        if (err == SWD_OK) {
            /* read protection first, the core untouched: level 0 is not a
             * factory MCU (Open Dimmer runs there) and keeps running; for a
             * taken-up install it is the state after the protection change */
            stage = STEP_IDENTITY;
            err   = read_optr(&optr);
            if (err == SWD_OK && (uint8_t)optr == 0xAAu) {
                break;
            }
        }
        if (err == SWD_OK) {
            stage = STEP_STOP;
            err   = halt_now(&halted);
        }
    }
    g_status.dpidr = dpidr;
    if (err == SWD_OK && (uint8_t)optr == 0xAAu && resumed) {
        g_status.optr = optr;
        g_step_at     = hal_millis();
        g_resumed     = RESUMED_AT_0;
        next_step(STEP_REATTACH);   /* the link is up: the checks, then the bootloader */
        return;
    }
    if (err == SWD_OK && (uint8_t)optr == 0xAAu) {
        swd_power_down();
        swd_end();
        hal_nvm_delete(NV_ITEM_MCU_FLASHER_JOB);
        g_status.optr        = optr;
        g_status.state       = MCU_FLASHER_STATE_FAILED;
        g_status.error       = MCU_FLASHER_ERR_WRONG_CLASS;
        g_status.duration_ms = sat16(hal_millis());
        return;
    }
    if (err == SWD_OK && halted && g_resumed) {
        /* level 1 again: the protection change never took (or option bytes a
         * cut left corrupt load as level 1): the factory firmware may still be
         * there, so from here a limited install (PHASE_RETAKEN: its gates
         * relaxed for what a cut may leave, also at its next power-ups), not
         * one taken up at every power-up. Each later power-up that attaches
         * costs one of its count, so a cut or a failure before the option
         * bytes keeps it and still ends it. */
        a.phase      = PHASE_RETAKEN;
        a.boots_left = resumed ? ARMED_BOOTS : (uint8_t)(a.boots_left - 1u);
        a.held       = 0;
        hal_nvm_write(NV_ITEM_MCU_FLASHER_JOB, sizeof(a), (uint8_t *)&a);
        g_keep = a.boots_left;
    }
    if (err == SWD_OK && halted) {
        g_status.app_build = sat16(hal_millis());   /* attach time: shown for an install */
        next_step(STEP_IDENTITY);
        return;
    }

    /* Not this time: kept armed; the status tells how far it got, the transfer
     * error and when (ms since the module's start). A power-up is counted only
     * when the pins were free at the start: held at once, the MCU ran on through
     * a restart of the module alone (an OTA, a config write), it was not a
     * power-up. A taken-up install is never given up. */
    if (!resumed && held && ++a.held < HELD_STARTS) {
        hal_nvm_write(NV_ITEM_MCU_FLASHER_JOB, sizeof(a), (uint8_t *)&a);
    } else if (!resumed) {
        a.held = 0;
        a.boots_left--;
        hal_nvm_write(NV_ITEM_MCU_FLASHER_JOB, sizeof(a), (uint8_t *)&a);
    }
    swd_end();
    g_status.state = MCU_FLASHER_STATE_ARMED;
    g_status.page  = resumed ? RESUME_ALWAYS : a.boots_left;
    g_status.step  = stage;
    g_status.error = stage == STEP_BEGIN ? MCU_FLASHER_ERR_PINS_DRIVEN :
                     err == SWD_OK ? MCU_FLASHER_ERR_HALT :
                     err == SWD_ERR_NO_TARGET ? MCU_FLASHER_ERR_NO_TARGET : MCU_FLASHER_ERR_SWD;
    g_status.swd_err     = (uint8_t)err;
    g_status.duration_ms = sat16(hal_millis());
}

/* The armed state is set at start-up, before the network (mcu_flasher_early),
 * or by a job that ends armed: told when the module is on the network (again),
 * then every ARMED_REPORT_MS. */
static void armed_report(void) {
    uint8_t joined = hal_zigbee_get_network_status() == HAL_ZIGBEE_NETWORK_JOINED;

    if (joined && (!g_was_joined || hal_millis() - g_last_report >= ARMED_REPORT_MS)) {
        report();
    }
    g_was_joined = joined;
}

void mcu_flasher_task(void) {
    if (!g_enabled) {
        return;
    }
    if (g_status.state == MCU_FLASHER_STATE_ARMED) {
        armed_report();
        return;
    }
    g_was_joined = 0;
    if (g_status.state != MCU_FLASHER_STATE_RUNNING) {
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
            if (is_install()) {
                arm(PHASE_ARMED, ARMED_BOOTS);   /* the factory firmware holds the pins */
            } else {
                finish(MCU_FLASHER_STATE_FAILED, MCU_FLASHER_ERR_PINS_DRIVEN, SWD_ERR_PINS);
            }
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
        if (err == SWD_OK && is_install()) {
            /* read protection before the core is stopped: at level 0 it is not a
             * factory MCU, and it keeps running (a halted one would stay so
             * until a power cycle) */
            uint32_t optr = 0;

            err           = read_optr(&optr);
            g_status.optr = optr;   /* packed: assigned, never through a pointer */
            if (err == SWD_OK && (uint8_t)optr == 0xAAu) {
                fail = MCU_FLASHER_ERR_WRONG_CLASS;
                break;
            }
        }
        if (err == SWD_OK) {
            next_step(is_install() ? STEP_STOP : STEP_IDENTITY);
        }
        break;

    case STEP_STOP: {
        uint8_t halted;

        err = halt_now(&halted);
        if (err == SWD_OK && !halted) {
            fail = MCU_FLASHER_ERR_HALT;
            break;
        }
        if (err == SWD_OK) {
            next_step(STEP_IDENTITY);
        }
        break;
    }

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
            /* install: a factory MCU, read-protected (level 1) */
            if (is_install() && (uint8_t)g_status.optr == 0xAA && g_resumed) {
                /* a taken-up install at level 0: its option bytes again */
            } else if (is_install() &&
                       ((uint8_t)g_status.optr == 0xAA || (uint8_t)g_status.optr == 0xCC)) {
                fail = (uint8_t)g_status.optr ==
                       0xAA ? MCU_FLASHER_ERR_WRONG_CLASS : MCU_FLASHER_ERR_PROTECTED;
                break;
            }
            if (is_install() && !g_resumed && user != 0xF0 && user != 0xF9) {
                /* (a taken-up install writes its option bytes again, whatever
                 * a cut left there) */
                fail = MCU_FLASHER_ERR_PROTECTED;
                break;
            }
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
        if (err == SWD_OK && complete && rom && (is_update() || is_install()) &&
            g_status.rom_crc != PY32_ROM_CRC) {
            fail = MCU_FLASHER_ERR_WRONG_TARGET;
            break;
        }
        if (err == SWD_OK && complete && rom && is_install() && g_resumed != RESUMED_AT_0) {
            g_rewrites = 0;
            next_step(STEP_OB);
            break;
        }
        if (err == SWD_OK && complete && !rom && g_status.job >= MCU_FLASHER_JOB_UPDATE_BOOT) {
            /* the carried bootloader there already: nothing to write (an
             * install taken up at level 0 ends here, the core never touched) */
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

    case STEP_OB: {
        uint8_t started;
        armed_t a = { ARMED_MAGIC, PHASE_OB_WRITTEN, RESUME_ALWAYS, 0 };

        /* before the programming starts: a power cut from here on takes the
         * install up at the next power-up (the flash may be erased by then) */
        hal_nvm_write(NV_ITEM_MCU_FLASHER_JOB, sizeof(a), (uint8_t *)&a);
        err = ob_program(&started);
        if (started || err != SWD_OK) {
            g_keep = RESUME_ALWAYS;   /* started, or a transfer failed on the way: maybe */
        }

        /* not started (the controller stayed locked): nothing changed, a
         * failure drops the job as before the option bytes */
        if (err == SWD_OK && !started) {
            fail = MCU_FLASHER_ERR_FLASH;
            break;
        }
        if (err == SWD_OK) {
            g_step_at = hal_millis();
            next_step(STEP_OB_WAIT);
        }
        break;
    }

    case STEP_OB_WAIT: {
        uint32_t sr = 0;

        /* one SR read a pass: the reprogramming (and the mass erase that
         * the protection change brings) may take a while */
        err = swd_mem_read32(ADDR_FLASH_SR, &sr);
        if (err != SWD_OK) {
            break;
        }
        if (sr & FLASH_SR_BSY) {
            if (hal_millis() - g_step_at > OB_PROGRAM_MS) {
                fail = MCU_FLASHER_ERR_FLASH;
            }
            break;
        }
        err = swd_mem_write32(ADDR_FLASH_SR, FLASH_SR_EOP | FLASH_SR_WRPERR | FLASH_SR_OPTVERR);
        if (err == SWD_OK) {
            err = swd_mem_write32(ADDR_FLASH_CR, 0);
        }
        if (err == SWD_OK && (sr & FLASH_SR_WRPERR)) {
            fail = MCU_FLASHER_ERR_FLASH;
            break;
        }
        if (err == SWD_OK) {
            next_step(STEP_OB_CHECK);
        }
        break;
    }

    case STEP_OB_CHECK:
        /* the stored option bytes read back before the reload: a wrong one is
         * never loaded (written again, twice at most) */
        err = swd_mem_read_block(ADDR_OPTION_AREA, word, 4);
        if (err == SWD_OK) {
            if (memcmp(word, k_option_area, sizeof(k_option_area)) == 0) {
                next_step(STEP_RELOAD);
            } else if (g_rewrites++ < 2) {
                next_step(STEP_OB);
            } else {
                fail = MCU_FLASHER_ERR_OB_VERIFY;
            }
        }
        break;

    case STEP_RELOAD: {
        /* PHASE_OB_WRITTEN is in NV (STEP_OB): if the link does not come back,
         * the next power-up loads them and takes the install up */
        swd_mem_write32(ADDR_FLASH_CR, FLASH_CR_OBL_LAUNCH);   /* resets: the link drops */
        g_step_at = hal_millis();
        next_step(STEP_REATTACH);
        break;
    }

    case STEP_REATTACH:
        /* reconnect every pass for a while; then RDP 0 with Open Dimmer's
         * option bytes and an erased flash, or the job stops */
        if (swd_connect(&word[0]) == SWD_OK && swd_power_up() == SWD_OK &&
            swd_mem_read32(ADDR_FLASH_OPTR, &word[1]) == SWD_OK &&
            swd_mem_read32(ADDR_FLASH_WRPR, &word[2]) == SWD_OK &&
            swd_mem_read32(ADDR_FLASH, &word[3]) == SWD_OK) {
            g_status.optr = word[1];
            g_status.wrpr = (uint16_t)word[2];
            uint8_t ours = (word[1] & 0xFFFFu) == (OB_OPTR & 0xFFFFu) &&
                           (word[2] & 0xFFFFu) == OB_WRPR;
            if (ours && (g_resumed || word[3] == 0xFFFFFFFFu)) {
                /* right after the reload the flash must be blank (the mass
                 * erase): the bootloader as update_bootloader writes it. Taken
                 * up at a power-up (no ROM CRC of this run yet), a part of it
                 * or all may be there, behind these wires or others: the
                 * identity, the ROM and sector 0 are read first (STEP_SECTOR0
                 * goes on), the core untouched. */
                g_rewrites = 0;
                next_step(g_resumed == RESUMED_AT_0 ? STEP_IDENTITY : STEP_HALT);
            } else if (g_resumed && (uint8_t)word[1] != 0xCCu) {
                /* not the state after the change: the install from its start
                 * (level 0 with other option bytes too: written again, no erase) */
                g_resumed = 1;
                next_step(STEP_STOP);
            } else {
                fail = MCU_FLASHER_ERR_RELOAD;
                break;
            }
        } else if (hal_millis() - g_step_at > REATTACH_MS) {
            arm(PHASE_OB_WRITTEN, RESUME_ALWAYS); /* power-cycle the module */
            return;
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
            if (is_install() && g_status.step <= STEP_POWER) {
                arm(PHASE_ARMED, ARMED_BOOTS);   /* no attach now: at the next power-up */
                return;
            }
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
