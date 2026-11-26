#pragma once

#include "hal/zigbee.h"
#include "hal/zigbee_limits.h"

#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#include "zcl_include.h"
#pragma pack(pop)

// Shared constants
#define MAX_ENDPOINTS         HAL_ZIGBEE_MAX_ENDPOINTS
#define MAX_IN_CLUSTERS       HAL_ZIGBEE_MAX_IN_CLUSTERS
#define MAX_OUT_CLUSTERS      HAL_ZIGBEE_MAX_OUT_CLUSTERS
#define MAX_ATTRS             HAL_ZIGBEE_MAX_ATTRS
#define OTA_QUERY_INTERVAL    15 * 60 // 15 minutes

// Network module functions (implemented in zigbee_network.c)
void telink_zigbee_hal_network_init(void);
void telink_zigbee_hal_bdb_init(af_simple_descriptor_t *endpoint_descriptor);

// ZCL module functions (implemented in zigbee_zcl.c)
void telink_zigbee_hal_zcl_init(hal_zigbee_endpoint *endpoints,
                                uint8_t endpoints_cnt);
af_simple_descriptor_t *telink_zigbee_hal_zcl_get_descriptors(void);

/* The stack's report_handler(), unless hal_zigbee_hold_reports holds the reporting. */
void telink_zigbee_report_handler(void);

// How many more reports may ask for an APS ACK now, if above 0: the SDK's zcl_reporting.c,
// patched (patch_sdk/zigbee/zcl/zcl_reporting.c.patch, which says why there is a limit). Read
// from the APS TX cache as it is: frames handed out earlier in the same main-loop pass reach it
// only later, so reports sent back to back (mcu_version, then mcu_build) see the same room and
// can go 1-2 past the limit.
int zcl_reportAckRoom(void);

void telink_gpio_hal_setup_wake_ups();

void telink_gpio_reinit_after_deep_retention();
void telink_gpio_to_pull_for_deep_retention();
void telink_gpio_reinit_interrupts();
