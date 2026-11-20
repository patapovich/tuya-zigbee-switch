"""The board default's SW token (two-wire install of the MCU image, mcu_flasher.c) is added once
to a stored config that has none (config_nv.c device_config_swd_default, boards with an MCU
image; user decision 2026-11-20: on by default, soldering the wires is the consent). Modules
whose config was written before the default had the token get it with the update; a token
removed by hand later stays removed (NV item 65 marks the step done). A stub built with the
ZDMS16-2's default config (the main stub's default has no SW token: the step does nothing)."""
import subprocess
from pathlib import Path

import pytest
from client import StubProc
from conftest import Device

ROOT = Path(__file__).resolve().parent.parent
BOARD_DEFAULT = "jtbgusdc;TS0601-ZDMS16-2;DM2;M;SWD3D7;"
STORED = "jtbgusdc;TS0601-ZDMS16-2;DM2;M;"           # a house unit's config, written by hand
BASIC, DEVICE_CONFIG = 0x0000, 0xFF00


@pytest.fixture(scope="module")
def stub_with_board_default(tmp_path_factory) -> str:
    binary = tmp_path_factory.mktemp("swd_default") / "stub_device"
    subprocess.run(["make", "-s", "-C", str(ROOT / "src/stub"), "build", f"BINARY={binary}",
                    f"EXTRA_CFLAGS='-DDEFAULT_CONFIG={BOARD_DEFAULT}'"], check=True, capture_output=True)
    return str(binary)


def config_after_start(binary: str, stored: str | None) -> str:
    proc = StubProc(cmd=[binary], device_config=stored).start()
    try:
        return str(Device(proc).read_zigbee_attr(1, BASIC, DEVICE_CONFIG))
    finally:
        proc.stop()


def test_a_stored_config_without_the_token_gets_it_once(stub_with_board_default):
    assert config_after_start(stub_with_board_default, STORED) == STORED + "SWD3D7;"
    # Taken out by hand afterwards (the stub writes the given config again): it stays out.
    assert config_after_start(stub_with_board_default, STORED) == STORED


def test_a_config_with_a_token_is_kept_as_it_is(stub_with_board_default):
    own = STORED + "SWB4B5;"
    assert config_after_start(stub_with_board_default, own) == own


def test_no_separator_at_the_end_is_added(stub_with_board_default):
    assert config_after_start(stub_with_board_default, STORED[:-1]) == STORED + "SWD3D7;"
