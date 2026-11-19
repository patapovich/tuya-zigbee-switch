#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#include "zcl_ballast_config.h"
#include "zcl_cover_switch_config.h"
#include "zcl_include.h"
#include "zcl_multistate_input.h"
#include "zcl_window_covering_custom.h"
#include "zcl_onoff_configuration.h"
#pragma pack(pop)

#include "telink_size_t_hack.h"

#include "hal/zigbee.h"
#include "telink_zigbee_hal.h"
#include "zigbee/battery_cluster.h"
#include "zigbee/consts.h"

// Storage for Telink endpoint configuration
static af_simple_descriptor_t endpoint_descriptors[MAX_ENDPOINTS];
static u16 in_clusters[MAX_IN_CLUSTERS];
static u16 out_clusters[MAX_OUT_CLUSTERS];
static zcl_specClusterInfo_t cluster_infos[HAL_ZIGBEE_MAX_CLUSTER_INFOS];
static zclAttrInfo_t         attr_tables[MAX_ATTRS];

/* hal/zigbee_limits.h states the SDK's limit; a negative array size stops the build when the
 * two part (the Telink gcc 4.5 has no _Static_assert). */
typedef char zcl_cluster_limit_matches_the_sdk[
        ZCL_CLUSTER_NUM_MAX == HAL_ZIGBEE_MAX_ZCL_CLUSTERS ? 1 : -1];

// HAL state tracking
static hal_zigbee_endpoint *hal_endpoints = NULL;
static uint8_t hal_endpoints_cnt          = 0;
static hal_attribute_change_callback_t attribute_change_callback = NULL;
static hal_zcl_activity_callback_t     zcl_activity_callback     = NULL;

static cluster_registerFunc_t get_register_func_by_cluster_id(u16 cluster_id) {
    if (cluster_id == ZCL_CLUSTER_GEN_BASIC) {
        return zcl_basic_register;
    }
#ifndef DIMMER_ONLY /* no battery, switch or cover endpoints (config_parser.c) */
    if (cluster_id == ZCL_CLUSTER_GEN_POWER_CFG ||
        cluster_id == ZCL_CLUSTER_POWER_CFG) {
        return zcl_powerCfg_register;
    }
#endif
    if (cluster_id == ZCL_CLUSTER_GEN_IDENTIFY) {
        return zcl_identify_register;
    }
    if (cluster_id == ZCL_CLUSTER_GEN_GROUPS) {
        return zcl_group_register;
    }
    if (cluster_id == ZCL_CLUSTER_GEN_ON_OFF_SWITCH_CONFIG) {
        return zcl_onoff_configuration_register;
    }
    if (cluster_id == ZCL_CLUSTER_GEN_LEVEL_CONTROL) {
        return zcl_level_register;
    }
    if (cluster_id == ZCL_CLUSTER_GEN_ON_OFF) {
        return zcl_onOff_register;
    }

    /* Also on dimmer_only boards: their dimmer endpoints report Open Dimmer's wall switch
     * gestures there (an unregistered cluster has no attributes for the stack: no reports). */
    if (cluster_id == ZCL_CLUSTER_GEN_MULTISTATE_INPUT_BASIC) {
        return zcl_multistate_input_register;
    }
#ifndef DIMMER_ONLY
    if (cluster_id == ZCL_CLUSTER_CLOSURES_WINDOW_COVERING) {
        return zcl_windowCovering_custom_register;
    }
#endif
    if (cluster_id == 0x0301) { // Lighting Ballast Config
        return zcl_ballast_config_register;
    }
#ifndef DIMMER_ONLY
    if (cluster_id == ZCL_CLUSTER_COVER_SWITCH_CONFIG) {
        return zcl_cover_switch_config_register;
    }
#endif
    if (cluster_id == ZCL_CLUSTER_GEN_POLL_CONTROL) {
        return zcl_pollCtrl_register;
    }
    return NULL;
}

static status_t cmd_callback(u8 endpoint, u16 clusterId, u8 cmdId,
                             void *cmdPayload, u16 cmdPayloadLen) {
    hal_zigbee_cluster *cluster = hal_zigbee_find_cluster(
        hal_endpoints, hal_endpoints_cnt, endpoint, clusterId);

    if (cluster && cluster->cmd_callback) {
        hal_zigbee_cmd_result_t status = cluster->cmd_callback(
            endpoint, clusterId, cmdId, cmdPayload, cmdPayloadLen);
        if (status == HAL_ZIGBEE_CMD_PROCESSED) {
            return ZCL_STA_SUCCESS;
        } else if (status == HAL_ZIGBEE_INVALID_VALUE) {
            return ZCL_STA_INVALID_VALUE;
        } else if (status == HAL_ZIGBEE_MALFORMED_COMMAND) {
            return ZCL_STA_MALFORMED_COMMAND;
        } else if (status == HAL_ZIGBEE_ACTION_DENIED) {
            return ZCL_STA_ACTION_DENIED;
        } else if (status == HAL_ZIGBEE_CMD_SKIPPED) {
            return ZCL_STA_UNSUP_CLUSTER_COMMAND;
        }
    }
    return(ZCL_STA_SUCCESS);
}

static zclIncoming_t *cmd_incoming_from_addr_info(zclIncomingAddrInfo_t *pAddrInfo) {
    // Telink passes &zclIncoming_t.addrInfo into clusterAppCb, so recover the
    // enclosing message to access the raw payload bytes and length.
    return (zclIncoming_t *)((char *)pAddrInfo - offsetof(zclIncoming_t, addrInfo));
}

static status_t cmd_callback_on_off(zclIncomingAddrInfo_t *pAddrInfo, u8 cmdId,
                                    void *cmdPayload) {
    zclIncoming_t *pInMsg = cmd_incoming_from_addr_info(pAddrInfo);

    return cmd_callback(pAddrInfo->dstEp, ZCL_CLUSTER_GEN_ON_OFF, cmdId,
                        pInMsg->pData, pInMsg->dataLen);
}

#ifndef DIMMER_ONLY
static status_t cmd_callback_window_covering(zclIncomingAddrInfo_t *pAddrInfo,
                                             u8 cmdId, void *cmdPayload) {
    zclIncoming_t *pInMsg = cmd_incoming_from_addr_info(pAddrInfo);

    return cmd_callback(pAddrInfo->dstEp, ZCL_CLUSTER_CLOSURES_WINDOW_COVERING, cmdId,
                        pInMsg->pData, pInMsg->dataLen);
}

#endif

static status_t cmd_callback_level_control(zclIncomingAddrInfo_t *pAddrInfo,
                                           u8 cmdId, void *cmdPayload) {
    zclIncoming_t *pInMsg = cmd_incoming_from_addr_info(pAddrInfo);

    return cmd_callback(pAddrInfo->dstEp, ZCL_CLUSTER_GEN_LEVEL_CONTROL, cmdId,
                        pInMsg->pData, pInMsg->dataLen);
}

/* Identify (dimmer endpoints with Open Dimmer): the Identify command; the stack answers
 * Identify Query from the IdentifyTime attribute. */
static status_t cmd_callback_identify(zclIncomingAddrInfo_t *pAddrInfo, u8 cmdId,
                                      void *cmdPayload) {
    zclIncoming_t *pInMsg = cmd_incoming_from_addr_info(pAddrInfo);

    return cmd_callback(pAddrInfo->dstEp, ZCL_CLUSTER_GEN_IDENTIFY, cmdId,
                        pInMsg->pData, pInMsg->dataLen);
}

static status_t cmd_callback_poll_control(zclIncomingAddrInfo_t *pAddrInfo,
                                          u8 cmdId, void *cmdPayload) {
    zclIncoming_t *pInMsg = cmd_incoming_from_addr_info(pAddrInfo);

    return cmd_callback(pAddrInfo->dstEp, ZCL_CLUSTER_GEN_POLL_CONTROL, cmdId,
                        pInMsg->pData, pInMsg->dataLen);
}

static cluster_forAppCb_t get_cmd_callback_by_cluster_id(u16 cluster_id) {
    if (cluster_id == ZCL_CLUSTER_GEN_LEVEL_CONTROL) { // Level Control cluster
        return cmd_callback_level_control;
    }
    if (cluster_id == ZCL_CLUSTER_GEN_ON_OFF) { // On/Off cluster
        return cmd_callback_on_off;
    }
#ifndef DIMMER_ONLY
    if (cluster_id == ZCL_CLUSTER_CLOSURES_WINDOW_COVERING) {
        return cmd_callback_window_covering;
    }
#endif
    if (cluster_id == ZCL_CLUSTER_GEN_POLL_CONTROL) {
        return cmd_callback_poll_control;
    }
    if (cluster_id == ZCL_CLUSTER_GEN_IDENTIFY) {
        return cmd_callback_identify;
    }
    return NULL;
}

/* The stack has written what it accepted: the same checks as its
 * zcl_attrWrite() tell which records those were. */
static bool write_record_accepted(u8 endpoint, u16 cluster_id, const zclWriteRec_t *rec) {
    zclAttrInfo_t *attr = zcl_findAttribute(endpoint, cluster_id, rec->attrID);

    return attr != NULL && attr->data != NULL && attr->type == rec->dataType &&
           (attr->access & ACCESS_CONTROL_WRITE);
}

static void zcl_incoming_message_callback(zclIncoming_t *pInHdlrMsg) {
    if (pInHdlrMsg->hdr.cmd == ZCL_CMD_WRITE ||
        pInHdlrMsg->hdr.cmd == ZCL_CMD_WRITE_NO_RSP) {
        zclWriteCmd_t *writeCmd = (zclWriteCmd_t *)pInHdlrMsg->attrCmd;
        if (attribute_change_callback == NULL || writeCmd == NULL) {
            return; /* NULL: the stack could not parse the command */
        }
        u8  endpoint   = pInHdlrMsg->msg->indInfo.dst_ep;
        u16 cluster_id = pInHdlrMsg->msg->indInfo.cluster_id;
        for (u8 i = 0; i < writeCmd->numAttr; i++) {
            /* The stack hands over every record of the command, also the
             * ones it refused (read-only, unknown, wrong type) and left
             * unchanged: those are no change to act on. */
            if (!write_record_accepted(endpoint, cluster_id, &writeCmd->attrList[i])) {
                continue;
            }
            attribute_change_callback(endpoint, cluster_id, writeCmd->attrList[i].attrID);
        }
    }
}

static void af_rx_callback(void *arg) {
    if (zcl_activity_callback != NULL) {
        zcl_activity_callback();
    }
    zcl_rx_handler(arg);
}

void telink_zigbee_hal_zcl_init(hal_zigbee_endpoint *endpoints,
                                uint8_t endpoints_cnt) {
    zcl_init(zcl_incoming_message_callback);
    zcl_reportingTabInit();

    hal_endpoints     = endpoints;
    hal_endpoints_cnt =
        endpoints_cnt < MAX_ENDPOINTS ? endpoints_cnt : MAX_ENDPOINTS;
    if (endpoints_cnt > hal_endpoints_cnt) {
        hal_zigbee_tables_dropped += (uint16_t)(endpoints_cnt - hal_endpoints_cnt);
    }
    af_simple_descriptor_t *endpoint_desc_ptr = endpoint_descriptors;
    u16 *in_cluster_ptr  = in_clusters;
    u16 *out_cluster_ptr = out_clusters;
    zcl_specClusterInfo_t *cluster_info_ptr = cluster_infos;
    zclAttrInfo_t *        attr_table_ptr   = attr_tables;
    /* What the SDK's ZCL cluster list still takes after its own (OTA client, Green Power). */
    uint8_t zcl_room = HAL_ZIGBEE_MAX_ZCL_CLUSTERS - HAL_ZIGBEE_SDK_ZCL_CLUSTERS;

    for (hal_zigbee_endpoint *endpoint = endpoints;
         endpoint < endpoints + hal_endpoints_cnt; endpoint++) {
        endpoint_desc_ptr->endpoint            = endpoint->endpoint;
        endpoint_desc_ptr->app_profile_id      = endpoint->profile_id;
        endpoint_desc_ptr->app_dev_id          = endpoint->device_id;
        endpoint_desc_ptr->app_dev_ver         = endpoint->device_version;
        endpoint_desc_ptr->app_in_cluster_lst  = in_cluster_ptr;
        endpoint_desc_ptr->app_out_cluster_lst = out_cluster_ptr;

        zcl_specClusterInfo_t *endpoint_first_cluster_ptr = cluster_info_ptr;
        for (hal_zigbee_cluster *cluster = endpoint->clusters;
             cluster < endpoint->clusters + endpoint->cluster_count; cluster++) {
            /* A full table: the cluster is not registered (counted), never written past. One
             * with a register function takes an entry of the SDK's ZCL cluster list, unless
             * this endpoint has registered its id already (client or server alike: the SDK
             * refuses the duplicate, zcl.c:188). */
            uint8_t ota         = cluster->cluster_id == ZCL_CLUSTER_OTA;
            uint8_t takes_entry = !ota && get_register_func_by_cluster_id(cluster->cluster_id);
            for (zcl_specClusterInfo_t *p = endpoint_first_cluster_ptr;
                 takes_entry && p < cluster_info_ptr; p++) {
                if (p->clusterId == cluster->cluster_id && p->clusterRegisterFunc) {
                    takes_entry = 0;
                }
            }
            if ((cluster->is_server ? in_cluster_ptr >= in_clusters + MAX_IN_CLUSTERS
                                    : out_cluster_ptr >= out_clusters + MAX_OUT_CLUSTERS) ||
                (!ota && (cluster_info_ptr >= cluster_infos + HAL_ZIGBEE_MAX_CLUSTER_INFOS ||
                          (takes_entry && zcl_room == 0) ||
                          attr_table_ptr + cluster->attribute_count > attr_tables + MAX_ATTRS))) {
                hal_zigbee_tables_dropped++;
                printf("ZCL tables full: cluster 0x%04x of endpoint %d not registered\r\n",
                       cluster->cluster_id, endpoint->endpoint);
                continue;
            }
            if (cluster->is_server) {
                *in_cluster_ptr = cluster->cluster_id;
                in_cluster_ptr++;
                endpoint_desc_ptr->app_in_cluster_count++;
            } else {
                *out_cluster_ptr = cluster->cluster_id;
                out_cluster_ptr++;
                endpoint_desc_ptr->app_out_cluster_count++;
            }
            if (ota) {
                continue;
            }
            if (takes_entry) {
                zcl_room--;
            }
            cluster_info_ptr->clusterId           = cluster->cluster_id;
            cluster_info_ptr->manuCode            = 0;
            cluster_info_ptr->attrTbl             = attr_table_ptr;
            cluster_info_ptr->attrNum             = 0;
            cluster_info_ptr->clusterRegisterFunc =
                get_register_func_by_cluster_id(cluster->cluster_id);
            cluster_info_ptr->clusterAppCb =
                get_cmd_callback_by_cluster_id(cluster->cluster_id);
            for (hal_zigbee_attribute *attr = cluster->attributes;
                 attr < cluster->attributes + cluster->attribute_count; attr++) {
                attr_table_ptr->id     = attr->attribute_id;
                attr_table_ptr->type   = attr->data_type_id;
                attr_table_ptr->access =
                    ACCESS_CONTROL_READ | ACCESS_CONTROL_REPORTABLE;
                if (attr->flag == ATTR_WRITABLE) {
                    attr_table_ptr->access |= ACCESS_CONTROL_WRITE;
                }
                attr_table_ptr->data = attr->value;
                attr_table_ptr++;
                cluster_info_ptr->attrNum++;
            }

            cluster_info_ptr++;
        }
        af_endpointRegister(endpoint->endpoint, endpoint_desc_ptr,
                            af_rx_callback, NULL);
        u8 cluster_count = cluster_info_ptr - endpoint_first_cluster_ptr;
        zcl_register(endpoint->endpoint, cluster_count, endpoint_first_cluster_ptr);

        endpoint_desc_ptr++;
    }
}

void hal_zigbee_notify_attribute_changed(uint8_t endpoint, uint16_t cluster_id,
                                         uint16_t attribute_id) {
    report_handler(); // Trigger reporting if needed
}

hal_zigbee_status_t hal_zigbee_send_cmd_to_bindings(const hal_zigbee_cmd *cmd) {
    epInfo_t dstEpInfo;

    TL_SETSTRUCTCONTENT(dstEpInfo, 0);

    dstEpInfo.profileId   = HA_PROFILE_ID;
    dstEpInfo.dstAddrMode = APS_DSTADDR_EP_NOTPRESETNT;
    zcl_sendCmd(cmd->endpoint, &dstEpInfo, cmd->cluster_id, cmd->command_id,
                cmd->cluster_specific,
                cmd->direction == HAL_ZIGBEE_DIR_CLIENT_TO_SERVER
                  ? ZCL_FRAME_CLIENT_SERVER_DIR
                  : ZCL_FRAME_SERVER_CLIENT_DIR,
                cmd->disable_default_rsp, cmd->manufacturer_code, ZCL_SEQ_NUM,
                cmd->payload_len, (u8 *)cmd->payload);

    return HAL_ZIGBEE_OK;
}

hal_zigbee_status_t
hal_zigbee_send_report_attr(uint8_t endpoint, uint16_t cluster_id,
                            uint16_t attr_id, uint8_t zcl_type_id,
                            const void *value, uint8_t value_len) {
    if (zb_isDeviceJoinedNwk()) {
        epInfo_t dstEpInfo;
        TL_SETSTRUCTCONTENT(dstEpInfo, 0);

        dstEpInfo.profileId   = HA_PROFILE_ID;
        dstEpInfo.dstAddrMode = APS_DSTADDR_EP_NOTPRESETNT;

        zclAttrInfo_t *pAttrEntry;
        pAttrEntry = zcl_findAttribute(endpoint, cluster_id, attr_id);
        zcl_sendReportCmd(endpoint, &dstEpInfo, TRUE, ZCL_FRAME_SERVER_CLIENT_DIR,
                          cluster_id, pAttrEntry->id, pAttrEntry->type,
                          pAttrEntry->data);
    }
    return HAL_ZIGBEE_OK;
}

void hal_zigbee_register_on_attribute_change_callback(
    hal_attribute_change_callback_t callback) {
    attribute_change_callback = callback;
}

void hal_zigbee_register_on_zcl_activity_callback(hal_zcl_activity_callback_t callback) {
    zcl_activity_callback = callback;
}

// Internal interface functions

af_simple_descriptor_t *telink_zigbee_hal_zcl_get_descriptors(void) {
    return endpoint_descriptors;
}
