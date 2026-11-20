#ifndef _SECONDARY_MCU_BOOT_H_
#define _SECONDARY_MCU_BOOT_H_

#include <stdint.h>

/* The PY32 bootloader (sector 0) the SWD master writes with the job
 * update_bootloader (mcu_flasher.c). Generated from the open-dimmer build
 * (fw/tools/embed_ztu_boot.py): secondary_mcu_boot.c. CRC = zlib CRC-32 of the
 * 4 KB sector padded with 0xFF (what Open Dimmer reports as DP 136). */
extern const uint8_t  secondary_mcu_boot[];
extern const uint16_t secondary_mcu_boot_size;
extern const uint32_t secondary_mcu_boot_crc;
extern const uint8_t  secondary_mcu_boot_version;

#endif
