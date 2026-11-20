"""No per-function optimization in the firmware sources.

tc32-gcc 4.5 (the Telink toolchain) miscompiles a switch in a function under
`#pragma GCC optimize("Os")` or `__attribute__((optimize("Os")))` in a file built
with -O2: it lays out a byte / halfword jump table (as for -Os) but loads it as a
table of 32-bit addresses (as for -O2) and jumps to `mov pc` with garbage. The
ZDMS16-2 rebooted on every Scenes command (scene_cmd's switch) and would on every
wall switch gesture to bindings (dimmer_bind_gesture's): found on hardware
2026-11-20, the stub cannot show it. Size: noinline, simpler code, or a whole
file with its own flags, never a per-function optimize level."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def test_no_optimize_pragma_or_attribute():
    bad = []
    for f in sorted(SRC.rglob("*.[ch]")):
        for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            code = line.split("//")[0]
            if re.search(r"#\s*pragma\s+GCC\s+(optimize|push_options)|__attribute__\s*\(\(\s*optimize", code):
                bad.append(f"{f.relative_to(SRC)}:{n}: {line.strip()}")
    assert not bad, "per-function optimization (miscompiled switch on tc32):\n" + "\n".join(bad)
