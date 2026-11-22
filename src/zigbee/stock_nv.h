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

/* The newest intact copy of item item_id of module module (both sectors looked at): exactly
 * len payload bytes into buf, its byte sum or CRC checked. 1 = found (buf holds it); 0 =
 * none (buf undefined). */
uint8_t stock_nv_item(stock_nv_read_t read, uint8_t module, uint8_t item_id, uint8_t *buf,
                      uint16_t len);

#endif
