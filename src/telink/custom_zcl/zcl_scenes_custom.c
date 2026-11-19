/* Scenes cluster (0x0005) registered with our own command handler: the dimmer endpoints' Scenes
 * server lives in src/zigbee/scene_cluster.c (DIMMER_SCENES), not in the SDK's zcl_scene.c
 * (compiled out: ZCL_SCENE_SUPPORT unset). Commands from client to server go to the app
 * callback (the HAL's cmd_callback); anything else is not supported. */
#ifdef DIMMER_SCENES
#pragma pack(push, 1)
#include "zcl_include.h"
#pragma pack(pop)

#include "zcl_scenes_custom.h"

static status_t zcl_scenes_custom_cmd_handler(zclIncoming_t *pInMsg) {
    if (pInMsg->hdr.frmCtrl.bf.dir != ZCL_FRAME_CLIENT_SERVER_DIR || !pInMsg->clusterAppCb)
        return ZCL_STA_UNSUP_CLUSTER_COMMAND;

    return pInMsg->clusterAppCb(&pInMsg->addrInfo, pInMsg->hdr.cmd, NULL);
}

_CODE_ZCL_ status_t zcl_scenes_custom_register(u8 endpoint, u16 manuCode, u8 attrNum,
                                               const zclAttrInfo_t attrTbl[],
                                               cluster_forAppCb_t cb) {
    return(zcl_registerCluster(endpoint, ZCL_CLUSTER_GEN_SCENES, manuCode, attrNum, attrTbl,
                               zcl_scenes_custom_cmd_handler, cb));
}

#endif
