/* Firmware image of an own secondary MCU, carried by this firmware so it can
 * update the MCU (tuya_mcu_ota.c). Empty (size 0) unless the board is built
 * with SECONDARY_MCU_IMAGE. Generated content: secondary_mcu_image.c. */
#ifndef SECONDARY_MCU_IMAGE_H
#define SECONDARY_MCU_IMAGE_H

#include <stdint.h>

extern const uint8_t  secondary_mcu_image[];
extern const uint32_t secondary_mcu_image_size;

#endif
