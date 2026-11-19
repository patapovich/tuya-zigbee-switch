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
    assert got == {"endpoints": (2, 8), "in_clusters": (15, 32), "out_clusters": (1, 32),
                   "zcl_clusters": (17, 32), "attributes": (76, 128)}


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
