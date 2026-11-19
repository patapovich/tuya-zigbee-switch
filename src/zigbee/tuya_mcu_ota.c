#include "zigbee/tuya_mcu_ota.h"
#include "device_config/config_parser.h"
#include "hal/printf_selector.h"
#include "hal/tasks.h"
#include "hal/timer.h"
#include "zigbee/consts.h"
#include "zigbee/secondary_mcu_image.h"
#include "zigbee/tuya_mcu_app.h"
#include "zigbee/tuya_mcu_diag.h"
#include "zigbee/tuya_secondary_mcu.h"

#ifdef SECONDARY_MCU_IMAGE

/* The carried app image as the MCU's bootloader takes it (zdms16-py32
 * src/ota_proto.h): the vector table, then a 16 byte header at offset 0xC0,
 * little-endian as in flash: magic, size, CRC, version byte, flags, build. */
#define IMAGE_HEADER_OFFSET     0xC0
#define IMAGE_SIZE_MIN          (IMAGE_HEADER_OFFSET + 16)
#define IMAGE_MAGIC             0x5A445041u
#define IMAGE_APP_BASE          0x08001000u   /* where the bootloader puts it */
#define IMAGE_SP                0x20000BE0u   /* every app's initial SP: below the RAM mailbox */
#define IMAGE_LIMIT_LEGACY      (12u * 1024u) /* an app whose settings ring is at 0x08004000 */
#define IMAGE_MAX               (14u * 1024u) /* 0x3800, the v2 bootloader's BOOT_APP_MAX: over
                                               * 12 KB only with RING_HIGH, after the ring move
                                               * (BOOTLOADER.md section 1), and behind the ring
                                               * gate (tuya_mcu_ota.h) */
#define IMAGE_FLAG_SIM          0x01          /* bench build with a virtual zero cross */
#define IMAGE_FLAG_MEASURE      0x02          /* measurement build: outputs always off */
#define IMAGE_FLAG_DIAG         0x04          /* diagnostics build: takes no update */
#define IMAGE_FLAGS_BENCH       (IMAGE_FLAG_SIM | IMAGE_FLAG_MEASURE | IMAGE_FLAG_DIAG)
#define IMAGE_FLAG_RING_HIGH    0x10          /* settings ring at 0x08004800: 14 KB fit */
#define BUILD_NONE              0             /* 2.1.x: valid and served, never newer */
#define BUILD_MIN               256           /* 1..255: never made */
#define BUILD_MAX               0xFFFD        /* 0xFFFE: SIM builds; 0xFFFF: never */

#define IDENT_LEN               7             /* "ZDMS" kind version protocol (a 2.1.x app) */
#define IDENT_LEN_BUILD         9             /* + its build, BE (a v2 app) */
#define IDENT_LEN_BOOT          10            /* + its bootloader's version */
#define IDENT_KIND_APP          1
#define IDENT_PROTOCOL          1
#define IDENT_TIMEOUT_MS        500   /* first wait; doubles at every retry */
#define NOTIFY_LEN              11    /* version size crc build; a 2.1.x app reads the first 9 */
#define REQUEST_LEN             16    /* v2 bootloader: + version, mode, installed header 8..15 */
#define REQUEST_MODE_NO_APP     2     /* no valid app: served past the ring gate */
#define REQUEST_MODE_RESCUE     4     /* 3 unconfirmed boots */
#define REQUEST_MODE_BUTTON     5     /* the module button at power-on */
#define REQUEST_MODE_LINK       6     /* the app's link watchdog asked for a probe */
#define REQUEST_RUN_GAP_MS      10000 /* a bootloader asks at least every 500 ms, gives up after 10 s */
#define BLOCK_HEADER_LEN        13    /* offset size crc version */
#define BLOCK_MAX               64    /* bytes per answer: one 86 byte frame */

typedef struct {
    uint32_t size;
    uint32_t crc;
    uint8_t  version;
    uint16_t build;
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

#define FAILURES_MAX       3                /* failed results in a row before a pause */
#define PAUSE_FIRST_MIN    15               /* then served again; doubles at every */
#define PAUSE_MAX_MIN      (24u * 60u)      /* further failure, up to a day */
#define OFFERS_MAX         3                /* offers without a result before giving up */

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

/* The carried image, checked once: what the MCU's v2 bootloader checks
 * before it runs an app (app_valid) and before it takes one, and a little
 * more. Header, size and CRC (with the CRC field read as 0, as
 * tools/mkimage.py computes it); the initial SP and an odd reset vector
 * inside the image; a size within the settings layout the image declares;
 * never a bench build (SIM, MEASURE, DIAG: they must never reach a lamp on
 * mains by themselves); a build number that can exist. A 2.1.x image (build
 * 0) is valid and served: what a bootloader may install is never refused
 * here, it is only never offered to an app that compares builds. */
static uint8_t image_usable(const image_info_t *img) {
    const uint8_t *h     = secondary_mcu_image + IMAGE_HEADER_OFFSET;
    uint32_t       sp    = le32(secondary_mcu_image);
    uint32_t       reset = le32(secondary_mcu_image + 4);
    uint8_t        flags = h[13];

    if (le32(h) != IMAGE_MAGIC || img->size != secondary_mcu_image_size ||
        img->size > IMAGE_MAX ||
        (img->size > IMAGE_LIMIT_LEGACY && !(flags & IMAGE_FLAG_RING_HIGH))) {
        return 0;
    }
    if (sp != IMAGE_SP || !(reset & 1u) || reset - IMAGE_APP_BASE >= img->size) {
        return 0;
    }
    if ((flags & IMAGE_FLAGS_BENCH) ||
        (img->build != BUILD_NONE && img->build < BUILD_MIN) || img->build > BUILD_MAX) {
        return 0;
    }
    static const uint8_t zero[4] = { 0, 0, 0, 0 };
    uint32_t             at      = IMAGE_HEADER_OFFSET + 8;
    uint32_t             crc     = crc32_update(0, secondary_mcu_image, at);
    crc = crc32_update(crc, zero, 4);
    crc = crc32_update(crc, secondary_mcu_image + at + 4, img->size - at - 4);
    return crc == img->crc;
}

static int image_info(image_info_t *info) {
    if (g_image_state == 2 || secondary_mcu_image_size < IMAGE_SIZE_MIN) {
        return 0;
    }
    const uint8_t *h = secondary_mcu_image + IMAGE_HEADER_OFFSET;
    info->size    = le32(h + 4);
    info->crc     = le32(h + 8);
    info->version = h[12];
    info->build   = (uint16_t)(h[14] | (h[15] << 8));
    if (g_image_state == 0) {
        g_image_state = image_usable(info) ? 1 : 2;
        if (g_image_state == 2) {
            printf("Carried MCU firmware unusable: not offered, not served\r\n");
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
static uint16_t   g_own_build;              /* the build in its last identity answer */
static uint8_t    g_own_running;            /* its version byte, whether it said a build */
static uint8_t    g_own_has_build;
static uint8_t    g_own_offerable;          /* its update protocol is ours */
static uint8_t    g_ring_moved;             /* its last health report said DP 137 bit 25 */

/* The ring gate (tuya_mcu_ota.h): an image this MCU may get. */
static uint8_t ring_gate_open(const image_info_t *img) {
    return img->size <= IMAGE_LIMIT_LEGACY ||
           (g_ring_moved && g_own_build >= OPEN_DIMMER_TRANSITION_BUILD);
}

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
    return (packed >> 6) >= TUYA_MCU_IDENT_ASK_FROM_MAJOR && secondary_mcu_image_size != 0;
}

uint16_t tuya_mcu_ota_own_build(void) {
    return g_own_build;
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

static void offer_update(uint8_t gated_only);

/* Only our own firmware answers the identity query. The protocol byte is
 * the update protocol: a firmware with another one is still ours (its DPs),
 * it is only not offered an image it could not take. A v2 app adds its build
 * and its bootloader's version. */
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

    uint8_t  running   = data[5];
    uint8_t  has_build = len >= IDENT_LEN_BUILD;
    uint16_t build     = has_build ? (uint16_t)((data[7] << 8) | data[8]) : 0;
    uint8_t  boot      = len >= IDENT_LEN_BOOT ? data[9] : 0;
    g_own_build             = build;
    g_own_running           = running;
    g_own_has_build         = has_build;
    g_own_offerable         = data[6] == IDENT_PROTOCOL;
    tuya_mcu_diag.mcu_build = build;
    tuya_mcu_diag.mcu_boot  = boot;
    tuya_mcu_diag_log(TUYA_MCU_DIAG_IDENT, running,
                      ((uint32_t)(uint8_t)len << 24) | ((uint32_t)boot << 16) | build);
    printf("Own MCU firmware 0x%02x build %u, bootloader %u\r\n", running, build, boot);
    tuya_mcu_app_set_mcu_own();             /* shows "x.y.z own" */

    if (!g_own_offerable) {
        printf("Own MCU firmware with update protocol %u: not offered\r\n", data[6]);
        return;
    }
    offer_update(0);
}

/* Only ever to a newer firmware: by the build when the app tells one (a carried 2.1.x image,
 * build 0, is never newer), else by the version byte, as a 2.1.x app compares; and past the
 * ring gate. gated_only: only an image the gate held back (one over 12 KB). */
static void offer_update(uint8_t gated_only) {
    image_info_t img;

    if (!g_own_offerable || !image_info(&img) || !ring_gate_open(&img) ||
        (gated_only && img.size <= IMAGE_LIMIT_LEGACY) ||
        (g_own_has_build ? img.build <= g_own_build : img.version <= g_own_running)) {
        return;
    }
    if (g_offers >= OFFERS_MAX) {
        return;                     /* accepted but never started: not again */
    }
    g_offers++;
    uint8_t d[NOTIFY_LEN];
    d[0] = img.version;
    put_be32(&d[1], img.size);
    put_be32(&d[5], img.crc);
    d[9]  = (uint8_t)(img.build >> 8);
    d[10] = (uint8_t)img.build;
    printf("Carried MCU firmware 0x%02x build %u: offering the update\r\n", img.version,
           img.build);
    tuya_secondary_mcu_queue_cmd(TUYA_MCU_CMD_OTA_NOTIFY, d, sizeof(d));
}

/* Diagnostics only, never a reason to answer otherwise (contract item 1):
 * what a request tells about the bootloader that asks. A v2 bootloader sends
 * its version, why it asks and the installed header's bytes 8..15; a v1 one
 * sends 6 bytes. A run of requests is logged once: at the first request,
 * another bootloader or mode, or after a pause. Rescues (a crash loop, the
 * module button) and link probes (the app heard nothing from us for 30 min)
 * are also counted. */
static uint32_t g_request_ms;
static uint8_t  g_request_seen;

static void note_request(const uint8_t *data, uint16_t len) {
    uint8_t  boot  = 1;
    uint8_t  mode  = 0;
    uint16_t build = 0;

    if (len >= REQUEST_LEN) {
        boot  = data[6];
        mode  = data[7];
        build = (uint16_t)(data[14] | (data[15] << 8));
    }
    uint16_t boot_mode = (uint16_t)((boot << 8) | mode);
    uint32_t now       = hal_millis();
    uint8_t  new_run   = !g_request_seen || boot_mode != tuya_mcu_diag.request_boot_mode ||
                         now - g_request_ms > REQUEST_RUN_GAP_MS;

    g_request_seen = 1;
    g_request_ms   = now;
    tuya_mcu_diag.request_boot_mode = boot_mode;
    tuya_mcu_diag.request_build     = build;
    if (!new_run) {
        return;
    }
    if (mode == REQUEST_MODE_RESCUE || mode == REQUEST_MODE_BUTTON) {
        tuya_mcu_diag.rescues++;
        printf("MCU in rescue (%s)\r\n",
               mode == REQUEST_MODE_BUTTON ? "module button" : "crash loop");
    } else if (mode == REQUEST_MODE_LINK) {
        tuya_mcu_diag.link_probes++;
        printf("MCU link probe: it heard nothing from us for 30 min\r\n");
    }
    tuya_mcu_diag_log(TUYA_MCU_DIAG_BOOT_REQUEST, mode,
                      ((uint32_t)(uint8_t)len << 24) | ((uint32_t)boot << 16) | build);
}

static void answer_block(uint16_t seq, const uint8_t *data, uint16_t len) {
    image_info_t img;

    if (len < 6) {
        return;
    }
    note_request(data, len);
    if (!image_info(&img) ||
        (!ring_gate_open(&img) && !(len >= REQUEST_LEN && data[7] == REQUEST_MODE_NO_APP))) {
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
    if (tuya_secondary_mcu_send_long_reply(TUYA_MCU_CMD_OTA_BLOCK, seq, head, sizeof(head),
                                           n ? secondary_mcu_image + off : NULL, n) == 0 &&
        tuya_mcu_diag.first_answer_ms == 0) {
        tuya_mcu_diag.first_answer_ms = tuya_mcu_diag_ms(hal_millis());   /* contract item 3 */
    }
}

/* ---- health report (tuya_mcu_ota.h) ----------------------------------------------------- */

#define DP_HEALTH_FIRST          130
#define DP_HEALTH_UPTIME         130
#define DP_HEALTH_RESET          131
#define DP_HEALTH_ZC_LOSSES      132
#define DP_HEALTH_UART_LOSSES    135
#define DP_HEALTH_BOOT_WORD      137
#define DP_HEALTH_TEMPERATURE    138
#define DP_HEALTH_TEMP_CAL       139 /* right after 138, same frame: the round is done at 138 */
#define HEALTH_FORMAT            1
#define BOOT_WORD_RING_MOVED     (1u << 25)

static tuya_mcu_health_t g_health;
static uint8_t           g_health_rounds;

static void health_round_done(void) {
    g_health.len    = (uint8_t)(sizeof(g_health) - 1);
    g_health.format = HEALTH_FORMAT;
    hal_zigbee_notify_attribute_changed(endpoints[0].endpoint, ZCL_CLUSTER_BASIC,
                                        ZCL_ATTR_BASIC_MCU_HEALTH);
    if (g_health_rounds++ % HEALTH_REPORT_EVERY == 0) {
        hal_zigbee_send_report_attr(endpoints[0].endpoint, ZCL_CLUSTER_BASIC,
                                    ZCL_ATTR_BASIC_MCU_HEALTH, ZCL_DATA_TYPE_OCTET_STR,
                                    &g_health, (uint8_t)sizeof(g_health));
    }
}

static void health_on_dp(uint8_t dpid, uint8_t dp_type, const uint8_t *value,
                         uint16_t value_len) {
    if (dpid < DP_HEALTH_FIRST || dpid > DP_HEALTH_TEMP_CAL || dp_type != TUYA_DP_TYPE_VALUE ||
        value_len != 4 || !tuya_mcu_app_mcu_is_own()) {
        return;
    }
    uint32_t v = be32(value);

    switch (dpid) {
    case DP_HEALTH_UPTIME:
        g_health.uptime_min = v;
        break;
    case DP_HEALTH_RESET:
        g_health.reset_cause = (uint8_t)v;
        break;
    case DP_HEALTH_ZC_LOSSES:
        g_health.zc_losses = v;
        break;
    case DP_HEALTH_UART_LOSSES:
        g_health.uart_losses = v;
        break;
    case DP_HEALTH_BOOT_WORD: {
        uint8_t moved = (v & BOOT_WORD_RING_MOVED) != 0;
        g_health.flags = (uint8_t)((v >> 24) & 0x03);
        if (moved && !g_ring_moved) {
            g_ring_moved = 1;
            offer_update(1);        /* an image over 12 KB waited for this */
        }
        g_ring_moved = moved;
        if (g_own_build < OPEN_DIMMER_FEATURES_BUILD) {
            health_round_done();    /* no DP 138 follows */
        }
        break;
    }
    case DP_HEALTH_TEMPERATURE:
        g_health.temperature = (int16_t)(int32_t)v;
        health_round_done();
        break;
    case DP_HEALTH_TEMP_CAL:
        g_health.temp_cal = v;      /* factory data: the same every round */
        break;
    default:
        break;
    }
}

void tuya_mcu_ota_init(void) {
    g_health.temperature = -32768;
    tuya_secondary_mcu_add_dp_listener(health_on_dp);
}

uint8_t tuya_mcu_ota_register_attrs(hal_zigbee_attribute *table, uint8_t start_index) {
    hal_zigbee_attribute *attr = &table[start_index];

    attr->attribute_id = ZCL_ATTR_BASIC_MCU_HEALTH;
    attr->data_type_id = ZCL_DATA_TYPE_OCTET_STR;
    attr->flag         = ATTR_READONLY;
    attr->size         = (uint8_t)sizeof(g_health);
    attr->value        = (uint8_t *)&g_health;
    return 1;
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
            printf("Secondary MCU updated to 0x%02x build %u\r\n", len >= 2 ? data[1] : 0,
                   len >= 4 ? (data[2] << 8) | data[3] : 0);
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

void tuya_mcu_ota_init(void) {
}

uint8_t tuya_mcu_ota_register_attrs(hal_zigbee_attribute *table, uint8_t start_index) {
    (void)table;
    (void)start_index;
    return 0;
}

uint16_t tuya_mcu_ota_own_build(void) {
    return 0;
}

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
