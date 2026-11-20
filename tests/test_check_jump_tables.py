"""helper_scripts/check_jump_tables.py on the dimmer-only board's ELF: built with -Os and
-fno-jump-tables, it has no switch jump table inside .text (tc32-gcc would dispatch one
wrongly). Needs the Telink toolchain; skipped without it."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TC32 = Path.home() / "tools/tc32/tc32/bin"
OBJDUMP = TC32 / "tc32-elf-objdump"
ELF = ROOT / "build/telink/tlc_switch.elf"


@pytest.mark.skipif(not OBJDUMP.exists() or not ELF.exists(), reason="no Telink build here")
def test_the_last_telink_build_has_no_inline_jump_table():
    r = subprocess.run([sys.executable, str(ROOT / "helper_scripts/check_jump_tables.py"),
                        "--objdump", str(OBJDUMP), str(ELF)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
