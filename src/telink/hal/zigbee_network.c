#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#include "zcl_include.h"
#pragma pack(pop)

#include "ota.h"

#include "telink_size_t_hack.h"

#include "hal/timer.h"
#include "hal/zigbee.h"
#include "telink_zigbee_hal.h"
#include "version_cfg.h"
#include "zigbee/stock_nv.h"

// Forward definitions
void bdb_init_callback(u8 status, u8 joinedNetwork);
void bdb_commissioning_callback(u8 status, void *arg);
void bdb_identify_callback(u8 endpoint, u16 srcAddr, u16 identifyTime);

void zdo_leave_indication_callback(nlme_leave_ind_t *pLeaveInd);
void zdo_leave_confirmation_callback(nlme_leave_cnf_t *pLeaveCnf);

// Network status tracking
static hal_network_status_change_callback_t network_status_change_callback =
    NULL;
static bool steeringInProgress = 0;
static void stock_nv_log(uint8_t kind, uint8_t a, uint8_t b);

// Telink ZDO callbacks
zdo_appIndCb_t zdo_callbacks = {
    bdb_zdoStartDevCnf,              // start device cnf cb
    NULL,                            // reset cnf cb
    NULL,                            // device announce indication cb
    zdo_leave_indication_callback,   // leave ind cb
    zdo_leave_confirmation_callback, // leave cnf cb
    NULL,                            // nwk update ind cb
    NULL,                            // permit join ind cb
    NULL,                            // nlme sync cnf cb
    NULL,                            // tc join ind cb
    NULL,                            // tc detects that the frame counter is near limit
};

// Telink BDB storage
static bdb_commissionSetting_t bdb_commission_setting = {
    .linkKey.tcLinkKey.keyType = SS_GLOBAL_LINK_KEY,
    .linkKey.tcLinkKey.key     = (u8 *)tcLinkKeyCentralDefault,

    .linkKey.distributeLinkKey.keyType = MASTER_KEY,
    .linkKey.distributeLinkKey.key     = (u8 *)linkKeyDistributedMaster,

    .linkKey.touchLinkKey.keyType = MASTER_KEY,
    .linkKey.touchLinkKey.key     = (u8 *)touchLinkKeyMaster,

    .touchlinkEnable       =                              0,
    .touchlinkChannel      = DEFAULT_CHANNEL,
    .touchlinkLqiThreshold =                           0xA0,
};

static bdb_appCb_t device_bdb_cb = {
    bdb_init_callback,
    bdb_commissioning_callback,
    bdb_identify_callback,
    NULL,
};

static void notify_about_network_status_change() {
    stock_nv_log(STOCK_NV_LOG_NET, (uint8_t)hal_zigbee_get_network_status(), 0);
    if (network_status_change_callback != NULL) {
        network_status_change_callback(hal_zigbee_get_network_status());
    }
}

void zdo_leave_indication_callback(nlme_leave_ind_t *pLeaveInd) {
    stock_nv_log(STOCK_NV_LOG_LEAVE, 1, 0);
}

void zdo_leave_confirmation_callback(nlme_leave_cnf_t *pLeaveCnf) {
    stock_nv_log(STOCK_NV_LOG_LEAVE, 2, 0);
    notify_about_network_status_change();
}

void bdb_init_callback(u8 status, u8 joinedNetwork) {
    stock_nv_log(STOCK_NV_LOG_BDB_INIT, status, joinedNetwork);
    if (status == BDB_INIT_STATUS_SUCCESS) {
        if (joinedNetwork) {
            ota_queryStart(OTA_QUERY_INTERVAL);
      #ifdef ZB_ED_ROLE
            zb_setPollRate(POLL_RATE);
      #endif
        }
    } else {
        if (joinedNetwork) {
            stock_nv_log(STOCK_NV_LOG_REJOIN, 1, 0);
            zb_rejoinReqWithBackOff(zb_apsChannelMaskGet(), g_bdbAttrs.scanDuration);
        }
    }
    notify_about_network_status_change();
}

void bdb_commissioning_callback(u8 status, void *arg) {
    printf("BDB commissioning callback, status: %d\r\n", status);
    stock_nv_log(STOCK_NV_LOG_BDB_COMM, status, 0);
    switch (status) {
    case BDB_COMMISSION_STA_SUCCESS:
        ota_queryStart(OTA_QUERY_INTERVAL);
#ifdef ZB_ED_ROLE
        // Need set poll rate manually,
        // to avoid bugs related to no poll task
        // after fast re-connect.
        zb_setPollRate(POLL_RATE);
        printf("Set poll rate to %d\r\n", POLL_RATE);
#endif
        steeringInProgress = 0;
        break;
    case BDB_COMMISSION_STA_IN_PROGRESS:
        break;
    case BDB_COMMISSION_STA_NOT_AA_CAPABLE:
        break;
    case BDB_COMMISSION_STA_NO_NETWORK:
    case BDB_COMMISSION_STA_TCLK_EX_FAILURE:
    case BDB_COMMISSION_STA_TARGET_FAILURE: {
        steeringInProgress = 0;
    } break;
    case BDB_COMMISSION_STA_FORMATION_FAILURE:
        break;
    case BDB_COMMISSION_STA_NO_IDENTIFY_QUERY_RESPONSE:
        break;
    case BDB_COMMISSION_STA_BINDING_TABLE_FULL:
        break;
    case BDB_COMMISSION_STA_NOT_PERMITTED:
        break;
    case BDB_COMMISSION_STA_NO_SCAN_RESPONSE:
    case BDB_COMMISSION_STA_PARENT_LOST:
        stock_nv_log(STOCK_NV_LOG_REJOIN, 2, status);
        zb_rejoinReqWithBackOff(zb_apsChannelMaskGet(), g_bdbAttrs.scanDuration);
        break;
    case BDB_COMMISSION_STA_REJOIN_FAILURE:
        if (!zb_isDeviceFactoryNew()) {
            stock_nv_log(STOCK_NV_LOG_REJOIN, 3, status);
            zb_rejoinReqWithBackOff(zb_apsChannelMaskGet(), g_bdbAttrs.scanDuration);
        }
        break;
    default:
        break;
    }
    notify_about_network_status_change();
}

void bdb_identify_callback(u8 endpoint, u16 srcAddr, u16 identifyTime) {
    // Not implemented
}

hal_zigbee_network_status_t hal_zigbee_get_network_status(void) {
    if (zb_isDeviceJoinedNwk()) {
        return HAL_ZIGBEE_NETWORK_JOINED;
    }
    if (steeringInProgress) {
        return HAL_ZIGBEE_NETWORK_JOINING;
    }
    return HAL_ZIGBEE_NETWORK_NOT_JOINED;
}

void hal_register_on_network_status_change_callback(
    hal_network_status_change_callback_t callback) {
    network_status_change_callback = callback;
    notify_about_network_status_change();
}

void hal_zigbee_leave_network(void) {
    nlme_leave_req_t leaveReq;

    TL_SETSTRUCTCONTENT(leaveReq, 0);
    leaveReq.removeChildren = 1;
    leaveReq.rejoin         = 0;
    zb_nlmeLeaveReq(&leaveReq);
    notify_about_network_status_change();
}

void hal_zigbee_start_network_steering(void) {
    printf("Starting network steering\r\n");
    u8 res = bdb_networkSteerStart();
    if (res == 0) {
        steeringInProgress = 1;
    } else {
        printf("Failed to start network steering, status: %d\r\n", res);
    }
}

hal_zigbee_status_t hal_zigbee_send_announce(void) {
    if (zb_zdoSendDevAnnance() != RET_OK) {
        return HAL_ZIGBEE_ERR_SEND_FAILED;
    }
    return HAL_ZIGBEE_OK;
}

void hal_zigbee_set_poll_rate_ms(uint32_t poll_rate_ms) {
    zb_setPollRate(poll_rate_ms);
}

uint32_t hal_zigbee_get_poll_rate_ms(void) {
    return zb_getPollRate();
}

// Internal interface functions

// A device updated from the stock Tuya firmware starts its outgoing NWK frame
// counter again from zero, but the routers around it still remember the much
// higher counter the stock firmware had reached. NWK security is checked hop by
// hop, and routers running stock Tuya firmware acknowledge such frames at the
// MAC level and then drop them as replays instead of forwarding them. The
// device then loses the coordinator for minutes at a time whenever its route
// runs through one of those routers, while frames sent straight to the
// coordinator still get through (romasku/tuya-zigbee-switch#255).
// Start far above anything a stock firmware can have reached. This happens
// once: the counter lives in flash and only grows from then on.
#define NWK_FRAME_COUNTER_FLOOR    0x10000000u

static void nwk_frame_counter_apply_floor(void) {
    if (ss_ib.outgoingFrameCounter < NWK_FRAME_COUNTER_FLOOR) {
        ss_ib.outgoingFrameCounter = NWK_FRAME_COUNTER_FLOOR;
        nv_nwkFrameCountSaveToFlash(ss_ib.outgoingFrameCounter);
    }
}

/* A module converted from the factory Zigbee firmware keeps its network (W4c). The conversion
 * moves only the image: the factory NV is still there, at Tuya's 1 MB layout (zigbee/stock_nv.h),
 * while this firmware's NV is at the 512 KB layout, so without this the module comes up factory
 * new and joins again only while joining is allowed. When this firmware's NV has no network
 * information and the factory NV has an intact one (same SDK structures, the sizes checked),
 * the network information, the security material (the network key) and the trust centre link
 * key go into this firmware's NV before zb_init(), which then starts on that network as after
 * any restart. The NWK frame counter is not taken: the floor below starts above it. The factory
 * network information is then erased, so a factory reset later never brings it back. */
typedef char   stock_zb_info_is_192_bytes[(sizeof(zb_info_t) == 192) ? 1 : -1];
typedef char   stock_ss_ib_is_81_bytes[(sizeof(ss_info_base_t) == 81) ? 1 : -1];
typedef char   stock_key_pair_is_36_bytes[(sizeof(ss_dev_pair_set_t) == 36) ? 1 : -1];

static void stock_nv_read(uint32_t addr, uint32_t len, uint8_t *buf) {
    flash_read(addr, len, buf);
}

/* The diag fields of a factory copy of the network information. */
static void stock_nv_diag_network(const zb_info_t *info) {
    g_stock_nv_diag.on_network = info->bdbAttr.nodeIsOnANetwork;
    g_stock_nv_diag.channel    = info->macPib.phyChannelCur;
    g_stock_nv_diag.pan_id     = info->nwkNib.panId;
    g_stock_nv_diag.nwk_addr   = info->nwkNib.nwkAddr;
    memcpy(g_stock_nv_diag.ext_pan_id, info->nwkNib.extPANId, sizeof(g_stock_nv_diag.ext_pan_id));
}

/* What is at STOCK_NV_BASE. */
static uint8_t stock_nv_area(void) {
    uint32_t w = 0;

    flash_read(STOCK_NV_BASE, 4, (uint8_t *)&w);
    if ((w & 0xFFFFu) == 0x5A5Au || (w & 0xFFFFu) == 0x7A7Au) {
        return STOCK_NV_AREA_FACTORY;
    }
    return w == 0xFFFFFFFFu ? STOCK_NV_AREA_BLANK : STOCK_NV_AREA_OTHER;
}

/* The marker sector: 1 = the marker, 0 = blank (free), 2 = something else. The log's next
 * free record and this start-up's number when the marker is there. */
static uint32_t g_log_at;    /* 0: no log */
static uint8_t  g_log_boot;

static uint8_t stock_nv_marker(void) {
    uint32_t head[STOCK_NV_LOG_START / 4u];
    uint8_t  blank = 1;

    flash_read(STOCK_NV_MARKER_ADDR, sizeof(head), (uint8_t *)head);
    if (head[0] != STOCK_NV_MARKER_MAGIC) {
        for (uint8_t i = 0; i < sizeof(head) / 4u; i++) {
            blank &= head[i] == 0xFFFFFFFFu;
        }
        return blank ? 0 : 2;
    }
    g_log_boot = 0;
    for (uint32_t at = STOCK_NV_MARKER_ADDR + STOCK_NV_LOG_START;
         at + sizeof(stock_nv_rec_t) <= STOCK_NV_MARKER_ADDR + STOCK_NV_SECTOR_BYTES;
         at += sizeof(stock_nv_rec_t)) {
        stock_nv_rec_t r;

        flash_read(at, sizeof(r), (uint8_t *)&r);
        if (r.boot == 0xFFu && r.kind == 0xFFu) {
            g_log_at = at;
            break;
        }
        g_log_boot = r.boot;
    }
    if (g_log_boot < 0xFEu) {
        g_log_boot++;
    }
    return 1;
}

/* A log record, while the sector has room (none without the marker). */
static void stock_nv_log(uint8_t kind, uint8_t a, uint8_t b) {
    if (!g_log_at ||
        g_log_at + sizeof(stock_nv_rec_t) > STOCK_NV_MARKER_ADDR + STOCK_NV_SECTOR_BYTES) {
        return;
    }
    stock_nv_rec_t r = { g_log_boot, kind, a, b, hal_millis() };

    flash_write_page(g_log_at, sizeof(r), (uint8_t *)&r);
    g_log_at += sizeof(r);
}

static void stock_nv_take_network(void) {
    zb_info_t         info;
    ss_info_base_t    ss;
    ss_dev_pair_set_t pair;
    uint8_t           marker;

    g_stock_nv_flash_read     = stock_nv_read;
    g_stock_nv_diag.flash_cap = (uint8_t)(flash_read_mid() >> 16);
    if (g_stock_nv_diag.flash_cap < FLASH_SIZE_1M) {
        g_stock_nv_diag.result = STOCK_NV_NOT_1MB; /* no factory NV past 512 KB on this chip */
        return;
    }
    g_stock_nv_diag.area = stock_nv_area();
    marker = stock_nv_marker();
    if (marker == 1) {
        g_stock_nv_diag.found |= 0x80u;
    }

    /* the factory copies are looked at (and told) whatever this firmware's NV holds; they are
     * never written or erased */
    uint8_t has_info = g_stock_nv_diag.area == STOCK_NV_AREA_FACTORY &&
                       stock_nv_item(stock_nv_read, NV_MODULE_ZB_INFO, NV_ITEM_ZB_INFO,
                                     (uint8_t *)&info, sizeof(info));
    uint8_t has_ss = has_info && stock_nv_item(stock_nv_read, NV_MODULE_APS, NV_ITEM_APS_SSIB,
                                               (uint8_t *)&ss, sizeof(ss));

    if (has_info) {
        g_stock_nv_diag.found |= 1u;
        stock_nv_diag_network(&info);
    }
    if (has_ss) {
        g_stock_nv_diag.found |= 2u;
    }

    zb_info_t own;

    if (nv_flashReadNew(1, NV_MODULE_ZB_INFO, NV_ITEM_ZB_INFO, sizeof(own), (u8 *)&own) ==
        NV_SUCC) {
        g_stock_nv_diag.result = STOCK_NV_OWN_NETWORK; /* on a network of its own already */
    } else if (!has_info || !has_ss) {
        g_stock_nv_diag.result = has_info ? STOCK_NV_NO_SECURITY : STOCK_NV_NO_ZB_INFO;
    } else if (!info.bdbAttr.nodeIsOnANetwork || info.nwkNib.panId == 0xFFFFu ||
               info.nwkNib.nwkAddr >= 0xFFF8u) {
        g_stock_nv_diag.result = STOCK_NV_IMPLAUSIBLE;
    } else if (marker == 2) {
        g_stock_nv_diag.result = STOCK_NV_MARKER_IN_USE;
#ifndef MCU_FLASHER_DUMP
    } else if (marker == 1) {
        g_stock_nv_diag.result = STOCK_NV_TAKEN_BEFORE; /* debug builds take it again */
#endif
    } else {
        uint8_t has_pair = stock_nv_item(stock_nv_read, NV_MODULE_KEYPAIR, NV_ITEM_SS_KEY_PAIR,
                                         (uint8_t *)&pair, sizeof(pair));

        if (has_pair) {
            g_stock_nv_diag.found |= 4u;
        }
        nv_flashWriteNew(1, NV_MODULE_ZB_INFO, NV_ITEM_ZB_INFO, sizeof(info), (u8 *)&info);
        nv_flashWriteNew(1, NV_MODULE_APS, NV_ITEM_APS_SSIB, sizeof(ss), (u8 *)&ss);
        if (has_pair) {
            nv_flashWriteNew(1, NV_MODULE_KEYPAIR, NV_ITEM_SS_KEY_PAIR, sizeof(pair),
                             (u8 *)&pair);
        }
        g_stock_nv_diag.result = STOCK_NV_TAKEN;
        if (marker == 0) {
            uint32_t head[STOCK_NV_LOG_START / 4u];

            memset(head, 0xFF, sizeof(head));
            head[0] = STOCK_NV_MARKER_MAGIC;
            memcpy(&head[1], &g_stock_nv_diag, sizeof(g_stock_nv_diag));
            flash_write_page(STOCK_NV_MARKER_ADDR, sizeof(head), (uint8_t *)head);
            g_stock_nv_diag.found |= 0x80u;
            stock_nv_marker();   /* the log from here */
        }
    }
    stock_nv_log(STOCK_NV_LOG_BOOT, g_stock_nv_diag.result, g_stock_nv_diag.area);
}

void telink_zigbee_hal_network_init(void) {
    stock_nv_take_network();
    zb_init();
    // zb_init() restores the counter from flash; raise it before anything is
    // sent.
    nwk_frame_counter_apply_floor();
    zb_zdoCbRegister(&zdo_callbacks);
    af_powerDescPowerModeUpdate(POWER_MODE_RECEIVER_COMES_PERIODICALLY);
}

void telink_zigbee_hal_bdb_init(af_simple_descriptor_t *endpoint_descriptor) {
    // BDB init
    // TODO: Support from restore from deep sleep here
    bdb_init(endpoint_descriptor, &bdb_commission_setting, &device_bdb_cb, 1);
}
