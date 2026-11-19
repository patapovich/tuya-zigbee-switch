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

/* Of them the SDK's own: the OTA client only. Checked in the Telink SDK (telink_tools/sdk):
 * the list (zcl_vars.clusterList[ZCL_CLUSTER_NUM_MAX], zigbee/zcl/zcl.h:513) is filled only
 * by zcl_registerCluster() (zigbee/zcl/zcl.c:182-200: one entry per endpoint and cluster id,
 * client and server alike, a second one of the same id refused), which every cluster's
 * register function calls, and those run only from zcl_register() (zcl.c:136-147, it skips
 * an entry without a register function). zcl_register() is called by this HAL (one call per
 * endpoint, every cluster but OTA) and by the SDK's ota_init() for its OTA client
 * (zigbee/ota/ota.c:335, one cluster: zigbee/ota/otaEpCfg.c:84-87). gp_init() (Green Power,
 * zigbee/gp/gp.c:80-89) and the WWAH init (zigbee/wwah/wwah.c) would register one more each,
 * but nothing calls them: neither is in the linked ELF. A client-only cluster (e.g. On/Off
 * client for binding) takes an entry here only because this HAL registers it: sending needs
 * none (zcl_sendCmd, bindings and match descriptors use the simple descriptor's out list);
 * an entry is needed to take commands or attribute requests for it (zcl.c:841). */
#define HAL_ZIGBEE_SDK_ZCL_CLUSTERS     1

/* Clusters and attributes the HAL could not register because a table was full (0: none). */
extern uint16_t hal_zigbee_tables_dropped;

#endif
