"""DPs of the own PY32 firmware that the module uses only once the MCU has
answered the identity query: per-channel power-on (103/104 instead of the
shared 14) and the wall switch gestures (105/106 -> genMultistateInput)."""
from own_mcu import own_mcu_contact
from test_dimmer_mcu import ack_writes, start
from tuya_mcu import TUYA_CMD_REPORT_PASSIVE, enum_dp, mcu_report, pump, take_tx

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
    """First contact in the real order, with the real dump (power-on of
    channel 1 'previous', channel 2 'on'), then nothing left to send."""
    own_mcu_contact(proc, device, power_on=(2, 1))
    take_tx(proc)


def test_own_mcu_contact_reads_the_power_on_per_channel():
    proc, device = start()
    try:
        own(proc, device)
        assert (startup(device, 1), startup(device, 2)) == (STARTUP_PREVIOUS, STARTUP_ON)
    finally:
        proc.stop()


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


def test_fade_and_hold_settings_map_to_their_basic_attributes():
    """The board's full dp config (12 entries, the limit): a setting written in
    Zigbee goes to its DP, and the MCU's report lands in its attribute."""
    from tuya_mcu import value_dp
    proc, device = start()
    try:
        mcu_report(proc, value_dp(0x9B, 1800), cmd=TUYA_CMD_REPORT_PASSIVE)   # ch2 fade on 1800 ms
        assert int(device.read_zigbee_attr(1, 0x0000, 0xFF37)) == 1800
        take_tx(proc)
        device.write_zigbee_attr(1, 0x0000, 0xFF35, 2500)                      # ch1 hold 2500 ms
        assert (0x99, 2500) in ack_writes(proc, device)
    finally:
        proc.stop()
