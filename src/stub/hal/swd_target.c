#include "stub/hal/swd_target.h"

#include "hal/timer.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* See swd_target.h for what is modelled. Bit numbers below are ADIv5 (DP,
 * AHB-AP), ARMv6-M (SCS) and the PY32F002A reference manual (DBGMCU, FLASH,
 * RCC, GPIO). */

#define LINE_RESET_ONES        50
#define JTAG_TO_SWD            0xE79Eu
#define JTAG_TO_SWD_BITS       16

#define ACK_OK                 0x1u
#define ACK_WAIT               0x2u
#define ACK_FAULT              0x4u

#define DPIDR_VALUE            0x0BC11477u

/* CTRL/STAT */
#define CS_STICKYORUN          (1u << 1)
#define CS_STICKYCMP           (1u << 4)
#define CS_STICKYERR           (1u << 5)
#define CS_WDATAERR            (1u << 7)
#define CS_CDBGRSTREQ          (1u << 26)
#define CS_CDBGPWRUPREQ        (1u << 28)
#define CS_CDBGPWRUPACK        (1u << 29)
#define CS_CSYSPWRUPREQ        (1u << 30)
#define CS_STICKY_MASK         (CS_STICKYORUN | CS_STICKYCMP | CS_STICKYERR | \
                                CS_WDATAERR)
#define CS_REQ_MASK            (CS_CDBGRSTREQ | CS_CDBGPWRUPREQ | CS_CSYSPWRUPREQ)

/* ABORT */
#define AB_DAPABORT            (1u << 0)
#define AB_STKCMPCLR           (1u << 1)
#define AB_STKERRCLR           (1u << 2)
#define AB_WDERRCLR            (1u << 3)
#define AB_ORUNERRCLR          (1u << 4)

/* SELECT: APSEL[31:24], APBANKSEL[7:4], CTRLSEL[0] */
#define SELECT_MASK            0xFF0000F1u
#define SELECT_CTRLSEL         (1u << 0)
#define WCR_RESET              0x00000040u
#define WCR_MASK               0x000003C7u

/* AHB-AP 0 registers (APBANKSEL << 4 | A[3:2] << 2) */
#define AP_CSW                 0x00u
#define AP_TAR                 0x04u
#define AP_DRW                 0x0Cu
#define AP_IDR                 0xFCu
#define AP_IDR_VALUE           0x04770031u
#define CSW_RESET              0x03000040u
#define CSW_WRITE_MASK         0xFF000037u /* DbgSwEnable/Prot, AddrInc, Size */
#define CSW_DEVICEEN           (1u << 6)
#define CSW_SIZE_MASK          0x7u
#define CSW_SIZE_32            0x2u
#define CSW_ADDRINC_SINGLE     0x1u
#define TAR_BLOCK_MASK         0x3FFu

/* Memory areas */
#define FLASH_BASE             0x08000000u
#define FLASH_SIZE             0x5000u
#define FLASH_PAGE             128u
#define FLASH_PAGE_WORDS       (FLASH_PAGE / 4u)
#define FLASH_SECTOR           4096u
#define SRAM_BASE              0x20000000u
#define SRAM_SIZE              0x0C00u
#define SYSROM_BASE            0x1FFF0000u
#define SYSROM_SIZE            0x0E00u
#define UID_BASE               0x1FFF0E00u
#define UID_SIZE               0x10u
#define OPTB_BASE              0x1FFF0E80u
#define OPTB_SIZE              0x10u
#define FACT_BASE              0x1FFF0F00u
#define FACT_SIZE              0x80u
#define FACT_HSI_FS4           0x1FFF0F6Cu

/* Registers */
#define REG_CPUID              0xE000ED00u
#define REG_AIRCR              0xE000ED0Cu
#define REG_DHCSR              0xE000EDF0u
#define REG_DCRSR              0xE000EDF4u
#define REG_DCRDR              0xE000EDF8u
#define REG_DEMCR              0xE000EDFCu
#define REG_DBGMCU_IDCODE      0x40015800u
#define REG_DBGMCU_CR          0x40015804u
#define REG_DBGMCU_APB_FZ1     0x40015808u
#define REG_DBGMCU_APB_FZ2     0x4001580Cu
#define REG_RCC_ICSCR          0x40021004u
#define REG_RCC_IOPENR         0x40021034u
#define REG_RCC_APBENR1        0x4002103Cu
#define REG_FLASH_KEYR         0x40022008u
#define REG_FLASH_OPTKEYR      0x4002200Cu
#define REG_OB_TRIGGER         0x40022080u
#define REG_FLASH_SR           0x40022010u
#define REG_FLASH_CR           0x40022014u
#define REG_FLASH_OPTR         0x40022020u
#define REG_FLASH_SDKR         0x40022024u
#define REG_FLASH_WRPR         0x4002202Cu
#define REG_FLASH_TIMING       0x40022100u /* TS0 .. PRETPE */
#define FLASH_TIMING_COUNT     9u
#define FLASH_TIMING_ALL       0x1FFu
#define REG_GPIOA_MODER        0x50000000u
#define REG_GPIOA_ODR          0x50000014u
#define REG_GPIOA_BSRR         0x50000018u
#define REG_GPIOA_BRR          0x50000028u

#define CPUID_VALUE            0x410CC601u
#define DBGMCU_IDCODE_VALUE    0x60001000u
#define DBGMCU_FZ1_IWDG        (1u << 12)
#define ICSCR_RESET            0x0159950Cu
#define IOPENR_MASK            0x00000023u
#define IOPENR_GPIOAEN         (1u << 0)
#define APBENR1_DBGEN          (1u << 27)
#define RDP_LEVEL0             0xAAu
#define RDP_LEVEL1             0x55u
#define RDP_LEVEL2             0xCCu
/* Loaded on a complement mismatch (OPTVERR): RDP 0xFF = level 1, user 0xFF */
#define OPTVERR_OPTR           0x0000FFFFu
#define OPTVERR_SDKR           0x0000001Fu
#define OPTVERR_WRPR           0x0000FFFFu
#define SDKR_MASK              0x1F1Fu

#define FLASH_KEY1             0x45670123u
#define FLASH_KEY2             0xCDEF89ABu
#define FLASH_OPTKEY1          0x08192A3Bu
#define FLASH_OPTKEY2          0x4C5D6E7Fu
#define FSR_EOP                (1u << 0)
#define FSR_WRPERR             (1u << 4)
#define FSR_OPTVERR            (1u << 15)
#define FSR_BSY                (1u << 16)
#define FSR_W1C                (FSR_EOP | FSR_WRPERR | FSR_OPTVERR)
#define FCR_PG                 (1u << 0)
#define FCR_PER                (1u << 1)
#define FCR_SER                (1u << 11)
#define FCR_OPTSTRT            (1u << 17)
#define FCR_PGSTRT             (1u << 19)
#define FCR_EOPIE              (1u << 24)
#define FCR_OBL_LAUNCH         (1u << 27)
#define FCR_OPTLOCK            (1u << 30)
#define FCR_LOCK               (1u << 31)
#define FCR_OPS                (FCR_PG | FCR_PER | FCR_SER)
#define FCR_WRITE_MASK         (FCR_OPS | FCR_OPTSTRT | FCR_PGSTRT | FCR_EOPIE | \
                                FCR_OBL_LAUNCH | FCR_OPTLOCK | FCR_LOCK)
#define FCR_RESET              (FCR_LOCK | FCR_OPTLOCK)
#define BUSY_READS_DEFAULT     2u
#define BUSY_READS_OB          5u

/* The stock preset: a factory unit (RDP level 1, its own firmware) */
#define STOCK_IMAGE_SIZE       0x2000u
#define STOCK_ENTRY            0x080000C0u

/* State file: magic "SWDT", then the format version and the model's size */
#define STATE_MAGIC            0x54445753u
#define STATE_VERSION          1u
#define CUT_GARBAGE            0xA5A5A5A5u

#define GPIOA_MODER_RESET      0xEBFFFFFFu
#define MODER_SWD_MASK         (0xFu << 26) /* PA13 SWDIO, PA14 SWCLK */
#define MODER_SWD_AF           (0xAu << 26)
#define MODER_GATES_MASK       0xFu         /* PA0, PA1 */
#define MODER_GATES_OUT        0x5u

#define DHCSR_KEY              0xA05Fu
#define DHCSR_C_DEBUGEN        (1u << 0)
#define DHCSR_C_HALT           (1u << 1)
#define DHCSR_S_REGRDY         (1u << 16)
#define DHCSR_S_HALT           (1u << 17)
#define DHCSR_S_LOCKUP         (1u << 19)
#define DHCSR_S_RESET_ST       (1u << 25)
#define DCRSR_REGWNR           (1u << 16)
#define DCRSR_REGSEL_MASK      0x1Fu
#define DEMCR_VC_CORERESET     (1u << 0)
#define DEMCR_MASK             0x01000401u /* VC_CORERESET, VC_HARDERR, DWTENA */
#define AIRCR_READ             0xFA050000u
#define AIRCR_VECTKEY          0x05FAu
#define AIRCR_SYSRESETREQ      (1u << 2)
#define XPSR_T                 (1u << 24)

/* Core registers by DCRSR.REGSEL */
#define CORE_SP                13u
#define CORE_PC                15u
#define CORE_XPSR              16u
#define CORE_MSP               17u
#define CORE_PSP               18u
#define CORE_CONTROL           20u
#define CORE_REGS              21u

/* The unmodelled Open Dimmer app at the stub start */
#define DEFAULT_PC             0x08001000u
#define DEFAULT_MSP            (SRAM_BASE + SRAM_SIZE)
#define SRAM_PATTERN           0xDEADBEEFu

#define WRITE_LOG_DEPTH        16384u
#define FLASH_LOG_DEPTH        4096u

typedef enum {
    ST_LOCKOUT,     /* power-on or protocol error: only a line reset is heard */
    ST_LINE_RESET,  /* inside the ones of a line reset */
    ST_IDLE,        /* waiting for a start bit */
    ST_REQUEST,     /* request bits after the start bit */
    ST_TRN_REQUEST, /* turnaround after the request */
    ST_ACK,         /* driving the ACK */
    ST_RDATA,       /* driving read data, then its parity */
    ST_TRN_READ,    /* turnaround after read data */
    ST_TRN_ACK,     /* turnaround after the ACK (write OK, WAIT, FAULT) */
    ST_WDATA,       /* taking write data, then its parity */
} dp_state_t;

typedef enum {
    AREA_FLASH,
    AREA_RAM,
    AREA_ROM,
} area_kind_t;

typedef struct {
    uint32_t    base;
    uint32_t    size;
    uint8_t *   mem;
    area_kind_t kind;
} area_t;

typedef enum {
    CORE_RUNNING,
    CORE_HALTED,
    CORE_LOCKUP,
} core_state_t;

typedef enum {
    FOP_ERASE_PAGE,
    FOP_ERASE_SECTOR,
    FOP_PROGRAM,
    FOP_WRPERR,
    FOP_GARBAGE,
    FOP_OPTIONS,    /* the option area from the staged registers */
    FOP_MASS_ERASE, /* log only: the RDP 1 -> 0 erase */
} flash_op_t;

typedef enum {
    PRESET_OPEN_DIMMER,
    PRESET_STOCK,
} preset_t;

typedef enum {
    KEY_WAIT1,   /* locked, waiting for KEY1 */
    KEY_WAIT2,   /* KEY1 taken */
    KEY_LOCKOUT, /* a wrong key: locked until a reset */
} key_state_t;

typedef struct {
    /* Core debug */
    uint32_t     cpuid;
    uint32_t     dhcsr_c;  /* C_DEBUGEN, C_HALT */
    uint8_t      reset_st; /* S_RESET_ST: a reset since the last DHCSR read */
    uint32_t     demcr;
    uint32_t     dcrdr;
    uint32_t     core[CORE_REGS];
    core_state_t core_state;

    /* DBGMCU */
    uint32_t     dbgmcu_idcode;
    uint32_t     dbgmcu_cr;
    uint32_t     dbgmcu_apb_fz1;
    uint32_t     dbgmcu_apb_fz2;

    /* FLASH controller */
    uint32_t     flash_sr;
    uint32_t     flash_cr;
    uint32_t     optr; /* loaded from the option area */
    uint32_t     sdkr;
    uint32_t     wrpr;
    uint32_t     stage_optr; /* written, programmed by OPTSTRT */
    uint32_t     stage_sdkr;
    uint32_t     stage_wrpr;
    uint32_t     timing[FLASH_TIMING_COUNT];
    uint16_t     timing_written;
    key_state_t  key_state;
    key_state_t  optkey_state;
    uint8_t      busy;      /* an operation runs (SR.BSY) */
    flash_op_t   busy_op;
    uint32_t     busy_addr;
    uint32_t     busy_left; /* SR reads until it ends */
    uint32_t     pg_buf[FLASH_PAGE_WORDS];
    uint32_t     pg_base;
    uint8_t      pg_n;

    /* RCC, GPIOA */
    uint32_t     icscr;
    uint32_t     iopenr;
    uint32_t     apbenr1;
    uint32_t     gpioa_moder;
    uint32_t     gpioa_odr;
} regs_t;

typedef struct {
    /* Test settings */
    uint8_t            connect;
    uint8_t            clk_held_low;
    uint8_t            rdp;
    uint32_t           wait_ap;
    uint32_t           busy_reads;
    uint32_t           busy_reads_ob;
    uint32_t           cut_left; /* transfers until the power goes, 0 = never */
    uint8_t            preset;

    /* The stock firmware's pin grab */
    uint8_t            stock_fw;      /* the flash still holds it */
    uint32_t           stock_pins_ms; /* after power-up; 0 = never */
    int64_t            power_up_ms;   /* stub time of the last power-up */
    uint8_t            stopped;       /* halted / locked up since then */
    uint8_t            pins_taken;

    /* The wire: the host side as swd_io.c last set it, the target side */
    uint8_t            host_clk_driven;
    uint8_t            host_clk_level;
    uint8_t            host_dio_driven;
    uint8_t            host_dio_level;
    uint8_t            clk_line; /* SWCLK as the target sees it */
    uint8_t            tgt_drive;
    uint8_t            tgt_level;
    uint8_t            swd_lost; /* PA13 / PA14 no longer SWD */

    /* SW-DP protocol */
    dp_state_t         st;
    uint8_t            need_dpidr;      /* after a line reset, until a DPIDR read */
    uint32_t           ones_run;        /* rising edges in a row with a host 1 */
    uint32_t           run_ones_errors; /* all-ones requests in that run */
    uint8_t            win_active;      /* the 16 bits after a line reset */
    uint8_t            win_n;
    uint16_t           win_bits;
    uint32_t           win_protocol_errors;
    uint32_t           win_parity_errors;
    uint8_t            req;
    uint8_t            nbits;
    uint8_t            ack;
    uint8_t            apndp;
    uint8_t            rnw;
    uint8_t            addr; /* A[3:2] << 2 */
    uint32_t           data;

    /* DP registers */
    uint32_t           ctrl_stat;
    uint32_t           select;
    uint32_t           wcr;
    uint32_t           rdbuff; /* the last AP read result */
    uint32_t           resend;

    /* AHB-AP 0 */
    uint32_t           csw;
    uint32_t           tar;

    regs_t             regs;
    swd_target_stats_t stats;
} model_t;

typedef struct {
    uint32_t addr;
    uint32_t value;
} write_rec_t;

typedef struct {
    flash_op_t op;
    uint32_t   addr;
} flash_rec_t;

static uint8_t s_flash[FLASH_SIZE];
static uint8_t s_sram[SRAM_SIZE];
static uint8_t s_sysrom[SYSROM_SIZE];
static uint8_t s_uid[UID_SIZE];
static uint8_t s_optb[OPTB_SIZE];
static uint8_t s_fact[FACT_SIZE];

static const area_t k_areas[] = {
    { FLASH_BASE,  FLASH_SIZE,  s_flash,  AREA_FLASH },
    { SRAM_BASE,   SRAM_SIZE,   s_sram,   AREA_RAM   },
    { SYSROM_BASE, SYSROM_SIZE, s_sysrom, AREA_ROM   },
    { UID_BASE,    UID_SIZE,    s_uid,    AREA_ROM   },
    { OPTB_BASE,   OPTB_SIZE,   s_optb,   AREA_ROM   },
    { FACT_BASE,   FACT_SIZE,   s_fact,   AREA_ROM   },
};

static const uint8_t k_optb_default[OPTB_SIZE] = {
    0xaa, 0xf9, 0x55, 0x06, 0xff, 0x00, 0x00, 0xff,
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00, 0x00,
};

/* A factory unit's option bytes: RDP 0x00 (level 1), user byte 0xF0 */
static const uint8_t k_optb_stock[OPTB_SIZE] = {
    0x00, 0xf0, 0xff, 0x0f, 0xff, 0x00, 0x00, 0xff,
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00, 0x00,
};

/* The last ROM word: with it the ROM's CRC-32 is the real chip's 0xA52232EA */
static const uint8_t k_sysrom_tail[4] = { 0xe7, 0x75, 0xa5, 0xe6 };

/* HSI_FS = 4 (24 MHz) flash timing table */
static const uint32_t k_hsi_fs4[] = {
    0x01B0B4B4u, 0x06C000B4u, 0x00014820u, 0x00014820u, 0x12C05DC0u,
};

static const char *const k_flash_op_names[] = {
    "erase_page",      "erase_sector", "program", "wrperr", "garbage",
    "program_options", "mass_erase",
};

static model_t     m;
static uint8_t     s_ready;
static write_rec_t s_log[WRITE_LOG_DEPTH];
static uint32_t    s_log_count;
static uint32_t    s_log_dropped;
static flash_rec_t s_flog[FLASH_LOG_DEPTH];
static uint32_t    s_flog_count;
static uint32_t    s_flog_dropped;

static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static void put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint8_t parity32(uint32_t v) {
    v ^= v >> 16;
    v ^= v >> 8;
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return (uint8_t)(v & 1u);
}

static const area_t *area_find(uint32_t addr, uint32_t len) {
    for (uint32_t i = 0; i < sizeof(k_areas) / sizeof(k_areas[0]); i++) {
        const area_t *a = &k_areas[i];
        if (addr >= a->base &&
            (uint64_t)(addr - a->base) + len <= (uint64_t)a->size) {
            return a;
        }
    }
    return NULL;
}

static uint8_t in_flash(uint32_t addr) {
    return addr >= FLASH_BASE && addr - FLASH_BASE < FLASH_SIZE;
}

/* An initial / current MSP: the stack may start at the end of the SRAM. */
static uint8_t in_stack(uint32_t addr) {
    return addr >= SRAM_BASE && addr - SRAM_BASE <= SRAM_SIZE;
}

static void log_write(uint32_t addr, uint32_t value) {
    m.stats.mem_writes++;
    if (s_log_count < WRITE_LOG_DEPTH) {
        s_log[s_log_count].addr  = addr;
        s_log[s_log_count].value = value;
        s_log_count++;
    } else {
        s_log_dropped++;
    }
}

static void log_flash(flash_op_t op, uint32_t addr) {
    if (s_flog_count < FLASH_LOG_DEPTH) {
        s_flog[s_flog_count].op   = op;
        s_flog[s_flog_count].addr = addr;
        s_flog_count++;
    } else {
        s_flog_dropped++;
    }
}

/* ---- option bytes, resets ---- */

static uint32_t optb16(uint32_t offset) {
    return (uint32_t)s_optb[offset] | ((uint32_t)s_optb[offset + 1] << 8);
}

static void apply_rdp(void) {
    uint8_t rdp = m.rdp ? RDP_LEVEL1 : RDP_LEVEL0;

    m.regs.optr       = (m.regs.optr & ~0xFFu) | rdp;
    m.regs.stage_optr = m.regs.optr;
    s_optb[0]         = rdp;
    s_optb[2]         = (uint8_t) ~rdp;
}

static uint8_t optb_pair_ok(uint32_t offset) {
    return (optb16(offset) ^ optb16(offset + 2)) == 0xFFFFu;
}

static void stage_from_loaded(void) {
    m.regs.stage_optr = m.regs.optr;
    m.regs.stage_sdkr = m.regs.sdkr;
    m.regs.stage_wrpr = m.regs.wrpr;
}

/* The option byte load (power-on, OBL_LAUNCH): a pair whose complement does
 * not match loads its fallback and sets OPTVERR. After the peripheral reset. */
static void options_load(void) {
    uint8_t bad = 0;

    if (optb_pair_ok(0)) {
        m.regs.optr = optb16(0);
    } else {
        m.regs.optr = OPTVERR_OPTR;
        bad         = 1;
    }
    if (optb_pair_ok(4)) {
        m.regs.sdkr = optb16(4) & SDKR_MASK;
    } else {
        m.regs.sdkr = OPTVERR_SDKR;
        bad         = 1;
    }
    if (optb_pair_ok(12)) {
        m.regs.wrpr = optb16(12);
    } else {
        m.regs.wrpr = OPTVERR_WRPR;
        bad         = 1;
    }
    if (bad) {
        m.regs.flash_sr |= FSR_OPTVERR;
    }
    m.rdp = (m.regs.optr & 0xFFu) != RDP_LEVEL0;
    stage_from_loaded();
}

/* The option area as OPTSTRT programs it from the staged registers. */
static void options_program(void) {
    uint32_t v[3]  = { m.regs.stage_optr, m.regs.stage_sdkr, m.regs.stage_wrpr };
    uint32_t at[3] = { 0, 4, 12 };

    for (uint32_t i = 0; i < 3; i++) {
        uint16_t w = (uint16_t)v[i];
        s_optb[at[i]]     = (uint8_t)w;
        s_optb[at[i] + 1] = (uint8_t)(w >> 8);
        s_optb[at[i] + 2] = (uint8_t) ~w;
        s_optb[at[i] + 3] = (uint8_t)(~w >> 8);
    }
    memset(&s_optb[8], 0xFF, 4);
}

/* What a system reset puts back (a busy flash operation is handled before). */
static void periph_reset(void) {
    m.regs.flash_sr     = 0;
    m.regs.flash_cr     = FCR_RESET;
    m.regs.key_state    = KEY_WAIT1;
    m.regs.optkey_state = KEY_WAIT1;
    stage_from_loaded();
    m.regs.busy      = 0;
    m.regs.busy_left = 0;
    m.regs.pg_n      = 0;
    memset(m.regs.timing, 0, sizeof(m.regs.timing));
    m.regs.timing_written = 0;
    m.regs.icscr          = ICSCR_RESET;
    m.regs.iopenr         = 0;
    m.regs.apbenr1        = 0;
    m.regs.gpioa_moder    = GPIOA_MODER_RESET;
    m.regs.gpioa_odr      = 0;
    m.swd_lost            = 0;
}

/* What only a power-on reset clears. */
static void debug_regs_clear(void) {
    m.regs.dhcsr_c = 0;
    m.regs.demcr   = 0;
    m.regs.dcrdr   = 0;
    memset(m.regs.core, 0, sizeof(m.regs.core));
    m.regs.dbgmcu_cr      = 0;
    m.regs.dbgmcu_apb_fz1 = 0;
    m.regs.dbgmcu_apb_fz2 = 0;
}

/* Every core state change: a halt or a lockup since the power-up keeps the
 * stock firmware from ever reaching its pin grab. */
static void core_set(core_state_t state) {
    m.regs.core_state = state;
    if (state != CORE_RUNNING) {
        m.stopped = 1;
    }
}

/* RDP level 1 with the debug domain powered: a running core loses its flash
 * (like an STM32 with a debugger attached) and locks up. */
static void rdp_debug_check(void) {
    if (m.rdp && (m.ctrl_stat & CS_CDBGPWRUPACK) &&
        m.regs.core_state == CORE_RUNNING) {
        core_set(CORE_LOCKUP);
    }
}

/* The core takes its vectors: halted there for a debugger, else it runs the
 * firmware, or locks up on vectors it cannot run. */
static void core_take_vectors(uint8_t halt) {
    uint32_t sp = get32(s_flash);
    uint32_t rv = get32(s_flash + 4);
    uint32_t pc = rv & ~1u;
    uint8_t  ok;

    m.regs.core[CORE_MSP]  = sp & ~3u;
    m.regs.core[CORE_PC]   = pc;
    m.regs.core[CORE_XPSR] = (rv & 1u) ? XPSR_T : 0;
    if (halt) {
        m.regs.dhcsr_c |= DHCSR_C_HALT;
        core_set(CORE_HALTED);
        return;
    }
    ok = (sp & 3u) == 0 && in_stack(sp) && (rv & 1u) && in_flash(pc) &&
         (s_flash[pc - FLASH_BASE] != 0xFF || s_flash[pc - FLASH_BASE + 1] != 0xFF);
    m.regs.dhcsr_c &= ~DHCSR_C_HALT;
    core_set(ok ? CORE_RUNNING : CORE_LOCKUP);
}

static void core_resume(void) {
    uint8_t ok = (m.regs.core[CORE_XPSR] & XPSR_T) &&
                 in_flash(m.regs.core[CORE_PC] & ~1u) &&
                 in_stack(m.regs.core[CORE_MSP]);

    core_set(ok ? CORE_RUNNING : CORE_LOCKUP);
}

static void flash_apply(flash_op_t op, uint32_t addr, uint8_t cut);

static void flash_finish(void) {
    if (m.regs.busy) {
        flash_apply(m.regs.busy_op, m.regs.busy_addr, 0);
        m.regs.busy      = 0;
        m.regs.busy_left = 0;
        m.regs.flash_sr |= FSR_EOP;
    }
}

static void system_reset(void) {
    m.stats.sys_resets++;
    flash_finish();
    periph_reset();
    m.regs.reset_st = 1;
    core_take_vectors((m.regs.demcr & DEMCR_VC_CORERESET) &&
                      (m.regs.dhcsr_c & DHCSR_C_DEBUGEN));
}

/* OBL_LAUNCH: a system reset that reloads the option bytes; the SWD link
 * drops (lockout until a line reset, sticky flags cleared), the rest of the
 * DP and the debug registers stay. */
static void obl_launch(void) {
    m.stats.obl_launches++;
    m.stats.sys_resets++;
    flash_finish();
    periph_reset();
    options_load();
    m.regs.reset_st = 1;
    core_take_vectors((m.regs.demcr & DEMCR_VC_CORERESET) &&
                      (m.regs.dhcsr_c & DHCSR_C_DEBUGEN));
    m.st              = ST_LOCKOUT;
    m.need_dpidr      = 1;
    m.tgt_drive       = 0;
    m.ones_run        = 0;
    m.run_ones_errors = 0;
    m.win_active      = 0;
    m.ctrl_stat      &= ~CS_STICKY_MASK;
}

static void dp_power_on(void) {
    m.st              = ST_LOCKOUT;
    m.need_dpidr      = 1;
    m.tgt_drive       = 0;
    m.ones_run        = 0;
    m.run_ones_errors = 0;
    m.win_active      = 0;
    m.ctrl_stat       = 0;
    m.select          = 0;
    m.wcr             = WCR_RESET;
    m.rdbuff          = 0;
    m.resend          = 0;
    m.csw             = CSW_RESET;
    m.tar             = 0;
    m.wait_ap         = 0;
    m.cut_left        = 0;
}

static void power_cycle(void) {
    if (m.regs.busy) {
        flash_apply(m.regs.busy_op, m.regs.busy_addr, 1);
        m.regs.busy = 0;
    }
    m.stats.power_cycles++;
    dp_power_on();
    for (uint32_t o = 0; o < SRAM_SIZE; o += 4) {
        put32(&s_sram[o], SRAM_PATTERN ^ o);
    }
    debug_regs_clear();
    periph_reset();
    options_load();
    m.regs.reset_st = 1;
    m.power_up_ms   = hal_millis();
    m.stopped       = 0;
    m.pins_taken    = 0;
    core_take_vectors(0);
}

static void memory_defaults(void) {
    memset(s_flash, 0xFF, sizeof(s_flash));
    memset(s_sram, 0x00, sizeof(s_sram));
    for (uint32_t i = 0; i < SYSROM_SIZE; i++) {
        s_sysrom[i] = (uint8_t)((i * 37u + 11u) & 0xFFu);
    }
    memcpy(&s_sysrom[SYSROM_SIZE - 4u], k_sysrom_tail, sizeof(k_sysrom_tail));
    for (uint32_t i = 0; i < UID_SIZE; i++) {
        s_uid[i] = (uint8_t)(0x10u + i);
    }
    memcpy(s_optb, k_optb_default, sizeof(s_optb));
    memset(s_fact, 0xFF, sizeof(s_fact));
    for (uint32_t i = 0; i < sizeof(k_hsi_fs4) / sizeof(k_hsi_fs4[0]); i++) {
        put32(&s_fact[FACT_HSI_FS4 - FACT_BASE + 4u * i], k_hsi_fs4[i]);
    }

    memset(&m.regs, 0, sizeof(m.regs));
    m.regs.cpuid         = CPUID_VALUE;
    m.regs.dbgmcu_idcode = DBGMCU_IDCODE_VALUE;
    debug_regs_clear();
    periph_reset();
    options_load();
    m.regs.reset_st        = 1; /* power-on is a reset too */
    m.regs.core_state      = CORE_RUNNING;
    m.regs.core[CORE_PC]   = DEFAULT_PC;
    m.regs.core[CORE_MSP]  = DEFAULT_MSP;
    m.regs.core[CORE_XPSR] = XPSR_T;
}

static void power_on(void) {
    uint8_t host_clk_driven = m.host_clk_driven;
    uint8_t host_clk_level  = m.host_clk_level;
    uint8_t host_dio_driven = m.host_dio_driven;
    uint8_t host_dio_level  = m.host_dio_level;

    memset(&m, 0, sizeof(m));
    m.host_clk_driven = host_clk_driven;
    m.host_clk_level  = host_clk_level;
    m.host_dio_driven = host_dio_driven;
    m.host_dio_level  = host_dio_level;

    m.connect       = 1;
    m.busy_reads    = BUSY_READS_DEFAULT;
    m.busy_reads_ob = BUSY_READS_OB;
    dp_power_on();
    memory_defaults();
    m.power_up_ms  = hal_millis();
    s_log_count    = 0;
    s_log_dropped  = 0;
    s_flog_count   = 0;
    s_flog_dropped = 0;
}

/* A factory unit: its firmware in the flash, RDP level 1, running. */
static void preset_stock(void) {
    for (uint32_t i = 0; i < STOCK_IMAGE_SIZE; i++) {
        s_flash[i] = (uint8_t)(i * 131u + 7u);
    }
    put32(s_flash, SRAM_BASE + SRAM_SIZE);
    put32(s_flash + 4, STOCK_ENTRY | 1u);
    memcpy(s_optb, k_optb_stock, sizeof(s_optb));
    options_load();
    core_take_vectors(0);
    m.stock_fw = 1;
}

/* ---- the start: presets, environment, state file ---- */

static const char *env_str(const char *name) {
    const char *v = getenv(name);

    return (v && *v) ? v : NULL;
}

static uint8_t env_preset(void) {
    const char *v = env_str("SWD_TARGET_PRESET");

    if (v && strcmp(v, "stock") == 0) {
        return PRESET_STOCK;
    }
    if (v && strcmp(v, "open_dimmer") != 0) {
        fprintf(stderr, "[SWD_TARGET] unknown SWD_TARGET_PRESET '%s': open_dimmer\n",
                v);
    }
    return PRESET_OPEN_DIMMER;
}

static void env_flash(void) {
    const char *path = env_str("SWD_TARGET_FLASH");
    FILE *      f;
    size_t      n;

    if (!path) {
        return;
    }
    f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "[SWD_TARGET] cannot read SWD_TARGET_FLASH '%s'\n", path);
        return;
    }
    n = fread(s_flash, 1, sizeof(s_flash), f);
    fclose(f);
    fprintf(stderr, "[SWD_TARGET] %u flash bytes from %s\n", (unsigned)n, path);
}

static void env_stock_pins(void) {
    const char *v = env_str("SWD_TARGET_STOCK_PINS_MS");

    if (v) {
        m.stock_pins_ms = (uint32_t)strtoul(v, NULL, 10);
    }
}

/* Power-on with a preset; the environment's flash file and pin time on top. */
static void start_preset(uint8_t preset) {
    power_on();
    m.preset = preset;
    if (preset == PRESET_STOCK) {
        preset_stock();
    }
    if (env_str("SWD_TARGET_FLASH")) {
        env_flash();
        core_take_vectors(0); /* the core runs what the file holds */
    }
    env_stock_pins();
}

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t model_size;
    uint32_t pad;
    int64_t  uptime_ms; /* the target's time since its power-up */
} state_hdr_t;

static uint8_t *const k_state_areas[] = {
    s_flash, s_sram, s_sysrom, s_uid, s_optb, s_fact,
};
static const uint32_t k_state_sizes[] = {
    FLASH_SIZE, SRAM_SIZE, SYSROM_SIZE, UID_SIZE, OPTB_SIZE, FACT_SIZE,
};

static int state_save(const char *path) {
    state_hdr_t hdr = { STATE_MAGIC, STATE_VERSION, sizeof(model_t), 0,
                        (int64_t)hal_millis() - m.power_up_ms };
    size_t      len = strlen(path) + 5;
    char *      tmp = malloc(len);
    FILE *      f;
    int         ok;

    if (!tmp) {
        return -1;
    }
    snprintf(tmp, len, "%s.tmp", path);
    f  = fopen(tmp, "wb");
    ok = f != NULL && fwrite(&hdr, sizeof(hdr), 1, f) == 1;
    for (uint32_t i = 0; ok && i < sizeof(k_state_sizes) / sizeof(k_state_sizes[0]); i++) {
        ok = fwrite(k_state_areas[i], k_state_sizes[i], 1, f) == 1;
    }
    ok = ok && fwrite(&m, sizeof(m), 1, f) == 1;
    if (f) {
        ok = (fclose(f) == 0) && ok;
    }
    ok = ok && rename(tmp, path) == 0;
    if (!ok) {
        fprintf(stderr, "[SWD_TARGET] cannot save the state to %s\n", path);
        remove(tmp);
    }
    free(tmp);
    return ok ? 0 : -1;
}

/* 0 = loaded; -1 = no (usable) file, nothing changed. */
static int state_load(const char *path) {
    static uint8_t buf[FLASH_SIZE + SRAM_SIZE + SYSROM_SIZE + UID_SIZE +
                       OPTB_SIZE + FACT_SIZE];
    state_hdr_t hdr;
    model_t     saved;
    FILE *      f = fopen(path, "rb");
    int         ok;
    uint32_t    pos = 0;

    if (!f) {
        return -1;
    }
    ok = fread(&hdr, sizeof(hdr), 1, f) == 1 && hdr.magic == STATE_MAGIC &&
         hdr.version == STATE_VERSION && hdr.model_size == sizeof(model_t) &&
         fread(buf, sizeof(buf), 1, f) == 1 && fread(&saved, sizeof(saved), 1, f) == 1;
    fclose(f);
    if (!ok) {
        fprintf(stderr, "[SWD_TARGET] %s is not a state of this build: ignored\n",
                path);
        return -1;
    }
    for (uint32_t i = 0; i < sizeof(k_state_sizes) / sizeof(k_state_sizes[0]); i++) {
        memcpy(k_state_areas[i], buf + pos, k_state_sizes[i]);
        pos += k_state_sizes[i];
    }
    /* The host side of the wire is this process's. */
    saved.host_clk_driven = m.host_clk_driven;
    saved.host_clk_level  = m.host_clk_level;
    saved.host_dio_driven = m.host_dio_driven;
    saved.host_dio_level  = m.host_dio_level;
    saved.clk_line        = 0;
    m              = saved;
    m.power_up_ms  = (int64_t)hal_millis() - hdr.uptime_ms;
    s_log_count    = 0;
    s_log_dropped  = 0;
    s_flog_count   = 0;
    s_flog_dropped = 0;
    return 0;
}

static void state_save_at_exit(void) {
    const char *path = env_str("SWD_TARGET_STATE");

    if (path) {
        (void)state_save(path);
    }
}

static void stock_tick(void);

static void start_from_env(void) {
    const char *state = env_str("SWD_TARGET_STATE");
    const char *pc    = env_str("SWD_TARGET_POWER_CYCLE");

    start_preset(env_preset());
    if (state) {
        if (state_load(state) == 0) {
            fprintf(stderr, "[SWD_TARGET] state from %s\n", state);
            env_stock_pins();
        }
        atexit(state_save_at_exit);
    }
    if (pc && strcmp(pc, "1") == 0) {
        power_cycle();
    }
}

static void ensure_ready(void) {
    if (!s_ready) {
        s_ready = 1;
        start_from_env();
    }
    stock_tick();
    rdp_debug_check();
}

/* ---- the wire ---- */

static uint8_t wired(void) {
    return m.connect && !m.swd_lost;
}

/* SWCLK held low by the target side: the test setting, or the stock
 * firmware that took PA13 / PA14. */
static uint8_t clk_held(void) {
    return m.clk_held_low || m.pins_taken;
}

/* The stock firmware takes its pins stock_pins_ms after its power-up, if it
 * has run all the time since (no halt, no lockup). */
static void stock_tick(void) {
    if (m.stock_fw && m.stock_pins_ms && !m.pins_taken && !m.stopped &&
        m.regs.core_state == CORE_RUNNING &&
        (int64_t)hal_millis() - m.power_up_ms >= (int64_t)m.stock_pins_ms) {
        m.pins_taken = 1;
        m.tgt_drive  = 0;
        m.clk_line   = 0;
    }
}

static uint8_t clk_line_now(void) {
    if (!wired() || clk_held() || !m.host_clk_driven) {
        return 0;
    }
    return m.host_clk_level;
}

static uint8_t dio_line_now(void) {
    if (m.host_dio_driven) {
        return m.host_dio_level;
    }
    if (wired() && m.tgt_drive) {
        return m.tgt_level;
    }
    return 1;
}

static void tgt_drive(uint8_t level) {
    if (!m.tgt_drive && m.host_dio_driven) {
        m.stats.contention++;
    }
    m.tgt_drive = 1;
    m.tgt_level = level ? 1 : 0;
}

static void tgt_release(void) {
    m.tgt_drive = 0;
}

/* ---- flash controller ---- */

static uint8_t sector_protected(uint32_t addr) {
    uint32_t sector = (addr - FLASH_BASE) / FLASH_SECTOR;

    return !((m.regs.wrpr >> sector) & 1u);
}

/* The option area from the staged registers; RDP level 1 -> 0 erases the
 * whole main flash right then. */
static void flash_apply_options(uint8_t cut) {
    uint8_t was_level1 = s_optb[0] != RDP_LEVEL0;

    if (cut) {
        log_flash(FOP_GARBAGE, OPTB_BASE);
        for (uint32_t o = 0; o < OPTB_SIZE; o += 4) {
            put32(&s_optb[o], get32(&s_optb[o]) & CUT_GARBAGE);
        }
        return;
    }
    options_program();
    if (was_level1 && s_optb[0] == RDP_LEVEL0) {
        memset(s_flash, 0xFF, sizeof(s_flash));
        log_flash(FOP_MASS_ERASE, FLASH_BASE);
        m.stats.mass_erases++;
        m.stock_fw = 0;
    }
}

static void flash_apply(flash_op_t op, uint32_t addr, uint8_t cut) {
    uint32_t size;
    uint8_t *p;

    if (op == FOP_OPTIONS) {
        flash_apply_options(cut);
        return;
    }
    p          = &s_flash[addr - FLASH_BASE];
    m.stock_fw = 0; /* the stock firmware is no longer whole */
    switch (op) {
    case FOP_ERASE_PAGE:
        size = FLASH_PAGE;
        break;
    case FOP_ERASE_SECTOR:
        size = FLASH_SECTOR;
        break;
    case FOP_PROGRAM:
        size = FLASH_PAGE;
        break;
    default:
        return;
    }
    if (cut) {
        log_flash(FOP_GARBAGE, addr);
        for (uint32_t o = 0; o < size; o += 4) {
            put32(p + o, get32(p + o) & CUT_GARBAGE);
        }
    } else if (op == FOP_PROGRAM) {
        for (uint32_t i = 0; i < FLASH_PAGE_WORDS; i++) {
            put32(p + 4u * i, get32(p + 4u * i) & m.regs.pg_buf[i]);
        }
    } else {
        memset(p, 0xFF, size);
    }
}

static void flash_wrperr(uint32_t addr) {
    m.regs.flash_sr |= FSR_WRPERR;
    m.stats.wrperr++;
    log_flash(FOP_WRPERR, addr);
}

static void flash_start(flash_op_t op, uint32_t addr) {
    if (sector_protected(addr)) {
        flash_wrperr(addr);
        return;
    }
    log_flash(op, addr);
    if (op == FOP_PROGRAM) {
        m.stats.programs++;
    } else {
        m.stats.erases++;
    }
    m.regs.busy      = 1;
    m.regs.busy_op   = op;
    m.regs.busy_addr = addr;
    m.regs.busy_left = m.busy_reads;
    if (!m.regs.busy_left) {
        flash_finish();
    }
}

static void flash_program_word(uint32_t addr, uint32_t value) {
    uint32_t base  = addr & ~(FLASH_PAGE - 1u);
    uint32_t index = (addr - base) / 4u;

    if (m.regs.pg_n == 0) {
        if (index != 0) {
            flash_wrperr(addr);
            return;
        }
        m.regs.pg_base = base;
    } else if (base != m.regs.pg_base || index != m.regs.pg_n) {
        m.regs.pg_n = 0;
        flash_wrperr(addr);
        return;
    }
    m.regs.pg_buf[m.regs.pg_n++] = value;
    if (m.regs.pg_n < FLASH_PAGE_WORDS) {
        return;
    }
    m.regs.pg_n = 0;
    if (!(m.regs.flash_cr & FCR_PGSTRT)) {
        flash_wrperr(base);
        return;
    }
    m.regs.flash_cr &= ~FCR_PGSTRT;
    flash_start(FOP_PROGRAM, base);
}

/* A word write into the flash through the AP (RDP and BSY checked before).
 * 0 = taken (done or refused with WRPERR), -1 = bus error. */
static int flash_mem_write(uint32_t addr, uint32_t value) {
    uint32_t ops = m.regs.flash_cr & FCR_OPS;

    if ((m.regs.flash_cr & FCR_LOCK) || !ops) {
        return -1;
    }
    switch (ops) {
    case FCR_PER:
        flash_start(FOP_ERASE_PAGE, addr & ~(FLASH_PAGE - 1u));
        break;
    case FCR_SER:
        flash_start(FOP_ERASE_SECTOR, addr & ~(FLASH_SECTOR - 1u));
        break;
    case FCR_PG:
        flash_program_word(addr, value);
        break;
    default:
        flash_wrperr(addr); /* more than one operation */
        break;
    }
    return 0;
}

static uint32_t flash_sr_read(uint8_t backdoor) {
    uint32_t v = m.regs.flash_sr;

    if (m.regs.busy) {
        v |= FSR_BSY;
        if (!backdoor && --m.regs.busy_left == 0) {
            flash_finish();
        }
    }
    return v;
}

static void flash_keyr_write(uint32_t value) {
    if (!(m.regs.flash_cr & FCR_LOCK) || m.regs.key_state == KEY_LOCKOUT) {
        return;
    }
    if (m.regs.key_state == KEY_WAIT1 && value == FLASH_KEY1) {
        m.regs.key_state = KEY_WAIT2;
    } else if (m.regs.key_state == KEY_WAIT2 && value == FLASH_KEY2) {
        m.regs.key_state = KEY_WAIT1;
        m.regs.flash_cr &= ~FCR_LOCK;
    } else {
        m.regs.key_state = KEY_LOCKOUT;
    }
}

/* OPTKEYR: only with CR.LOCK clear; a wrong key keeps OPTLOCK until a reset. */
static void flash_optkeyr_write(uint32_t value) {
    if ((m.regs.flash_cr & FCR_LOCK) || !(m.regs.flash_cr & FCR_OPTLOCK) ||
        m.regs.optkey_state == KEY_LOCKOUT) {
        return;
    }
    if (m.regs.optkey_state == KEY_WAIT1 && value == FLASH_OPTKEY1) {
        m.regs.optkey_state = KEY_WAIT2;
    } else if (m.regs.optkey_state == KEY_WAIT2 && value == FLASH_OPTKEY2) {
        m.regs.optkey_state = KEY_WAIT1;
        m.regs.flash_cr    &= ~FCR_OPTLOCK;
    } else {
        m.regs.optkey_state = KEY_LOCKOUT;
    }
}

static uint8_t options_unlocked(void) {
    return !(m.regs.flash_cr & (FCR_LOCK | FCR_OPTLOCK));
}

/* A write to OPTR / SDKR / WRPR stages a value for OPTSTRT. */
static void option_reg_write(uint32_t *stage, uint32_t value) {
    if (options_unlocked()) {
        *stage = value;
    }
}

/* The word write to 0x40022080 with CR.OPTSTRT: program the option area. */
static void flash_ob_trigger(void) {
    if (!(m.regs.flash_cr & FCR_OPTSTRT) || !options_unlocked() || m.regs.busy) {
        return;
    }
    m.regs.flash_cr &= ~FCR_OPTSTRT;
    if ((m.regs.stage_optr & 0xFFu) == RDP_LEVEL2) {
        flash_wrperr(OPTB_BASE); /* never program RDP level 2 */
        return;
    }
    log_flash(FOP_OPTIONS, OPTB_BASE);
    m.stats.option_programs++;
    m.regs.busy      = 1;
    m.regs.busy_op   = FOP_OPTIONS;
    m.regs.busy_addr = OPTB_BASE;
    m.regs.busy_left = m.busy_reads_ob;
    if (!m.regs.busy_left) {
        flash_finish();
    }
}

static void flash_cr_write(uint32_t value) {
    uint32_t v;

    if (m.regs.flash_cr & FCR_LOCK) {
        return;
    }
    v = value & FCR_WRITE_MASK;
    if (m.regs.flash_cr & FCR_OPTLOCK) {
        /* OPTLOCK locks OPTSTRT and OBL_LAUNCH; only OPTKEYR clears it. */
        v = (v & ~(FCR_OPTSTRT | FCR_OBL_LAUNCH)) | FCR_OPTLOCK;
    }
    m.regs.flash_cr = v;
    if (!(m.regs.flash_cr & FCR_PG)) {
        m.regs.pg_n = 0;
    }
    if (m.regs.flash_cr & FCR_OBL_LAUNCH) {
        obl_launch();
    }
}

/* ---- GPIOA ---- */

static void gpioa_moder_write(uint32_t value) {
    m.regs.gpioa_moder = value;
    if ((value & MODER_SWD_MASK) != MODER_SWD_AF) {
        m.swd_lost  = 1;
        m.tgt_drive = 0;
    }
}

/* ---- core debug ---- */

static uint32_t dhcsr_value(void) {
    uint32_t v = m.regs.dhcsr_c | DHCSR_S_REGRDY;

    if (m.regs.core_state == CORE_HALTED) {
        v |= DHCSR_S_HALT;
    } else if (m.regs.core_state == CORE_LOCKUP) {
        v |= DHCSR_S_LOCKUP;
    }
    if (m.regs.reset_st) {
        v |= DHCSR_S_RESET_ST;
    }
    return v;
}

static void dhcsr_write(uint32_t value) {
    if ((value >> 16) != DHCSR_KEY) {
        return;
    }
    if (!(value & DHCSR_C_DEBUGEN)) {
        /* With C_DEBUGEN 0 the other control bits are ignored. */
        m.regs.dhcsr_c = 0;
    } else {
        m.regs.dhcsr_c = value & (DHCSR_C_DEBUGEN | DHCSR_C_HALT);
    }
    if (m.regs.dhcsr_c & DHCSR_C_HALT) {
        core_set(CORE_HALTED); /* from running or lockup */
    } else if (m.regs.core_state == CORE_HALTED) {
        core_resume();
    }
}

static uint8_t core_reg_valid(uint32_t sel) {
    return sel <= CORE_PSP || sel == CORE_CONTROL;
}

static void dcrsr_write(uint32_t value) {
    uint32_t sel = value & DCRSR_REGSEL_MASK;

    if (m.regs.core_state != CORE_HALTED || !core_reg_valid(sel)) {
        return;
    }
    if (sel == CORE_SP) {
        sel = CORE_MSP; /* CONTROL.SPSEL 0 in the debug state */
    }
    if (value & DCRSR_REGWNR) {
        uint32_t v = m.regs.dcrdr;
        if (sel == CORE_MSP || sel == CORE_PSP) {
            v &= ~3u;
        }
        m.regs.core[sel] = v;
    } else {
        m.regs.dcrdr = m.regs.core[sel];
    }
}

/* ---- memory map ---- */

static uint8_t timing_index(uint32_t addr, uint32_t *index) {
    if (addr >= REG_FLASH_TIMING && (addr & 3u) == 0 &&
        (addr - REG_FLASH_TIMING) / 4u < FLASH_TIMING_COUNT) {
        *index = (addr - REG_FLASH_TIMING) / 4u;
        return 1;
    }
    return 0;
}

static uint8_t gpioa_reg(uint32_t addr) {
    return addr == REG_GPIOA_MODER || addr == REG_GPIOA_ODR ||
           addr == REG_GPIOA_BSRR || addr == REG_GPIOA_BRR;
}

/* 0 = done, -1 = bus error. The backdoor ignores RDP, clocks and side
 * effects. */
static int mem_read32(uint32_t addr, uint32_t *value, uint8_t backdoor) {
    const area_t *a = area_find(addr, 4);
    uint32_t      index;

    if (a) {
        if (a->kind == AREA_FLASH && m.rdp && !backdoor) {
            return -1;
        }
        *value = get32(a->mem + (addr - a->base));
        return 0;
    }
    if (timing_index(addr, &index)) {
        *value = m.regs.timing[index];
        return 0;
    }
    if (gpioa_reg(addr)) {
        if (!(m.regs.iopenr & IOPENR_GPIOAEN) && !backdoor) {
            return -1;
        }
        *value = addr == REG_GPIOA_MODER ? m.regs.gpioa_moder
               : addr == REG_GPIOA_ODR   ? m.regs.gpioa_odr
                                         : 0;
        return 0;
    }
    switch (addr) {
    case REG_CPUID:
        *value = m.regs.cpuid;
        break;
    case REG_AIRCR:
        *value = AIRCR_READ;
        break;
    case REG_DHCSR:
        *value = dhcsr_value();
        if (!backdoor) {
            m.regs.reset_st = 0;
        }
        break;
    case REG_DCRSR:
        *value = 0; /* write only */
        break;
    case REG_DCRDR:
        *value = m.regs.dcrdr;
        break;
    case REG_DEMCR:
        *value = m.regs.demcr;
        break;
    case REG_DBGMCU_IDCODE:
        *value = m.regs.dbgmcu_idcode;
        break;
    case REG_DBGMCU_CR:
        *value = m.regs.dbgmcu_cr;
        break;
    case REG_DBGMCU_APB_FZ1:
        *value = m.regs.dbgmcu_apb_fz1;
        break;
    case REG_DBGMCU_APB_FZ2:
        *value = m.regs.dbgmcu_apb_fz2;
        break;
    case REG_RCC_ICSCR:
        *value = m.regs.icscr;
        break;
    case REG_RCC_IOPENR:
        *value = m.regs.iopenr;
        break;
    case REG_RCC_APBENR1:
        *value = m.regs.apbenr1;
        break;
    case REG_FLASH_KEYR:
    case REG_FLASH_OPTKEYR:
    case REG_OB_TRIGGER:
        *value = 0; /* write only */
        break;
    case REG_FLASH_SR:
        *value = flash_sr_read(backdoor);
        break;
    case REG_FLASH_CR:
        *value = m.regs.flash_cr;
        break;
    case REG_FLASH_OPTR:
        *value = m.regs.optr;
        break;
    case REG_FLASH_SDKR:
        *value = m.regs.sdkr;
        break;
    case REG_FLASH_WRPR:
        *value = m.regs.wrpr;
        break;
    default:
        return -1;
    }
    return 0;
}

static void dbgmcu_write(uint32_t *reg, uint32_t value) {
    if (m.regs.apbenr1 & APBENR1_DBGEN) {
        *reg = value;
    }
}

/* A write through the AP. 0 = done (or ignored), -1 = bus error. */
static int mem_write32(uint32_t addr, uint32_t value) {
    const area_t *a = area_find(addr, 4);
    uint32_t      index;

    if (a) {
        if (a->kind == AREA_RAM) {
            put32(a->mem + (addr - a->base), value);
        } else if (a->kind == AREA_FLASH) {
            return m.rdp ? -1 : flash_mem_write(addr, value);
        } else if (addr == OPTB_BASE && (value & 0xFFu) == RDP_LEVEL2) {
            m.stats.rdp2_written++;
        }
        return 0; /* ROM-like areas ignore writes */
    }
    if (timing_index(addr, &index)) {
        m.regs.timing[index]   = value;
        m.regs.timing_written |= (uint16_t)(1u << index);
        return 0;
    }
    if (gpioa_reg(addr)) {
        if (!(m.regs.iopenr & IOPENR_GPIOAEN)) {
            return -1;
        }
        if (addr == REG_GPIOA_MODER) {
            gpioa_moder_write(value);
        } else if (addr == REG_GPIOA_ODR) {
            m.regs.gpioa_odr = value & 0xFFFFu;
        } else if (addr == REG_GPIOA_BSRR) {
            /* Set wins over reset. */
            m.regs.gpioa_odr = ((m.regs.gpioa_odr & ~(value >> 16)) | value) &
                               0xFFFFu;
        } else {
            m.regs.gpioa_odr &= ~(value & 0xFFFFu);
        }
        return 0;
    }
    switch (addr) {
    case REG_DHCSR:
        dhcsr_write(value);
        break;
    case REG_DCRSR:
        dcrsr_write(value);
        break;
    case REG_DCRDR:
        m.regs.dcrdr = value;
        break;
    case REG_DEMCR:
        m.regs.demcr = value & DEMCR_MASK;
        break;
    case REG_AIRCR:
        if ((value >> 16) == AIRCR_VECTKEY && (value & AIRCR_SYSRESETREQ)) {
            system_reset();
        }
        break;
    case REG_DBGMCU_CR:
        dbgmcu_write(&m.regs.dbgmcu_cr, value);
        break;
    case REG_DBGMCU_APB_FZ1:
        dbgmcu_write(&m.regs.dbgmcu_apb_fz1, value);
        break;
    case REG_DBGMCU_APB_FZ2:
        dbgmcu_write(&m.regs.dbgmcu_apb_fz2, value);
        break;
    case REG_RCC_ICSCR:
        m.regs.icscr = value;
        break;
    case REG_RCC_IOPENR:
        m.regs.iopenr = value & IOPENR_MASK;
        break;
    case REG_RCC_APBENR1:
        m.regs.apbenr1 = value;
        break;
    case REG_FLASH_KEYR:
        flash_keyr_write(value);
        break;
    case REG_FLASH_OPTKEYR:
        flash_optkeyr_write(value);
        break;
    case REG_OB_TRIGGER:
        flash_ob_trigger();
        break;
    case REG_FLASH_SR:
        m.regs.flash_sr &= ~(value & FSR_W1C);
        break;
    case REG_FLASH_CR:
        flash_cr_write(value);
        break;
    case REG_FLASH_OPTR:
        if ((value & 0xFFu) == RDP_LEVEL2) {
            m.stats.rdp2_written++; /* counted even when not taken */
        }
        option_reg_write(&m.regs.stage_optr, value);
        break;
    case REG_FLASH_SDKR:
        option_reg_write(&m.regs.stage_sdkr, value);
        break;
    case REG_FLASH_WRPR:
        option_reg_write(&m.regs.stage_wrpr, value);
        break;
    case REG_CPUID:
    case REG_DBGMCU_IDCODE:
        break;
    default:
        return -1;
    }
    return 0;
}

/* ---- AHB-AP 0 ---- */

static uint8_t ap_reg(void) {
    return (uint8_t)((((m.select >> 4) & 0xFu) << 4) | m.addr);
}

static uint8_t ap_selected(void) {
    return (m.select >> 24) == 0;
}

static uint32_t drw_access(uint8_t write, uint32_t wdata) {
    uint32_t addr  = m.tar;
    uint32_t rdata = 0;
    int      err;

    if (write) {
        log_write(addr, wdata);
    }
    if ((m.csw & CSW_SIZE_MASK) != CSW_SIZE_32 || (addr & 3u)) {
        err = -1; /* only aligned 32-bit accesses are modelled */
    } else if (write) {
        err = mem_write32(addr, wdata);
    } else {
        err = mem_read32(addr, &rdata, 0);
    }
    if (err) {
        m.ctrl_stat |= CS_STICKYERR;
        m.stats.bus_errors++;
        return 0;
    }
    if (((m.csw >> 4) & 3u) == CSW_ADDRINC_SINGLE) {
        m.tar = (addr & ~TAR_BLOCK_MASK) | ((addr + 4u) & TAR_BLOCK_MASK);
    }
    return rdata;
}

static uint32_t ap_read(uint8_t reg) {
    if (!ap_selected()) {
        return 0;
    }
    switch (reg) {
    case AP_CSW:
        return m.csw;

    case AP_TAR:
        return m.tar;

    case AP_DRW:
        return drw_access(0, 0);

    case AP_IDR:
        return AP_IDR_VALUE;

    default:
        return 0;
    }
}

static void ap_write(uint8_t reg, uint32_t value) {
    if (!ap_selected()) {
        return;
    }
    switch (reg) {
    case AP_CSW:
        m.csw = (value & CSW_WRITE_MASK) | CSW_DEVICEEN;
        break;
    case AP_TAR:
        m.tar = value;
        break;
    case AP_DRW:
        (void)drw_access(1, value);
        break;
    default:
        break;
    }
}

/* ---- SW-DP ---- */

static void abort_write(uint32_t value) {
    if (value & AB_DAPABORT) {
        m.wait_ap = 0;
    }
    if (value & AB_STKCMPCLR) {
        m.ctrl_stat &= ~CS_STICKYCMP;
    }
    if (value & AB_STKERRCLR) {
        m.ctrl_stat &= ~CS_STICKYERR;
    }
    if (value & AB_WDERRCLR) {
        m.ctrl_stat &= ~CS_WDATAERR;
    }
    if (value & AB_ORUNERRCLR) {
        m.ctrl_stat &= ~CS_STICKYORUN;
    }
}

static void dp_write(uint8_t addr, uint32_t value) {
    uint32_t req;

    switch (addr) {
    case 0x0:
        abort_write(value);
        break;
    case 0x4:
        if (m.select & SELECT_CTRLSEL) {
            m.wcr = value & WCR_MASK;
            break;
        }
        /* The ACKs mirror the REQs at once; sticky flags only clear by ABORT. */
        req         = value & CS_REQ_MASK;
        m.ctrl_stat = (m.ctrl_stat & CS_STICKY_MASK) | req | (req << 1);
        break;
    case 0x8:
        m.select = value & SELECT_MASK;
        break;
    default:
        break; /* 0xC: reserved in DPv1 */
    }
}

static uint8_t read_ok(uint32_t value) {
    m.data = value;
    m.stats.transfers_ok++;
    return ACK_OK;
}

static uint8_t dp_request(void) {
    if (m.rnw && m.addr == 0x0) {
        m.need_dpidr = 0;
        m.stats.dpidr_reads++;
        return read_ok(DPIDR_VALUE);
    }
    if (!m.rnw && m.addr == 0x0) {
        return ACK_OK; /* ABORT */
    }
    if (m.rnw && m.addr == 0x4) {
        return read_ok((m.select & SELECT_CTRLSEL) ? m.wcr : m.ctrl_stat);
    }
    if (m.ctrl_stat & CS_STICKY_MASK) {
        return ACK_FAULT;
    }
    if (!m.rnw) {
        return ACK_OK; /* CTRL/STAT, SELECT, 0xC */
    }
    if (m.addr == 0x8) {
        return read_ok(m.resend);
    }
    m.resend = m.rdbuff;
    return read_ok(m.rdbuff);
}

static uint8_t ap_request(void) {
    uint32_t posted;

    if (m.ctrl_stat & CS_STICKY_MASK) {
        return ACK_FAULT;
    }
    if (!(m.ctrl_stat & CS_CDBGPWRUPACK)) {
        m.ctrl_stat |= CS_STICKYERR;
        return ACK_FAULT;
    }
    if (m.wait_ap) {
        m.wait_ap--;
        return ACK_WAIT;
    }
    if (m.regs.busy && ap_selected() && ap_reg() == AP_DRW &&
        (in_flash(m.tar) || (m.tar >= OPTB_BASE && m.tar - OPTB_BASE < OPTB_SIZE))) {
        return ACK_WAIT; /* the flash stalls the AHB while busy */
    }
    if (!m.rnw) {
        return ACK_OK;
    }
    posted   = m.rdbuff;
    m.rdbuff = ap_read(ap_reg());
    m.resend = posted;
    return read_ok(posted);
}

static void request_error(uint8_t parity_bad) {
    m.stats.protocol_errors++;
    m.stats.parity_errors += parity_bad;
    if (m.req == 0xFF) {
        m.run_ones_errors++; /* may be the start of a line reset */
    }
    if (m.win_active) {
        m.win_protocol_errors++;
        m.win_parity_errors += parity_bad;
    }
    m.st = ST_LOCKOUT;
}

static void request_done(void) {
    uint8_t apndp      = (m.req >> 1) & 1u;
    uint8_t rnw        = (m.req >> 2) & 1u;
    uint8_t a2         = (m.req >> 3) & 1u;
    uint8_t a3         = (m.req >> 4) & 1u;
    uint8_t parity     = (m.req >> 5) & 1u;
    uint8_t stop       = (m.req >> 6) & 1u;
    uint8_t park       = (m.req >> 7) & 1u;
    uint8_t parity_bad = parity != (apndp ^ rnw ^ a2 ^ a3);

    if (parity_bad || stop || !park) {
        request_error(parity_bad);
        return;
    }
    m.apndp = apndp;
    m.rnw   = rnw;
    m.addr  = (uint8_t)((a3 << 3) | (a2 << 2));
    if (m.need_dpidr && (apndp || !rnw || m.addr != 0x0)) {
        request_error(0);
        return;
    }
    m.ack = apndp ? ap_request() : dp_request();
    if (m.ack == ACK_WAIT) {
        m.stats.waits++;
    } else if (m.ack == ACK_FAULT) {
        m.stats.faults++;
    }
    m.st = ST_TRN_REQUEST;
}

static void write_done(uint8_t parity_bit) {
    if (parity_bit != parity32(m.data)) {
        m.ctrl_stat |= CS_WDATAERR;
        m.stats.parity_errors++;
        return;
    }
    m.stats.transfers_ok++;
    if (m.apndp) {
        ap_write(ap_reg(), m.data);
    } else {
        dp_write(m.addr, m.data);
    }
    rdp_debug_check(); /* a power-up or a resume may have just done it */
}

/* The end of a transfer that got an ACK: the place of a power cut. */
static void transfer_end(void) {
    if (m.cut_left && --m.cut_left == 0) {
        power_cycle();
    }
}

static void line_reset(void) {
    m.stats.line_resets++;
    /* Those ones were a line reset, not bad requests. */
    m.stats.protocol_errors -= m.run_ones_errors;
    m.stats.parity_errors   -= m.run_ones_errors;
    m.run_ones_errors        = 0;
    m.win_active             = 0;
    m.need_dpidr             = 1;
    m.st = ST_LINE_RESET;
    tgt_release();
}

/* The 16 bits after a line reset, against the JTAG-to-SWD sequence. */
static void window_bit(uint8_t from_target, uint8_t bit) {
    if (from_target) {
        m.win_active = 0;
        return;
    }
    m.win_bits |= (uint16_t)(bit << m.win_n);
    m.win_n++;
    if (m.win_n < JTAG_TO_SWD_BITS) {
        return;
    }
    m.win_active = 0;
    if (m.win_bits == JTAG_TO_SWD) {
        m.stats.jtag_to_swd++;
        m.stats.protocol_errors -= m.win_protocol_errors;
        m.stats.parity_errors   -= m.win_parity_errors;
    }
}

static void dp_step(uint8_t bit) {
    switch (m.st) {
    case ST_LOCKOUT:
        break;
    case ST_LINE_RESET:
        if (!bit) {
            m.st                  = ST_IDLE;
            m.win_active          = 1;
            m.win_n               = 1; /* this 0 is bit 0 */
            m.win_bits            = 0;
            m.win_protocol_errors = 0;
            m.win_parity_errors   = 0;
        }
        break;
    case ST_IDLE:
        if (bit) {
            m.req   = 1;
            m.nbits = 1;
            m.st    = ST_REQUEST;
        }
        break;
    case ST_REQUEST:
        m.req |= (uint8_t)(bit << m.nbits);
        m.nbits++;
        if (m.nbits == 8) {
            request_done();
        }
        break;
    case ST_TRN_REQUEST:
        tgt_drive(m.ack & 1u);
        m.nbits = 1;
        m.st    = ST_ACK;
        break;
    case ST_ACK:
        if (m.nbits < 3) {
            tgt_drive((m.ack >> m.nbits) & 1u);
            m.nbits++;
        } else if (m.ack == ACK_OK && m.rnw) {
            tgt_drive(m.data & 1u);
            m.nbits = 1;
            m.st    = ST_RDATA;
        } else {
            tgt_release();
            m.st = ST_TRN_ACK;
        }
        break;
    case ST_RDATA:
        if (m.nbits < 32) {
            tgt_drive((m.data >> m.nbits) & 1u);
            m.nbits++;
        } else if (m.nbits == 32) {
            tgt_drive(parity32(m.data));
            m.nbits++;
        } else {
            tgt_release();
            m.st = ST_TRN_READ;
            transfer_end();
        }
        break;
    case ST_TRN_READ:
        m.st = ST_IDLE;
        break;
    case ST_TRN_ACK:
        if (m.ack == ACK_OK) {
            m.data  = 0;
            m.nbits = 0;
            m.st    = ST_WDATA;
        } else {
            m.st = ST_IDLE;
            transfer_end();
        }
        break;
    case ST_WDATA:
        if (m.nbits < 32) {
            m.data |= (uint32_t)bit << m.nbits;
            m.nbits++;
        } else {
            m.st = ST_IDLE; /* before: OBL_LAUNCH drops the link */
            write_done(bit);
            transfer_end();
        }
        break;
    }
}

static void rising_edge(void) {
    uint8_t from_target = m.tgt_drive;
    uint8_t bit         = dio_line_now();

    m.stats.edges++;
    if (!from_target && bit) {
        m.ones_run++;
        if (m.ones_run == LINE_RESET_ONES) {
            line_reset();
            return;
        }
    } else {
        m.ones_run        = 0;
        m.run_ones_errors = 0;
    }
    if (m.win_active) {
        window_bit(from_target, bit);
    }
    dp_step(bit);
}

void swd_target_host_clk(uint8_t driven, uint8_t level) {
    uint8_t line;

    ensure_ready();
    m.host_clk_driven = driven ? 1 : 0;
    m.host_clk_level  = level ? 1 : 0;
    if (m.connect && clk_held() && m.host_clk_driven) {
        m.stats.contention++;
    }
    line = clk_line_now();
    if (line && !m.clk_line) {
        m.clk_line = 1;
        rising_edge();
    } else {
        m.clk_line = line;
    }
}

void swd_target_host_dio(uint8_t driven, uint8_t level) {
    ensure_ready();
    driven = driven ? 1 : 0;
    if (driven && !m.host_dio_driven && wired() && m.tgt_drive) {
        m.stats.contention++;
    }
    m.host_dio_driven = driven;
    m.host_dio_level  = level ? 1 : 0;
}

uint8_t swd_target_dio_level(void) {
    ensure_ready();
    return dio_line_now();
}

uint8_t swd_target_clk_idle_level(void) {
    ensure_ready();
    return (m.connect && clk_held()) ? 0 : 1;
}

void swd_target_note_untaken(void) {
    ensure_ready();
    m.stats.untaken_ops++;
}

/* ---- test controls ---- */

void swd_target_reset(void) {
    ensure_ready();
    start_preset(env_preset());
    m.clk_line = clk_line_now();
}

void swd_target_clear(void) {
    ensure_ready();
    memset(&m.stats, 0, sizeof(m.stats));
    /* Nothing counted before is taken back later. */
    m.run_ones_errors     = 0;
    m.win_protocol_errors = 0;
    m.win_parity_errors   = 0;
    s_log_count           = 0;
    s_log_dropped         = 0;
    s_flog_count          = 0;
    s_flog_dropped        = 0;
}

void swd_target_power_cycle(void) {
    ensure_ready();
    power_cycle();
    m.clk_line = clk_line_now();
}

void swd_target_set_connect(uint8_t on) {
    ensure_ready();
    m.connect  = on ? 1 : 0;
    m.clk_line = clk_line_now();
}

void swd_target_set_clk_held_low(uint8_t on) {
    ensure_ready();
    m.clk_held_low = on ? 1 : 0;
    m.clk_line     = clk_line_now();
}

void swd_target_set_rdp(uint8_t level) {
    ensure_ready();
    m.rdp = level ? 1 : 0;
    apply_rdp();
}

void swd_target_set_wait_ap(uint32_t count) {
    ensure_ready();
    m.wait_ap = count;
}

void swd_target_set_busy_reads(uint32_t reads) {
    ensure_ready();
    m.busy_reads = reads;
}

void swd_target_set_cut_after(uint32_t transfers) {
    ensure_ready();
    m.cut_left = transfers;
}

void swd_target_set_busy_reads_ob(uint32_t reads) {
    ensure_ready();
    m.busy_reads_ob = reads;
}

void swd_target_set_stock_pins_ms(uint32_t ms) {
    ensure_ready();
    m.stock_pins_ms = ms;
}

int swd_target_preset(const char *name) {
    uint8_t preset;

    ensure_ready();
    if (strcmp(name, "stock") == 0) {
        preset = PRESET_STOCK;
    } else if (strcmp(name, "open_dimmer") == 0) {
        preset = PRESET_OPEN_DIMMER;
    } else {
        return -1;
    }
    start_preset(preset);
    m.clk_line = clk_line_now();
    return 0;
}

void swd_target_start(void) {
    ensure_ready();
}

int swd_target_save(void) {
    const char *path = env_str("SWD_TARGET_STATE");

    ensure_ready();
    return path ? state_save(path) : -1;
}

int swd_target_peek32(uint32_t addr, uint32_t *value) {
    ensure_ready();
    return mem_read32(addr, value, 1);
}

int swd_target_poke32(uint32_t addr, uint32_t value) {
    const area_t *a;
    uint32_t      index;

    ensure_ready();
    a = area_find(addr, 4);
    if (a) {
        put32(a->mem + (addr - a->base), value);
        return 0;
    }
    if (timing_index(addr, &index)) {
        m.regs.timing[index] = value;
        return 0;
    }
    switch (addr) {
    case REG_CPUID:
        m.regs.cpuid = value;
        break;
    case REG_AIRCR:
    case REG_DCRSR:
    case REG_FLASH_KEYR:
    case REG_FLASH_OPTKEYR:
    case REG_OB_TRIGGER:
    case REG_GPIOA_BSRR:
    case REG_GPIOA_BRR:
        break; /* no state to set */
    case REG_DHCSR:
        m.regs.dhcsr_c = value & (DHCSR_C_DEBUGEN | DHCSR_C_HALT);
        if (m.regs.dhcsr_c & DHCSR_C_HALT) {
            core_set(CORE_HALTED);
        } else if (m.regs.core_state == CORE_HALTED) {
            core_set(CORE_RUNNING);
        }
        break;
    case REG_DCRDR:
        m.regs.dcrdr = value;
        break;
    case REG_DEMCR:
        m.regs.demcr = value & DEMCR_MASK;
        break;
    case REG_DBGMCU_IDCODE:
        m.regs.dbgmcu_idcode = value;
        break;
    case REG_DBGMCU_CR:
        m.regs.dbgmcu_cr = value;
        break;
    case REG_DBGMCU_APB_FZ1:
        m.regs.dbgmcu_apb_fz1 = value;
        break;
    case REG_DBGMCU_APB_FZ2:
        m.regs.dbgmcu_apb_fz2 = value;
        break;
    case REG_RCC_ICSCR:
        m.regs.icscr = value;
        break;
    case REG_RCC_IOPENR:
        m.regs.iopenr = value & IOPENR_MASK;
        break;
    case REG_RCC_APBENR1:
        m.regs.apbenr1 = value;
        break;
    case REG_FLASH_SR:
        m.regs.flash_sr = value & ~FSR_BSY;
        break;
    case REG_FLASH_CR:
        m.regs.flash_cr = value;
        break;
    case REG_FLASH_OPTR: /* the loaded value (and the staged one) */
        m.regs.optr       = value;
        m.regs.stage_optr = value;
        break;
    case REG_FLASH_SDKR:
        m.regs.sdkr       = value;
        m.regs.stage_sdkr = value;
        break;
    case REG_FLASH_WRPR:
        m.regs.wrpr       = value;
        m.regs.stage_wrpr = value;
        break;
    case REG_GPIOA_MODER:
        m.regs.gpioa_moder = value;
        break;
    case REG_GPIOA_ODR:
        m.regs.gpioa_odr = value & 0xFFFFu;
        break;
    default:
        return -1;
    }
    return 0;
}

int swd_target_load(uint32_t addr, const uint8_t *data, uint32_t len) {
    const area_t *a;

    ensure_ready();
    a = area_find(addr, len);
    if (!a) {
        return -1;
    }
    memcpy(a->mem + (addr - a->base), data, len);
    return 0;
}

void swd_target_get_stats(swd_target_stats_t *out) {
    ensure_ready();
    *out = m.stats;
}

void swd_target_get_state(swd_target_state_t *out) {
    static const char *const names[] = { "running", "halted", "lockup" };

    ensure_ready();
    out->core      = names[m.regs.core_state];
    out->pc        = m.regs.core[CORE_PC];
    out->msp       = m.regs.core[CORE_MSP];
    out->xpsr      = m.regs.core[CORE_XPSR];
    out->gates_low = (m.regs.gpioa_moder & MODER_GATES_MASK) == MODER_GATES_OUT &&
                     !(m.regs.gpioa_odr & 0x3u);
    out->iwdg_frozen    = (m.regs.dbgmcu_apb_fz1 & DBGMCU_FZ1_IWDG) ? 1 : 0;
    out->timing_set     = m.regs.timing_written == FLASH_TIMING_ALL;
    out->flash_locked   = (m.regs.flash_cr & FCR_LOCK) ? 1 : 0;
    out->key_lockout    = m.regs.key_state == KEY_LOCKOUT;
    out->flash_busy     = m.regs.busy;
    out->swd_lost       = m.swd_lost;
    out->preset         = m.preset == PRESET_STOCK ? "stock" : "open_dimmer";
    out->rdp            = m.rdp;
    out->optlock        = (m.regs.flash_cr & FCR_OPTLOCK) ? 1 : 0;
    out->optkey_lockout = m.regs.optkey_state == KEY_LOCKOUT;
    out->stock_fw       = m.stock_fw;
    out->pins_taken     = m.pins_taken;
    out->stock_pins_ms  = m.stock_pins_ms;
    out->uptime_ms      = (uint32_t)((int64_t)hal_millis() - m.power_up_ms);
}

uint8_t swd_target_powered(void) {
    ensure_ready();
    return (m.ctrl_stat & CS_CDBGPWRUPACK) ? 1 : 0;
}

uint8_t swd_target_lockout(void) {
    ensure_ready();
    return m.st == ST_LOCKOUT;
}

uint32_t swd_target_ctrl_stat(void) {
    ensure_ready();
    return m.ctrl_stat;
}

uint32_t swd_target_write_count(void) {
    ensure_ready();
    return s_log_count;
}

uint32_t swd_target_writes_dropped(void) {
    ensure_ready();
    return s_log_dropped;
}

void swd_target_write_at(uint32_t index, uint32_t *addr, uint32_t *value) {
    ensure_ready();
    if (index >= s_log_count) {
        *addr  = 0;
        *value = 0;
        return;
    }
    *addr  = s_log[index].addr;
    *value = s_log[index].value;
}

uint32_t swd_target_flash_log_count(void) {
    ensure_ready();
    return s_flog_count;
}

uint32_t swd_target_flash_log_dropped(void) {
    ensure_ready();
    return s_flog_dropped;
}

void swd_target_flash_log_at(uint32_t index, const char **op, uint32_t *addr) {
    ensure_ready();
    if (index >= s_flog_count) {
        *op   = "";
        *addr = 0;
        return;
    }
    *op   = k_flash_op_names[s_flog[index].op];
    *addr = s_flog[index].addr;
}
