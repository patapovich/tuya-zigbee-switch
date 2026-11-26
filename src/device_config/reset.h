#ifndef RESET_H
#define RESET_H

#include <stdint.h>

#define DEFAULT_RESET_DELAY_MS    300

__attribute__((noreturn)) void reset_all();

void schedule_full_reset(uint16_t delay_ms);
void schedule_reboot(uint16_t delay_ms);

/* A restart after the module's own leave (reset.c), in the builds where a restart of this module
 * leaves the lamp alone: the ZDMS16-2 (SECONDARY_MCU_IMAGE: its MCU drives the lamp) and the
 * TS110E (HK_DIMMER: the dimming MCU drives it, the virtual MCU's warm start sends what it has).
 * Elsewhere a reset stays as it was (the stack's leave in place). */
#if defined(SECONDARY_MCU_IMAGE) || defined(HK_DIMMER)
#define LEAVE_RESTART
#define LEAVE_RESTART_DELAY_MS    1000

/* The module's own leave (rejoin 0) is confirmed. left_network: its NV was emptied while it was
 * on a network (not a reset of a unit already factory new, which the stack confirms the same). */
void restart_after_leave(uint8_t left_network);

/* A restart after a leave is on its way: no network steering until then. */
extern uint8_t g_leave_restart;
#define restart_after_leave_pending()    (g_leave_restart)

#else
#define restart_after_leave_pending()    0
#endif

#endif // RESET_H
