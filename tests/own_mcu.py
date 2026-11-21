"""A fake own PY32 MCU (open-dimmer src/tuya.c, dimmer.c) for the stub
tests: it answers what the module sends in the order the real firmware does,
with the real full dump, and each round of answers back to back (the module's
UART hands them over as one burst).

What the real MCU does at first contact, as seen in a co-simulation of both
firmwares:

    MCU  0x20 query network (seq 0, 1, ...: numbered from 0 at every start)
    ZTU  0x20 answer, 0x0B version query
    MCU  its own full dump (first answer after its start), 0x0B answer
    ZTU  0x28 query all, 0xA3 identity query
    MCU  full dump, 0xA3 answer

The full dump is 5 passive (0x05) frames, 234 bytes: DP 14 (channel 1's
power-on) first, then per channel DPs 1..6, the curve (101/102) and the
power-on (103/104), then its five fade/hold settings (150..154/155..159).
From build 500 (2.2.0) that last frame of a channel also carries its wall
switch mode, level after a power cut and start boost (162, 164, 168 for
channel 1; 163, 165, 169 for channel 2), and its first frame the fast press
(170 / 171): 298 bytes.

Writes are taken as the firmware takes them (dimmer.c dimmer_write_dp) and
answered with an active report of the DP; a DP the build does not know, or a
value it refuses, gets no answer.

Each burst goes to the stub as one DMA chunk of the Telink router build
(uart_dma): a burst over its RX DMA buffer would be dropped whole there too.
"""
import re
from pathlib import Path

from test_mcu_ota import VERSION
from tuya_mcu import (
    TUYA_CMD_QUERY_ALL,
    TUYA_CMD_QUERY_NETWORK,
    TUYA_CMD_REPORT,
    TUYA_CMD_REPORT_PASSIVE,
    TUYA_CMD_WRITE,
    TUYA_TYPE_BOOL,
    TUYA_TYPE_ENUM,
    TUYA_TYPE_VALUE,
    dp_payload,
    parse_frames,
    pump,
    take_tx,
    tuya_frame,
)

CMD_NETWORK_STATUS = 0x02
CMD_VERSION = 0x0B
OTA_NOTIFY = 0xA0
OTA_IDENT = 0xA3

DP_POWER_ON = 14
DP_CURVE_BASE = 101
DP_POWER_ON_BASE = 103
DP_PROFILE_BASE = 150
DP_TRANSITION_BASE = 160   # transition time ms per channel, from build 400 (never in the dump)
DP_EXTRA_BASE = 162        # + 2 * k + channel, from build 500: EXTRA_KEYS[k]
EXTRA_KEYS = ("input", "power_level", "identify", "boost", "fast_press", "lamp_comp")
DP_HEALTH_FIRST = 130      # 130..137 every 2 min, 138 (die temperature) and 139 (its sensor's
                           # calibration) in a frame of their own
PROFILE_KEYS = ("fade_on", "fade_off", "soft_end", "hold", "hold_end")
TRANSITION_BUILD = 400
FEATURES_BUILD = 500
LAMP_COMP_BUILD = 1300    # 2.2.8: the lamp compensation, DP 172 / 173 (in its dump)
COUNTDOWN_MAX = 86400
UART_C = Path(__file__).resolve().parent.parent / "src/telink/hal/uart.c"


def telink_router_chunk_max() -> int:
    """Payload of the Telink RX DMA buffer on routers (the ZDMS16-2 build)."""
    text = UART_C.read_text()
    m = re.search(r"#ifdef END_DEVICE\s+#define UART_RX_DMA_BUF_SIZE\s+\d+\s+"
                  r"#else\s+#define UART_RX_DMA_BUF_SIZE\s+(\d+)\s+#endif", text)
    assert m, "UART_RX_DMA_BUF_SIZE block not found"
    return int(m.group(1)) - 4


UART_BURST_MAX = telink_router_chunk_max()   # a longer burst is dropped whole


def channel(power_on: int) -> dict:
    """The firmware's defaults (fresh flash), with a given power-on."""
    return {"on": 0, "level": 1000, "min": 200, "switch_type": 0, "max": 1000,
            "countdown": 0, "curve": 2, "power_on": power_on, "fade_on": 950,
            "fade_off": 950, "soft_end": 12, "hold": 3300, "hold_end": 20, "transition": 0,
            "input": 0, "power_level": 0, "identify": 0, "boost": 0, "fast_press": 0,
            "lamp_comp": 0}


class OwnMcu:
    def __init__(self, proc, device, version: int = VERSION, power_on=(0, 1),
                 protocol: int = 1, started: bool = True, build: int | None = None,
                 boot: int | None = None) -> None:
        self.proc, self.device = proc, device
        self.version, self.protocol = version, protocol
        self.build, self.boot = build, boot   # a v2 app says them in its identity answer
        self.ch = [channel(po) for po in power_on]
        self.query_seq = 0
        self.report_seq = 0
        # The module's first answer to a query of ours makes us dump our state
        # (the module may hold an old one): False = we have just started.
        self.told_state = not started
        self.lose: dict[int, int] = {}       # cmd -> how many answers to lose
        self.answers_ident = True
        self.log: list[tuple[str, int]] = []  # ("rx"/"tx", cmd) in wire order
        self.writes: list[tuple[int, int]] = []   # (dpid, value) of every DP write, in order
        assert proc.exec(f"uart_dma {UART_BURST_MAX}").ok

    def has(self, build: int) -> bool:
        """Its app is of that build or later."""
        return self.build is not None and self.build >= build

    # ---- frames ----------------------------------------------------------

    def _report(self, units: list[bytes], passive: bool = True) -> str:
        frame = tuya_frame(TUYA_CMD_REPORT_PASSIVE if passive else TUYA_CMD_REPORT,
                           b"".join(units), seq=self.report_seq)
        self.report_seq = (self.report_seq + 1) & 0xFFFF
        return frame

    def _dp(self, dpid: int) -> bytes:
        if dpid == DP_POWER_ON:
            return dp_payload(dpid, TUYA_TYPE_ENUM, bytes([self.ch[0]["power_on"]]))
        if DP_PROFILE_BASE <= dpid < DP_PROFILE_BASE + 10:
            c = self.ch[(dpid - DP_PROFILE_BASE) // 5]
            v = c[PROFILE_KEYS[(dpid - DP_PROFILE_BASE) % 5]]
            return dp_payload(dpid, TUYA_TYPE_VALUE, v.to_bytes(4, "big"))
        if DP_POWER_ON_BASE <= dpid < DP_POWER_ON_BASE + 2:
            c = self.ch[dpid - DP_POWER_ON_BASE]
            return dp_payload(dpid, TUYA_TYPE_ENUM, bytes([c["power_on"]]))
        if DP_TRANSITION_BASE <= dpid < DP_TRANSITION_BASE + 2:
            v = self.ch[dpid - DP_TRANSITION_BASE]["transition"]
            return dp_payload(dpid, TUYA_TYPE_VALUE, v.to_bytes(4, "big"))
        if DP_EXTRA_BASE <= dpid < DP_EXTRA_BASE + 2 * len(EXTRA_KEYS):
            k, ch = divmod(dpid - DP_EXTRA_BASE, 2)
            return dp_payload(dpid, TUYA_TYPE_VALUE, self.ch[ch][EXTRA_KEYS[k]].to_bytes(4, "big"))
        if DP_CURVE_BASE <= dpid < DP_CURVE_BASE + 2:
            return dp_payload(dpid, TUYA_TYPE_ENUM, bytes([self.ch[dpid - DP_CURVE_BASE]["curve"]]))
        c = self.ch[(dpid - 1) // 6]
        idx = (dpid - 1) % 6
        if idx == 0:
            return dp_payload(dpid, TUYA_TYPE_BOOL, bytes([c["on"]]))
        if idx == 3:
            return dp_payload(dpid, TUYA_TYPE_ENUM, bytes([c["switch_type"]]))
        key = {1: "level", 2: "min", 4: "max", 5: "countdown"}[idx]
        return dp_payload(dpid, TUYA_TYPE_VALUE, c[key].to_bytes(4, "big"))

    def dump(self) -> list[str]:
        """dimmer_report_all(): the 5 frames of a full dump."""
        frames = [self._report([self._dp(DP_POWER_ON)])]
        for ch in range(2):
            base = ch * 6 + 1
            fast = [self._dp(DP_EXTRA_BASE + 8 + ch)] if self.has(FEATURES_BUILD) else []
            frames.append(self._report([self._dp(base + i) for i in range(6)]
                                       + [self._dp(DP_CURVE_BASE + ch),
                                          self._dp(DP_POWER_ON_BASE + ch)] + fast))
            extras = [DP_EXTRA_BASE + 2 * k + ch for k in (0, 1, 3)] if self.has(FEATURES_BUILD) else []
            frames.append(self._report([self._dp(DP_PROFILE_BASE + 5 * ch + k)
                                        for k in range(5)] + [self._dp(dp) for dp in extras]))
            if self.has(LAMP_COMP_BUILD):        # the frame was full: one of its own
                frames.append(self._report([self._dp(DP_EXTRA_BASE + 10 + ch)]))
        return frames

    def health(self, uptime_min: int = 61, reset: int = 0x08, zc_losses: int = 0,
               noise: int = 0, saves: int = 0, uart_losses: int = 0, boot_crc: int = 0xEAC45031,
               boot_word: int = 0, temperature: int | None = 31,
               temp_cal: int = 0x047203B5) -> list[str]:
        """health_poll(): DPs 130..137 in one active report, then (from 2.2.0) the die
        temperature, DP 138 (signed; None: -32768, not measured yet), and the factory
        calibration of its sensor, DP 139 (raw at 85 degC << 16 | at 30 degC), in a frame of
        their own."""
        values = (uptime_min, reset, zc_losses, noise, saves, uart_losses, boot_crc, boot_word)
        frames = [self._report([dp_payload(DP_HEALTH_FIRST + i, TUYA_TYPE_VALUE, v.to_bytes(4, "big"))
                                for i, v in enumerate(values)], passive=False)]
        if self.has(FEATURES_BUILD):
            t = -32768 if temperature is None else temperature
            frames.append(self._report([dp_payload(138, TUYA_TYPE_VALUE, (t & 0xFFFFFFFF).to_bytes(4, "big")),
                                        dp_payload(139, TUYA_TYPE_VALUE, temp_cal.to_bytes(4, "big"))],
                                       passive=False))
        return frames

    def take_write(self, dpid: int, data: bytes) -> bool:
        """dimmer_write_dp(): apply a DP write; False when the build does not take it (no
        answer then)."""
        vlen = int.from_bytes(data[2:4], "big")
        value = int.from_bytes(data[4:4 + vlen], "big")
        self.writes.append((dpid, value))
        if DP_POWER_ON_BASE <= dpid < DP_POWER_ON_BASE + 2:
            self.ch[dpid - DP_POWER_ON_BASE]["power_on"] = value
        elif DP_TRANSITION_BASE <= dpid < DP_TRANSITION_BASE + 2:
            if not self.has(TRANSITION_BUILD):
                return False                     # an older app does not know it
            self.ch[dpid - DP_TRANSITION_BASE]["transition"] = min(value, 0xFFFF)
        elif DP_EXTRA_BASE <= dpid < DP_EXTRA_BASE + 2 * len(EXTRA_KEYS):
            if not self.has(FEATURES_BUILD):
                return False
            k, ch = divmod(dpid - DP_EXTRA_BASE, 2)
            c = self.ch[ch]
            if k == 5:                           # lamp compensation, from 2.2.8
                if not self.has(LAMP_COMP_BUILD) or value > 252:
                    return False
                c["lamp_comp"] = (value + 2) // 4 * 4
            elif k == 4:                         # fast press
                if value > 1:
                    return False
                c["fast_press"] = value
            elif k == 0:
                if value > 2:
                    return False
                c["input"] = value
            elif k == 1:                         # kept in steps of 4, within 10..1000
                c["power_level"] = ((min(max(value, 10), 1000) + 2) >> 2) * 4 if value else 0
            elif k == 2:
                c["identify"] = min(value, 255)
            else:
                if value > 750:
                    return False
                c["boost"] = value // 50 * 50
        elif dpid == DP_POWER_ON:
            for c in self.ch:
                c["power_on"] = value
        elif 1 <= dpid <= 12:
            c = self.ch[(dpid - 1) // 6]
            idx = (dpid - 1) % 6
            if idx == 0:
                c["on"] = 1 if value else 0
                if not c["on"]:
                    c["countdown"] = 0           # an off ends a countdown
            elif idx == 1:
                c["level"] = min(max(value, c["min"]), c["max"])
            elif idx == 5:                       # counts only while on
                c["countdown"] = min(value, COUNTDOWN_MAX) if c["on"] else 0
        return True

    # ---- what the MCU sends by itself ------------------------------------

    def send(self, frames: list[str]) -> None:
        """One burst: all frames back to back as one DMA chunk, then one poll
        of the module."""
        size = sum(len(f) // 2 for f in frames)
        assert size <= UART_BURST_MAX, f"burst of {size} B would be dropped whole"
        for f in frames:
            self.log.append(("tx", int(f[10:12], 16)))
        res = self.proc.exec("uart_rx " + " ".join(frames))
        assert res.ok and int(res.payload["injected"]) == size
        pump(self.proc)

    def query_network(self, seq: int | None = None) -> None:
        """Its periodic network query (every 30 s once connected)."""
        if seq is not None:
            self.query_seq = seq
        self.send([tuya_frame(TUYA_CMD_QUERY_NETWORK, seq=self.query_seq)])
        self.query_seq = (self.query_seq + 1) & 0xFFFF

    def restart(self) -> None:
        """Watchdog, reflash or update: it numbers its queries from 0 again and
        dumps its state at the module's first answer. The first query comes
        200 ms after the start."""
        self.query_seq = 0
        self.told_state = False
        for c in self.ch:                    # not saved: gone with the restart
            c["countdown"] = c["identify"] = 0

    # ---- answering the module --------------------------------------------

    def _lost(self, cmd: int) -> bool:
        if self.lose.get(cmd, 0) > 0:
            self.lose[cmd] -= 1
            return True
        return False

    def _answer(self, cmd: int, data: bytes) -> list[str]:
        """Its answers to one frame of the module (the module does not check
        the answers' sequence numbers: 0)."""
        if cmd == TUYA_CMD_QUERY_NETWORK and data:      # the answer to our query
            if not self.told_state:
                self.told_state = True
                return self.dump()
            return []
        if cmd == CMD_NETWORK_STATUS:
            return [tuya_frame(CMD_NETWORK_STATUS, seq=0)]
        if self._lost(cmd):
            return []
        if cmd == CMD_VERSION:
            return [tuya_frame(CMD_VERSION, bytes([self.version]), seq=0)]
        if cmd == TUYA_CMD_QUERY_ALL:
            return self.dump()
        if cmd == OTA_IDENT and self.answers_ident:
            ident = b"ZDMS" + bytes([1, self.version, self.protocol])
            if self.build is not None:
                ident += self.build.to_bytes(2, "big")
                if self.boot is not None:
                    ident += bytes([self.boot])
            return [tuya_frame(OTA_IDENT, ident, seq=0)]
        if cmd == OTA_NOTIFY:
            return [tuya_frame(OTA_NOTIFY, bytes([1]), seq=0)]   # declined
        if cmd == TUYA_CMD_WRITE:
            if not self.take_write(data[0], data):
                return []
            return [self._report([self._dp(data[0])], passive=False)]
        return []

    def serve(self, ms: int = 0, step_ms: int = 50) -> list[int]:
        """Answer the module until it is quiet, for at least `ms` of time.
        Returns the commands the module sent."""
        sent = []
        elapsed = 0
        quiet = 0
        while elapsed < ms or quiet < 2:
            tx = take_tx(self.proc)
            if not tx:
                quiet += 1
                self.device.step_time(step_ms)
                elapsed += step_ms
                pump(self.proc)
                continue
            quiet = 0
            answers = []
            for cmd, data in parse_frames(tx):
                self.log.append(("rx", cmd))
                sent.append(cmd)
                answers += self._answer(cmd, data)
            if answers:
                self.send(answers)
            else:
                pump(self.proc)
        return sent


def own_mcu_contact(proc, device, prepare=None, **kw) -> OwnMcu:
    """Mains on: both start, the MCU's first query (seq 0, 200 ms) comes before
    the module listens, its second one (seq 1, 2.2 s) starts the first contact,
    which then runs in the real order with the real dump. prepare(mcu): set its
    saved settings before that."""
    mcu = OwnMcu(proc, device, **kw)
    if prepare is not None:
        prepare(mcu)
    mcu.query_seq = 1
    device.step_time(1900)          # the module's own contact timer is at 2 s
    pump(proc)
    take_tx(proc)
    mcu.query_network()
    mcu.serve(1000)
    return mcu
