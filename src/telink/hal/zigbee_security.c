/* The stack's Update Device to the trust centre, its handler of the trust centre's APS Tunnel and
 * its handler of a ZDO Mgmt_Permit_Joining request, wrapped (router builds with TCLK_CHECK:
 * src/telink/Makefile links them with --wrap) for the trust centre link key check
 * (zigbee/tclk_check.h). libzb_router.a calls the first two from other objects only
 * (ss_zdoChildAuthStart and zdo_nlme_leave_indication_cb; aps_command_handle), and the third is
 * called from zdp.c's table (g_zdpClientFunc), so the wrap reaches every call; the end-device
 * library has none of them. */
#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#pragma pack(pop)

#include "telink_size_t_hack.h"

#include <stddef.h>

#include "zigbee/tclk_check.h"

void __real_ss_apsmeUpdateDevReq(void *req);
void __real_ss_apsTunnelCmdHandle(void *buf);
void __real_zdo_mgmtPermitJoinIndicate(void *buf);

#if defined(UPDDEV_PLAIN_COPY) && !defined(MCU_FLASHER_DUMP)
#error "UPDDEV_PLAIN_COPY is for bench builds: MCU_DUMP=1 (src/telink/Makefile)"
#endif

/* The status where the library reads it (ss_apsmeUpdateDevReqSend: byte 18). */
typedef char upd_dev_status_at_18[(offsetof(ss_apsmeUpdateDeviceReq_t, status) == 18) ? 1 : -1];

/* req is the stack's buffer, which the send takes over (tl_bufInitalloc): read first. */
void __wrap_ss_apsmeUpdateDevReq(void *req) {
    uint8_t status = ((const ss_apsmeUpdateDeviceReq_t *)req)->status;
    uint8_t type   = ss_ib.tcLinkKeyType;

#ifdef UPDDEV_PLAIN_COPY
    /* Bench builds (UPDDEV_PLAIN_COPY=1): as with a global key, a copy with the network key
     * alone goes now and the APS-encrypted one 20 ms later, encrypted then, with the type put
     * back: a trust centre that cannot decrypt it may still read the first (IKEA routers send
     * both). Whether Z-Stack takes it from a router it holds a unique key for is not known. */
    ss_ib.tcLinkKeyType = SS_GLOBAL_LINK_KEY;
#endif
    __real_ss_apsmeUpdateDevReq(req);
#ifdef UPDDEV_PLAIN_COPY
    ss_ib.tcLinkKeyType = type;
#endif
    tclk_check_update_device(status, type, zb_getMacAssocPermit());
}

/* The trust centre's answer to an Update Device of this router (the stack passes the key in it
 * on to the child): the trust centre read it. */
void __wrap_ss_apsTunnelCmdHandle(void *buf) {
    tclk_check_ok();
    __real_ss_apsTunnelCmdHandle(buf);
}

/* A Mgmt_Permit_Joining request (the APS indication; its ZDP payload: the sequence number, the
 * time in s, the trust centre significance). Network-wide: the coordinator's broadcast with a
 * time, as Zigbee2MQTT's "permit join" (all) sends it; its "permit join" via one device sends it
 * to that device alone. Read before the handler, which frees the buffer. */
void __wrap_zdo_mgmtPermitJoinIndicate(void *buf) {
    const aps_data_ind_t *ind = (const aps_data_ind_t *)buf;

    tclk_check_permit_joining(ZB_NWK_IS_ADDRESS_BROADCAST(ind->dst_addr) &&
                              ind->src_short_addr == 0x0000u && ind->asdu[1] != 0u);
    __real_zdo_mgmtPermitJoinIndicate(buf);
}
