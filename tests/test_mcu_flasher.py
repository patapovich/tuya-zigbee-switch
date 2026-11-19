"""The SWD master of the secondary MCU (src/zigbee/mcu_flasher.c, src/base_components/swd.c)
against the stub's edge-level PY32F002A model (src/stub/hal/swd_target.c): dormant without the
SW config token, the read-only surveys, and the ways they end."""
import struct
import zlib
from pathlib import Path

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
          "optr wrpr app_build option_crc rom_crc sector0_crc duration_ms page").split()
STATUS_FORMAT = "<8B5IHH3IHB"   # mcu_flasher_status_t without its length byte

OPTION_BYTES = bytes.fromhex("aaf95506ff0000ffffffffffffff0000")
# the model's synthetic ROM: its last 4 bytes make the CRC the real chip's (0xA52232EA)
ROM = bytes((i * 37 + 11) & 0xFF for i in range(0xE00 - 4)) + bytes.fromhex("e775a5e6")


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


# ---- update_bootloader (phase 2) ------------------------------------------------------------

import re  # noqa: E402

ATTR_CONFIRM = 0xFF52
JOB_UPDATE, JOB_UPDATE_FORCE = 3, 4
ERR_NOT_CONFIRMED, ERR_PROTECTED, ERR_HALT, ERR_FLASH, ERR_VERIFY = 7, 8, 9, 10, 11
STEP_PROGRAM = 11
BOOT_SRC = Path(__file__).resolve().parent.parent / "src" / "zigbee" / "secondary_mcu_boot.c"


def carried_boot() -> bytes:
    """The bootloader the module carries (secondary_mcu_boot.c), as bytes."""
    text = BOOT_SRC.read_text()
    body = text[text.index("secondary_mcu_boot[] = {"):text.index("};")]
    return bytes(int(h, 16) for h in re.findall(r"0x([0-9a-fA-F]{2})", body))


def sector(image: bytes) -> bytes:
    return image + b"\xFF" * (0x1000 - len(image))


def load_flash(proc: StubProc, tmp_path, image: bytes) -> None:
    path = tmp_path / "sector0.bin"
    path.write_bytes(sector(image))
    target(proc, "load", "0x08000000", path)


def confirm(device: Device, text: str) -> None:
    device.write_zigbee_attr(1, BASIC, ATTR_CONFIRM, text)


def flash_read(proc: StubProc, addr: int, words: int) -> bytes:
    out = b""
    for i in range(words):
        out += struct.pack("<I", int(target(proc, "peek32", f"0x{addr + 4 * i:08X}")["value"], 16))
    return out


def test_the_carried_bootloader_is_v2():
    boot = carried_boot()
    assert boot[0x10:0x14] == b"ODBL" and boot[0x14] >= 2
    assert crc(sector(boot)) == 0x7D215316  # what the test unit reports as DP 136


def test_a_writing_job_needs_its_name_first(flasher, tmp_path):
    proc, device = flasher
    load_flash(proc, tmp_path, carried_boot()[:-1] + b"\x00")
    start_job(device, JOB_UPDATE)
    st = status(device)
    assert (st["state"], st["error"]) == (STATE_IDLE, ERR_NOT_CONFIRMED)
    confirm(device, "update_bootloader_force")      # another job's name
    start_job(device, JOB_UPDATE)
    assert status(device)["error"] == ERR_NOT_CONFIRMED
    confirm(device, "update_bootloader")
    proc.exec("freeze_time 1")
    proc.exec("step_time 61000")                     # too old
    start_job(device, JOB_UPDATE)
    assert status(device)["error"] == ERR_NOT_CONFIRMED
    proc.exec("freeze_time 0")
    assert int(target(proc, "stats")["mem_writes"]) == 0


def test_an_equal_bootloader_is_not_written(flasher, tmp_path):
    proc, device = flasher
    load_flash(proc, tmp_path, carried_boot())
    confirm(device, "update_bootloader")
    start_job(device, JOB_UPDATE)
    st = run_until_settled(proc, device)
    assert (st["state"], st["error"]) == (STATE_DONE, ERR_NONE)
    assert st["sector0_crc"] == crc(sector(carried_boot()))
    stats = target(proc, "stats")
    assert int(stats["mem_writes"]) == 0 and int(stats["erases"]) == 0


def run_update(proc, device, job=JOB_UPDATE, name="update_bootloader") -> dict:
    confirm(device, name)
    start_job(device, job)
    return run_until_settled(proc, device, passes=2000)


def check_written(proc, st):
    boot = carried_boot()
    assert (st["state"], st["error"]) == (STATE_DONE, ERR_NONE), st
    assert st["sector0_crc"] == crc(sector(boot)) and st["boot_version"] == boot[0x14]
    assert flash_read(proc, 0x08000000, 0x400) == sector(boot)
    stats = target(proc, "stats")
    # page 0 (the vectors) erased first and programmed last
    log = target(proc, "flash_log")["list"].split(",")
    assert log[0] == "erase_page:0x08000000"
    programs = [e for e in log if e.startswith("program:")]
    assert programs[-1] == "program:0x08000000"
    assert int(stats["contention"]) == 0 and int(stats["wrperr"]) == 0
    # running the new bootloader: its reset vector, the watchdog freeze and the gates as left
    assert stats["core"] == "running"
    assert int(stats["pc"], 16) == struct.unpack_from("<I", boot, 4)[0] & ~1
    assert stats["iwdg_frozen"] == "1" and stats["gates_low"] == "1"
    assert int(target(proc, "peek32", "0x20000BF8")["value"], 16) == 0  # the boot word
    return log


def test_force_rewrites_an_equal_bootloader_vectors_last(flasher, tmp_path):
    proc, device = flasher
    load_flash(proc, tmp_path, carried_boot())
    st = run_update(proc, device, JOB_UPDATE_FORCE, "update_bootloader_force")
    log = check_written(proc, st)
    erased = [e for e in log if e.startswith("erase_page:")]
    assert len(erased) == 33           # page 0 first, then 1..31, then page 0 again for its program
    assert int(target(proc, "stats")["programs"]) == -(-len(carried_boot()) // 128)


def test_a_different_bootloader_is_replaced(flasher, tmp_path):
    proc, device = flasher
    old = bytearray(carried_boot())
    old[0x200] ^= 0xFF                 # an older build: another byte inside
    old += b"\x11" * 300               # and longer: its tail must go too
    load_flash(proc, tmp_path, bytes(old))
    check_written(proc, run_update(proc, device))


@pytest.mark.parametrize("setup", [("rdp", 1), ("poke32", "0x4002202C", "0x0000FFFE"),
                                   ("poke32", "0x40022020", "0x000000AA")])
def test_protection_or_unknown_option_bytes_refuse_the_write(flasher, tmp_path, setup):
    proc, device = flasher
    load_flash(proc, tmp_path, carried_boot()[:-1] + b"\x00")
    target(proc, *setup)
    st = run_update(proc, device)
    assert (st["state"], st["error"]) == (STATE_FAILED, ERR_PROTECTED)
    stats = target(proc, "stats")
    assert int(stats["erases"]) == 0 and int(stats["programs"]) == 0


def test_another_rom_refuses_the_write(flasher, tmp_path):
    proc, device = flasher
    load_flash(proc, tmp_path, carried_boot()[:-1] + b"\x00")
    target(proc, "poke32", "0x1FFF0100", "0x12345678")
    st = run_update(proc, device)
    assert (st["state"], st["error"]) == (STATE_FAILED, ERR_WRONG_TARGET)
    assert int(target(proc, "stats")["erases"]) == 0


def test_a_power_cut_while_writing_is_repaired_by_running_it_again(flasher, tmp_path):
    proc, device = flasher
    load_flash(proc, tmp_path, carried_boot()[:-1] + b"\x00")
    confirm(device, "update_bootloader")
    start_job(device, JOB_UPDATE)
    for _ in range(4000):
        proc.exec("s")
        st = status(device)
        if st["step"] == STEP_PROGRAM and st["page"] >= 6:
            break
    target(proc, "cut_after", 40)       # in the middle of a page
    st = run_until_settled(proc, device, passes=2000)
    assert st["state"] == STATE_FAILED
    target(proc, "power_cycle")          # the mains comes back: page 0 is blank
    assert target(proc, "stats")["core"] == "lockup"
    check_written(proc, run_update(proc, device))


# ---- install: a factory MCU (read-protected) to Open Dimmer (phase 4) ------------------------

from test_dimmer_mcu import tuya_frame  # noqa: E402

JOB_INSTALL = 5
STATE_ARMED = 5
ERR_WRONG_CLASS, ERR_OB_VERIFY, ERR_NO_ATTACH, ERR_RELOAD = 12, 13, 14, 15
OB_OPEN_DIMMER = bytes.fromhex("aaf95506ff0000ffffffffffffff0000")


def mcu_tells(proc: StubProc, version: int = 0x47) -> None:
    """The MCU answers the version query on the UART (0x47: the factory 1.0.7)."""
    proc.exec(f"uart_rx {tuya_frame(0x0B, bytes([version]))}")
    for _ in range(4):
        proc.exec("s")


def run_install(proc, device) -> dict:
    confirm(device, "install")
    start_job(device, JOB_INSTALL)
    return run_until_settled(proc, device, passes=4000)


def check_installed(proc, st):
    boot = carried_boot()
    assert (st["state"], st["error"]) == (STATE_DONE, ERR_NONE), st
    assert st["sector0_crc"] == crc(sector(boot)) and st["boot_version"] == boot[0x14]
    stats = target(proc, "stats")
    assert stats["rdp2_written"] == "0"                     # never level 2
    assert int(stats["mass_erases"]) == 1 and int(stats["obl_launches"]) >= 1
    assert stats["core"] == "running" and int(stats["contention"]) == 0
    assert flash_read(proc, 0x1FFF0E80, 4) == OB_OPEN_DIMMER  # Open Dimmer's option bytes
    assert flash_read(proc, 0x08000000, 0x400) == sector(boot)
    assert int(target(proc, "peek32", "0x40022020")["value"], 16) & 0xFFFF == 0xF9AA   # loaded
    assert flash_read(proc, 0x08001000, 4) == b"\xFF" * 16   # no app: the bootloader fetches it


@pytest.fixture
def stock(monkeypatch, tmp_path):
    monkeypatch.setenv("SWD_TARGET_PRESET", "stock")
    monkeypatch.setenv("SWD_TARGET_STATE", str(tmp_path / "py32.state"))
    return tmp_path


def test_install_needs_a_factory_version_on_the_uart():
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        confirm(device, "install")
        start_job(device, JOB_INSTALL)
        st = status(device)
        assert (st["state"], st["error"]) == (STATE_IDLE, ERR_WRONG_CLASS)
        assert int(target(proc, "stats")["line_resets"]) == 0


def test_install_refuses_an_unprotected_mcu():
    """An MCU that tells 1.x but is not read-protected is not a factory one: nothing written."""
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        mcu_tells(proc)
        st = run_install(proc, device)
        assert (st["state"], st["error"]) == (STATE_FAILED, ERR_WRONG_CLASS)
        stats = target(proc, "stats")
        assert stats["option_programs"] == "0" and int(stats["erases"]) == 0


def test_install_at_once_when_the_factory_firmware_leaves_the_pins(stock, monkeypatch):
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        mcu_tells(proc)
        check_installed(proc, run_install(proc, device))
        assert int(target(proc, "stats")["power_cycles"]) == 0      # no power cycle needed
    with StubProc(device_config=WITH_TOKEN) as proc:                # the job is gone from NV
        assert status(Device(proc))["state"] == STATE_IDLE


def test_install_armed_attaches_at_the_next_power_up(stock, monkeypatch):
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 2000")                  # the factory firmware has the pins now
        mcu_tells(proc)
        st = run_install(proc, device)
        assert (st["state"], st["page"]) == (STATE_ARMED, 3)   # power-cycle the module now
        assert target(proc, "stats")["pins_taken"] == "1"
        assert int(target(proc, "stats")["contention"]) == 0
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:  # mains off and on: both start again
        device = Device(proc)
        assert target(proc, "stats")["pins_taken"] == "0"     # the early hook halted it in time
        proc.exec("step_time 5000")
        check_installed(proc, run_until_settled(proc, device, passes=4000))


def test_an_install_that_never_attaches_gives_up(stock, monkeypatch):
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 2000")
        mcu_tells(proc)
        assert run_install(proc, device)["state"] == STATE_ARMED
        target(proc, "connect", 0)                   # the wires come off
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    for left in (2, 1, 0):
        with StubProc(device_config=WITH_TOKEN) as proc:
            st = status(Device(proc))
            assert (st["state"], st["page"]) == (STATE_ARMED, left)
    with StubProc(device_config=WITH_TOKEN) as proc:
        st = status(Device(proc))
        assert (st["state"], st["error"]) == (STATE_FAILED, ERR_NO_ATTACH)
        assert target(proc, "stats")["option_programs"] == "0"


def test_abort_disarms_an_armed_install(stock, monkeypatch):
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 2000")
        mcu_tells(proc)
        assert run_install(proc, device)["state"] == STATE_ARMED
        start_job(device, JOB_ABORT)
        assert status(device)["state"] == STATE_ABORTED
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        assert status(Device(proc))["state"] == STATE_IDLE
        assert target(proc, "stats")["pins_taken"] == "0"
