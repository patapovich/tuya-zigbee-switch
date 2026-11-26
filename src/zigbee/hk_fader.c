#ifdef HK_DIMMER

/* Open Dimmer's fader (open-dimmer src/dim_core.c; keep in sync), its times in ms and one tick a
 * constant DIM_TICK_MS: see hk_fader.h for what differs. */

#include "hk_fader.h"

static uint16_t rate_q(uint16_t ms) {
    /* the whole range (1000 steps) in ms: 1000 * 64 / (ms / 10) per 10 ms */
    if (ms < 10) {
        return 1000u * DIM_Q_PER_STEP;      /* at once */
    }
    uint32_t q = (1000u * DIM_Q_PER_STEP * 10u + ms / 2u) / ms;

    return (uint16_t)(q < 1u ? 1u : q);
}

void dim_profile_set(dim_profile_t *p, uint16_t fade_on_ms, uint16_t fade_off_ms,
                     uint8_t soft_end_pct, uint16_t hold_ms, uint8_t hold_end_pct) {
    p->on_q      = rate_q(fade_on_ms);
    p->off_q     = rate_q(fade_off_ms);
    p->soft_span = (uint16_t)((soft_end_pct > 100 ? 100 : soft_end_pct) * 10u);
    p->hold_q    = rate_q(hold_ms < 100 ? 100 : hold_ms);
    p->hold_span = (uint16_t)((hold_end_pct > 100 ? 100 : hold_end_pct) * 10u);
    p->boost_ms  = 0;
}

void dim_fader_init(dim_fader_t *f) {
    f->current    = 0;
    f->target     = 0;
    f->hold_dir   = 0;
    f->hold_lo    = 0;
    f->hold_hi    = DIM_VALUE_MAX;
    f->floor      = 0;
    f->floor_hold = 0;
    f->from_dark  = 0;
    f->acc        = 0;
    f->hold_fine  = 0;
    f->timed_left = 0;
    f->boost_left = 0;
    f->hold_wait  = 0;
    dim_profile_set(&f->prof, DIM_FADE_ON_MS, DIM_FADE_OFF_MS, DIM_SOFT_END_PCT, DIM_HOLD_MS,
                    DIM_HOLD_END_PCT);
}

void dim_fader_set(dim_fader_t *f, uint8_t on, uint16_t level) {
    if (level > DIM_VALUE_MAX) {
        level = DIM_VALUE_MAX;
    }
    if (f->hold_dir) {
        if (!on) {
            f->hold_dir = 0;                /* off wins over a hold */
            f->target   = 0;
        }
        return;                             /* the finger is in control */
    }
    uint16_t t = on ? level : 0;

    if (t != f->target) {
        f->timed_left = 0;                  /* a new target: the profile's rates again */
        if (!t) {
            if (f->boost_left) {
                f->current = f->floor;      /* off during a boost: out the usual way */
            }
            f->boost_left = 0;
        } else if (!f->boost_left && f->current == 0) {
            /* Coming on from dark: a start boost first (an LED driver that needs a kick). A new
             * level during it (the module sends on, then the level) keeps it going. */
            f->boost_left = f->prof.boost_ms;
        }
    }
    f->target = t;
}

void dim_fader_set_timed(dim_fader_t *f, uint8_t on, uint16_t level, uint32_t ms) {
    uint16_t before = f->target;

    dim_fader_set(f, on, level);
    if (f->hold_dir || ms == 0 || (f->target == before && f->timed_left)) {
        return;                             /* no time, or the same move going on */
    }

    /* From where the light is (dark starts at the floor) to the target (going dark: the floor,
     * the moment there counted in). */
    uint16_t from = f->current < f->floor ? f->floor : f->current;
    uint16_t to   = f->target < f->floor ? f->floor : f->target;

    if (from == to) {
        return;
    }
    if (f->target == 0 && ms > DIM_OFF_HOLD_MS) {
        ms -= DIM_OFF_HOLD_MS;
    }
    f->timed_from  = from;
    f->timed_to    = to;
    f->timed_total = f->timed_left = ms;
}

void dim_fader_hold_start(dim_fader_t *f, int8_t dir, uint16_t lo, uint16_t hi) {
    if (hi > DIM_VALUE_MAX) {
        hi = DIM_VALUE_MAX;
    }
    if (lo > hi) {
        lo = hi;
    }
    f->hold_lo    = lo;
    f->hold_hi    = hi;
    f->acc        = 0;
    f->target     = f->current;
    f->hold_dir   = dir < 0 ? -1 : 1;
    f->hold_fine  = dir < 0;        /* going down towards the min: fine there */
    f->timed_left = 0;
    f->boost_left = 0;              /* a hold picks its level from where the light is */
    f->hold_wait  = 0;
}

void dim_fader_hold_start_from_dark(dim_fader_t *f, uint16_t lo, uint16_t hi) {
    uint16_t boost = f->boost_left;           /* a hold from dark keeps its start boost */

    dim_fader_hold_start(f, +1, lo, hi);
    f->hold_fine  = 1;
    f->boost_left = boost;
    f->hold_wait  = DIM_HOLD_DARK_WAIT_MS;
}

uint16_t dim_fader_hold_stop(dim_fader_t *f) {
    if (f->hold_dir) {               /* an off may have ended the hold already: */
        f->hold_dir = 0;             /* then it keeps fading to its target      */
        if (f->boost_left) {
            f->current = f->hold_lo; /* let go during the start boost (at full): still at its min */
        }
        f->target = f->current;
    }
    return f->current;
}

void dim_fader_hold_cancel(dim_fader_t *f) {
    f->hold_dir  = 0;
    f->target    = f->current;
    f->from_dark = 1;               /* the light is coming on: the on rate */
    f->acc       = 0;
}

/* Whole steps to move now for q 1/64 steps (the rest is kept). */
static uint16_t take(dim_fader_t *f, uint16_t q) {
    uint32_t a = (uint32_t)f->acc + q;

    f->acc = (uint8_t)(a % DIM_Q_PER_STEP);
    return (uint16_t)(a / DIM_Q_PER_STEP);
}

/* Rate q, slowing to lo_q at distance 0 from the bottom of a span: q - (q - lo_q) (1 - d/span)^2.
 * The rate meets q with no kink at the top of the span. */
static uint16_t eased_q(uint16_t q, uint16_t lo_q, uint16_t d, uint16_t span) {
    if (span == 0 || d >= span || q <= lo_q) {
        return q;
    }
    uint32_t e = (uint32_t)(span - d) * (span - d) / span;     /* <= span: no overflow below */

    return (uint16_t)(q - (uint32_t)(q - lo_q) * e / span);
}

static uint16_t hold_tick(dim_fader_t *f) {
    uint16_t cur = f->current;

    if (f->hold_dir > 0 && cur < f->hold_lo) {
        cur = f->hold_lo;                   /* coming up from dark: start at min */
    } else if (f->hold_dir < 0 && cur > f->hold_hi) {
        cur = f->hold_hi;
    }
    if (f->hold_wait) {                     /* a hold from dark: at the min a moment first */
        f->hold_wait = f->hold_wait > DIM_TICK_MS ? (uint16_t)(f->hold_wait - DIM_TICK_MS) : 0;
        f->current   = f->target = cur;
        return cur;
    }
    uint16_t d = cur > f->hold_lo ? (uint16_t)(cur - f->hold_lo) : 0;
    uint16_t q = f->hold_fine ? eased_q(f->prof.hold_q, DIM_HOLD_BOTTOM_Q, d, f->prof.hold_span)
                                 : f->prof.hold_q;
    uint16_t step = take(f, q);

    if (f->hold_dir > 0) {
        cur = (uint16_t)(f->hold_hi - cur > step ? cur + step : f->hold_hi);
    } else {
        cur = (uint16_t)(cur - f->hold_lo > step ? cur - step : f->hold_lo);
    }
    f->current = cur;
    f->target  = cur;
    return cur;
}

uint16_t dim_fader_tick(dim_fader_t *f) {
    if (f->boost_left) {
        /* At full (also before a hold from dark: the kick an LED driver needs at the min); a
         * timed move runs on meanwhile, so the transition time stays. Then straight to the
         * target, where a timed move is now, or the hold's min; switched off during it: from the
         * floor, the usual way out. */
        uint8_t lit = f->target || f->hold_dir;         /* a hold from dark: target still 0 */

        f->boost_left = lit && f->boost_left > DIM_TICK_MS ? (uint16_t)(f->boost_left - DIM_TICK_MS)
                                                           : 0;
        if (f->boost_left) {
            f->timed_left = f->timed_left > DIM_TICK_MS ? f->timed_left - DIM_TICK_MS : 0;
            f->current    = DIM_VALUE_MAX;
            return f->current;
        }
        f->current = f->hold_dir ? f->hold_lo : !f->target ? f->floor : f->target;
        if (!f->timed_left) {
            return f->current;
        }
    }
    if (f->hold_dir) {
        return hold_tick(f);
    }
    if (f->timed_left) {
        /* A timed move: the even position for the time gone. d <= 1000 and the time < 2^22 ms:
         * the product fits 32 bits. */
        f->timed_left = f->timed_left > DIM_TICK_MS ? f->timed_left - DIM_TICK_MS : 0;
        uint16_t a = f->timed_from, b = f->timed_to;
        uint16_t d = a > b ? (uint16_t)(a - b) : (uint16_t)(b - a);
        uint16_t m = (uint16_t)((uint32_t)d * (f->timed_total - f->timed_left) / f->timed_total);

        f->current    = b > a ? (uint16_t)(a + m) : (uint16_t)(a - m);
        f->floor_hold = 0;
        f->from_dark  = 0;
        f->acc        = 0;
        return f->current;                  /* at its end the target, or the floor going dark */
    }
    uint16_t cur = f->current, tgt = f->target;
    uint16_t plain_q = DIM_STEP_DEFAULT * DIM_Q_PER_STEP;

    if (cur < tgt) {
        f->floor_hold = 0;
        if (cur < f->floor) {
            cur          = f->floor;        /* light comes on at the lowest lit level */
            f->from_dark = 1;
        }
        uint16_t step = take(f, f->from_dark ? f->prof.on_q : plain_q);

        cur = (uint16_t)(tgt - cur > step ? cur + step : tgt);
        if (cur == tgt) {
            f->from_dark = 0;
        }
    } else if (cur > tgt) {
        f->from_dark = 0;
        if (tgt < f->floor && cur <= f->floor) {
            /* At the floor on the way to dark: stay a moment, then out (below the floor the lamp
             * is dark anyway). */
            f->floor_hold = (uint16_t)(f->floor_hold + DIM_TICK_MS);
            if (f->floor_hold >= DIM_OFF_HOLD_MS) {
                f->floor_hold = 0;
                cur           = tgt;
            }
        } else if (tgt < f->floor) {
            /* going dark: the off rate, slowing to 1 step per 10 ms at the floor */
            uint16_t step = take(f, eased_q(f->prof.off_q, DIM_Q_PER_STEP,
                                            (uint16_t)(cur - f->floor), f->prof.soft_span));

            cur = (uint16_t)(cur - f->floor > step ? cur - step : f->floor);
        } else {
            uint16_t step = take(f, plain_q);

            cur = (uint16_t)(cur - tgt > step ? cur - step : tgt);
        }
    }
    f->current = cur;
    return cur;
}

uint16_t dim_comp(uint16_t duty, uint16_t peak, uint16_t amount, uint16_t min_duty) {
    if (!amount || duty < min_duty || duty >= min_duty + DIM_COMP_SPAN || peak <= duty) {
        return 0;
    }
    uint32_t fall = (uint32_t)(peak - duty);

    if (fall > DIM_COMP_FALL) {
        fall = DIM_COMP_FALL;
    }
    /* amount * (min_duty + SPAN - duty) / SPAN, times fall / FALL up to FALL: one division */
    return (uint16_t)((uint32_t)amount * (uint32_t)(min_duty + DIM_COMP_SPAN - duty) * fall /
                      (DIM_COMP_SPAN * DIM_COMP_FALL));
}

#endif /* HK_DIMMER */
