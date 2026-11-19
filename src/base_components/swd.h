#ifndef _SWD_H_
#define _SWD_H_

#include <stdint.h>

#include "hal/gpio.h"

/* ARM Serial Wire Debug host (ADIv5 SW-DP, AHB-AP 0), bit-banged over the pin
 * layer of src/hal/swd_io.h. Platform-neutral: the stub runs it against an
 * edge-level target model (src/stub/hal/swd_target.c).
 *
 * Every call is short and bounded (no waiting on the target beyond the WAIT
 * retries), so a caller can slice longer work across main-loop passes. */

typedef enum {
    SWD_OK = 0,
    SWD_ERR_NO_TARGET, /* no ACK (the line floats high): no wire, no power, lockout */
    SWD_ERR_PROTOCOL,  /* an ACK that is none of OK / WAIT / FAULT */
    SWD_ERR_WAIT,      /* still WAIT after SWD_WAIT_RETRIES (DAPABORT sent) */
    SWD_ERR_FAULT,     /* FAULT: a sticky error (cleared again by swd_clear_errors) */
    SWD_ERR_PARITY,    /* read data with a bad parity bit */
    SWD_ERR_POWER,     /* the debug power-up was not acknowledged */
    SWD_ERR_PINS,      /* SWCLK held low by someone else: nothing driven */
} swd_err_t;

#define SWD_WAIT_RETRIES       100
#define SWD_POWER_POLLS        100

/* DP registers (A[3:2]) */
#define SWD_DP_DPIDR           0x0  /* read */
#define SWD_DP_ABORT           0x0  /* write */
#define SWD_DP_CTRL_STAT       0x4
#define SWD_DP_SELECT          0x8  /* write */
#define SWD_DP_RDBUFF          0xC  /* read */

/* AHB-AP registers (bank << 4 | A[3:2]) */
#define SWD_AP_CSW             0x00
#define SWD_AP_TAR             0x04
#define SWD_AP_DRW             0x0C
#define SWD_AP_IDR             0xFC

/* Clears STKCMP, STKERR, WDERR, ORUNERR. */
#define SWD_ABORT_CLEAR_ALL    0x1Eu

/* Check that nobody holds SWCLK low, then take the pins (hal_swd_take).
 * SWD_ERR_PINS: nothing was driven, the pins are released. */
swd_err_t swd_begin(hal_gpio_pin_t clk, hal_gpio_pin_t dio);

/* Release the pins (hal_swd_release). */
void swd_end(void);

/* Line reset, JTAG-to-SWD, line reset, idle, then DPIDR (the first transfer
 * after a line reset must read it). */
swd_err_t swd_connect(uint32_t *dpidr);

swd_err_t swd_dp_read(uint8_t addr, uint32_t *value);
swd_err_t swd_dp_write(uint8_t addr, uint32_t value);

/* ABORT: clear the sticky error flags. */
swd_err_t swd_clear_errors(void);

/* Clear sticky errors and power the debug domain up (CTRL/STAT CSYSPWRUPREQ +
 * CDBGPWRUPREQ, polled for both ACKs). The PY32F002A answers every AP access
 * with FAULT until this is done (measured with a probe). */
swd_err_t swd_power_up(void);

/* Power-down request (CTRL/STAT = 0), for the end of a session. */
swd_err_t swd_power_down(void);

/* AP 0 register access; reg = bank << 4 | A[3:2] (SWD_AP_*). Reads are
 * completed through RDBUFF. */
swd_err_t swd_ap_read(uint8_t reg, uint32_t *value);
swd_err_t swd_ap_write(uint8_t reg, uint32_t value);

/* Memory access through AP 0 with 32-bit transfers (CSW set on first use). */
swd_err_t swd_mem_read32(uint32_t addr, uint32_t *value);

/* Consecutive words from addr (word-aligned); TAR is written again at every
 * 1 KB boundary (AHB-AP auto-increment is only guaranteed inside one). */
swd_err_t swd_mem_read_block(uint32_t addr, uint32_t *words, uint16_t count);

#endif
