"""The Telink HAL's ZCL tables (src/hal/zigbee_limits.h): every Telink board's config fits
them, as the stub counts them at init; the AVATTO ZDMS16-2's counts and headroom are pinned
(more endpoints and clusters, e.g. Scenes, must be planned against them). A config over the
limits is never written past: the Telink HAL leaves out what does not fit and counts it in
the MCU link diagnostics (zcl_tables_dropped)."""
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
LINE = re.compile(r"ZCL tables: endpoints (\d+)/(\d+) in_clusters (\d+)/(\d+) out_clusters "
                  r"(\d+)/(\d+) zcl_clusters (\d+)/(\d+) attributes (\d+)/(\d+)")
KEYS = ("endpoints", "in_clusters", "out_clusters", "zcl_clusters", "attributes")


def boards() -> dict:
    db = yaml.safe_load((ROOT / "device_db.yaml").read_text())
    return {name: b for name, b in db.items()
            if b.get("build", True) and b.get("mcu_family") == "Telink"}


def counts(tmp_path: Path, config: str, dp_config: str = "") -> dict[str, tuple[int, int]]:
    """(used, limit) per table, from the stub's log at init (its NV in tmp_path)."""
    stub = os.environ.get("STUB_DEVICE", str(ROOT / "build/stub/stub_device"))
    args = [stub, "--device-config", config, "--freeze-time"]
    if dp_config:
        args += ["--dp-config", dp_config]
    out = subprocess.run(args, input="q\n", capture_output=True, text=True, timeout=10,
                         cwd=tmp_path).stderr
    m = LINE.search(out)
    assert m, out[-2000:]
    v = list(map(int, m.groups()))
    return {k: (v[2 * i], v[2 * i + 1]) for i, k in enumerate(KEYS)}


def dp_config_of(board: dict) -> str:
    return "".join(a["dp"] + a["type"] + a["attr"] + ";" for a in board.get("dp_attributes") or [])


def test_every_telink_board_fits_the_zcl_tables(tmp_path):
    """All boards at once (a stub run each, in parallel)."""
    from concurrent.futures import ThreadPoolExecutor

    def run(item):
        name, board = item
        cwd = tmp_path / name
        cwd.mkdir()
        return name, counts(cwd, board["config_str"], dp_config_of(board))

    with ThreadPoolExecutor(os.cpu_count() or 4) as pool:
        results = dict(pool.map(run, sorted(boards().items())))
    assert len(results) > 100
    over = {name: got for name, got in results.items()
            if any(used > limit for used, limit in got.values())}
    assert over == {}


def test_the_zdms16_2_counts_and_headroom(tmp_path):
    board = boards()["MODULE_AVATTO_ZDMS16_2"]
    got = counts(tmp_path, board["config_str"], dp_config_of(board))
    print("ZDMS16-2 ZCL tables:", got)
    # Scenes (DIMMER_SCENES): +1 cluster and +5 attributes per dimmer endpoint; the wall
    # switches' endpoints 3 / 4 (DIMMER_BINDINGS): On/Off, Level Control and Scenes clients each,
    # and per dimmer 3 binding settings (genOnOffSwitchCfg); attributes: also the SWD master's 5
    # (status, control, confirm, and in the stub's debug build dump address and data; SWD3D7 in
    # the board's config) and the start-up's look at the factory NV (0xff55); per
    # dimmer Open Dimmer 2.2.8's lamp
    # compensation and custom curve (lightingBallastCfg 0xff11, 0xff12), the long press from off
    # and the double tap (genOnOffSwitchCfg 0xff15, 0xff16).
    assert got == {"endpoints": (4, 8), "in_clusters": (17, 32), "out_clusters": (7, 32),
                   "zcl_clusters": (24, 32), "attributes": (110, 128)}    # 23 + the SDK's OTA


def test_a_config_over_the_limits_is_said_so(tmp_path):
    """10 endpoints (the default test config of the suite): more than the Telink HAL takes."""
    stub = os.environ.get("STUB_DEVICE", str(ROOT / "build/stub/stub_device"))
    out = subprocess.run([stub, "--device-config", "A;B;SA0u;SA1u;SA2u;SA3u;RB0;RB1;RB2;RB3;RB4;RB5;",
                          "--freeze-time"], input="q\n", capture_output=True, text=True,
                         timeout=10, cwd=tmp_path).stderr
    assert "the ZCL tables of the Telink HAL would overflow" in out


def test_a_config_with_every_dp_listener_starts(tmp_path):
    """Dimmers, a DP relay and dp attributes on one MCU (with the health report, every user of
    the DP listener table, which stops the stub when full): it starts and maps its DPs."""
    from client import StubProc
    from conftest import Device
    from tuya_mcu import TUYA_CMD_REPORT_PASSIVE, enum_dp, mcu_report, value_dp
    proc = StubProc(device_config="A;B;DM1;RT18;M;", dp_config="65E30;").start()
    try:
        device = Device(proc)
        assert proc.is_running()
        mcu_report(proc, enum_dp(0x65, 3), value_dp(3, 300), cmd=TUYA_CMD_REPORT_PASSIVE)
        assert int(device.read_zigbee_attr(1, 0x0000, 0xFF30)) == 3          # dp attribute
        assert int(device.read_zigbee_attr(2, 0x0301, 0x0010)) == (300 * 254 + 500) // 1000  # ep 2: after the relay
    finally:
        proc.stop()


# The Telink SDK's names of the cluster ids get_register_func_by_cluster_id() tests.
SDK_CLUSTER_IDS = {
    "ZCL_CLUSTER_GEN_BASIC": 0x0000, "ZCL_CLUSTER_GEN_POWER_CFG": 0x0001,
    "ZCL_CLUSTER_POWER_CFG": 0x0001, "ZCL_CLUSTER_GEN_IDENTIFY": 0x0003,
    "ZCL_CLUSTER_GEN_GROUPS": 0x0004, "ZCL_CLUSTER_GEN_SCENES": 0x0005,
    "ZCL_CLUSTER_GEN_ON_OFF": 0x0006,
    "ZCL_CLUSTER_GEN_ON_OFF_SWITCH_CONFIG": 0x0007, "ZCL_CLUSTER_GEN_LEVEL_CONTROL": 0x0008,
    "ZCL_CLUSTER_GEN_MULTISTATE_INPUT_BASIC": 0x0012, "ZCL_CLUSTER_GEN_POLL_CONTROL": 0x0020,
    "ZCL_CLUSTER_CLOSURES_WINDOW_COVERING": 0x0102, "0x0301": 0x0301,
    "ZCL_CLUSTER_COVER_SWITCH_CONFIG": 0xFC01,
}
OTA = 0x0019


def registered_clusters(dimmer_only: bool) -> set[int]:
    """The cluster ids the Telink HAL has a register function for (only those reach the
    stack: an unregistered cluster has no attributes there), in a build with or without
    DIMMER_ONLY, read from src/telink/hal/zigbee_zcl.c."""
    text = (ROOT / "src/telink/hal/zigbee_zcl.c").read_text()
    body = text[text.index("get_register_func_by_cluster_id(u16 cluster_id) {"):]
    body = body[:body.index("\n}\n")]
    if dimmer_only:
        body = re.sub(r"#ifndef DIMMER_ONLY.*?#endif", "", body, flags=re.S)
    else:  # DIMMER_SCENES: the dimmer_only builds' (src/telink/Makefile)
        body = re.sub(r"#ifdef DIMMER_SCENES.*?#endif", "", body, flags=re.S)
    names = re.findall(r"cluster_id == (\w+)", body)
    unknown = [n for n in names if n not in SDK_CLUSTER_IDS]
    assert not unknown, f"add their ids to SDK_CLUSTER_IDS: {unknown}"
    return {SDK_CLUSTER_IDS[n] for n in names}


def board_clusters(cwd: Path, board: dict) -> set[int]:
    stub = os.environ.get("STUB_DEVICE", str(ROOT / "build/stub/stub_device"))
    args = [stub, "--device-config", board["config_str"], "--freeze-time"]
    if dp_config_of(board):
        args += ["--dp-config", dp_config_of(board)]
    out = subprocess.run(args, input="zcl_list_attrs\nq\n", capture_output=True, text=True,
                         timeout=10, cwd=cwd).stdout
    return {int(c, 16) for c in re.findall(r"Cluster 0x([0-9a-f]{4}) \((?:server|client)\)", out)}


def test_the_zdms16_2_gesture_cluster_is_registered_in_its_dimmer_only_build():
    """Its dimmer endpoints report Open Dimmer's wall switch gestures on genMultistateInput:
    without a register function the cluster never reached the stack (no reports)."""
    assert 0x0012 in registered_clusters(dimmer_only=True)
    assert {0x0003, 0x0005, 0x0006, 0x0007, 0x0008, 0x0301} <= registered_clusters(dimmer_only=True)
    assert 0x0005 not in registered_clusters(dimmer_only=False)


def test_every_cluster_of_every_telink_board_reaches_the_stack(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    def run(item):
        name, board = item
        cwd = tmp_path / name
        cwd.mkdir()
        return name, board_clusters(cwd, board) - {OTA}, bool(board.get("dimmer_only"))

    with ThreadPoolExecutor(os.cpu_count() or 4) as pool:
        results = list(pool.map(run, sorted(boards().items())))
    assert len(results) > 100 and all(clusters for _, clusters, _ in results)
    missing = {name: sorted(hex(c) for c in clusters - registered_clusters(dimmer_only))
               for name, clusters, dimmer_only in results
               if clusters - registered_clusters(dimmer_only)}
    assert missing == {}
