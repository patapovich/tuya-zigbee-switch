"""The stub's dimmer-MCU model, phase 4: option byte programming, OBL_LAUNCH,
RDP level 1 with a debugger, the stock preset with its pin grab, and the start
from the environment / a state file. Through the reference SWD host."""

import os

import pytest
from client import StubProc
from swd_ref_host import (
    ACK_NONE,
    ACK_OK,
    ACK_WAIT,
    AP_DRW,
    AP_TAR,
    C_DEBUGEN,
    C_HALT,
    DEMCR,
    DHCSR,
    DHCSR_KEY,
    DP_CTRL_STAT,
    DP_DPIDR,
    S_HALT,
    S_LOCKUP,
    SwdRefHost,
)
from test_swd_target_core_flash import (
    CR_LOCK,
    CR_OPTLOCK,
    FLASH,
    FLASH_CR,
    FLASH_OPTR,
    FLASH_SR,
    FLASH_WRPR,
    OPTION_BYTES,
    SR_BSY,
    SR_EOP,
    SR_WRPERR,
    SRAM,
    Py32Flash,
    assert_bus_error,
    flash_log,
    load_app,
    peek,
    poke,
)
from test_swd_target_model import SWD_CLK, SWD_DIO, stats, target

DPIDR = 0x0BC11477
DEVICE_CONFIG = "StubManufacturer;StubDevice;LC0;SA0u;SA1u;SA2u;SA3u;RB0;RB1;RB2;RB3;CC0C1;CC2C3;"

FLASH_OPTKEYR = 0x4002200C
FLASH_SDKR = 0x40022024
OB_TRIGGER = 0x40022080
OPTKEY1 = 0x08192A3B
OPTKEY2 = 0x4C5D6E7F
CR_OPTSTRT = 1 << 17
CR_OBL_LAUNCH = 1 << 27
SR_OPTVERR = 1 << 15

OPTB_OPEN_DIMMER = bytes.fromhex("aaf95506ff0000ffffffffffffff0000")
OPTB_STOCK = bytes.fromhex("00f0ff0fff0000ffffffffffffff0000")


def option_bytes(p: StubProc) -> bytes:
    return b"".join(peek(p, OPTION_BYTES + o).to_bytes(4, "little") for o in range(0, 16, 4))


def poke_option_bytes(p: StubProc, raw: bytes) -> None:
    for o in range(0, 16, 4):
        poke(p, OPTION_BYTES + o, int.from_bytes(raw[o : o + 4], "little"))


class Py32Options(Py32Flash):
    """HAL_FLASH_OBProgram and HAL_FLASH_OB_Launch, step by step."""

    def unlock_options(self) -> None:
        self.h.mem_write32(FLASH_OPTKEYR, OPTKEY1)
        self.h.mem_write32(FLASH_OPTKEYR, OPTKEY2)

    def stage(self, optr: int, sdkr: int, wrpr: int) -> None:
        self.h.mem_write32(FLASH_OPTR, optr)
        self.h.mem_write32(FLASH_SDKR, sdkr)
        self.h.mem_write32(FLASH_WRPR, wrpr)

    def start(self) -> None:
        self.cr(self.cr() | CR_OPTSTRT)
        self.h.mem_write32(OB_TRIGGER, 0xFFFFFFFF)

    def program(self, optr: int, sdkr: int, wrpr: int) -> list[int]:
        self.stage(optr, sdkr, wrpr)
        self.start()
        return self.wait()

    def launch(self) -> None:
        """OBL_LAUNCH: the write itself is taken, then the link is gone."""
        self.h.mem_write32(FLASH_CR, self.cr() | CR_OBL_LAUNCH)


def session(p: StubProc) -> SwdRefHost:
    h = SwdRefHost(p, clk=SWD_CLK, dio=SWD_DIO)
    h.take()
    h.start_session()
    return h


@pytest.fixture
def host(stub_proc: StubProc) -> SwdRefHost:
    return session(stub_proc)


@pytest.fixture
def ob(host: SwdRefHost) -> Py32Options:
    return Py32Options(host)


def unlocked(ob: Py32Options) -> Py32Options:
    ob.unlock()
    ob.unlock_options()
    assert ob.cr() == 0
    return ob


# ---- option byte unlock and staging ----


def test_option_unlock_needs_the_flash_unlock_first(host: SwdRefHost, ob: Py32Options, stub_proc: StubProc):
    ob.unlock_options()  # CR.LOCK still set: ignored, no lockout
    assert ob.cr() == CR_LOCK | CR_OPTLOCK
    ob.unlock()
    assert ob.cr() == CR_OPTLOCK
    # OPTLOCK keeps OPTSTRT and OBL_LAUNCH out.
    ob.cr(CR_OPTSTRT | CR_OBL_LAUNCH)
    assert ob.cr() == CR_OPTLOCK
    assert stats(stub_proc)["obl_launches"] == 0
    ob.unlock_options()
    assert ob.cr() == 0
    st = stats(stub_proc)
    assert st["optlock"] == 0 and st["optkey_lockout"] == 0
    ob.cr(CR_OPTLOCK)  # set again by software
    assert ob.cr() == CR_OPTLOCK
    ob.cr(0)  # writing 0 does not clear it
    assert ob.cr() == CR_OPTLOCK
    ob.unlock_options()
    assert ob.cr() == 0


@pytest.mark.parametrize("keys", [[0x12345678], [OPTKEY1, OPTKEY1], [OPTKEY2]])
def test_a_wrong_option_key_locks_until_a_reset(host: SwdRefHost, ob: Py32Options, stub_proc: StubProc, keys):
    ob.unlock()
    for key in keys:
        host.mem_write32(FLASH_OPTKEYR, key)
    ob.unlock_options()
    assert ob.cr() == CR_OPTLOCK
    assert stats(stub_proc)["optkey_lockout"] == 1
    host.system_reset()
    assert stats(stub_proc)["optkey_lockout"] == 0
    unlocked(ob)


def test_option_registers_stage_until_a_load(host: SwdRefHost, ob: Py32Options, stub_proc: StubProc):
    unlocked(ob)
    ob.stage(0x0000F0AA, 0x0000001F, 0x0000FFFE)
    # Reads still give the loaded values.
    assert host.mem_read32(FLASH_OPTR) == 0x0000F9AA
    assert host.mem_read32(FLASH_WRPR) == 0x0000FFFF
    ob.start()
    assert ob.wait() == [SR_BSY] * 5 + [SR_EOP]  # busy_reads_ob 5
    assert option_bytes(stub_proc) == bytes.fromhex("aaf0550f1f00e0ffffffffffFEff0100")
    assert ob.cr() & CR_OPTSTRT == 0
    assert host.mem_read32(FLASH_OPTR) == 0x0000F9AA  # until a load
    assert flash_log(stub_proc) == ["program_options:0x1FFF0E80"]
    st = stats(stub_proc)
    assert st["option_programs"] == 1 and st["mass_erases"] == 0
    assert peek(stub_proc, FLASH) == 0xFFFFFFFF
    # OBL_LAUNCH loads them; the link drops.
    ob.launch()
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    host.start_session()
    assert host.mem_read32(FLASH_OPTR) == 0x0000F0AA
    assert host.mem_read32(FLASH_WRPR) == 0x0000FFFE
    assert host.mem_read32(FLASH_SDKR) == 0x0000001F
    assert host.mem_read32(FLASH_SR) & SR_OPTVERR == 0
    st = stats(stub_proc)
    assert st["obl_launches"] == 1 and st["rdp"] == 0


def test_writes_while_optlock_are_not_staged(host: SwdRefHost, ob: Py32Options, stub_proc: StubProc):
    ob.unlock()
    ob.stage(0x0000F0AA, 0x0, 0x0)  # OPTLOCK still set
    ob.unlock_options()
    ob.start()
    ob.wait()
    # The loaded values programmed back; SDKR as the register holds it (0x1F).
    assert option_bytes(stub_proc) == bytes.fromhex("aaf955061f00e0ffffffffffffff0000")
    # The trigger alone (no OPTSTRT) programs nothing.
    host.mem_write32(OB_TRIGGER, 0)
    assert stats(stub_proc)["option_programs"] == 1


def test_option_area_waits_while_busy(host: SwdRefHost, ob: Py32Options, stub_proc: StubProc):
    unlocked(ob)
    ob.stage(0x0000F9AA, 0x1F, 0xFFFF)
    ob.start()
    host.write_ap(AP_TAR, OPTION_BYTES)
    assert host.transfer(1, 1, AP_DRW).ack == ACK_WAIT
    host.write_ap(AP_TAR, FLASH)
    assert host.transfer(1, 1, AP_DRW).ack == ACK_WAIT
    ob.wait()
    assert host.mem_read32(OPTION_BYTES) == 0x0655F9AA


# ---- RDP ----


def test_rdp1_to_0_erases_the_flash(stub_proc: StubProc):
    target(stub_proc, "preset stock")
    assert stats(stub_proc)["stock_fw"] == 1
    host = session(stub_proc)
    ob = Py32Options(host)
    assert host.mem_read32(FLASH_OPTR) == 0x0000F000
    host.write_ap(AP_TAR, FLASH)
    host.read_ap(AP_DRW)
    assert_bus_error(host)  # level 1
    host.mem_write32(DHCSR, DHCSR_KEY | C_DEBUGEN)
    host.mem_write32(DEMCR, 1)  # halt after the option load
    unlocked(ob)
    ob.program(0x0000F0AA, host.mem_read32(FLASH_SDKR), host.mem_read32(FLASH_WRPR))
    assert option_bytes(stub_proc) == bytes.fromhex("aaf0550f1f00e0ffffffffffffff0000")
    assert all(peek(stub_proc, FLASH + o) == 0xFFFFFFFF for o in range(0, 0x5000, 0x100))
    assert flash_log(stub_proc) == ["program_options:0x1FFF0E80", "mass_erase:0x08000000"]
    st = stats(stub_proc)
    assert st["mass_erases"] == 1 and st["stock_fw"] == 0 and st["rdp"] == 1
    # Still level 1 until the load.
    host.write_ap(AP_TAR, FLASH)
    host.read_ap(AP_DRW)
    assert_bus_error(host)
    ob.launch()
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    assert host.connect() == DPIDR
    # Sticky flags cleared, the debug power and registers kept.
    cs = host.read_dp(DP_CTRL_STAT)
    assert cs & 0xA0 == 0 and cs >> 28 == 0xF
    host.mem_setup()
    assert host.mem_read32(FLASH_OPTR) == 0x0000F0AA
    assert host.mem_read32(FLASH) == 0xFFFFFFFF
    assert host.dhcsr() & (S_HALT | C_HALT) == S_HALT | C_HALT
    st = stats(stub_proc)
    assert st["rdp"] == 0 and st["obl_launches"] == 1 and st["core"] == "halted"


def test_obl_with_a_complement_mismatch(host: SwdRefHost, ob: Py32Options, stub_proc: StubProc):
    poke(stub_proc, OPTION_BYTES, 0x0655F9AB)
    unlocked(ob)
    ob.launch()
    host.start_session()
    assert host.mem_read32(FLASH_SR) & SR_OPTVERR
    assert host.mem_read32(FLASH_OPTR) == 0x0000FFFF  # RDP level 1, user byte 0xFF
    assert stats(stub_proc)["rdp"] == 1
    host.write_ap(AP_TAR, FLASH)
    host.read_ap(AP_DRW)
    assert_bus_error(host)
    host.mem_write32(FLASH_SR, SR_OPTVERR)
    assert host.mem_read32(FLASH_SR) & SR_OPTVERR == 0
    # A power-on load checks the same way.
    target(stub_proc, "power_cycle")
    host.start_session()
    assert host.mem_read32(FLASH_SR) & SR_OPTVERR


def test_rdp2_is_never_programmed(host: SwdRefHost, ob: Py32Options, stub_proc: StubProc):
    host.mem_write32(FLASH_OPTR, 0x0000F9CC)  # OPTLOCK set: not staged, still counted
    host.mem_write32(OPTION_BYTES, 0x0633F9CC)  # the area: ignored, counted
    assert stats(stub_proc)["rdp2_written"] == 2
    unlocked(ob)
    assert ob.program(0x0000F9CC, 0x1F, 0xFFFF) == [SR_WRPERR]
    assert option_bytes(stub_proc) == OPTB_OPEN_DIMMER
    st = stats(stub_proc)
    assert st["rdp2_written"] == 3 and st["option_programs"] == 0 and st["wrperr"] == 1
    assert flash_log(stub_proc) == ["wrperr:0x1FFF0E80"]


def test_rdp1_debugger_locks_up_a_running_core(stub_proc: StubProc):
    target(stub_proc, "preset stock")
    assert stats(stub_proc)["core"] == "running"
    h = SwdRefHost(stub_proc, clk=SWD_CLK, dio=SWD_DIO)
    h.take()
    h.connect()
    h.clear_errors()
    assert stats(stub_proc)["core"] == "running"  # not powered yet
    h.power_up()
    assert stats(stub_proc)["core"] == "lockup"
    h.mem_setup()
    assert h.dhcsr() & S_LOCKUP


def test_rdp1_debugger_keeps_a_halted_core_halted(stub_proc: StubProc):
    target(stub_proc, "rdp 1")
    poke(stub_proc, DHCSR, C_DEBUGEN | C_HALT)
    host = session(stub_proc)
    assert stats(stub_proc)["core"] == "halted"
    assert host.resume() & S_LOCKUP  # it runs, loses its flash
    assert host.halt() & S_HALT
    assert stats(stub_proc)["core"] == "halted"


def test_rdp0_debugger_leaves_a_running_core_alone(host: SwdRefHost, stub_proc: StubProc):
    assert stats(stub_proc)["core"] == "running"


def test_cut_during_option_programming(host: SwdRefHost, ob: Py32Options, stub_proc: StubProc):
    unlocked(ob)
    ob.stage(0x0000F9AA, 0x1F, 0xFFFF)
    ob.cr(CR_OPTSTRT)
    target(stub_proc, "cut_after 2")
    host.mem_write32(OB_TRIGGER, 0)
    assert option_bytes(stub_proc) == bytes(a & 0xA5 for a in OPTB_OPEN_DIMMER)
    assert flash_log(stub_proc)[-1] == "garbage:0x1FFF0E80"
    host.start_session()
    assert host.mem_read32(FLASH_SR) & SR_OPTVERR
    assert stats(stub_proc)["rdp"] == 1


# ---- the stock firmware's pin grab ----


def test_stock_firmware_takes_its_pins(stub_proc: StubProc):
    target(stub_proc, "preset stock")
    target(stub_proc, "stock_pins_ms 100")
    h = SwdRefHost(stub_proc, clk=SWD_CLK, dio=SWD_DIO)
    stub_proc.exec("step_time 99")
    assert h.idle_check() == 1
    stub_proc.exec("step_time 1")
    assert h.idle_check() == 0
    st = stats(stub_proc)
    assert st["pins_taken"] == 1 and st["uptime_ms"] == 100
    h.take()
    h.line_reset()
    h.idle(2)
    assert h.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    st = stats(stub_proc)
    assert st["edges"] == 0 and st["contention"] > 0
    h.release()
    # Until the next power cycle; then the time starts again.
    target(stub_proc, "power_cycle")
    assert h.idle_check() == 1
    stub_proc.exec("step_time 99")
    assert h.idle_check() == 1
    stub_proc.exec("step_time 1")
    assert h.idle_check() == 0


def test_rdp1_debugger_in_time_keeps_the_pins(stub_proc: StubProc):
    target(stub_proc, "preset stock")
    target(stub_proc, "stock_pins_ms 100")
    stub_proc.exec("step_time 50")
    session(stub_proc)  # the debug power-up locks the core up at level 1
    stub_proc.exec("step_time 200")
    h = SwdRefHost(stub_proc, clk=SWD_CLK, dio=SWD_DIO)
    assert h.idle_check() == 1
    assert stats(stub_proc)["pins_taken"] == 0


@pytest.mark.parametrize("halt", [True, False])
def test_a_halt_in_time_keeps_the_pins(stub_proc: StubProc, halt: bool):
    target(stub_proc, "preset stock")
    poke_option_bytes(stub_proc, OPTB_OPEN_DIMMER)  # a stock image at level 0
    target(stub_proc, "power_cycle")
    target(stub_proc, "stock_pins_ms 100")
    host = session(stub_proc)
    assert stats(stub_proc)["core"] == "running"
    if halt:
        host.halt()
        host.resume()
        assert stats(stub_proc)["core"] == "running"
    stub_proc.exec("step_time 150")
    assert host.idle_check() == (1 if halt else 0)
    assert stats(stub_proc)["pins_taken"] == (0 if halt else 1)


def test_erasing_the_stock_firmware_keeps_the_pins(stub_proc: StubProc):
    target(stub_proc, "preset stock")
    poke_option_bytes(stub_proc, OPTB_OPEN_DIMMER)
    target(stub_proc, "power_cycle")
    host = session(stub_proc)
    fl = Py32Flash(host)
    fl.unlock()
    fl.erase(1 << 1, FLASH + 0x1000)  # one page of it
    assert stats(stub_proc)["stock_fw"] == 0
    target(stub_proc, "stock_pins_ms 100")
    target(stub_proc, "power_cycle")
    stub_proc.exec("step_time 500")
    assert host.idle_check() == 1


# ---- presets and the environment ----


def start(env: dict[str, str], monkeypatch) -> StubProc:
    for key in ("SWD_TARGET_PRESET", "SWD_TARGET_STOCK_PINS_MS", "SWD_TARGET_FLASH",
                "SWD_TARGET_STATE", "SWD_TARGET_POWER_CYCLE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return StubProc(device_config=DEVICE_CONFIG).start()


def test_preset_stock_from_the_environment(monkeypatch):
    p = start({"SWD_TARGET_PRESET": "stock", "SWD_TARGET_STOCK_PINS_MS": "250"}, monkeypatch)
    try:
        st = stats(p)
        assert (st["preset"], st["rdp"], st["core"], st["stock_fw"]) == ("stock", 1, "running", 1)
        assert st["stock_pins_ms"] == 250
        assert (st["pc"], st["msp"]) == (0x080000C0, 0x20000C00)
        assert option_bytes(p) == OPTB_STOCK
        assert peek(p, FLASH_OPTR) == 0x0000F000
        assert peek(p, FLASH + 8) == int.from_bytes(bytes((i * 131 + 7) & 0xFF for i in range(8, 12)), "little")
        target(p, "reset")  # again the environment's preset
        assert stats(p)["preset"] == "stock"
    finally:
        p.stop()


def test_flash_file_from_the_environment(monkeypatch, tmp_path):
    image = tmp_path / "app.bin"
    words = [0x20000C00, 0x080010C1] + [0xFFFFFFFF] * (0x10C0 // 4 - 2) + [0x4770BF00]
    image.write_bytes(b"".join(w.to_bytes(4, "little") for w in words))
    p = start({"SWD_TARGET_FLASH": str(image), "SWD_TARGET_PRESET": "bogus"}, monkeypatch)
    try:
        st = stats(p)
        assert (st["preset"], st["core"], st["pc"]) == ("open_dimmer", "running", 0x080010C0)
        assert peek(p, FLASH + 0x10C0) == 0x4770BF00
    finally:
        p.stop()


def test_state_file_restart(monkeypatch, tmp_path):
    state = tmp_path / "py32.state"
    env = {"SWD_TARGET_STATE": str(state)}
    p = start(env, monkeypatch)
    try:
        assert not state.exists()
        h = session(p)
        h.mem_write32(SRAM + 0x40, 0x13572468)
        h.mem_write32(DEMCR, 1)
        poke(p, FLASH + 0x100, 0x11223344)
        p.exec("step_time 500")
        before = stats(p)
    finally:
        p.stop()
    assert state.exists()

    # Only the ZTU restarted: the PY32 kept everything, its time ran on.
    p = start(env, monkeypatch)
    try:
        st = stats(p)
        assert st["uptime_ms"] == 500 and st["powered"] == 1
        assert st["line_resets"] == before["line_resets"]
        assert peek(p, SRAM + 0x40) == 0x13572468
        assert peek(p, DEMCR) == 1
        assert peek(p, FLASH + 0x100) == 0x11223344
        poke(p, SRAM + 0x44, 0x5A5A5A5A)
        assert target(p, "save") == {}
    finally:
        p.stop()

    # Both restarted: a power cycle at the start.
    p = start({**env, "SWD_TARGET_POWER_CYCLE": "1"}, monkeypatch)
    try:
        st = stats(p)
        assert st["power_cycles"] == before["power_cycles"] + 1
        assert st["powered"] == 0 and st["lockout"] == 1 and st["uptime_ms"] == 0
        assert peek(p, SRAM + 0x40) == 0xDEADBEEF ^ 0x40
        assert peek(p, DEMCR) == 0
        assert peek(p, FLASH + 0x100) == 0x11223344  # the flash stays
    finally:
        p.stop()


def test_stock_time_runs_on_over_a_ztu_restart(monkeypatch, tmp_path):
    env = {
        "SWD_TARGET_STATE": str(tmp_path / "py32.state"),
        "SWD_TARGET_PRESET": "stock",
        "SWD_TARGET_STOCK_PINS_MS": "1000",
    }
    p = start(env, monkeypatch)
    try:
        p.exec("step_time 600")
    finally:
        p.stop()
    p = start(env, monkeypatch)
    try:
        h = SwdRefHost(p, clk=SWD_CLK, dio=SWD_DIO)
        p.exec("step_time 399")
        assert h.idle_check() == 1
        p.exec("step_time 1")
        assert h.idle_check() == 0
    finally:
        p.stop()
    # A power cycle with the restart gives the time back.
    p = start({**env, "SWD_TARGET_POWER_CYCLE": "1"}, monkeypatch)
    try:
        h = SwdRefHost(p, clk=SWD_CLK, dio=SWD_DIO)
        assert h.idle_check() == 1
        assert stats(p)["pins_taken"] == 0
    finally:
        p.stop()


def test_save_needs_a_state_file(monkeypatch, tmp_path):
    p = start({}, monkeypatch)
    try:
        assert not p.exec("swd_target save").ok
    finally:
        p.stop()
    # A file that is not a state of this build: ignored, the preset starts.
    bad = tmp_path / "bad.state"
    bad.write_bytes(b"not a state")
    p = start({"SWD_TARGET_STATE": str(bad)}, monkeypatch)
    try:
        assert stats(p)["preset"] == "open_dimmer"
    finally:
        p.stop()
    assert bad.read_bytes()[:4] == b"SWDT"  # saved over at the exit
    assert os.path.getsize(bad) > 0x5000
