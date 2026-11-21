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


# NV item 59 (NV_ITEM_MCU_FLASHER_AUTO) in the stub's NV directory: the automatic install of
# a start-up with the wires (mcu_flasher.c auto_arm) was used.
AUTO_USED = Path("./stub_nvm_data/item_3b.bin")


@pytest.fixture(autouse=True)
def auto_install_used(request, cleanup_nvm):
    """Every test but the test_auto_* ones runs a module whose automatic install was used
    already: those test the manual jobs, which a first start-up with the wires would otherwise
    pre-empt."""
    if not request.node.name.startswith("test_auto_"):
        AUTO_USED.parent.mkdir(exist_ok=True)
        AUTO_USED.write_bytes(b"\xa5")


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


def check_installed(proc, st, reloaded: bool = True):
    """reloaded=False: an install taken up after a power cut, where the power-up loaded the
    option bytes (no OBL_LAUNCH needed)."""
    boot = carried_boot()
    assert (st["state"], st["error"]) == (STATE_DONE, ERR_NONE), st
    assert st["sector0_crc"] == crc(sector(boot)) and st["boot_version"] == boot[0x14]
    stats = target(proc, "stats")
    assert stats["rdp2_written"] == "0"                     # never level 2
    assert int(stats["mass_erases"]) == 1 and int(stats["obl_launches"]) >= (1 if reloaded else 0)
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
        assert stats["core"] == "running"          # told by its read protection, never stopped


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


# ---- the automatic install: two wires and a power-up (user decision 2026-11-19) ----------

def test_auto_install_at_the_first_power_up_with_the_wires(stock, monkeypatch):
    """No Zigbee command at all: the start-up sees the wires and installs."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        check_installed(proc, run_until_settled(proc, device, passes=4000))
        assert target(proc, "stats")["pins_taken"] == "0"      # halted in time
        assert AUTO_USED.read_bytes() == b"\xa5"
        resets = target(proc, "stats")["line_resets"]          # the model's counts carry on
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:            # once: the next one leaves it
        assert status(Device(proc))["state"] == STATE_IDLE
        assert target(proc, "stats")["line_resets"] == resets


def test_auto_install_waits_for_the_wires(stock, monkeypatch):
    """Converted first, opened later: start-ups without the wires touch nothing; the
    power-up after the soldering installs."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    monkeypatch.setenv("SWD_TARGET_CONNECT", "0")
    for _ in range(2):
        with StubProc(device_config=WITH_TOKEN) as proc:
            assert status(Device(proc))["state"] == STATE_IDLE
            assert int(target(proc, "stats")["line_resets"]) == 0
        assert not AUTO_USED.exists()
    monkeypatch.setenv("SWD_TARGET_CONNECT", "1")
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        check_installed(proc, run_until_settled(proc, Device(proc), passes=4000))



def status_reports(device: Device) -> int:
    return sum(1 for e in device._events if e.kind == "zcl_report_send"
               and int(e.payload["attr"], 16) == ATTR_STATUS)


def test_auto_armed_status_goes_out_once_on_the_network(stock, monkeypatch):
    """Armed at start-up, before the network: the status is reported when the module is on
    the network, then every 10 min while it waits (the converter's mcu_firmware tells the
    user to power-cycle it)."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    monkeypatch.setenv("SWD_TARGET_CONNECT", "0")
    with StubProc(device_config=WITH_TOKEN) as proc:            # the stock MCU runs on
        Device(proc)
        proc.exec("step_time 2000")
    monkeypatch.setenv("SWD_TARGET_CONNECT", "1")
    with StubProc(device_config=WITH_TOKEN, joined=False) as proc:
        device = Device(proc)

        def after(ms: int) -> int:
            proc.exec(f"step_time {ms}")
            proc.exec("step_time 1")    # an event of a task due at the end comes after the RES
            return status_reports(device)

        assert after(5000) == 0                                  # not on the network yet
        assert status(device)["state"] == STATE_ARMED
        proc.exec("net 1")
        assert after(1000) == 1
        assert after(590000) == 1
        assert after(15000) == 2                                 # 10 min later
        proc.exec("net 0")
        assert after(1000) == 2
        proc.exec("net 1")                                       # back on the network: at once
        assert after(1000) == 3

def test_auto_leaves_an_open_dimmer_mcu_running():
    """Wires on a unit that runs Open Dimmer already (read protection level 0): told before
    the core is touched, nothing written, never again."""
    with StubProc(device_config=WITH_TOKEN) as proc:
        st = status(Device(proc))
        assert (st["state"], st["job"], st["error"]) == (STATE_FAILED, JOB_INSTALL, ERR_WRONG_CLASS)
        stats = target(proc, "stats")
        assert stats["core"] == "running"
        assert stats["option_programs"] == "0" and int(stats["erases"]) == 0
    with StubProc(device_config=WITH_TOKEN) as proc:
        assert status(Device(proc))["state"] == STATE_IDLE
        assert int(target(proc, "stats")["line_resets"]) == 0


def test_auto_arms_when_the_factory_firmware_has_the_pins(stock, monkeypatch):
    """Wired first, converted later: the module's first start (after its OTA, the MCU running
    on) finds the pins held and arms; the next power-up installs."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    monkeypatch.setenv("SWD_TARGET_CONNECT", "0")
    with StubProc(device_config=WITH_TOKEN) as proc:            # the stock MCU runs on
        Device(proc)
        proc.exec("step_time 2000")
        assert target(proc, "stats")["pins_taken"] == "1"
    monkeypatch.setenv("SWD_TARGET_CONNECT", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:            # a restart of the module only
        st = status(Device(proc))
        assert (st["state"], st["error"]) == (STATE_ARMED, ERR_PINS_DRIVEN)
        assert int(target(proc, "stats")["contention"]) == 0
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:            # mains off and on
        check_installed(proc, run_until_settled(proc, Device(proc), passes=4000))


def test_auto_gives_up_once_for_good(stock, monkeypatch):
    """Armed by itself: a start-up that finds SWCLK held at once (the MCU ran on, a restart of
    the module alone) costs no power-up; power-ups without an answer do, and after them the
    install has failed and is not armed by itself again."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    monkeypatch.setenv("SWD_TARGET_CONNECT", "0")
    with StubProc(device_config=WITH_TOKEN) as proc:            # no wires yet: nothing
        Device(proc)
        target(proc, "clk_held_low", 1)                         # kept in the model's state
    monkeypatch.setenv("SWD_TARGET_CONNECT", "1")
    for _ in range(2):
        with StubProc(device_config=WITH_TOKEN) as proc:        # SWCLK held at once
            st = status(Device(proc))
            assert (st["state"], st["error"], st["page"]) == (STATE_ARMED, ERR_PINS_DRIVEN, 3)
    monkeypatch.setenv("SWD_TARGET_CONNECT", "0")               # the wires come off
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    seen = []
    for _ in range(5):
        with StubProc(device_config=WITH_TOKEN) as proc:
            st = status(Device(proc))
            seen.append((st["state"], st["page"]))
            stats = target(proc, "stats")
            assert stats["option_programs"] == "0" and int(stats["contention"]) == 0
    assert seen == [(STATE_ARMED, 2), (STATE_ARMED, 1), (STATE_ARMED, 0),
                    (STATE_FAILED, 0), (STATE_IDLE, 0)], seen


# ---- power cuts during an install (review 2026-11-19: a cut past the option bytes left a blank
# MCU that the next start-ups never took up) -------------------------------------------------

STEP_PROGRAM_, STEP_OB_WAIT_, STEP_OB_CHECK_, STEP_REATTACH_ = 11, 16, 17, 19
RESUME_ALWAYS = 0xFF


def install_until(proc, device, step: int, page: int = 0) -> dict:
    """A manual install of a factory MCU, run until it reaches step (and page)."""
    mcu_tells(proc)
    confirm(device, "install")
    start_job(device, JOB_INSTALL)
    for _ in range(4000):
        proc.exec("s")
        st = status(device)
        if st["step"] == step and st["page"] >= page:
            return st
        assert st["state"] == STATE_RUNNING, st
    raise AssertionError(f"never reached step {step}")


@pytest.mark.parametrize("step,page", [(STEP_PROGRAM_, 1), (STEP_PROGRAM_, 6), (STEP_OB_WAIT_, 0),
                                       (STEP_OB_CHECK_, 0), (STEP_REATTACH_, 0)])
def test_a_power_cut_past_the_option_bytes_is_taken_up_at_the_next_power_up(stock, monkeypatch, step, page):
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        install_until(proc, Device(proc), step, page)       # mains off here
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 3000")
        check_installed(proc, run_until_settled(proc, device, passes=4000), reloaded=False)
    with StubProc(device_config=WITH_TOKEN) as proc:         # the job is gone from NV
        assert status(Device(proc))["state"] == STATE_IDLE


def test_a_failure_past_the_option_bytes_keeps_the_job(stock, monkeypatch):
    """The link lost while the bootloader is written: the job is armed again (no limit), and
    the power-up with the link back installs."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        install_until(proc, device, STEP_PROGRAM_, 3)
        target(proc, "connect", 0)                           # a wire comes off
        st = run_until_settled(proc, device, passes=4000)
        assert (st["state"], st["page"]) == (STATE_ARMED, RESUME_ALWAYS), st
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    for _ in range(4):                                       # never given up while it is off
        with StubProc(device_config=WITH_TOKEN) as proc:
            device = Device(proc)
            proc.exec("step_time 3000")
            st = run_until_settled(proc, device, passes=400)
            assert (st["state"], st["page"]) == (STATE_ARMED, RESUME_ALWAYS), st
    monkeypatch.setenv("SWD_TARGET_CONNECT", "1")            # soldered again
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 3000")
        check_installed(proc, run_until_settled(proc, device, passes=4000))


JOB_ITEM = Path("./stub_nvm_data/item_3a.bin")   # NV_ITEM_MCU_FLASHER_JOB (58)
ERR_WRONG_TARGET_ = 3


def test_a_taken_up_install_that_finds_the_factory_firmware_is_a_limited_one(stock, monkeypatch):
    """PHASE_OB_WRITTEN in NV but the MCU at level 1 with its factory firmware (the option bytes
    never started): not taken up at every power-up, a limited install; a lasting refusal (here
    the ROM CRC) keeps it for its three more power-ups, then it ends, and the next power-up
    leaves the MCU alone."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    with StubProc(device_config=WITH_TOKEN) as proc:
        target(proc, "poke32", "0x1FFF0100", "0x12345678")   # the ROM CRC will not match
        target(proc, "save")
    JOB_ITEM.write_bytes(bytes([0xA7, 0x02, 0xFF, 0x00]))   # taken up, at every power-up
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    for left in (3, 2, 1):
        with StubProc(device_config=WITH_TOKEN) as proc:
            device = Device(proc)
            proc.exec("step_time 3000")
            st = run_until_settled(proc, device, passes=400)
            assert (st["state"], st["error"], st["page"]) == (STATE_ARMED, ERR_WRONG_TARGET_, left), st
            assert JOB_ITEM.read_bytes() == bytes([0xA7, 0x03, left, 0x00])
    with StubProc(device_config=WITH_TOKEN) as proc:        # its last power-up
        device = Device(proc)
        proc.exec("step_time 3000")
        st = run_until_settled(proc, device, passes=400)
        assert (st["state"], st["error"]) == (STATE_FAILED, ERR_WRONG_TARGET_), st
        assert not JOB_ITEM.exists()
    with StubProc(device_config=WITH_TOKEN) as proc:
        assert status(Device(proc))["state"] == STATE_IDLE
        stats = target(proc, "stats")
        assert stats["core"] == "running" and stats["option_programs"] == "0"


def test_a_confirm_longer_than_a_job_name_is_dropped():
    """The SDK takes a char string of any length into the 256-byte buffer; one longer than any
    job name is dropped at once, so a read of it never makes a response over one frame."""
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        confirm(device, "x" * 40)
        assert device.read_zigbee_attr(1, BASIC, ATTR_CONFIRM) == ""
        confirm(device, "install")
        assert device.read_zigbee_attr(1, BASIC, ATTR_CONFIRM) == "install"


def test_held_starts_count_as_one_power_up_per_twenty(stock, monkeypatch):
    """SWCLK held low at once at every start: 20 of them count as one power-up, so a short or a
    firmware that takes the pin at once still ends the job."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    with StubProc(device_config=WITH_TOKEN) as proc:
        target(proc, "clk_held_low", 1)
    JOB_ITEM.write_bytes(bytes([0xA7, 0x01, 0x03, 0x00]))   # an armed install, 3 power-ups
    pages = []
    for _ in range(21):
        with StubProc(device_config=WITH_TOKEN) as proc:
            pages.append(status(Device(proc))["page"])
    assert pages[:19] == [3] * 19 and pages[19] == 2 and pages[20] == 2, pages


# ---- review 2026-11-21: an abort, a second power cut and a marker left behind -----------------

ERR_KEPT = 16
PHASE_ARMED_, PHASE_OB_WRITTEN_, PHASE_RETAKEN_ = 1, 2, 3
STEP_ROM_ = 4


def job_item(phase: int, boots: int) -> bytes:
    return bytes([0xA7, phase, boots, 0x00])


def power_up_with_job(monkeypatch, phase: int = PHASE_OB_WRITTEN_, boots: int = RESUME_ALWAYS) -> None:
    """The next start of the stub is a power-up of both chips with this job in NV."""
    JOB_ITEM.write_bytes(job_item(phase, boots))
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")


# -- abort: past the option bytes it only stops the run; armed, an MCU that answers lets the job go

def test_abort_past_the_option_bytes_keeps_the_job_of_a_silent_mcu(stock, monkeypatch):
    """The bootloader half written, then abort: the MCU told its version before the install
    erased it, which does not count. The run stops, the job stays, the next power-up installs."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        install_until(proc, device, STEP_PROGRAM_, 3)
        start_job(device, JOB_ABORT)
        st = run_until_settled(proc, device, passes=50)
        assert (st["state"], st["error"], st["page"]) == (STATE_ARMED, ERR_KEPT, RESUME_ALWAYS), st
        assert JOB_ITEM.read_bytes() == job_item(PHASE_OB_WRITTEN_, RESUME_ALWAYS)
        start_job(device, JOB_ABORT)                         # armed now: the same answer
        st = status(device)
        assert (st["state"], st["error"]) == (STATE_ARMED, ERR_KEPT), st
        assert JOB_ITEM.exists()
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 3000")
        check_installed(proc, run_until_settled(proc, device, passes=4000), reloaded=False)


def test_abort_past_the_option_bytes_drops_the_job_of_an_mcu_that_answers(monkeypatch, tmp_path):
    """The job left in NV, the wires off, the MCU repaired by other means: it answers on the
    UART, so abort drops the job; until it answers, abort keeps it."""
    monkeypatch.setenv("SWD_TARGET_STATE", str(tmp_path / "py32.state"))
    monkeypatch.setenv("SWD_TARGET_CONNECT", "0")
    power_up_with_job(monkeypatch)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        assert status(device)["state"] == STATE_ARMED
        start_job(device, JOB_ABORT)
        st = status(device)
        assert (st["state"], st["error"]) == (STATE_ARMED, ERR_KEPT), st
        assert JOB_ITEM.exists()
        mcu_tells(proc, 0x86)
        start_job(device, JOB_ABORT)
        assert status(device)["state"] == STATE_ABORTED
        assert not JOB_ITEM.exists()


def test_abort_of_a_running_install_past_the_option_bytes_only_stops_it(monkeypatch, tmp_path):
    """Taken up at a power-up with an MCU that runs: what it sent before the job was armed does
    not let the job go, a frame after that does."""
    monkeypatch.setenv("SWD_TARGET_STATE", str(tmp_path / "py32.state"))
    power_up_with_job(monkeypatch)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        mcu_tells(proc, 0x86)
        assert status(device)["state"] == STATE_RUNNING
        start_job(device, JOB_ABORT)
        st = run_until_settled(proc, device, passes=50)
        assert (st["state"], st["error"], st["page"]) == (STATE_ARMED, ERR_KEPT, RESUME_ALWAYS), st
        assert JOB_ITEM.read_bytes() == job_item(PHASE_OB_WRITTEN_, RESUME_ALWAYS)
        start_job(device, JOB_ABORT)                         # nothing from the MCU since
        assert status(device)["state"] == STATE_ARMED and JOB_ITEM.exists()
        mcu_tells(proc, 0x86)
        start_job(device, JOB_ABORT)
        assert status(device)["state"] == STATE_ABORTED
        assert not JOB_ITEM.exists()


def test_abort_before_the_option_bytes_drops_the_job_as_before(stock, monkeypatch):
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    power_up_with_job(monkeypatch, PHASE_ARMED_, 3)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("s")
        st = status(device)
        assert (st["state"], st["step"]) == (STATE_RUNNING, STEP_ROM_), st
        start_job(device, JOB_ABORT)
        st = run_until_settled(proc, device, passes=50)
        assert (st["state"], st["error"]) == (STATE_ABORTED, ERR_NONE), st
        assert not JOB_ITEM.exists()
        assert target(proc, "stats")["option_programs"] == "0"


# -- an install taken up at level 1: its relaxed gate and its count live in NV

def corrupt_option_bytes(proc: StubProc) -> None:
    """What a power cut inside the option programming may leave: the model then loads level 1
    and a user byte that is neither the factory's nor Open Dimmer's."""
    for addr in (0x1FFF0E80, 0x1FFF0E84, 0x1FFF0E8C):
        target(proc, "poke32", f"0x{addr:08X}", "0x12345678")
    target(proc, "save")


def test_an_install_taken_up_at_level_1_survives_another_power_cut(stock, monkeypatch):
    """Option bytes a cut left corrupt, the install taken up, and the mains off again before
    it wrote them: the next power-up still takes them as they are and installs."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    with StubProc(device_config=WITH_TOKEN) as proc:
        corrupt_option_bytes(proc)
    power_up_with_job(monkeypatch)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        for _ in range(3):
            proc.exec("s")
        assert status(device)["state"] == STATE_RUNNING
        assert JOB_ITEM.read_bytes() == job_item(PHASE_RETAKEN_, 3)   # limited from here on
    with StubProc(device_config=WITH_TOKEN) as proc:          # the second cut was here
        device = Device(proc)
        for _ in range(3):
            proc.exec("s")
        assert JOB_ITEM.read_bytes() == job_item(PHASE_RETAKEN_, 2)   # this power-up counted
        proc.exec("step_time 3000")
        check_installed(proc, run_until_settled(proc, device, passes=4000))
    with StubProc(device_config=WITH_TOKEN) as proc:
        assert status(Device(proc))["state"] == STATE_IDLE


def test_a_failure_of_an_install_taken_up_at_level_1_keeps_it_for_its_power_ups(stock, monkeypatch):
    """The link lost while it reads the ROM: armed again with the power-ups it has left, not
    dropped; with the link away for good it counts down and ends."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    with StubProc(device_config=WITH_TOKEN) as proc:
        corrupt_option_bytes(proc)
    power_up_with_job(monkeypatch)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        for _ in range(3):
            proc.exec("s")
        target(proc, "connect", 0)                           # a wire comes off
        st = run_until_settled(proc, device, passes=400)
        assert (st["state"], st["error"], st["page"]) == (STATE_ARMED, ERR_NO_TARGET, 3), st
        assert JOB_ITEM.read_bytes() == job_item(PHASE_RETAKEN_, 3)
    seen = []
    for _ in range(5):
        with StubProc(device_config=WITH_TOKEN) as proc:
            st = status(Device(proc))
            seen.append((st["state"], st["page"]))
            assert target(proc, "stats")["option_programs"] == "0"
    assert seen == [(STATE_ARMED, 2), (STATE_ARMED, 1), (STATE_ARMED, 0),
                    (STATE_FAILED, 0), (STATE_IDLE, 0)], seen


def test_abort_of_an_install_taken_up_at_level_1_keeps_it_while_the_mcu_is_silent(stock, monkeypatch):
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    with StubProc(device_config=WITH_TOKEN) as proc:
        corrupt_option_bytes(proc)
    power_up_with_job(monkeypatch)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        for _ in range(3):
            proc.exec("s")
        start_job(device, JOB_ABORT)
        st = run_until_settled(proc, device, passes=50)
        assert (st["state"], st["error"], st["page"]) == (STATE_ARMED, ERR_KEPT, 3), st
        assert JOB_ITEM.read_bytes() == job_item(PHASE_RETAKEN_, 3)
        assert target(proc, "stats")["option_programs"] == "0"


# -- an install taken up at level 0: the chip identified before sector 0 is touched

@pytest.fixture
def unprotected(monkeypatch, tmp_path):
    """An MCU at level 0 with Open Dimmer's option bytes, kept over the stub's restarts."""
    monkeypatch.setenv("SWD_TARGET_STATE", str(tmp_path / "py32.state"))
    return tmp_path


def test_a_taken_up_install_leaves_an_mcu_that_has_the_bootloader_alone(unprotected, monkeypatch):
    """The job left in NV, the MCU brought up by other means: the carried bootloader is there,
    so nothing is written and the core is never stopped or reset."""
    with StubProc(device_config=WITH_TOKEN) as proc:
        load_flash(proc, unprotected, carried_boot())
        target(proc, "save")
    power_up_with_job(monkeypatch)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        st = run_until_settled(proc, device, passes=400)
        assert (st["state"], st["error"], st["job"]) == (STATE_DONE, ERR_NONE, JOB_INSTALL), st
        assert st["sector0_crc"] == crc(sector(carried_boot())) and st["rom_crc"] == crc(ROM)
        assert st["boot_version"] == carried_boot()[0x14]
        stats = target(proc, "stats")
        assert int(stats["mem_writes"]) == 0 and int(stats["sys_resets"]) == 0
        assert int(stats["erases"]) == 0 and stats["core"] == "running"
        assert stats["powered"] == "0" and int(stats["contention"]) == 0
        assert not JOB_ITEM.exists()
    with StubProc(device_config=WITH_TOKEN) as proc:
        assert status(Device(proc))["state"] == STATE_IDLE


def test_a_taken_up_install_never_writes_a_chip_it_cannot_identify(unprotected, monkeypatch):
    """The same marker, another ROM behind the wires: read, refused, kept armed; nothing
    written, the core never stopped."""
    with StubProc(device_config=WITH_TOKEN) as proc:
        load_flash(proc, unprotected, carried_boot()[:-1] + b"\x00")
        target(proc, "poke32", "0x1FFF0100", "0x12345678")
        target(proc, "save")
    power_up_with_job(monkeypatch)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        st = run_until_settled(proc, device, passes=400)
        assert (st["state"], st["error"], st["page"]) == (
            STATE_ARMED, ERR_WRONG_TARGET, RESUME_ALWAYS), st
        stats = target(proc, "stats")
        assert int(stats["mem_writes"]) == 0 and int(stats["sys_resets"]) == 0
        assert int(stats["erases"]) == 0 and stats["option_programs"] == "0"
        assert stats["core"] == "running"


def test_a_taken_up_install_replaces_another_bootloader_after_identifying_the_chip(unprotected, monkeypatch):
    old = bytearray(carried_boot())
    old[0x200] ^= 0xFF
    with StubProc(device_config=WITH_TOKEN) as proc:
        load_flash(proc, unprotected, bytes(old))
        target(proc, "save")
    power_up_with_job(monkeypatch)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        st = run_until_settled(proc, device, passes=4000)
        assert st["rom_crc"] == crc(ROM) and st["idcode"] == 0x60001000   # read in this session
        check_written(proc, st)
        assert not JOB_ITEM.exists()


def test_a_power_cut_while_the_bootloader_is_written_is_identified_then_taken_up(stock, monkeypatch):
    """Sector 0 partly written (its vectors blank, the core in lockup): the identity, the ROM
    and sector 0 are read first, then the bootloader is written."""
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        install_until(proc, Device(proc), STEP_PROGRAM_, 6)
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 3000")
        st = run_until_settled(proc, device, passes=4000)
        assert st["rom_crc"] == crc(ROM) and st["cpuid"] == 0x410CC601
        check_installed(proc, st, reloaded=False)
