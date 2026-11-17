"""The generated Z2M converter's own-MCU-firmware settings for the ZDMS16-2
(dimming curve per dimmer, shown per the device option own_mcu_settings)."""

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
