"""A Python model of the TS110E's fader (src/zigbee/hk_fader.c, Open Dimmer's dim_core.c fader in
ms), for the stub tests' expected timings: where the light is after how many 10 ms ticks. Only the
parts the tests ask about (the profile, the plain / on / off fade with its slow end and its moment
at the floor, a hold with its slow zone and its wait from dark); tests/test_hk_fader.py checks the
C fader itself against Open Dimmer's host tests."""

TICK = 10
Q = 64
STEP_DEFAULT = 5
HOLD_BOTTOM_Q = 32
OFF_HOLD_MS = 80
DARK_WAIT_MS = 700


def rate_q(ms: int) -> int:
    if ms < 10:
        return 1000 * Q
    return max(1, (1000 * Q * 10 + ms // 2) // ms)


def eased_q(q: int, lo: int, d: int, span: int) -> int:
    if span == 0 or d >= span or q <= lo:
        return q
    e = (span - d) * (span - d) // span
    return q - (q - lo) * e // span


class Fader:
    """The channel's fader with the TS110E's defaults (fade on 1 s, off 1.5 s, hold 4 s, slow
    zones 15 / 25 % of min..max)."""

    def __init__(self, mn: int = 10, mx: int = 1000, fade_on: int = 1000, fade_off: int = 1500,
                 hold: int = 4000, soft_pct: int = 15, hold_pct: int = 25):
        rng = max(mx - mn, 0)
        self.floor, self.lo, self.hi = mn, mn, mx
        self.on_q, self.off_q = rate_q(fade_on), rate_q(fade_off)
        # hold_time over min..max (hk_dimmer.c profile_set; Open Dimmer's rate_q: over 0..1000)
        hold = max(hold, 100)
        self.hold_q = max(1, (rng * Q * 10 + hold // 2) // hold)
        self.soft_span = min(soft_pct, 100) * 10 * rng // 1000
        self.hold_span = min(hold_pct, 100) * 10 * rng // 1000
        self.current = self.target = 0
        self.acc = self.floor_hold = self.hold_wait = 0
        self.from_dark = self.hold_dir = self.hold_fine = 0

    def take(self, q: int) -> int:
        a = self.acc + q
        self.acc = a % Q
        return a // Q

    def hold_from_dark(self) -> None:
        self.target, self.hold_dir, self.hold_fine, self.acc = self.current, 1, 1, 0
        self.hold_wait = DARK_WAIT_MS

    def hold(self, direction: int) -> None:
        self.target, self.hold_dir, self.hold_fine, self.acc = self.current, direction, direction < 0, 0
        self.hold_wait = 0

    def tick(self) -> int:
        if self.hold_dir:
            cur = max(self.current, self.lo) if self.hold_dir > 0 else min(self.current, self.hi)
            if self.hold_wait:
                self.hold_wait = max(0, self.hold_wait - TICK)
                self.current = self.target = cur
                return cur
            d = max(cur - self.lo, 0)
            q = eased_q(self.hold_q, HOLD_BOTTOM_Q, d, self.hold_span) if self.hold_fine else self.hold_q
            step = self.take(q)
            cur = min(cur + step, self.hi) if self.hold_dir > 0 else max(cur - step, self.lo)
            self.current = self.target = cur
            return cur
        cur, tgt = self.current, self.target
        if cur < tgt:
            self.floor_hold = 0
            if cur < self.floor:
                cur, self.from_dark = self.floor, 1
            step = self.take(self.on_q if self.from_dark else STEP_DEFAULT * Q)
            cur = min(cur + step, tgt)
            if cur == tgt:
                self.from_dark = 0
        elif cur > tgt:
            self.from_dark = 0
            if tgt < self.floor and cur <= self.floor:
                self.floor_hold += TICK
                if self.floor_hold >= OFF_HOLD_MS:
                    self.floor_hold, cur = 0, tgt
            elif tgt < self.floor:
                step = self.take(eased_q(self.off_q, Q, cur - self.floor, self.soft_span))
                cur = max(cur - step, self.floor)
            else:
                cur = max(cur - self.take(STEP_DEFAULT * Q), tgt)
        self.current = cur
        return cur

    def ticks_to(self, value: int, limit: int = 100000) -> int:
        """Ticks until the light is at value (the tick that gets it there counted)."""
        n = 0
        while self.current != value and n < limit:
            self.tick()
            n += 1
        return n


def off_ms(start: int = 1000, **kw) -> int:
    """ms from an off (lit at start, settled) until dark."""
    f = Fader(**kw)
    f.current = start
    return f.ticks_to(0) * TICK


def hold_from_dark_at(ms: int, **kw) -> int:
    """The value a hold from dark has ms after its press (its first tick at the press)."""
    f = Fader(**kw)
    f.hold_from_dark()
    for _ in range(ms // TICK + 1):
        f.tick()
    return f.current


def hold_from_dark_top_ms(**kw) -> int:
    """ms from the press of a hold from dark until it is at the top."""
    f = Fader(**kw)
    f.hold_from_dark()
    return (f.ticks_to(f.hi) - 1) * TICK
