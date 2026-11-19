#ifndef _HAL_SWD_IO_H_
#define _HAL_SWD_IO_H_

#include <stdint.h>

#include "hal/gpio.h"

/* Two GPIOs as the host side of an ARM Serial Wire Debug link (SWCLK, SWDIO),
 * for the ZTU-hosted SWD master of the secondary MCU (src/zigbee/mcu_flasher.c,
 * the AVATTO ZDMS16-2's PY32F002A). Only builds with SECONDARY_MCU_IMAGE use it,
 * and only while a job runs: outside a job neither pin is ever touched, so a
 * unit without the SW config token behaves as if this did not exist.
 *
 * The bit-level protocol lives in src/base_components/swd.c (platform-neutral);
 * this is the pin layer under it. The Telink version drives the pins (SWD has no
 * minimum clock: interrupts and cache misses only stretch a bit). The stub
 * version feeds an edge-level target model (src/stub/hal/swd_target.c).
 *
 * Electrical rules the implementations keep:
 * - SWCLK is the host's: push-pull output while the link is taken.
 * - SWDIO is shared: input with a weak pull-up (a missing wire reads 1s), the
 *   output enabled only while the host drives a bit (hal_swd_dio_drive).
 * - The target samples SWDIO on the rising SWCLK edge and changes it after the
 *   rising edge; the host samples before it raises SWCLK. */

/* Check before the first drive of a session: put a pull-up on SWCLK, drive
 * nothing, and read it. 0 = something holds SWCLK low (a target firmware that
 * took the pin, or a short): the caller must not drive. Leaves both pins as
 * they were found (released). */
uint8_t hal_swd_clk_idle_level(hal_gpio_pin_t clk);

/* Take the pins for a session: SWCLK output low, SWDIO input with pull-up,
 * its output off. */
void hal_swd_take(hal_gpio_pin_t clk, hal_gpio_pin_t dio);

/* Release both pins back to the power-up state of the platform (Telink: GPIO,
 * input and output disabled, no pull = high impedance). */
void hal_swd_release(void);

/* SWCLK level (1 = high). */
void hal_swd_clk(uint8_t high);

/* SWDIO output enable (1 = the host drives SWDIO). */
void hal_swd_dio_drive(uint8_t on);

/* SWDIO output level, used while driven. */
void hal_swd_dio_set(uint8_t high);

/* SWDIO input level. */
uint8_t hal_swd_dio_get(void);

/* Half a bit time (Telink: 1 us, about 400 kHz). */
void hal_swd_delay(void);

#endif
