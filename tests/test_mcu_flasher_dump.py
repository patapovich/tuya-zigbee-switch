"""The dump job of the SWD master (src/zigbee/mcu_flasher.c) against the stub's PY32F002A model
(src/stub/hal/swd_target.c): an MCU at read protection level 0 read over Zigbee (a factory one
that left its flash open, or Open Dimmer), nothing written; a read-protected one refused before
its core is touched; the MCU's own firmware started again at the end."""
import struct

from client import StubProc
from conftest import Device
from test_mcu_flasher import (  # noqa: F401  (auto_install_used: autouse)
    BASIC, JOB_ABORT, JOB_ITEM, ROM, STATE_DONE, STATE_FAILED, STATE_IDLE, STATE_RUNNING,
    WITH_TOKEN, auto_install_used, carried_boot, crc, flash_read, job_item, load_flash,
    run_until_settled, start_job, status, target,
)

ATTR_DUMP_ADDR = 0xFF53
ATTR_DUMP_DATA = 0xFF54
JOB_DUMP = 6
STATE_ARMED = 5
ERR_BUSY, ERR_PROTECTED = 5, 8
STEP_DUMP = 21
DUMP_NONE, DUMP_FAILED = 0xFFFFFFFF, 0xFFFFFFFE
FLASH, FLASH_SIZE = 0x08000000, 0x8000          # with the hidden 12 KB
SYS = 0x1FFF0000
OPTB_OPEN_FACTORY = bytes.fromhex("aabe5541ff0000ffffffffffffff0000")   # OPTR 0x0000BEAA


def dump_chunk(device: Device, addr: int) -> tuple[int, bytes]:
    device.write_zigbee_attr(1, BASIC, ATTR_DUMP_ADDR, addr)
    raw = bytes.fromhex(device.read_zigbee_attr(1, BASIC, ATTR_DUMP_DATA))
    assert 4 <= len(raw) <= 48   # one frame
    return struct.unpack_from("<I", raw)[0], raw[4:]


def dump_area(device: Device, base: int, size: int) -> bytes:
    out = b""
    while len(out) < size:
        got, data = dump_chunk(device, base + len(out))
        assert got == base + len(out) and data
        out += data
    return out[:size]


def until_serving(proc: StubProc, device: Device, passes: int = 4000) -> dict:
    for _ in range(passes):
        proc.exec("s")
        st = status(device)
        if st["state"] != STATE_RUNNING or st["step"] == STEP_DUMP:
            return st
    return status(device)


def untouched(proc: StubProc) -> dict:
    stats = target(proc, "stats")
    assert int(stats["erases"]) == 0 and stats["option_programs"] == "0"
    assert int(stats["mass_erases"]) == 0 and int(stats["contention"]) == 0
    return stats


def end_dump(proc: StubProc, device: Device) -> dict:
    start_job(device, JOB_ABORT)
    st = run_until_settled(proc, device)
    assert (st["state"], st["error"]) == (STATE_DONE, 0), st
    assert dump_chunk(device, FLASH) == (DUMP_NONE, b"")   # served no more
    stats = untouched(proc)
    assert stats["core"] == "running" and int(stats["sys_resets"]) >= 1   # its firmware again
    return st


def open_factory(monkeypatch, tmp_path, pins_ms: str = "1500") -> None:
    monkeypatch.setenv("SWD_TARGET_PRESET", "stock_rdp0")
    monkeypatch.setenv("SWD_TARGET_STATE", str(tmp_path / "py32.state"))
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", pins_ms)


def test_an_open_factory_mcu_is_dumped_at_the_next_power_up(monkeypatch, tmp_path):
    open_factory(monkeypatch, tmp_path)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 2000")                       # its firmware has the pins now
        start_job(device, JOB_DUMP)                       # no confirm: nothing is written
        st = run_until_settled(proc, device)
        assert (st["state"], st["page"]) == (STATE_ARMED, 3)   # power-cycle the module now
        assert target(proc, "stats")["core"] == "running"
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        stats = target(proc, "stats")
        assert stats["pins_taken"] == "0" and stats["core"] == "halted"   # caught in time
        assert not JOB_ITEM.exists()                      # once: cut short, armed again by hand
        st = until_serving(proc, device)
        assert (st["state"], st["step"], st["error"]) == (STATE_RUNNING, STEP_DUMP, 0), st
        flash = flash_read(proc, FLASH, FLASH_SIZE // 4)
        assert st["optr"] & 0xFFFF == 0xBEAA
        assert st["sector0_crc"] == crc(flash) and st["rom_crc"] == crc(ROM)
        assert dump_area(device, FLASH, FLASH_SIZE) == flash
        assert dump_area(device, SYS, len(ROM)) == ROM
        assert dump_area(device, SYS + 0xE80, 16) == OPTB_OPEN_FACTORY
        end_dump(proc, device)
        assert flash_read(proc, FLASH, FLASH_SIZE // 4) == flash


def test_an_mcu_that_leaves_the_pins_is_dumped_at_once(monkeypatch, tmp_path):
    open_factory(monkeypatch, tmp_path, pins_ms="0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        start_job(device, JOB_DUMP)
        st = until_serving(proc, device)
        assert (st["state"], st["step"]) == (STATE_RUNNING, STEP_DUMP), st
        assert int(target(proc, "stats")["power_cycles"]) == 0
        got, data = dump_chunk(device, FLASH)
        assert got == FLASH and data == flash_read(proc, FLASH, 11)
        end_dump(proc, device)


def test_open_dimmer_is_dumped_without_a_power_cycle(tmp_path):
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        target(proc, "reset")
        load_flash(proc, tmp_path, carried_boot())        # vectors to start again from
        start_job(device, JOB_DUMP)
        st = until_serving(proc, device)
        assert (st["state"], st["step"]) == (STATE_RUNNING, STEP_DUMP), st
        assert target(proc, "stats")["core"] == "halted"   # its settings ring stays still
        end_dump(proc, device)


def test_a_read_protected_mcu_is_refused_and_held(monkeypatch, tmp_path):
    """Reading the protection powers the debug port, which closes the flash to the running core
    (it locks up): it is halted with the gates low until the next power cycle; nothing written."""
    monkeypatch.setenv("SWD_TARGET_PRESET", "stock")
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        start_job(device, JOB_DUMP)
        st = run_until_settled(proc, device)
        assert (st["state"], st["error"]) == (STATE_FAILED, ERR_PROTECTED), st
        stats = untouched(proc)
        assert stats["core"] == "halted" and stats["flash_locked"] == "1"


def test_a_read_protected_mcu_is_refused_at_the_power_up(monkeypatch, tmp_path):
    monkeypatch.setenv("SWD_TARGET_PRESET", "stock")
    monkeypatch.setenv("SWD_TARGET_STATE", str(tmp_path / "py32.state"))
    monkeypatch.setenv("SWD_TARGET_STOCK_PINS_MS", "1500")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 2000")
        start_job(device, JOB_DUMP)
        assert run_until_settled(proc, device)["state"] == STATE_ARMED
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        st = status(Device(proc))
        assert (st["state"], st["error"]) == (STATE_FAILED, ERR_PROTECTED), st
        stats = untouched(proc)
        assert stats["core"] == "halted" and stats["flash_locked"] == "1"
        assert not JOB_ITEM.exists()


def test_only_aligned_addresses_in_the_two_areas_are_read(monkeypatch, tmp_path):
    open_factory(monkeypatch, tmp_path, pins_ms="0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        assert dump_chunk(device, FLASH) == (DUMP_NONE, b"")      # no dump serving
        start_job(device, JOB_DUMP)
        assert until_serving(proc, device)["step"] == STEP_DUMP
        for addr in (FLASH + FLASH_SIZE, FLASH + 2, 0x40022020, 0x20000000, SYS + 0x1000,
                     FLASH - 4):
            assert dump_chunk(device, addr) == (DUMP_NONE, b""), hex(addr)
        got, data = dump_chunk(device, FLASH + FLASH_SIZE - 16)   # the end: what is left
        assert got == FLASH + FLASH_SIZE - 16 and len(data) == 16
        end_dump(proc, device)


def test_a_read_that_fails_is_told_and_the_next_one_works(monkeypatch, tmp_path):
    """The system memory has holes the model does not map (a bus error there): the answer says
    the read failed, the link is set up again, the next read is whole."""
    open_factory(monkeypatch, tmp_path, pins_ms="0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        start_job(device, JOB_DUMP)
        assert until_serving(proc, device)["step"] == STEP_DUMP
        assert dump_chunk(device, SYS + 0xE40) == (DUMP_FAILED, b"")
        got, data = dump_chunk(device, SYS + 0xE80)
        assert got == SYS + 0xE80 and data[:16] == OPTB_OPEN_FACTORY
        end_dump(proc, device)


def test_a_dump_left_alone_ends_by_itself(monkeypatch, tmp_path):
    open_factory(monkeypatch, tmp_path, pins_ms="0")
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        start_job(device, JOB_DUMP)
        assert until_serving(proc, device)["step"] == STEP_DUMP
        proc.exec("step_time 590000")
        dump_chunk(device, FLASH)                      # a read: the 10 minutes again
        proc.exec("step_time 590000")
        assert status(device)["state"] == STATE_RUNNING
        proc.exec("step_time 20000")
        st = run_until_settled(proc, device)
        assert (st["state"], st["error"]) == (STATE_DONE, 0)
        stats = untouched(proc)
        assert stats["core"] == "running" and int(stats["sys_resets"]) >= 1


def test_a_dump_does_not_take_the_place_of_an_armed_install():
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        JOB_ITEM.parent.mkdir(exist_ok=True)
        JOB_ITEM.write_bytes(job_item(1, 3))           # an install armed since this start
        start_job(device, JOB_DUMP)
        st = status(device)
        assert st["error"] == ERR_BUSY and st["state"] != STATE_RUNNING
        assert JOB_ITEM.read_bytes() == job_item(1, 3)


def test_abort_disarms_an_armed_dump(monkeypatch, tmp_path):
    open_factory(monkeypatch, tmp_path)
    with StubProc(device_config=WITH_TOKEN) as proc:
        device = Device(proc)
        proc.exec("step_time 2000")
        start_job(device, JOB_DUMP)
        assert run_until_settled(proc, device)["state"] == STATE_ARMED
        start_job(device, JOB_ABORT)
        assert not JOB_ITEM.exists()
    monkeypatch.setenv("SWD_TARGET_POWER_CYCLE", "1")
    with StubProc(device_config=WITH_TOKEN) as proc:
        assert status(Device(proc))["state"] == STATE_IDLE
        assert target(proc, "stats")["core"] == "running"
