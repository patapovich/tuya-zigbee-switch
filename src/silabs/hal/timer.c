#include "hal/timer.h"
#include "sl_sleeptimer.h"


/* The 32-bit sleeptimer tick (32768 Hz) wraps every 36.4 h, and its
 * conversion with it, long before 2^32 ms: `hal_millis() - then` was wrong
 * once per wrap. The 64-bit tick count does not wrap; its low 32 bits of
 * milliseconds wrap at 2^32 ms as the callers expect. */
uint32_t hal_millis() {
    uint64_t ms = 0;

    (void)sl_sleeptimer_tick64_to_ms(sl_sleeptimer_get_tick_count64(), &ms);
    return (uint32_t)ms;
}
