#include "config_nv.h"
#include "hal/nvm.h"
#include "hal/printf_selector.h"
#include "nvm_items.h"
#include <string.h>

#ifdef HAL_SILABS
#include "silabs_config.h"
#endif

#ifndef STRINGIFY
#define _STRINGIFY(x)    #x
#define STRINGIFY(x)     _STRINGIFY(x)
#endif

#ifndef DEFAULT_CONFIG
const char default_config_data[] = "unknown;TS0012-CUSTOM;";
#else
const char default_config_data[] = STRINGIFY(DEFAULT_CONFIG);
#endif

#ifndef DEFAULT_DP_CONFIG
const char default_dp_config_data[] = "";
#else
const char default_dp_config_data[] = STRINGIFY(DEFAULT_DP_CONFIG);
#endif

device_config_str_t device_config_str;
device_config_str_t dp_config_str;
device_config_str_t device_config_ext_str;

void device_config_write_to_nv() {
    printf("Writing config to nv: %s\r\n", device_config_str.data);
    hal_nvm_status_t st = 0;

    printf("Size: %d\r\n", (int)sizeof(device_config_str));
    st = hal_nvm_write(NV_ITEM_DEVICE_CONFIG, sizeof(device_config_str),
                       (uint8_t *)&device_config_str);

    if (st != HAL_NVM_SUCCESS) {
        printf(
            "Failed to write DEVICE_CONFIG_DATA to NV, status: %d. (bytes: %d)\r\n",
            st, device_config_str.size);
    } else {
        printf("success!\r\n");
    }
}

#ifdef SECONDARY_MCU_IMAGE

/* A board that carries an MCU image installs it over two soldered wires (the SW token,
 * src/zigbee/mcu_flasher.c): soldering them is the consent, on by default (user decision
 * 2026-11-20). A config stored before the board's default had the token gets the default's SW
 * token once; NV_ITEM_CONFIG_SWD_DEFAULT marks that done, so a token removed later by hand
 * stays removed. Before the config is parsed: it counts from this start-up on. */
#define SWD_DEFAULT_DONE    0xA5u

/* Where the SW token of the config s (n characters) starts, 0 if it has none (a config never
 * starts with one). No strstr in the Telink build. */
static uint16_t swd_token_at(const uint8_t *s, uint16_t n) {
    for (uint16_t i = 0; i + 2 < n; i++) {
        if (s[i] == ';' && s[i + 1] == 'S' && s[i + 2] == 'W')
            return (uint16_t)(i + 1);
    }
    return 0;
}

static void device_config_swd_default(uint8_t stored) {
    const uint8_t *def  = (const uint8_t *)default_config_data;
    uint16_t       dn   = (uint16_t)(sizeof(default_config_data) - 1);
    uint16_t       at   = swd_token_at(def, dn);
    uint16_t       s    = device_config_str.size;
    uint8_t        done = 0;

    if (hal_nvm_read(NV_ITEM_CONFIG_SWD_DEFAULT, 1, &done) == HAL_NVM_SUCCESS &&
        done == SWD_DEFAULT_DONE)
        return;

    if (stored && at && s > 0 && s < sizeof(device_config_str.data) &&
        !swd_token_at(device_config_str.data, s)) {
        uint16_t n   = 0;
        uint8_t  sep = device_config_str.data[s - 1] != ';';
        while (at + n < dn && def[at + n] != ';')
            n++;
        if (at + n < dn)
            n++;                                  /* with its ';' */
        if (s + sep + n < sizeof(device_config_str.data)) {
            if (sep)
                device_config_str.data[s++] = ';';
            memcpy(device_config_str.data + s, def + at, n);
            s += n;
            device_config_str.data[s] = 0;
            device_config_str.size    = s;
            printf("Config: the board's SWD token added\r\n");
            device_config_write_to_nv();
        }
    }
    done = SWD_DEFAULT_DONE;
    hal_nvm_write(NV_ITEM_CONFIG_SWD_DEFAULT, 1, &done);
}

#endif

void device_config_read_from_nv() {
    hal_nvm_status_t st = 0;

    st = hal_nvm_read(NV_ITEM_DEVICE_CONFIG, sizeof(device_config_str),
                      (uint8_t *)&device_config_str);

    if (st != HAL_NVM_SUCCESS) {
        printf("Failed to read NV_ITEM_DEVICE_CONFIG, using default config "
               "instead, status: %d. (bytes: %d)\r\n",
               st, device_config_str.size);
        memcpy(device_config_str.data, default_config_data,
               sizeof(default_config_data));
        device_config_str.size = strlen((const char *)default_config_data);
    }
#ifdef SECONDARY_MCU_IMAGE
    device_config_swd_default(st == HAL_NVM_SUCCESS);
#endif

    printf("Using config: %d chars from\r\n%s\r\n", device_config_str.size,
           device_config_str.data);
}

void dp_config_write_to_nv() {
    printf("Writing dp config to nv: %s\r\n", dp_config_str.data);
    hal_nvm_status_t st = 0;

    st = hal_nvm_write(NV_ITEM_DP_CONFIG, sizeof(dp_config_str),
                       (uint8_t *)&dp_config_str);

    if (st != HAL_NVM_SUCCESS) {
        printf("Failed to write DP_CONFIG to NV, status: %d. (bytes: %d)\r\n",
               st, dp_config_str.size);
    } else {
        printf("success!\r\n");
    }
}

void dp_config_read_from_nv() {
    hal_nvm_status_t st = 0;

    st = hal_nvm_read(NV_ITEM_DP_CONFIG, sizeof(dp_config_str),
                      (uint8_t *)&dp_config_str);

    if (st != HAL_NVM_SUCCESS) {
        memcpy(dp_config_str.data, default_dp_config_data,
               sizeof(default_dp_config_data));
        dp_config_str.size = strlen((const char *)default_dp_config_data);
    }

    printf("Using dp config: %d chars from\r\n%s\r\n", dp_config_str.size,
           dp_config_str.data);
}

void device_config_ext_write_to_nv() {
    printf("Writing device config ext to nv: %s\r\n", device_config_ext_str.data);
    hal_nvm_status_t st = 0;

    st = hal_nvm_write(NV_ITEM_DEVICE_CONFIG_EXT, sizeof(device_config_ext_str),
                       (uint8_t *)&device_config_ext_str);

    if (st != HAL_NVM_SUCCESS) {
        printf("Failed to write DEVICE_CONFIG_EXT to NV, status: %d. (bytes: %d)\r\n",
               st, device_config_ext_str.size);
    } else {
        printf("success!\r\n");
    }
}

void device_config_ext_read_from_nv() {
    hal_nvm_status_t st = 0;

    st = hal_nvm_read(NV_ITEM_DEVICE_CONFIG_EXT, sizeof(device_config_ext_str),
                      (uint8_t *)&device_config_ext_str);

    if (st != HAL_NVM_SUCCESS) {
        // No overflow ever written: empty is the correct default, not an
        // error -- most devices' config_str fits the single-write limit
        // and never touch this attribute at all.
        device_config_ext_str.data[0] = 0;
        device_config_ext_str.size    = 0;
    }

    printf("Using device config ext: %d chars from\r\n%s\r\n",
           device_config_ext_str.size, device_config_ext_str.data);
}

void device_config_append_ext() {
    if (device_config_ext_str.size == 0) {
        return;
    }

    // -1 leaves room for the NUL terminator written below. Guarded rather
    // than computed unconditionally: device_config_str.size is read back
    // from NV with no upper bound enforced on the way in, and this is
    // unsigned arithmetic -- a size at or past the buffer would otherwise
    // wrap "room" to a huge value instead of correctly leaving none.
    uint16_t buf_len = (uint16_t)sizeof(device_config_str.data);
    uint16_t room    = (device_config_str.size + 1 >= buf_len)
                        ? 0
                        : buf_len - device_config_str.size - 1;
    uint16_t append_len = device_config_ext_str.size;

    if (append_len > room) {
        printf("device_config_ext truncated: %d of %d bytes did not fit\r\n",
               append_len - room, append_len);
        append_len = room;
    }

    memcpy(device_config_str.data + device_config_str.size,
           device_config_ext_str.data, append_len);
    device_config_str.size += append_len;
    device_config_str.data[device_config_str.size] = 0;
}
