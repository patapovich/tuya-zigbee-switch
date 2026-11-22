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
#define STOCK_NV_OWN_NETWORK     0 /* this firmware's NV is on a network: not looked at */
#define STOCK_NV_NOT_1MB         1 /* the chip has no flash up there */
#define STOCK_NV_NO_ZB_INFO      2 /* no intact factory network information */
#define STOCK_NV_NO_SECURITY     3 /* ... but no intact security material */
#define STOCK_NV_IMPLAUSIBLE     4 /* found, but not on a network (or no PAN / address) */
#define STOCK_NV_TAKEN           5 /* taken into this firmware's NV at this start-up */

/* What is at STOCK_NV_BASE (stock_nv_diag_t.area). */
#define STOCK_NV_AREA_BLANK      0
#define STOCK_NV_AREA_FACTORY    1 /* a factory NV sector */
#define STOCK_NV_AREA_MARKER     2 /* the marker this firmware leaves when it took the network */
#define STOCK_NV_AREA_OTHER      3

/* Left at STOCK_NV_BASE when the network was taken (the factory sectors erased): never an NV
 * sector again, so nothing is taken twice; it tells what was taken (its diag fields). */
#define STOCK_NV_MARKER_MAGIC    0x31433457u /* "W4C1" */

/* Basic 0xff55 (octet string, ZDMS16-2 builds): the start-up's look at the factory NV. The
 * network fields are the factory copy's (read at this start-up), or, with the marker there,
 * what was taken then. */
typedef struct __attribute__((packed)) {
    uint8_t  len;         /* ZCL octet string length byte */
    uint8_t  format;      /* 1 */
    uint8_t  result;      /* STOCK_NV_* of this start-up */
    uint8_t  flash_cap;   /* flash MID >> 16: 0x13 = 512 KB, 0x14 = 1 MB */
    uint8_t  area;        /* STOCK_NV_AREA_* at this start-up */
    uint8_t  found;       /* intact factory copies: bit 0 network information, 1 security, 2 TC link key */
    uint8_t  on_network;  /* bdbAttr.nodeIsOnANetwork */
    uint8_t  channel;     /* macPib.phyChannelCur */
    uint16_t pan_id;
    uint16_t nwk_addr;
    uint8_t  ext_pan_id[8];
} stock_nv_diag_t;

extern stock_nv_diag_t g_stock_nv_diag;

/* The newest intact copy of item item_id of module module (both sectors looked at): exactly
 * len payload bytes into buf, its byte sum or CRC checked. 1 = found (buf holds it); 0 =
 * none (buf undefined). */
uint8_t stock_nv_item(stock_nv_read_t read, uint8_t module, uint8_t item_id, uint8_t *buf,
                      uint16_t len);

#endif
