// The SDK's OTA (zigbee/ota/ota.c) for a dimmer_only board, compiled through this
// file in its place (sdk.mk). The module is an OTA client only: it never serves
// an image. The SDK's zcl_otaCb dispatches both directions, and its
// client-to-server branch links the OTA server's handlers and, through them, the
// zcl_ota_*RspSend senders. Here the SDK's callback gets another name and is
// left unreferenced, so --gc-sections drops it with everything only it uses; the
// callback below is the SDK's server-to-client branch, word for word, and answers
// a client-to-server command (which only an OTA server receives) as unsupported,
// as a device without an OTA server does.
#define zcl_otaCb zcl_otaCb_sdk_unused
#include "ota/ota.c"
#undef zcl_otaCb

#ifdef ZCL_OTA
status_t zcl_otaCb(zclIncomingAddrInfo_t *pAddrInfo, u8 cmdId, void *cmdPayload);

status_t zcl_otaCb(zclIncomingAddrInfo_t *pAddrInfo, u8 cmdId, void *cmdPayload)
{
    status_t status = ZCL_STA_UNSUP_CLUSTER_COMMAND;

    if (pAddrInfo->dstEp == g_otaCtx.simpleDesc->endpoint) {
        if (pAddrInfo->dirCluster == ZCL_FRAME_CLIENT_SERVER_DIR) {
            return ZCL_STA_UNSUP_CLUSTER_COMMAND;
        }
        switch (cmdId) {
        case ZCL_CMD_OTA_IMAGE_NOTIFY:
            status = ota_imageNotifyHandler(pAddrInfo, (ota_imageNotify_t *)cmdPayload);
            break;
        case ZCL_CMD_OTA_QUERY_NEXT_IMAGE_RSP:
            status = ota_queryNextImageRspHandler(pAddrInfo, (ota_queryNextImageRsp_t *)cmdPayload);
            break;
        case ZCL_CMD_OTA_IMAGE_BLOCK_RSP:
            status = ota_imageBlockRspHandler(pAddrInfo, (ota_imageBlockRsp_t *)cmdPayload);
            break;
        case ZCL_CMD_OTA_UPGRADE_END_RSP:
            status = ota_upgradeEndRspHandler(pAddrInfo, (ota_upgradeEndRsp_t *)cmdPayload);
            break;
        case ZCL_CMD_OTA_QUERY_DEVICE_SPECIFIC_FILE_RSP:
            status = ota_queryDevSpecFileRspHandler(pAddrInfo, (ota_querySpecFileRsp_t *)cmdPayload);
            break;
        default:
            status = ZCL_STA_UNSUP_CLUSTER_COMMAND;
            break;
        }
    } else {
        // the SDK's callback returns SUCCESS for another endpoint
        status = ZCL_STA_SUCCESS;
    }

    return status;
}
#endif
