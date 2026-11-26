"""The TS110E's fader (src/zigbee/hk_fader.c) on the host: Open Dimmer's own host tests of its
fader (open-dimmer tests/test_dim_core.c, keep in sync), ported to this copy: its times in ms
(floor_hold, boost_left, hold_wait, timed_*), a tick always 10 ms (no half period: the 60 Hz cases
are left out), no step field, the slow zones as the caller gives them; and its timed moves past
Open Dimmer's 65535 ms (the module's network times go up to 600 s)."""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROG = r"""
#include <stdio.h>
#include <stdint.h>
#include "zigbee/hk_fader.h"

/* the default profile in whole steps (1/64 steps / 64) */
#define ON_STEP   ((640000 + DIM_FADE_ON_MS / 2) / DIM_FADE_ON_MS / 64)
#define SOFT_SPAN (DIM_SOFT_END_PCT * 10)
#define HOLD_SPAN (DIM_HOLD_END_PCT * 10)

static int g_failed;

#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            printf("FAIL %d: %s\n", __LINE__, #cond);                            \
            g_failed++;                                                          \
        }                                                                        \
    } while (0)

static void set_profile(dim_fader_t *f, const dim_profile_t *p) {
    f->prof = *p;
}

static int timed_ticks(dim_fader_t *f, uint16_t target, int limit) {
    int n = 0;
    while (f->current != target && n < limit) {
        dim_fader_tick(f);
        n++;
    }
    return n;
}

static void test_fade_up_takes_two_seconds(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 1000);
    int n = 0;
    while (dim_fader_tick(&f) != 1000 && n < 10000) {
        n++;
    }
    CHECK(n + 1 == 200);               /* 200 ticks = 2 s */
}

static void test_fade_lands_exactly_on_target(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 12);
    for (int i = 0; i < 10; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 12);
    dim_fader_set(&f, 1, 3);
    for (int i = 0; i < 10; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 3);
}

static void test_off_fades_to_zero_and_keeps_nothing(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 500);
    for (int i = 0; i < 200; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 500);
    dim_fader_set(&f, 0, 500);
    CHECK(f.target == 0);
    for (int i = 0; i < 99; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 5);
    CHECK(dim_fader_tick(&f) == 0);
    CHECK(dim_fader_tick(&f) == 0);
}

static void test_level_clamped(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 5000);
    CHECK(f.target == 1000);
}

static void test_hold_time_zero_is_the_100_ms_floor(void) {
    dim_profile_t p0, p100, p101;
    dim_profile_set(&p0, 950, 950, 12, 0, 20);
    dim_profile_set(&p100, 950, 950, 12, 100, 20);
    dim_profile_set(&p101, 950, 950, 12, 101, 20);
    CHECK(p0.hold_q == 6400);
    CHECK(p100.hold_q == 6400);
    CHECK(p101.hold_q < 6400);
}

static void test_hold_follows_without_lag_and_freezes_on_release(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 500);
    for (int i = 0; i < 200; i++) {
        dim_fader_tick(&f);
    }
    dim_fader_hold_start(&f, +1, 100, 900);
    for (int i = 0; i < 50; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current >= 500 + 50 * DIM_HOLD_RATE && f.current <= 500 + 50 * DIM_HOLD_RATE + 2);
    CHECK(f.target == f.current);
    uint16_t at_release = f.current;
    CHECK(dim_fader_hold_stop(&f) == at_release);
    for (int i = 0; i < 100; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == at_release);
}

static void test_hold_stops_at_bounds(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 800);
    for (int i = 0; i < 200; i++) {
        dim_fader_tick(&f);
    }
    dim_fader_hold_start(&f, +1, 100, 900);
    for (int i = 0; i < 1000; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 900);
    dim_fader_hold_start(&f, -1, 100, 900);
    for (int i = 0; i < 1000; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 100);
}

static void test_hold_up_from_dark_starts_at_min(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 600);
    dim_fader_tick(&f);
    dim_fader_hold_start_from_dark(&f, 200, 1000);
    CHECK(dim_fader_tick(&f) == 200);
}

static void test_hold_is_slow_just_above_the_min(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 60);
    for (int i = 0; i < 50; i++) {
        dim_fader_tick(&f);
    }
    dim_fader_hold_start_from_dark(&f, 60, 1000);
    f.hold_wait = 0;                                    /* (Open Dimmer's test predates the wait) */
    int n50 = 0, n200 = 0, n = 0;
    uint16_t prev = 60;
    while (f.current < 60 + HOLD_SPAN + 100 && n < 2000) {
        uint16_t now = dim_fader_tick(&f);
        CHECK(now >= prev && now - prev <= DIM_HOLD_RATE + 1);
        prev = now;
        n++;
        if (now < 60 + 50) {
            n50 = n;
        }
        if (now < 60 + HOLD_SPAN) {
            n200 = n;
        }
    }
    CHECK(n50 > 3 * 50 / DIM_HOLD_RATE);
    CHECK(n200 > 3 * HOLD_SPAN / DIM_HOLD_RATE / 2);
    CHECK(n200 < 200);
    dim_fader_hold_start(&f, -1, 60, 1000);
    for (int i = 0; i < 2000; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 60);
    dim_profile_t p;
    dim_profile_set(&p, DIM_FADE_ON_MS, DIM_FADE_OFF_MS, DIM_SOFT_END_PCT, DIM_HOLD_MS, DIM_HOLD_END_PCT);
    CHECK(p.hold_span == HOLD_SPAN && p.hold_q == 194);
    p.hold_span = 0;
    set_profile(&f, &p);
    dim_fader_hold_start(&f, +1, 60, 1000);
    CHECK(dim_fader_tick(&f) == 63);
}

static void test_remote_write_during_hold(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 500);
    for (int i = 0; i < 200; i++) {
        dim_fader_tick(&f);
    }
    dim_fader_hold_start(&f, -1, 100, 1000);
    dim_fader_set(&f, 1, 900);
    dim_fader_tick(&f);
    CHECK(f.current == 500 - DIM_HOLD_RATE);
    dim_fader_set(&f, 0, 900);
    CHECK(f.hold_dir == 0 && f.target == 0);
}

static void test_off_during_hold_then_release_stays_off(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 800);
    for (int i = 0; i < 200; i++) {
        dim_fader_tick(&f);
    }
    dim_fader_hold_start(&f, -1, 100, 1000);
    dim_fader_tick(&f);
    dim_fader_set(&f, 0, 800);
    dim_fader_tick(&f);
    dim_fader_hold_stop(&f);
    CHECK(f.target == 0);
    for (int i = 0; i < 300; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 0);
}

static void test_fader_floor(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    f.floor = 287;
    dim_fader_set(&f, 1, 600);
    CHECK(dim_fader_tick(&f) == 287 + ON_STEP);
    int n_on = 1;
    uint16_t prev = 287 + ON_STEP;
    while (f.current != 600 && n_on < 1000) {
        uint16_t now = dim_fader_tick(&f);
        CHECK(now > prev);
        prev = now;
        n_on++;
    }
    CHECK(f.current == 600);
    CHECK(n_on >= (600 - 287) * DIM_FADE_ON_MS / 10000 && n_on <= (600 - 287) * DIM_FADE_ON_MS / 10000 + 2);
    dim_fader_set(&f, 0, 600);
    int at_floor = 0, n_off = 0, last_steps = 0;
    prev = 600;
    while (f.current && n_off < 1000) {
        uint16_t now = dim_fader_tick(&f);
        CHECK(now == 0 || now >= 287);
        CHECK(now <= prev);
        if (now == 287) {
            at_floor++;
        }
        if (now > 0 && now < 287 + SOFT_SPAN / 4 && now != 287) {
            last_steps++;
        }
        prev = now;
        n_off++;
    }
    CHECK(f.current == 0);
    CHECK(at_floor == DIM_OFF_HOLD_MS / 10);  /* a moment at the floor (80 ms), then dark */
    CHECK(last_steps >= 10);
    CHECK(n_off < 200);
    dim_fader_set(&f, 1, 800);
    for (int k = 0; k < 400; k++) {
        dim_fader_tick(&f);
    }
    dim_fader_set(&f, 1, 600);
    CHECK(dim_fader_tick(&f) == 795);         /* lit to lit: the plain step */
    for (int k = 0; k < 400; k++) {
        dim_fader_tick(&f);
    }
    dim_fader_set(&f, 1, 300);
    for (int k = 0; k < 59; k++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 305);
    dim_fader_set(&f, 1, 500);
    CHECK(dim_fader_tick(&f) == 305 + DIM_STEP_DEFAULT);
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 20);
    CHECK(dim_fader_tick(&f) == 5);
}

static void test_hold_from_dark_and_cancel(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    f.floor = 60;
    dim_fader_set(&f, 1, 700);
    dim_fader_hold_start_from_dark(&f, 60, 1000);
    CHECK(dim_fader_tick(&f) == 60);
    for (int k = 0; k < 9; k++) {
        dim_fader_tick(&f);
    }
    uint16_t tap = f.current;
    CHECK(tap >= 60 && tap < 60 + 10);
    dim_fader_hold_cancel(&f);
    dim_fader_set(&f, 1, 700);
    CHECK(dim_fader_tick(&f) == tap + ON_STEP);   /* quick on */
    int n = 0;
    while (f.current != 700 && n < 1000) {
        dim_fader_tick(&f);
        n++;
    }
    CHECK(f.current == 700);
    CHECK(n < (700 - 60) / ON_STEP + 2);
    dim_fader_set(&f, 0, 700);
    while (f.current) {
        dim_fader_tick(&f);
    }
    dim_fader_set(&f, 1, 700);
    dim_fader_hold_start_from_dark(&f, 60, 1000);
    for (int k = 0; k < DIM_HOLD_DARK_WAIT_MS / 10; k++) {
        CHECK(dim_fader_tick(&f) == 60);
    }
    for (int k = 0; k < 40; k++) {
        dim_fader_tick(&f);
    }
    uint16_t held = f.current;
    CHECK(held > 60 && held < 60 + 40 * DIM_HOLD_RATE);
    CHECK(dim_fader_hold_stop(&f) == held);
    CHECK(dim_fader_tick(&f) == held);
    dim_fader_set(&f, 0, 700);
    while (f.current) {
        dim_fader_tick(&f);
    }
    dim_fader_set(&f, 1, 700);
    dim_fader_hold_start_from_dark(&f, 60, 1000);
    for (int k = 0; k < (DIM_HOLD_DARK_WAIT_MS - 100) / 10; k++) {
        dim_fader_tick(&f);
    }
    CHECK(dim_fader_hold_stop(&f) == 60);
    for (int k = 0; k < 100; k++) {
        CHECK(dim_fader_tick(&f) == 60);
    }
}

static void test_fade_profile(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_profile_t p;
    dim_profile_set(&p, 500, 2500, 0, 3300, 20);
    CHECK(p.on_q == 1280 && p.off_q == 256 && p.soft_span == 0 && p.hold_q == 194);
    set_profile(&f, &p);
    f.floor = 100;
    dim_fader_set(&f, 1, 500);
    CHECK(dim_fader_tick(&f) == 120);
    while (f.current != 500) {
        dim_fader_tick(&f);
    }
    dim_fader_set(&f, 0, 500);
    uint16_t prev = 500;
    int n = 0, at_floor = 0;
    while (f.current) {
        uint16_t now = dim_fader_tick(&f);
        CHECK(now == 0 || prev - now == 4 || now == 100);   /* plain steps of 4 */
        at_floor += now == 100;
        prev = now;
        n++;
    }
    /* (Open Dimmer's test sets the moment at the floor 0: here a constant, 80 ms) */
    CHECK(n == (500 - 100) / 4 + DIM_OFF_HOLD_MS / 10);
    CHECK(at_floor == DIM_OFF_HOLD_MS / 10);
    dim_profile_set(&p, 0, 10000, 12, 10000, 20);
    CHECK(p.on_q == 64000 && p.off_q == 64 && p.hold_q == 64);
    dim_fader_init(&f);
    set_profile(&f, &p);
    f.floor = 50;
    dim_fader_set(&f, 1, 1000);
    CHECK(dim_fader_tick(&f) == 1000);
    dim_profile_set(&p, 4000, 950, 12, 3300, 20);
    set_profile(&f, &p);
    dim_fader_set(&f, 0, 1000);
    while (f.current) {
        dim_fader_tick(&f);
    }
    dim_fader_set(&f, 1, 1000);
    int k = 0;
    while (f.current != 1000 && k < 2000) {
        dim_fader_tick(&f);
        k++;
    }
    CHECK(k >= 378 && k <= 382);
    /* the module's longest setting, 60 s for the whole range */
    dim_profile_set(&p, 60000, 60000, 0, 60000, 0);
    CHECK(p.on_q == 11 && p.off_q == 11 && p.hold_q == 11);
}

static void test_hold_up_from_a_lit_level_is_not_slowed(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    f.floor = 60;
    dim_fader_set(&f, 1, 130);
    for (int i = 0; i < 100; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 130);
    dim_fader_hold_start(&f, +1, 60, 1000);
    CHECK(dim_fader_tick(&f) == 130 + DIM_HOLD_RATE);
    for (int i = 0; i < 20; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current == 130 + 21 * DIM_HOLD_RATE);
    dim_fader_hold_stop(&f);
    dim_fader_hold_start(&f, -1, 60, 1000);
    int n = 0;
    while (f.current > 60 && n < 2000) {
        dim_fader_tick(&f);
        n++;
    }
    CHECK(n > (130 + 21 * DIM_HOLD_RATE - 60) * 3 / DIM_HOLD_RATE / 2);
}

static void test_timed_lit_to_lit_takes_the_time_at_one_rate(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 200);
    timed_ticks(&f, 200, 1000);
    dim_fader_set_timed(&f, 1, 800, 3000);
    for (int i = 0; i < 150; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current >= 498 && f.current <= 502);
    int n = timed_ticks(&f, 800, 1000);
    CHECK(n >= 149 && n <= 151);
    dim_fader_set_timed(&f, 1, 300, 500);
    n = timed_ticks(&f, 300, 1000);
    CHECK(n >= 49 && n <= 51);
}

static void test_timed_from_dark_starts_at_the_floor(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    f.floor = 100;
    dim_fader_set_timed(&f, 1, 600, 2000);
    CHECK(dim_fader_tick(&f) >= 100);
    int n = timed_ticks(&f, 600, 1000);
    CHECK(n >= 197 && n <= 200);
}

static void test_timed_off_reaches_the_floor_in_the_time_then_goes_dark(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    f.floor = 100;
    dim_fader_set(&f, 1, 600);
    timed_ticks(&f, 600, 1000);
    dim_fader_set_timed(&f, 0, 600, 1000);
    int n = timed_ticks(&f, 100, 1000);
    CHECK(n >= 91 && n <= 93);
    int m = timed_ticks(&f, 0, 100);
    CHECK(n + m >= 99 && n + m <= 102);
}

static void test_timed_zero_is_the_profile(void) {
    dim_fader_t a, b;
    dim_fader_init(&a);
    dim_fader_init(&b);
    dim_fader_set(&a, 1, 1000);
    dim_fader_set_timed(&b, 1, 1000, 0);
    CHECK(b.timed_left == 0);
    CHECK(timed_ticks(&a, 1000, 10000) == timed_ticks(&b, 1000, 10000));
}

static void test_timed_ends_with_a_new_target_or_a_hold_and_stays_for_the_same_target(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 100);
    timed_ticks(&f, 100, 1000);
    dim_fader_set_timed(&f, 1, 900, 8000);
    for (int i = 0; i < 10; i++) {
        dim_fader_tick(&f);
    }
    dim_fader_set(&f, 1, 900);
    CHECK(f.timed_left != 0);
    dim_fader_set_timed(&f, 1, 900, 8000);
    int n = timed_ticks(&f, 900, 1000);
    CHECK(n >= 788 && n <= 791);
    dim_fader_set_timed(&f, 1, 100, 8000);
    dim_fader_tick(&f);
    dim_fader_set(&f, 1, 950);
    CHECK(f.timed_left == 0);
    dim_fader_set_timed(&f, 1, 200, 5000);
    dim_fader_hold_start(&f, +1, 10, 1000);
    CHECK(f.timed_left == 0);
}

static void test_timed_extremes(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    dim_fader_set(&f, 1, 500);
    timed_ticks(&f, 500, 1000);
    dim_fader_set_timed(&f, 1, 501, 0xFFFF);
    int n = timed_ticks(&f, 501, 10000);
    CHECK(n >= 6552 && n <= 6554);
    dim_fader_set_timed(&f, 1, 510, 10000);
    for (int i = 0; i < 500; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current >= 505 && f.current <= 506);
    n = timed_ticks(&f, 510, 10000);
    CHECK(n >= 499 && n <= 501);
    dim_fader_set_timed(&f, 1, 1000, 1);
    CHECK(dim_fader_tick(&f) == 1000);
    dim_fader_set_timed(&f, 1, 1000, 3000);
    CHECK(f.timed_left == 0);
}

/* Past Open Dimmer's 65535 ms: the module's network times (up to 600 s), evenly, no overflow. */
static void test_timed_up_to_600_s(void) {
    dim_fader_t f;
    dim_fader_init(&f);
    f.floor = 10;
    dim_fader_set(&f, 1, 10);
    timed_ticks(&f, 10, 1000);
    dim_fader_set_timed(&f, 1, 1000, 600000);
    for (int i = 0; i < 30000; i++) {
        dim_fader_tick(&f);
    }
    CHECK(f.current >= 504 && f.current <= 506);          /* half way at 300 s */
    int n = timed_ticks(&f, 1000, 40000);
    CHECK(n >= 29999 && n <= 30001);
    dim_fader_set_timed(&f, 0, 1000, 120000);             /* off in 2 minutes */
    n = timed_ticks(&f, 0, 20000);
    CHECK(n >= 11999 && n <= 12002);
}

static void boosted(dim_fader_t *f, uint16_t ms) {
    dim_profile_t p;
    dim_fader_init(f);
    f->floor = 100;
    dim_profile_set(&p, DIM_FADE_ON_MS, DIM_FADE_OFF_MS, DIM_SOFT_END_PCT, DIM_HOLD_MS, DIM_HOLD_END_PCT);
    p.boost_ms = ms;
    set_profile(f, &p);
}

static void test_boost_at_full_then_straight_to_the_level(void) {
    dim_fader_t f;
    boosted(&f, 200);
    dim_fader_set(&f, 1, 300);
    for (int i = 0; i < 19; i++) {
        CHECK(dim_fader_tick(&f) == 1000);
    }
    dim_fader_tick(&f);
    CHECK(f.current == 300);
    dim_fader_set(&f, 0, 300);
    timed_ticks(&f, 0, 1000);
    dim_fader_set(&f, 1, 300);
    CHECK(dim_fader_tick(&f) == 1000);
}

static void test_boost_goes_on_through_the_level_after_on_and_ends_with_off_or_a_hold(void) {
    dim_fader_t f;
    boosted(&f, 300);
    dim_fader_set(&f, 1, 1000);
    dim_fader_tick(&f);
    dim_fader_set(&f, 1, 250);
    int n = timed_ticks(&f, 250, 100);
    CHECK(n >= 28 && n <= 30);
    boosted(&f, 300);
    dim_fader_set(&f, 1, 400);
    dim_fader_tick(&f);
    dim_fader_set(&f, 0, 400);
    CHECK(f.boost_left == 0 && f.current == 100);
    n = timed_ticks(&f, 0, 50);
    CHECK(n >= 1 && n <= DIM_OFF_HOLD_MS / 10 + 1);
    boosted(&f, 300);
    dim_fader_set(&f, 1, 400);
    dim_fader_hold_start(&f, +1, 100, 1000);
    CHECK(f.boost_left == 0);
    boosted(&f, 300);
    dim_fader_set(&f, 1, 400);
    dim_fader_hold_start_from_dark(&f, 100, 1000);
    CHECK(f.boost_left > 0);
    n = 0;
    while (f.boost_left && n < 100) {
        CHECK(dim_fader_tick(&f) == DIM_VALUE_MAX || !f.boost_left);
        n++;
    }
    CHECK(n >= 28 && n <= 30 && f.current == 100 && f.hold_dir == 1);
    f.hold_wait = 0;                                      /* (its wait at the min: tested above) */
    dim_fader_tick(&f);
    CHECK(f.current >= 100 && f.current < 120);
}

static void test_a_hold_from_dark_let_go_during_the_boost_stays_at_the_min(void) {
    dim_fader_t f;
    boosted(&f, 600);
    dim_fader_set(&f, 1, 400);
    dim_fader_hold_start_from_dark(&f, 100, 1000);
    for (int i = 0; i < 45; i++) {
        CHECK(dim_fader_tick(&f) == DIM_VALUE_MAX);
    }
    CHECK(dim_fader_hold_stop(&f) == 100);
    CHECK(f.hold_dir == 0 && f.target == 100);
    int n = 0;
    while (f.boost_left && n < 100) {
        CHECK(dim_fader_tick(&f) == DIM_VALUE_MAX || !f.boost_left);
        n++;
    }
    CHECK(n >= 14 && n <= 16);
    CHECK(f.current == 100);
    CHECK(dim_fader_tick(&f) == 100);
}

static void test_a_boost_does_not_lengthen_a_timed_on(void) {
    dim_fader_t f;
    boosted(&f, 300);
    dim_fader_set_timed(&f, 1, 600, 1000);
    int n = 0;
    while ((f.timed_left || f.boost_left) && n < 1000) {
        dim_fader_tick(&f);
        n++;
    }
    CHECK(n >= 99 && n <= 101 && f.current == 600);
}

static void test_no_boost_from_lit_or_when_off_and_a_timed_on_after_it(void) {
    dim_fader_t f;
    boosted(&f, 0);
    dim_fader_set(&f, 1, 300);
    CHECK(f.boost_left == 0);
    boosted(&f, 200);
    dim_fader_set(&f, 1, 300);
    timed_ticks(&f, 300, 100);
    dim_fader_set(&f, 1, 700);
    CHECK(f.boost_left == 0);
    boosted(&f, 100);
    dim_fader_set_timed(&f, 1, 600, 1000);
    for (int i = 0; i < 9; i++) {
        CHECK(dim_fader_tick(&f) == 1000);
    }
    uint16_t at = dim_fader_tick(&f);
    CHECK(at >= 140 && at <= 160);
    int n = timed_ticks(&f, 600, 1000);
    CHECK(n >= 89 && n <= 91);
}

static void test_lamp_compensation(void) {
    const uint16_t lo = 2760;
    CHECK(dim_comp(2760, 2760, 320, lo) == 0);
    CHECK(dim_comp(3000, 3000, 320, lo) == 0);
    CHECK(dim_comp(2760, 6000, 320, lo) == 320);
    CHECK(dim_comp(3260, 6000, 320, lo) == 160);
    CHECK(dim_comp(3700, 6000, 320, lo) == 19);
    CHECK(dim_comp(3760, 6000, 320, lo) == 0);
    CHECK(dim_comp(5000, 7000, 320, lo) == 0);
    CHECK(dim_comp(2760, 2810, 320, lo) == 160);
    CHECK(dim_comp(2760, 6000, 0, lo) == 0);
    CHECK(dim_comp(0, 6000, 320, lo) == 0);
    CHECK(dim_comp(2000, 6000, 320, lo) == 0);
    CHECK(dim_comp(100, 10000, 2520, 100) == 2520);
    uint16_t prev = dim_comp(3759, 9000, 2520, lo);
    for (uint16_t d = 3758; d >= lo; d--) {
        uint16_t c = dim_comp(d, 9000, 2520, lo);
        CHECK(c >= prev && c - prev <= 3);
        prev = c;
    }
}

int main(void) {
    test_fade_up_takes_two_seconds();
    test_fade_lands_exactly_on_target();
    test_off_fades_to_zero_and_keeps_nothing();
    test_level_clamped();
    test_hold_time_zero_is_the_100_ms_floor();
    test_hold_follows_without_lag_and_freezes_on_release();
    test_hold_stops_at_bounds();
    test_hold_up_from_dark_starts_at_min();
    test_hold_is_slow_just_above_the_min();
    test_remote_write_during_hold();
    test_off_during_hold_then_release_stays_off();
    test_fader_floor();
    test_hold_from_dark_and_cancel();
    test_fade_profile();
    test_hold_up_from_a_lit_level_is_not_slowed();
    test_timed_lit_to_lit_takes_the_time_at_one_rate();
    test_timed_from_dark_starts_at_the_floor();
    test_timed_off_reaches_the_floor_in_the_time_then_goes_dark();
    test_timed_zero_is_the_profile();
    test_timed_ends_with_a_new_target_or_a_hold_and_stays_for_the_same_target();
    test_timed_extremes();
    test_timed_up_to_600_s();
    test_boost_at_full_then_straight_to_the_level();
    test_boost_goes_on_through_the_level_after_on_and_ends_with_off_or_a_hold();
    test_a_hold_from_dark_let_go_during_the_boost_stays_at_the_min();
    test_a_boost_does_not_lengthen_a_timed_on();
    test_no_boost_from_lit_or_when_off_and_a_timed_on_after_it();
    test_lamp_compensation();
    printf("done %d\n", g_failed);
    return 0;
}
"""


def test_the_fader_passes_open_dimmers_host_tests():
    with tempfile.TemporaryDirectory() as tmp:
        c, exe = Path(tmp) / "fader.c", Path(tmp) / "fader"
        c.write_text(PROG)
        subprocess.run(["gcc", "-Wall", "-Wextra", "-Werror", "-DHK_DIMMER", "-I", str(ROOT / "src"),
                        str(c), str(ROOT / "src/zigbee/hk_fader.c"), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    assert out.strip().endswith("done 0"), out


def test_the_fader_is_open_dimmers_where_it_is_shared():
    """The sync note names what differs; the model the stub tests take their timings from
    (tests/hk_fader_model.py) follows the same rules."""
    import hk_fader_model as model
    f = model.Fader()
    f.current = 1000
    assert model.off_ms() == 1720 and model.hold_from_dark_top_ms() == 5310
    text = (ROOT / "src/zigbee/hk_fader.h").read_text()
    assert "open-dimmer" in text and "keep in sync" in text
