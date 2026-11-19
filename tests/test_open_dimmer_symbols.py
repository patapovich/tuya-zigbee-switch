"""The symbol check every Telink board build runs on its ELF
(helper_scripts/check_open_dimmer_symbols.py, src/telink/Makefile
check-open-dimmer): Open Dimmer support exactly in the boards whose device_db
entry has secondary_mcu_image (zdms16-py32 docs/BOOTLOADER.md section 15, rule
6). Here on host builds of the stub, with and without SECONDARY_MCU_IMAGE, and
on nm listings of the edge cases."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "helper_scripts" / "check_open_dimmer_symbols.py"
NM = shutil.which("nm")


def check(nm: str, elf: Path, with_image: bool) -> subprocess.CompletedProcess:
    args = [sys.executable, "-I", str(CHECK), "--nm", nm] + (["--with-image"] if with_image else [])
    return subprocess.run(args + [str(elf)], capture_output=True, text=True)


@pytest.fixture(scope="module")
def stubs(tmp_path_factory) -> dict[bool, Path]:
    """The stub as built for the tests (it carries the image), and the same
    sources without SECONDARY_MCU_IMAGE (the stubs of tuya_mcu_ota.c, the
    one-byte image of secondary_mcu_image.c), as a touch switch builds them."""
    without = tmp_path_factory.mktemp("noimg") / "stub_device"
    subprocess.run(["make", "-s", "-C", str(ROOT / "src/stub"), "build", f"BINARY={without}",
                    "EXTRA_CFLAGS=-USECONDARY_MCU_IMAGE"], check=True, capture_output=True)
    return {True: ROOT / "build/stub/stub_device", False: without}


@pytest.mark.skipif(NM is None, reason="no host nm")
@pytest.mark.parametrize("built_with", [True, False])
@pytest.mark.parametrize("board_has_image", [True, False])
def test_the_check_passes_only_when_the_build_matches_the_board(stubs, built_with,
                                                               board_has_image):
    res = check(NM, stubs[built_with], board_has_image)
    assert (res.returncode == 0) == (built_with == board_has_image), res.stdout + res.stderr


def fake_nm(tmp_path: Path, listing: str) -> str:
    """An nm that prints `listing` whatever it is asked."""
    out = tmp_path / "listing.txt"
    out.write_text(listing)
    nm = tmp_path / "nm"
    nm.write_text(f"#!/bin/sh\ncat '{out}'\n")
    nm.chmod(0o755)
    return str(nm)


ZDMS16 = """\
00843db0 0000000c b g_ident_timer
00006708 00000058 t ident_ask
00007120 00000180 T mcu_flasher_task
0002f988 00002cd0 R secondary_mcu_image
00032658 00000004 R secondary_mcu_image_size
000067ac 00000018 T tuya_mcu_ota_may_be_own
"""
TOUCH_SWITCH = """\
0002f988 00000001 R secondary_mcu_image
00032658 00000004 R secondary_mcu_image_size
000067ac 00000004 T tuya_mcu_ota_may_be_own
"""


@pytest.mark.parametrize("listing, with_image, ok", [
    (ZDMS16, True, True),
    (ZDMS16, False, False),
    (TOUCH_SWITCH, False, True),
    (TOUCH_SWITCH, True, False),
    # the linker dropped the unused one-byte image
    ("000067ac 00000004 T tuya_mcu_ota_may_be_own\n", False, True),
    # Open Dimmer code without an image, and an image without the code
    (TOUCH_SWITCH + "00843db0 0000000c b g_ident_timer\n", False, False),
    (ZDMS16.replace("00006708 00000058 t ident_ask\n", ""), True, False),
    # the SWD master in a board without an image, or missing in one with it
    (TOUCH_SWITCH + "00007120 00000180 T mcu_flasher_task\n", False, False),
    (ZDMS16.replace("00007120 00000180 T mcu_flasher_task\n", ""), True, False),
    (ZDMS16.replace("00002cd0 R secondary_mcu_image\n", "00000010 R secondary_mcu_image\n"),
     True, False),
])
def test_the_check_reads_the_listing(tmp_path, listing, with_image, ok):
    res = check(fake_nm(tmp_path, listing), tmp_path / "fw.elf", with_image)
    assert (res.returncode == 0) == ok, res.stdout + res.stderr
