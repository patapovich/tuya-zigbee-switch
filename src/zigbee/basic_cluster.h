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
/* The start-up's look at the factory NV (zigbee/stock_nv.h), ZDMS16-2 builds. */
#ifdef SECONDARY_MCU_IMAGE
#define BASIC_STOCK_NV_ATTRS    1
#else
#define BASIC_STOCK_NV_ATTRS    0
#endif

typedef struct {
    uint8_t              deviceEnable;
    char                 manuName[32];
    char                 modelId[32];
    hal_zigbee_attribute attr_infos[37 + BASIC_MCU_FLASHER_ATTRS + BASIC_STOCK_NV_ATTRS]; // 13 fixed + led + 2 config + 12 dp attrs + MCU version + 7 MCU diag + health (checked in dp_attr.c), + the SWD master's (mcu_flasher.h)
} zigbee_basic_cluster;

void basic_cluster_add_to_endpoint(zigbee_basic_cluster *cluster,
                                   hal_zigbee_endpoint *endpoint);

void basic_cluster_callback_attr_write_trampoline(uint16_t attribute_id);

#endif
