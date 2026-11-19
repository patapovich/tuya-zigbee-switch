"""A small reference SWD host for the stub's dimmer-MCU model.

Written from the ADIv5 / SWD protocol v1 description, on its own (not from
the model in src/stub/hal/swd_target.c), so the model's tests check it against
an independent reading of the spec. It drives the stub pin layer
(src/hal/swd_io.h) by hand, one pin call per REPL `swd_pin` command.

Clocking: SWCLK idles low. A host bit: put it on SWDIO, raise SWCLK (the
target samples on the rising edge), lower SWCLK. A target bit: read SWDIO while
SWCLK is low, then pulse SWCLK (the target puts out its next bit after the
rising edge). Every bit sequence goes LSB first.
"""

from dataclasses import dataclass

from client import StubProc

ACK_OK = 0b001
ACK_WAIT = 0b010
ACK_FAULT = 0b100
ACK_NONE = 0b111  # nobody drove SWDIO: the pull-up

# DP registers, A[3:2] (DPv1)
DP_DPIDR = 0x0  # read
DP_ABORT = 0x0  # write
DP_CTRL_STAT = 0x4
DP_SELECT = 0x8  # write
DP_RESEND = 0x8  # read
DP_RDBUFF = 0xC  # read

ABORT_DAPABORT = 1 << 0
ABORT_STKCMPCLR = 1 << 1
ABORT_STKERRCLR = 1 << 2
ABORT_WDERRCLR = 1 << 3
ABORT_ORUNERRCLR = 1 << 4
ABORT_CLEAR_STICKY = 0x1E

CS_STICKYORUN = 1 << 1
CS_STICKYCMP = 1 << 4
CS_STICKYERR = 1 << 5
CS_WDATAERR = 1 << 7
CS_CDBGPWRUPREQ = 1 << 28
CS_CDBGPWRUPACK = 1 << 29
CS_CSYSPWRUPREQ = 1 << 30
CS_CSYSPWRUPACK = 1 << 31

# MEM-AP registers: APBANKSEL << 4 | A[3:2] << 2
AP_CSW = 0x00
AP_TAR = 0x04
AP_DRW = 0x0C
AP_IDR = 0xFC

CSW_SIZE_32 = 0x2
CSW_ADDRINC_SINGLE = 0x1 << 4

JTAG_TO_SWD = 0xE79E
LINE_RESET_ONES = 50

# ARMv6-M debug registers (SCS)
AIRCR = 0xE000ED0C
DHCSR = 0xE000EDF0
DCRSR = 0xE000EDF4
DCRDR = 0xE000EDF8
DEMCR = 0xE000EDFC
DHCSR_KEY = 0xA05F << 16
C_DEBUGEN = 1 << 0
C_HALT = 1 << 1
S_REGRDY = 1 << 16
S_HALT = 1 << 17
S_LOCKUP = 1 << 19
S_RESET_ST = 1 << 25
DCRSR_REGWNR = 1 << 16
DEMCR_VC_CORERESET = 1 << 0
AIRCR_SYSRESETREQ = 0x05FA0004
REG_SP = 13
REG_PC = 15
REG_XPSR = 16
REG_MSP = 17
XPSR_T = 1 << 24


def parity(value: int) -> int:
    return bin(value).count("1") & 1


def request_byte(apndp: int, rnw: int, addr: int) -> int:
    """Start, APnDP, RnW, A[2:3], parity, stop 0, park 1 (bit 0 first)."""
    a2 = (addr >> 2) & 1
    a3 = (addr >> 3) & 1
    par = apndp ^ rnw ^ a2 ^ a3
    return 1 | apndp << 1 | rnw << 2 | a2 << 3 | a3 << 4 | par << 5 | 0 << 6 | 1 << 7


@dataclass
class Transfer:
    ack: int
    data: int | None = None
    parity_ok: bool = True


class SwdError(Exception):
    def __init__(self, what: str, ack: int) -> None:
        super().__init__(f"{what}: ack=0b{ack:03b}")
        self.ack = ack


class SwdRefHost:
    def __init__(self, proc: StubProc, clk: int = 22, dio: int = 23) -> None:
        self.p = proc
        self.clk = clk
        self.dio = dio
        self._driving = False
        self._level: int | None = None
        self._select: int | None = None

    # ---- pins ----

    def _pin(self, cmd: str) -> dict[str, str]:
        res = self.p.exec(f"swd_pin {cmd}")
        assert res.ok, f"swd_pin {cmd}: {res.payload}"
        return res.payload

    def idle_check(self) -> int:
        return int(self._pin(f"idle {self.clk}")["level"])

    def take(self) -> None:
        self._pin(f"take {self.clk} {self.dio}")
        self._driving = False
        self._level = None
        self._select = None

    def release(self) -> None:
        self._pin("release")
        self._driving = False

    def drive(self, on: bool) -> None:
        if on != self._driving:
            self._pin(f"drive {int(on)}")
            self._driving = on

    def _set(self, level: int) -> None:
        if level != self._level:
            self._pin(f"set {level}")
            self._level = level

    def clock(self, cycles: int = 1) -> None:
        for _ in range(cycles):
            self._pin("clk 1")
            self._pin("clk 0")

    def write_bits(self, value: int, count: int) -> None:
        """Host bits, LSB first. The caller has SWDIO driven."""
        for i in range(count):
            self._set((value >> i) & 1)
            self.clock()

    def read_bits(self, count: int) -> int:
        """Target bits, LSB first, each read before its rising edge."""
        value = 0
        for i in range(count):
            bit = int(self._pin("get")["level"])
            value |= bit << i
            self.clock()
        return value

    # ---- line ----

    def line_reset(self, ones: int = LINE_RESET_ONES + 6) -> None:
        self.drive(True)
        self.write_bits((1 << ones) - 1, ones)
        self._select = None

    def idle(self, cycles: int = 2) -> None:
        self.drive(True)
        self.write_bits(0, cycles)

    def jtag_to_swd(self) -> None:
        self.drive(True)
        self.write_bits(JTAG_TO_SWD, 16)

    def connect(self) -> int:
        """Line reset, JTAG-to-SWD, line reset, idle, DPIDR."""
        self.line_reset()
        self.jtag_to_swd()
        self.line_reset()
        self.idle(2)
        return self.read_dp(DP_DPIDR)

    # ---- transfers ----

    def raw_transfer(self, request: int, wdata: int = 0) -> Transfer:
        """One packet from a given request byte (it may be malformed)."""
        rnw = (request >> 2) & 1
        self.drive(True)
        self.write_bits(request, 8)
        self.drive(False)
        self.clock()  # turnaround
        ack = self.read_bits(3)
        if ack == ACK_OK and rnw:
            data = self.read_bits(32)
            par = self.read_bits(1)
            self.clock()  # turnaround
            self.drive(True)
            return Transfer(ack, data, par == parity(data))
        if ack == ACK_OK:
            self.clock()  # turnaround
            self.drive(True)
            self.write_bits(wdata, 32)
            self.write_bits(parity(wdata), 1)
            return Transfer(ack)
        if ack in (ACK_WAIT, ACK_FAULT):
            self.clock()  # turnaround
            self.drive(True)
            return Transfer(ack)
        # No (or a bad) ACK: let the data phase and a turnaround pass with
        # SWDIO released.
        self.clock(33 + 1)
        self.drive(True)
        return Transfer(ack)

    def transfer(self, apndp: int, rnw: int, addr: int, wdata: int = 0) -> Transfer:
        return self.raw_transfer(request_byte(apndp, rnw, addr), wdata)

    def _ok_read(self, apndp: int, addr: int) -> int:
        t = self.transfer(apndp, 1, addr)
        if t.ack != ACK_OK:
            raise SwdError(f"{'AP' if apndp else 'DP'} read 0x{addr:X}", t.ack)
        assert t.parity_ok, "read data parity"
        assert t.data is not None
        return t.data

    def _ok_write(self, apndp: int, addr: int, value: int) -> None:
        t = self.transfer(apndp, 0, addr, value)
        if t.ack != ACK_OK:
            raise SwdError(f"{'AP' if apndp else 'DP'} write 0x{addr:X}", t.ack)

    def read_dp(self, addr: int) -> int:
        return self._ok_read(0, addr)

    def write_dp(self, addr: int, value: int) -> None:
        self._ok_write(0, addr, value)
        if addr == DP_SELECT:
            self._select = value

    def select_ap(self, reg: int, apsel: int = 0) -> None:
        value = apsel << 24 | (reg >> 4 & 0xF) << 4
        if value != self._select:
            self.write_dp(DP_SELECT, value)

    def read_ap(self, reg: int, apsel: int = 0) -> int:
        """Posted: returns the result of the previous AP read."""
        self.select_ap(reg, apsel)
        return self._ok_read(1, reg & 0xC)

    def write_ap(self, reg: int, value: int, apsel: int = 0) -> None:
        self.select_ap(reg, apsel)
        self._ok_write(1, reg & 0xC, value)

    def read_ap_now(self, reg: int, apsel: int = 0) -> int:
        """AP read, then RDBUFF for its own result."""
        self.read_ap(reg, apsel)
        return self.read_dp(DP_RDBUFF)

    # ---- sessions ----

    def clear_errors(self) -> None:
        self.write_dp(DP_ABORT, ABORT_CLEAR_STICKY)

    def power_up(self, polls: int = 10) -> int:
        self.write_dp(DP_CTRL_STAT, CS_CSYSPWRUPREQ | CS_CDBGPWRUPREQ)
        for _ in range(polls):
            cs = self.read_dp(DP_CTRL_STAT)
            if cs & (CS_CSYSPWRUPACK | CS_CDBGPWRUPACK) == (
                CS_CSYSPWRUPACK | CS_CDBGPWRUPACK
            ):
                return cs
        raise AssertionError(f"no power-up ACK: CTRL/STAT 0x{cs:08X}")

    def mem_setup(self, csw: int = CSW_SIZE_32 | CSW_ADDRINC_SINGLE) -> None:
        self.write_ap(AP_CSW, csw)

    def mem_read32(self, addr: int) -> int:
        self.write_ap(AP_TAR, addr)
        self.read_ap(AP_DRW)
        return self.read_dp(DP_RDBUFF)

    def mem_write32(self, addr: int, value: int) -> None:
        self.write_ap(AP_TAR, addr)
        self.write_ap(AP_DRW, value)

    def start_session(self) -> int:
        """Connect, clear sticky flags, power up, 32-bit MEM-AP. DPIDR back."""
        dpidr = self.connect()
        self.clear_errors()
        self.power_up()
        self.mem_setup()
        return dpidr

    # ---- blocks (MEM-AP with AddrInc single; TAR only wraps in 1 KB) ----

    def mem_write_block(self, addr: int, words: list[int]) -> None:
        for i, word in enumerate(words):
            a = addr + 4 * i
            if i == 0 or a % 0x400 == 0:
                self.write_ap(AP_TAR, a)
            self.write_ap(AP_DRW, word)

    def mem_read_block(self, addr: int, count: int) -> list[int]:
        out: list[int] = []
        for i in range(count):
            a = addr + 4 * i
            if i == 0 or a % 0x400 == 0:
                if i:
                    out.append(self.read_dp(DP_RDBUFF))
                self.write_ap(AP_TAR, a)
                self.read_ap(AP_DRW)  # the result comes with the next read
            else:
                out.append(self.read_ap(AP_DRW))
        out.append(self.read_dp(DP_RDBUFF))
        return out

    # ---- ARMv6-M core debug ----

    def dhcsr(self) -> int:
        return self.mem_read32(DHCSR)

    def halt(self) -> int:
        self.mem_write32(DHCSR, DHCSR_KEY | C_DEBUGEN | C_HALT)
        return self.dhcsr()

    def resume(self) -> int:
        self.mem_write32(DHCSR, DHCSR_KEY | C_DEBUGEN)
        return self.dhcsr()

    def _regrdy(self) -> None:
        for _ in range(10):
            if self.dhcsr() & S_REGRDY:
                return
        raise AssertionError("S_REGRDY stays 0")

    def write_core_reg(self, sel: int, value: int) -> None:
        self.mem_write32(DCRDR, value)
        self.mem_write32(DCRSR, DCRSR_REGWNR | sel)
        self._regrdy()

    def read_core_reg(self, sel: int) -> int:
        self.mem_write32(DCRSR, sel)
        self._regrdy()
        return self.mem_read32(DCRDR)

    def system_reset(self) -> None:
        """SYSRESETREQ. The AP stays usable: the debug domain is not reset."""
        self.mem_write32(AIRCR, AIRCR_SYSRESETREQ)

    def reset_halt(self) -> int:
        """Halt at the reset vector: vector catch, then a system reset."""
        self.mem_write32(DHCSR, DHCSR_KEY | C_DEBUGEN)
        self.mem_write32(DEMCR, DEMCR_VC_CORERESET)
        self.system_reset()
        return self.dhcsr()
