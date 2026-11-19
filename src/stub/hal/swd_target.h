#ifndef _STUB_SWD_TARGET_H_
#define _STUB_SWD_TARGET_H_

#include <stdint.h>

/* Edge-level model of the dimmer MCU (PY32F002A, Cortex-M0+) as seen through
 * its debug port, for the stub build: the two wires (SWCLK, SWDIO), the SW-DP
 * (ADIv5, SWD protocol v1, DPv1 MINDP, DPIDR 0x0BC11477) and AHB-AP 0 with the
 * memory map of an Open Dimmer unit, its core debug, flash controller, RCC and
 * GPIOA. The stub pin layer (swd_io.c) feeds it every SWCLK / SWDIO change;
 * tests steer it with the REPL `swd_target`.
 *
 * Wire rules:
 * - The target acts only on rising SWCLK edges: it samples SWDIO there, and
 *   when it drives SWDIO it changes its output right after the edge (so the
 *   host reads a bit while SWCLK is low, before the next rising edge).
 * - SWDIO = the host's level if the host drives, else the target's if the
 *   target drives, else 1 (pull-up). Both driving at once counts contention.
 * - Released, SWCLK reads low at the target (its pull-down): take/release never
 *   make a rising edge.
 * - Not connected: the target sees no edge and never drives. The same once a
 *   GPIOA MODER write takes PA13 / PA14 (SWDIO / SWCLK) out of their AF mode
 *   (swd_lost), until a power cycle.
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
 *   A DRW access to the flash while the flash controller is busy answers WAIT
 *   (the AHB stalls).
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
 *   0x08000000-0x08004FFF flash, erased 0xFF (bus error under RDP 1); AP
 *                         writes go to the flash controller (below)
 *   0x20000000-0x20000BFF SRAM, 0 (a power cycle fills it with a pattern)
 *   0x1FFF0000-0x1FFF0DFF system ROM, byte i = (i * 37 + 11) & 0xFF but the
 *                         last 4 bytes e7 75 a5 e6: CRC-32 0xA52232EA as the
 *                         real chip's
 *   0x1FFF0E00-0x1FFF0E0F UID, bytes 0x10..0x1F
 *   0x1FFF0E80-0x1FFF0E8F option bytes (RDP 1 swaps bytes 0 and 2: 55 .. aa);
 *                         a power cycle loads OPTR, SDKR, WRPR and RDP from them
 *   0x1FFF0F00-0x1FFF0F7F factory config, 0xFFFFFFFF but the HSI_FS=4 timing
 *                         table at 0x1FFF0F6C
 *   (the ROM-like areas above ignore AP writes)
 *   0xE000ED00 CPUID 0x410CC601
 *   0xE000EDF0 DHCSR: writes need key 0xA05F; C_DEBUGEN b0, C_HALT b1 (only
 *              with C_DEBUGEN); reads add S_REGRDY b16 (always 1), S_HALT b17,
 *              S_LOCKUP b19, S_RESET_ST b25 (set by every reset, cleared by a
 *              read). C_DEBUGEN + C_HALT halts (also out of lockup); C_HALT 0
 *              (or C_DEBUGEN 0) resumes a halted core: running if xPSR.T, PC
 *              (bit 0 ignored) inside the flash and MSP inside the SRAM
 *              (0x20000000..0x20000C00), else lockup
 *   0xE000EDF4 DCRSR / 0xE000EDF8 DCRDR: core register transfer (REGWnR b16,
 *              REGSEL R0..R12, 13 = SP = MSP, 14 LR, 15 PC, 16 xPSR, 17 MSP,
 *              18 PSP, 20 CONTROL/PRIMASK) only while halted, else ignored
 *   0xE000EDFC DEMCR (bits 0 VC_CORERESET, 10, 24 r/w)
 *   0xE000ED0C AIRCR reads 0xFA050000; SYSRESETREQ with key 0x05FA is a
 *              system reset: the peripherals below back to reset (a busy flash
 *              operation finishes first); SRAM, DHCSR.C_DEBUGEN, DEMCR, DCRDR,
 *              DBGMCU kept. Then, with C_DEBUGEN and VC_CORERESET, the core
 *              is halted at the reset vector (MSP = flash word 0, PC = word 1
 *              without bit 0, xPSR.T = its bit 0); else it runs the firmware
 *   0x40015800 DBGMCU IDCODE 0x60001000 (RO), +4 CR, +8 APB_FZ1 (b12 IWDG
 *              stop), +C APB_FZ2: writes only with RCC_APBENR1.DBGEN
 *   0x40021004 RCC_ICSCR 0x0159950C (r/w)
 *   0x40021034 RCC_IOPENR (r/w, bits 0, 1, 5; GPIOAEN b0)
 *   0x4002103C RCC_APBENR1 (r/w, DBGEN b27)
 *   0x40022008 FLASH KEYR (write only): 0x45670123 then 0xCDEF89AB clears
 *              CR.LOCK; any other write while locked locks it out until a
 *              reset; ignored while unlocked
 *   0x4002200C FLASH OPTKEYR (write only): with CR.LOCK clear, 0x08192A3B
 *              then 0x4C5D6E7F clears CR.OPTLOCK; a wrong key keeps it until
 *              a reset; ignored while LOCK or with OPTLOCK clear
 *   0x40022010 FLASH SR: EOP b0, WRPERR b4, OPTVERR b15 (write 1 to clear),
 *              BSY b16
 *   0x40022014 FLASH CR (reset 0xC0000000; writes ignored while LOCK): PG b0,
 *              PER b1, SER b11, OPTSTRT b17, PGSTRT b19, EOPIE b24,
 *              OBL_LAUNCH b27, OPTLOCK b30 (set by writing 1, cleared only by
 *              OPTKEYR; while set OPTSTRT and OBL_LAUNCH are not taken), LOCK
 *              b31. OBL_LAUNCH written 1: an option byte load (below)
 *   0x40022020 OPTR 0x0000F9AA (RDP 1: 0x0000F955), +24 SDKR 0x1F, +2C WRPR
 *              0xFFFF (bit k 0 = 4 KB sector k protected): reads give the
 *              loaded values; with LOCK and OPTLOCK clear a write stages a
 *              value for OPTSTRT (staged = loaded after every load or system
 *              reset). Every write of an RDP byte 0xCC counts rdp2_written
 *   0x40022080 option program trigger (write only): a word write with
 *              CR.OPTSTRT set programs the option area (below)
 *   0x40022100-0x40022120 flash timing TS0..PRETPE (r/w, reset 0; timing_set
 *              once all nine were written since the last reset)
 *   0x50000000 GPIOA, bus error without RCC_IOPENR.GPIOAEN: MODER +0
 *              (0xEBFFFFFF), ODR +14, BSRR +18, BRR +28
 *   anything else: bus error.
 * Every DRW write the AP takes (bus error or not) is logged as (addr, value).
 * The backdoor (peek/poke/load) ignores RDP, locks and BSY and has no side
 * effects; poke can change read-only registers too (e.g. another IDCODE).
 *
 * Flash controller (a word write into the flash, unlocked, exactly one of
 * PG / PER / SER set; else bus error): PER erases the write's 128-byte page,
 * SER its 4 KB sector. PG: 32 word writes in order from the start of one page
 * fill a buffer; the 32nd, with CR.PGSTRT set, programs the page (new = old
 * AND buffer) and clears PGSTRT. A write out of order or into another page,
 * the 32nd without PGSTRT, an erase or program of a WRPR-protected sector, or
 * more than one of PG / PER / SER: WRPERR, nothing changes, no BSY / EOP.
 * Clearing CR.PG drops a partial buffer. An operation keeps SR.BSY for
 * `busy_reads` (default 2) AP reads of SR, then takes effect and sets EOP.
 *
 * Option bytes. Programming (CR.OPTSTRT, then a write to 0x40022080; OPTSTRT
 * clears): the option area gets bytes 0-1 OPTR[15:0], 2-3 its complement,
 * 4-5 SDKR[15:0], 6-7 complement, 8-11 0xFF, 12-13 WRPR[15:0], 14-15
 * complement, after SR.BSY for `busy_reads_ob` (default 5) reads of SR; then
 * EOP. A staged RDP byte 0xCC: WRPERR, nothing programmed. If the stored RDP
 * byte goes from not 0xAA to 0xAA, the whole main flash is erased to 0xFF at
 * that moment (the end of BSY; the real chip's timing is unknown). While BSY,
 * DRW accesses to the flash and the option area answer WAIT. The loaded
 * registers change only at a load: power-on, or OBL_LAUNCH = a system reset
 * that loads them and drops the SWD link (DP locked out until a line reset,
 * sticky flags cleared; power-up, SELECT, AP and debug registers kept). A load
 * checks each pair against its complement; a mismatch sets SR.OPTVERR and
 * loads OPTR 0x0000FFFF (RDP level 1, user byte 0xFF), SDKR 0x1F, WRPR 0xFFFF
 * for that pair. RDP level = loaded OPTR[7:0] != 0xAA.
 *
 * RDP level 1: flash reads / writes through the AP are bus errors; and while
 * the debug domain is powered (CDBGPWRUPACK) a running core loses its flash
 * and locks up (as an STM32 with a debugger attached); a halted core stays
 * halted (a resume then locks up).
 *
 * Firmware: the core runs it after a reset that no debugger holds; the model
 * only decides running or lockup from the vectors: MSP (word 0) 4-aligned in
 * 0x20000000..0x20000C00, the reset vector (word 1) odd, inside the flash,
 * pointing at a halfword that is not 0xFFFF. At the stub start the core is
 * running (the Open Dimmer app, not in the model's flash: PC 0x08001000, MSP
 * 0x20000C00, xPSR 0x01000000).
 *
 * Presets (the start, `reset`, `preset`): open_dimmer as above; stock = a
 * factory unit: the flash holds a stock image (bytes (i * 131 + 7) & 0xFF in
 * 0x08000000-0x08001FFF, MSP 0x20000C00, reset vector 0x080000C1), option
 * area 00 f0 ff 0f ff 00 00 ff ff ff ff ff ff ff 00 00 (OPTR 0x0000F000, RDP
 * level 1), core running it. With stock_pins_ms n (0 = never), a stock
 * firmware that has run without a halt or lockup for n ms of stub time
 * (hal_millis) since its power-up takes PA13 / PA14: SWCLK held low (the idle
 * check reads 0, host SWCLK drives count contention) and the DP never answers,
 * until a power cycle. The stock firmware is gone once the flash controller
 * erases or programs any of the flash (the backdoor does not count).
 *
 * Environment (read at the start, src/stub/main.c, before the app runs):
 * SWD_TARGET_PRESET=stock|open_dimmer (default open_dimmer),
 * SWD_TARGET_STOCK_PINS_MS=<ms>, SWD_TARGET_FLASH=<file> (raw, at 0x08000000;
 * the core then takes its vectors), SWD_TARGET_STATE=<file>: the whole target
 * (memories, option area and loaded registers, DP, core, the time since its
 * power-up, stats; not the logs) is loaded from it at the start, over the
 * preset, if it is a state of this build, and saved at the exit, on `q` and on
 * `swd_target save`. SWD_TARGET_STOCK_PINS_MS still applies over a loaded
 * state. SWD_TARGET_POWER_CYCLE=1: a power cycle at the start (after the load).
 * `reset` starts from the environment's preset again (not the state file).
 *
 * Power cycle (`power_cycle`, or `cut_after n`: the power goes after the nth
 * further SWD transfer that got an ACK): a busy flash operation leaves its
 * page / sector / option area as old AND 0xA5A5A5A5; SRAM gets a pattern
 * (word at offset o = 0xDEADBEEF ^ o); the DP is reset (CTRL/STAT 0, locked
 * out until a line reset), AP, debug registers, DBGMCU and peripherals reset,
 * option bytes loaded, then the firmware runs. */

/* The host side of the wire (src/stub/hal/swd_io.c). */
void    swd_target_host_clk(uint8_t driven, uint8_t level);
void    swd_target_host_dio(uint8_t driven, uint8_t level);
uint8_t swd_target_dio_level(void);
uint8_t swd_target_clk_idle_level(void);

/* A pin call while the host has not taken the pins (counted in untaken_ops). */
void    swd_target_note_untaken(void);

/* Test controls. reset = everything back to the stub start defaults (config,
 * DP, memory, core, stats, logs). clear = stats and logs only. */
void swd_target_reset(void);
void swd_target_clear(void);
void swd_target_power_cycle(void);
void swd_target_set_connect(uint8_t on);
void swd_target_set_clk_held_low(uint8_t on);
void swd_target_set_rdp(uint8_t level);
void swd_target_set_wait_ap(uint32_t count);
void swd_target_set_busy_reads(uint32_t reads);
void swd_target_set_busy_reads_ob(uint32_t reads);
void swd_target_set_stock_pins_ms(uint32_t ms);

/* reset with the given preset ("stock", "open_dimmer"); -1 = unknown */
int  swd_target_preset(const char *name);

/* Start from the environment now (src/stub/main.c, before the app starts). */
void swd_target_start(void);

/* Save to SWD_TARGET_STATE; -1 = not set or not written. */
int  swd_target_save(void);

/* 0 = no cut. */
void swd_target_set_cut_after(uint32_t transfers);

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
    uint32_t edges;           /* rising SWCLK edges the target saw */
    uint32_t erases;          /* page and sector erases started */
    uint32_t programs;        /* page programs started */
    uint32_t wrperr;          /* operations refused with WRPERR */
    uint32_t power_cycles;
    uint32_t rdp2_written;    /* writes of RDP 0xCC (OPTR, option area) */
    uint32_t obl_launches;
    uint32_t option_programs; /* option area programs started */
    uint32_t mass_erases;     /* RDP 1 -> 0 */
} swd_target_stats_t;

typedef struct {
    const char *core;         /* "running", "halted", "lockup" */
    uint32_t    pc;
    uint32_t    msp;
    uint32_t    xpsr;
    uint8_t     gates_low;    /* PA0 and PA1 outputs driving 0 */
    uint8_t     iwdg_frozen;  /* DBGMCU APB_FZ1 b12 */
    uint8_t     timing_set;   /* all nine flash timing registers written */
    uint8_t     flash_locked; /* FLASH CR.LOCK */
    uint8_t     key_lockout;  /* a wrong KEYR write: locked until a reset */
    uint8_t     flash_busy;   /* SR.BSY */
    uint8_t     swd_lost;     /* PA13 / PA14 taken out of SWD */
    const char *preset;       /* "stock", "open_dimmer" */
    uint8_t     rdp;          /* loaded RDP level: 0, 1 */
    uint8_t     optlock;      /* FLASH CR.OPTLOCK */
    uint8_t     optkey_lockout;
    uint8_t     stock_fw;     /* the flash still holds the stock firmware */
    uint8_t     pins_taken;   /* the stock firmware took PA13 / PA14 */
    uint32_t    stock_pins_ms;
    uint32_t    uptime_ms;    /* since the target's last power-up */
} swd_target_state_t;

void     swd_target_get_stats(swd_target_stats_t *out);
void     swd_target_get_state(swd_target_state_t *out);
uint8_t  swd_target_powered(void);   /* CTRL/STAT.CDBGPWRUPACK */
uint8_t  swd_target_lockout(void);   /* 1 = only a line reset is heard */
uint32_t swd_target_ctrl_stat(void);

/* Write log, oldest first; writes past its depth are only counted. */
uint32_t swd_target_write_count(void);
uint32_t swd_target_writes_dropped(void);
void     swd_target_write_at(uint32_t index, uint32_t *addr, uint32_t *value);

/* Flash operation log, oldest first: erase_page, erase_sector, program (page
 * address), wrperr (the address of the refused write or operation), garbage
 * (a power cut during an operation; its page / sector / option area),
 * program_options (0x1FFF0E80), mass_erase (0x08000000). */
uint32_t swd_target_flash_log_count(void);
uint32_t swd_target_flash_log_dropped(void);
void     swd_target_flash_log_at(uint32_t index, const char **op, uint32_t *addr);

#endif
