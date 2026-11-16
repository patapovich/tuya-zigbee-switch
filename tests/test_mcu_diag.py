"""Secondary-MCU link diagnostics on the Basic cluster (0xff40..0xff4f)."""
import sys
from pathlib import Path

from client import StubProc
from conftest import Device
from test_dimmer_mcu import (
    DP_LEVEL_L1,
    DP_ON_L1,
    TUYA_CMD_REPORT,
    TUYA_CMD_REPORT_PASSIVE,
    ZDMS16_2,
    bool_dp,
    mcu_report,
    pump,
    take_tx,
    tuya_frame,
    value_dp,
)
from zcl_consts import ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "helper_scripts"))
from decode_mcu_diag import decode_counters, decode_log, to_bytes  # noqa: E402

BASIC = 0x0000
ATTR_COUNTERS = 0xFF40
ATTR_LOG = [0xFF41 + i for i in range(5)]
ATTR_CONTROL = 0xFF4F


def counters(device: Device) -> dict:
    return decode_counters(to_bytes(device.read_zigbee_attr(1, BASIC, ATTR_COUNTERS)))


def log(device: Device) -> list[dict]:
    return decode_log([to_bytes(device.read_zigbee_attr(1, BASIC, a)) for a in ATTR_LOG])


def start() -> tuple[StubProc, Device]:
    proc = StubProc(device_config=ZDMS16_2).start()
    device = Device(proc)
    pump(proc)
    take_tx(proc)
    device.write_zigbee_attr(1, BASIC, ATTR_CONTROL, 1)  # clean slate
    return proc, device


def test_not_registered_without_secondary_mcu():
    with StubProc(device_config="A;B;LB0;") as proc:
        res = proc.exec(f"zcl_read 1 0x{BASIC:04X} 0x{ATTR_COUNTERS:04X}")
        assert not res.ok


def test_write_ack_is_logged_with_latency():
    proc, device = start()
    try:
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
            bytes([200, 0, 0]),
        )
        pump(proc)
        take_tx(proc)
        device.step_time(40)
        mcu_report(proc, value_dp(DP_LEVEL_L1, 787))  # ack of the level write
        c = counters(device)
        assert c["tx_dp"] == 2  # level, then on after the ack
        assert c["tx_ack"] == 1
        assert c["ack_latency_last_ms"] >= 40
        kinds = [(e["kind"], e["id"]) for e in log(device)]
        assert kinds[:3] == [("TX_DP", DP_LEVEL_L1), ("RX_DP", DP_LEVEL_L1), ("ACK", DP_LEVEL_L1)]
        tx = [e for e in log(device) if e["kind"] == "TX_DP"]
        assert tx[0]["value"] == 787
    finally:
        proc.stop()


def test_ack_timeout_is_counted():
    proc, device = start()
    try:
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
            bytes([200, 0, 0]),
        )
        pump(proc)
        device.step_time(500)
        pump(proc)
        assert counters(device)["tx_timeout"] >= 1
        assert ("ACK_TIMEOUT", DP_LEVEL_L1) in [(e["kind"], e["id"]) for e in log(device)]
    finally:
        proc.stop()


def test_multi_dp_frame_logs_every_dp():
    proc, device = start()
    try:
        mcu_report(proc, bool_dp(DP_ON_L1, 1), value_dp(DP_LEVEL_L1, 500),
                   cmd=TUYA_CMD_REPORT_PASSIVE)
        c = counters(device)
        assert c["rx_frames"] == 1 and c["rx_dps"] == 2
        entries = [(e["kind"], e["id"], e["value"]) for e in log(device)]
        assert entries == [("RX_DP_PASSIVE", DP_ON_L1, 1), ("RX_DP_PASSIVE", DP_LEVEL_L1, 500)]
    finally:
        proc.stop()


def test_bad_checksum_and_mcu_commands_are_logged():
    proc, device = start()
    try:
        good = tuya_frame(TUYA_CMD_REPORT, bool_dp(DP_ON_L1, 1))
        bad = good[:-2] + f"{(int(good[-2:], 16) + 1) % 256:02X}"
        proc.exec(f"uart_rx {bad}")
        proc.exec(f"uart_rx {tuya_frame(0x20)}")  # MCU asks for network status
        pump(proc)
        c = counters(device)
        assert c["rx_bad"] == 1
        kinds = [e["kind"] for e in log(device)]
        assert "RX_BAD" in kinds and "RX_CMD" in kinds
        assert "TX_CMD" in kinds  # our network status answer
    finally:
        proc.stop()


def test_log_keeps_the_newest_entries():
    proc, device = start()
    try:
        for i in range(40):
            mcu_report(proc, value_dp(DP_LEVEL_L1, 100 + i))
        entries = log(device)
        assert len(entries) == 30
        assert [e["value"] for e in entries] == list(range(110, 140))
        assert counters(device)["log_total"] == 40
    finally:
        proc.stop()


def test_control_write_clears():
    proc, device = start()
    try:
        mcu_report(proc, bool_dp(DP_ON_L1, 1))
        assert counters(device)["rx_frames"] == 1
        device.write_zigbee_attr(1, BASIC, ATTR_CONTROL, 1)
        assert counters(device)["rx_frames"] == 0
        assert log(device) == []
        assert int(device.read_zigbee_attr(1, BASIC, ATTR_CONTROL)) == 0
    finally:
        proc.stop()


TUYA_CMD_QUERY_ALL = 0x28


def _sent_cmds(proc: StubProc) -> list[int]:
    from test_dimmer_mcu import parse_frames
    return [cmd for cmd, _ in parse_frames(take_tx(proc))]


def test_query_all_sent_after_boot_without_mcu_contact():
    """The ZDMS16-2 MCU never asks for the network status on its own, so the
    module has to start the conversation: query-all ~2 s after boot."""
    with StubProc(device_config=ZDMS16_2) as proc:
        device = Device(proc)
        pump(proc)
        assert TUYA_CMD_QUERY_ALL not in _sent_cmds(proc)
        device.step_time(2100)
        pump(proc)
        assert TUYA_CMD_QUERY_ALL in _sent_cmds(proc)
        device.step_time(10000)
        pump(proc)
        assert TUYA_CMD_QUERY_ALL not in _sent_cmds(proc)  # once only


def test_control_2_sends_query_all():
    proc, device = start()
    try:
        device.step_time(2100)  # first-contact dump out of the way
        pump(proc)
        take_tx(proc)
        device.write_zigbee_attr(1, BASIC, ATTR_CONTROL, 2)
        pump(proc)
        assert TUYA_CMD_QUERY_ALL in _sent_cmds(proc)
        assert int(device.read_zigbee_attr(1, BASIC, ATTR_CONTROL)) == 0
    finally:
        proc.stop()
