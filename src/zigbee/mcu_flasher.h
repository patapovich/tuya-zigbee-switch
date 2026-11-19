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
 * Phase 1: read-only jobs (survey_dp, survey_full), started by one write of the
 * control attribute; the result is the status attribute. */

#define MCU_FLASHER_ATTRS              2 /* status + control, with the token */

#define MCU_FLASHER_JOB_NONE           0
#define MCU_FLASHER_JOB_SURVEY_DP      1
#define MCU_FLASHER_JOB_SURVEY_FULL    2
#define MCU_FLASHER_JOB_ABORT          0xFF

enum {
    MCU_FLASHER_STATE_IDLE = 0,
    MCU_FLASHER_STATE_RUNNING,
    MCU_FLASHER_STATE_DONE,
    MCU_FLASHER_STATE_FAILED,
    MCU_FLASHER_STATE_ABORTED,
};

enum {
    MCU_FLASHER_ERR_NONE = 0,
    MCU_FLASHER_ERR_PINS_DRIVEN,   /* SWCLK held low: nothing driven */
    MCU_FLASHER_ERR_NO_TARGET,     /* no DP answer */
    MCU_FLASHER_ERR_WRONG_TARGET,  /* an identity value is not the PY32F002A's */
    MCU_FLASHER_ERR_SWD,           /* a transfer failed: see swd_err */
    MCU_FLASHER_ERR_BUSY,          /* a job is running */
    MCU_FLASHER_ERR_BAD_JOB,       /* unknown job */
};

/* Status attribute (Basic 0xff50, octet string), little-endian. */
typedef struct __attribute__((packed)) {
    uint8_t  len;          /* ZCL octet string length byte */
    uint8_t  format;       /* 1 */
    uint8_t  state;        /* MCU_FLASHER_STATE_* */
    uint8_t  job;          /* MCU_FLASHER_JOB_* */
    uint8_t  step;         /* the job's step reached (see mcu_flasher.c) */
    uint8_t  progress;     /* % */
    uint8_t  error;        /* MCU_FLASHER_ERR_* */
    uint8_t  swd_err;      /* swd_err_t of the failed transfer */
    uint8_t  rdp;          /* 0xAA level 0, 0xCC level 2, else level 1; 0 = not read */
    uint32_t dpidr;
    uint32_t ap_idr;
    uint32_t cpuid;
    uint32_t idcode;       /* DBGMCU IDCODE */
    uint32_t optr;         /* FLASH_OPTR */
    uint32_t sdkr;
    uint32_t wrpr;
    uint32_t option_crc;   /* CRC-32 of the stored option bytes 0x1FFF0E80, 16 B */
    uint32_t uid_crc;      /* CRC-32 of the UID 0x1FFF0E00, 16 B */
    uint32_t rom_crc;      /* CRC-32 of the system ROM 0x1FFF0000..0x1FFF0DFF */
    uint32_t sector0_crc;  /* CRC-32 of 0x08000000..0x08000FFF (RDP 0 only) */
    uint32_t boot_id;      /* boot_id magic word at 0x08000010 */
    uint32_t boot_info;    /* the next word: version, features, reserved */
    uint32_t app_magic;    /* app header at 0x080010C0: magic */
    uint32_t app_size;
    uint32_t app_crc;
    uint32_t app_info;     /* version, flags, build */
    uint16_t started_ms;   /* ZTU uptime (ms, saturated) when the job started */
    uint16_t duration_ms;  /* how long it ran (saturated) */
} mcu_flasher_status_t;

/* config_parser.c: an SW<clk><dio> token. */
void mcu_flasher_parse_token(const char *entry);

/* basic_cluster.c: the two attributes, only with a valid token (else 0). */
uint8_t mcu_flasher_register_attrs(hal_zigbee_attribute *table, uint8_t start_index);

/* basic_cluster.c: an attribute write. */
void mcu_flasher_on_write(uint16_t attribute_id);

/* app.c: one slice of a running job per main-loop pass. */
void mcu_flasher_task(void);

#endif
