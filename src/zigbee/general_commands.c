#include "basic_cluster.h"
#include "consts.h"
#include "cover_cluster.h"
#include "cover_switch_cluster.h"
#include "dimmer_cluster.h"
#include "hal/printf_selector.h"
#include "poll_control_cluster.h"
#include "relay_cluster.h"
#include "switch_cluster.h"

static void zigbee_on_attr_change(uint8_t endpoint, uint16_t cluster_id,
                                  uint16_t attribute_id) {
    printf("Attribute changed, ep: %d, cluster: %d, attr: %d\r\n", endpoint,
           cluster_id, attribute_id);
    if (cluster_id == ZCL_CLUSTER_BASIC) {
        basic_cluster_callback_attr_write_trampoline(attribute_id);
    } else if (cluster_id == ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG) {
        // genOnOffSwitchCfg is shared by switches and dimmers; each trampoline
        // no-ops on endpoints it does not own. A DIMMER_ONLY build has no
        // switch, relay or cover endpoints (config_parser.c).
#ifndef DIMMER_ONLY
        switch_cluster_callback_attr_write_trampoline(endpoint, attribute_id);
#endif
        dimmer_cluster_callback_attr_write_trampoline(endpoint, cluster_id, attribute_id);
    } else if (cluster_id == ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG ||
               cluster_id == ZCL_CLUSTER_LEVEL_CONTROL) {
        // Dimmer endpoints only (ballast min/max, OnLevel).
        dimmer_cluster_callback_attr_write_trampoline(endpoint, cluster_id, attribute_id);
    } else if (cluster_id == ZCL_CLUSTER_COVER_SWITCH_CONFIG) {
#ifndef DIMMER_ONLY
        cover_switch_cluster_callback_attr_write_trampoline(endpoint, attribute_id);
#endif
    } else if (cluster_id == ZCL_CLUSTER_ON_OFF) {
        // OnOff is shared by relays and dimmers; each trampoline no-ops on
        // endpoints it does not own (StartUpOnOff is writable on both).
#ifndef DIMMER_ONLY
        relay_cluster_callback_attr_write_trampoline(endpoint, attribute_id);
#endif
        dimmer_cluster_callback_attr_write_trampoline(endpoint, cluster_id, attribute_id);
    } else if (cluster_id == ZCL_CLUSTER_WINDOW_COVERING) {
#ifndef DIMMER_ONLY
        cover_cluster_callback_attr_write_trampoline(endpoint, attribute_id);
#endif
    }
#ifdef END_DEVICE
    else if (cluster_id == ZCL_CLUSTER_POLL_CONTROL) {
        poll_control_cluster_callback_attr_write(attribute_id);
    }
#endif
}

void init_global_attr_write_callback() {
    hal_zigbee_register_on_attribute_change_callback(zigbee_on_attr_change);
}
