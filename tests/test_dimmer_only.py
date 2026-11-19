"""The DIMMER_ONLY build (device_db dimmer_only, the AVATTO ZDMS16-2): the
firmware leaves out the GPIO buttons, LEDs, relays and battery and the switch,
relay and cover endpoints (src/device_config/config_parser.c) to fit the SDK's
OTA size limit. For a board's own config it must be the full build: the same
endpoints and attributes, the same UART traffic to a stock and to an own MCU.
The tokens it leaves out are ignored, and no dimmer_only board's config uses
one."""

import subprocess
import sys
from pathlib import Path

import own_mcu
import pytest
import yaml
from client import StubProc
from conftest import Device
from test_dimmer_mcu import ZDMS16_2, ZDMS16_2_DP_CONFIG
from test_stock_mcu_compat import SCENARIOS, encode, run_scenario
from tuya_mcu import pump, take_tx
from zcl_consts import (
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CMD_ONOFF_OFF,
    ZCL_CMD_ONOFF_ON,
)

ROOT = Path(__file__).resolve().parent.parent
FULL = str(ROOT / "build/stub/stub_device")

# The tokens a DIMMER_ONLY build parses (config_parser.c): dimmers (DM, DR,
# Pxx, U), the MCU link (IT, W, Y) and the image type (i); not PT (relays).
DIMMER_TOKENS = ("DM", "DR", "P", "U", "IT", "W", "Y", "i")
CHECK = ROOT / "helper_scripts" / "check_dimmer_only_symbols.py"


def dimmer_only_boards() -> dict[str, tuple[str, str]]:
    """Board -> (config, DP config) of every dimmer_only board, the DP config
    built as board.mk builds it."""
    db = yaml.safe_load((ROOT / "device_db.yaml").read_text())
    return {
        name: (board["config_str"],
               "".join(a["dp"] + a["type"] + a["attr"] + ";"
                       for a in board.get("dp_attributes") or []))
        for name, board in db.items() if board.get("dimmer_only")
    }


@pytest.fixture(scope="module")
def dimmer_only(tmp_path_factory) -> str:
    binary = tmp_path_factory.mktemp("dimmer_only") / "stub_device"
    subprocess.run(["make", "-s", "-C", str(ROOT / "src/stub"), "build", f"BINARY={binary}",
                    "EXTRA_CFLAGS=-DDIMMER_ONLY"], check=True, capture_output=True)
    return str(binary)


def attribute_listing(binary: str, config: str, dp_config: str, cwd: Path) -> str:
    cwd.mkdir(exist_ok=True)            # the stub's NV lives in its working directory
    args = [binary, "--device-config", config, "--freeze-time"]
    if dp_config:
        args += ["--dp-config", dp_config]
    out = subprocess.run(args, input="zcl_list_attrs\nq\n", capture_output=True, text=True,
                         timeout=10, cwd=cwd, check=True).stdout
    return out[out.index("=== Zigbee Attributes ==="):]


def test_there_are_dimmer_only_boards():
    assert "MODULE_AVATTO_ZDMS16_2" in dimmer_only_boards()


@pytest.mark.parametrize("board", sorted(dimmer_only_boards()))
def test_a_dimmer_only_board_uses_only_the_tokens_its_build_parses(board):
    config, _ = dimmer_only_boards()[board]
    tokens = [t for t in config.split(";")[2:] if t]
    left_out = [t for t in tokens if t != "M" and
                (t.startswith("PT") or not t.startswith(DIMMER_TOKENS))]
    # M (switches momentary) is left out too, a no-op without switches.
    assert not left_out, f"{board}: {left_out} would be ignored by its DIMMER_ONLY build"


@pytest.mark.parametrize("board", sorted(dimmer_only_boards()))
def test_its_endpoints_and_attributes_are_those_of_the_full_build(board, dimmer_only, tmp_path):
    config, dp_config = dimmer_only_boards()[board]
    full = attribute_listing(FULL, config, dp_config, tmp_path / "full")
    assert "Endpoint 2:" in full
    assert attribute_listing(dimmer_only, config, dp_config, tmp_path / "dimmer_only") == full


def test_the_tokens_it_leaves_out_are_ignored(dimmer_only, tmp_path):
    config = "A;B;DM1;RA0;SA1u;CC2C3;XB4B5u;BTC5;LD2;IA4;BC0u;RT18;ST19;PT0E;SLP;LP50;D60;M;"
    listing = attribute_listing(dimmer_only, config, "", tmp_path / "dimmer_only")
    assert "Endpoint 1:" in listing and "Endpoint 2:" not in listing
    assert "Endpoint 6:" in attribute_listing(FULL, config, "", tmp_path / "full")


@pytest.mark.parametrize("name", SCENARIOS)
def test_a_stock_mcu_gets_what_the_full_build_sends(name, dimmer_only, monkeypatch):
    """The scenarios of the factory MCU compatibility gate, frame for frame and
    millisecond for millisecond."""
    full_mcu, full_state = run_scenario(name)
    monkeypatch.setenv("STUB_DEVICE", dimmer_only)
    mcu, state = run_scenario(name)
    assert encode(mcu) == encode(full_mcu)
    assert state == full_state


def own_mcu_run(monkeypatch) -> tuple[bytes, list, dict]:
    """First contact with an own MCU, then on, a level with a transition time,
    a dimming curve write and off on both channels: the module's UART bytes."""
    sent = []

    def recording_take_tx(proc):
        tx = take_tx(proc)
        sent.append(tx)
        return tx

    monkeypatch.setattr(own_mcu, "take_tx", recording_take_tx)
    proc = StubProc(device_config=ZDMS16_2, dp_config=ZDMS16_2_DP_CONFIG).start()
    try:
        device = Device(proc)
        pump(proc)
        mcu = own_mcu.own_mcu_contact(proc, device, build=400)
        for ep in (1, 2):
            device.call_zigbee_cmd(ep, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_ON, None)
            mcu.serve(500)
            device.call_zigbee_cmd(ep, ZCL_CLUSTER_LEVEL_CONTROL,
                                   ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, bytes([90, 15, 0]))
            mcu.serve(2000)
        device.write_zigbee_attr(1, 0x0000, 0xFF30, 3)
        mcu.serve(1000)
        for ep in (1, 2):
            device.call_zigbee_cmd(ep, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF, None)
            mcu.serve(2000)
        attrs = {f"{ep}/{cluster:04x}/{attr:04x}": device.read_zigbee_attr(ep, cluster, attr)
                 for ep in (1, 2) for cluster, attr in ((0x0006, 0x0000), (0x0008, 0x0000))}
        attrs["mcu_version"] = device.read_zigbee_attr(1, 0x0000, 0xFF12)
        return b"".join(sent), mcu.log, attrs
    finally:
        proc.stop()


def test_an_own_mcu_gets_what_the_full_build_sends(dimmer_only, monkeypatch):
    full = own_mcu_run(monkeypatch)
    monkeypatch.setenv("STUB_DEVICE", dimmer_only)
    got = own_mcu_run(monkeypatch)
    assert full[0] and "own" in full[2]["mcu_version"]
    assert got == full


# ---- the ELF check of every Telink board build (src/telink/Makefile
# check-dimmer-only), on nm listings ----------------------------------------

FULL_NM = """\
000081b8 T switch_cluster_add_to_endpoint
00007ab0 T relay_cluster_add_to_endpoint
00006c70 T dimmer_cluster_add_to_endpoint
00008818 T cover_switch_cluster_add_to_endpoint
00009098 T cover_cluster_add_to_endpoint
00009224 T battery_cluster_add_to_endpoint
"""
DIMMER_ONLY_NM = "00006c70 T dimmer_cluster_add_to_endpoint\n"


def check(tmp_path: Path, listing: str, dimmer_only: bool) -> subprocess.CompletedProcess:
    out = tmp_path / "listing.txt"
    out.write_text(listing)
    nm = tmp_path / "nm"
    nm.write_text(f"#!/bin/sh\ncat '{out}'\n")
    nm.chmod(0o755)
    args = [sys.executable, "-I", str(CHECK), "--nm", str(nm), "elf"]
    return subprocess.run(args + (["--dimmer-only"] if dimmer_only else []),
                          capture_output=True, text=True)


@pytest.mark.parametrize("built_dimmer_only", [True, False])
@pytest.mark.parametrize("board_dimmer_only", [True, False])
def test_the_elf_check_passes_only_when_the_build_matches_the_board(tmp_path, built_dimmer_only,
                                                                    board_dimmer_only):
    res = check(tmp_path, DIMMER_ONLY_NM if built_dimmer_only else FULL_NM, board_dimmer_only)
    assert (res.returncode == 0) == (built_dimmer_only == board_dimmer_only), res.stderr


@pytest.mark.parametrize("dimmer_only", [True, False])
def test_the_elf_check_wants_the_dimmer_endpoints_in_every_build(tmp_path, dimmer_only):
    listing = (DIMMER_ONLY_NM if dimmer_only else FULL_NM).replace(
        "00006c70 T dimmer_cluster_add_to_endpoint\n", "")
    assert check(tmp_path, listing, dimmer_only).returncode == 1


# Green Power left out of a dimmer_only build (src/telink/patch_sdk/gp_stub.c).
GP_NM = """\
0002c410 T dGp_dataInd
0002b520 T cgp_mcpsDataIndFilter
0002c6d0 T gpDataIndDuplicateFind
0002c7c8 T gpCcmStar
"""


@pytest.mark.parametrize("line", GP_NM.splitlines())
def test_the_elf_check_refuses_green_power_in_a_dimmer_only_build(tmp_path, line):
    assert check(tmp_path, DIMMER_ONLY_NM + line + "\n", True).returncode == 1


def test_the_elf_check_takes_green_power_or_none_in_other_builds(tmp_path):
    assert check(tmp_path, FULL_NM + GP_NM, False).returncode == 0
    assert check(tmp_path, FULL_NM, False).returncode == 0  # an end device: no GP in its stack
