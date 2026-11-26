#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#include "zcl_ballast_config.h"
#include "zcl_cover_switch_config.h"
#include "zcl_include.h"
#include "zcl_multistate_input.h"
#include "zcl_window_covering_custom.h"
#include "zcl_onoff_configuration.h"
#ifdef DIMMER_SCENES
#include "zcl_scenes_custom.h"
#endif
#pragma pack(pop)

#include "telink_size_t_hack.h"

#include "hal/timer.h"
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
#ifdef DIMMER_SCENES
    if (cluster_id == ZCL_CLUSTER_GEN_SCENES) {
        return zcl_scenes_custom_register;
    }
#endif
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
#ifdef DIMMER_SCENES
        else if (status == HAL_ZIGBEE_NOT_FOUND) {
            return ZCL_STA_NOT_FOUND;
        } else if (status == HAL_ZIGBEE_INVALID_FIELD) {
            return ZCL_STA_INVALID_FIELD;
        }
#endif
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

#ifdef DIMMER_SCENES

/* The Scenes command being handled (hal_zigbee_send_response answers it) and whether it was
 * answered (no default response then). */
static zclIncoming_t *scene_in_msg;
static u8             scene_answered;

static status_t cmd_callback_scenes(zclIncomingAddrInfo_t *pAddrInfo, u8 cmdId,
                                    void *cmdPayload) {
    zclIncoming_t *pInMsg = cmd_incoming_from_addr_info(pAddrInfo);

    scene_in_msg   = pInMsg;
    scene_answered = 0;
    status_t status = cmd_callback(pAddrInfo->dstEp, ZCL_CLUSTER_GEN_SCENES, cmdId,
                                   pInMsg->pData, pInMsg->dataLen);
    scene_in_msg = NULL;
    return scene_answered && status == ZCL_STA_SUCCESS ? ZCL_STA_CMD_HAS_RESP : status;
}

void hal_zigbee_send_response(uint8_t command_id, const uint8_t *payload, uint8_t payload_len) {
    zclIncoming_t *in = scene_in_msg;

    if (in == NULL)
        return;

    scene_answered = 1;
    if (!UNICAST_MSG(in->msg))
        return; /* ZCL: a command to a group or a broadcast gets no response */

    epInfo_t dst;
    TL_SETSTRUCTCONTENT(dst, 0);
    dst.dstAddrMode       = APS_SHORT_DSTADDR_WITHEP;
    dst.dstAddr.shortAddr = in->addrInfo.srcAddr;
    dst.dstEp             = in->addrInfo.srcEp;
    dst.profileId         = in->addrInfo.profileId;
    zcl_sendCmd(in->addrInfo.dstEp, &dst, ZCL_CLUSTER_GEN_SCENES, command_id, TRUE,
                ZCL_FRAME_SERVER_CLIENT_DIR, TRUE, 0, in->addrInfo.seqNum, payload_len,
                (u8 *)payload);
}

bool hal_zigbee_group_has_endpoint(uint16_t group_id, uint8_t endpoint) {
    return aps_group_search(group_id, endpoint) != NULL;
}

static hal_group_removed_callback_t group_removed_callback = NULL;

void hal_zigbee_register_on_group_removed_callback(hal_group_removed_callback_t callback) {
    group_removed_callback = callback;
}

static hal_network_left_callback_t network_left_callback = NULL;

void hal_zigbee_register_on_network_left_callback(hal_network_left_callback_t callback) {
    network_left_callback = callback;
}

/* Every leave (asked by the network or by us) and every reset to factory new goes through the
 * stack's nv_resetToFactoryNew (patch_sdk/drv_nv.c), which keeps NV_MODULE_APP. */
void telink_zigbee_network_left(void) {
    if (network_left_callback != NULL)
        network_left_callback();
}

/* Remove Group (0x03) / Remove All Groups (0x04) of the Groups cluster, cluster-specific, client
 * to server, not manufacturer-specific: told before the stack takes it. */
static void scene_check_group_removal(apsdeDataInd_t *ind) {
    if (group_removed_callback == NULL || ind->indInfo.cluster_id != ZCL_CLUSTER_GEN_GROUPS ||
        ind->asduLen < 3 || (ind->asdu[0] & 0x0F) != ZCL_FRAME_TYPE_SPECIFIC_CMD)
        return;

    if (ind->asdu[2] == ZCL_CMD_GROUP_REMOVE_GROUP && ind->asduLen >= 5)
        group_removed_callback(ind->indInfo.dst_ep, BUILD_U16(ind->asdu[3], ind->asdu[4]), false);
    else if (ind->asdu[2] == ZCL_CMD_GROUP_REMOVE_ALL_GROUP)
        group_removed_callback(ind->indInfo.dst_ep, 0, true);
}

#endif

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
#ifdef DIMMER_SCENES
    if (cluster_id == ZCL_CLUSTER_GEN_SCENES) {
        return cmd_callback_scenes;
    }
#endif
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
#ifdef DIMMER_SCENES
    scene_check_group_removal((apsdeDataInd_t *)arg);
#endif
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

/* The stack's restored reporting table compares with 0xFF after a restart, so it reports every
 * attribute at once: held until the app knows its state (hal_zigbee_hold_reports). */
static uint8_t  g_reports_held;
static uint32_t g_reports_held_until;

void hal_zigbee_hold_reports(uint8_t hold, uint32_t timeout_ms) {
    g_reports_held       = hold;
    g_reports_held_until = hal_millis() + timeout_ms;
    if (!hold)
        report_handler();
}

void telink_zigbee_report_handler(void) {
    if (g_reports_held) {
        if ((int32_t)(hal_millis() - g_reports_held_until) < 0)
            return;

        g_reports_held = 0;
    }
    report_handler();
}

void hal_zigbee_notify_attribute_changed(uint8_t endpoint, uint16_t cluster_id,
                                         uint16_t attribute_id) {
    telink_zigbee_report_handler(); // Trigger reporting if needed
}

hal_zigbee_status_t hal_zigbee_send_cmd_to_bindings(const hal_zigbee_cmd *cmd) {
    epInfo_t dstEpInfo;

    TL_SETSTRUCTCONTENT(dstEpInfo, 0);

    dstEpInfo.profileId   = HA_PROFILE_ID;
    dstEpInfo.dstAddrMode = APS_DSTADDR_EP_NOTPRESETNT;
#ifdef DIMMER_BINDINGS
    /* Each binding goes out as its own aps_data_request; a group's clears the ack bit. */
    if (cmd->aps_ack)
        dstEpInfo.txOptions = APS_TX_OPT_ACK_TX;
#endif
    zcl_sendCmd(cmd->endpoint, &dstEpInfo, cmd->cluster_id, cmd->command_id,
                cmd->cluster_specific,
                cmd->direction == HAL_ZIGBEE_DIR_CLIENT_TO_SERVER
                  ? ZCL_FRAME_CLIENT_SERVER_DIR
                  : ZCL_FRAME_SERVER_CLIENT_DIR,
                cmd->disable_default_rsp, cmd->manufacturer_code, ZCL_SEQ_NUM,
                cmd->payload_len, (u8 *)cmd->payload);

    return HAL_ZIGBEE_OK;
}

#ifdef DIMMER_BINDINGS
uint16_t hal_zigbee_bound_group(uint8_t endpoint,
                                uint16_t cluster_id) {
    for (u8 i = 0; i < APS_BINDING_TABLE_SIZE; i++) {
        aps_binding_entry_t *b = &g_apsBindingTbl[i];
        if (b->used && b->srcEp == endpoint && b->clusterId == cluster_id &&
            b->dstAddrMode == APS_SHORT_GROUPADDR_NOEP)
            return b->groupAddr;
    }
    return 0;
}

#endif

hal_zigbee_status_t
hal_zigbee_send_report_attr(uint8_t endpoint, uint16_t cluster_id,
                            uint16_t attr_id, uint8_t zcl_type_id,
                            const void *value, uint8_t value_len) {
    if (zb_isDeviceJoinedNwk()) {
        epInfo_t dstEpInfo;
        TL_SETSTRUCTCONTENT(dstEpInfo, 0);

        dstEpInfo.profileId   = HA_PROFILE_ID;
        dstEpInfo.dstAddrMode = APS_DSTADDR_EP_NOTPRESETNT;

        /* With an APS ACK, as the stack's reports (unicast destinations only, up to the limit;
         * no resend stop: its reports are of different Basic attributes) */
        if (zcl_reportAckRoom() > 0)
            dstEpInfo.txOptions = APS_TX_OPT_ACK_TX;

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
