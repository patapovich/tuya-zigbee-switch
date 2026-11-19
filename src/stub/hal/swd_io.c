#include "hal/swd_io.h"
#include "stub/hal/swd_target.h"
#include "stub/machine_io.h"

#include <stdint.h>

/* Stub pin layer: no GPIOs, every change goes to the target model
 * (swd_target.c), which owns the wire. Events only for take / release / the
 * idle check, never per edge. */

typedef struct {
    uint8_t        taken;
    hal_gpio_pin_t clk;
    hal_gpio_pin_t dio;
    uint8_t        dio_drive;
    uint8_t        dio_level;
} swd_io_stub_t;

static swd_io_stub_t g_swd_io = { .dio_level = 1 };

static uint8_t check_taken(void) {
    if (!g_swd_io.taken) {
        swd_target_note_untaken();
        return 0;
    }
    return 1;
}

uint8_t hal_swd_clk_idle_level(hal_gpio_pin_t clk) {
    uint8_t level = swd_target_clk_idle_level();

    io_evt("swd clk_check clk=%d level=%d", clk, level);
    return level;
}

void hal_swd_take(hal_gpio_pin_t clk, hal_gpio_pin_t dio) {
    g_swd_io.taken     = 1;
    g_swd_io.clk       = clk;
    g_swd_io.dio       = dio;
    g_swd_io.dio_drive = 0;
    swd_target_host_dio(0, g_swd_io.dio_level);
    swd_target_host_clk(1, 0);
    io_evt("swd pins=taken clk=%d dio=%d", clk, dio);
}

void hal_swd_release(void) {
    g_swd_io.taken     = 0;
    g_swd_io.dio_drive = 0;
    swd_target_host_dio(0, g_swd_io.dio_level);
    swd_target_host_clk(0, 0);
    io_evt("swd pins=released");
}

void hal_swd_clk(uint8_t high) {
    if (check_taken()) {
        swd_target_host_clk(1, high ? 1 : 0);
    }
}

void hal_swd_dio_drive(uint8_t on) {
    if (check_taken()) {
        g_swd_io.dio_drive = on ? 1 : 0;
        swd_target_host_dio(g_swd_io.dio_drive, g_swd_io.dio_level);
    }
}

void hal_swd_dio_set(uint8_t high) {
    if (check_taken()) {
        g_swd_io.dio_level = high ? 1 : 0;
        swd_target_host_dio(g_swd_io.dio_drive, g_swd_io.dio_level);
    }
}

uint8_t hal_swd_dio_get(void) {
    if (!check_taken()) {
        return 1;
    }
    return swd_target_dio_level();
}

void hal_swd_delay(void) {
}
