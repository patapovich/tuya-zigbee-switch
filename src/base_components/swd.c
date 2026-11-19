/* Only in builds that carry an MCU image (src/telink/Makefile; the stub is
 * also built without, for tests/test_open_dimmer_symbols.py). */
#ifdef SECONDARY_MCU_IMAGE

#include "swd.h"

#include "hal/swd_io.h"

/* Bit timing as CMSIS-DAP does it: every bit is a falling then a rising SWCLK
 * edge. The target samples SWDIO on the rising edge and changes it after the
 * rising edge; the host samples in the low phase, before it raises SWCLK.
 * Request, ACK and data are sent LSB first. */

#define ACK_OK            0x1u
#define ACK_WAIT          0x2u
#define ACK_FAULT         0x4u

#define CSW_32BIT_INC     0x23000012u /* Prot 0x23, AddrInc single, Size 32 */
#define SELECT_UNKNOWN    0xFFFFFFFFu

static uint32_t g_select = SELECT_UNKNOWN;
static uint8_t  g_csw_set;

static void write_bit(uint8_t bit) {
    hal_swd_dio_set(bit);
    hal_swd_clk(0);
    hal_swd_delay();
    hal_swd_clk(1);
    hal_swd_delay();
}

static uint8_t read_bit(void) {
    hal_swd_clk(0);
    hal_swd_delay();
    uint8_t bit = hal_swd_dio_get();
    hal_swd_clk(1);
    hal_swd_delay();
    return bit;
}

static void write_bits(uint32_t value, uint8_t count) {
    while (count--) {
        write_bit((uint8_t)(value & 1u));
        value >>= 1;
    }
}

static uint32_t read_bits(uint8_t count) {
    uint32_t value = 0;

    for (uint8_t i = 0; i < count; i++) {
        value |= (uint32_t)read_bit() << i;
    }
    return value;
}

/* One clock with nobody driving; the host lets go of SWDIO first. */
static void turn_to_target(void) {
    hal_swd_dio_drive(0);
    hal_swd_clk(0);
    hal_swd_delay();
    hal_swd_clk(1);
    hal_swd_delay();
}

/* One clock with nobody driving, then the host drives SWDIO again. */
static void turn_to_host(void) {
    hal_swd_clk(0);
    hal_swd_delay();
    hal_swd_clk(1);
    hal_swd_delay();
    hal_swd_dio_set(0);
    hal_swd_dio_drive(1);
}

static uint8_t parity32(uint32_t v) {
    v ^= v >> 16;
    v ^= v >> 8;
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return (uint8_t)(v & 1u);
}

/* start, APnDP, RnW, A2, A3, parity, stop (0), park (1) */
static uint8_t request(uint8_t ap, uint8_t read, uint8_t addr) {
    uint8_t bits = (uint8_t)((ap & 1u) | ((read & 1u) << 1) | (((addr >> 2) & 3u) << 2));

    return (uint8_t)(0x81u | (bits << 1) | (parity32(bits) << 5));
}

static void idle(uint8_t cycles) {
    hal_swd_dio_set(0);
    write_bits(0, cycles);
}

/* >= 50 clocks with SWDIO high. */
static void line_reset_ones(void) {
    hal_swd_dio_drive(1);
    write_bits(0xFFFFFFFFu, 32);
    write_bits(0xFFFFFFFFu, 24);
}

static swd_err_t transfer(uint8_t ap, uint8_t read, uint8_t addr, uint32_t *data) {
    uint8_t req = request(ap, read, addr);

    for (uint8_t attempt = 0; attempt < SWD_WAIT_RETRIES; attempt++) {
        hal_swd_dio_drive(1);
        write_bits(req, 8);
        turn_to_target();
        uint8_t ack = (uint8_t)read_bits(3);

        if (ack == ACK_OK) {
            if (read) {
                uint32_t value = read_bits(32);
                uint8_t  par   = read_bit();
                turn_to_host();
                idle(2);
                if (par != parity32(value)) {
                    return SWD_ERR_PARITY;
                }
                *data = value;
            } else {
                turn_to_host();
                write_bits(*data, 32);
                write_bit(parity32(*data));
                idle(2);
            }
            return SWD_OK;
        }
        turn_to_host();
        if (ack == ACK_WAIT) {
            idle(8);
            continue;
        }
        idle(2);
        if (ack == ACK_FAULT) {
            return SWD_ERR_FAULT;
        }

        /* Nobody answered (the line stays high) or garbage: the DP needs a
         * line reset before it listens again (swd_connect). */
        return ack == 0x7u ? SWD_ERR_NO_TARGET : SWD_ERR_PROTOCOL;
    }
    uint32_t abort = 1u; /* DAPABORT */
    transfer(0, 0, SWD_DP_ABORT, &abort);
    return SWD_ERR_WAIT;
}

swd_err_t swd_begin(hal_gpio_pin_t clk, hal_gpio_pin_t dio) {
    if (!hal_swd_clk_idle_level(clk)) {
        return SWD_ERR_PINS;
    }
    hal_swd_take(clk, dio);
    g_select  = SELECT_UNKNOWN;
    g_csw_set = 0;
    return SWD_OK;
}

void swd_end(void) {
    hal_swd_release();
}

swd_err_t swd_connect(uint32_t *dpidr) {
    /* ADIv5 JTAG-to-SWD: the 16-bit sequence right after the ones (harmless
     * on an SW-only DP), then a line reset and at least 2 idle clocks. */
    line_reset_ones();
    write_bits(0xE79Eu, 16);
    line_reset_ones();
    idle(2);
    g_select  = SELECT_UNKNOWN;
    g_csw_set = 0;
    return transfer(0, 1, SWD_DP_DPIDR, dpidr);
}

swd_err_t swd_dp_read(uint8_t addr, uint32_t *value) {
    return transfer(0, 1, addr, value);
}

swd_err_t swd_dp_write(uint8_t addr, uint32_t value) {
    return transfer(0, 0, addr, &value);
}

swd_err_t swd_clear_errors(void) {
    return swd_dp_write(SWD_DP_ABORT, SWD_ABORT_CLEAR_ALL);
}

swd_err_t swd_power_up(void) {
    swd_err_t err = swd_clear_errors();

    if (err == SWD_OK) {
        err = swd_dp_write(SWD_DP_CTRL_STAT, 0x50000000u);
    }
    for (uint8_t i = 0; err == SWD_OK && i < SWD_POWER_POLLS; i++) {
        uint32_t stat = 0;
        err = swd_dp_read(SWD_DP_CTRL_STAT, &stat);
        if (err == SWD_OK && (stat & 0xA0000000u) == 0xA0000000u) {
            return SWD_OK;
        }
    }
    return err == SWD_OK ? SWD_ERR_POWER : err;
}

swd_err_t swd_power_down(void) {
    return swd_dp_write(SWD_DP_CTRL_STAT, 0);
}

static swd_err_t select_bank(uint8_t reg) {
    uint32_t sel = (uint32_t)(reg & 0xF0u); /* APSEL 0, APBANKSEL = reg[7:4] */

    if (sel == g_select) {
        return SWD_OK;
    }
    swd_err_t err = swd_dp_write(SWD_DP_SELECT, sel);
    g_select = err == SWD_OK ? sel : SELECT_UNKNOWN;
    return err;
}

swd_err_t swd_ap_read(uint8_t reg, uint32_t *value) {
    uint32_t  posted;
    swd_err_t err = select_bank(reg);

    if (err == SWD_OK) {
        err = transfer(1, 1, reg & 0x0Cu, &posted);
    }
    if (err == SWD_OK) {
        err = swd_dp_read(SWD_DP_RDBUFF, value);
    }
    return err;
}

swd_err_t swd_ap_write(uint8_t reg, uint32_t value) {
    swd_err_t err = select_bank(reg);

    if (err == SWD_OK) {
        err = transfer(1, 0, reg & 0x0Cu, &value);
    }
    return err;
}

static swd_err_t mem_setup(uint32_t addr) {
    swd_err_t err = SWD_OK;

    if (!g_csw_set) {
        err       = swd_ap_write(SWD_AP_CSW, CSW_32BIT_INC);
        g_csw_set = err == SWD_OK;
    }
    if (err == SWD_OK) {
        err = swd_ap_write(SWD_AP_TAR, addr);
    }
    return err;
}

swd_err_t swd_mem_read32(uint32_t addr, uint32_t *value) {
    swd_err_t err = mem_setup(addr);

    if (err == SWD_OK) {
        err = swd_ap_read(SWD_AP_DRW, value);
    }
    return err;
}

swd_err_t swd_mem_read_block(uint32_t addr, uint32_t *words, uint16_t count) {
    while (count > 0) {
        uint16_t  room = (uint16_t)((0x400u - (addr & 0x3FFu)) / 4u);
        uint16_t  n    = count < room ? count : room;
        uint32_t  posted;
        swd_err_t err = mem_setup(addr);

        /* Posted reads: each DRW read returns the previous one's word, the
         * last comes from RDBUFF. */
        if (err == SWD_OK) {
            err = transfer(1, 1, SWD_AP_DRW, &posted);
        }
        for (uint16_t i = 1; err == SWD_OK && i < n; i++) {
            err = transfer(1, 1, SWD_AP_DRW, &words[i - 1]);
        }
        if (err == SWD_OK) {
            err = swd_dp_read(SWD_DP_RDBUFF, &words[n - 1]);
        }
        if (err != SWD_OK) {
            return err;
        }
        addr  += 4u * n;
        words += n;
        count  = (uint16_t)(count - n);
    }
    return SWD_OK;
}

swd_err_t swd_mem_write32(uint32_t addr, uint32_t value) {
    swd_err_t err = mem_setup(addr);

    if (err == SWD_OK) {
        err = swd_ap_write(SWD_AP_DRW, value);
    }
    return err;
}

swd_err_t swd_mem_write_block(uint32_t addr, const uint32_t *words, uint16_t count) {
    while (count > 0) {
        uint16_t  room = (uint16_t)((0x400u - (addr & 0x3FFu)) / 4u);
        uint16_t  n    = count < room ? count : room;
        swd_err_t err  = mem_setup(addr);

        for (uint16_t i = 0; err == SWD_OK && i < n; i++) {
            uint32_t w = words[i];
            err = transfer(1, 0, SWD_AP_DRW, &w);
        }
        if (err != SWD_OK) {
            return err;
        }
        addr  += 4u * n;
        words += n;
        count  = (uint16_t)(count - n);
    }
    return SWD_OK;
}

#endif /* SECONDARY_MCU_IMAGE */
