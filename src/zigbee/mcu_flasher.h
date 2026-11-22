#ifndef _MCU_FLASHER_H_
#define _MCU_FLASHER_H_

#include <stdint.h>

#include "hal/zigbee.h"

/* SWD master for the secondary MCU (the AVATTO ZDMS16-2's PY32F002A), over two
 * wires from free module pads to the MCU's SWD pads. Only in builds with
 * SECONDARY_MCU_IMAGE, and dormant there unless the config names the pins
 * with the SW<clk><dio> token (e.g. SWD3D7): without it no pin is touched, no
 * attribute is added, and nothing else changes.
 *
 * Jobs: the read-only surveys (survey_dp, survey_full) start with one write of
 * the control attribute; update_bootloader (and _force) writes the carried
 * bootloader (secondary_mcu_boot.c) into sector 0; install takes a factory MCU
 * to Open Dimmer, whatever its firmware: a read-protected one (the protection
 * change erases its flash) or one that left its flash open (level 0 without
 * Open Dimmer's bootloader or app: its pages erased by the install, unless a
 * sector is write-protected), then option bytes as Open Dimmer has them and
 * the bootloader, which fetches the app over the UART. Open Dimmer is never
 * taken for a factory MCU. The chip is identified by its debug port, AP,
 * CPUID and IDCODE; the ROM's CRC is told, not checked. The writing jobs need the confirm attribute set to the
 * job's name first (one use, 60 s). An install that cannot attach at once
 * (the factory firmware may hold the SWD pins) is armed in NV and attaches
 * early at the next power-ups (mcu_flasher_early). The result is the status
 * attribute.
 *
 * dump (debug builds only, make MCU_DUMP=1) reads an MCU at read protection level 0 (a factory one that left its
 * flash open, or Open Dimmer) without writing it: attached at once, or (the
 * factory firmware holds the pins) armed for the next power-up like an
 * install; the core is halted, the CRC-32 of the 32 KB flash goes to
 * sector0_crc and the job then serves reads: a write of the dump address
 * attribute fills the dump data attribute with the 44 bytes there (flash
 * 0x08000000..0x08007FFF, system memory 0x1FFF0000..0x1FFF0FFF). The abort
 * job, or 10 minutes without a read, ends it: the MCU is reset with the
 * debugger's hand off, so its own firmware starts again. A read-protected
 * MCU is refused: reading its protection has already closed its flash to the
 * running core, so it is halted with the gate outputs low until the module's
 * next power cycle; nothing is written. */

#ifdef MCU_FLASHER_DUMP                   /* debug builds (make MCU_DUMP=1) */
#define MCU_FLASHER_ATTRS               5 /* status, control, confirm, dump address, dump data */
#else
#define MCU_FLASHER_ATTRS               3 /* status, control, confirm */
#endif

#define MCU_FLASHER_JOB_NONE            0
#define MCU_FLASHER_JOB_SURVEY_DP       1
#define MCU_FLASHER_JOB_SURVEY_FULL     2
#define MCU_FLASHER_JOB_UPDATE_BOOT     3
#define MCU_FLASHER_JOB_UPDATE_FORCE    4
#define MCU_FLASHER_JOB_INSTALL         5
#define MCU_FLASHER_JOB_DUMP            6
#define MCU_FLASHER_JOB_ABORT           0xFF

enum {
    MCU_FLASHER_STATE_IDLE = 0,
    MCU_FLASHER_STATE_RUNNING,
    MCU_FLASHER_STATE_DONE,
    MCU_FLASHER_STATE_FAILED,
    MCU_FLASHER_STATE_ABORTED,
    MCU_FLASHER_STATE_ARMED,       /* install: waits for the next power-up (power-cycle the module) */
};

enum {
    MCU_FLASHER_ERR_NONE = 0,
    MCU_FLASHER_ERR_PINS_DRIVEN,   /* SWCLK held low: nothing driven */
    MCU_FLASHER_ERR_NO_TARGET,     /* no DP answer */
    MCU_FLASHER_ERR_WRONG_TARGET,  /* an identity value is not the PY32F002A's */
    MCU_FLASHER_ERR_SWD,           /* a transfer failed: see swd_err */
    MCU_FLASHER_ERR_BUSY,          /* a job is running */
    MCU_FLASHER_ERR_BAD_JOB,       /* unknown job */
    MCU_FLASHER_ERR_NOT_CONFIRMED, /* a writing job without its confirm text */
    MCU_FLASHER_ERR_PROTECTED,     /* RDP not 0, unexpected option bytes or sector 0 write-protected */
    MCU_FLASHER_ERR_HALT,          /* the core did not halt */
    MCU_FLASHER_ERR_FLASH,         /* unlock failed, write protection error or flash timeout */
    MCU_FLASHER_ERR_VERIFY,        /* sector 0 CRC wrong after writing it twice */
    MCU_FLASHER_ERR_WRONG_CLASS,   /* install: not a factory MCU (UART version not 1.x, or RDP 0) */
    MCU_FLASHER_ERR_OB_VERIFY,     /* install: the option bytes did not read back as written */
    MCU_FLASHER_ERR_NO_ATTACH,     /* install: no attach in its armed power-ups */
    MCU_FLASHER_ERR_RELOAD,        /* install: after the option reload not RDP 0 / not erased */
    MCU_FLASHER_ERR_KEPT,          /* install: one past its option bytes stays armed (an abort, a new install) */
};

/* Status attribute (Basic 0xff50, octet string), little-endian. At most 48
 * bytes after the length byte, as every octet string attribute of this
 * firmware (tuya_mcu_diag.c): a read response or report must fit one frame
 * (a bigger one is not sent by the stack, and format 1's 80 bytes left a
 * module mute until a power cycle). */
typedef struct __attribute__((packed)) {
    uint8_t  len;          /* ZCL octet string length byte */
    uint8_t  format;       /* 2 */
    uint8_t  state;        /* MCU_FLASHER_STATE_* */
    uint8_t  job;          /* MCU_FLASHER_JOB_* */
    uint8_t  step;         /* the job's step reached (see mcu_flasher.c) */
    uint8_t  error;        /* MCU_FLASHER_ERR_* */
    uint8_t  swd_err;      /* swd_err_t of the failed transfer */
    uint8_t  boot_version; /* boot_id version byte (0: no "ODBL" boot_id) */
    uint8_t  app_version;  /* app header version byte, Tuya packed (0: no "APDZ" header) */
    uint32_t dpidr;
    uint32_t ap_idr;
    uint32_t cpuid;
    uint32_t idcode;       /* DBGMCU IDCODE */
    uint32_t optr;         /* FLASH_OPTR (RDP level in the low byte) */
    uint16_t wrpr;         /* FLASH_WRPR */
    uint16_t app_build;    /* app header build */
    uint32_t option_crc;   /* CRC-32 of the stored option bytes 0x1FFF0E80, 16 B */
    uint32_t rom_crc;      /* CRC-32 of the system ROM 0x1FFF0000..0x1FFF0DFF */
    uint32_t sector0_crc;  /* CRC-32 of 0x08000000..0x08000FFF (RDP 0 only) */
    uint16_t duration_ms;  /* how long it ran (saturated) */
    uint8_t  page;         /* update_bootloader: the page being written (1..31, then 0) */
} mcu_flasher_status_t;

#define MCU_FLASHER_STATUS_MAX     48 /* bytes after the length byte */

/* Dump data attribute (Basic 0xff54, octet string): the address read and the
 * words there, little-endian. addr MCU_FLASHER_DUMP_NONE: no dump serving or
 * an address outside the two areas (or not word-aligned); MCU_FLASHER_DUMP_FAILED:
 * the read failed (the link is set up again for the next one). */
#define MCU_FLASHER_DUMP_WORDS     11
#define MCU_FLASHER_DUMP_NONE      0xFFFFFFFFu
#define MCU_FLASHER_DUMP_FAILED    0xFFFFFFFEu
typedef struct __attribute__((packed)) {
    uint8_t  len;          /* ZCL octet string length byte: 4 + 4 * the words read */
    uint32_t addr;
    uint8_t  data[4 * MCU_FLASHER_DUMP_WORDS];
} mcu_flasher_dump_t;

/* app.c, right after parse_config(): an armed install attaches here, some
 * 40-80 ms after power-up, before a factory firmware takes the SWD pins. */
void mcu_flasher_early(void);

/* config_parser.c: an SW<clk><dio> token. */
void mcu_flasher_parse_token(const char *entry);

/* basic_cluster.c: the attributes, only with a valid token (else 0). */
uint8_t mcu_flasher_register_attrs(hal_zigbee_attribute *table, uint8_t start_index);

/* basic_cluster.c: an attribute write. */
void mcu_flasher_on_write(uint16_t attribute_id);

/* app.c: one slice of a running job per main-loop pass. */
void mcu_flasher_task(void);

#endif
