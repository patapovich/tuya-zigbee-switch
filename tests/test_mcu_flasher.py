"""The SWD master of the secondary MCU (src/zigbee/mcu_flasher.c, src/base_components/swd.c)
against the stub's edge-level PY32F002A model (src/stub/hal/swd_target.c): dormant without the
SW config token, the read-only surveys, and the ways they end."""
import struct
import zlib

import pytest

from client import StubProc
from conftest import Device
from test_dimmer_mcu import ZDMS16_2

BASIC = 0x0000
ATTR_STATUS = 0xFF50
ATTR_CONTROL = 0xFF51
JOB_SURVEY_DP, JOB_SURVEY_FULL, JOB_ABORT = 1, 2, 0xFF
STATE_IDLE, STATE_RUNNING, STATE_DONE, STATE_FAILED, STATE_ABORTED = range(5)
ERR_NONE, ERR_PINS_DRIVEN, ERR_NO_TARGET, ERR_WRONG_TARGET, ERR_SWD = range(5)
STEP_END = 7

TOKEN = "SWD3D7;"
WITH_TOKEN = ZDMS16_2 + TOKEN

FIELDS = ("format state job step error swd_err boot_version app_version dpidr ap_idr cpuid idcode "
          "optr wrpr app_build option_crc rom_crc sector0_crc duration_ms reserved").split()
STATUS_FORMAT = "<8B5IHH3IHB"   # mcu_flasher_status_t without its length byte

OPTION_BYTES = bytes.fromhex("aaf95506ff0000ffffffffffffff0000")
ROM = bytes((i * 37 + 11) & 0xFF for i in range(0xE00))


def crc(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def status(device: Device) -> dict:
    raw = bytes.fromhex(device.read_zigbee_attr(1, BASIC, ATTR_STATUS))
    assert len(raw) <= 48  # one frame (mcu_flasher.h MCU_FLASHER_STATUS_MAX)
    return dict(zip(FIELDS, struct.unpack(STATUS_FORMAT, raw)))


def start_job(device: Device, job: int) -> None:
    device.write_zigbee_attr(1, BASIC, ATTR_CONTROL, job)


def run_until_settled(proc: StubProc, device: Device, passes: int = 400) -> dict:
    for _ in range(passes):
        proc.exec("s")
        st = status(device)
        if st["state"] != STATE_RUNNING:
            return st
    return status(device)


def target(proc: StubProc, *args) -> dict:
    res = proc.exec("swd_target " + " ".join(str(a) for a in args))
    assert res.ok, res
    return res.payload


@pytest.fixture
def flasher():
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        target(proc, "reset")
        yield proc, device


# ---- dormancy -------------------------------------------------------------------------------

def test_without_the_token_nothing_is_registered_or_driven():
    with StubProc(device_config=ZDMS16_2) as proc:
        for attr in (ATTR_STATUS, ATTR_CONTROL):
            assert not proc.exec(f"zcl_read 1 0x{BASIC:04X} 0x{attr:04X}").ok
        for _ in range(20):
            proc.exec("s")
        stats = target(proc, "stats")
        assert int(stats["edges"]) == 0 and int(stats["untaken_ops"]) == 0


@pytest.mark.parametrize("token", ["SWB1D7;", "SWD3B7;", "SWA7D3;", "SWD3D3;", "SWD3;", "SWD3D7X;"])
def test_a_bad_token_leaves_it_off(token):
    with StubProc(device_config=ZDMS16_2 + token) as proc:
        assert not proc.exec(f"zcl_read 1 0x{BASIC:04X} 0x{ATTR_STATUS:04X}").ok


def test_a_token_on_the_uart_pins_of_a_w_token_leaves_it_off():
    with StubProc(device_config=ZDMS16_2 + "WD3C3;SWD3D7;") as proc:
        assert not proc.exec(f"zcl_read 1 0x{BASIC:04X} 0x{ATTR_STATUS:04X}").ok


def test_with_the_token_the_pins_stay_untouched_until_a_job(flasher):
    proc, device = flasher
    for _ in range(20):
        proc.exec("s")
    st = status(device)
    assert st["state"] == STATE_IDLE and st["format"] == 2
    stats = target(proc, "stats")
    assert int(stats["line_resets"]) == 0 and int(stats["dpidr_reads"]) == 0


# ---- surveys --------------------------------------------------------------------------------

def test_survey_dp_reads_the_dpidr_only(flasher):
    proc, device = flasher
    start_job(device, JOB_SURVEY_DP)
    st = run_until_settled(proc, device)
    assert (st["state"], st["error"], st["job"]) == (STATE_DONE, ERR_NONE, JOB_SURVEY_DP)
    assert st["dpidr"] == 0x0BC11477
    assert st["cpuid"] == 0 and st["rom_crc"] == 0  # no AP access
    stats = target(proc, "stats")
    assert int(stats["powered"]) == 0
    assert int(stats["contention"]) == 0 and int(stats["mem_writes"]) == 0


def test_survey_full_reads_identity_and_checksums(flasher, tmp_path):
    proc, device = flasher
    image = bytearray(b"\xFF" * 0x1100)
    image[0x10:0x20] = b"ODBL" + bytes([2, 0x0F, 0xFF, 0xFF]) + struct.pack("<II", 14336, 0x20000BE0)
    image[0x10C0:0x10D0] = b"APDZ" + struct.pack("<IIBBH", 13288, 0xD3DBD024, 0xA2, 0x18, 700)
    path = tmp_path / "flash.bin"
    path.write_bytes(bytes(image))
    target(proc, "load", "0x08000000", path)
    start_job(device, JOB_SURVEY_FULL)
    st = run_until_settled(proc, device)
    assert (st["state"], st["error"], st["step"]) == (STATE_DONE, ERR_NONE, STEP_END)
    assert (st["dpidr"], st["ap_idr"], st["cpuid"], st["idcode"]) == (
        0x0BC11477, 0x04770031, 0x410CC601, 0x60001000)
    assert (st["optr"], st["wrpr"]) == (0xF9AA, 0xFFFF)
    assert st["option_crc"] == crc(OPTION_BYTES)
    assert st["rom_crc"] == crc(ROM)
    assert st["sector0_crc"] == crc(bytes(image[:0x1000]))
    assert (st["boot_version"], st["app_version"], st["app_build"]) == (2, 0xA2, 700)
    stats = target(proc, "stats")
    assert int(stats["mem_writes"]) == 0          # a survey never writes the target
    assert int(stats["contention"]) == 0
    assert int(stats["protocol_errors"]) == 0 and int(stats["parity_errors"]) == 0
    assert int(stats["powered"]) == 0             # powered down again at the end


def test_survey_full_under_read_protection_skips_the_flash(flasher):
    proc, device = flasher
    target(proc, "rdp", 1)
    target(proc, "poke32", "0x40022020", "0x0000F055")   # what a stock unit shows (RDP 0x55: level 1)
    start_job(device, JOB_SURVEY_FULL)
    st = run_until_settled(proc, device)
    assert (st["state"], st["error"]) == (STATE_DONE, ERR_NONE)
    assert st["optr"] & 0xFF == 0x55 and st["rom_crc"] == crc(ROM)
    assert st["sector0_crc"] == 0 and st["app_version"] == 0 and st["boot_version"] == 0
    assert int(target(proc, "stats")["faults"]) == 0


def test_wait_answers_are_retried(flasher):
    proc, device = flasher
    target(proc, "wait_ap", 5)
    start_job(device, JOB_SURVEY_FULL)
    st = run_until_settled(proc, device)
    assert (st["state"], st["error"]) == (STATE_DONE, ERR_NONE)
    assert int(target(proc, "stats")["waits"]) == 5


# ---- failures -------------------------------------------------------------------------------

def test_no_wires_ends_in_no_target(flasher):
    proc, device = flasher
    target(proc, "connect", 0)
    start_job(device, JOB_SURVEY_FULL)
    st = run_until_settled(proc, device)
    assert (st["state"], st["error"], st["dpidr"]) == (STATE_FAILED, ERR_NO_TARGET, 0)


def test_a_held_swclk_is_never_driven(flasher):
    proc, device = flasher
    target(proc, "clk_held_low", 1)
    start_job(device, JOB_SURVEY_DP)
    st = run_until_settled(proc, device)
    assert (st["state"], st["error"]) == (STATE_FAILED, ERR_PINS_DRIVEN)
    stats = target(proc, "stats")
    assert int(stats["contention"]) == 0 and int(stats["line_resets"]) == 0


def test_another_chip_is_refused(flasher):
    proc, device = flasher
    target(proc, "poke32", "0x40015800", "0x12345678")   # DBGMCU IDCODE
    start_job(device, JOB_SURVEY_FULL)
    st = run_until_settled(proc, device)
    assert (st["state"], st["error"], st["idcode"]) == (STATE_FAILED, ERR_WRONG_TARGET, 0x12345678)
    assert st["rom_crc"] == 0


def test_abort_stops_a_running_job(flasher):
    proc, device = flasher
    start_job(device, JOB_SURVEY_FULL)
    for _ in range(8):
        proc.exec("s")
    assert status(device)["state"] == STATE_RUNNING
    start_job(device, JOB_ABORT)
    st = run_until_settled(proc, device)
    assert st["state"] == STATE_ABORTED
    # and a new job runs afterwards
    start_job(device, JOB_SURVEY_DP)
    assert run_until_settled(proc, device)["state"] == STATE_DONE


def test_a_second_job_does_not_disturb_the_running_one(flasher):
    proc, device = flasher
    start_job(device, JOB_SURVEY_FULL)
    proc.exec("s")
    start_job(device, JOB_SURVEY_DP)
    st = run_until_settled(proc, device)
    assert (st["state"], st["job"]) == (STATE_DONE, JOB_SURVEY_FULL)


def test_an_unknown_job_is_refused():
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        start_job(device, 7)
        st = status(device)
        assert st["state"] == STATE_IDLE and st["error"] == 6
