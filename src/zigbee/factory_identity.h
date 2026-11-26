#ifndef _FACTORY_IDENTITY_H_
#define _FACTORY_IDENTITY_H_

#include <stdint.h>

#include "zigbee/stock_nv.h"

/* The module's factory identity, kept over the conversion (builds that carry an MCU image, and
 * the HK builds: the TS110E):
 * - the manufacturer name the factory Zigbee firmware reported: its NV module 10, item 3, a ZCL
 *   character string in 33 bytes (length byte + up to 32 characters), e.g. "_TZE284_jtbgusdc".
 *   The factory firmware first stores its built-in default there and later the name made of its
 *   prefix and the MCU's product ID; the newest intact copy is the one it used last. With the
 *   HK token (the TS110E, whose factory firmware is no TS0601 one) the item's id is not known:
 *   the first of the module's 33-byte items that holds a Tuya name (_T....._xxxxxxxx);
 * - the factory MCU's version, as this firmware first heard it from the MCU (a version below
 *   2.x: Open Dimmer is 2.x), so it is known after Open Dimmer has replaced it. Never on the
 *   TS110E: its factory MCU never answers (the version 1.0.0 is the virtual MCU's).
 * The factory NV is still there after a conversion (only the image moves, ensure_ota_scheme.c).
 * What is found goes into this firmware's NV once (NV_ITEM_FACTORY_IDENTITY), so it stays when
 * the factory area is gone. Shown as Basic 0xff59, a character string: "<name>",
 * "<name>, MCU x.y.z", "MCU x.y.z" or "" (nothing known). Tuya's manufacturing area (0xFB000:
 * the product ID and the device keys) is never read. */
#define FACTORY_NV_MODULE        10u
#define FACTORY_NV_NAME_ITEM     3u
#define FACTORY_NAME_RECORD      33u  /* length byte + up to 32 characters */
#define FACTORY_NAME_MAX         32u
#define FACTORY_IDENTITY_TEXT    48u  /* the attribute's characters at most (one frame) */

/* Basic 0xff59: a ZCL character string (length byte, then the characters). */
extern uint8_t g_factory_identity_attr[1 + FACTORY_IDENTITY_TEXT];

/* At start-up: this firmware's record; while it has no name, the factory NV through read
 * (NULL: not looked at, e.g. a chip without the 1 MB flash the factory NV is on). */
void factory_identity_init(stock_nv_read_t read);

/* The MCU told its version (Tuya packed: major in bits 7..6, minor 5..4, patch 3..0): kept as
 * the factory MCU's when below 2.x and none was kept before. */
void factory_identity_note_mcu(uint8_t packed);

#endif
