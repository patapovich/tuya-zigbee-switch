#include "hal/system.h"
#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#pragma pack(pop)
#include <stdbool.h>
#include <stdint.h>

void hal_system_reset(void) {
    // Telink 8258 system reset
    mcu_reset();
}

void hal_factory_reset(void) {
    zb_factoryReset();
}

#ifdef HK_DIMMER

/* pm.h: DEEP_ANA_REG0..2 (0x3a..0x3c) are reset only by a power cycle; 0x3c is the SDK's
 * (SYS_DEEP_ANA_REG), 0x3a drv_pm's in PM_ENABLE (end device) builds and, in these builds, the
 * restart exception's (telink/hal/zigbee_network.c RESTART_EXCEPTION_ANA_REG, which other
 * builds keep in 0x3b). */
#define HAL_KEPT_ANA_REG    DEEP_ANA_REG1

uint8_t hal_system_kept_read(void) {
    return analog_read(HAL_KEPT_ANA_REG);
}

void hal_system_kept_write(uint8_t value) {
    analog_write(HAL_KEPT_ANA_REG, value);
}

#endif
