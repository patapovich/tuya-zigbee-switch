"""The Z2M converter generator: its output must be valid JS, and it must refuse
a device database entry that would give invalid JS or exposes the firmware
cannot back. Zigbee2MQTT refuses a converter file that does not load, which
drops every model in it, and Home Assistant then deletes their entities."""

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from generated_converter import ROOT, custom_converter_file, custom_converter_js

sys.path.insert(0, str(ROOT / "helper_scripts"))
import make_z2m_custom_converters as generator  # noqa: E402

BOARD = "MODULE_AVATTO_ZDMS16_2"


@pytest.mark.parametrize("z2m_v1", [False, True])
def test_generated_converter_is_valid_js(z2m_v1):
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    result = subprocess.run(["node", "--check", custom_converter_file(z2m_v1)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def generate(tmp_path: Path, board: dict) -> subprocess.CompletedProcess:
    db = tmp_path / "device_db.yaml"
    db.write_text(yaml.safe_dump({BOARD: board}, allow_unicode=True))
    return subprocess.run(
        [sys.executable, "helper_scripts/make_z2m_custom_converters.py", str(db)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def zdms16_2() -> dict:
    return copy.deepcopy(yaml.safe_load((ROOT / "device_db.yaml").read_text())[BOARD])


def mutate(change):
    board = zdms16_2()
    change(board["dp_attributes"])
    return board


BAD = {
    "value without max": (lambda dps: dps[2].pop("max"), "a value needs min and max"),
    "value min above max": (lambda dps: dps[2].update(min=20, max=10), "min 20 > max 10"),
    "enum without values": (lambda dps: dps[0].pop("values"), "an enum needs values"),
    "enum label not a string": (lambda dps: dps[0]["values"].update({False: 9}), "enum label False is not a string"),
    "enum value out of range": (lambda dps: dps[0]["values"].update(linear=256), "is not 0..255"),
    "duplicate dp": (lambda dps: dps[1].update(dp="65"), "dp 65 also in dp_attributes[0]"),
    "duplicate attr": (lambda dps: dps[1].update(attr="30"), "attr 30 also in dp_attributes[0]"),
    "reserved attr": (lambda dps: dps[1].update(attr="12"), "Basic 0xff12, which the firmware uses itself"),
    "diag attr": (lambda dps: dps[1].update(attr="43"), "Basic 0xff43, which the firmware uses itself"),
    "over the firmware cap": (
        lambda dps: dps.append(dict(dps[-1], dp="A0", attr="3c", dimmer=1, name="extra")),
        "13 dp attributes, the firmware maps at most 12",
    ),
    "missing label": (lambda dps: dps[3].pop("label"), "needs a label"),
    "name not an identifier": (lambda dps: dps[3].update(name='fade"on'), "name must be lower case"),
    "same expose twice": (lambda dps: dps[1].update(dimmer=1), "same name and endpoint as dp_attributes[0]"),
    "dimmer beyond the board": (lambda dps: dps[1].update(dimmer=3), "dimmer 3 but the board has 2"),
}


@pytest.mark.parametrize("case", list(BAD))
def test_generator_refuses_a_bad_dp_attribute(tmp_path, case):
    change, message = BAD[case]
    result = generate(tmp_path, mutate(change))
    assert result.returncode != 0
    assert message in result.stderr
    assert result.stdout == ""


def test_the_cap_is_the_firmware_one():
    header = (ROOT / "src/zigbee/dp_attr.h").read_text()
    assert f"#define MAX_DP_ATTRS    {generator.MAX_DP_ATTRS}" in header
    assert {0xFF00, 0xFF01, 0xFF02, 0xFF11, 0xFF12, 0xFF16, 0xFF40, 0xFF45, 0xFF4F} <= generator.RESERVED_BASIC_ATTRS
    assert not {0xFF30, 0xFF3B, 0xFF46} & generator.RESERVED_BASIC_ATTRS


def test_labels_and_enum_labels_are_quoted_for_js(tmp_path):
    """A quote or a backslash in a label or an enum label used to end the JS string."""
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    label = 'Curve "soft" or \'hard\' \\ </script> & more'
    board = mutate(lambda dps: (dps[0].update(label=label), dps[0]["values"].update({'it\'s "x"': 6}),
                                dps[2].update(unit='m"s')))
    result = generate(tmp_path, board)
    assert result.returncode == 0, result.stderr
    out = tmp_path / "out.js"
    out.write_text(result.stdout)
    assert subprocess.run(["node", "--check", str(out)], capture_output=True).returncode == 0
    # The strings arrive unchanged.
    script = (
        "const src = require('fs').readFileSync(process.argv[1], 'utf8');"
        "const i = src.indexOf('romasku.ownMcuEnum({'); const j = src.indexOf('}),', i);"
        "const arg = eval('(' + src.slice(i + 'romasku.ownMcuEnum('.length, j + 1) + ')');"
        "const k = src.indexOf('romasku.ownMcuNumeric({'); const l = src.indexOf('}),', k);"
        "const num = eval('(' + src.slice(k + 'romasku.ownMcuNumeric('.length, l + 1) + ')');"
        "console.log(JSON.stringify([arg.description, Object.keys(arg.lookup), num.unit]));"
    )
    shown = json.loads(subprocess.run(["node", "-e", script, str(out)], capture_output=True, text=True, check=True).stdout)
    assert shown[0] == label
    assert 'it\'s "x"' in shown[1]
    assert shown[2] == 'm"s'


def test_a_missing_value_fails_the_render_but_may_be_tested():
    """jinja2's default prints a missing value as nothing (valueMin: ,);
    StrictUndefined also refuses {% if missing %}, which the template uses."""
    from jinja2.exceptions import UndefinedError

    assert generator.env.from_string("{% if d.unit %}x{% endif %}{{ d.min_mcu or 0 }}").render(d={}) == "0"
    with pytest.raises(UndefinedError):
        generator.env.from_string("valueMin: {{ d.min }},").render(d={})


def test_database_converter_renders():
    """The real database passes the checks (the other tests start from it)."""
    assert "const definitions = [" in custom_converter_js()
