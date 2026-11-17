#include "zigbee/tuya_mcu_ota.h"
#include "hal/printf_selector.h"
#include "zigbee/secondary_mcu_image.h"
#include "zigbee/tuya_mcu_app.h"
#include "zigbee/tuya_secondary_mcu.h"

/* Header of the carried image, right after its vector table (the MCU side
* defines it in ota_proto.h): magic, size, crc, version, little-endian. */
#define IMAGE_HEADER_OFFSET    0xC0
#define IMAGE_MAGIC            0x5A445041u
#define IMAGE_BOOTLOADER       0x00   /* version answered by the MCU's bootloader */
#define OWN_FIRMWARE_MAJOR     2
#define BLOCK_HEADER_LEN       13     /* offset size crc version */
#define BLOCK_MAX              64     /* bytes per answer: one 86 byte frame */

typedef struct {
    uint32_t size;
    uint32_t crc;
    uint8_t  version;
} image_info_t;

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static uint32_t be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) |
           (uint32_t)p[3];
}

static void put_be32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static int image_info(image_info_t *info) {
    if (secondary_mcu_image_size < IMAGE_HEADER_OFFSET + 16) {
        return 0;
    }
    const uint8_t *h = secondary_mcu_image + IMAGE_HEADER_OFFSET;
    if (le32(h) != IMAGE_MAGIC || le32(h + 4) != secondary_mcu_image_size) {
        return 0;
    }
    info->size    = le32(h + 4);
    info->crc     = le32(h + 8);
    info->version = h[12];
    return 1;
}

void tuya_mcu_ota_on_version(uint8_t packed) {
    image_info_t img;

    if (!image_info(&img)) {
        return;
    }
    if (packed == IMAGE_BOOTLOADER) {
        printf("Secondary MCU is in its bootloader: it fetches the carried firmware\r\n");
        return;
    }
    if ((packed >> 6) < OWN_FIRMWARE_MAJOR || packed == img.version) {
        return;     /* stock MCU (never touched), or already this firmware */
    }
    uint8_t d[9];
    d[0] = img.version;
    put_be32(&d[1], img.size);
    put_be32(&d[5], img.crc);
    printf("Secondary MCU firmware 0x%02x, carried 0x%02x: offering the update\r\n",
           packed, img.version);
    tuya_secondary_mcu_queue_cmd(TUYA_MCU_CMD_OTA_NOTIFY, d, sizeof(d));
}

static void answer_block(uint16_t seq, const uint8_t *data, uint16_t len) {
    image_info_t img;

    if (len < 6 || !image_info(&img)) {
        return;
    }
    uint32_t off  = be32(data);
    uint16_t want = (uint16_t)((data[4] << 8) | data[5]);
    uint16_t n    = 0;

    if (off < img.size) {
        uint32_t left = img.size - off;
        n = want < BLOCK_MAX ? want : BLOCK_MAX;
        if (n > left) {
            n = (uint16_t)left;
        }
    }
    uint8_t head[BLOCK_HEADER_LEN];
    put_be32(&head[0], off);
    put_be32(&head[4], img.size);
    put_be32(&head[8], img.crc);
    head[12] = img.version;
    tuya_secondary_mcu_send_long_reply(TUYA_MCU_CMD_OTA_BLOCK, seq, head, sizeof(head),
                                       n ? secondary_mcu_image + off : NULL, n);
}

uint8_t tuya_mcu_ota_on_command(uint8_t cmd, uint16_t seq, const uint8_t *data,
                                uint16_t len) {
    switch (cmd) {
    case TUYA_MCU_CMD_OTA_NOTIFY:
        printf("Secondary MCU %s the update\r\n",
               (len >= 1 && data[0] == 0) ? "accepted" : "declined");
        return 1;

    case TUYA_MCU_CMD_OTA_BLOCK:
        answer_block(seq, data, len);
        return 1;

    case TUYA_MCU_CMD_OTA_RESULT:
        if (len >= 1 && data[0] == 0) {
            printf("Secondary MCU updated to 0x%02x\r\n", len >= 2 ? data[1] : 0);

            /* It restarts now: treat its next query as a first contact, so
             * the version and the settings dump are read again. */
            tuya_mcu_app_mcu_restarted();
        } else {
            printf("Secondary MCU update failed (%u), it asks again\r\n",
                   len >= 1 ? data[0] : 0xFF);
        }
        return 1;

    default:
        return 0;
    }
}
