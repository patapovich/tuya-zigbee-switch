#include "hal/system.h"
#include "hal/zigbee.h"
#include "stub/hal/stub.h"
#include "stub/machine_io.h"
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>

/* The stub has no Telink stack, so no stack exception ever restarts it. */
uint8_t g_restart_exception;

void hal_system_reset(void) {
    io_log("SYSTEM",
           "System reset requested - performing graceful stub shutdown");
    io_log("SYSTEM",
           "In real hardware, this would trigger a complete system reset");
    io_log("SYSTEM", "Stub application exiting with code 0");
    exit(0);
}

void hal_factory_reset(void) {
    io_log("SYSTEM", "Factory reset requested - performing stub factory reset");
    io_log(
        "SYSTEM",
        "In real hardware, this would clear all non-volatile memory to defaults");

    if (hal_zigbee_get_network_status() != HAL_ZIGBEE_NETWORK_JOINED) {
        stub_zigbee_reset_factory_new();   /* not on a network yet (joining): factory new */
    } else {
        hal_zigbee_leave_network();
    }
}

/* The kept byte (hal_system_kept_*): in a file next to the NV items, so it outlives a restart
 * of the stub process (hal_system_reset, the reboot command); a power cut (quit) removes it. */
#define KEPT_FILE    "./stub_nvm_data/kept.bin"

uint8_t hal_system_kept_read(void) {
    FILE *f = fopen(KEPT_FILE, "rb");
    int   v = 0;

    if (f != NULL) {
        v = fgetc(f);
        fclose(f);
    }
    return v == EOF ? 0 : (uint8_t)v;
}

void hal_system_kept_write(uint8_t value) {
    FILE *f;

    mkdir("./stub_nvm_data", 0700);
    f = fopen(KEPT_FILE, "wb");
    if (f != NULL) {
        fputc(value, f);
        fclose(f);
    }
}

void stub_system_power_cut(void) {
    remove(KEPT_FILE);
}
