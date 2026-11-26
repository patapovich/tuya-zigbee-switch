#include "zigbee/stock_nv.h"

#include <string.h>

#include "zigbee/crc32_nibble.h"

stock_nv_diag_t g_stock_nv_diag = { sizeof(stock_nv_diag_t) - 1u, 2, 0, 0, 0, 0, 0, 0, 0, 0,
                                    { 0 } };
stock_nv_read_t g_stock_nv_flash_read;

#ifdef MCU_FLASHER_DUMP
uint8_t             g_stock_nv_log_page_no;
stock_nv_log_page_t g_stock_nv_log_page = { 3, 0, 0, { { 0 } } };
stock_nv_raw_t      g_stock_nv_raw;
uint8_t             g_stock_nv_wipe_cmd;
void (*g_stock_nv_wipe)(uint8_t rst_flag);

void stock_nv_log_fill_page(void) {
    uint32_t magic = 0;
    uint16_t total = 0;
    uint8_t  n     = 0;

    g_stock_nv_log_page.page  = g_stock_nv_log_page_no;
    g_stock_nv_log_page.total = 0;
    g_stock_nv_log_page.len   = 3;
    if (g_stock_nv_log_page_no >= STOCK_NV_RAW_PAGE) {
        static uint8_t raw[192];
        uint8_t        which = (uint8_t)((g_stock_nv_log_page_no - STOCK_NV_RAW_PAGE) >> 4);
        uint16_t       at    = (uint16_t)((g_stock_nv_log_page_no & 0x0Fu) * STOCK_NV_RAW_BYTES);
        uint16_t       size  = g_stock_nv_raw ? g_stock_nv_raw(which, raw, sizeof(raw)) : 0;
        uint16_t       n     = at < size ? (uint16_t)(size - at) : 0;

        if (n > STOCK_NV_RAW_BYTES) {
            n = STOCK_NV_RAW_BYTES;
        }
        memcpy(g_stock_nv_log_page.rec, raw + at, n);
        g_stock_nv_log_page.total = size;
        g_stock_nv_log_page.len   = (uint8_t)(3u + n);
        return;
    }
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

uint8_t stock_nv_log_scan(stock_nv_read_t read, uint32_t *at, uint8_t *boot) {
    uint8_t head_result = 0xFF;
    uint8_t stale;

    /* the head: the magic, then stock_nv_diag_t (its result in the third byte) */
    read(STOCK_NV_MARKER_ADDR + 6u, 1, &head_result);
    stale = head_result == STOCK_NV_OWN_NETWORK;
    *at   = 0;
    *boot = 0;
    for (uint32_t a = STOCK_NV_MARKER_ADDR + STOCK_NV_LOG_START;
         a + sizeof(stock_nv_rec_t) <= STOCK_NV_MARKER_ADDR + STOCK_NV_SECTOR_BYTES;
         a += sizeof(stock_nv_rec_t)) {
        stock_nv_rec_t r;

        read(a, sizeof(r), (uint8_t *)&r);
        if (r.boot == 0xFFu && r.kind == 0xFFu) {
            *at = a;
            return stale;
        }
        *boot = r.boot;
        if (r.kind == STOCK_NV_LOG_JOIN) {
            stale = 1;
        } else if (r.kind == STOCK_NV_LOG_BOOT && r.a == STOCK_NV_TAKEN) {
            stale = 0;   /* taken again since */
        } else if (r.kind == STOCK_NV_LOG_LEAVE && r.a >= STOCK_NV_LOG_WIPE + STOCK_NV_WIPE_FORCE) {
            stale = 0;   /* a forced wipe: the next take is the bench's */
        }
    }
    return 1;   /* full: a later join is not in it */
}

#endif

#define SECT_VALID          0x5A5Au /* NV_SECTOR_VALID */
#define SECT_VALID_CRC      0x7A7Au /* NV_SECTOR_VALID_CHECKCRC */
#define IDX_VALID           0x5Au   /* ITEM_FIELD_VALID */
#define IDX_VALID_SINGLE    0x7Au   /* ITEM_FIELD_VALID_SINGLE */
#define HDR_CHECKSUM        0x5Au   /* ITEM_HDR_FIELD_VALID_CHECKSUM: a byte sum */
#define HDR_CHECKCRC        0x7Au   /* ITEM_HDR_FIELD_VALID_CHECKCRC: CRC-32, low half */
#define IDX_HDR_BYTES       4u
#define IDX_ENTRY_BYTES     8u
#define ITEM_HDR_BYTES      8u

/* The index's entries: (its area - the header) / 8, the 4 bytes left over never written. So
 * 127 in a 1 KB area, and a 512 B one (63) ends at those 4 bytes (0xFFFFFFFF) too; the key
 * pairs' 3 KB, 383. */
#define IDX_MAX             127u
#define IDX_MAX_KEYPAIR     383u

static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* Sector s (0 or 1) of the module, if its header says it is a valid sector of that module: its
 * address (0: not) and *end its end (the SDK's 1 MB layout, stock_nv.h). The key pairs' second
 * sector is where Tuya's own module 9 is while they never moved on: its header names module 9. */
static uint32_t stock_nv_sector(stock_nv_read_t read, uint8_t module, uint8_t s, uint32_t *end) {
    uint32_t bytes = module == STOCK_NV_KEYPAIR ? STOCK_NV_KEYPAIR_SECTOR_BYTES :
                     STOCK_NV_SECTOR_BYTES;
    uint32_t sect = STOCK_NV_BASE + module * STOCK_NV_MODULE_BYTES + s * bytes;
    uint8_t  h[IDX_HDR_BYTES];
    uint16_t flag;

    *end = sect + bytes;
    read(sect, IDX_HDR_BYTES, h);
    flag = (uint16_t)(h[0] | h[1] << 8);
    if ((flag != SECT_VALID && flag != SECT_VALID_CRC) || h[2] != module) {
        return 0;
    }
    return sect;
}

/* The item at `at` (inside its sector: the caller's check): the wanted id and size, its check
 * right. */
static uint8_t item_ok(stock_nv_read_t read, uint32_t at, uint8_t item_id, uint8_t *buf,
                       uint16_t len) {
    uint8_t hdr[ITEM_HDR_BYTES];

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

#ifdef HK_DIMMER
uint32_t stock_nv_ids_of_size(stock_nv_read_t read, uint8_t module, uint16_t len) {
    uint32_t ids = 0;

    for (uint8_t s = 0; s < 2u; s++) {
        uint32_t end;
        uint32_t sect = stock_nv_sector(read, module, s, &end);

        if (!sect) {
            continue;
        }
        for (uint32_t i = 0; i < IDX_MAX; i++) {
            uint8_t e[IDX_ENTRY_BYTES];

            read(sect + IDX_HDR_BYTES + i * IDX_ENTRY_BYTES, IDX_ENTRY_BYTES, e);
            if (rd32(e) == 0xFFFFFFFFu) {
                break;
            }
            /* the index's size is the item's: its header and the payload */
            if ((uint16_t)(e[4] | e[5] << 8) == len + ITEM_HDR_BYTES && e[6] < 32u &&
                (e[7] == IDX_VALID || e[7] == IDX_VALID_SINGLE)) {
                ids |= 1ul << e[6];
            }
        }
    }
    return ids;
}

#endif

uint8_t stock_nv_item(stock_nv_read_t read, uint8_t module, uint8_t item_id, uint8_t *buf,
                      uint16_t len) {
    uint32_t best    = 0;
    uint32_t entries = module == STOCK_NV_KEYPAIR ? IDX_MAX_KEYPAIR : IDX_MAX;

    for (uint8_t s = 0; s < 2u; s++) {
        uint32_t end;
        uint32_t sect = stock_nv_sector(read, module, s, &end);

        if (!sect) {
            continue;
        }
        /* a write appends: the last intact copy is the newest */
        for (uint32_t i = 0; i < entries; i++) {
            uint8_t  e[IDX_ENTRY_BYTES];
            uint32_t off;

            read(sect + IDX_HDR_BYTES + i * IDX_ENTRY_BYTES, IDX_ENTRY_BYTES, e);
            off = rd32(e);
            if (off == 0xFFFFFFFFu) {
                break;
            }
            if (e[6] == item_id && (e[7] == IDX_VALID || e[7] == IDX_VALID_SINGLE) &&
                off >= sect + IDX_HDR_BYTES && off + ITEM_HDR_BYTES + len <= end &&
                item_ok(read, off, item_id, buf, len)) {
                best = off;
            }
        }
    }
    return best != 0 && item_ok(read, best, item_id, buf, len);
}

/* ss_info_base_t and ss_dev_pair_set_t (zigbee/ss/security_service.h), byte offsets */
#define SS_MATERIAL            0x10u   /* 2 x (16-byte key, sequence number, key type) */
#define SS_MATERIAL_BYTES      18u
#define SS_TC_ADDRESS          0x36u
#define SS_BITS                0x3Eu
#define SS_ACTIVE_SEQ          0x3Fu
#define SS_PRECONF_KEY_TYPE    0x40u
#define SS_TCLK_TYPE           0x4Cu
#define PAIR_KEY_ATTR          24u     /* 1 unverified, 2 verified */
#define PAIR_LINK_KEY_TYPE     25u     /* 0 unique, 1 global */
#define SS_LEVEL_ZB30          5u      /* ENC-MIC-32: the level every Zigbee 3.0 network uses */

uint8_t stock_nv_take_ss(uint8_t *ss, const uint8_t *pair) {
    const uint8_t *tc   = ss + SS_TC_ADDRESS;
    unsigned       slot = ss[SS_BITS] >> 5 & 1u;
    const uint8_t *m    = ss + SS_MATERIAL + slot * SS_MATERIAL_BYTES;
    unsigned       key  = 0;
    unsigned       any  = 0;
    unsigned       all  = 0xFF;
    unsigned       r    = STOCK_NV_SS_TAKE;

    for (unsigned i = 0; i < 16u; i++) {
        key |= m[i];
        if (i < 8u) {
            any |= tc[i];
            all &= tc[i];
        }
    }

    /* A level in the factory bits that is not 5 is another layout of the byte, and a slot
     * other than 0 or 1 (bit 6 set) none. The active slot must hold the active network key:
     * the stack secures its own NWK commands only with a key there (ss_keyPreconfigured), and
     * the key must have the active sequence number. A centralised network has a trust centre:
     * none (zero) or the distributed one (all FF) would leave a join through this router
     * without it. */
    if ((ss[SS_BITS] & 0x4Fu) != SS_LEVEL_ZB30 || !key || m[16] != ss[SS_ACTIVE_SEQ] || !any ||
        all == 0xFFu) {
        return 0;
    }

    /* this SDK's bits: level 5, "secure all" (set by its ss_zdoInit() only at a factory-new
     * start-up), the slot, the default global link key (SS_GLB_LK_TC_DEFAULT: bdb_init() points
     * tcLinkKey at this build's); the factory firmware's own pre-configured key type is not the
     * network's (keyPairSetNew, a factory RAM address, zdo_ssInfoInit() sets at every load) */
    ss[SS_BITS]             = (uint8_t)(0x40u | slot << 4 | 0x08u | SS_LEVEL_ZB30);
    ss[SS_PRECONF_KEY_TYPE] = 0;

    /* The trust centre link key: unique (0, after a key exchange) needs this trust centre's
     * key pair, unverified or verified, as the stack looks it up (ss_devKeyPairFind); else the
     * stack sends a joining child's Update Device only encrypted with a key the trust centre
     * does not have, and the join through this router fails without a word. Global (1) sends it
     * also with the network key alone. Another value sends none. */
    if (pair && !memcmp(pair, tc, 8) && pair[PAIR_KEY_ATTR] - 1u < 2u &&
        pair[PAIR_LINK_KEY_TYPE] == 0u) {
        r |= STOCK_NV_SS_PAIR;
    }
    if (ss[SS_TCLK_TYPE] > 1u || (ss[SS_TCLK_TYPE] == 0u && !(r & STOCK_NV_SS_PAIR))) {
        ss[SS_TCLK_TYPE] = 1;
        r |= STOCK_NV_SS_GLOBAL_TCLK;
    }
    return (uint8_t)r;
}
