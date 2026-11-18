#include "zigbee/tuya_mcu_ota.h"
#include "hal/printf_selector.h"
#include "hal/tasks.h"
#include "zigbee/secondary_mcu_image.h"
#include "zigbee/tuya_mcu_app.h"
#include "zigbee/tuya_mcu_diag.h"
#include "zigbee/tuya_secondary_mcu.h"

#ifdef SECONDARY_MCU_IMAGE

/* Header of the carried image, right after its vector table (the MCU side
* defines it in ota_proto.h): magic, size, crc, version, little-endian. */
#define IMAGE_HEADER_OFFSET     0xC0
#define IMAGE_MAGIC             0x5A445041u
#define IDENT_LEN               7     /* "ZDMS" kind version protocol */
#define IDENT_KIND_APP          1
#define IDENT_PROTOCOL          1
#define IDENT_ASK_FROM_MAJOR    2     /* stock ZDMS16-2 MCUs are 1.x */
#define IDENT_TIMEOUT_MS        500   /* first wait; doubles at every retry */
#define BLOCK_HEADER_LEN        13    /* offset size crc version */
#define BLOCK_MAX               64    /* bytes per answer: one 86 byte frame */

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

#define IMAGE_FLAG_SIM        0x01          /* bench build with a virtual zero cross */
#define IMAGE_FLAG_MEASURE    0x02          /* measurement build: outputs always off */
#define IMAGE_MAX             (12u * 1024u) /* app area behind the 4 KB bootloader */
#define FAILURES_MAX          3             /* failed results in a row before a pause */
#define PAUSE_FIRST_MIN       15            /* then served again; doubles at every */
#define PAUSE_MAX_MIN         (24u * 60u)   /* further failure, up to a day */
#define OFFERS_MAX            3             /* offers without a result before giving up */
#define IDENT_RETRIES         3             /* asked 4 times in 7.5 s before a bare version */
#define IDENT_LATE_MAX        2             /* then at the MCU's next 2 network queries */

static uint8_t  g_image_state;              /* 0 not checked, 1 good, 2 unusable */
static uint8_t  g_failures;                 /* failed update results since the last good one */
static uint16_t g_pause_min;                /* not served for this long after failures, 0: served */
static uint8_t  g_offers;                   /* offers since the last result */

static hal_task_t g_pause_timer;
static uint8_t    g_pause_timer_ready;

static uint32_t crc32_update(uint32_t crc, const uint8_t *p, uint32_t n) {
    crc = ~crc;
    while (n--) {
        crc ^= *p++;
        for (uint8_t k = 0; k < 8; k++) {
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
        }
    }
    return ~crc;
}

/* The carried image, checked once: header, size, CRC (with the CRC field read
 * as 0, as tools/mkimage.py computes it), never a SIM bench build or a
 * measurement build (they must never reach a lamp on mains by themselves). */
static int image_info(image_info_t *info) {
    if (g_image_state == 2 || secondary_mcu_image_size < IMAGE_HEADER_OFFSET + 16) {
        return 0;
    }
    const uint8_t *h = secondary_mcu_image + IMAGE_HEADER_OFFSET;
    info->size    = le32(h + 4);
    info->crc     = le32(h + 8);
    info->version = h[12];
    if (g_image_state == 0) {
        static const uint8_t zero[4] = { 0, 0, 0, 0 };
        uint32_t             at      = IMAGE_HEADER_OFFSET + 8;
        uint32_t             crc     = crc32_update(0, secondary_mcu_image, at);
        crc = crc32_update(crc, zero, 4);
        crc = crc32_update(crc, secondary_mcu_image + at + 4,
                           secondary_mcu_image_size - at - 4);
        g_image_state = (le32(h) == IMAGE_MAGIC && info->size == secondary_mcu_image_size &&
                         info->size <= IMAGE_MAX && crc == info->crc &&
                         !(h[13] & (IMAGE_FLAG_SIM | IMAGE_FLAG_MEASURE))) ? 1 : 2;
        if (g_image_state == 2) {
            printf("Carried MCU firmware unusable: not offered\r\n");
            return 0;
        }
    }
    return g_pause_min == 0;
}

static void diag_update(void) {
    tuya_mcu_diag.ota_failures  = g_failures;
    tuya_mcu_diag.ota_pause_min = g_pause_min;
}

static void pause_over(void *arg) {
    (void)arg;
    g_pause_min = 0;
    diag_update();
    printf("Serving the MCU firmware again after the pause\r\n");
}

/* Every failed result means the app area was already changed: the MCU has no
 * app until an update succeeds, and its bootloader keeps asking. Stopping for
 * good would leave the light dark until a power cycle; a pause that doubles
 * keeps a path that always fails from wearing the MCU's flash all day. */
static void result_failed(void) {
    if (g_failures < 0xFF) {
        g_failures++;
    }
    if (g_failures >= FAILURES_MAX) {
        uint32_t pause = PAUSE_FIRST_MIN;
        for (uint8_t k = FAILURES_MAX; k < g_failures && pause < PAUSE_MAX_MIN; k++) {
            pause *= 2;
        }
        if (pause > PAUSE_MAX_MIN) {
            pause = PAUSE_MAX_MIN;
        }
        if (!g_pause_timer_ready) {
            g_pause_timer.handler = pause_over;
            g_pause_timer.arg     = NULL;
            hal_tasks_init(&g_pause_timer);
            g_pause_timer_ready = 1;
        }
        g_pause_min = (uint16_t)pause;
        hal_tasks_schedule(&g_pause_timer, pause * 60000u);
    }
    diag_update();
}

static void result_ok(void) {
    g_failures = 0;
    if (g_pause_min) {
        g_pause_min = 0;
        hal_tasks_unschedule(&g_pause_timer);
    }
    diag_update();
}

static hal_task_t g_ident_timer;
static uint8_t    g_ident_timer_ready;
static uint8_t    g_ident_waiting;
static uint8_t    g_ident_retries;
static uint8_t    g_ident_gave_up;          /* its version answer's questions all ran out */
static uint8_t    g_ident_late;             /* asked at its queries since */

static void ident_timeout(void *arg);

static void ident_ask(uint8_t retries, uint32_t wait_ms) {
    if (!g_ident_timer_ready) {
        g_ident_timer.handler = ident_timeout;
        g_ident_timer.arg     = NULL;
        hal_tasks_init(&g_ident_timer);
        g_ident_timer_ready = 1;
    }
    g_ident_waiting = 1;
    g_ident_retries = retries;
    tuya_secondary_mcu_queue_cmd(TUYA_MCU_CMD_OTA_IDENT, NULL, 0);
    hal_tasks_schedule(&g_ident_timer, wait_ms);
}

static void ident_timeout(void *arg) {
    (void)arg;
    if (!g_ident_waiting) {
        return;
    }

    /* Lost on the way (a dropped UART burst, the MCU busy writing its
     * flash, a full queue): asked again, at growing intervals. A bare
     * version shown for our own MCU makes the converter take it for a stock
     * one, so that comes only after all of them. */
    if (g_ident_retries < IDENT_RETRIES) {
        ident_ask((uint8_t)(g_ident_retries + 1),
                  (uint32_t)IDENT_TIMEOUT_MS << (g_ident_retries + 1));
        return;
    }
    g_ident_waiting = 0;
    if (!g_ident_gave_up) {
        g_ident_gave_up = 1;
        tuya_mcu_app_show_mcu_version(); /* not ours: shown as it is */
    }
}

uint8_t tuya_mcu_ota_may_be_own(uint8_t packed) {
    return (packed >> 6) >= IDENT_ASK_FROM_MAJOR && secondary_mcu_image_size != 0;
}

void tuya_mcu_ota_mcu_restarted(void) {
    g_ident_gave_up = 0;
    if (g_ident_waiting) {
        g_ident_waiting = 0;
        hal_tasks_unschedule(&g_ident_timer);
    }
}

uint8_t tuya_mcu_ota_on_version(uint8_t packed) {
    if (!tuya_mcu_ota_may_be_own(packed)) {
        return 0;
    }
    g_ident_gave_up = 0;
    g_ident_late    = 0;
    ident_ask(0, IDENT_TIMEOUT_MS);
    return 1;
}

/* All questions lost (the link stopped for longer than they span: a UART
 * re-init, a long burst loss, an MCU stuck for a while): it is shown bare and
 * taken for a stock MCU. Its periodic network query (every 30 s) shows the
 * link works again, so a couple more questions are sent then; an answer turns
 * it into ours (shown "x.y.z own", everything read again). */
void tuya_mcu_ota_mcu_queried(void) {
    if (!g_ident_gave_up || g_ident_waiting || g_ident_late >= IDENT_LATE_MAX) {
        return;
    }
    g_ident_late++;
    ident_ask(IDENT_RETRIES, (uint32_t)IDENT_TIMEOUT_MS << IDENT_RETRIES);
}

/* Only our own firmware answers the identity query. The protocol byte is
 * the update protocol: a firmware with another one is still ours (its DPs),
 * it is only not offered an image it could not take. */
static void on_ident(const uint8_t *data, uint16_t len) {
    if (!g_ident_waiting) {
        return;                     /* only an answer to our own question */
    }
    if (len < IDENT_LEN || data[0] != 'Z' || data[1] != 'D' || data[2] != 'M' ||
        data[3] != 'S' || data[4] != IDENT_KIND_APP) {
        return;
    }
    g_ident_waiting = 0;
    g_ident_gave_up = 0;
    hal_tasks_unschedule(&g_ident_timer);
    tuya_mcu_app_set_mcu_own();             /* shows "x.y.z own" */

    image_info_t img;
    uint8_t      running = data[5];
    if (data[6] != IDENT_PROTOCOL) {
        printf("Own MCU firmware with update protocol %u: not offered\r\n", data[6]);
        return;
    }
    if (!image_info(&img) || img.version <= running) {
        return;                     /* only ever to a newer firmware */
    }
    if (g_offers >= OFFERS_MAX) {
        return;                     /* accepted but never started: not again */
    }
    g_offers++;
    uint8_t d[9];
    d[0] = img.version;
    put_be32(&d[1], img.size);
    put_be32(&d[5], img.crc);
    printf("Own MCU firmware 0x%02x, carried 0x%02x: offering the update\r\n",
           running, img.version);
    tuya_secondary_mcu_queue_cmd(TUYA_MCU_CMD_OTA_NOTIFY, d, sizeof(d));
}

static void answer_block(uint16_t seq, const uint8_t *data, uint16_t len) {
    image_info_t img;

    if (len < 6 || !image_info(&img)) {
        return;
    }
    uint32_t off  = be32(data);
    uint16_t want = (uint16_t)((data[4] << 8) | data[5]);

    if (off == 0) {
        /* The MCU is in its bootloader: its app's next query is a first
         * contact again, even if the result frame gets lost. */
        tuya_mcu_app_mcu_restarted();
    }
    uint16_t n = 0;

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
    case TUYA_MCU_CMD_OTA_IDENT:
        on_ident(data, len);
        return 1;

    case TUYA_MCU_CMD_OTA_NOTIFY:
        printf("Secondary MCU %s the update\r\n",
               (len >= 1 && data[0] == 0) ? "accepted" : "declined");
        return 1;

    case TUYA_MCU_CMD_OTA_BLOCK:
        answer_block(seq, data, len);
        return 1;

    case TUYA_MCU_CMD_OTA_RESULT:
        g_offers = 0;
        if (len >= 1 && data[0] == 0) {
            printf("Secondary MCU updated to 0x%02x\r\n", len >= 2 ? data[1] : 0);
            result_ok();

            /* It restarts now: treat its next query as a first contact, so
             * the version and the settings dump are read again. */
            tuya_mcu_app_mcu_restarted();
        } else {
            result_failed();
            if (g_pause_min) {
                printf("Secondary MCU update failed (%u), served again in %u min\r\n",
                       len >= 1 ? data[0] : 0xFF, g_pause_min);
            } else {
                printf("Secondary MCU update failed (%u), it asks again\r\n",
                       len >= 1 ? data[0] : 0xFF);
            }
        }
        return 1;

    default:
        return 0;
    }
}

#else /* boards that carry no MCU firmware: nothing of this */

uint8_t tuya_mcu_ota_on_version(uint8_t packed) {
    (void)packed;
    return 0;
}

uint8_t tuya_mcu_ota_may_be_own(uint8_t packed) {
    (void)packed;
    return 0;
}

void tuya_mcu_ota_mcu_restarted(void) {
}

void tuya_mcu_ota_mcu_queried(void) {
}

uint8_t tuya_mcu_ota_on_command(uint8_t cmd, uint16_t seq, const uint8_t *data,
                                uint16_t len) {
    (void)cmd;
    (void)seq;
    (void)data;
    (void)len;
    return 0;
}

#endif
