/* Update of an own secondary-MCU firmware from the image this firmware carries
 * (secondary_mcu_image.c; AVATTO ZDMS16-2 with the own PY32 firmware).
 *
 * An MCU that reports firmware 2.x or later is asked who it is (0xA3). Only
 * our own firmware answers ("ZDMS", kind, version); a stock MCU does not,
 * whatever version number it has, so it is never offered anything (1.x is
 * not even asked). Our firmware with a version other than the carried one is
 * offered it (0xA0); it restarts into its bootloader, which asks for the
 * image block by block (0xA1, answered from flash, stateless) and reports the
 * result (0xA2). A bootloader without a
 * valid app (e.g. after a power cut during an update) asks by itself. */
#ifndef TUYA_MCU_OTA_H
#define TUYA_MCU_OTA_H

#include <stdint.h>

#define TUYA_MCU_CMD_OTA_NOTIFY    0xA0   /* module -> MCU: version size crc */
#define TUYA_MCU_CMD_OTA_BLOCK     0xA1   /* MCU -> module: offset max_len; answer: block */
#define TUYA_MCU_CMD_OTA_RESULT    0xA2   /* MCU -> module: status version */
#define TUYA_MCU_CMD_OTA_IDENT     0xA3   /* module -> MCU: who are you? */

/** The MCU reported this version byte. Returns 1 if it is asked who it is
 *  first: the version is then shown (tuya_mcu_app_show_mcu_version) once it
 *  has answered, or after a timeout, so ours always shows as "x.y.z own". */
uint8_t tuya_mcu_ota_on_version(uint8_t packed);

/** An MCU command; returns 1 if it was an update command (handled here). */
uint8_t tuya_mcu_ota_on_command(uint8_t cmd, uint16_t seq, const uint8_t *data,
                                uint16_t len);

#endif
