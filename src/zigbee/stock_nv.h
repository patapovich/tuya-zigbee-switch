#ifndef _STOCK_NV_H_
#define _STOCK_NV_H_

#include <stdint.h>

/* The NV of the factory Zigbee firmware (the Tuya ZTU module: Telink SDK NV on its 1 MB flash):
 * module id at STOCK_NV_BASE + id * STOCK_NV_MODULE_BYTES, two 4 KB sectors each; a sector
 * header (u16 used flag, u8 module id, u8 op sector), an index (u32 absolute offset, u16 size,
 * u8 item id, u8 state) and the items (u32 check, u16 size, u8 item id, u8 check kind, then the
 * payload). A conversion to this firmware moves only the image (ensure_ota_scheme.c): the
 * factory NV is still there, and the network the module was on is read from it once
 * (telink/hal/zigbee_network.c). */
#define STOCK_NV_BASE            0x000D8000u
#define STOCK_NV_MODULE_BYTES    0x2000u
#define STOCK_NV_SECTOR_BYTES    0x1000u

typedef void (*stock_nv_read_t)(uint32_t addr, uint32_t len, uint8_t *buf);

/* What the start-up found (stock_nv_diag_t.result). */
#define STOCK_NV_OWN_NETWORK      0 /* this firmware's NV is on a network: not looked at */
#define STOCK_NV_NOT_1MB          1 /* the chip has no flash up there */
#define STOCK_NV_NO_ZB_INFO       2 /* no intact factory network information */
#define STOCK_NV_NO_SECURITY      3 /* ... but no intact security material */
#define STOCK_NV_IMPLAUSIBLE      4 /* found, but not on a network (or no PAN / address) */
#define STOCK_NV_TAKEN            5 /* taken into this firmware's NV at this start-up */
#define STOCK_NV_TAKEN_BEFORE     6 /* the marker says it was taken before: not again */
#define STOCK_NV_MARKER_IN_USE    7 /* the marker sector holds something else: nothing taken */

/* What is at STOCK_NV_BASE (stock_nv_diag_t.area). */
#define STOCK_NV_AREA_BLANK       0
#define STOCK_NV_AREA_FACTORY     1 /* a factory NV sector */
#define STOCK_NV_AREA_MARKER      2 /* (format 1: the marker was here) */
#define STOCK_NV_AREA_OTHER       3

/* Written when the network was taken, in a sector the factory firmware never uses (blank in
 * the factory dump: Tuya's NV is 0xD8000..0xEFFFF): the factory NV is left as it is. The marker
 * (magic + the diag fields then) stops a second take (a factory reset later must not bring
 * the old network back; debug builds take it again, for tests), and after it a log of the
 * start-ups and network events (stock_nv_rec_t), until the sector is full. */
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

#define STOCK_NV_LOG_BOOT        1 /* a = diag result, b = area at STOCK_NV_BASE */
#define STOCK_NV_LOG_BDB_INIT    2 /* a = status, b = joined */
#define STOCK_NV_LOG_BDB_COMM    3 /* a = commissioning status */
#define STOCK_NV_LOG_REJOIN      4 /* a rejoin with back-off started */
#define STOCK_NV_LOG_LEAVE       5 /* a = 1 indication, 2 confirmation */
#define STOCK_NV_LOG_NET         6 /* a = hal network status */

/* Basic 0xff55 (octet string, ZDMS16-2 builds): the start-up's look at the factory NV. The
 * network fields are the factory copy's (read at this start-up), or, with the marker there,
 * what was taken then. */
typedef struct __attribute__((packed)) {
    uint8_t len;          /* ZCL octet string length byte */
    uint8_t format;       /* 1 */
    uint8_t result;       /* STOCK_NV_* of this start-up */
    uint8_t flash_cap;    /* flash MID >> 16: 0x13 = 512 KB, 0x14 = 1 MB */
    uint8_t area;         /* STOCK_NV_AREA_* at this start-up */
    uint8_t found;        /* intact factory copies: bit 0 network information, 1 security, 2 TC link key;
                           * bit 7: the marker (taken earlier) */
    uint8_t  on_network;  /* bdbAttr.nodeIsOnANetwork */
    uint8_t  channel;     /* macPib.phyChannelCur */
    uint16_t pan_id;
    uint16_t nwk_addr;
    uint8_t  ext_pan_id[8];
} stock_nv_diag_t;

extern stock_nv_diag_t g_stock_nv_diag;

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
/* The HAL's flash read, set by the Telink start-up (NULL elsewhere: pages stay empty). */
extern stock_nv_read_t g_stock_nv_flash_read;

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
 * the factory network again (a test of the take on a module whose factory NV is intact). The
 * Telink start-up sets the hook (NULL elsewhere). */
#define STOCK_NV_WIPE_MAGIC    0xA5u
extern uint8_t g_stock_nv_wipe_cmd;
extern void    (*g_stock_nv_wipe)(void);
void stock_nv_log_fill_page(void);

/* The newest intact copy of item item_id of module module (both sectors looked at): exactly
 * len payload bytes into buf, its byte sum or CRC checked. 1 = found (buf holds it); 0 =
 * none (buf undefined). */
uint8_t stock_nv_item(stock_nv_read_t read, uint8_t module, uint8_t item_id, uint8_t *buf,
                      uint16_t len);

#endif
