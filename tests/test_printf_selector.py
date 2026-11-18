"""printf on Telink (src/hal/printf_selector.h): a release build has no
console (the SDK's drv_putchar() is empty), so the calls compile to nothing
and their strings stay out of the image; a debug build (make DEBUG=1) prints
through tl_printf as before."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SNIPPET = '#include "hal/printf_selector.h"\nCALL: printf("queue full %d\\r\\n", count);\n'


def expand(*defines: str) -> str:
    out = subprocess.run(
        ["gcc", "-E", "-P", "-x", "c", f"-I{ROOT / 'src'}", *defines, "-"],
        input=SNIPPET, capture_output=True, text=True, check=True,
    ).stdout
    return next(line for line in out.splitlines() if line.startswith("CALL:"))


def test_release_build_drops_printf_and_its_string():
    call = expand("-DHAL_TELINK")
    assert "queue full" not in call and "count" not in call
    assert expand("-DHAL_TELINK", "-DUART_PRINTF_MODE=0") == call


def test_debug_build_prints_through_tl_printf():
    call = expand("-DHAL_TELINK", "-DUART_PRINTF_MODE=1")
    assert 'tl_printf("queue full %d\\r\\n", count)' in call


def test_other_platforms_keep_the_c_library_printf():
    assert 'printf("queue full' in expand()
    assert "tl_printf" not in expand()
