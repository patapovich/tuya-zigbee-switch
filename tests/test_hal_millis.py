"""hal_millis() on Telink counts milliseconds from the 16 MHz system timer,
which wraps every 268 s: src/hal/millis_acc.h must carry the count across
that wrap so that `now - then` on uint32_t milliseconds stays right."""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROG = r"""
#include <stdio.h>
#include <stdint.h>
#include "hal/millis_acc.h"

#define TPM 16000u                           /* Telink: 16 MHz system timer */

static int fails;
static void check(int ok, const char *what, uint64_t t) {
    if (!ok && fails++ < 5) printf("FAIL %s at tick %llu\n", what, (unsigned long long)t);
}

int main(void) {
    millis_acc_t acc = { 0 };
    uint64_t t = 0;                          /* true tick count, never wraps */
    uint32_t seed = 12345, prev = 0;

    /* Before the first wrap: the same value the plain conversion gave. */
    for (; t < 0xFFFFFFFFull; t += 1234567) {
        uint32_t ms = millis_acc_update(&acc, (uint32_t)t, TPM);
        check(ms == (uint32_t)t / TPM, "first period", t);
        prev = ms;
    }
    /* 63 more wraps, gaps from 0 up to one tick short of a whole wrap. */
    while (t < 64ull << 32) {
        seed = seed * 1103515245u + 12345u;
        uint64_t gap = (seed >> 3) % 7 == 0 ? 0xFFFFFFFFull - (seed % 3) : seed % 50000000u;
        t += gap;
        uint32_t ms = millis_acc_update(&acc, (uint32_t)t, TPM);
        check(ms == (uint32_t)(t / TPM), "exact", t);
        check((uint32_t)(ms - prev) == (uint32_t)(t / TPM) - prev, "elapsed", t);
        prev = ms;
    }
    /* The result itself wraps at 2^32 ms, where uint32_t arithmetic expects. */
    millis_acc_t late = { .last_tick = 0, .ms = 0xFFFFFF00u, .rem_ticks = 0 };
    uint32_t before = millis_acc_update(&late, 0, TPM);
    uint32_t after  = millis_acc_update(&late, 1000u * TPM, TPM);
    check(after - before == 1000u && after < before, "2^32 ms wrap", 0);
    printf("done %d\n", fails);
    return 0;
}
"""


def test_millis_accumulator_counts_on_across_the_timer_wrap():
    with tempfile.TemporaryDirectory() as tmp:
        c, exe = Path(tmp) / "millis.c", Path(tmp) / "millis"
        c.write_text(PROG)
        subprocess.run(["gcc", "-Wall", "-Werror", "-I", str(ROOT / "src"), str(c), "-o", str(exe)],
                       check=True)
        out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    assert out.strip().splitlines()[-1] == "done 0", out


def test_telink_main_loop_keeps_the_accumulator_fed():
    """The accumulator sees one wrap between two calls at most: the main loop
    calls hal_millis() on every pass, whatever else runs."""
    main = (ROOT / "src/telink/main.c").read_text()
    loop = main[main.index("while (1) {"):]
    assert loop.index("(void)hal_millis();") < loop.index("ev_main();")
    timer = (ROOT / "src/telink/hal/timer.c").read_text()
    assert "millis_acc_update(" in timer and "CLOCK_16M_SYS_TIMER_CLK_1MS" in timer
