"""The install of a factory MCU that left its flash open (read protection level 0; any firmware,
any variant; seen on a _TZE204_o9gyszw2) against the stub's PY32F002A model (preset
stock_rdp0): no protection change erases it, so the install erases its pages itself, then
writes the option bytes and the bootloader as for a read-protected one. Open Dimmer (its
bootloader or app there) is never taken for one."""
import struct

from client import StubProc
from conftest import Device
from test_mcu_flasher import (  # noqa: F401  (auto_install_used: autouse)
    ERR_PROTECTED, ERR_WRONG_CLASS, JOB_INSTALL, JOB_ITEM, OB_OPEN_DIMMER, STATE_ARMED,
    STATE_DONE, STATE_FAILED, STATE_RUNNING, WITH_TOKEN, auto_install_used, carried_boot, confirm,
    crc, flash_read, load_flash, mcu_tells, run_install, run_until_settled, sector, start_job,
    status, target,
)

STEP_ERASE_ALL = 22
FLASH, FLASH_SIZE = 0x08000000, 0x8000   # with the hidden 12 KB
VERSION_1_0_13 = 0x4D                    # 01.00.1101


def open_factory(monkeypatch, tmp_path, pins_ms: str = "1500") -> None:
    monkeypatch.setenv("SWD_TARGET_PRESET", "stock_rdp0")
    monkeypatch.setenv("SWD_TARGET_STATE", str(tmp_path / "py32.state"))
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", pins_ms)


def check_open_installed(proc: StubProc, st: dict) -> None:
    boot = carried_boot()
    assert (st["state"], st["error"]) == (STATE_DONE, 0), st
    assert st["sector0_crc"] == crc(sector(boot)) and st["boot_version"] == boot[0x14]
    stats = target(proc, "stats")
    assert int(stats["mass_erases"]) == 0 and stats["rdp2_written"] == "0"
    assert int(stats["erases"]) >= 64                       # the factory image, page by page
    assert stats["core"] == "running" and int(stats["contention"]) == 0
    assert flash_read(proc, 0x1FFF0E80, 4) == OB_OPEN_DIMMER   # Open Dimmer's option bytes
    assert flash_read(proc, FLASH, 0x400) == sector(boot)
    rest = FLASH_SIZE - 0x1000
    assert flash_read(proc, FLASH + 0x1000, rest // 4) == b"\xFF" * rest   # nothing of it left


def test_an_open_factory_mcu_is_installed_at_once(monkeypatch, tmp_path):
    open_factory(monkeypatch, tmp_path, pins_ms="0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        mcu_tells(proc, VERSION_1_0_13)
        check_open_installed(proc, run_install(proc, device))
        assert int(target(proc, "stats")["power_cycles"]) == 0
        assert not JOB_ITEM.exists()


def test_a_silent_open_factory_mcu_is_installed_too(monkeypatch, tmp_path):
    """Any factory firmware: no version on the UART is no reason to refuse; the attach tells."""
    open_factory(monkeypatch, tmp_path, pins_ms="0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        check_open_installed(proc, run_install(proc, Device(proc)))


def test_an_open_factory_mcu_is_installed_at_the_next_power_up(monkeypatch, tmp_path):
    open_factory(monkeypatch, tmp_path)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 2000")                  # the factory firmware has the pins now
        mcu_tells(proc, VERSION_1_0_13)
        st = run_install(proc, device)
        assert (st["state"], st["page"]) == (STATE_ARMED, 3)
        assert int(target(proc, "stats")["erases"]) == 0
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        assert target(proc, "stats")["pins_taken"] == "0"      # halted in time
        proc.exec("step_time 5000")
        check_open_installed(proc, run_until_settled(proc, device, passes=6000))


def test_auto_install_of_an_open_factory_mcu(monkeypatch, tmp_path):
    """The first start-up with the wires (the user's consent) takes an open factory MCU too."""
    open_factory(monkeypatch, tmp_path)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        st = status(device)
        assert (st["state"], st["job"]) == (STATE_RUNNING, JOB_INSTALL), st
        proc.exec("step_time 5000")
        check_open_installed(proc, run_until_settled(proc, device, passes=6000))


def test_a_power_cut_while_erasing_is_taken_up_at_the_next_power_up(monkeypatch, tmp_path):
    """From the first erase the factory firmware is gone: the job is kept as past the option
    bytes and goes on at the next power-up, which erases the rest."""
    open_factory(monkeypatch, tmp_path, pins_ms="0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        mcu_tells(proc, VERSION_1_0_13)
        confirm(device, "install")
        start_job(device, JOB_INSTALL)
        for _ in range(4000):
            proc.exec("s")
            if status(device)["step"] == STEP_ERASE_ALL:
                break
        for _ in range(20):                          # some pages erased
            proc.exec("s")
        st = status(device)
        assert (st["state"], st["step"]) == (STATE_RUNNING, STEP_ERASE_ALL), st
        assert 0 < int(target(proc, "stats")["erases"])
        assert JOB_ITEM.read_bytes() == bytes([0xA7, 0x02, 0xFF, 0x00])   # kept, every power-up
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 5000")
        check_open_installed(proc, run_until_settled(proc, device, passes=6000))
        assert not JOB_ITEM.exists()


def test_a_write_protected_open_mcu_is_refused_before_any_erase(monkeypatch, tmp_path):
    open_factory(monkeypatch, tmp_path, pins_ms="0")
    with StubProc(device_config=WITH_TOKEN) as proc:        # WRPR 0xFFFE: sector 0 protected
        target(proc, "poke32", "0x1FFF0E8C", "0x0001FFFE")   # loaded at the next power-up
        target(proc, "save")
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        st = run_install(proc, device)
        assert (st["state"], st["error"]) == (STATE_FAILED, ERR_PROTECTED), st
        stats = target(proc, "stats")
        assert int(stats["erases"]) == 0 and stats["option_programs"] == "0"
        assert stats["stock_fw"] == "1"                       # its firmware untouched


def test_an_open_dimmer_app_without_its_bootloader_is_not_a_factory_mcu(monkeypatch, tmp_path):
    """Open Dimmer's app header there (APDZ at 0x080010C0), no bootloader: refused, nothing
    written, the core never stopped."""
    open_factory(monkeypatch, tmp_path, pins_ms="0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        image = bytearray(b"\xFF" * 0x1100)
        image[0x10C0:0x10C4] = struct.pack("<I", 0x5A445041)   # "APDZ"
        path = tmp_path / "app.bin"
        path.write_bytes(bytes(image))
        target(proc, "load", "0x08000000", path)
        st = run_install(proc, device)
        assert (st["state"], st["error"]) == (STATE_FAILED, ERR_WRONG_CLASS), st
        stats = target(proc, "stats")
        assert int(stats["erases"]) == 0 and stats["option_programs"] == "0"
        assert stats["core"] == "running"
