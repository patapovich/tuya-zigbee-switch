"""Mgmt_Rtg_req answered from the routing table (src/telink/patch_sdk/zigbee/zdo/zdp.c.patch,
backported from SDK 4.0.x) in every router build but the ZDMS16-2's: its image has no room left
for it below the OTA limit, so src/telink/Makefile sets NO_MGMT_RTG_RSP for SECONDARY_MCU_IMAGE
and that router answers NOT_SUPPORTED, as SDK 3.7.2.0 does (its routes are missing from
Zigbee2MQTT's network map). The TS110E and every other router keep the answer.

- the Makefile: the flag exactly for SECONDARY_MCU_IMAGE;
- the patch: the answer, its prototype and its row of g_zdpClientFunc under the flag, the
  permit-join row (the TCLK check's wrap, test_tclk_check.py) untouched;
- the patched zdp.c compiled with tc32-gcc by the Makefile's own command for the ZDMS16-2, the
  TS110E and a plain router: the function and the table row (cluster 0x0032) in the TS110E's and
  the plain router's object only, the permit-join row in all;
- the check every board build runs on its linked ELF (helper_scripts/check_mgmt_rtg_symbol.py,
  Makefile check-mgmt-rtg): which way it is called per board, and what it says."""
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_tclk_check import make_vars

ROOT = Path(__file__).resolve().parent.parent
TELINK_DIR = ROOT / "src" / "telink"
SDK = ROOT / "telink_tools" / "sdk"
SDK_ZDP_C = SDK / "zigbee" / "zdo" / "zdp.c"
PATCH = TELINK_DIR / "patch_sdk" / "zigbee" / "zdo" / "zdp.c.patch"
TC32_BIN = ROOT / "telink_tools" / "toolchain" / "tc32" / "bin"
CHECK = ROOT / "helper_scripts" / "check_mgmt_rtg_symbol.py"

MGMT_RTG_REQ, MGMT_PERMIT_JOINING_REQ = 0x0032, 0x0036

BOARDS = {   # board.mk's variables of each kind of build
    "plain": {"DEVICE_TYPE": "router"},
    "ts110e": {"DEVICE_TYPE": "router", "DIMMER_ONLY": "1", "HK_DIMMER": "1"},
    "zdms16_2": {"DEVICE_TYPE": "router", "DIMMER_ONLY": "1", "SECONDARY_MCU_IMAGE": "1"},
}
ANSWERS = {"plain": True, "ts110e": True, "zdms16_2": False}


def patched_zdp(tmp_path: Path) -> Path:
    """The SDK's zdp.c, its CRs dropped and the patch applied, as sdk.mk does it."""
    orig = tmp_path / "zdp.c.orig"
    orig.write_bytes(SDK_ZDP_C.read_bytes().replace(b"\r", b""))
    out = tmp_path / "zdp.c"
    with PATCH.open("rb") as p, out.open("wb") as o:
        subprocess.run(["patch", "-s", "-o", "-", str(orig)], stdin=p, stdout=o, check=True)
    return out


@pytest.mark.parametrize("board", BOARDS)
def test_the_makefile_leaves_it_out_of_the_zdms16_2_only(board):
    v = make_vars(**BOARDS[board])
    assert ("-DNO_MGMT_RTG_RSP" in v["DEVICE_DEFS"]) == (not ANSWERS[board])
    ed = make_vars(**{**BOARDS[board], "DEVICE_TYPE": "end_device"})
    assert ("-DNO_MGMT_RTG_RSP" in ed["DEVICE_DEFS"]) == (board == "zdms16_2")   # no effect there


def test_the_patch_puts_the_answer_under_the_flag(tmp_path):
    if not SDK_ZDP_C.is_file():
        pytest.skip("no SDK checkout at telink_tools/sdk")
    text = patched_zdp(tmp_path).read_text(errors="replace")
    assert re.search(r"^#if defined\(ZB_ROUTER_ROLE\) && !defined\(NO_MGMT_RTG_RSP\)\n"
                     r"#define ZDP_MGMT_RTG_RSP\n"
                     r"static void zdo_mgmtRtgIndicate\(void \*buf\);\n#endif$", text, re.M)
    assert text.count("ZDP_MGMT_RTG_RSP") == 3                 # the define and two #ifdefs
    assert re.search(r"^#ifdef ZDP_MGMT_RTG_RSP\n\s+\{MGMT_RTG_REQ_CLID,\s+FALSE,\s+"
                     r"zdo_mgmtRtgIndicate\},\n#endif$", text, re.M)
    body = text[text.index("_CODE_ZDO_ static void zdo_mgmtRtgIndicate(void *buf)\n{"):]
    head = text[:text.index("_CODE_ZDO_ static void zdo_mgmtRtgIndicate(void *buf)\n{")]
    assert head.rstrip().endswith("#define ZDP_MGMT_RTG_UNUSED       0xFFFE")
    assert head[head.rindex("#ifdef") if "#ifdef" in head else 0:].startswith("#ifdef ZDP_MGMT_RTG_RSP")
    assert body[:body.index("_CODE_ZDO_ void zdp_init(void)")].rstrip().endswith("}\n#endif")
    # the permit-join row as in the SDK (router builds; the TCLK check wraps its handler)
    assert re.search(r"^#ifdef ZB_ROUTER_ROLE\n\s+\{MGMT_PERMIT_JOINING_REQ_CLID,\s+FALSE,\s+"
                     r"zdo_mgmtPermitJoinIndicate\},\n#endif$", text, re.M)


def tc32_object(tmp_path: Path, board: str) -> Path:
    """The patched zdp.c compiled by the Makefile's own command for it, with the board's
    variables."""
    if not ((TC32_BIN / "tc32-elf-gcc").is_file() and SDK_ZDP_C.is_file()):
        pytest.skip("no tc32 toolchain or SDK at telink_tools")
    obj = "../../build/telink/sdk/zigbee/zdo/zdp.o"
    args = [f"{k}={v}" for k, v in BOARDS[board].items()]
    out = subprocess.run(["make", "-s", "-C", str(TELINK_DIR), "-n", "-B", obj, *args],
                         capture_output=True, text=True, timeout=60).stdout
    cmd = [line for line in out.splitlines() if "tc32-elf-gcc" in line and " -c " in line]
    assert len(cmd) == 1, out
    src = "../../build/telink/sdk_patched/zigbee/zdo/zdp.c"
    assert f" {src}" in cmd[0] and f"-o {obj}" in cmd[0]
    d = tmp_path / board
    d.mkdir()
    o = d / "zdp.o"
    line = cmd[0].replace(f"-o {obj}", f"-o {o}").replace(f" {src}", f" {patched_zdp(d)}")
    subprocess.run(line, shell=True, cwd=TELINK_DIR, check=True, capture_output=True)
    return o


def table(obj: Path) -> tuple[list[int], list[str]]:
    """The cluster ids of g_zdpClientFunc's rows and the functions they point at."""
    dump = subprocess.run([str(TC32_BIN / "tc32-elf-objdump"), "-s", "-r", "-j",
                           ".rodata.g_zdpClientFunc", str(obj)], capture_output=True, text=True,
                          check=True).stdout
    funcs = re.findall(r"^[0-9a-f]{8} R_TC32_ABS32\s+(\w+)$", dump, re.M)
    data = bytes.fromhex("".join(w for line in dump.splitlines() if re.match(r"^ [0-9a-f]{4} ", line)
                                 for w in line.split()[1:5] if re.fullmatch(r"[0-9a-f]{2,8}", w)))
    assert len(data) % 8 == 0 and len(data) // 8 == len(funcs), (len(data), funcs)
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 8)], funcs


@pytest.mark.parametrize("board", BOARDS)
def test_tc32_builds_answer_it_but_the_zdms16_2(tmp_path, board):
    obj = tc32_object(tmp_path, board)
    syms = subprocess.run([str(TC32_BIN / "tc32-elf-nm"), str(obj)], capture_output=True,
                          text=True, check=True).stdout.split()
    clusters, funcs = table(obj)
    answers = ANSWERS[board]
    assert ("zdo_mgmtRtgIndicate" in syms) == answers
    assert (MGMT_RTG_REQ in clusters) == answers
    assert ("zdo_mgmtRtgIndicate" in funcs) == answers
    if answers:
        assert funcs[clusters.index(MGMT_RTG_REQ)] == "zdo_mgmtRtgIndicate"
    # the permit-join row in every router build (the TCLK check's wrap reaches it, ld --wrap)
    assert funcs[clusters.index(MGMT_PERMIT_JOINING_REQ)] == "zdo_mgmtPermitJoinIndicate"
    assert len(clusters) == 18 + answers          # the SDK's 18 router rows (+1)


# --- the check of the linked ELF (every board build) -------------------------------------------

def fake_nm(tmp_path: Path, listing: str) -> str:
    out = tmp_path / "listing.txt"
    out.write_text(listing)
    nm = tmp_path / "nm"
    nm.write_text(f"#!/bin/sh\ncat '{out}'\n")
    nm.chmod(0o755)
    return str(nm)


ROUTER = """\
0002ca40 T zdo_mgmtPermitJoinIndicate
00013e40 t zdo_mgmtRtgIndicate
0002fb20 R g_zdpClientFunc
"""
ZDMS16_2 = """\
0002ca40 T zdo_mgmtPermitJoinIndicate
00033684 R g_zdpClientFunc
00015b88 T zdo_mgmtRtgIndicate_x
"""


@pytest.mark.parametrize("listing, answers, ok", [
    (ROUTER, True, True), (ROUTER, False, False), (ZDMS16_2, False, True), (ZDMS16_2, True, False),
    ("", False, True), ("", True, False)])
def test_the_elf_check(tmp_path, listing, answers, ok):
    res = subprocess.run([sys.executable, "-I", str(CHECK), "--nm", fake_nm(tmp_path, listing),
                          "--answers" if answers else "--not-answers", str(tmp_path / "fw.elf")],
                         capture_output=True, text=True)
    assert (res.returncode == 0) == ok, res.stdout + res.stderr


def check_line(**overrides) -> str:
    args = [f"{k}={v}" for k, v in overrides.items()]
    out = subprocess.run(["make", "-s", "-C", str(TELINK_DIR), "-n", "check-mgmt-rtg", *args],
                         capture_output=True, text=True, timeout=60).stdout
    line = " ".join(out[out.index("check_mgmt_rtg_symbol.py"):].replace("\\\n", " ").split())
    return line.splitlines()[0]


@pytest.mark.parametrize("board", BOARDS)
def test_every_board_build_checks_its_elf_the_right_way(board):
    want = "--answers" if ANSWERS[board] else "--not-answers"
    assert f" {want} ../../build/telink/tlc_switch.elf" in check_line(**BOARDS[board])
    ed = check_line(**{**BOARDS[board], "DEVICE_TYPE": "end_device"})
    assert " --not-answers " in ed


def test_the_build_and_the_ota_files_run_the_check():
    """`build` (board.mk build-firmware) and `ota` (each OTA file links the ELF again)."""
    text = (TELINK_DIR / "Makefile").read_text()
    for target in ("build", "ota"):
        deps = re.search(rf"^{target}: (.*)$", text, re.M).group(1).split()
        assert "check-mgmt-rtg" in deps, target
