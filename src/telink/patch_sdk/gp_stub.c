/* Green Power left out of a dimmer_only board (sdk.mk: GP_SOURCES).
 *
 * This firmware never calls gp_init(): no Green Power endpoint or cluster is
 * registered, the GP stub gets no callbacks (gpStubCbInit), the proxy table stays
 * empty and no GP frame is ever sent. What the SDK still links is the stack's side
 * of it: libzb_router.a refers to three functions and one variable of the GP code,
 * which pull its members dGP_stub.o, cGP_stub.o, gp_base.o and gp_sec.o (5.4 KB)
 * and need the SDK's gp.c / gp_proxy.c (0.6 KB). Defining them here, ahead of the
 * library, keeps all of it out of the link.
 *
 * What each one did, read from the 3.7.2.0 library (tc32 disassembly):
 * - cGp_mcpsDataInd(): nwk_data.o hands it a received frame of the GP NWK frame
 *   type, and the buffer with it. The library filters it (freeing what it drops)
 *   and posts the rest to dGp_dataInd(), whose consumers are the callbacks
 *   gp_init() would have registered: here there are none. So the frame is freed
 *   at once.
 * - cGp_dataCnf(): mac_data.o calls it for a confirm whose MSDU handle lies in
 *   the GP range (0x70..0xBF). Only cGp_dataReq() hands out such handles, and
 *   nothing calls it here. The library frees the buffer after the confirm
 *   (cGpDataCnfHandler), so this does too, should it ever be called.
 * - gpDataIndDuplicatePeriodic(): a second_clock.o tick that ages the GP
 *   duplicate table, which stays empty. Nothing to do.
 * - g_gpDeviceAnnounceCheckCb: zdo_deviceAnnounceIndicate() calls it, if set,
 *   for every Device Announce, and drops the announce when it returns TRUE. gp.c
 *   sets it to gpDevAnnceCheckCb(), which returns FALSE while no alias conflict
 *   timer runs and the proxy table is empty: always, here. NULL skips the call
 *   with the same result.
 *
 * The cost: a GP device (Hue Tap and the like) next to a ZDMS16-2 is not proxied
 * by it. Other routers still proxy it. */

#include "tl_common.h"
#include "zb_api.h"

gpDeviceAnnounceCheckCb_t g_gpDeviceAnnounceCheckCb = NULL;

void cGp_mcpsDataInd(void *arg) {
    zb_buf_free((zb_buf_t *)arg);
}

void cGp_dataCnf(void *arg) {
    zb_buf_free((zb_buf_t *)arg);
}

void gpDataIndDuplicatePeriodic(void) {
}
