#include "hal/timer.h"
#include "hal/millis_acc.h"
#pragma pack(push, 1)
#include "tl_common.h"
#pragma pack(pop)
#include <stdint.h>

/* clock_time() is the 16 MHz system timer. clock_time() / 16000 on its own
 * wraps every 268 s, not at 2^32 ms, so every `hal_millis() - then` check
 * (button long press and multi press, cover travel time, poll control fast
 * poll end, the secondary MCU link timeouts) went wrong once per 268 s: a
 * press could count as a long one, a cover could run to its end stop, a fast
 * poll could never end. The accumulator counts on past the timer wrap; the
 * main loop calls hal_millis() every pass, so it never misses one. An end
 * device that sleeps longer than 268 s in one go loses whole wraps there
 * (time runs slow, not backwards).
 *
 * In .data rather than .bss: deep retention keeps only the low RAM, and .data
 * always lies there. Interrupts are masked around the update, so this is safe
 * to call from an interrupt too. */
static millis_acc_t g_millis __attribute__((section(".data.hal_millis"))) = { 0 };

uint32_t hal_millis() {
    unsigned char r  = irq_disable();
    uint32_t      ms = millis_acc_update(&g_millis, clock_time(),
                                         CLOCK_16M_SYS_TIMER_CLK_1MS);

    irq_restore(r);
    return ms;
}
