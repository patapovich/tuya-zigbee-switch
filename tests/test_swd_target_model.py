"""The stub's dimmer-MCU SWD model (src/stub/hal/swd_target.c), checked through
an independent reference host (swd_ref_host.py) that drives the stub pin layer
by hand. Values the real PY32F002A gives are noted where they were measured."""

import zlib

import pytest
from client import Event, StubProc
from swd_ref_host import (
    ABORT_CLEAR_STICKY,
    ABORT_DAPABORT,
    ABORT_WDERRCLR,
    ACK_FAULT,
    ACK_NONE,
    ACK_OK,
    ACK_WAIT,
    AP_CSW,
    AP_DRW,
    AP_IDR,
    AP_TAR,
    CS_CDBGPWRUPACK,
    CS_CDBGPWRUPREQ,
    CS_CSYSPWRUPACK,
    CS_CSYSPWRUPREQ,
    CS_STICKYERR,
    CS_WDATAERR,
    CSW_ADDRINC_SINGLE,
    CSW_SIZE_32,
    DP_ABORT,
    DP_CTRL_STAT,
    DP_DPIDR,
    DP_RDBUFF,
    DP_RESEND,
    DP_SELECT,
    SwdError,
    SwdRefHost,
    parity,
    request_byte,
)

DPIDR = 0x0BC11477
AHB_AP_IDR = 0x04770031

CPUID = 0xE000ED00
AIRCR = 0xE000ED0C
DHCSR = 0xE000EDF0
DEMCR = 0xE000EDFC
DBGMCU_IDCODE = 0x40015800
DBGMCU_CR = 0x40015804
FLASH_SR = 0x40022010
FLASH_CR = 0x40022014
FLASH_OPTR = 0x40022020
FLASH_SDKR = 0x40022024
FLASH_WRPR = 0x4002202C
RCC_ICSCR = 0x40021004
FLASH = 0x08000000
SRAM = 0x20000000
SYSROM = 0x1FFF0000
UID = 0x1FFF0E00
OPTION_BYTES = 0x1FFF0E80
HSI_FS4_TABLE = 0x1FFF0F6C

SWD_CLK = 22
SWD_DIO = 23


def target(p: StubProc, cmd: str) -> dict[str, str]:
    res = p.exec(f"swd_target {cmd}")
    assert res.ok, f"swd_target {cmd}: {res.payload}"
    return res.payload


def stats(p: StubProc) -> dict[str, int]:
    """Numbers (decimal or 0x hex) as int; `core` (a word) as it is."""
    out = {}
    for k, v in target(p, "stats").items():
        try:
            out[k] = int(v, 0)
        except ValueError:
            out[k] = v
    return out


def writes(p: StubProc) -> list[tuple[int, int]]:
    listed = target(p, "writes")["list"]
    out = []
    for item in filter(None, listed.split(",")):
        addr, value = item.split("=")
        out.append((int(addr, 16), int(value, 16)))
    return out


@pytest.fixture
def host(stub_proc: StubProc) -> SwdRefHost:
    h = SwdRefHost(stub_proc, clk=SWD_CLK, dio=SWD_DIO)
    assert h.idle_check() == 1
    h.take()
    return h


def assert_clean(p: StubProc) -> None:
    st = stats(p)
    for key in ("contention", "protocol_errors", "parity_errors", "untaken_ops"):
        assert st[key] == 0, (key, st)


# ---- SW-DP line handling ----


def test_dpidr_after_line_reset(host: SwdRefHost, stub_proc: StubProc):
    host.line_reset()
    host.idle(2)
    assert host.read_dp(DP_DPIDR) == DPIDR
    st = stats(stub_proc)
    assert st["line_resets"] == 1
    assert st["dpidr_reads"] == 1
    assert st["transfers_ok"] == 1
    assert_clean(stub_proc)


def test_power_on_needs_a_line_reset(host: SwdRefHost, stub_proc: StubProc):
    host.idle(2)
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    host.line_reset()
    host.idle(2)
    assert host.read_dp(DP_DPIDR) == DPIDR


def test_jtag_to_swd_is_accepted(host: SwdRefHost, stub_proc: StubProc):
    assert host.connect() == DPIDR
    st = stats(stub_proc)
    assert st["line_resets"] == 2
    assert st["jtag_to_swd"] == 1
    assert_clean(stub_proc)


def test_line_reset_from_idle_is_no_protocol_error(host: SwdRefHost, stub_proc: StubProc):
    host.connect()
    host.line_reset()
    host.idle(2)
    assert host.read_dp(DP_DPIDR) == DPIDR
    assert_clean(stub_proc)
    # A run of ones that stops short of a line reset is a bad request.
    host.write_bits((1 << 20) - 1, 20)
    host.idle(2)
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    st = stats(stub_proc)
    assert st["protocol_errors"] == 1
    assert st["lockout"] == 1


def test_first_transfer_after_line_reset_must_read_dpidr(host: SwdRefHost, stub_proc: StubProc):
    host.line_reset()
    host.idle(2)
    assert host.transfer(0, 1, DP_CTRL_STAT).ack == ACK_NONE
    # Locked out: even a DPIDR read gets no answer now.
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    st = stats(stub_proc)
    assert st["protocol_errors"] == 1
    assert st["lockout"] == 1
    host.line_reset()
    host.idle(2)
    assert host.read_dp(DP_DPIDR) == DPIDR
    assert host.read_dp(DP_CTRL_STAT) == 0
    assert stats(stub_proc)["lockout"] == 0


@pytest.mark.parametrize(
    "flip,parity_error",
    [(1 << 5, True), (1 << 6, False), (1 << 7, False)],
    ids=["parity", "stop", "park"],
)
def test_bad_request_gets_no_ack_until_line_reset(
    host: SwdRefHost, stub_proc: StubProc, flip: int, parity_error: bool
):
    host.connect()
    assert host.raw_transfer(request_byte(0, 1, DP_DPIDR) ^ flip).ack == ACK_NONE
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    st = stats(stub_proc)
    assert st["protocol_errors"] == 1
    assert st["parity_errors"] == (1 if parity_error else 0)
    host.line_reset()
    host.idle(2)
    assert host.read_dp(DP_DPIDR) == DPIDR


# ---- power, sticky flags, ABORT ----


def test_power_up_handshake(host: SwdRefHost, stub_proc: StubProc):
    host.connect()
    host.clear_errors()
    assert host.read_dp(DP_CTRL_STAT) == 0
    assert stats(stub_proc)["powered"] == 0
    host.write_dp(DP_CTRL_STAT, CS_CSYSPWRUPREQ | CS_CDBGPWRUPREQ)
    assert host.read_dp(DP_CTRL_STAT) == (
        CS_CSYSPWRUPREQ | CS_CSYSPWRUPACK | CS_CDBGPWRUPREQ | CS_CDBGPWRUPACK
    )
    assert stats(stub_proc)["powered"] == 1
    host.write_dp(DP_CTRL_STAT, 0)
    assert host.read_dp(DP_CTRL_STAT) == 0
    assert stats(stub_proc)["powered"] == 0


def test_ap_access_unpowered_faults_and_sticks(host: SwdRefHost, stub_proc: StubProc):
    """Measured on the real chip: an AP access before the debug power-up
    answers FAULT."""
    host.connect()
    assert host.transfer(1, 1, AP_CSW).ack == ACK_FAULT
    assert host.read_dp(DP_CTRL_STAT) & CS_STICKYERR
    # Sticky: AP, RDBUFF and other DP accesses fault; DPIDR, CTRL/STAT reads
    # and ABORT writes still work.
    assert host.transfer(1, 1, AP_CSW).ack == ACK_FAULT
    assert host.transfer(0, 1, DP_RDBUFF).ack == ACK_FAULT
    assert host.transfer(0, 0, DP_CTRL_STAT, CS_CDBGPWRUPREQ).ack == ACK_FAULT
    assert host.transfer(0, 0, DP_SELECT, 0).ack == ACK_FAULT
    assert host.read_dp(DP_DPIDR) == DPIDR
    host.write_dp(DP_ABORT, ABORT_CLEAR_STICKY)
    assert host.read_dp(DP_CTRL_STAT) & CS_STICKYERR == 0
    host.power_up()
    assert host.transfer(1, 1, AP_CSW).ack == ACK_OK
    st = stats(stub_proc)
    assert st["faults"] == 5
    assert st["contention"] == 0


def test_write_data_parity_error_sets_wdataerr(host: SwdRefHost, stub_proc: StubProc):
    host.start_session()
    # SELECT := bank 0xF, sent with a wrong data parity bit.
    host.drive(True)
    host.write_bits(request_byte(0, 0, DP_SELECT), 8)
    host.drive(False)
    host.clock()
    assert host.read_bits(3) == ACK_OK
    host.clock()
    host.drive(True)
    host.write_bits(0xF0, 32)
    host.write_bits(parity(0xF0) ^ 1, 1)
    assert host.read_dp(DP_CTRL_STAT) & CS_WDATAERR
    assert host.transfer(1, 1, AP_CSW).ack == ACK_FAULT
    host.write_dp(DP_ABORT, ABORT_WDERRCLR)
    assert host.read_dp(DP_CTRL_STAT) & CS_WDATAERR == 0
    # The write was dropped: A=0x0 is still CSW (bank 0), not bank 0xF.
    assert host.transfer(1, 1, AP_CSW).ack == ACK_OK
    assert host.read_dp(DP_RDBUFF) & 0x3F == CSW_ADDRINC_SINGLE | CSW_SIZE_32
    st = stats(stub_proc)
    assert st["parity_errors"] == 1
    assert st["protocol_errors"] == 0


def test_wait_ap_then_ok(host: SwdRefHost, stub_proc: StubProc):
    host.start_session()
    target(stub_proc, "wait_ap 2")
    # DP accesses are not held up.
    assert host.read_dp(DP_DPIDR) == DPIDR
    assert host.transfer(1, 1, AP_CSW).ack == ACK_WAIT
    assert host.transfer(1, 1, AP_CSW).ack == ACK_WAIT
    assert host.transfer(1, 1, AP_CSW).ack == ACK_OK
    assert stats(stub_proc)["waits"] == 2
    # DAPABORT cancels the rest of a stuck AP access.
    target(stub_proc, "wait_ap 50")
    assert host.transfer(1, 0, AP_TAR, SRAM).ack == ACK_WAIT
    host.write_dp(DP_ABORT, ABORT_DAPABORT)
    host.write_ap(AP_TAR, SRAM)
    assert host.read_ap_now(AP_TAR) == SRAM
    assert_clean(stub_proc)


# ---- AHB-AP and memory ----


def test_ap_idr_via_bank_f(host: SwdRefHost, stub_proc: StubProc):
    host.start_session()
    assert host.read_ap_now(AP_IDR) == AHB_AP_IDR
    assert host._select == 0xF0
    # No AP behind APSEL 1.
    assert host.read_ap_now(AP_IDR, apsel=1) == 0


@pytest.mark.parametrize(
    "addr,value",
    [
        (CPUID, 0x410CC601),
        (DBGMCU_IDCODE, 0x60001000),
        (FLASH_OPTR, 0x0000F9AA),
        (FLASH_SDKR, 0x0000001F),
        (FLASH_WRPR, 0x0000FFFF),
        (FLASH_SR, 0x00000000),
        (FLASH_CR, 0xC0000000),
        (RCC_ICSCR, 0x0159950C),
        (AIRCR, 0xFA050000),
        (DBGMCU_CR, 0),
        (FLASH, 0xFFFFFFFF),
        (SRAM, 0),
        (UID, 0x13121110),
        (UID + 12, 0x1F1E1D1C),
        (OPTION_BYTES, 0x0655F9AA),
        (OPTION_BYTES + 4, 0xFF0000FF),
        (OPTION_BYTES + 12, 0x0000FFFF),
        (HSI_FS4_TABLE, 0x01B0B4B4),
        (HSI_FS4_TABLE + 16, 0x12C05DC0),
        (HSI_FS4_TABLE - 4, 0xFFFFFFFF),
        # system ROM byte i = (i * 37 + 11) & 0xFF: 0b 30 55 7a
        (SYSROM + 0x100, 0x7A55300B),
    ],
)
def test_mem_read32(host: SwdRefHost, stub_proc: StubProc, addr: int, value: int):
    host.start_session()
    assert host.mem_read32(addr) == value


def test_system_rom_pattern(host: SwdRefHost, stub_proc: StubProc):
    host.start_session()
    for offset in (0x0, 0x4, 0xDF8):
        expect = int.from_bytes(
            bytes(((offset + i) * 37 + 11) & 0xFF for i in range(4)), "little"
        )
        assert host.mem_read32(SYSROM + offset) == expect
    # The last word makes the CRC the real chip's.
    assert host.mem_read32(SYSROM + 0xDFC) == int.from_bytes(
        bytes.fromhex("e775a5e6"), "little"
    )


def test_system_rom_crc_is_the_real_chips(stub_proc: StubProc):
    rom = b"".join(
        int(target(stub_proc, f"peek32 {SYSROM + o:X}")["value"], 16).to_bytes(4, "little")
        for o in range(0, 0xE00, 4)
    )
    assert zlib.crc32(rom) == 0xA52232EA


def test_posted_reads_and_rdbuff(host: SwdRefHost, stub_proc: StubProc):
    for i in range(4):
        target(stub_proc, f"poke32 {SRAM + 0x10 + 4 * i:X} {0xA0A0A0A0 + i:X}")
    host.connect()
    host.clear_errors()
    host.power_up()
    host.mem_setup()
    host.write_ap(AP_TAR, SRAM + 0x10)
    # Each AP read returns the previous AP read's result (0 at first).
    assert host.read_ap(AP_DRW) == 0
    assert host.read_ap(AP_DRW) == 0xA0A0A0A0
    assert host.read_dp(DP_RDBUFF) == 0xA0A0A0A1
    # RDBUFF does no new access: again the same, and TAR has moved by 2 only.
    assert host.read_dp(DP_RDBUFF) == 0xA0A0A0A1
    assert host.read_dp(DP_RESEND) == 0xA0A0A0A1
    assert host.read_ap(AP_TAR) == 0xA0A0A0A1
    assert host.read_dp(DP_RDBUFF) == SRAM + 0x18


def test_tar_wraps_inside_1kb_block(host: SwdRefHost, stub_proc: StubProc):
    target(stub_proc, f"poke32 {SRAM + 0x3FC:X} 11111111")
    target(stub_proc, f"poke32 {SRAM:X} 22222222")
    target(stub_proc, f"poke32 {SRAM + 0x400:X} 33333333")
    host.start_session()
    host.write_ap(AP_TAR, SRAM + 0x3FC)
    host.read_ap(AP_DRW)
    assert host.read_ap(AP_DRW) == 0x11111111
    assert host.read_dp(DP_RDBUFF) == 0x22222222
    assert host.read_ap_now(AP_TAR) == SRAM + 0x004


def test_no_increment_without_addrinc(host: SwdRefHost, stub_proc: StubProc):
    host.start_session()
    host.mem_setup(CSW_SIZE_32)
    host.write_ap(AP_TAR, SRAM + 0x20)
    host.write_ap(AP_DRW, 0x12345678)
    host.write_ap(AP_DRW, 0x9ABCDEF0)
    assert host.read_ap_now(AP_TAR) == SRAM + 0x20
    assert target(stub_proc, f"peek32 {SRAM + 0x20:X}")["value"] == "0x9ABCDEF0"


def test_rdp1_flash_read_then_fault(host: SwdRefHost, stub_proc: StubProc):
    """pyOCD on the real chip under RDP 1: the flash read itself is OK, the
    next access FAULTs."""
    target(stub_proc, "rdp 1")
    host.start_session()
    host.write_ap(AP_TAR, FLASH)
    assert host.transfer(1, 1, AP_DRW).ack == ACK_OK
    assert host.transfer(0, 1, DP_RDBUFF).ack == ACK_FAULT
    assert host.read_dp(DP_CTRL_STAT) & CS_STICKYERR
    host.clear_errors()
    # The option bytes are readable and show RDP level 1 (not 0xAA).
    assert host.mem_read32(FLASH_OPTR) == 0x0000F955
    assert host.mem_read32(OPTION_BYTES) == 0x06AAF955
    assert host.mem_read32(SRAM) == 0
    # Flash writes are bus errors too.
    host.mem_write32(FLASH, 0)
    assert host.transfer(0, 1, DP_RDBUFF).ack == ACK_FAULT
    host.clear_errors()
    assert stats(stub_proc)["bus_errors"] == 2
    target(stub_proc, "rdp 0")
    assert host.mem_read32(FLASH) == 0xFFFFFFFF


def test_bus_errors(host: SwdRefHost, stub_proc: StubProc):
    host.start_session()
    for bad in (0x40000000, 0x1FFF0E10, 0x20000C00, SRAM + 2):
        host.write_ap(AP_TAR, bad)
        assert host.transfer(1, 1, AP_DRW).ack == ACK_OK
        assert host.transfer(0, 1, DP_RDBUFF).ack == ACK_FAULT, hex(bad)
        host.clear_errors()
    # Only 32-bit accesses are modelled.
    host.mem_setup(0x0)
    host.write_ap(AP_TAR, SRAM)
    host.read_ap(AP_DRW)
    assert host.transfer(0, 1, DP_RDBUFF).ack == ACK_FAULT
    assert stats(stub_proc)["bus_errors"] == 5


def test_write_logging(host: SwdRefHost, stub_proc: StubProc):
    host.start_session()
    target(stub_proc, f"poke32 {SRAM:X} 5A5A5A5A")  # backdoor: not logged
    host.mem_write32(DEMCR, 0x01000001)
    assert host.mem_read32(DEMCR) == 0x01000001
    host.mem_write32(SRAM + 8, 0xCAFEF00D)
    host.mem_write32(DBGMCU_IDCODE, 0)  # read-only
    host.mem_write32(FLASH_CR, 0)  # locked
    assert host.mem_read32(SRAM + 8) == 0xCAFEF00D
    assert host.mem_read32(DBGMCU_IDCODE) == 0x60001000
    assert host.mem_read32(FLASH_CR) == 0xC0000000
    assert writes(stub_proc) == [
        (DEMCR, 0x01000001),
        (SRAM + 8, 0xCAFEF00D),
        (DBGMCU_IDCODE, 0),
        (FLASH_CR, 0),
    ]
    st = stats(stub_proc)
    assert st["mem_writes"] == 4
    target(stub_proc, "clear")
    assert writes(stub_proc) == []
    assert stats(stub_proc)["mem_writes"] == 0


def test_dhcsr_halt_needs_key(host: SwdRefHost, stub_proc: StubProc):
    host.start_session()
    s_regrdy, s_halt, s_reset_st = 1 << 16, 1 << 17, 1 << 25
    # Power-on was a reset: S_RESET_ST once, cleared by the read.
    assert host.mem_read32(DHCSR) == s_regrdy | s_reset_st
    host.mem_write32(DHCSR, 0x00000003)
    assert host.mem_read32(DHCSR) == s_regrdy
    host.mem_write32(DHCSR, 0xA05F0002)  # C_HALT without C_DEBUGEN
    assert host.mem_read32(DHCSR) == s_regrdy
    host.mem_write32(DHCSR, 0xA05F0003)
    assert host.mem_read32(DHCSR) == s_regrdy | s_halt | 0x3
    host.mem_write32(DHCSR, 0xA05F0001)
    assert host.mem_read32(DHCSR) == s_regrdy | 0x1


def test_reset_with_vector_catch_halts(host: SwdRefHost, stub_proc: StubProc):
    host.start_session()
    s_reset_st = 1 << 25
    host.mem_write32(DHCSR, 0xA05F0001)
    host.mem_write32(DEMCR, 0x00000001)
    host.mem_write32(AIRCR, 0x05FA0004)
    assert host.mem_read32(DHCSR) == s_reset_st | (3 << 16) | 0x3
    assert host.mem_read32(DHCSR) & s_reset_st == 0
    assert stats(stub_proc)["sys_resets"] == 1
    assert (AIRCR, 0x05FA0004) in writes(stub_proc)


# ---- wires ----


def test_no_wire_reads_all_ones(stub_proc: StubProc):
    target(stub_proc, "connect 0")
    h = SwdRefHost(stub_proc, clk=SWD_CLK, dio=SWD_DIO)
    assert h.idle_check() == 1
    h.take()
    h.line_reset()
    h.idle(2)
    assert h.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    with pytest.raises(SwdError) as e:
        h.connect()
    assert e.value.ack == ACK_NONE
    st = stats(stub_proc)
    assert st["edges"] == 0
    assert st["line_resets"] == 0
    assert st["contention"] == 0
    target(stub_proc, "connect 1")
    assert h.connect() == DPIDR


def test_clk_held_low(stub_proc: StubProc):
    events: list[Event] = []
    stub_proc.on_event.append(events.append)
    target(stub_proc, "clk_held_low 1")
    h = SwdRefHost(stub_proc, clk=SWD_CLK, dio=SWD_DIO)
    assert h.idle_check() == 0
    assert any(
        e.kind == "swd"
        and "clk_check" in e.payload
        and e.payload["clk"] == str(SWD_CLK)
        and e.payload["level"] == "0"
        for e in events
    )
    # Driving SWCLK anyway fights the target and reaches nothing.
    h.take()
    h.line_reset()
    st = stats(stub_proc)
    assert st["contention"] > 0
    assert st["edges"] == 0
    assert st["line_resets"] == 0
    target(stub_proc, "clk_held_low 0")
    assert h.idle_check() == 1


def test_pin_events_and_untaken_calls(stub_proc: StubProc):
    events: list[Event] = []
    stub_proc.on_event.append(events.append)
    h = SwdRefHost(stub_proc, clk=SWD_CLK, dio=SWD_DIO)
    h.take()
    assert h.connect() == DPIDR
    h.release()
    swd = [e.payload for e in events if e.kind == "swd"]
    assert swd == [
        {"pins": "taken", "clk": str(SWD_CLK), "dio": str(SWD_DIO)},
        {"pins": "released"},
    ]
    assert stats(stub_proc)["untaken_ops"] == 0
    stub_proc.exec("swd_pin clk 1")
    stub_proc.exec("swd_pin get")
    assert stats(stub_proc)["untaken_ops"] == 2


def test_host_without_turnaround_is_contention(host: SwdRefHost, stub_proc: StubProc):
    host.connect()
    assert_clean(stub_proc)
    host.drive(True)
    host.write_bits(request_byte(0, 1, DP_DPIDR), 8)
    host.clock(2)  # still driving when the target puts out its ACK
    assert stats(stub_proc)["contention"] == 1


def test_correct_session_is_clean(host: SwdRefHost, stub_proc: StubProc):
    assert host.start_session() == DPIDR
    assert host.read_ap_now(AP_IDR) == AHB_AP_IDR
    host.mem_write32(SRAM + 0x40, 0x01020304)
    assert host.mem_read32(SRAM + 0x40) == 0x01020304
    assert host.mem_read32(CPUID) == 0x410CC601
    host.line_reset()
    host.idle(2)
    assert host.read_dp(DP_DPIDR) == DPIDR
    host.release()
    st = stats(stub_proc)
    assert_clean(stub_proc)
    assert st["faults"] == 0
    assert st["waits"] == 0
    assert st["bus_errors"] == 0
    assert st["lockout"] == 0


# ---- backdoor ----


def test_backdoor_and_reset(host: SwdRefHost, stub_proc: StubProc, tmp_path):
    image = bytes(range(256)) * 4
    path = tmp_path / "image.bin"
    path.write_bytes(image)
    assert target(stub_proc, f"load {FLASH + 0x400:X} {path}")["len"] == "1024"
    assert target(stub_proc, f"peek32 {FLASH + 0x400:X}")["value"] == "0x03020100"
    assert not stub_proc.exec("swd_target peek32 40000000").ok
    assert not stub_proc.exec(f"swd_target load {FLASH + 0x4F00:X} {path}").ok
    target(stub_proc, f"poke32 {DBGMCU_IDCODE:X} 12345678")
    host.start_session()
    assert host.mem_read32(FLASH + 0x7FC) == 0xFFFEFDFC
    assert host.mem_read32(FLASH + 0x800) == 0xFFFFFFFF
    assert host.mem_read32(DBGMCU_IDCODE) == 0x12345678
    target(stub_proc, "reset")
    assert target(stub_proc, f"peek32 {FLASH + 0x400:X}")["value"] == "0xFFFFFFFF"
    assert target(stub_proc, f"peek32 {DBGMCU_IDCODE:X}")["value"] == "0x60001000"
    st = stats(stub_proc)
    assert st["edges"] == 0
    assert st["powered"] == 0
    assert st["lockout"] == 1
    assert host.transfer(0, 1, DP_DPIDR).ack == ACK_NONE
    assert host.connect() == DPIDR
