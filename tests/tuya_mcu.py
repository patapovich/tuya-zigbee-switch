"""Tuya secondary-MCU serial protocol helpers for the stub tests: build frames
the MCU would send, and decode what the firmware sent (stub uart_rx/uart_tx)."""
from client import StubProc

TUYA_CMD_NETWORK_STATUS = 0x02
TUYA_CMD_RESET_NETWORK = 0x03
TUYA_CMD_WRITE = 0x04
TUYA_CMD_REPORT_PASSIVE = 0x05
TUYA_CMD_REPORT = 0x06
TUYA_CMD_QUERY_NETWORK = 0x20
TUYA_CMD_SYNC_TIME = 0x24
TUYA_CMD_QUERY_ALL = 0x28

TUYA_TYPE_BOOL = 0x01
TUYA_TYPE_VALUE = 0x02
TUYA_TYPE_ENUM = 0x04


def tuya_frame(cmd: int, data: bytes = b"", seq: int = 1) -> str:
    body = (
        bytes([0x55, 0xAA, 0x02])
        + seq.to_bytes(2, "big")
        + bytes([cmd])
        + len(data).to_bytes(2, "big")
        + data
    )
    return (body + bytes([sum(body) % 256])).hex().upper()


def dp_payload(dp: int, dp_type: int, value: bytes) -> bytes:
    return bytes([dp, dp_type]) + len(value).to_bytes(2, "big") + value


def value_dp(dp: int, value: int) -> bytes:
    return dp_payload(dp, TUYA_TYPE_VALUE, value.to_bytes(4, "big"))


def bool_dp(dp: int, value: int) -> bytes:
    return dp_payload(dp, TUYA_TYPE_BOOL, bytes([value]))


def enum_dp(dp: int, value: int) -> bytes:
    return dp_payload(dp, TUYA_TYPE_ENUM, bytes([value]))


def pump(proc: StubProc, times: int = 4) -> None:
    for _ in range(times):
        proc.exec("s")


def take_tx(proc: StubProc) -> bytes:
    res = proc.exec("uart_tx")
    assert res.ok
    return bytes.fromhex(res.payload["tx"])


def parse_frames(raw: bytes) -> list[tuple[int, bytes]]:
    """Split a TX capture into (cmd, data) frames, checking every checksum."""
    frames = []
    i = 0
    while i + 9 <= len(raw):
        assert raw[i : i + 3] == b"\x55\xaa\x02", raw[i:].hex()
        cmd = raw[i + 5]
        dlen = int.from_bytes(raw[i + 6 : i + 8], "big")
        end = i + 8 + dlen
        assert sum(raw[i:end]) % 256 == raw[end], "bad checksum"
        frames.append((cmd, raw[i + 8 : end]))
        i = end + 1
    assert i == len(raw), f"trailing bytes {raw[i:].hex()}"
    return frames


def dp_writes(raw: bytes) -> list[tuple[int, int]]:
    """(dpid, value) for every DP write in a TX capture."""
    out = []
    for cmd, data in parse_frames(raw):
        if cmd != TUYA_CMD_WRITE:
            continue
        dpid, _, vlen = data[0], data[1], int.from_bytes(data[2:4], "big")
        out.append((dpid, int.from_bytes(data[4 : 4 + vlen], "big")))
    return out


def mcu_report(proc: StubProc, *dps: bytes, cmd: int = TUYA_CMD_REPORT) -> None:
    proc.exec(f"uart_rx {tuya_frame(cmd, b''.join(dps))}")
    pump(proc)


def dp_units(raw: bytes) -> list[bytes]:
    """The DP units (dpid, type, len, value) of every DP write in a capture."""
    return [data for cmd, data in parse_frames(raw) if cmd == TUYA_CMD_WRITE]


# ---- a factory MCU -----------------------------------------------------------

TUYA_CMD_MCU_VERSION = 0x0B
OTA_BLOCK = 0xA1
STOCK_VERSION = 0x47                         # the stock ZDMS16-2 PY32: 1.0.7

# What the module may send to an MCU it has not identified as Open Dimmer
# (contract item 6, src/zigbee/tuya_mcu_ota.h): the stock USART ring holds 23
# bytes, its parse buffer 31, and a frame of 32..38 bytes wedges its RX until
# a power cut (open-dimmer docs/BOOTLOADER.md section 15, WiFi 1.0.3 parser).
FACTORY_FRAME_MAX = 29
FACTORY_BURST_MAX = 23


def stock_dump() -> list[bytes]:
    """A stock ZDMS16-2 dump (answer to 0x28): per channel on, level, min,
    switch type, max, then the shared power-on (DP 14, memory)."""
    frames = []
    for base in (1, 7):
        frames.append(bool_dp(base, 0) + value_dp(base + 1, 500) + value_dp(base + 2, 201)
                      + enum_dp(base + 3, 0) + value_dp(base + 4, 1000))
    frames.append(enum_dp(14, 2))
    return frames


class StockMcu:
    """A factory PY32 as the module sees it: 0x0B is answered with its
    version, 0x28 with a DP dump, a DP write with that DP back (0x06), and
    everything else is ignored (the Tuya serial protocol has no NAK). Frames
    the module sends before `ready_ms` (stub time: ms since the module's app
    init) are lost, as while the stock firmware waits 1.5 s after its power-up
    before it reads its UART.

    Every frame it gets is checked against the factory limits: at most 29
    bytes, at most 23 bytes in one burst (the frames of one stub poll, i.e.
    sent without an MCU frame in between), longer frames only as the direct
    reply to an 0xA1 request it sent itself. `trace` holds (t, cmd, data) of
    every frame in wire order; `faults` every breach of the limits."""

    def __init__(self, proc, device, version: int = STOCK_VERSION, ready_ms: int = 0,
                 dump: list[bytes] | None = None, step_ms: int = 10,
                 asked: frozenset[int] = frozenset()) -> None:
        self.proc, self.device = proc, device
        self.version, self.ready_ms = version, ready_ms
        self.asked = asked                   # own commands it may get (0xA3 for a 2.x one)
        self.dump = stock_dump() if dump is None else dump
        self.step_ms = step_ms
        self.t = 0
        self.seq = 1
        self.trace: list[tuple[int, int, bytes]] = []
        self.faults: list[str] = []
        self.requests = 0                    # 0xA1 requests sent and not answered yet
        self.ignored: list[int] = []         # commands heard and ignored

    # ---- what it sends --------------------------------------------------

    def _frame(self, cmd: int, data: bytes, seq: int | None) -> str:
        if seq is None:
            seq = self.seq
            self.seq = (self.seq + 1) & 0xFFFF
        if cmd == OTA_BLOCK:
            self.requests += 1
        return tuya_frame(cmd, data, seq)

    def send(self, cmd: int, data: bytes = b"", seq: int | None = None) -> None:
        """A frame of its own (a test's spurious frame), then the module's
        answers to it."""
        assert self.proc.exec(f"uart_rx {self._frame(cmd, data, seq)}").ok
        pump(self.proc, 2)
        self.poll()

    def _send_answers(self, answers: list[tuple[int, bytes]]) -> None:
        """Its answers to one burst, back to back (one chunk)."""
        frames = [self._frame(cmd, data, None) for cmd, data in answers]
        assert self.proc.exec("uart_rx " + " ".join(frames)).ok
        pump(self.proc, 2)

    # ---- what it gets ---------------------------------------------------

    def _check(self, burst: list[tuple[int, bytes]]) -> None:
        total = 0
        for cmd, data in burst:
            n = 9 + len(data)
            if cmd == OTA_BLOCK and self.requests > 0:
                self.requests -= 1           # the direct reply: any length
                continue
            if 0xA0 <= cmd <= 0xA3 and cmd not in self.asked:
                self.faults.append(f"{self.t} ms: own command 0x{cmd:02X} to a factory MCU")
            if n > FACTORY_FRAME_MAX:
                self.faults.append(f"{self.t} ms: 0x{cmd:02X} frame of {n} B")
            total += n
        if total > FACTORY_BURST_MAX:
            self.faults.append(f"{self.t} ms: burst of {total} B "
                               f"({', '.join(f'0x{c:02X}' for c, _ in burst)})")

    def _answer(self, cmd: int, data: bytes) -> list[tuple[int, bytes]]:
        if cmd == TUYA_CMD_MCU_VERSION:
            return [(TUYA_CMD_MCU_VERSION, bytes([self.version]))]
        if cmd == TUYA_CMD_QUERY_ALL:
            return [(TUYA_CMD_REPORT, f) for f in self.dump]
        if cmd == TUYA_CMD_WRITE:
            return [(TUYA_CMD_REPORT, data)]     # the stock ZDMS16-2 answers with 0x06
        self.ignored.append(cmd)
        return []

    def poll(self) -> None:
        """Take what the module sent now; answer it; repeat while the answers
        make it send more (at the same stub time)."""
        for _ in range(16):
            burst = parse_frames(take_tx(self.proc))
            if not burst:
                return
            self.trace += [(self.t, cmd, data) for cmd, data in burst]
            self._check(burst)
            if self.t < self.ready_ms:
                continue                     # not listening yet: lost
            answers = []
            for cmd, data in burst:
                answers += self._answer(cmd, data)
            if answers:
                self._send_answers(answers)
        raise AssertionError("the module never stops sending")

    def run(self, ms: int, step_ms: int | None = None) -> None:
        step = step_ms or self.step_ms
        end = self.t + ms
        while self.t < end:
            n = min(step, end - self.t)
            self.device.step_time(n)
            self.t += n
            pump(self.proc, 1)
            self.poll()

    def cmds(self) -> list[int]:
        return [cmd for _, cmd, _ in self.trace]
