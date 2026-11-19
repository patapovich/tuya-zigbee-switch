#ifndef _STUB_SWD_TARGET_H_
#define _STUB_SWD_TARGET_H_

#include <stdint.h>

/* Edge-level model of the dimmer MCU (PY32F002A, Cortex-M0+) as seen through
 * its debug port, for the stub build: the two wires (SWCLK, SWDIO), the SW-DP
 * (ADIv5, SWD protocol v1, DPv1 MINDP, DPIDR 0x0BC11477) and AHB-AP 0 with the
 * memory map of an Open Dimmer unit. The stub pin layer (swd_io.c) feeds it
 * every SWCLK / SWDIO change; tests steer it with the REPL `swd_target`.
 *
 * Wire rules:
 * - The target acts only on rising SWCLK edges: it samples SWDIO there, and
 *   when it drives SWDIO it changes its output right after the edge (so the
 *   host reads a bit while SWCLK is low, before the next rising edge).
 * - SWDIO = the host's level if the host drives, else the target's if the
 *   target drives, else 1 (pull-up). Both driving at once counts contention.
 * - Released, SWCLK reads low at the target (its pull-down): take/release never
 *   make a rising edge.
 * - Not connected: the target sees no edge and never drives.
 * - SWCLK held low (the target firmware took the pin): the idle check reads 0,
 *   every host SWCLK drive counts contention, the target sees no edge.
 *
 * SW-DP:
 * - Line reset: 50 rising edges in a row with SWDIO 1 that the target did not
 *   drive. A request needs at least one 0 (idle) after it. The first transfer
 *   after a line reset must read DPIDR; anything else gets no ACK and locks the
 *   DP out until the next line reset, as a protocol error (bad parity, stop 1,
 *   park 0) does. Power-on also leaves the DP locked out until a line reset.
 * - The JTAG-to-SWD sequence (16 bits, 0xE79E LSB first, right after a line
 *   reset) is only bits between two line resets; it is counted in
 *   jtag_to_swd, not as a protocol error. A run of ones that ends in a line
 *   reset is not counted as a protocol error either.
 * - ACK OK / WAIT / FAULT, 1-cycle turnarounds, read data + even parity, write
 *   data + even parity (bad parity: WDATAERR, the write is dropped).
 * - DPIDR reads, CTRL/STAT reads and ABORT writes always answer OK. Any other
 *   access answers FAULT while a sticky flag (STICKYERR, WDATAERR, STICKYORUN,
 *   STICKYCMP) is set. An AP access with CDBGPWRUPACK 0 answers FAULT and sets
 *   STICKYERR (measured on the real chip). `wait_ap n`: the next n AP accesses
 *   (sticky and power checks passed) answer WAIT; DAPABORT cancels the rest.
 * - CTRL/STAT: the power-up / debug-reset ACKs mirror their REQs at once.
 *   ORUNDETECT, TRNMODE, TRNCNT, MASKLANE are RAZ/WI (MINDP, overrun detection
 *   not modelled). SELECT.CTRLSEL 1 maps A=0x4 to WCR (stored, no effect).
 * - A=0x8 read = RESEND (last AP/RDBUFF read data), A=0xC write ignored.
 *
 * AHB-AP 0 (other APSELs read 0, writes ignored): CSW (reset 0x03000040, Size
 * byte: a host must set Size 32), TAR, DRW, IDR 0x04770031 (bank 0xF, 0xFC).
 * AP reads are posted: they return the previous AP read result (RDBUFF); the
 * access itself is done at once. AddrInc single: TAR += 4 inside its 1 KB
 * block after a DRW access without an error. A DRW access that is not 32-bit
 * and aligned, or hits an unmapped address (or the flash under RDP 1), is a
 * bus error: the transfer ACKs OK, STICKYERR is set.
 *
 * Memory map (little-endian), defaults = an Open Dimmer unit:
 *   0x08000000-0x08004FFF flash, erased 0xFF (bus error under RDP 1; AP
 *                         writes are logged and dropped: no programming yet)
 *   0x20000000-0x20000BFF SRAM, 0
 *   0x1FFF0000-0x1FFF0DFF system ROM, byte i = (i * 37 + 11) & 0xFF
 *   0x1FFF0E00-0x1FFF0E0F UID, bytes 0x10..0x1F
 *   0x1FFF0E80-0x1FFF0E8F option bytes (RDP 1 swaps bytes 0 and 2: 55 .. aa)
 *   0x1FFF0F00-0x1FFF0F7F factory config, 0xFFFFFFFF but the HSI_FS=4 timing
 *                         table at 0x1FFF0F6C
 *   (the ROM-like areas above ignore AP writes)
 *   0xE000ED00 CPUID 0x410CC601
 *   0xE000EDF0 DHCSR: writes need key 0xA05F; keeps C_DEBUGEN, C_HALT (C_HALT
 *              only with C_DEBUGEN); reads add S_REGRDY, S_HALT = C_HALT and
 *              S_RESET_ST (set by power-on / system reset, cleared by a read)
 *   0xE000EDFC DEMCR (bits 0, 10, 24 r/w)
 *   0xE000ED0C AIRCR reads 0xFA050000; SYSRESETREQ with key 0x05FA is a
 *              system reset: FLASH SR/CR and RCC_ICSCR back to reset values,
 *              halted if DEMCR.VC_CORERESET and C_DEBUGEN, else C_HALT kept
 *   0x40015800 DBGMCU IDCODE 0x60001000, +4 CR, +8 APB_FZ1, +C APB_FZ2 (r/w)
 *   0x40022010 FLASH SR 0, +14 CR 0xC0000000, +20 OPTR 0x0000F9AA (RDP 1:
 *              0x0000F955), +24 SDKR 0x1F, +2C WRPR 0xFFFF: locked, so AP
 *              writes are logged and change nothing
 *   0x40021004 RCC_ICSCR 0x0159950C (r/w)
 *   anything else: bus error.
 * Every DRW write the AP takes (bus error or not) is logged as (addr, value).
 * The backdoor (peek/poke/load) ignores RDP and has no side effects; poke can
 * change read-only registers too (e.g. another IDCODE). */

/* The host side of the wire (src/stub/hal/swd_io.c). */
void    swd_target_host_clk(uint8_t driven, uint8_t level);
void    swd_target_host_dio(uint8_t driven, uint8_t level);
uint8_t swd_target_dio_level(void);
uint8_t swd_target_clk_idle_level(void);

/* A pin call while the host has not taken the pins (counted in untaken_ops). */
void    swd_target_note_untaken(void);

/* Test controls. reset = everything back to the stub start defaults (config,
 * DP, memory, stats, write log). clear = stats and write log only. */
void swd_target_reset(void);
void swd_target_clear(void);
void swd_target_set_connect(uint8_t on);
void swd_target_set_clk_held_low(uint8_t on);
void swd_target_set_rdp(uint8_t level);
void swd_target_set_wait_ap(uint32_t count);

/* Backdoor, 0 = done, -1 = not mapped (load: not inside one memory area). */
int swd_target_peek32(uint32_t addr, uint32_t *value);
int swd_target_poke32(uint32_t addr, uint32_t value);
int swd_target_load(uint32_t addr, const uint8_t *data, uint32_t len);

typedef struct {
    uint32_t line_resets;
    uint32_t dpidr_reads;
    uint32_t transfers_ok;
    uint32_t waits;
    uint32_t faults;
    uint32_t protocol_errors;
    uint32_t parity_errors; /* request and write data parity */
    uint32_t contention;
    uint32_t mem_writes;
    uint32_t bus_errors;
    uint32_t jtag_to_swd;
    uint32_t sys_resets;
    uint32_t untaken_ops;
    uint32_t edges;         /* rising SWCLK edges the target saw */
} swd_target_stats_t;

void     swd_target_get_stats(swd_target_stats_t *out);
uint8_t  swd_target_powered(void);   /* CTRL/STAT.CDBGPWRUPACK */
uint8_t  swd_target_lockout(void);   /* 1 = only a line reset is heard */
uint32_t swd_target_ctrl_stat(void);

/* Write log, oldest first; writes past its depth are only counted. */
uint32_t swd_target_write_count(void);
uint32_t swd_target_writes_dropped(void);
void     swd_target_write_at(uint32_t index, uint32_t *addr, uint32_t *value);

#endif
