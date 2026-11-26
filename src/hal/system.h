#ifndef _HAL_SYSTEM_H_
#define _HAL_SYSTEM_H_

#include <stdint.h>

/**
 * Reset the system/microcontroller
 */
void __attribute__((noreturn)) hal_system_reset(void);

/* The stack exception (its event code) that last restarted the module since its power-up,
 * 0: none. Telink: telink/hal/zigbee_network.c; ZDMS16-2 and TS110E builds show it as Basic
 * 0xff5c. */
extern uint8_t g_restart_exception;

void hal_factory_reset(void);

/**
 * A byte kept through a reset of the chip alone (watchdog, software reset), cleared by a power
 * cut (0 then). Telink: the analog register DEEP_ANA_REG1 (0x3b; the restart exception above
 * goes to 0x3a in these builds). Used by the ZH-Z06 dimmers' virtual MCU (hk_dimmer.c) to tell a
 * warm start from a power-up.
 */
uint8_t hal_system_kept_read(void);
void hal_system_kept_write(uint8_t value);

#endif /* _HAL_SYSTEM_H_ */
