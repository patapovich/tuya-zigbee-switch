#ifndef _NWK_SEC_H_
#define _NWK_SEC_H_

#include <stdint.h>

/* Are the module's own NWK commands (link status, route request / reply / record, network
 * status, leave) sent NWK-secured? The Telink stack secures them only while the "secure all" flag
 * of its security record is set (ss_ib.secureAllFresh, besides a security level and a network
 * key); data frames look at the level alone. It sets the flag only at a factory-new start-up and
 * otherwise takes the record from NV as it is, so a 0 saved once (a factory record taken over
 * without normalising it) stays through every restart, leave and join: the module's NWK
 * commands go out unsecured, routers of this firmware and Z-Stack drop them, and the coordinator
 * loses its routes to the module while its data still gets through. The HAL checks the flag at
 * every start-up and after every join or rejoin and puts it back (telink/hal/zigbee_network.c).
 * Basic 0xff5d (enum8, read only; not on Silabs builds, which have no such check) tells what it
 * found: */
#define NWK_SEC_UNSECURED            0 /* sent unsecured: a network without NWK security, or the
                                        * debug builds' test hook */
#define NWK_SEC_SECURED              1
#define NWK_SEC_REPAIRED_AT_START    2 /* this start-up found them unsecured and repaired it */
#define NWK_SEC_REPAIRED_AT_JOIN     3 /* a join or rejoin found them unsecured and repaired it */

/* Router builds with the trust centre link key check (TCLK_CHECK: not the ZDMS16-2): while
 * joining was open network-wide, the trust centre answered none of the last joins of children
 * through the module, so it most likely no longer reads the module's trust centre link key
 * (zigbee/tclk_check.h). It stands in for the state above until the trust centre answers again
 * or the module joins a network of its own; a rejoin keeps it. */
#define NWK_SEC_TC_KEY_STALE         4

extern uint8_t g_nwk_sec_state;

/* A check: the stack's security level and "secure all" flag now (the Telink HAL passes 0 also
 * for an active key slot that is none of the two, which it puts right with the flag); repaired:
 * the state to tell if the flag has to be put back (NWK_SEC_REPAIRED_AT_START or _AT_JOIN). 1:
 * the caller sets the flag (and saves it). A repair stays told until the next start-up. Inline:
 * the HAL's check is its only caller in an image (a function of its own cost the ZDMS16-2 image
 * 32 bytes more). */
static inline uint8_t nwk_sec_check_state(uint8_t level, uint8_t secure_all, uint8_t repaired) {
    if (level == 0) {
        /* a network without NWK security: nothing is secured, nothing to repair */
        g_nwk_sec_state = NWK_SEC_UNSECURED;
        return 0;
    }
    if (secure_all) {
        if (g_nwk_sec_state == NWK_SEC_UNSECURED) {
            g_nwk_sec_state = NWK_SEC_SECURED;
        }
        return 0;
    }
    g_nwk_sec_state = repaired;
    return 1;
}

#ifdef MCU_FLASHER_DUMP

/* Debug builds (Basic 0xff5e, written): the fault made on purpose, for the bench. Each clears the
 * flag and makes the state NWK_SEC_UNSECURED; the attribute reads 0 again after the write. */
#define NWK_SEC_TEST_RAM               0xA1 /* in RAM only: NV keeps the flag, a restart is secured */
#define NWK_SEC_TEST_NV                0xA2 /* in RAM and NV: the next start-up repairs it */
#define NWK_SEC_TEST_RESET             0xA3 /* in RAM, then a factory reset a moment later (the write's
                                             * answer goes out first) that leaves in place, no
                                             * restart: the join after it repairs it */

#define NWK_SEC_TEST_RESET_DELAY_MS    500

extern uint8_t g_nwk_sec_test_cmd;

/* Set by 0xA3: its reset leaves in place, without the restart that follows the module's own
 * leave (device_config/reset.c), so the join after it shows the repair. The next leave confirm
 * clears it. */
extern uint8_t g_nwk_sec_test_keep_running;

/* Basic 0xff5e was written (basic_cluster.c). */
void nwk_sec_test_on_write(void);

#endif

#endif
