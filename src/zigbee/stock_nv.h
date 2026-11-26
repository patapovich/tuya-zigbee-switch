#ifndef _STOCK_NV_H_
#define _STOCK_NV_H_

#include <stdint.h>

/* The NV of the factory Zigbee firmware (the Tuya ZTU module: Telink SDK NV on its 1 MB flash,
 * the SDK's 1 MB layout, drv_nv.h FLASH_CAP_SIZE_1M): module id at STOCK_NV_BASE + id *
 * STOCK_NV_MODULE_BYTES, two sectors each, 4 KB but the key pairs' 16 KB (STOCK_NV_KEYPAIR:
 * 0xE6000 and 0xEA000); a sector header (u16 used flag, u8 module id, u8 op sector), an index
 * (u32 absolute offset, u16 size, u8 item id, u8 state; 512 B with the header, 1 KB for the
 * address table and OTA, 3 KB for the key pairs) and the items (u32 check, u16 size, u8 item id,
 * u8 check kind, then the payload). A conversion to this firmware moves only the image
 * (ensure_ota_scheme.c): the factory NV is still there, and the network the module was on is
 * read from it once (telink/hal/zigbee_network.c). */
#define STOCK_NV_BASE                    0x000D8000u
#define STOCK_NV_MODULE_BYTES            0x2000u
#define STOCK_NV_SECTOR_BYTES            0x1000u
#define STOCK_NV_KEYPAIR                 7u
#define STOCK_NV_KEYPAIR_SECTOR_BYTES    0x4000u
/* stock_nv_diag_t.flash_cap of a 1 MB chip (flash MID >> 16): the factory NV is past 512 KB. */
#define STOCK_NV_FLASH_CAP_1M            0x14u

typedef void (*stock_nv_read_t)(uint32_t addr, uint32_t len, uint8_t *buf);

/* What the start-up found (stock_nv_diag_t.result). */
#define STOCK_NV_OWN_NETWORK      0 /* this firmware's NV is on a network: not looked at */
#define STOCK_NV_NOT_1MB          1 /* the chip has no flash up there */
#define STOCK_NV_NO_ZB_INFO       2 /* no intact factory network information */
#define STOCK_NV_NO_SECURITY      3 /* ... but no intact security material */
#define STOCK_NV_IMPLAUSIBLE      4 /* found, but not on a network (or no PAN / address), or its
                                    * security material does not check out (stock_nv_take_ss) */
#define STOCK_NV_TAKEN            5 /* taken into this firmware's NV at this start-up */
#define STOCK_NV_TAKEN_BEFORE     6 /* the marker says it was taken before: not again */
#define STOCK_NV_MARKER_IN_USE    7 /* the marker sector holds something else: nothing taken */

/* Debug builds: the module has been on a network of its own since the factory copy was taken
 * (and exchanged a newer link key with the trust centre then), or has left the network since
 * (the coordinator may have dropped it and its key): not taken again (stock_nv_log_scan). */
#define STOCK_NV_STALE            8

/* What is at STOCK_NV_BASE (stock_nv_diag_t.area). */
#define STOCK_NV_AREA_BLANK       0
#define STOCK_NV_AREA_FACTORY     1 /* a factory NV sector */
#define STOCK_NV_AREA_MARKER      2 /* (format 1: the marker was here) */
#define STOCK_NV_AREA_OTHER       3

/* Written when the network was taken (or at a start-up on a network of its own while the
 * factory network is still there: a module converted before the marker), in a sector the
 * factory firmware never uses (blank in the factory dump: Tuya's NV is 0xD8000..0xEFFFF): the
 * factory NV is left as it is. The marker (magic + the diag fields then) stops a second take (a
 * factory reset later must not bring the old network back; debug builds take it again, for
 * tests, unless the log says the module was on a network of its own since: STOCK_NV_STALE), and
 * after it a log of the start-ups and network events (stock_nv_rec_t), until the sector is
 * full. */
#define STOCK_NV_MARKER_ADDR      0x000D0000u
#define STOCK_NV_MARKER_MAGIC     0x32433457u /* "W4C2" */
#define STOCK_NV_LOG_START        32u         /* records from here in the sector */

/* A log record. */
typedef struct __attribute__((packed)) {
    uint8_t  boot;  /* start-up number since the take (1 = it) */
    uint8_t  kind;  /* STOCK_NV_LOG_* */
    uint8_t  a;
    uint8_t  b;
    uint32_t ms;    /* since that start-up */
} stock_nv_rec_t;

#define STOCK_NV_LOG_BOOT         1 /* a = diag result, b = area at STOCK_NV_BASE */
#define STOCK_NV_LOG_BDB_INIT     2 /* a = status, b = joined */
#define STOCK_NV_LOG_BDB_COMM     3 /* a = commissioning status */
#define STOCK_NV_LOG_REJOIN       4 /* a rejoin with back-off started */
#define STOCK_NV_LOG_LEAVE        5 /* a = 1 indication, 2 confirmation */
#define STOCK_NV_LOG_NET          6 /* a = hal network status */
#define STOCK_NV_LOG_RST_FLAG     7 /* the take cleared the factory-reset flag: a = its byte */

/* The factory copy is older than what the module has done since (stock_nv_log_scan: STOCK_NV_
 * STALE): a = 1 joined a network by steering, 2 found on another network at a start-up (another
 * address or PAN than the factory copy), STOCK_NV_LOG_JOIN_LEFT left a network (its own leave,
 * which empties the NV: the coordinator may have dropped the module and its link key). */
#define STOCK_NV_LOG_JOIN         8
#define STOCK_NV_LOG_JOIN_LEFT    3

/* Basic 0xff55 (octet string, ZDMS16-2 builds): the start-up's look at the factory NV. The
 * network fields are the factory copy's (read at this start-up), or, with the marker there,
 * what was taken then. */
typedef struct __attribute__((packed)) {
    uint8_t len;          /* ZCL octet string length byte */
    uint8_t format;       /* 2 */
    uint8_t result;       /* STOCK_NV_* of this start-up */
    uint8_t flash_cap;    /* flash MID >> 16: 0x13 = 512 KB, 0x14 = 1 MB */
    uint8_t area;         /* STOCK_NV_AREA_* at this start-up */
    uint8_t found;        /* intact factory copies: bit 0 network information, 1 security, 2 TC link key;
                           * bit 3: the take found the SDK's factory-reset flag set and cleared it;
                           * bit 4: the take made the TC link key type global: the factory one
                           * was unique with no pair of this trust centre's that checks out, or
                           * no type at all (STOCK_NV_SS_GLOBAL_TCLK);
                           * bit 7: the marker (taken earlier, or the module was on a network
                           * of its own with no marker) */
    uint8_t  on_network;  /* bdbAttr.nodeIsOnANetwork */
    uint8_t  channel;     /* macPib.phyChannelCur */
    uint16_t pan_id;
    uint16_t nwk_addr;
    uint8_t  ext_pan_id[8];
} stock_nv_diag_t;

extern stock_nv_diag_t g_stock_nv_diag;

/* The HAL's flash read, set by the Telink start-up (NULL elsewhere: pages stay empty). */
extern stock_nv_read_t g_stock_nv_flash_read;

#ifdef HK_DIMMER

/* The ids below 32 (a bit each) of the module's items that have a valid index entry for a
 * payload of len bytes (stock_nv_item reads them): for an item whose id differs between the
 * factory firmwares (the manufacturer name, factory_identity.c). HK builds only. */
uint32_t stock_nv_ids_of_size(stock_nv_read_t read, uint8_t module, uint16_t len);

#endif

#ifdef MCU_FLASHER_DUMP

/* Debug builds (MCU_FLASHER_DUMP): the log read 5 records at a time. A write of the page
 * attribute (Basic 0xff56) fills the page attribute (0xff57) from the flash. */
#define STOCK_NV_LOG_PAGE_RECS    5
typedef struct __attribute__((packed)) {
    uint8_t        len;    /* ZCL octet string length byte */
    uint8_t        page;
    uint16_t       total;  /* records in the log */
    stock_nv_rec_t rec[STOCK_NV_LOG_PAGE_RECS];
} stock_nv_log_page_t;

extern uint8_t             g_stock_nv_log_page_no;
extern stock_nv_log_page_t g_stock_nv_log_page;

/* Debug builds: pages 0x80 and up are raw items instead of log records, 40 bytes a page:
 * page 0x80 + 0x10 * which + chunk; which 0 / 1 = this firmware's / the factory network
 * information, 2 / 3 = security material, 4 / 5 = TC link key; total = the item's size (0:
 * not there). The Telink start-up sets the reader (NULL elsewhere). */
#define STOCK_NV_RAW_PAGE     0x80u
#define STOCK_NV_RAW_BYTES    40u
typedef uint16_t (*stock_nv_raw_t)(uint8_t which, uint8_t *buf, uint16_t max);
extern stock_nv_raw_t g_stock_nv_raw;

/* Debug builds: a write of STOCK_NV_WIPE_MAGIC to Basic 0xff58 empties this firmware's network
 * NV (no leave is sent: the network keeps the device) and restarts, so the next start-up takes
 * the factory network again (a test of the take on a module whose factory NV is intact), unless
 * the module has been on a network of its own or left the network since that factory copy was
 * taken (STOCK_NV_STALE). The Telink start-up sets the hook (NULL elsewhere). */
#define STOCK_NV_WIPE_MAGIC                   0xA5u

/* The same, and the SDK's factory-reset flag set before the restart (CFG_FACTORY_RST_CNT not
 * blank), as a factory firmware's OTA download can leave it on a converted module: a test of
 * the take's clearing of it. */
#define STOCK_NV_WIPE_RST_FLAG_MAGIC          0xA6u

/* 0xA5 / 0xA6 forced: the next take ignores a network of its own since the last one (the bench,
 * on a test module only: after a join since, the trust centre may no longer accept the copy's
 * link key, and joins through the module can fail). Not with a full log (no room to say so). */
#define STOCK_NV_WIPE_FORCE_MAGIC             0xA7u
#define STOCK_NV_WIPE_RST_FLAG_FORCE_MAGIC    0xA8u

/* The hook's kind (the magic - STOCK_NV_WIPE_MAGIC), and its STOCK_NV_LOG_LEAVE record's a:
 * STOCK_NV_LOG_WIPE + the kind (9 the wipe, 10 + the flag, 11 / 12 the same, forced). */
#define STOCK_NV_WIPE_RST_FLAG                1u
#define STOCK_NV_WIPE_FORCE                   2u
#define STOCK_NV_LOG_WIPE                     9u
extern uint8_t g_stock_nv_wipe_cmd;
extern void    (*g_stock_nv_wipe)(uint8_t kind);
void stock_nv_log_fill_page(void);

/* Debug builds: the log after the marker (read: the flash) looked through. *at: where the next
 * record goes (0: the sector is full); *boot: the start-up number of the last record (0: none).
 * Returns 1 if the factory copy is older than a network the module was on since (STOCK_NV_STALE):
 * the marker was written for a network of its own (its head says STOCK_NV_OWN_NETWORK) or a
 * STOCK_NV_LOG_JOIN follows the last take, with no forced wipe after it; also when the sector is
 * full (a join after that would not be in it). */
uint8_t stock_nv_log_scan(stock_nv_read_t read, uint32_t *at, uint8_t *boot);

#endif

/* The newest intact copy of item item_id of module module (both sectors looked at): exactly
 * len payload bytes into buf, its byte sum or CRC checked. 1 = found (buf holds it); 0 =
 * none (buf undefined). */
uint8_t stock_nv_item(stock_nv_read_t read, uint8_t module, uint8_t item_id, uint8_t *buf,
                      uint16_t len);

/* The factory security record (ss_info_base_t, 81 bytes, the pointers 4 bytes) made this
 * firmware's, after checks; pair: the factory TC link key item (ss_dev_pair_set_t, 36 bytes) or
 * NULL. Both SDKs lay the record out alike but byte 0x3E: the factory firmware's has the security
 * level in bits 0-3, "secure all" in bit 4 and the active key slot in bits 5-6, this SDK's 0-2,
 * 3 and 4-5 (and bits 6-7 its global link key flag). Returns 0 if it cannot be taken (STOCK_NV_
 * IMPLAUSIBLE: no trust centre address, a security level other than Zigbee 3.0's 5, no network
 * key with the active sequence number in the active slot), else STOCK_NV_SS_TAKE and the bits
 * below; the record then holds this firmware's byte 0x3E (that slot, "secure all" set, the
 * default global link key) and no pre-configured key type. The pointers are the caller's (this
 * build's key addresses; keyPairSetNew the stack sets at every load). */
#define STOCK_NV_SS_BYTES          81u
#define STOCK_NV_PAIR_BYTES        36u
#define STOCK_NV_SS_TAKE           0x01u
#define STOCK_NV_SS_PAIR           0x02u /* the pair is this trust centre's unique link key: take it too */
#define STOCK_NV_SS_GLOBAL_TCLK    0x04u /* tcLinkKeyType made 1 (global): no such pair */
uint8_t stock_nv_take_ss(uint8_t *ss, const uint8_t *pair);

#endif
