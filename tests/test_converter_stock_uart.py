"""The dimmer transitions end to end for a stock (factory) MCU: what the converter sends for a
message (light()'s converters as zigbee-herdsman-converters 26.125.0 has them, ported in
converter_harness.js) replayed on the stub module with a stock MCU, and the UART frames it
gets. The transition option and the "none" time (0xFFFF, which the converter sends while the
MCU version is not known) must not change them: a plain on with the option set gets the
frames of a plain on (on, no level), as before the option."""
import json

import pytest
from client import StubProc
from conftest import Device
from generated_converter import run_node
from test_converter_own_mcu import ZDMS
from test_dimmer_mcu import ZDMS16_2, ZDMS16_2_DP_CONFIG
from tuya_mcu import StockMcu, pump
from zcl_consts import (
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CMD_LEVEL_STEP,
    ZCL_CMD_ONOFF_OFF,
    ZCL_CMD_ONOFF_ON,
    ZCL_CMD_ONOFF_TOGGLE,
)

ZCL_CMD_LEVEL_STEP_WITH_ON_OFF = 0x06

# A sequence of (message, light state as Zigbee2MQTT has it) through the definition's first
# converter for the key, the store (zhc's globalStore) kept along it as in Zigbee2MQTT.
SEQ = ZDMS + """
const tzFor = (key) => def.extend.flatMap((x) => x.toZigbee ?? [])
    .find((t) => t.key?.includes(key) && (!t.endpoints || t.endpoints.includes("dimmer_left")));
const run = async (version, steps, options = {}) => {
    h.globalStore.clear();
    const dev = h.device();
    if (version !== undefined) dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = version;
    const out = [];
    for (const [message, state] of steps) {
        const key = Object.keys(message).find((k) => k !== "transition");
        h.log.length = 0;
        await tzFor(key).convertSet(dev.getEndpoint(1), key, message[key],
            {device: dev, message: {...message}, options, state, endpoint_name: "dimmer_left", mapped: model});
        out.push(h.log.filter((l) => l[0] === "command").map((l) => [l[2], l[3], l[4]]));
    }
    return out;
};
"""

LIT = {"state": "ON", "brightness": 100}
DARK = {"state": "OFF", "brightness": 100}


def converter(version, steps, options=None):
    return json.loads(run_node(SEQ + f"""
(async () => console.log(JSON.stringify(await run({json.dumps(version)}, {json.dumps(steps)},
                                                  {json.dumps(options or {})}))))();
"""))


def zcl(cluster: str, command: str, p: dict) -> tuple[int, int, bytes | None]:
    """A converter command as the ZCL frame the module gets."""
    if cluster == "genOnOff":
        return ZCL_CLUSTER_ON_OFF, {"on": ZCL_CMD_ONOFF_ON, "off": ZCL_CMD_ONOFF_OFF,
                                    "toggle": ZCL_CMD_ONOFF_TOGGLE}[command], None
    tail = p["transtime"].to_bytes(2, "little") + bytes([p["optionsMask"], p["optionsOverride"]])
    if command in ("moveToLevel", "moveToLevelWithOnOff"):
        cmd = ZCL_CMD_LEVEL_MOVE_TO_LEVEL if command == "moveToLevel" else ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF
        return ZCL_CLUSTER_LEVEL_CONTROL, cmd, bytes([p["level"]]) + tail
    cmd = ZCL_CMD_LEVEL_STEP if command == "step" else ZCL_CMD_LEVEL_STEP_WITH_ON_OFF
    return ZCL_CLUSTER_LEVEL_CONTROL, cmd, bytes([p["stepmode"], p["stepsize"]]) + tail


def uart(commands: list) -> list:
    """The UART frames a stock MCU gets for the commands (per message), from a lit start."""
    proc = StubProc(device_config=ZDMS16_2, dp_config=ZDMS16_2_DP_CONFIG).start()
    try:
        device = Device(proc)
        pump(proc)
        mcu = StockMcu(proc, device)
        mcu.poll()
        mcu.run(5000)
        device.call_zigbee_cmd(1, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
                               bytes([100, 0, 0]))
        mcu.run(2500)
        out = []
        for message in commands:
            start = len(mcu.trace)
            for cluster, command, payload in message:
                device.call_zigbee_cmd(1, *zcl(cluster, command, payload))
                mcu.run(2500)
            out.append([(cmd, data.hex()) for _, cmd, data in mcu.trace[start:]])
        return out
    finally:
        proc.stop()


def test_a_plain_on_with_the_transition_option_gets_the_frames_of_a_plain_on():
    """Off with the option is a level move to 0 (zhc's off with a time, as before); the on
    after it, and a plain on, a toggle of a dark light: genOnOff, so the stock MCU gets on
    and no level (zhc alone would send its remembered level: on, then a level)."""
    steps = [[{"state": "OFF"}, LIT], [{"state": "ON"}, DARK], [{"state": "OFF"}, LIT],
             [{"state": "TOGGLE"}, DARK], [{"state": "ON"}, LIT]]
    plain = converter("1.0.7", steps)
    timed = converter("1.0.7", steps, {"transition": 1.2})
    assert [[c[:2] for c in m] for m in plain] == [[["genOnOff", "off"]], [["genOnOff", "on"]],
                                                   [["genOnOff", "off"]], [["genOnOff", "toggle"]],
                                                   [["genOnOff", "on"]]]
    assert [[c[:2] for c in m] for m in timed] == [[["genLevelCtrl", "moveToLevelWithOnOff"]],
                                                   [["genOnOff", "on"]],
                                                   [["genLevelCtrl", "moveToLevelWithOnOff"]],
                                                   [["genOnOff", "toggle"]], [["genOnOff", "on"]]]
    assert timed[0][0][2]["transtime"] == 12 and timed[0][0][2]["level"] == 0
    assert uart(timed) == uart(plain)
    assert all(uart(timed))                                     # each one sent something


def test_a_level_change_with_the_option_gets_the_frames_without_it():
    """A brightness with the option: zhc's level move with the time; the stock MCU's frames do
    not depend on the time."""
    steps = [[{"brightness": 200}, LIT], [{"state": "ON", "brightness": 50}, LIT],
             [{"brightness_step": 30}, LIT]]
    plain = converter("1.0.7", steps)
    timed = converter("1.0.7", steps, {"transition": 2})
    assert [c[2]["transtime"] for m in plain for c in m] == [0, 0, 0]
    assert [c[2]["transtime"] for m in timed for c in m] == [20, 20, 20]
    assert uart(timed) == uart(plain)


@pytest.mark.parametrize("version", [None, "", "0.0.0"])
def test_none_goes_out_while_the_mcu_version_is_not_known_and_a_stock_mcu_does_not_mind(version):
    """No version kept yet (just paired, the read not answered): an Open Dimmer must not get 0
    (at once) for "no transition", so 0xFFFF goes out; a stock MCU gets the frames of 0."""
    steps = [[{"brightness": 200}, LIT], [{"state": "ON", "brightness": 50}, LIT],
             [{"brightness_step": -30}, LIT], [{"brightness_step_onoff": 20}, LIT],
             [{"state": "OFF", "brightness": 0}, LIT], [{"state": "ON"}, DARK]]
    unknown = converter(version, steps)
    stock = converter("1.0.7", steps)
    assert [c[2].get("transtime") for m in unknown for c in m] == [0xFFFF] * 5 + [None]
    assert [c[2].get("transtime") for m in stock for c in m] == [0] * 5 + [None]
    assert uart(unknown) == uart(stock)
