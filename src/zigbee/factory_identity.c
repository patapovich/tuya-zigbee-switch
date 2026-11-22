#include "zigbee/factory_identity.h"

#include "device_config/nvm_items.h"
#include "hal/nvm.h"

#include <string.h>

#define FACTORY_IDENTITY_FORMAT    1u

/* NV_ITEM_FACTORY_IDENTITY */
typedef struct __attribute__((packed)) {
    uint8_t format;   /* FACTORY_IDENTITY_FORMAT */
    uint8_t mcu;      /* the factory MCU's packed version; 0: not heard */
    uint8_t name_len; /* 0: no name found */
    char    name[FACTORY_NAME_MAX];
} factory_identity_t;

uint8_t g_factory_identity_attr[1 + FACTORY_IDENTITY_TEXT];

static factory_identity_t g_id;

/* A length 1..32 and printable characters only. */
static uint8_t name_ok(const uint8_t *rec) {
    if (rec[0] == 0 || rec[0] > FACTORY_NAME_MAX) {
        return 0;
    }
    for (uint8_t i = 1; i <= rec[0]; i++) {
        if (rec[i] < 0x20u || rec[i] > 0x7Eu) {
            return 0;
        }
    }
    return 1;
}

static uint8_t put_number(char *at, uint8_t v) {
    uint8_t n = 0;

    if (v >= 10u) {
        at[n++] = (char)('0' + v / 10u);
    }
    at[n++] = (char)('0' + v % 10u);
    return n;
}

static void make_text(void) {
    char *  t = (char *)&g_factory_identity_attr[1];
    uint8_t n = g_id.name_len;

    memcpy(t, g_id.name, n);
    if (g_id.mcu) {
        if (n) {
            t[n++] = ',';
            t[n++] = ' ';
        }
        memcpy(t + n, "MCU ", 4);
        n     += 4u;
        n     += put_number(t + n, (uint8_t)(g_id.mcu >> 6));
        t[n++] = '.';
        n     += put_number(t + n, (uint8_t)((g_id.mcu >> 4) & 3u));
        t[n++] = '.';
        n     += put_number(t + n, (uint8_t)(g_id.mcu & 15u));
    }
    g_factory_identity_attr[0] = n;
}

static void save(void) {
    hal_nvm_write(NV_ITEM_FACTORY_IDENTITY, sizeof(g_id), (uint8_t *)&g_id);
}

void factory_identity_init(stock_nv_read_t read) {
    if (hal_nvm_read(NV_ITEM_FACTORY_IDENTITY, sizeof(g_id), (uint8_t *)&g_id) != HAL_NVM_SUCCESS ||
        g_id.format != FACTORY_IDENTITY_FORMAT || g_id.name_len > FACTORY_NAME_MAX) {
        memset(&g_id, 0, sizeof(g_id));
        g_id.format = FACTORY_IDENTITY_FORMAT;
    }
    if (g_id.name_len == 0 && read) {
        uint8_t rec[FACTORY_NAME_RECORD];

        if (stock_nv_item(read, FACTORY_NV_MODULE, FACTORY_NV_NAME_ITEM, rec, sizeof(rec)) &&
            name_ok(rec)) {
            g_id.name_len = rec[0];
            memcpy(g_id.name, &rec[1], rec[0]);
            save();
        }
    }
    make_text();
}

void factory_identity_note_mcu(uint8_t packed) {
    if (packed == 0 || (packed >> 6) >= 2u || g_id.mcu) {
        return;
    }
    g_id.mcu = packed;
    save();
    make_text();
}
