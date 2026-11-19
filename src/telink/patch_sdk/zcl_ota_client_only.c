// The SDK's OTA cluster (zigbee/zcl/ota_upgrading/zcl_ota.c) for a dimmer_only
// board, compiled through this file in its place (sdk.mk). As in
// ota_client_only.c: the module is an OTA client only, so the parsers of the
// client-to-server commands (query next image, image block / page, upgrade end,
// device specific file requests) are left out. The SDK's zcl_ota_register gets
// another name and is left unreferenced, so --gc-sections drops it with its
// command handler and those parsers; the register below hands the cluster a
// handler that parses the server-to-client commands with the SDK's own
// zcl_ota_serverCmdHandler and answers a client-to-server command as
// unsupported.
#define zcl_ota_register    zcl_ota_register_sdk_unused
#include "zcl/ota_upgrading/zcl_ota.c"
#undef zcl_ota_register

#ifdef ZCL_OTA
static status_t zcl_ota_clientOnlyCmdHandler(zclIncoming_t *pInMsg) {
    if (pInMsg->hdr.frmCtrl.bf.dir == ZCL_FRAME_CLIENT_SERVER_DIR) {
        return ZCL_STA_UNSUP_CLUSTER_COMMAND;
    }
    return zcl_ota_serverCmdHandler(pInMsg);
}

_CODE_ZCL_ status_t zcl_ota_register(u8 endpoint, u16 manuCode, u8 arrtNum,
                                     const zclAttrInfo_t attrTbl[], cluster_forAppCb_t cb) {
    return zcl_registerCluster(endpoint, ZCL_CLUSTER_OTA, manuCode, arrtNum, attrTbl,
                               zcl_ota_clientOnlyCmdHandler, cb);
}

#endif
