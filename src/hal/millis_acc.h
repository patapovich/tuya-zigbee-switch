#ifndef _HAL_MILLIS_ACC_H_
#define _HAL_MILLIS_ACC_H_

#include <stdint.h>

/*
 * Milliseconds from a free-running 32-bit tick counter that wraps long before
 * 2^32 ms do (the Telink system timer runs at 16 MHz and wraps every 268 s).
 *
 * Every call adds the ticks since the previous call, so the count goes on past
 * the counter's wrap and itself wraps only at 2^32 ms (49.7 days): the wrap
 * that `now - then` arithmetic on uint32_t milliseconds expects. Converting
 * the raw counter instead (ticks / ticks_per_ms) jumps back by 268 435 ms at
 * every counter wrap.
 *
 * Constraint: at most one counter wrap between two calls. A longer gap loses
 * whole wraps (time then runs slow, never backwards), so the caller makes sure
 * it is called often, e.g. once per main loop pass.
 *
 * Not reentrant: callers that may race (an interrupt) serialise the update.
 * Starts at zero, so up to the first wrap the result equals ticks / ticks_per_ms.
 */
typedef struct {
    uint32_t last_tick;
    uint32_t ms;
    uint32_t rem_ticks; /* ticks of the next millisecond, < ticks_per_ms */
} millis_acc_t;

static inline uint32_t millis_acc_update(millis_acc_t *acc, uint32_t tick,
                                         uint32_t ticks_per_ms) {
    uint32_t delta = tick - acc->last_tick; /* modulo 2^32: right across a wrap */
    uint32_t whole = delta / ticks_per_ms;

    acc->last_tick  = tick;
    acc->rem_ticks += delta - whole * ticks_per_ms;
    if (acc->rem_ticks >= ticks_per_ms) {
        acc->rem_ticks -= ticks_per_ms;
        whole++;
    }
    acc->ms += whole;
    return acc->ms;
}

#endif
