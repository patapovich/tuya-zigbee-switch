"""The stub's dimmer-MCU model, phase 2: core control, the flash controller,
RCC / GPIOA, power cycles and power cuts, all through the reference SWD host
(swd_ref_host.py)."""

import pytest
from client import StubProc
from swd_ref_host import (
    ACK_FAULT,
    ACK_NONE,
    ACK_OK,
    ACK_WAIT,
    AP_DRW,
    AP_TAR,
    CS_STICKYERR,
    DEMCR,
    DHCSR,
    DHCSR_KEY,
    DP_CTRL_STAT,
    DP_DPIDR,
    DP_RDBUFF,
    C_DEBUGEN,
    C_HALT,
    DCRDR,
    DCRSR,
    DCRSR_REGWNR,
    REG_MSP,
    REG_PC,
    REG_SP,
    REG_XPSR,
    S_HALT,
    S_LOCKUP,
    S_REGRDY,
    S_RESET_ST,
    XPSR_T,
    SwdRefHost,
)
from test_swd_target_model import SWD_CLK, SWD_DIO, stats, target, writes

DPIDR = 0x0BC11477
FLASH = 0x08000000
SRAM = 0x20000000
SRAM_END = 0x20000C00
OPTION_BYTES = 0x1FFF0E80
HSI_FS4_TABLE = 0x1FFF0F6C

FLASH_KEYR = 0x40022008
FLASH_SR = 0x40022010
FLASH_CR = 0x40022014
FLASH_OPTR = 0x40022020
FLASH_WRPR = 0x4002202C
FLASH_TS0 = 0x40022100
KEY1 = 0x45670123
KEY2 = 0xCDEF89AB
SR_EOP = 1 << 0
SR_WRPERR = 1 << 4
SR_BSY = 1 << 16
CR_PG = 1 << 0
CR_PER = 1 << 1
CR_SER = 1 << 11
CR_PGSTRT = 1 << 19
CR_OPTLOCK = 1 << 30
CR_LOCK = 1 << 31

RCC_IOPENR = 0x40021034
RCC_APBENR1 = 0x4002103C
APBENR1_DBGEN = 1 << 27
DBGMCU_APB_FZ1 = 0x40015808
FZ1_IWDG = 1 << 12
GPIOA_MODER = 0x50000000
GPIOA_ODR = 0x50000014
GPIOA_BSRR = 0x50000018
GPIOA_BRR = 0x50000028
MODER_RESET = 0xEBFFFFFF

PAGE = 128
SECTOR = 4096
APP_ENTRY = 0x080010C0


def peek(p: StubProc, addr: int) -> int:
    return int(target(p, f"peek32 {addr:X}")["value"], 16)


def poke(p: StubProc, addr: int, value: int) -> None:
    target(p, f"poke32 {addr:X} {value:X}")


def flash_log(p: StubProc) -> list[str]:
    return [x for x in target(p, "flash_log")["list"].split(",") if x]


def load_words(p: StubProc, tmp_path, addr: int, words: list[int]) -> None:
    path = tmp_path / f"img_{addr:08X}.bin"
    path.write_bytes(b"".join(w.to_bytes(4, "little") for w in words))
    target(p, f"load {addr:X} {path}")


def load_app(p: StubProc, tmp_path, msp: int = SRAM_END, reset: int = APP_ENTRY | 1) -> None:
    """Vectors, and two instructions at the app entry (nop; bx lr)."""
    load_words(p, tmp_path, FLASH, [msp, reset])
    load_words(p, tmp_path, APP_ENTRY, [0x4770BF00])


class Py32Flash:
    """The PY32F002A flash controller sequences (reference manual)."""

    def __init__(self, h: SwdRefHost) -> None:
        self.h = h

    def unlock(self) -> None:
        self.h.mem_write32(FLASH_KEYR, KEY1)
        self.h.mem_write32(FLASH_KEYR, KEY2)

    def cr(self, value: int | None = None) -> int:
        if value is not None:
            self.h.mem_write32(FLASH_CR, value)
        return self.h.mem_read32(FLASH_CR)

    def sr(self) -> int:
        return self.h.mem_read32(FLASH_SR)

    def clear_sr(self) -> None:
        self.h.mem_write32(FLASH_SR, SR_EOP | SR_WRPERR)

    def wait(self, polls: int = 20) -> list[int]:
        """SR values read until BSY clears (the last one without BSY)."""
        seen = []
        for _ in range(polls):
            seen.append(self.sr())
            if not seen[-1] & SR_BSY:
                return seen
        raise AssertionError(f"still busy: {seen}")

    def erase(self, op: int, addr: int) -> list[int]:
        self.cr(op)
        self.h.mem_write32(addr, 0xFFFFFFFF)
        seen = self.wait()
        self.cr(0)
        return seen

    def program_page(self, addr: int, words: list[int]) -> list[int]:
        assert len(words) == 32
        self.cr(CR_PG)
        self.h.mem_write_block(addr, words[:31])
        self.cr(CR_PG | CR_PGSTRT)
        self.h.mem_write32(addr + 124, words[31])
        seen = self.wait()
        self.cr_after = self.cr()
        self.cr(0)
        return seen


@pytest.fixture
def host(stub_proc: StubProc) -> SwdRefHost:
    h = SwdRefHost(stub_proc, clk=SWD_CLK, dio=SWD_DIO)
    h.take()
    h.start_session()
    return h


@pytest.fixture
def fl(host: SwdRefHost) -> Py32Flash:
    return Py32Flash(host)


def assert_bus_error(h: SwdRefHost) -> None:
    assert h.transfer(0, 1, DP_RDBUFF).ack == ACK_FAULT
    assert h.read_dp(DP_CTRL_STAT) & CS_STICKYERR
    h.clear_errors()


# ---- flash controller ----


def test_flash_unlock_and_relock(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc):
    assert fl.cr() == CR_LOCK | CR_OPTLOCK
    assert stats(stub_proc)["flash_locked"] == 1
    assert fl.cr(CR_PER) == CR_LOCK | CR_OPTLOCK  # locked: ignored
    fl.unlock()
    assert fl.cr() == CR_OPTLOCK
    assert stats(stub_proc)["flash_locked"] == 0
    assert host.mem_read32(FLASH_KEYR) == 0
    assert fl.cr(CR_PER) == CR_OPTLOCK | CR_PER
    assert fl.cr(0) == CR_OPTLOCK  # OPTLOCK stays: no OPTKEYR
    fl.cr(CR_LOCK)
    assert stats(stub_proc)["flash_locked"] == 1
    fl.unlock()
    assert fl.cr() == CR_OPTLOCK


@pytest.mark.parametrize("keys", [[0x12345678, KEY1, KEY2], [KEY1, KEY1, KEY2], [KEY2, KEY1]])
def test_wrong_key_locks_until_reset(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc, keys):
    for key in keys:
        host.mem_write32(FLASH_KEYR, key)
    fl.unlock()
    assert fl.cr() & CR_LOCK
    st = stats(stub_proc)
    assert st["key_lockout"] == 1 and st["flash_locked"] == 1
    host.system_reset()
    assert stats(stub_proc)["key_lockout"] == 0
    fl.unlock()
    assert fl.cr() == CR_OPTLOCK


def test_flash_write_needs_unlock_and_one_operation(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc):
    host.mem_write32(FLASH, 0)  # locked
    assert_bus_error(host)
    fl.unlock()
    host.mem_write32(FLASH + 4, 0)  # no PG / PER / SER
    assert_bus_error(host)
    assert writes(stub_proc) == [  # the refused writes are logged too
        (FLASH, 0),
        (FLASH_KEYR, KEY1),
        (FLASH_KEYR, KEY2),
        (FLASH + 4, 0),
    ]
    assert peek(stub_proc, FLASH) == 0xFFFFFFFF
    st = stats(stub_proc)
    assert st["bus_errors"] == 2
    assert st["erases"] == 0 and st["programs"] == 0
    assert flash_log(stub_proc) == []


def test_page_erase(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc, tmp_path):
    load_words(stub_proc, tmp_path, FLASH, [0x11000000 + i for i in range(4 * 32)])
    fl.unlock()
    # BSY for two reads of SR (the default busy_reads), then EOP.
    assert fl.erase(CR_PER, FLASH + PAGE + 4) == [SR_BSY, SR_BSY, SR_EOP]
    assert all(peek(stub_proc, FLASH + PAGE + o) == 0xFFFFFFFF for o in range(0, PAGE, 4))
    assert peek(stub_proc, FLASH + PAGE - 4) == 0x1100001F
    assert peek(stub_proc, FLASH + 2 * PAGE) == 0x11000040
    fl.clear_sr()
    assert fl.sr() == 0
    assert flash_log(stub_proc) == ["erase_page:0x08000080"]
    st = stats(stub_proc)
    assert st["erases"] == 1 and st["wrperr"] == 0 and st["flash_busy"] == 0


def test_sector_erase(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc, tmp_path):
    for base in (FLASH + SECTOR - 4, FLASH + SECTOR, FLASH + 2 * SECTOR - 4, FLASH + 2 * SECTOR):
        poke(stub_proc, base, 0x12345678)
    fl.unlock()
    assert fl.erase(CR_SER, FLASH + SECTOR + 0x234)[-1] == SR_EOP
    assert peek(stub_proc, FLASH + SECTOR - 4) == 0x12345678
    assert peek(stub_proc, FLASH + SECTOR) == 0xFFFFFFFF
    assert peek(stub_proc, FLASH + 2 * SECTOR - 4) == 0xFFFFFFFF
    assert peek(stub_proc, FLASH + 2 * SECTOR) == 0x12345678
    assert flash_log(stub_proc) == ["erase_sector:0x08001000"]


def test_page_program_ands_into_the_old_content(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc, tmp_path):
    page = FLASH + 2 * PAGE
    load_words(stub_proc, tmp_path, page, [0xF0F0F0F0] * 32)
    words = [0x3C3C0000 + i for i in range(32)]
    fl.unlock()
    assert fl.program_page(page, words)[-1] == SR_EOP
    assert host.mem_read_block(page, 32) == [0xF0F0F0F0 & w for w in words]
    assert fl.cr_after == CR_OPTLOCK | CR_PG  # PGSTRT cleared itself
    # On an erased page the words land as they are.
    fl.clear_sr()
    fl.program_page(page + PAGE, words)
    assert host.mem_read_block(page + PAGE, 32) == words
    assert flash_log(stub_proc) == ["program:0x08000100", "program:0x08000180"]
    st = stats(stub_proc)
    assert st["programs"] == 2 and st["wrperr"] == 0


def _pg_no_pgstrt(h, fl, page):
    fl.cr(CR_PG)
    h.mem_write_block(page, [0] * 32)


def _pg_out_of_order(h, fl, page):
    fl.cr(CR_PG)
    h.mem_write32(page, 0)
    h.mem_write32(page + 8, 0)


def _pg_other_page(h, fl, page):
    fl.cr(CR_PG)
    h.mem_write32(page, 0)
    h.mem_write32(page + PAGE + 4, 0)


def _pg_not_page_start(h, fl, page):
    fl.cr(CR_PG)
    h.mem_write32(page + 4, 0)


def _two_operations(h, fl, page):
    fl.cr(CR_PG | CR_PER)
    h.mem_write32(page, 0)


@pytest.mark.parametrize(
    "sequence",
    [_pg_no_pgstrt, _pg_out_of_order, _pg_other_page, _pg_not_page_start, _two_operations],
    ids=["no_pgstrt", "out_of_order", "other_page", "not_page_start", "two_operations"],
)
def test_program_rules_give_wrperr(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc, sequence):
    page = FLASH + 3 * PAGE
    fl.unlock()
    sequence(host, fl, page)
    assert fl.sr() == SR_WRPERR  # no BSY, no EOP
    fl.cr(0)
    assert all(peek(stub_proc, page + o) == 0xFFFFFFFF for o in range(0, 2 * PAGE, 4))
    st = stats(stub_proc)
    assert st["wrperr"] == 1 and st["programs"] == 0 and st["erases"] == 0
    assert [x.split(":")[0] for x in flash_log(stub_proc)] == ["wrperr"]
    fl.clear_sr()
    assert fl.sr() == 0
    # A correct page program works afterwards.
    fl.program_page(page, list(range(32)))
    assert peek(stub_proc, page + 124) == 31


def test_clearing_pg_drops_a_partial_page(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc):
    page = FLASH + 5 * PAGE
    fl.unlock()
    fl.cr(CR_PG)
    host.mem_write_block(page, [0] * 10)
    fl.cr(0)
    assert fl.program_page(page, [0x55555555] * 32)[-1] == SR_EOP
    assert peek(stub_proc, page) == 0x55555555
    assert stats(stub_proc)["wrperr"] == 0


def test_flash_access_waits_while_busy(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc):
    target(stub_proc, "busy_reads 3")
    fl.unlock()
    fl.cr(CR_PER)
    host.mem_write32(FLASH, 0)
    assert stats(stub_proc)["flash_busy"] == 1
    # Flash reads and writes stall (WAIT); registers answer.
    host.write_ap(AP_TAR, FLASH + 0x40)
    assert host.transfer(1, 1, AP_DRW).ack == ACK_WAIT
    assert host.transfer(1, 0, AP_DRW, 0).ack == ACK_WAIT
    assert host.mem_read32(FLASH_CR) == CR_OPTLOCK | CR_PER
    assert fl.wait() == [SR_BSY, SR_BSY, SR_BSY, SR_EOP]
    assert host.mem_read32(FLASH + 0x40) == 0xFFFFFFFF
    st = stats(stub_proc)
    assert st["waits"] == 2 and st["flash_busy"] == 0 and st["faults"] == 0


def test_wrpr_protects_a_sector(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc, tmp_path):
    poke(stub_proc, FLASH, 0x12345678)
    poke(stub_proc, FLASH_WRPR, 0xFFFE)  # sector 0 protected
    fl.unlock()
    fl.cr(CR_PER)
    host.mem_write32(FLASH + 0x80, 0)
    assert fl.sr() == SR_WRPERR
    fl.cr(0)
    fl.clear_sr()
    fl.cr(CR_SER)
    host.mem_write32(FLASH, 0)
    assert fl.sr() == SR_WRPERR
    fl.cr(0)
    fl.clear_sr()
    assert fl.program_page(FLASH, [0] * 32) == [SR_WRPERR]
    assert peek(stub_proc, FLASH) == 0x12345678
    assert fl.erase(CR_SER, FLASH + SECTOR) == [SR_WRPERR | SR_BSY, SR_WRPERR | SR_BSY, SR_WRPERR | SR_EOP]
    assert stats(stub_proc)["wrperr"] == 3
    assert flash_log(stub_proc) == [
        "wrperr:0x08000080",
        "wrperr:0x08000000",
        "wrperr:0x08000000",
        "erase_sector:0x08001000",
    ]
    # From the option bytes: WRPR 0xFFFD (and its complement) after a power cycle.
    poke(stub_proc, OPTION_BYTES + 12, 0x0002FFFD)
    target(stub_proc, "power_cycle")
    host.start_session()
    assert host.mem_read32(FLASH_WRPR) == 0xFFFD


def test_timing_registers(host: SwdRefHost, stub_proc: StubProc):
    table = host.mem_read_block(HSI_FS4_TABLE, 5)
    # TS0, TS1, TS2P, TPS3, TS3, PERTPE, SMERTPE, PRGTPE, PRETPE
    values = [
        table[0] & 0xFF,
        (table[0] >> 16) & 0x1FF,
        table[1] & 0xFF,
        (table[1] >> 16) & 0x7FF,
        (table[0] >> 8) & 0xFF,
        table[2] & 0x1FFFF,
        table[3] & 0x1FFFF,
        table[4] & 0xFFFF,
        (table[4] >> 16) & 0x3FFF,
    ]
    for i, v in enumerate(values[:8]):
        host.mem_write32(FLASH_TS0 + 4 * i, v)
    assert stats(stub_proc)["timing_set"] == 0
    host.mem_write32(FLASH_TS0 + 32, values[8])
    assert stats(stub_proc)["timing_set"] == 1
    assert host.mem_read_block(FLASH_TS0, 9) == values
    host.system_reset()
    assert stats(stub_proc)["timing_set"] == 0
    assert host.mem_read32(FLASH_TS0) == 0


# ---- core control ----


def test_reset_halt_with_vector_catch(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc, tmp_path):
    load_app(stub_proc, tmp_path)
    host.mem_write32(SRAM_END - 8, 0x12345678)  # kept over a system reset
    host.mem_write32(RCC_APBENR1, APBENR1_DBGEN)
    host.mem_write32(DBGMCU_APB_FZ1, FZ1_IWDG)
    host.mem_write32(RCC_IOPENR, 1)
    fl.unlock()
    assert host.reset_halt() == S_RESET_ST | S_REGRDY | S_HALT | C_HALT | C_DEBUGEN
    st = stats(stub_proc)
    assert (st["core"], st["pc"], st["msp"], st["xpsr"]) == ("halted", APP_ENTRY, SRAM_END, XPSR_T)
    assert st["sys_resets"] == 1 and st["iwdg_frozen"] == 1
    assert host.read_core_reg(REG_PC) == APP_ENTRY
    assert host.read_core_reg(REG_SP) == SRAM_END
    assert host.mem_read32(SRAM_END - 8) == 0x12345678
    assert host.mem_read32(DEMCR) == 1
    assert host.mem_read32(DBGMCU_APB_FZ1) == FZ1_IWDG
    # Peripherals are back to reset.
    assert host.mem_read32(RCC_APBENR1) == 0
    assert host.mem_read32(RCC_IOPENR) == 0
    assert fl.cr() == CR_LOCK | CR_OPTLOCK
    assert host.dhcsr() == S_REGRDY | S_HALT | C_HALT | C_DEBUGEN  # S_RESET_ST read
    assert host.resume() == S_REGRDY | C_DEBUGEN
    assert stats(stub_proc)["core"] == "running"


@pytest.mark.parametrize(
    "msp,reset,core",
    [
        (SRAM_END, APP_ENTRY | 1, "running"),
        (SRAM + 0x400, APP_ENTRY | 1, "running"),
        (SRAM_END - 2, APP_ENTRY | 1, "lockup"),  # MSP not 4-aligned
        (SRAM_END + 4, APP_ENTRY | 1, "lockup"),  # MSP outside the SRAM
        (SRAM_END, APP_ENTRY, "lockup"),  # reset vector even
        (SRAM_END, 0x08005001, "lockup"),  # outside the flash
        (SRAM_END, 0x08002001, "lockup"),  # pointing at erased flash
        (0xFFFFFFFF, 0xFFFFFFFF, "lockup"),  # erased vectors
    ],
)
def test_reset_runs_the_firmware_or_locks_up(host: SwdRefHost, stub_proc: StubProc, tmp_path, msp, reset, core):
    load_app(stub_proc, tmp_path, msp, reset)
    host.mem_write32(DHCSR, DHCSR_KEY | C_DEBUGEN)
    host.system_reset()  # no vector catch: the core runs
    st = stats(stub_proc)
    assert st["core"] == core
    assert (st["pc"], st["msp"]) == (reset & ~1, msp & ~3)
    lockup = S_LOCKUP if core == "lockup" else 0
    assert host.dhcsr() == S_RESET_ST | S_REGRDY | C_DEBUGEN | lockup


@pytest.mark.parametrize(
    "pc,xpsr,msp,core",
    [
        (APP_ENTRY, XPSR_T, SRAM_END, "running"),
        (APP_ENTRY | 1, XPSR_T, SRAM_END, "running"),  # bit 0 of a PC is ignored
        (APP_ENTRY, 0, SRAM_END, "lockup"),  # no Thumb state
        (SRAM + 0x100, XPSR_T, SRAM_END, "lockup"),  # PC outside the flash
        (APP_ENTRY, XPSR_T, SRAM_END + 0x100, "lockup"),  # MSP outside the SRAM
    ],
)
def test_register_writes_and_resume(host: SwdRefHost, stub_proc: StubProc, pc, xpsr, msp, core):
    assert host.halt() & S_HALT
    host.write_core_reg(REG_PC, pc)
    host.write_core_reg(REG_XPSR, xpsr)
    host.write_core_reg(REG_MSP, msp | 3)  # MSP[1:0] read as 0
    assert host.read_core_reg(REG_PC) == pc
    assert host.read_core_reg(REG_MSP) == msp
    st = stats(stub_proc)
    assert (st["pc"], st["xpsr"], st["msp"]) == (pc, xpsr, msp)
    dhcsr = host.resume()
    assert stats(stub_proc)["core"] == core
    assert dhcsr & S_LOCKUP == (S_LOCKUP if core == "lockup" else 0)
    assert dhcsr & S_HALT == 0


def test_core_registers_only_while_halted(host: SwdRefHost, stub_proc: StubProc):
    assert stats(stub_proc)["core"] == "running"
    before = stats(stub_proc)["pc"]
    host.mem_write32(DCRDR, 0x08000200)
    host.mem_write32(DCRSR, DCRSR_REGWNR | REG_PC)
    assert stats(stub_proc)["pc"] == before
    # Out of a lockup by a halt.
    host.halt()
    host.write_core_reg(REG_XPSR, 0)
    assert host.resume() & S_LOCKUP
    assert host.halt() == S_REGRDY | S_HALT | C_HALT | C_DEBUGEN
    assert stats(stub_proc)["core"] == "halted"
    # C_DEBUGEN 0 lets a halted core go too.
    host.write_core_reg(REG_XPSR, XPSR_T)
    host.mem_write32(DHCSR, DHCSR_KEY)
    assert host.dhcsr() == S_REGRDY
    assert stats(stub_proc)["core"] == "running"


def test_dbgmcu_writes_need_dbgen(host: SwdRefHost, stub_proc: StubProc):
    host.mem_write32(DBGMCU_APB_FZ1, FZ1_IWDG)
    assert host.mem_read32(DBGMCU_APB_FZ1) == 0
    host.mem_write32(RCC_APBENR1, APBENR1_DBGEN)
    host.mem_write32(DBGMCU_APB_FZ1, FZ1_IWDG)
    assert host.mem_read32(DBGMCU_APB_FZ1) == FZ1_IWDG
    assert stats(stub_proc)["iwdg_frozen"] == 1
    host.system_reset()  # DBGEN cleared, DBGMCU kept
    assert host.mem_read32(RCC_APBENR1) == 0
    host.mem_write32(DBGMCU_APB_FZ1, 0)
    assert host.mem_read32(DBGMCU_APB_FZ1) == FZ1_IWDG
    target(stub_proc, "power_cycle")
    host.start_session()
    assert host.mem_read32(DBGMCU_APB_FZ1) == 0
    assert stats(stub_proc)["iwdg_frozen"] == 0


# ---- RCC / GPIOA ----


def test_gpioa_gates_low(host: SwdRefHost, stub_proc: StubProc):
    host.write_ap(AP_TAR, GPIOA_MODER)
    host.read_ap(AP_DRW)
    assert_bus_error(host)  # GPIOA clock off: reads and writes
    host.mem_write32(GPIOA_BRR, 0x3)
    assert_bus_error(host)
    host.mem_write32(RCC_IOPENR, 1)
    assert host.mem_read32(GPIOA_MODER) == MODER_RESET
    assert host.mem_read32(GPIOA_ODR) == 0
    host.mem_write32(GPIOA_MODER, (MODER_RESET & ~0xF) | 0x5)  # PA0, PA1 out
    host.mem_write32(GPIOA_BRR, 0x3)
    assert stats(stub_proc)["gates_low"] == 1
    host.mem_write32(GPIOA_BSRR, 0x1)
    assert host.mem_read32(GPIOA_ODR) == 0x1
    assert stats(stub_proc)["gates_low"] == 0
    host.mem_write32(GPIOA_BSRR, 0x1 << 16)
    assert host.mem_read32(GPIOA_ODR) == 0
    assert stats(stub_proc)["gates_low"] == 1
    host.system_reset()
    assert stats(stub_proc)["gates_low"] == 0
    host.mem_write32(RCC_IOPENR, 1)
    assert host.mem_read32(GPIOA_MODER) == MODER_RESET


def test_moder_off_the_swd_pins_loses_the_link(host: SwdRefHost, stub_proc: StubProc):
    host.mem_write32(RCC_IOPENR, 1)
    host.mem_write32(GPIOA_MODER, MODER_RESET & ~(0x3 << 28))  # PA14 SWCLK as input
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    host.line_reset()
    host.idle(2)
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    assert stats(stub_proc)["swd_lost"] == 1
    target(stub_proc, "power_cycle")
    assert host.start_session() == DPIDR
    assert stats(stub_proc)["swd_lost"] == 0


# ---- power ----


def test_power_cycle(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc, tmp_path):
    host.mem_write32(SRAM, 0)
    host.mem_write32(DEMCR, 1)
    host.halt()
    fl.unlock()
    target(stub_proc, "rdp 1")
    target(stub_proc, "power_cycle")
    # The DP lost its power: locked out until a line reset, powered down.
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    assert host.connect() == DPIDR
    assert host.read_dp(DP_CTRL_STAT) == 0
    host.clear_errors()
    host.power_up()
    host.mem_setup()
    assert host.mem_read32(SRAM) == 0xDEADBEEF
    assert host.mem_read32(SRAM + 0xBF8) == 0xDEADBEEF ^ 0xBF8
    assert host.mem_read32(DEMCR) == 0
    assert host.dhcsr() == S_RESET_ST | S_REGRDY | S_LOCKUP  # erased flash, no debugger
    assert fl.cr() == CR_LOCK | CR_OPTLOCK
    assert host.mem_read32(FLASH_OPTR) == 0x0000F955  # RDP 1 kept in the option bytes
    st = stats(stub_proc)
    assert st["power_cycles"] == 1 and st["core"] == "lockup" and st["powered"] == 1


def test_power_cycle_runs_a_valid_firmware(host: SwdRefHost, stub_proc: StubProc, tmp_path):
    load_app(stub_proc, tmp_path)
    host.halt()
    target(stub_proc, "power_cycle")
    st = stats(stub_proc)
    assert (st["core"], st["pc"], st["msp"]) == ("running", APP_ENTRY, SRAM_END)


def test_cut_during_a_page_program(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc):
    page = FLASH + 6 * PAGE
    poke(stub_proc, page, 0xFFFF00FF)
    fl.unlock()
    fl.cr(CR_PG)
    host.mem_write_block(page, [0x01020304] * 31)
    fl.cr(CR_PG | CR_PGSTRT)
    target(stub_proc, "cut_after 2")  # TAR, then the 32nd word: BSY, then the cut
    host.mem_write32(page + 124, 0x01020304)
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    assert all(peek(stub_proc, page + o) == 0xA5A5A5A5 for o in range(4, PAGE, 4))
    assert peek(stub_proc, page) == 0xFFFF00FF & 0xA5A5A5A5
    assert flash_log(stub_proc) == ["program:0x08000300", "garbage:0x08000300"]
    st = stats(stub_proc)
    assert st["power_cycles"] == 1 and st["flash_busy"] == 0 and st["lockout"] == 1
    assert host.start_session() == DPIDR
    assert fl.cr() == CR_LOCK | CR_OPTLOCK


def test_cut_during_an_erase_and_without_an_operation(host: SwdRefHost, fl: Py32Flash, stub_proc: StubProc):
    poke(stub_proc, FLASH + SECTOR, 0x0F0F0F0F)
    fl.unlock()
    fl.cr(CR_SER)
    target(stub_proc, "cut_after 2")
    host.mem_write32(FLASH + SECTOR, 0)
    assert peek(stub_proc, FLASH + SECTOR) == 0x05050505
    assert peek(stub_proc, FLASH + SECTOR + 4) == 0xA5A5A5A5
    assert peek(stub_proc, FLASH) == 0xFFFFFFFF
    # A cut with nothing running changes no flash; it counts only acked transfers.
    host.start_session()
    target(stub_proc, "cut_after 3")
    assert host.raw_transfer(0x85).ack == ACK_NONE  # a bad request is no transfer
    host.line_reset()
    host.idle(2)
    host.read_dp(DP_DPIDR)
    host.read_dp(DP_CTRL_STAT)
    assert stats(stub_proc)["power_cycles"] == 1
    host.read_dp(DP_CTRL_STAT)
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    assert stats(stub_proc)["power_cycles"] == 2
    assert flash_log(stub_proc)[-1] == "garbage:0x08001000"
