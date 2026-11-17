"""The generated Z2M converter's own-MCU-firmware settings for the ZDMS16-2
(dimming curve per dimmer, shown per the device option own_mcu_settings)."""

import json
import re
import shutil
import subprocess

import pytest

from generated_converter import custom_converter_js


def zdms16_block() -> str:
    js = custom_converter_js()
    start = js.index('model: "ZDMS16-2"')
    return js[start : js.index("configure:", start)]


def test_curves_are_on_each_dimmer_endpoint_with_their_own_attribute():
    block = zdms16_block()
    curves = re.findall(
        r'ownMcuEnum\(\{\s*name: "dimming_curve",\s*endpointName: "(\w+)",\s*attrId: (0x\w+)', block
    )
    assert curves == [("dimmer_left", "0xff30"), ("dimmer_right", "0xff31")]
    assert "romasku.ownMcuOption()" in block
    assert "romasku.mcuVersionMeta()" in block


def test_own_firmware_is_told_by_the_suffix_only():
    """Run the converter's version check in node: a vendor 2.x is not ours."""
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    js = custom_converter_js()
    fn = js[js.index("const ownMcuMajorOf") : js.index("};\n", js.index("const ownMcuMajorOf")) + 3]
    script = fn + """
const dev = (cached, meta) => ({ meta: { mcuVersion: meta },
  getEndpoint: () => ({ clusters: { genBasic: { attributes: { 65298: cached } } } }) });
console.log(JSON.stringify([
  ownMcuMajorOf(dev("2.0.3 own", undefined)),
  ownMcuMajorOf(dev("2.0.3", undefined)),
  ownMcuMajorOf(dev("", "2.0.3 own")),
  ownMcuMajorOf(dev("1.0.7", "2.0.3 own")),
  ownMcuMajorOf(undefined),
]));
"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[2,null,2,null,null]"


def test_power_on_per_channel_and_gestures_follow_the_own_mcu():
    """Run the ZDMS16-2 power-on and gesture extends in node with stand-in exposes."""
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    js = custom_converter_js()
    helpers = js[js.index("const ownMcuMajorOf") : js.index("const romasku")]
    start = js.index("    dimmerPowerOnPerChannel:")
    extends = js[start : js.index("    dimmerPowerOnBehavior:", start)]
    script = """
const fake = (name, values) => { const x = { name, values, endpoint: undefined,
  withDescription() { return x; }, withCategory() { return x; },
  withEndpoint(ep) { x.endpoint = ep; return x; } }; return x; };
const e = { enum: (n, a, v) => fake(n, v), action: (v) => fake("action", v) };
const ea = { ALL: 7, SET: 2 };
""" + helpers + "const R = {\n" + extends + "};\n" + """
const dev = (v) => ({ meta: {}, getEndpoint: () => ({ clusters: { genBasic: { attributes: { 65298: v } } } }) });
const names = ["dimmer_left", "dimmer_right"];
const po = R.dimmerPowerOnPerChannel(names), ge = R.dimmerGestures(names);
const pc = names.map((n) => R.dimmerPowerOnChannel(n));
const shownAll = (d, o) => [...pc.flatMap((x) => shown(x, d, o)), ...shown(po, d, o)];
const shown = (ext, d, o) => ext.exposes[0](d, o).map((x) => `${x.name}${x.endpoint ? "_" + x.endpoint : ""}`);
const model = { endpoint: () => ({ dimmer_left: 1, dimmer_right: 2 }) };
const fz = (ext, ep, data) => ext.fromZigbee[0].convert(model, { endpoint: { ID: ep }, data, device: {} });
console.log(JSON.stringify({
  own: [shownAll(dev("2.1.1 own"), {}), shown(ge, dev("2.1.1 own"), {})],
  stock: [shownAll(dev("1.0.7"), {}), shown(ge, dev("1.0.7"), {})],
  hide: shownAll(dev("2.1.1 own"), { own_mcu_settings: "hide" }),
  show: shown(ge, dev("1.0.7"), { own_mcu_settings: "show" }),
  gestures: [fz(ge, 2, { presentValue: 0x32 }), fz(ge, 1, { presentValue: 0x15 }),
             fz(ge, 1, { presentValue: 0x10 }), fz(ge, 3, { presentValue: 0x11 })],
  power: [fz(po, 1, { startUpOnOff: 255 }), fz(po, 2, { startUpOnOff: 0 }), fz(po, 2, { onOff: 1 })],
}));
"""
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out["own"] == [["power_on_behavior_dimmer_left", "power_on_behavior_dimmer_right"], ["action"]]
    assert out["stock"] == [["power_on_behavior"], []]
    assert out["hide"] == ["power_on_behavior"]
    assert out["show"] == ["action"]
    assert out["gestures"] == [{"action": "double_dimmer_right"}, {"action": "release_dimmer_left"}, None, None]
    assert out["power"] == [
        {"power_on_behavior_dimmer_left": "previous", "power_on_behavior": "previous"},
        {"power_on_behavior_dimmer_right": "off"},
        None,
    ]


def test_fade_and_hold_settings_are_per_dimmer_with_their_own_attributes():
    block = zdms16_block()
    found = re.findall(
        r'ownMcuNumeric\(\{\s*name: "(\w+)",\s*endpointName: "(\w+)",\s*attrId: (0x\w+)', block
    )
    names = ["fade_on_time", "fade_off_time", "soft_dim_end", "hold_time", "hold_dim_end"]
    assert found == [(n, "dimmer_left", f"0xff{0x32 + k:02x}") for k, n in enumerate(names)] + [
        (n, "dimmer_right", f"0xff{0x37 + k:02x}") for k, n in enumerate(names)
    ]
    assert block.count("minMcu: 2,") == 12          # 2 curves + 10 fade and hold settings
