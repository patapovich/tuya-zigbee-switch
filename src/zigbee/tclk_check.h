#ifndef _TCLK_CHECK_H_
#define _TCLK_CHECK_H_

#include <stdint.h>

/* Does the trust centre still read what this router encrypts with its trust centre link key?
 * A child that joins through the router without the network key (an association, or a trust
 * centre rejoin) gets the key only after the router tells the trust centre (APS Update Device)
 * and the trust centre sends it back through the router (APS Tunnel). With a unique trust
 * centre link key the stack sends that Update Device only APS-encrypted with the key, and a
 * trust centre that no longer holds the same key (it gave the router another one at a later
 * join of its own, a W4c take brought an older one back, the coordinator lost it) drops it
 * without a word: no child joins through the router, and nothing else shows it, since the
 * router's other frames go with the network key. Nothing is sent to find out (a Verify Key
 * would end in the stack's commissioning code, which can take a joined router off the network):
 * after such an Update Device the router waits TCLK_CHECK_WINDOW_MS for a tunnel, and
 * TCLK_CHECK_MISSES windows in a row without one make Basic 0xff5d say NWK_SEC_TC_KEY_STALE
 * (zigbee/nwk_sec.h), reported at once to the Basic cluster's bindings (the converter binds
 * them to the coordinator). Any tunnel, or a join of the router's own (a new key), ends it,
 * reported too. RAM only: a restart starts over.
 * A trust centre that allows no join at that moment drops the Update Devices too, whatever the
 * key: joining allowed through this router alone (Zigbee2MQTT's "permit join" via one device:
 * the coordinator itself does not permit then), or a child the trust centre does not know (a
 * removed device's trust centre rejoins, which go on whether anything permits or not). So an
 * Update Device is watched only while joining is open network-wide: the last Mgmt_Permit_Joining
 * the router got was the coordinator's broadcast with a time (tclk_check_permit_joining), and the
 * router's own permit of it still runs.
 * Router builds with TCLK_CHECK (src/telink/Makefile: not the ZDMS16-2, no room in its image;
 * telink/hal/zigbee_security.c); the stub has it for every board. */
#define TCLK_CHECK_WINDOW_MS    2000   /* the trust centre answers in about 0.1 s, and a joining
                                        * child waits a few seconds for its key */
#define TCLK_CHECK_MISSES       3

/* An Update Device for a child, as the stack sends it: status as the request's (1 an unsecured
 * join, 3 a trust centre rejoin: both wait for the key; 0 a secured rejoin, 2 a child that left:
 * nothing comes back), tc_link_key_type the stack's (1 global: the Update Device goes also with
 * the network key alone, which any trust centre reads, and is not watched; 0 unique: only
 * APS-encrypted; others: the stack sends none, and children cannot join either), permitting
 * whether the router allows joins now. Update Devices while one waits count with it: one miss per
 * window. Native-width arguments (no extension). */
void tclk_check_update_device(unsigned status, unsigned tc_link_key_type, unsigned permitting);

/* A Mgmt_Permit_Joining came: network_wide 1 for the coordinator's broadcast with a time (the
 * trust centre allows joins then too), 0 for anything else (a close; a request to this router
 * alone, or from another router, which leaves the trust centre's own permit unknown). */
void tclk_check_permit_joining(unsigned network_wide);

/* The key works: a tunnel came from the trust centre, or the router joined a network of its own
 * (a new key). */
void tclk_check_ok(void);

#endif
