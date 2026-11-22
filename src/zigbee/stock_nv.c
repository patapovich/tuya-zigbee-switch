#include "zigbee/stock_nv.h"

#include "zigbee/crc32_nibble.h"

stock_nv_diag_t     g_stock_nv_diag = { sizeof(stock_nv_diag_t) - 1u, 2, 0, 0, 0, 0, 0, 0, 0, 0,
                                        { 0 } };
uint8_t             g_stock_nv_log_page_no;
stock_nv_log_page_t g_stock_nv_log_page = { 3, 0, 0, { { 0 } } };
stock_nv_read_t     g_stock_nv_flash_read;

void stock_nv_log_fill_page(void) {
    uint32_t magic = 0;
    uint16_t total = 0;
    uint8_t  n     = 0;

    g_stock_nv_log_page.page  = g_stock_nv_log_page_no;
    g_stock_nv_log_page.total = 0;
    g_stock_nv_log_page.len   = 3;
    if (!g_stock_nv_flash_read) {
        return;
    }
    g_stock_nv_flash_read(STOCK_NV_MARKER_ADDR, 4, (uint8_t *)&magic);
    if (magic != STOCK_NV_MARKER_MAGIC) {
        return;
    }
    for (uint32_t at = STOCK_NV_MARKER_ADDR + STOCK_NV_LOG_START;
         at + sizeof(stock_nv_rec_t) <= STOCK_NV_MARKER_ADDR + STOCK_NV_SECTOR_BYTES;
         at += sizeof(stock_nv_rec_t)) {
        stock_nv_rec_t r;

        g_stock_nv_flash_read(at, sizeof(r), (uint8_t *)&r);
        if (r.boot == 0xFFu && r.kind == 0xFFu) {
            break;
        }
        if (total / STOCK_NV_LOG_PAGE_RECS == g_stock_nv_log_page_no &&
            n < STOCK_NV_LOG_PAGE_RECS) {
            g_stock_nv_log_page.rec[n++] = r;
        }
        total++;
    }
    g_stock_nv_log_page.total = total;
    g_stock_nv_log_page.len   = (uint8_t)(3u + n * sizeof(stock_nv_rec_t));
}

#define SECT_VALID          0x5A5Au /* NV_SECTOR_VALID */
#define SECT_VALID_CRC      0x7A7Au /* NV_SECTOR_VALID_CHECKCRC */
#define IDX_VALID           0x5Au   /* ITEM_FIELD_VALID */
#define IDX_VALID_SINGLE    0x7Au   /* ITEM_FIELD_VALID_SINGLE */
#define HDR_CHECKSUM        0x5Au   /* ITEM_HDR_FIELD_VALID_CHECKSUM: a byte sum */
#define HDR_CHECKCRC        0x7Au   /* ITEM_HDR_FIELD_VALID_CHECKCRC: CRC-32, low half */
#define IDX_HDR_BYTES       4u
#define IDX_ENTRY_BYTES     8u
#define ITEM_HDR_BYTES      8u
#define IDX_MAX             127u    /* the largest index (a 1 KB info area) */

static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* The item at `at` inside the sector: the wanted id and size, its check right. */
static uint8_t item_ok(stock_nv_read_t read, uint32_t at, uint32_t sect, uint8_t item_id,
                       uint8_t *buf, uint16_t len) {
    uint8_t hdr[ITEM_HDR_BYTES];

    if (at < sect + IDX_HDR_BYTES || at + ITEM_HDR_BYTES + len > sect + STOCK_NV_SECTOR_BYTES) {
        return 0;
    }
    read(at, ITEM_HDR_BYTES, hdr);
    if ((uint16_t)(hdr[4] | hdr[5] << 8) != len || hdr[6] != item_id) {
        return 0;
    }
    read(at + ITEM_HDR_BYTES, len, buf);
    if (hdr[7] == HDR_CHECKSUM) {
        uint32_t sum = 0;

        for (uint16_t i = 0; i < len; i++) {
            sum += buf[i];
        }
        return sum == rd32(hdr);
    }
    if (hdr[7] == HDR_CHECKCRC) {
        /* nv_write_item: the payload, then size, item id and kind; the low half */
        uint32_t crc = xcrc32(buf, (int)len, 0xFFFFFFFFu);

        crc = xcrc32(hdr + 4, 4, crc);
        return (crc & 0xFFFFu) == (rd32(hdr) & 0xFFFFu);
    }
    return 0;
}

uint8_t stock_nv_item(stock_nv_read_t read, uint8_t module, uint8_t item_id, uint8_t *buf,
                      uint16_t len) {
    uint32_t best      = 0;
    uint32_t best_sect = 0;

    for (uint32_t s = 0; s < STOCK_NV_MODULE_BYTES / STOCK_NV_SECTOR_BYTES; s++) {
        uint32_t sect = STOCK_NV_BASE + module * STOCK_NV_MODULE_BYTES + s * STOCK_NV_SECTOR_BYTES;
        uint8_t  h[IDX_HDR_BYTES];
        uint16_t flag;

        read(sect, IDX_HDR_BYTES, h);
        flag = (uint16_t)(h[0] | h[1] << 8);
        if ((flag != SECT_VALID && flag != SECT_VALID_CRC) || h[2] != module) {
            continue;
        }
        /* a write appends: the last intact copy is the newest */
        for (uint32_t i = 0; i < IDX_MAX; i++) {
            uint8_t  e[IDX_ENTRY_BYTES];
            uint32_t off;

            read(sect + IDX_HDR_BYTES + i * IDX_ENTRY_BYTES, IDX_ENTRY_BYTES, e);
            off = rd32(e);
            if (off == 0xFFFFFFFFu) {
                break;
            }
            if (e[6] == item_id && (e[7] == IDX_VALID || e[7] == IDX_VALID_SINGLE) &&
                item_ok(read, off, sect, item_id, buf, len)) {
                best      = off;
                best_sect = sect;
            }
        }
    }
    return best != 0 && item_ok(read, best, best_sect, item_id, buf, len);
}
