/* Update of an own secondary-MCU firmware from the image this firmware carries
 * (secondary_mcu_image.c; AVATTO ZDMS16-2 with the own PY32 firmware).
 *
 * The MCU tells its firmware version at first contact (0x0B). If it runs an
 * own firmware (major version >= 2) other than the one carried here, it is
 * offered the carried one (0xA0); it restarts into its bootloader, which asks
 * for the image block by block (0xA1, answered from flash, stateless) and
 * reports the result (0xA2). A bootloader without a valid app (version 0.0.0,
 * e.g. after a power cut during an update) asks by itself.
 * The stock MCU (1.x) is never offered anything: it has no such bootloader. */
#ifndef TUYA_MCU_OTA_H
#define TUYA_MCU_OTA_H

#include <stdint.h>

#define TUYA_MCU_CMD_OTA_NOTIFY    0xA0   /* module -> MCU: version size crc */
#define TUYA_MCU_CMD_OTA_BLOCK     0xA1   /* MCU -> module: offset max_len; answer: block */
#define TUYA_MCU_CMD_OTA_RESULT    0xA2   /* MCU -> module: status version */

/** The MCU reported its firmware version (Tuya packed byte). */
void tuya_mcu_ota_on_version(uint8_t packed);

/** An MCU command; returns 1 if it was an update command (handled here). */
uint8_t tuya_mcu_ota_on_command(uint8_t cmd, uint16_t seq, const uint8_t *data,
                                uint16_t len);

#endif
