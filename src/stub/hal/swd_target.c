#include "stub/hal/swd_target.h"

#include <stdint.h>
#include <string.h>

/* See swd_target.h for what is modelled. Bit numbers below are ADIv5 (DP,
 * AHB-AP), ARMv6-M (SCS) and the PY32F002A reference manual (DBGMCU, FLASH,
 * RCC). */

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
#define REG_DEMCR              0xE000EDFCu
#define REG_DBGMCU_IDCODE      0x40015800u
#define REG_DBGMCU_CR          0x40015804u
#define REG_DBGMCU_APB_FZ1     0x40015808u
#define REG_DBGMCU_APB_FZ2     0x4001580Cu
#define REG_FLASH_SR           0x40022010u
#define REG_FLASH_CR           0x40022014u
#define REG_FLASH_OPTR         0x40022020u
#define REG_FLASH_SDKR         0x40022024u
#define REG_FLASH_WRPR         0x4002202Cu
#define REG_RCC_ICSCR          0x40021004u

#define CPUID_VALUE            0x410CC601u
#define DBGMCU_IDCODE_VALUE    0x60001000u
#define FLASH_SR_RESET         0x00000000u
#define FLASH_CR_RESET         0xC0000000u
#define OPTR_RESET             0x0000F9AAu
#define SDKR_RESET             0x0000001Fu
#define WRPR_RESET             0x0000FFFFu
#define ICSCR_RESET            0x0159950Cu
#define RDP_LEVEL0             0xAAu
#define RDP_LEVEL1             0x55u

#define DHCSR_KEY              0xA05Fu
#define DHCSR_C_DEBUGEN        (1u << 0)
#define DHCSR_C_HALT           (1u << 1)
#define DHCSR_S_REGRDY         (1u << 16)
#define DHCSR_S_HALT           (1u << 17)
#define DHCSR_S_RESET_ST       (1u << 25)
#define DEMCR_VC_CORERESET     (1u << 0)
#define DEMCR_MASK             0x01000401u /* VC_CORERESET, VC_HARDERR, DWTENA */
#define AIRCR_READ             0xFA050000u
#define AIRCR_VECTKEY          0x05FAu
#define AIRCR_SYSRESETREQ      (1u << 2)

#define WRITE_LOG_DEPTH        16384u

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

typedef struct {
    uint32_t cpuid;
    uint32_t dhcsr_c;  /* C_DEBUGEN, C_HALT */
    uint8_t  reset_st; /* S_RESET_ST: a reset since the last DHCSR read */
    uint32_t demcr;
    uint32_t dbgmcu_idcode;
    uint32_t dbgmcu_cr;
    uint32_t dbgmcu_apb_fz1;
    uint32_t dbgmcu_apb_fz2;
    uint32_t flash_sr;
    uint32_t flash_cr;
    uint32_t optr;
    uint32_t sdkr;
    uint32_t wrpr;
    uint32_t icscr;
} regs_t;

typedef struct {
    /* Test settings */
    uint8_t            connect;
    uint8_t            clk_held_low;
    uint8_t            rdp;
    uint32_t           wait_ap;

    /* The wire: the host side as swd_io.c last set it, the target side */
    uint8_t            host_clk_driven;
    uint8_t            host_clk_level;
    uint8_t            host_dio_driven;
    uint8_t            host_dio_level;
    uint8_t            clk_line;  /* SWCLK as the target sees it */
    uint8_t            tgt_drive;
    uint8_t            tgt_level;

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

/* HSI_FS = 4 (24 MHz) flash timing table */
static const uint32_t k_hsi_fs4[] = {
    0x01B0B4B4u, 0x06C000B4u, 0x00014820u, 0x00014820u, 0x12C05DC0u,
};

static model_t     m;
static uint8_t     s_ready;
static write_rec_t s_log[WRITE_LOG_DEPTH];
static uint32_t    s_log_count;
static uint32_t    s_log_dropped;

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

static void apply_rdp(void) {
    uint8_t rdp = m.rdp ? RDP_LEVEL1 : RDP_LEVEL0;

    m.regs.optr = (m.regs.optr & ~0xFFu) | rdp;
    s_optb[0]   = rdp;
    s_optb[2]   = (uint8_t) ~rdp;
}

static void memory_defaults(void) {
    memset(s_flash, 0xFF, sizeof(s_flash));
    memset(s_sram, 0x00, sizeof(s_sram));
    for (uint32_t i = 0; i < SYSROM_SIZE; i++) {
        s_sysrom[i] = (uint8_t)((i * 37u + 11u) & 0xFFu);
    }
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
    m.regs.flash_sr      = FLASH_SR_RESET;
    m.regs.flash_cr      = FLASH_CR_RESET;
    m.regs.optr          = OPTR_RESET;
    m.regs.sdkr          = SDKR_RESET;
    m.regs.wrpr          = WRPR_RESET;
    m.regs.icscr         = ICSCR_RESET;
    m.regs.reset_st      = 1; /* power-on is a reset too */
    apply_rdp();
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

    m.connect    = 1;
    m.st         = ST_LOCKOUT;
    m.need_dpidr = 1;
    m.wcr        = WCR_RESET;
    m.csw        = CSW_RESET;
    memory_defaults();
    s_log_count   = 0;
    s_log_dropped = 0;
}

static void ensure_ready(void) {
    if (!s_ready) {
        s_ready = 1;
        power_on();
    }
}

static uint8_t clk_line_now(void) {
    if (!m.connect || m.clk_held_low || !m.host_clk_driven) {
        return 0;
    }
    return m.host_clk_level;
}

static uint8_t dio_line_now(void) {
    if (m.host_dio_driven) {
        return m.host_dio_level;
    }
    if (m.connect && m.tgt_drive) {
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

/* ---- memory map ---- */

static uint32_t dhcsr_value(void) {
    uint32_t v = m.regs.dhcsr_c | DHCSR_S_REGRDY;

    if (m.regs.dhcsr_c & DHCSR_C_HALT) {
        v |= DHCSR_S_HALT;
    }
    if (m.regs.reset_st) {
        v |= DHCSR_S_RESET_ST;
    }
    return v;
}

static void system_reset(void) {
    m.stats.sys_resets++;
    m.regs.reset_st = 1;
    m.regs.flash_sr = FLASH_SR_RESET;
    m.regs.flash_cr = FLASH_CR_RESET;
    m.regs.icscr    = ICSCR_RESET;
    /* Debug registers keep their values; a reset vector catch halts. */
    if ((m.regs.demcr & DEMCR_VC_CORERESET) &&
        (m.regs.dhcsr_c & DHCSR_C_DEBUGEN)) {
        m.regs.dhcsr_c |= DHCSR_C_HALT;
    }
}

/* 0 = done, -1 = bus error. The backdoor ignores RDP and side effects. */
static int mem_read32(uint32_t addr, uint32_t *value, uint8_t backdoor) {
    const area_t *a = area_find(addr, 4);

    if (a) {
        if (a->kind == AREA_FLASH && m.rdp && !backdoor) {
            return -1;
        }
        *value = get32(a->mem + (addr - a->base));
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
    case REG_FLASH_SR:
        *value = m.regs.flash_sr;
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
    case REG_RCC_ICSCR:
        *value = m.regs.icscr;
        break;
    default:
        return -1;
    }
    return 0;
}

/* A write through the AP. 0 = done (or ignored), -1 = bus error. */
static int mem_write32(uint32_t addr, uint32_t value) {
    const area_t *a = area_find(addr, 4);

    if (a) {
        if (a->kind == AREA_RAM) {
            put32(a->mem + (addr - a->base), value);
        } else if (a->kind == AREA_FLASH && m.rdp) {
            return -1;
        }
        /* Flash: programming not modelled. ROM-like areas ignore writes. */
        return 0;
    }
    switch (addr) {
    case REG_DHCSR:
        if ((value >> 16) == DHCSR_KEY) {
            /* With C_DEBUGEN 0 the other control bits are ignored. */
            m.regs.dhcsr_c = (value & DHCSR_C_DEBUGEN)
                             ? (value & (DHCSR_C_DEBUGEN | DHCSR_C_HALT))
                             : 0;
        }
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
    case REG_CPUID:
    case REG_DBGMCU_IDCODE:
    /* FLASH: CR stays locked (no KEYR model), so these keep their values. */
    case REG_FLASH_SR:
    case REG_FLASH_CR:
    case REG_FLASH_OPTR:
    case REG_FLASH_SDKR:
    case REG_FLASH_WRPR:
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
        }
        break;
    case ST_WDATA:
        if (m.nbits < 32) {
            m.data |= (uint32_t)bit << m.nbits;
            m.nbits++;
        } else {
            write_done(bit);
            m.st = ST_IDLE;
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

/* ---- the wire ---- */

void swd_target_host_clk(uint8_t driven, uint8_t level) {
    uint8_t line;

    ensure_ready();
    m.host_clk_driven = driven ? 1 : 0;
    m.host_clk_level  = level ? 1 : 0;
    if (m.connect && m.clk_held_low && m.host_clk_driven) {
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
    if (driven && !m.host_dio_driven && m.connect && m.tgt_drive) {
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
    return (m.connect && m.clk_held_low) ? 0 : 1;
}

void swd_target_note_untaken(void) {
    ensure_ready();
    m.stats.untaken_ops++;
}

/* ---- test controls ---- */

void swd_target_reset(void) {
    ensure_ready();
    power_on();
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

int swd_target_peek32(uint32_t addr, uint32_t *value) {
    ensure_ready();
    return mem_read32(addr, value, 1);
}

int swd_target_poke32(uint32_t addr, uint32_t value) {
    const area_t *a;

    ensure_ready();
    a = area_find(addr, 4);
    if (a) {
        put32(a->mem + (addr - a->base), value);
        return 0;
    }
    switch (addr) {
    case REG_CPUID:
        m.regs.cpuid = value;
        break;
    case REG_AIRCR:
        break; /* reads are fixed */
    case REG_DHCSR:
        m.regs.dhcsr_c = value & (DHCSR_C_DEBUGEN | DHCSR_C_HALT);
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
    case REG_FLASH_SR:
        m.regs.flash_sr = value;
        break;
    case REG_FLASH_CR:
        m.regs.flash_cr = value;
        break;
    case REG_FLASH_OPTR:
        m.regs.optr = value;
        break;
    case REG_FLASH_SDKR:
        m.regs.sdkr = value;
        break;
    case REG_FLASH_WRPR:
        m.regs.wrpr = value;
        break;
    case REG_RCC_ICSCR:
        m.regs.icscr = value;
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
