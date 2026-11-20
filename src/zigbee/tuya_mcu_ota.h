/* Update of an Open Dimmer secondary-MCU firmware from the image this firmware
 * carries (secondary_mcu_image.c; AVATTO ZDMS16-2 with the own PY32 firmware).
 *
 * An MCU that reports firmware 2.x or later is asked who it is (0xA3; 4 times
 * in 7.5 s, then at its next 2 network queries if all were lost). Only
 * our own firmware answers ("ZDMS", kind, version, update protocol, and from
 * the v2 app on its build and bootloader version); a stock MCU does not,
 * whatever version number it has, so it is never offered anything (1.x is not
 * even asked). Our firmware gets the carried image offered (0xA0) when it is
 * newer: by the build number when the answer has one, else by the version
 * byte. It restarts into its bootloader, which asks for the image block by
 * block (0xA1, answered from flash, stateless) and reports the result (0xA2).
 * A bootloader without a valid app (e.g. after a power cut during an update)
 * asks by itself, and the v2 bootloader also asks for the image header at
 * power-on, in its rescue windows and when the app's link watchdog restarts
 * it. After 3 failed results in a row nothing is served for 15 min, then for
 * twice as long after every further failure (up to a day).
 *
 * ---- Frozen contract ---------------------------------------------------------
 *
 * The MCU's v2 bootloader is replaced only over SWD, while this firmware
 * changes over the air, so every later release of it keeps these promises
 * (open-dimmer docs/BOOTLOADER.md section 10; the MCU side is its
 * src/ota_proto.h). Pinned by tests/test_mcu_ota.py and
 * tests/test_stock_mcu_compat.py (and, for boards without an image, by the
 * symbol check of every Telink board build), and checked on a test module
 * for every release (bench test B2) before it goes further:
 *
 *  1. 0xA1 v1 semantics, for ever. A request is offset (4) + max_len (2) + any
 *     number of further bytes, which the serving rule ignores. The answer is
 *     offset, size, CRC, version byte and up to min(max_len, 128) bytes of the
 *     image, without state, for any offset; the header sits at image offset
 *     0xC0. Serving is gated only by the image check and the failure pause,
 *     never by build, version or the request's mode; an image over 12 KB also by
 *     the ring gate below, which looks at the request's mode and installed
 *     header for three things only: an MCU without a valid app (mode 2) is
 *     served what it can take; a probe, rescue, button or link run (mode 3, 4,
 *     5, 6) over an installed app whose header says RING_HIGH (request byte 13,
 *     the header's flags, bit 0x10) is served; and the header request (offset
 *     0xC0) of a probe (mode 3, or 6 for a link probe) that the gate keeps the
 *     image from gets a refusal at once: a header answer of size 0, CRC 0,
 *     version 0 and 16 zero bytes, which the v2 bootloader takes for "nothing
 *     here" and starts its app (boot.c pick(), PICK_REFUSE), instead of
 *     waiting out its 3 s probe window.
 *  2. Robust. No crash and no stall on any request a bootloader can send:
 *     offset 0..0xFFFFFFFF, max_len 0..0xFFFF, 6 to 16 bytes, at up to one
 *     request per 50 ms for days (a unit without an app asks every 500 ms).
 *  3. Timely. The first header answer within TUYA_MCU_OTA_FIRST_ANSWER_MS of
 *     power-up (half the bootloader's 3 s probe window), also on the first boot
 *     after an OTA of this firmware. A UART started earlier than today (before
 *     the Zigbee stack init) only with the first-contact timer at the end of
 *     the full init and at least TUYA_MCU_FIRST_CONTACT_MIN_MS, and a link that
 *     only answers 0xA1 until then.
 *  4. Identity, by class of MCU. The major always comes from a 0x0B answer in
 *     this run, never from a guess.
 *     (a) Identified as Open Dimmer in this run (a "ZDMS" / kind 1 answer): it
 *         is asked again after every seq-0 network query, every block request
 *         for offset 0 and every OK result.
 *     (b) Major >= TUYA_MCU_IDENT_ASK_FROM_MAJOR, not identified:
 *         1 + IDENT_RETRIES questions after each version answer and
 *         IDENT_LATE_MAX more at later network queries. At most
 *         UNIDENTIFIED_RESTARTS_MAX seq-0 queries per run are taken for a
 *         restart (a first contact again): a stock MCU may number every query
 *         0. Nothing beyond that.
 *     (c) Major below that, or no 0x0B answer yet: never asked, and no restart
 *         handling beyond phase 1's (one first contact, repeated once at its
 *         first network query).
 *  5. Results. 0xA2 status 0 = written and valid; anything else is a failure
 *     that counts toward the pause; the pause doubles up to a day and ends
 *     with a restart of this firmware.
 *
 * Not needed by the frozen bootloader; they protect factory firmware, the
 * Open Dimmer app's stock-ZTU rule and its link watchdog, and are pinned the
 * same way:
 *
 *  6. Factory MCUs. An MCU not identified as Open Dimmer gets only the
 *     phase-1 command set (0xA3 only within 4 (b)), with one queued frame in
 *     flight. Each frame is at most TUYA_MCU_FACTORY_FRAME_MAX bytes, and
 *     frames less than 2 ms apart (a burst: a reply and the queued frame next
 *     to it, or two replies) add up to at most TUYA_MCU_FACTORY_BURST_MAX.
 *     Longer frames (the 38 and 86 byte 0xA1 answers) go only as the direct
 *     reply to an 0xA1 request and are never pushed; that holds for every
 *     later idea too (an image-info push, a second image for a rescue, a
 *     longer offer). Reason: the stock parser holds 23 bytes in its ISR ring
 *     and 31 in its frame buffer, and a frame of 32..38 bytes wedges its RX
 *     until a power cut (open-dimmer docs/stock-firmware-analysis.md, "Stock
 *     MCU parser limits": read from the WiFi 1.0.3, inferred for 1.0.7).
 *     Towards a stock MCU this firmware sends exactly what phase 1 sends, apart
 *     from one more dump query per run, when the run's first one got no DP
 *     report within 1 s.
 *  7. No 0x01 (product query) to any MCU: the Open Dimmer app takes 0x01
 *     queries without any 0xA3 for a stock Tuya Zigbee module.
 *  8. Heard. An MCU identified as Open Dimmer gets a well-formed frame at least
 *     every 30 min: every 0x20 network query (the app sends one every 30 s) is
 *     answered, as in phase 1. The app's link watchdog takes 30 min without any
 *     frame for a dead link and restarts (only while dark, at most once per
 *     4 h), so a release that stopped answering would cost every unit a 3 s
 *     restart every 4 h. */
#ifndef TUYA_MCU_OTA_H
#define TUYA_MCU_OTA_H

#include <stdint.h>

#include "hal/zigbee.h"

#define TUYA_MCU_CMD_OTA_NOTIFY          0xA0 /* module -> MCU: version size crc build */
#define TUYA_MCU_CMD_OTA_BLOCK           0xA1 /* MCU -> module: offset max_len [boot mode installed]; answer: block */
#define TUYA_MCU_CMD_OTA_RESULT          0xA2 /* MCU -> module: status version [build] */
#define TUYA_MCU_CMD_OTA_IDENT           0xA3 /* module -> MCU: who are you? */

/* Contract item 3. */
#define TUYA_MCU_OTA_FIRST_ANSWER_MS     1500
#define TUYA_MCU_FIRST_CONTACT_MIN_MS    2000

/* Contract item 4. */
#define TUYA_MCU_IDENT_ASK_FROM_MAJOR    2      /* stock ZDMS16-2 MCUs are 1.x */
#define IDENT_RETRIES                    3      /* asked 4 times in 7.5 s before a bare version */
#define IDENT_LATE_MAX                   2      /* then at the MCU's next 2 network queries */
#define UNIDENTIFIED_RESTARTS_MAX        3      /* seq-0 queries of a 2.x MCU not identified */

/* Contract item 6. */
#define TUYA_MCU_FACTORY_FRAME_MAX       29     /* the largest frame phase 1 queues: 8 + 4 + 16 + 1 */
#define TUYA_MCU_FACTORY_BURST_MAX       23     /* the stock MCU's USART ring */

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

/** The dimmers' light: lit = 1 while any channel is on or its state is not known yet. An
 *  update makes the light go dark for the few seconds the MCU's bootloader writes, so an offer
 *  waits while it is lit (and a minute after it went off), a day at most. */
void tuya_mcu_ota_set_light(uint8_t lit);

/** For tuya_mcu_ota_set_light: the dimmers exist, their state is not known yet (as lit). A board
 *  without dimmers never says it, and its offers never wait. */
#define TUYA_MCU_OTA_LIGHT_UNKNOWN    0xFF

/** The build an Open Dimmer said in its last identity answer (0: none yet, or a 2.1.x app
 *  without one). Valid only while tuya_mcu_app_mcu_is_own(). */
uint16_t tuya_mcu_ota_own_build(void);

/* Open Dimmer from this build (2.1.14) takes a transition time: DP 160 / 161, the ms its
 * network changes of the next second take (open-dimmer src/dimmer.h). */
#define OPEN_DIMMER_TRANSITION_BUILD    400

/* Open Dimmer from this build (2.2.0) takes a countdown (DP 6 / 12), the wall switch mode
 * (162 / 163), the level after a power cut (164 / 165), identify (166 / 167) and the start
 * boost (168 / 169). Nothing of these goes to any other MCU. */
#define OPEN_DIMMER_FEATURES_BUILD      500

/* ---- ring gate and health report (Open Dimmer, open-dimmer src/health.h) ----------------
 *
 * An app before 2.1.14 keeps its settings ring at 0x08004000, right after a 12 KB app; 2.1.14
 * (build 400, the ring mover) moves it up to 0x08004800 and says so in its health report: DP
 * 137 bit 24 (its ring is the upper one) and bit 25 (its newest settings record is in it). An
 * image over 12 KB (it needs RING_HIGH, see image_usable()) would overwrite the old ring, so
 * it is offered, and its blocks are served to an asked run (mode 1) and a v1 bootloader, only
 * to an MCU whose last health report since this firmware's start said bit 25 and that
 * identified itself with a build of at least OPEN_DIMMER_TRANSITION_BUILD. A report that
 * opens the gate offers the update then. Two exceptions, for the bootloader's own runs, in
 * which no app reports (a crash loop never does, the button window and the power-on probe
 * come before it can, and an app that declines offers is reached only so):
 *  - a bootloader with no valid app (request mode 2, e.g. after a power cut during an
 *    update, which also restarted this firmware, so it has heard no report) is served
 *    whatever the image: the bootloader itself takes up to 14 KB there ("recovery comes
 *    before settings", BOOTLOADER.md section 1); a refusal would leave the lamp dark;
 *  - a probe, rescue, button or link run (mode 3, 4, 5, 6) whose request's installed header
 *    (bytes 8..15: CRC, version, flags, build) has RING_HIGH is served: that app's layout has
 *    its settings at 0x08004800 already, and over any other app the bootloader refuses an
 *    image over 12 KB itself (boot.c pick(): size > g_limit), so the module need not.
 *    Accepted risk: a 2.1.14 app whose ring copy failed (RING_HIGH set, its newest settings
 *    record still in the low ring, DP 137 bit 25 clear) that takes such an image in one of
 *    these runs loses its settings (the defaults), never its function. And a working RING_HIGH
 *    app gets a newer image over 12 KB already at the power-on probe, not only after its
 *    first health report.
 * A probe (mode 3 or 6) that the gate keeps the image from gets a refusal for its header
 * request (contract item 1) within the first-answer time of contract item 3, so a power-up is
 * not 3 s slower; a rescue or button run (mode 4, 5) gets no answer and its window runs out.
 * Images up to 12 KB are not gated. A request for offset 0
 * is a restart sign (contract item 4 (a)) whether the gate serves it or not. A factory MCU
 * never reports, so with such an image it gets no block answer at all (to the 0xA1 requests a
 * stock MCU should never send): fewer frames than phase 1, each one a reply it would have had
 * to ask for, so towards the factory firmware this is the safe direction of contract item 6
 * (tests/test_stock_mcu_compat.py takes the block answers out of the golden traces then).
 *
 * The health report (every 2 min, DPs 130..137 in one frame, from 2.2.0 138 the die
 * temperature and 139 its sensor's factory calibration in a frame of their own) of an
 * identified Open Dimmer (a round ends with DP 139 from build 500, with 137 before) is kept
 * in Basic 0xff48 (octet string,
 * tuya_mcu_health_t without its length byte, little endian; empty until the first report)
 * and, at the first report and every HEALTH_REPORT_EVERY-th after it, sent as an attribute
 * report to the Basic cluster's bindings (the converter binds it to the coordinator). */
#define HEALTH_REPORT_EVERY             5 /* health reports per one sent: ~10 min */

typedef struct __attribute__((packed)) {
    uint8_t  len;           /* octet string length: 0 until the first report */
    uint8_t  format;        /* 1 */
    uint32_t uptime_min;    /* DP 130 */
    uint32_t zc_losses;     /* DP 132: zero-cross lock losses */
    uint32_t uart_losses;   /* DP 135: UART receive losses */
    uint8_t  reset_cause;   /* DP 131: RCC_CSR >> 24 at its start (bit 4 software, 3 power...) */
    uint8_t  flags;         /* DP 137 >> 24: bit 0 ring high, bit 1 ring moved */
    int16_t  temperature;   /* DP 138: degC, -32768 none (before 2.2.0: always) */
    uint32_t temp_cal;      /* DP 139: raw value at 85 degC << 16 | at 30 degC (0: none) */
} tuya_mcu_health_t;

/** Start listening to the health report (DPs 130..138). */
void tuya_mcu_ota_init(void);

/** Append the health report attribute (Basic 0xff48) to a Basic cluster table; returns how
 *  many (0 on boards without an image). */
uint8_t tuya_mcu_ota_register_attrs(hal_zigbee_attribute *table, uint8_t start_index);

/** The MCU's network query that is no first contact, with no version
 *  question out: an MCU whose identity questions all ran out is asked again
 *  (twice at most, until its next version answer). */
void tuya_mcu_ota_mcu_queried(void);

/* CRC-32 (zlib), as the carried image's: SECONDARY_MCU_IMAGE builds only. */
uint32_t tuya_mcu_crc32_update(uint32_t crc, const uint8_t *p, uint32_t n);

/** An MCU command; returns 1 if it was an update command (handled here). */
uint8_t tuya_mcu_ota_on_command(uint8_t cmd, uint16_t seq, const uint8_t *data,
                                uint16_t len);

#endif
