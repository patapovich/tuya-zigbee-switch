#ifndef _HAL_ZIGBEE_LIMITS_H_
#define _HAL_ZIGBEE_LIMITS_H_

#include <stdint.h>

/* What the Telink HAL and stack can register (src/telink/hal/zigbee_zcl.c: endpoint
 * descriptors, in and out cluster lists, cluster infos, attribute tables; the SDK's own
 * ZCL cluster list, ZCL_CLUSTER_NUM_MAX in src/telink/configs/stack_cfg.h, which also holds
 * the SDK's OTA client and Green Power proxy clusters). The Telink HAL never writes past
 * them: what does not fit is not registered and counted in hal_zigbee_tables_dropped (the
 * MCU link diagnostics show it). The stub logs every configuration's counts against them,
 * and tests/test_zcl_tables.py checks the config of every Telink board in device_db. */
#define HAL_ZIGBEE_MAX_ENDPOINTS        8
#define HAL_ZIGBEE_MAX_IN_CLUSTERS      32
#define HAL_ZIGBEE_MAX_OUT_CLUSTERS     32
#define HAL_ZIGBEE_MAX_CLUSTER_INFOS    (HAL_ZIGBEE_MAX_IN_CLUSTERS + HAL_ZIGBEE_MAX_OUT_CLUSTERS)
#define HAL_ZIGBEE_MAX_ATTRS            128
#define HAL_ZIGBEE_MAX_ZCL_CLUSTERS     32  /* ZCL_CLUSTER_NUM_MAX */
#define HAL_ZIGBEE_SDK_ZCL_CLUSTERS     2   /* of them the SDK's own: OTA client, Green Power */

/* Clusters and attributes the HAL could not register because a table was full (0: none). */
extern uint16_t hal_zigbee_tables_dropped;

#endif
