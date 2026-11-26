#ifndef _BASIC_CLUSTER_H_
#define _BASIC_CLUSTER_H_

#include "hal/zigbee.h"

#include <stddef.h>

/* The SWD master's status, control, confirm and dump (mcu_flasher.h MCU_FLASHER_ATTRS), only in
 * builds that carry an MCU image: the table of every other board stays as it was. */
#if defined(SECONDARY_MCU_IMAGE) && defined(MCU_FLASHER_DUMP)
#define BASIC_MCU_FLASHER_ATTRS    5   /* + the dump job's two (debug builds) */
#elif defined(SECONDARY_MCU_IMAGE)
#define BASIC_MCU_FLASHER_ATTRS    3
#else
#define BASIC_MCU_FLASHER_ATTRS    0
#endif

/* The Open Dimmer build (Basic 0xff5f, zigbee/tuya_mcu_ota.h): only in builds that carry an MCU
 * image, the only ones that ask the MCU who it is. */
#if defined(SECONDARY_MCU_IMAGE)
#define BASIC_MCU_BUILD_ATTRS    1
#else
#define BASIC_MCU_BUILD_ATTRS    0
#endif

/* The secondary MCU link diagnostics (zigbee/tuya_mcu_diag.h): counters, log pages, control;
 * debug builds only. */
#if defined(MCU_FLASHER_DUMP)
#define BASIC_MCU_DIAG_ATTRS    7
#else
#define BASIC_MCU_DIAG_ATTRS    0
#endif

/* The start-up's look at the factory NV (zigbee/stock_nv.h), the factory identity
 * (zigbee/factory_identity.h) and the stack exception that last restarted the module
 * (telink/hal/zigbee_network.c): the boards converted from a factory Zigbee firmware that keep
 * its network, ZDMS16-2 and TS110E (HK_DIMMER) builds. */
#if defined(SECONDARY_MCU_IMAGE) || defined(HK_DIMMER)
#define BASIC_STOCK_NV    1
#endif
#ifdef HK_DIMMER
#include "zigbee/hk_dimmer.h"
#define BASIC_HK_ATTRS          HK_BASIC_ATTRS   /* the custom dimming curves (hk_dimmer.c) */
#else
#define BASIC_HK_ATTRS          0
#endif
#if defined(BASIC_STOCK_NV) && defined(MCU_FLASHER_DUMP)
#define BASIC_STOCK_NV_ATTRS    6   /* + the log page number, page and wipe (debug builds) */
#elif defined(BASIC_STOCK_NV)
#define BASIC_STOCK_NV_ATTRS    3
#else
#define BASIC_STOCK_NV_ATTRS    0
#endif

/* Whether the module's own NWK commands are secured (zigbee/nwk_sec.h): every build but the
 * Silabs ones (their stack has no such check here); debug builds also the hook that clears it. */
#ifndef HAL_SILABS
#define BASIC_NWK_SEC          1
#endif
#if defined(BASIC_NWK_SEC) && defined(MCU_FLASHER_DUMP)
#define BASIC_NWK_SEC_ATTRS    2
#elif defined(BASIC_NWK_SEC)
#define BASIC_NWK_SEC_ATTRS    1
#else
#define BASIC_NWK_SEC_ATTRS    0
#endif

typedef struct {
    uint8_t              deviceEnable;
    char                 manuName[32];
    char                 modelId[32];
    hal_zigbee_attribute attr_infos[30 + BASIC_MCU_DIAG_ATTRS + BASIC_MCU_FLASHER_ATTRS +
                                    BASIC_STOCK_NV_ATTRS + BASIC_HK_ATTRS +
                                    BASIC_NWK_SEC_ATTRS + BASIC_MCU_BUILD_ATTRS];                               // 13 fixed + led + 2 config + 12 dp attrs + MCU version + health (checked in dp_attr.c), + the MCU diag (debug builds), the SWD master's (mcu_flasher.h) and the MCU build
} zigbee_basic_cluster;

void basic_cluster_add_to_endpoint(zigbee_basic_cluster *cluster,
                                   hal_zigbee_endpoint *endpoint);

void basic_cluster_callback_attr_write_trampoline(uint16_t attribute_id);

#endif
