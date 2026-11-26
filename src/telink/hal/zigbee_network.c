#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#include "zcl_include.h"
#pragma pack(pop)

#include "ota.h"

#include "telink_size_t_hack.h"

#include <stddef.h>

#include "hal/timer.h"
#include "hal/zigbee.h"
#include "telink_zigbee_hal.h"
#include "version_cfg.h"
#include "zigbee/nwk_sec.h"
#include "zigbee/stock_nv.h"
#include "zigbee/tclk_check.h"
#include "device_config/reset.h"
#include "hal/system.h"

// Forward definitions
void bdb_init_callback(u8 status, u8 joinedNetwork);
void bdb_commissioning_callback(u8 status, void *arg);
void bdb_identify_callback(u8 endpoint, u16 srcAddr, u16 identifyTime);

void zdo_leave_indication_callback(nlme_leave_ind_t *pLeaveInd);
void zdo_leave_confirmation_callback(nlme_leave_cnf_t *pLeaveCnf);
void zdo_nwk_status_indication_callback(zdo_nwk_status_ind_t *pInd);

// Network status tracking
static hal_network_status_change_callback_t network_status_change_callback =
    NULL;
static bool steeringInProgress = 0;

/* The W4c take of a factory network (below): router builds only. Its static asserts hold this
 * SDK's records to the factory firmware's, which are the router library's layout (the network
 * information is not 192 bytes with the end-device library), and every factory firmware taken
 * so far was a router. */
#if defined(MCU_FLASHER_DUMP) && defined(ZB_ROUTER_ROLE)
static void stock_nv_log(uint8_t kind, uint8_t a, uint8_t b);

#else
/* Release builds keep no start-up log: no flash write at every start-up and network event. */
#define stock_nv_log(kind, a, b)    ((void)0)
#endif

// Telink ZDO callbacks
zdo_appIndCb_t zdo_callbacks = {
    bdb_zdoStartDevCnf,                 // start device cnf cb
    NULL,                               // reset cnf cb
    NULL,                               // device announce indication cb
    zdo_leave_indication_callback,      // leave ind cb
    zdo_leave_confirmation_callback,    // leave cnf cb
    NULL,                               // nwk update ind cb
    NULL,                               // permit join ind cb
    NULL,                               // nlme sync cnf cb
    NULL,                               // tc join ind cb
    NULL,                               // tc detects that the frame counter is near limit
    zdo_nwk_status_indication_callback, // nwk status ind cb (SDK 3.7.2.0 and later)
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

/* bdb.c's (no header has it): the start-up's pointer of the trust centre link key by the flag in
 * bits 6-7 of the record's byte 62. */
void bdb_globalLinkKeyFix(bool isFactoryNew);

/* Byte 62 of the record, this SDK's bit fields (securityLevel:3, secureAllFresh:1,
 * activeSecureMaterialIndex:2, reserved:2), read and written whole: smaller code than the bit
 * fields of the packed record one by one. */
#define SS_BYTE_62           (((uint8_t *)&ss_ib)[62])
#define SS_LEVEL             0x07u
#define SS_SECURE_ALL        0x08u
#define SS_SLOT_1            0x10u
#define SS_SLOT_PAST_TWO     0x20u /* slot 2 or 3 */
#define SS_SLOT              0x30u
#define SS_GLK               0xC0u
#define SS_GLK_TC_DEFAULT    (SS_GLB_LK_TC_DEFAULT << 6)
typedef char ss_byte_62_is_the_bits[(offsetof(ss_info_base_t, activeKeySeqNum) == 63) ? 1 : -1];

/* The stack's "secure all" flag (zigbee/nwk_sec.h) checked and, if it is cleared, put back. On a
 * network the record is saved too, whole and as RAM holds it, as every commissioning save does
 * (the keys, their numbers and the counters stay as they are): the start-up after takes it from
 * NV. Called after bdb_init(), whose ss_zdoInit() has restored the record (a factory-new start-up
 * sets the flag itself), and after every join or rejoin, whose commissioning save (if any) has
 * already written the cleared flag. The end-device library restores the record the same way and
 * secures its own NWK commands (leave, rejoin, end device timeout) by the same flag.
 * A record without the flag came from a factory record taken over raw (the first W4c test
 * builds; the debug builds' hook clears the flag alone), which left two more fields of byte 62
 * wrong, put right with it:
 * - the active key slot (bits 4-5) 2 or 3, none of the two: the factory byte's bit 5 is its slot
 *   1 there (stock_nv_take_ss maps it the same), and the stack reads the active key past the
 *   record's two slots (ss_keyPreconfigured, ss_zdoGetNwkKeyBySeqNum). A raw byte keeps such a
 *   slot through a repair of the flag alone, so it counts as the fault too. Slots 0 and 1 are
 *   left as they are (a join puts the network key in one and points the slot at it);
 * - the global link key flag (bits 6-7) 0: without SS_GLB_LK_TC_DEFAULT, which a factory-new
 *   start-up of this firmware sets, the start-up never points the trust centre link key at this
 *   build's (bdb_globalLinkKeyFix), and it keeps the address the record was saved with: the
 *   factory firmware's after a raw take. Set, and the pointer set by it now. */
static void nwk_sec_check(uint8_t repaired) {
    unsigned b = SS_BYTE_62;   /* native width: no extension after each change */

    if (!nwk_sec_check_state(b & SS_LEVEL,
                             (b & (SS_SECURE_ALL | SS_SLOT_PAST_TWO)) == SS_SECURE_ALL,
                             repaired)) {
        return;
    }
    b |= SS_SECURE_ALL;
    if (b & SS_SLOT_PAST_TWO) {
        b = (b & ~SS_SLOT) | SS_SLOT_1;
    }
    if (!(b & SS_GLK)) {
        b |= SS_GLK_TC_DEFAULT;
    }
    SS_BYTE_62 = (uint8_t)b;
    bdb_globalLinkKeyFix(0);   /* as at every start-up on a network: by the flag */
    if (!zb_isDeviceFactoryNew()) {
        zdo_ssInfoSaveToFlash();
    }
}

#ifdef MCU_FLASHER_DUMP
void hal_zigbee_nwk_sec_clear(uint8_t in_nv) {
    ss_ib.secureAllFresh = 0;
    if (in_nv) {
        zdo_ssInfoSaveToFlash();
    }
}

#endif

static void notify_about_network_status_change() {
    stock_nv_log(STOCK_NV_LOG_NET, (uint8_t)hal_zigbee_get_network_status(), 0);
    if (network_status_change_callback != NULL) {
        network_status_change_callback(hal_zigbee_get_network_status());
    }
}

void zdo_leave_indication_callback(nlme_leave_ind_t *pLeaveInd) {
    stock_nv_log(STOCK_NV_LOG_LEAVE, 1, 0);
}

#ifdef LEAVE_RESTART

/* The stack empties the NV in its own leave (zdo_selfLeaveProcessCb) before zb_reset() makes the
 * unit factory new, and also in a reset of a unit that is factory new already (zb_factoryReset),
 * which it then confirms as the same leave, status 0 (zb_factoryResetNotify). This tells them
 * apart for the leave confirm: a network was left (patch_sdk/drv_nv.c calls it). */
static uint8_t g_nv_left_network;

void telink_zigbee_nv_reset(void) {
    if (!zb_isDeviceFactoryNew()) {
        g_nv_left_network = 1;
    }
}

#endif

void zdo_leave_confirmation_callback(nlme_leave_cnf_t *pLeaveCnf) {
    stock_nv_log(STOCK_NV_LOG_LEAVE, 2, 0);
#ifdef LEAVE_RESTART
    /* Debug builds: a network left (a reset, the coordinator's remove) makes a factory copy taken
     * before stale, as a join of its own does: the coordinator may have dropped the module and
     * its link key, and the restart below would take the copy again and come up on that network
     * instead of steering. The wipes 0xA5..0xA8 leave nothing and still take it. */
    if (g_nv_left_network) {
        stock_nv_log(STOCK_NV_LOG_JOIN, STOCK_NV_LOG_JOIN_LEFT, 0);
    }

    /* our own leave (rejoin 0) or reset, done: the restart first, then the status (no steering
     * while it is on its way) */
    restart_after_leave(g_nv_left_network);
    g_nv_left_network = 0;
#endif
    notify_about_network_status_change();
}

/* NWK status indications, as the SDK's sample apps handle them (3.7.2.0 and 4.0.x
 * apps/sampleLight/zb_appCb.c; the library offers no other recovery for either case):
 * - Bad frame counter: a neighbour's frames carry a lower NWK frame counter than the one kept for
 *   it (it started its counter over: a factory reset, a reflash without its NV), so the library
 *   drops each of them as a replay until the network key changes, which a Zigbee2MQTT network
 *   never does. After 10 such drops the kept counter starts over, as in the samples. Unlike the
 *   samples, the drop count starts over with it, so a later start-over needs 10 drops again.
 * - Bad key sequence number: a frame under a network key this module does not have (it missed a
 *   key switch): a rejoin through the trust centre, which sends the current key (4.0.x: an
 *   insecure rejoin where there is a trust centre). At most every 15 minutes; the rejoin mode is
 *   set back to secure (the library's default for a router) once a rejoin has succeeded. */
#define NWK_KEY_REJOIN_GAP_MS    (15u * 60u * 1000u)
static bool     nwk_key_rejoin_insecure;
static bool     nwk_key_rejoin_done;
static uint32_t nwk_key_rejoin_at;

void zdo_nwk_status_indication_callback(zdo_nwk_status_ind_t *pInd) {
    if (pInd->status == NWK_COMMAND_STATUS_BAD_FRAME_COUNTER) {
        tl_zb_normal_neighbor_entry_t *nbe = nwk_neTblGetByShortAddr(pInd->shortAddr);
        if (nbe && nbe->frameCounterFailCnt >= 10 &&
            nbe->receivedFrameCnt < nbe->incomingFrameCnt) {
            nbe->incomingFrameCnt    = 0;
            nbe->frameCounterFailCnt = 0;
        }
    } else if (pInd->status == NWK_COMMAND_STATUS_BAD_KEY_SEQUENCE_NUMBER) {
        if (nwk_key_rejoin_done && hal_millis() - nwk_key_rejoin_at < NWK_KEY_REJOIN_GAP_MS)
            return;

        nwk_key_rejoin_done     = true;
        nwk_key_rejoin_at       = hal_millis();
        nwk_key_rejoin_insecure = !ss_securityModeIsDistributed();
        zb_rejoinSecModeSet(nwk_key_rejoin_insecure ? REJOIN_INSECURITY : REJOIN_SECURITY);
        if (zb_rejoinReq(zb_apsChannelMaskGet(), g_bdbAttrs.scanDuration) != ZDO_SUCCESS &&
            nwk_key_rejoin_insecure) {
            zb_rejoinSecModeSet(REJOIN_SECURITY);   /* not started: the mode as it was */
            nwk_key_rejoin_insecure = false;
        }
    }
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
        nwk_sec_check(NWK_SEC_REPAIRED_AT_JOIN);
        if (steeringInProgress) {
            /* a join of its own (a rejoin tells the same, not steering): a factory copy taken
             * before is older now (STOCK_NV_STALE) */
            stock_nv_log(STOCK_NV_LOG_JOIN, 1, 0);
#ifdef TCLK_CHECK
            /* and the trust centre link key is new (a board that leaves in place, without the
             * restart, kept the old one's check) */
            tclk_check_ok();
#endif
        }
        ota_queryStart(OTA_QUERY_INTERVAL);
#ifdef ZB_ED_ROLE
        // Need set poll rate manually,
        // to avoid bugs related to no poll task
        // after fast re-connect.
        zb_setPollRate(POLL_RATE);
        printf("Set poll rate to %d\r\n", POLL_RATE);
#endif
        steeringInProgress = 0;
        if (nwk_key_rejoin_insecure) {   /* the rejoin for a missed key is done */
            zb_rejoinSecModeSet(REJOIN_SECURITY);
            nwk_key_rejoin_insecure = false;
        }
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
    if (restart_after_leave_pending()) {
        return;   /* no join with the state the leave left: the restart comes first */
    }
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

/* Called right after bdb_init(): ss_zdoInit() in there loads the counter from flash (zb_init()
 * does not; before bdb_init() ss_ib is still zero). A floor before the load wrote 0x10000000 at
 * every start-up, and the load undid it (3 or more entries in the sector: a counter below the
 * floor stayed there) or took it (1 or 2 entries: two start-ups in a row off the network, as a
 * reset and then a power cut before the join, put a higher counter back to 0x10000000, below
 * frames already sent). Above the floor nothing is written here: the SDK's own handling.
 * It goes in twice: nv_nwkFrameCountFromFlash() (SDK drv_nv.c:985-1017) takes a last entry more
 * than 1024 above the one before it for a bad write and goes back to that one + 1024 (one floor
 * entry was undone that way), and takes two equal entries as they are. FLOOR + 1 as the SDK's
 * own saves are 1024 k + 1 (zdo_ssInfoUpdate), so the first periodic save is 1024 above it. */
static void nwk_frame_counter_apply_floor(void) {
    if (ss_ib.outgoingFrameCounter < NWK_FRAME_COUNTER_FLOOR) {
        ss_ib.outgoingFrameCounter = NWK_FRAME_COUNTER_FLOOR + 1u;
        nv_nwkFrameCountSaveToFlash(NWK_FRAME_COUNTER_FLOOR + 1u);
        nv_nwkFrameCountSaveToFlash(NWK_FRAME_COUNTER_FLOOR + 1u);
    }
}

#ifdef ZB_ROUTER_ROLE

/* A module converted from the factory Zigbee firmware keeps its network (W4c). The conversion
 * moves only the image: the factory NV is still there, at Tuya's 1 MB layout (zigbee/stock_nv.h),
 * while this firmware's NV is at the 512 KB layout, so without this the module comes up factory
 * new and joins again only while joining is allowed. When this firmware's NV has no network
 * information and the factory NV has an intact one (same SDK structures, the sizes checked),
 * the network information, the security material (the network key) and the trust centre link
 * key go into this firmware's NV before zb_init(), which then starts on that network as after
 * any restart. The NWK frame counter is not taken: the floor after bdb_init() starts above it
 * (nwk_frame_counter_apply_floor). The factory NV is never written or erased: a marker in a
 * free sector (STOCK_NV_MARKER_ADDR) says it was taken, and a release build never takes it
 * again, so a factory reset later does not bring the old network back. A module converted
 * before the marker came and on a network of its own since gets the marker at a start-up.
 * The security record is never taken as it is: its byte 0x3E has another bit order in the
 * factory firmware, and a record once taken raw (the first W4c test builds) kept the module's
 * own NWK commands unsecured through every later start-up (stock_nv_take_ss checks and maps it;
 * tests/test_stock_nv.py holds the take to it). */
typedef char stock_zb_info_is_192_bytes[(sizeof(zb_info_t) == 192) ? 1 : -1];
typedef char stock_ss_ib_is_81_bytes[(sizeof(ss_info_base_t) == 81) ? 1 : -1];
typedef char stock_key_pair_is_36_bytes[(sizeof(ss_dev_pair_set_t) == 36) ? 1 : -1];
typedef char stock_ss_ib_is_take_ss[(sizeof(ss_info_base_t) == STOCK_NV_SS_BYTES) ? 1 : -1];
typedef char stock_pair_is_take_ss[(sizeof(ss_dev_pair_set_t) == STOCK_NV_PAIR_BYTES) ? 1 : -1];
typedef char stock_key_pairs_are_module_7[(NV_MODULE_KEYPAIR == STOCK_NV_KEYPAIR) ? 1 : -1];

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

/* Either sector of the factory NV's first module (the network information): the factory
 * firmware writes them in turn, so after a rotation the first one may be blank. */
static uint8_t stock_nv_area(void) {
    uint8_t blank = 1;

    for (uint32_t at = STOCK_NV_BASE; at < STOCK_NV_BASE + STOCK_NV_MODULE_BYTES;
         at += STOCK_NV_SECTOR_BYTES) {
        uint32_t w = 0;

        flash_read(at, 4, (uint8_t *)&w);
        if ((w & 0xFFFFu) == 0x5A5Au || (w & 0xFFFFu) == 0x7A7Au) {
            return STOCK_NV_AREA_FACTORY;
        }
        blank &= w == 0xFFFFFFFFu;
    }
    return blank ? STOCK_NV_AREA_BLANK : STOCK_NV_AREA_OTHER;
}

/* The marker sector: 1 = the marker, 0 = blank (free), 2 = something else. Debug builds: the
 * log's next free record, this start-up's number and whether the factory copy is older than a
 * network of its own (stock_nv_log_scan) when the marker is there. */
#ifdef MCU_FLASHER_DUMP
static uint32_t g_log_at;    /* 0: no log */
static uint8_t  g_log_boot;
static uint8_t  g_log_stale;
#endif

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
#ifdef MCU_FLASHER_DUMP
    g_log_stale = stock_nv_log_scan(stock_nv_read, &g_log_at, &g_log_boot);
    if (g_log_boot < 0xFEu) {
        g_log_boot++;
    }
#endif
    return 1;
}

/* The marker, the diag fields as they are now in its head: a release build takes the factory
 * network no more. */
static void stock_nv_marker_write(void) {
    uint32_t head[STOCK_NV_LOG_START / 4u];

    memset(head, 0xFF, sizeof(head));
    head[0] = STOCK_NV_MARKER_MAGIC;
    memcpy(&head[1], &g_stock_nv_diag, sizeof(g_stock_nv_diag));
    flash_write_page(STOCK_NV_MARKER_ADDR, sizeof(head), (uint8_t *)head);
    g_stock_nv_diag.found |= 0x80u;
#ifdef MCU_FLASHER_DUMP
    stock_nv_marker();   /* the log from here */
#endif
}

#ifdef MCU_FLASHER_DUMP
/* Debug builds: a log record, while the sector has room (none without the marker). */
static void stock_nv_log(uint8_t kind, uint8_t a, uint8_t b) {
    if (!g_log_at ||
        g_log_at + sizeof(stock_nv_rec_t) > STOCK_NV_MARKER_ADDR + STOCK_NV_SECTOR_BYTES) {
        return;
    }
    stock_nv_rec_t r = { g_log_boot, kind, a, b, hal_millis() };

    flash_write_page(g_log_at, sizeof(r), (uint8_t *)&r);
    g_log_at += sizeof(r);
}

/* Debug builds: raw items for the log pages (zigbee/stock_nv.h STOCK_NV_RAW_PAGE). */
static uint16_t stock_nv_raw(uint8_t which, uint8_t *buf, uint16_t max) {
    static const struct {
        uint8_t  module;
        uint8_t  item;
        uint16_t size;
    }       k[] = {
        { NV_MODULE_ZB_INFO, NV_ITEM_ZB_INFO,     sizeof(zb_info_t)         },
        { NV_MODULE_APS,     NV_ITEM_APS_SSIB,    sizeof(ss_info_base_t)    },
        { NV_MODULE_KEYPAIR, NV_ITEM_SS_KEY_PAIR, sizeof(ss_dev_pair_set_t) },
    };
    uint8_t i = which >> 1;

    if (i >= sizeof(k) / sizeof(k[0]) || k[i].size > max) {
        return 0;
    }
    if (which & 1u) {
        return stock_nv_item(stock_nv_read, k[i].module, k[i].item, buf, k[i].size) ? k[i].size : 0;
    }
    return nv_flashReadNew(1, k[i].module, k[i].item, k[i].size, buf) == NV_SUCC ? k[i].size : 0;
}

/* Debug builds: the own network NV emptied (nv_resetToFactoryNew keeps the frame counter and
 * NV_MODULE_APP; no leave), then a restart a moment later (the write's answer goes out first).
 * kind STOCK_NV_WIPE_RST_FLAG: the SDK's factory-reset flag is left set (as a factory OTA
 * download can leave it), which zb_init() would take for a factory reset cut short;
 * STOCK_NV_WIPE_FORCE: the next take ignores a network of its own since (its log record says
 * so: stock_nv_log_scan). */
static void stock_nv_wipe(uint8_t kind) {
    nv_resetToFactoryNew();
    if (kind & STOCK_NV_WIPE_RST_FLAG) {
        uint8_t set = 0x00;

        flash_write_page(CFG_FACTORY_RST_CNT, 1, &set);
    }
    stock_nv_log(STOCK_NV_LOG_LEAVE, (uint8_t)(STOCK_NV_LOG_WIPE + kind), 0);
    schedule_reboot(500);
}

#endif

static void stock_nv_take_network(void) {
    zb_info_t         info;
    ss_info_base_t    ss;
    ss_dev_pair_set_t pair;
    uint8_t           marker;

    g_stock_nv_flash_read = stock_nv_read;
#ifdef MCU_FLASHER_DUMP
    g_stock_nv_raw  = stock_nv_raw;
    g_stock_nv_wipe = stock_nv_wipe;
#endif
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

    /* With the marker: a release build never takes it again; a debug build does, for the
     * bench, but not over a network of its own or a leave since (the trust centre's link key) */
#ifdef MCU_FLASHER_DUMP
    uint8_t taken_before = g_log_stale ? STOCK_NV_STALE : 0;
#else
    uint8_t taken_before = STOCK_NV_TAKEN_BEFORE;
#endif

    if (nv_flashReadNew(1, NV_MODULE_ZB_INFO, NV_ITEM_ZB_INFO, sizeof(own), (u8 *)&own) ==
        NV_SUCC) {
        g_stock_nv_diag.result = STOCK_NV_OWN_NETWORK; /* on a network of its own already */
        if (marker == 0 && has_info) {
            /* converted before the marker came, and joined since: the factory network is older
             * than this one, and a release build must not take it after a reset (the restart
             * after the leave would come up on it instead of factory new) */
            stock_nv_marker_write();
        }
#ifdef MCU_FLASHER_DUMP
        /* Debug builds take the factory copy again after a wipe (not after a leave, which logs
         * STOCK_NV_LOG_JOIN_LEFT: zdo_leave_confirmation_callback). On another address or PAN
         * than the copy, the module has joined since (under any build of this firmware, a
         * release one too) and exchanged a new link key with the trust centre, which may hold
         * the copy's no more: the copy must not come back (STOCK_NV_STALE). */
        if (marker == 1 && has_info && !g_log_stale &&
            (own.nwkNib.nwkAddr != info.nwkNib.nwkAddr || own.nwkNib.panId != info.nwkNib.panId ||
             memcmp(own.nwkNib.extPANId, info.nwkNib.extPANId, sizeof(own.nwkNib.extPANId)))) {
            stock_nv_log(STOCK_NV_LOG_JOIN, 2, 0);
            g_log_stale = 1;
        }
#endif
    } else if (!has_info || !has_ss) {
        g_stock_nv_diag.result = has_info ? STOCK_NV_NO_SECURITY : STOCK_NV_NO_ZB_INFO;
    } else if (!info.bdbAttr.nodeIsOnANetwork || info.nwkNib.panId == 0xFFFFu ||
               info.nwkNib.nwkAddr >= 0xFFF8u) {
        g_stock_nv_diag.result = STOCK_NV_IMPLAUSIBLE;
    } else if (marker == 2) {
        g_stock_nv_diag.result = STOCK_NV_MARKER_IN_USE;
    } else if (marker == 1 && taken_before) {
        g_stock_nv_diag.result = taken_before;
    } else {
        uint8_t has_pair = stock_nv_item(stock_nv_read, NV_MODULE_KEYPAIR, NV_ITEM_SS_KEY_PAIR,
                                         (uint8_t *)&pair, sizeof(pair));
        uint8_t take;

        if (has_pair) {
            g_stock_nv_diag.found |= 4u;
        }

        /* What the factory build owns, not the network, becomes this build's: byte 0x3E in
         * this SDK's bit order, no pre-configured key type, a trust centre link key type that
         * the pair taken backs up (stock_nv_take_ss); the key pointers (into the factory
         * firmware's flash) and the commissioning capability. The network key slots, their
         * numbers, the TC address and the counters stay as the factory firmware had them. */
        take = stock_nv_take_ss((uint8_t *)&ss, has_pair ? (const uint8_t *)&pair : NULL);
        if (!take) {
            g_stock_nv_diag.result = STOCK_NV_IMPLAUSIBLE;
            stock_nv_log(STOCK_NV_LOG_BOOT, g_stock_nv_diag.result, g_stock_nv_diag.area);
            return;
        }
        if (take & STOCK_NV_SS_GLOBAL_TCLK) {
            g_stock_nv_diag.found |= 0x10u;
        }
        ss.touchLinkKey      = bdb_commission_setting.linkKey.touchLinkKey.key;
        ss.distributeLinkKey = bdb_commission_setting.linkKey.distributeLinkKey.key;
        ss.tcLinkKey         = bdb_commission_setting.linkKey.tcLinkKey.key;
        info.bdbAttr.nodeCommissioningCapability = g_bdbAttrs.nodeCommissioningCapability;

        /* The SDK's factory-reset flag (CFG_FACTORY_RST_CNT, 0x79000 in this build's 512 KB
         * map) is not this firmware's yet: a factory firmware's OTA client can have downloaded
         * the conversion image over it (the TS110E's stores the image at 0x70000..), and
         * zb_init() takes any byte there but 0xFF for a factory reset cut short and finishes
         * it, emptying the network just taken (the module came up factory new). No reset of
         * this firmware can be pending: its NV has no network. */
        if (nv_facrotyNewRstFlagCheck()) {
            uint8_t flag = 0xFF;

            flash_read(CFG_FACTORY_RST_CNT, 1, &flag);
            nv_facrotyNewRstFlagClear();
            g_stock_nv_diag.found |= 8u;
            stock_nv_log(STOCK_NV_LOG_RST_FLAG, flag, 0);
        }

        nv_flashWriteNew(1, NV_MODULE_ZB_INFO, NV_ITEM_ZB_INFO, sizeof(info), (u8 *)&info);
        nv_flashWriteNew(1, NV_MODULE_APS, NV_ITEM_APS_SSIB, sizeof(ss), (u8 *)&ss);
        if (take & STOCK_NV_SS_PAIR) {
            nv_flashWriteNew(1, NV_MODULE_KEYPAIR, NV_ITEM_SS_KEY_PAIR, sizeof(pair),
                             (u8 *)&pair);
        }
        g_stock_nv_diag.result = STOCK_NV_TAKEN;
        if (marker == 0) {
            stock_nv_marker_write();
        }
    }
    stock_nv_log(STOCK_NV_LOG_BOOT, g_stock_nv_diag.result, g_stock_nv_diag.area);
}

#else
/* End devices: no take, the factory NV is left alone. */
static void stock_nv_take_network(void) {
}

#endif

/* Stack exceptions (sys_exceptionPost, event codes in the SDK's proj/os/ev.h). The SDK's sample
 * apps restart on every one (sys_exceptHandlerRegister -> SYSTEM_RESET); with no handler the
 * stack went on after it, and several leave its state broken: a task the full task queue refused
 * is dropped and its buffer freed though it may still be linked (the MAC's pending frame for a
 * sleepy child stays in its list), a buffer freed twice or outside its pool, a timer that could
 * not be added (a MAC wait without its timeout), a stack overflow, the MAC's own state checks.
 * Those restart the module at once. On the ZDMS16-2 the dimmer chip and its lights go on as
 * they are (Open Dimmer keeps the level). On the ZH-Z06 dimmers (HK_DIMMER) the module's virtual
 * MCU owns the level and its state is not saved here (no flash write from a possibly broken
 * stack, maybe in an IRQ): the warm start keeps the lights' on/off bits (the kept state below),
 * the level comes back as last saved (a minute after a change while lit, a running fade or hold
 * not at all), as after a watchdog reset. The event code is kept in an analog register that keeps
 * its value over a restart (a power cut clears it) and shown as Basic 0xff5c. Every other
 * exception (a refused parameter, a full table, an OTA address check) is left as the stack
 * handles it, as before.
 * The register: DEEP_ANA_REG1, but in the ZH-Z06 dimmer builds (HK_DIMMER) that one holds the
 * light's kept state (hal_system_kept_*, telink/hal/system.c), which tells their warm start from
 * a power-up; those take DEEP_ANA_REG0, which the SDK uses only around an end device's deep sleep
 * (drv_pm.c: written before it, read at bdb_init() with repower 0; these routers call it with 1). */
#ifdef HK_DIMMER
#if PM_ENABLE
#error "HK_DIMMER builds keep the restart exception in DEEP_ANA_REG0, drv_pm's in PM_ENABLE builds"
#endif
#define RESTART_EXCEPTION_ANA_REG    DEEP_ANA_REG0
#else
#define RESTART_EXCEPTION_ANA_REG    DEEP_ANA_REG1
#endif
extern volatile u16 T_evtExcept[4];   /* proj/os/ev.c: [0] line, [1] event */
uint8_t g_restart_exception;

static bool stack_exception_breaks_state(u8 evt) {
    return evt == SYS_EXCEPTTION_COMMON_TIMER_EVEVT ||
           evt == SYS_EXCEPTTION_COMMON_STACK_OVERFLOWN ||
           evt == SYS_EXCEPTTION_COMMON_TASK_POST ||
           (evt >= SYS_EXCEPTTION_ZB_BUFFER_OVERFLOWN && evt <= SYS_EXCEPTTION_ZB_TASK_POST) ||
           (evt >= SYS_EXCEPTTION_ZB_MAC_TX_IRQ && evt <= SYS_EXCEPTTION_ZB_MAC_TRX_TASK) ||
           (evt >= SYS_EXCEPTTION_EV_BUFFER_EXCEPTION_FREE_OVERFLON &&
            evt <= SYS_EXCEPTTION_EV_TASK_POST);
}

static void stack_exception(void) {
    u8 evt = (u8)T_evtExcept[1];

    if (!stack_exception_breaks_state(evt))
        return;

    analog_write(RESTART_EXCEPTION_ANA_REG, evt);
    hal_system_reset();
}

void telink_zigbee_hal_network_init(void) {
    g_restart_exception = analog_read(RESTART_EXCEPTION_ANA_REG);
    sys_exceptHandlerRegister(stack_exception);
    stock_nv_take_network();
    zb_init();
#ifdef LEAVE_RESTART
    /* zb_init() finishes a factory reset cut short (the SDK's flag) before the stack knows
     * whether it is factory new: no network was left at this start-up */
    g_nv_left_network = 0;
#endif
    // SDK 4.0.1.0 and later: 5 s (3.7.2.0: 2 s) for the network key after an association join
    // or a secure rejoin, for a trust centre that is slow to send it.
    TRANSPORT_NETWORK_KEY_WAIT_TIME = 5 * 1000;
    zb_zdoCbRegister(&zdo_callbacks);
    af_powerDescPowerModeUpdate(POWER_MODE_RECEIVER_COMES_PERIODICALLY);
}

void telink_zigbee_hal_bdb_init(af_simple_descriptor_t *endpoint_descriptor) {
    // BDB init
    // TODO: Support from restore from deep sleep here
    bdb_init(endpoint_descriptor, &bdb_commission_setting, &device_bdb_cb, 1);

    /* bdb_init() -> ss_zdoInit() loaded the counter (on a network + 1024, saved) and the rest
     * of the security record. The start it asks for is only queued (router lib:
     * zdo_nlme_start_router_req -> tl_zbPrimitivePost), so nothing has been sent yet; and a
     * raise is never a step back. */
    nwk_frame_counter_apply_floor();
    nwk_sec_check(NWK_SEC_REPAIRED_AT_START);
}
