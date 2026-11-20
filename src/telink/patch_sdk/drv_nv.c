#pragma pack(push, 1)
#include "tl_common.h"
#pragma pack(pop)

#ifdef DIMMER_SCENES
void telink_zigbee_network_left(void); /* hal/zigbee_zcl.c */

#endif

nv_sts_t nv_resetToFactoryNew(void) {
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
