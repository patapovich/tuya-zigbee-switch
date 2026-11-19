"""The ZDMS16-2's Z2M / Home Assistant face: channels l1 / l2 (as the stock definition of
zigbee-herdsman-converters names them, so a module moved from the stock firmware keeps the
light's and the switch type's entities), the module's own settings on the device, the times of
Open Dimmer in seconds (the attributes stay in ms), and labels that say what a setting is."""

import json
import re

import pytest

from generated_converter import custom_converter_js, run_node
from test_converter_generator import generate, mutate
from test_converter_own_mcu import ZDMS

SET = ZDMS + """
const ver = (v) => { const d = h.device(); d.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v; return d; };
const dev = ver("2.2.0 own");
// As Zigbee2MQTT picks it: the key, and the endpoint when the converter names its endpoints.
const tz = (key, ep) => def.extend.filter((x) => !x.fake).flatMap((x) => x.toZigbee ?? [])
    .find((t) => t.key.includes(key) && (!t.endpoints || t.endpoints.includes(`l${ep}`)));
const set = async (key, value, ep = 1) => {
    h.log.length = 0;
    try {
        const r = await tz(key, ep).convertSet(dev.getEndpoint(ep), key, value, {device: dev, mapped: model, options: {}});
        return [h.log.filter((l) => l[0] === "write").map((l) => l[3]), r?.state ?? null];
    } catch (error) {
        return error.message;
    }
};
const exposes = def.extend.filter((x) => !x.fake).flatMap((x) => x.exposes ?? [])
    .flatMap((x) => (typeof x === "function" ? x(dev, {}) : [x]));
const pick = (name, ep) => exposes.find((x) => x.name === name && x.endpoint === ep);
"""


def test_the_channels_are_l1_and_l2_and_the_actions_follow():
    out = json.loads(run_node(SET + """
console.log(JSON.stringify({
    endpoints: h.fakeExtends(def).find((x) => x.fake === "deviceEndpoints").args,
    actions: pick("action", undefined).values,
}));
"""))
    assert out["endpoints"] == {"endpoints": {"l1": 1, "l2": 2}}
    assert out["actions"] == [f"{k}_{ch}" for ch in ("l1", "l2")
                              for k in ("single", "double", "triple", "hold", "release", "press")]


def test_the_modules_own_settings_are_on_the_device():
    """mcu_version, multi_press_reset_count and device_config(_ext) are the module's, not a
    channel's: no endpoint (as mcu_firmware)."""
    out = json.loads(run_node(SET + """
console.log(JSON.stringify(h.fakeExtends(def)
    .filter((x) => ["mcu_version", "multi_press_reset_count", "device_config", "device_config_ext"].includes(x.args?.name))
    .map((x) => [x.args.name, x.args.endpointName ?? x.args.endpointNames ?? null])));
"""))
    assert out == [["mcu_version", None], ["multi_press_reset_count", None],
                   ["device_config", None], ["device_config_ext", None]]


def test_other_boards_keep_their_settings_on_the_first_endpoint():
    """Only dimmer boards moved: every other board's device_config stays on its first endpoint."""
    out = json.loads(run_node("""
console.log(JSON.stringify(definitions.filter((d) => d.model !== "ZDMS16-2").map((d) => {
    const ext = h.fakeExtends(d);
    const first = Object.keys(ext.find((x) => x.fake === "deviceEndpoints").args.endpoints)[0];
    return [first, ext.find((x) => x.args?.name === "device_config")?.args.endpointName];
})));
"""))
    assert len(out) == 212
    assert all(first == endpoint for first, endpoint in out), [x for x in out if x[0] != x[1]][:5]


def test_mcu_version_comes_from_endpoint_1_only():
    out = json.loads(run_node(ZDMS + """
const dev = h.device();
const run = (ep) => def.extend.flatMap((x) => x.fromZigbee ?? []).filter((f) => f.cluster === "genBasic")
    .map((f) => f.convert(model, {device: dev, endpoint: {ID: ep}, data: {[0xff12]: "1.0.7"}}, () => {}, {}, {device: dev}))
    .filter((r) => r && "mcu_version" in r);
console.log(JSON.stringify([run(1), run(2)]));
"""))
    assert out == [[{"mcu_version": "1.0.7"}], []]


def test_the_times_are_shown_in_seconds():
    out = json.loads(run_node(SET + """
const brief = (x) => ({min: x.value_min, max: x.value_max, step: x.value_step, unit: x.unit});
console.log(JSON.stringify(Object.fromEntries(
    ["fade_on_time", "fade_off_time", "hold_time", "start_boost", "brightness_fade_time", "countdown"]
        .map((n) => [n, brief(pick(n, "l2"))]))));
"""))
    assert out == {
        "fade_on_time": {"min": 0, "max": 10, "step": 0.05, "unit": "s"},
        "fade_off_time": {"min": 0, "max": 10, "step": 0.05, "unit": "s"},
        "hold_time": {"min": 0.1, "max": 10, "step": 0.05, "unit": "s"},
        "start_boost": {"min": 0, "max": 0.75, "step": 0.05, "unit": "s"},
        "brightness_fade_time": {"min": 0, "max": 60, "step": 0.1, "unit": "s"},
        "countdown": {"min": 0, "max": 86400, "unit": "s"},
    }


def test_a_time_in_seconds_goes_out_in_ms_and_comes_back_in_seconds():
    out = json.loads(run_node(SET + """
(async () => console.log(JSON.stringify({
    writes: [await set("fade_on_time", 0.95), await set("fade_off_time", "2.3", 2), await set("hold_time", 3.3004),
             await set("fade_on_time", 0), await set("hold_time", 10)],
    bad: [await set("fade_on_time", 10.01), await set("hold_time", 0.05), await set("fade_off_time", ""),
          await set("fade_off_time", "soon"), await set("hold_time", null)],
    reports: fz(dev, 1, {[0xff32]: 950, [0xff35]: 3300, [0xff37]: 0}),
    stock: fz(ver("1.0.7"), 1, {[0xff32]: 950}),
})))();
"""))
    hex_ = lambda a: str(a)
    assert out["writes"] == [
        [[{hex_(0xFF32): {"value": 950, "type": 0x23}}], {"fade_on_time": 0.95}],
        [[{hex_(0xFF38): {"value": 2300, "type": 0x23}}], {"fade_off_time": 2.3}],     # l2's own attribute
        [[{hex_(0xFF35): {"value": 3300, "type": 0x23}}], {"hold_time": 3.3}],         # rounded to the ms
        [[{hex_(0xFF32): {"value": 0, "type": 0x23}}], {"fade_on_time": 0}],
        [[{hex_(0xFF35): {"value": 10000, "type": 0x23}}], {"hold_time": 10}],
    ]
    assert out["bad"][0] == "fade_on_time: 0..10 s"
    assert out["bad"][1] == "hold_time: 0.1..10 s"
    assert all(m.startswith("fade_off_time: 0..10 s") for m in out["bad"][2:4]), out["bad"]
    assert out["bad"][4] == "hold_time: 0.1..10 s"
    assert {"fade_on_time_l1": 0.95} in out["reports"] and {"hold_time_l1": 3.3} in out["reports"]
    assert {"fade_on_time_l2": 0} in out["reports"]
    assert out["stock"] == []


def test_a_value_without_a_divisor_still_takes_integers_only():
    out = json.loads(run_node(SET + """
(async () => console.log(JSON.stringify([await set("fade_off_slow_zone", 12), await set("fade_off_slow_zone", 12.5),
                                         await set("hold_slow_zone", 101)])))();
"""))
    assert out[0] == [[{str(0xFF34): {"value": 12, "type": 0x23}}], {"fade_off_slow_zone": 12}]
    assert out[1] == "fade_off_slow_zone: an integer 0..100"
    assert out[2] == "hold_slow_zone: an integer 0..100"


def test_the_labels_say_what_a_setting_is():
    out = json.loads(run_node(SET + """
const label = (n, ep) => pick(n, ep)?.label ?? null;
console.log(JSON.stringify({
    switchType: h.fakeExtends(def).filter((x) => x.args?.name === "switch_type").map((x) => [x.args.label, x.args.description]),
    exposes: Object.fromEntries([["switch_mode", "l1"], ["fast_press", "l2"], ["on_level_local", "l1"],
        ["current_level_startup", "l2"], ["mcu_uart_rx_losses", undefined], ["mcu_settings_ring_moved", undefined]]
        .map(([n, ep]) => [n, label(n, ep)])),
    calibration: exposes.filter((x) => x.name === "mcu_temperature_calibration").length,
}));
"""))
    assert [l for l, _ in out["switchType"]] == ["Wall switch type", "Wall switch type"]
    for _, description in out["switchType"]:
        for word in ("toggle", "state", "momentary", "push button"):
            assert word in description
    assert out["exposes"] == {
        "switch_mode": "Wall switch mode",
        "fast_press": "Wall switch fast press",
        "on_level_local": "On level (wall switch)",
        "current_level_startup": "Power-on level",
        "mcu_uart_rx_losses": "MCU UART receive losses",
        "mcu_settings_ring_moved": "MCU settings relocated",
    }
    assert out["calibration"] == 0


def test_on_level_offers_previous_as_a_preset():
    """numeric() is a stand-in in the harness: the generated text adds the preset to its exposes."""
    block = custom_converter_js()
    body = block[block.index("dimmerOnLevel:"):block.index("dimmerSwitchType:")]
    assert 'withPreset("previous", 255' in body


DIVISOR_BAD = {
    "divisor zero": (lambda dps: dps[2].update(divisor=0), "divisor must be a number above 0"),
    "step not a number": (lambda dps: dps[2].update(step="fast"), "step must be a number above 0"),
    "divisor on an enum": (lambda dps: dps[0].update(divisor=10), "divisor only for a value (type V) with dimmer or min_mcu"),
}


@pytest.mark.parametrize("case", list(DIVISOR_BAD))
def test_generator_refuses_a_bad_divisor_or_step(tmp_path, case):
    change, message = DIVISOR_BAD[case]
    result = generate(tmp_path, mutate(change))
    assert result.returncode != 0
    assert message in result.stderr


def test_the_device_db_times_are_in_ms_shown_in_seconds():
    js = custom_converter_js()
    found = re.findall(r'ownMcuNumeric\(\{\s*name: "(\w+)",[^}]*?unit: "(\w+|%)",[^}]*?\}', js)
    units = {name: unit for name, unit in found}
    assert units == {"fade_on_time": "s", "fade_off_time": "s", "hold_time": "s",
                     "fade_off_slow_zone": "%", "hold_slow_zone": "%"}
    assert js.count("divisor: 1000,") == 2 * 2 * 3      # 2 definitions, 2 channels, 3 times
