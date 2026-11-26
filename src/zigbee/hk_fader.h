#ifndef _HK_FADER_H_
#define _HK_FADER_H_

#include <stdint.h>

/*
 * The fader of the ZH-Z06 dimmers' virtual MCU (hk_dimmer.c): Open Dimmer's, as open-dimmer
 * src/dim_core.c / dim_core.h have it (dim_profile_set, dim_fader_*, take, eased_q, hold_tick,
 * dim_fader_tick, dim_comp; keep in sync), so the TS110E fades, holds and boosts as a ZDMS16-2
 * with Open Dimmer does: the off fade's slow end (soft_span) and its moment at the floor, the
 * hold's slow zone near the minimum (hold_span, going down and rising from dark), the wait at the
 * minimum before a hold from dark rises, the start boost, timed moves (a transition time) at one
 * even rate, lit-to-lit changes at DIM_STEP_DEFAULT; and the lamp compensation's sum (dim_comp).
 *
 * Open Dimmer ticks it at each zero cross of the mains (a half cycle of 10 ms at 50 Hz, its
 * rates are per one); here hk_dimmer_poll ticks it every DIM_TICK_MS of the clock, and every frame
 * to the dimming MCU takes where it is then. What differs from Open Dimmer's:
 *  - its times are ms (Open Dimmer: 20 us ticks): floor_hold, boost_left, hold_wait, timed_*;
 *  - the half cycle is always DIM_TICK_MS (no half_period, no dim_fader_set_half_period), so
 *    acc counts 1/64 steps (Open Dimmer's acc / half_period);
 *  - a timed move takes up to 32 bits of ms (the module's network times: up to 600 s; Open
 *    Dimmer's DP takes 65535 ms); the even position from ms: d (<= 1000) x ms (< 2^22) fits;
 *  - the lit-to-lit step is always DIM_STEP_DEFAULT (no step field), the hold's bottom rate and
 *    the moment at the floor are the constants (not in the profile);
 *  - no zero-cross tracker, curves or custom tables (hk_dimmer.c has those it needs);
 *  - hk_dimmer.c's profile_set puts in its own hold_q: hold_time over the channel's min..max
 *    (Open Dimmer's dimmer.c: over 0..1000); dim_profile_set here is Open Dimmer's.
 */

#define DIM_VALUE_MAX            1000
#define DIM_TICK_MS              10     /* one tick: a 50 Hz half cycle */
#define DIM_STEP_DEFAULT         5      /* per 10 ms: 0 -> 1000 in 2 s */
#define DIM_Q_PER_STEP           64     /* rates are in 1/64 steps per 10 ms */
#define DIM_HOLD_BOTTOM_Q        32     /* a hold's rate at the min (its slow zone): 1/2 step */
#define DIM_OFF_HOLD_MS          80     /* at the min before going dark */

/* A hold from dark stays at the min this long before it rises: past the switch's hold time (400
 * ms) and a moment more, so a hold let go then leaves the light at its min. */
#define DIM_HOLD_DARK_WAIT_MS    700
#define DIM_HOLD_RATE            3      /* full hold rate per 10 ms at 3300 ms */
#define DIM_FADE_ON_MS           950    /* Open Dimmer's default profile (dim_fader_init) */
#define DIM_FADE_OFF_MS          950
#define DIM_SOFT_END_PCT         12
#define DIM_HOLD_MS              3300
#define DIM_HOLD_END_PCT         20

/* How a channel fades and holds. Rates in 1/64 steps per 10 ms (DIM_Q_PER_STEP). */
typedef struct {
    uint16_t on_q;      /* coming on from dark */
    uint16_t off_q;     /* going dark, above the soft end */
    uint16_t soft_span; /* going dark: values above the floor that slow down (0 = none) */
    uint16_t hold_q;    /* hold, full rate */
    uint16_t hold_span; /* hold: values above the min that slow down (0 = none) */
    uint16_t boost_ms;  /* start boost: at full this long when it comes on from dark (0: none) */
} dim_profile_t;

/** Profile from the settings: times in ms for the whole range (0 = at once), spans in % of
 * 0..1000 (the caller scales them to the channel's min..max, as Open Dimmer's dimmer.c apply). */
void     dim_profile_set(dim_profile_t *p, uint16_t fade_on_ms, uint16_t fade_off_ms,
                         uint8_t soft_end_pct, uint16_t hold_ms, uint8_t hold_end_pct);

typedef struct {
    uint16_t      current;     /* value applied now, 0..1000 (0: dark) */
    uint16_t      target;      /* 0 when off */
    int8_t        hold_dir;    /* 0: normal fading; +1 / -1: hold dimming */
    uint8_t       from_dark;   /* this rise started dark: the on rate */
    uint8_t       hold_fine;   /* the hold slows down near the min: going down, or up from dark */
    uint8_t       acc;         /* 1/64 steps not yet moved */
    uint16_t      hold_lo;
    uint16_t      hold_hi;
    uint16_t      floor;       /* lowest lit value (the channel min): fades jump 0 <-> floor */
    uint16_t      floor_hold;  /* ms spent at the floor on the way to dark */

    /* A timed move (dim_fader_set_timed): from -> to over timed_total ms, the light at the even
     * position for the time gone; timed_left 0: none. It ends with a new target or a hold. */
    uint16_t      timed_from;
    uint16_t      timed_to;
    uint32_t      timed_left;
    uint32_t      timed_total;
    uint16_t      boost_left;  /* ms of a start boost still to go (at full, then the target) */
    uint16_t      hold_wait;   /* ms a hold from dark still stays at the min (hk_dimmer.c also
                                * sets it for a hold while the dimming MCU's own ramp runs: the
                                * hold stays where it is until then) */
    dim_profile_t prof;
} dim_fader_t;

/** Dark, floor 0, Open Dimmer's default profile. */
void     dim_fader_init(dim_fader_t *f);

/** Target: 0 when off, else the level (10..1000). A new target ends a timed move; the same
 * target keeps it. Coming on from dark (current 0): the profile's start boost first. */
void     dim_fader_set(dim_fader_t *f, uint8_t on, uint16_t level);

/** As dim_fader_set, the move taking ms (a transition time) at one even rate, from where the
 * light is (the floor when dark) to the target; going dark it reaches the floor so that, with the
 * moment there, it is dark after ms. The same target again while it runs keeps it. 0: the
 * profile's rates; under a tick: at once. During a hold as dim_fader_set. */
void     dim_fader_set_timed(dim_fader_t *f, uint8_t on, uint16_t level, uint32_t ms);

/** Hold dimming from where the light is, at the hold rate towards hi (+1) or lo (-1). */
void     dim_fader_hold_start(dim_fader_t *f, int8_t dir, uint16_t lo, uint16_t hi);

/** A hold that started dark: at the min DIM_HOLD_DARK_WAIT_MS (after a start boost), then rising
 * slowly at first. */
void     dim_fader_hold_start_from_dark(dim_fader_t *f, uint16_t lo, uint16_t hi);

/** End of a hold: frozen where the light is (during a start boost: its min). Returns that. */
uint16_t dim_fader_hold_stop(dim_fader_t *f);

/** End of a hold that was only provisional (a tap from dark): normal fading, at the on rate,
 * towards the target set next. */
void     dim_fader_hold_cancel(dim_fader_t *f);

/** One tick (DIM_TICK_MS): move current towards target, return current. */
uint16_t dim_fader_tick(dim_fader_t *f);

/* Lamp compensation (Open Dimmer 2.2.8's dim_comp): the duty (0..10000) to take off a duty after
 * the light came down from peak: up to amount at the min's duty, linearly less up to DIM_COMP_SPAN
 * above it, none from there; all of it after a fall of DIM_COMP_FALL, less after a smaller one,
 * none while it rises. */
#define DIM_COMP_SPAN    1000
#define DIM_COMP_FALL    100
uint16_t dim_comp(uint16_t duty, uint16_t peak, uint16_t amount, uint16_t min_duty);

#endif
