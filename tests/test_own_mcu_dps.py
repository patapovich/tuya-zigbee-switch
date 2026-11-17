"""DPs of the own PY32 firmware that the module uses only once the MCU has
answered the identity query: per-channel power-on (103/104 instead of the
shared 14) and the wall switch gestures (105/106 -> genMultistateInput)."""
from test_dimmer_mcu import ack_writes, start
from test_mcu_ota import VERSION, contact_as_own
from tuya_mcu import TUYA_CMD_REPORT_PASSIVE, dp_writes, enum_dp, mcu_report, pump, take_tx

ZCL_CLUSTER_ON_OFF = 0x0006
ZCL_ATTR_START_UP_ONOFF = 0x4003
ZCL_CLUSTER_MULTISTATE_INPUT = 0x0012
ZCL_ATTR_PRESENT_VALUE = 0x0055
STARTUP_OFF, STARTUP_ON, STARTUP_PREVIOUS = 0x00, 0x01, 0xFF
DP_POWER_ON = 14
DP_POWER_ON_L1, DP_POWER_ON_L2 = 103, 104
DP_ACTION_L1, DP_ACTION_L2 = 105, 106


def startup(device, ep: int) -> int:
    return int(device.read_zigbee_attr(ep, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_START_UP_ONOFF))


def gesture(device, ep: int) -> int:
    return int(device.read_zigbee_attr(ep, ZCL_CLUSTER_MULTISTATE_INPUT, ZCL_ATTR_PRESENT_VALUE))


def written_after_startup_write(proc, device, ep: int, value: int) -> list[tuple[int, int]]:
    take_tx(proc)
    device.write_zigbee_attr(ep, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_START_UP_ONOFF, value)
    return ack_writes(proc, device)


def own(proc, device):
    contact_as_own(proc, device, VERSION)
    take_tx(proc)


def test_own_mcu_power_on_is_written_per_channel():
    proc, device = start()
    try:
        own(proc, device)
        assert (DP_POWER_ON_L2, 1) in written_after_startup_write(proc, device, 2, STARTUP_ON)
        writes = written_after_startup_write(proc, device, 1, STARTUP_PREVIOUS)
        assert (DP_POWER_ON_L1, 2) in writes
        assert DP_POWER_ON not in [dp for dp, _ in writes]
    finally:
        proc.stop()


def test_own_mcu_power_on_reports_are_per_channel_and_dp14_is_left_out():
    proc, device = start()
    try:
        own(proc, device)
        # query-all order of the own firmware: DP 14 (channel 1's) first, then per channel
        mcu_report(proc, enum_dp(DP_POWER_ON, 0), cmd=TUYA_CMD_REPORT_PASSIVE)
        mcu_report(proc, enum_dp(DP_POWER_ON_L1, 0), cmd=TUYA_CMD_REPORT_PASSIVE)
        mcu_report(proc, enum_dp(DP_POWER_ON_L2, 1), cmd=TUYA_CMD_REPORT_PASSIVE)
        assert startup(device, 1) == STARTUP_OFF
        assert startup(device, 2) == STARTUP_ON
        mcu_report(proc, enum_dp(DP_POWER_ON, 2), cmd=TUYA_CMD_REPORT_PASSIVE)
        assert (startup(device, 1), startup(device, 2)) == (STARTUP_OFF, STARTUP_ON)
    finally:
        proc.stop()


def test_another_mcu_keeps_the_shared_power_on_dp():
    proc, device = start()
    try:
        writes = written_after_startup_write(proc, device, 2, STARTUP_ON)
        assert (DP_POWER_ON, 1) in writes
        assert DP_POWER_ON_L2 not in [dp for dp, _ in writes]
        mcu_report(proc, enum_dp(DP_POWER_ON, 0), cmd=TUYA_CMD_REPORT_PASSIVE)
        assert (startup(device, 1), startup(device, 2)) == (STARTUP_OFF, STARTUP_OFF)
        mcu_report(proc, enum_dp(DP_POWER_ON_L2, 1), cmd=TUYA_CMD_REPORT_PASSIVE)
        assert startup(device, 2) == STARTUP_OFF      # not ours to read
    finally:
        proc.stop()


def test_gestures_reach_the_dimmer_endpoint_and_each_one_is_a_change():
    proc, device = start()
    try:
        own(proc, device)
        assert gesture(device, 1) == 0
        mcu_report(proc, enum_dp(DP_ACTION_L1, 2))            # double
        first = gesture(device, 1)
        assert first & 0x0F == 2
        mcu_report(proc, enum_dp(DP_ACTION_L1, 2))            # double again
        second = gesture(device, 1)
        assert second & 0x0F == 2 and second != first
        mcu_report(proc, enum_dp(DP_ACTION_L2, 4))            # hold on channel 2
        assert gesture(device, 2) & 0x0F == 4
        assert gesture(device, 1) == second
        for _ in range(20):                                   # the sequence wraps, never 0
            mcu_report(proc, enum_dp(DP_ACTION_L1, 1))
            assert gesture(device, 1) & 0x0F == 1
        mcu_report(proc, enum_dp(DP_ACTION_L1, 9))            # not a gesture
        assert gesture(device, 1) & 0x0F == 1
    finally:
        proc.stop()


def test_gestures_of_another_mcu_are_ignored():
    proc, device = start()
    try:
        pump(proc)
        mcu_report(proc, enum_dp(DP_ACTION_L1, 2))
        assert gesture(device, 1) == 0
    finally:
        proc.stop()
