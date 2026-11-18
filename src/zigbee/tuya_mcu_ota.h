/* Update of an own secondary-MCU firmware from the image this firmware carries
 * (secondary_mcu_image.c; AVATTO ZDMS16-2 with the own PY32 firmware).
 *
 * An MCU that reports firmware 2.x or later is asked who it is (0xA3; 4 times
 * in 7.5 s, then at its next 2 network queries if all were lost). Only
 * our own firmware answers ("ZDMS", kind, version, update protocol); a stock
 * MCU does not, whatever version number it has, so it is never offered
 * anything (1.x is not even asked). Our firmware with an older version than
 * the carried one and the same update protocol is offered it (0xA0); it
 * restarts into its bootloader, which asks for the image block by block
 * (0xA1, answered from flash, stateless) and reports the result (0xA2). A
 * bootloader without a valid app (e.g. after a power cut during an update)
 * asks by itself. After 3 failed results in a row nothing is served for
 * 15 min, then for twice as long after every further failure (up to a day). */
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

/** 1 if an MCU that reports this version byte may run our own firmware (it
 *  is asked who it is): 2.x or later, and this firmware carries an image. */
uint8_t tuya_mcu_ota_may_be_own(uint8_t packed);

/** The MCU restarts: a pending identity question is dropped (the next first
 *  contact asks again). */
void tuya_mcu_ota_mcu_restarted(void);

/** The MCU's network query that is no first contact, with no version
 *  question out: an MCU whose identity questions all ran out is asked again
 *  (twice at most, until its next version answer). */
void tuya_mcu_ota_mcu_queried(void);

/** An MCU command; returns 1 if it was an update command (handled here). */
uint8_t tuya_mcu_ota_on_command(uint8_t cmd, uint16_t seq, const uint8_t *data,
                                uint16_t len);

#endif
