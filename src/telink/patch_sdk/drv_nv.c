#pragma pack(push, 1)
#include "tl_common.h"
#pragma pack(pop)

#include "device_config/reset.h"

#ifdef DIMMER_SCENES
void telink_zigbee_network_left(void); /* hal/zigbee_zcl.c */

#endif
#ifdef LEAVE_RESTART
void telink_zigbee_nv_reset(void); /* hal/zigbee_network.c: was the unit on a network */

#endif

nv_sts_t nv_resetToFactoryNew(void) {
#ifdef LEAVE_RESTART
    telink_zigbee_nv_reset();
#endif
    if (!nv_facrotyNewRstFlagCheck()) {
        nv_facrotyNewRstFlagSet();
    }
#ifdef DIMMER_SCENES
    telink_zigbee_network_left(); /* NV_MODULE_APP stays: what of it was the network's goes */
#endif

    foreach(i, NV_MAX_MODULS) {
        if (i != NV_MODULE_NWK_FRAME_COUNT && i != NV_MODULE_APP) {
            nv_resetModule(i);
        }
    }

    nv_facrotyNewRstFlagClear();
    return NV_SUCC;
}
