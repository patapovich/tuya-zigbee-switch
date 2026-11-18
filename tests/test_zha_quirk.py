"""The generated ZHA quirk: valid Python, and the dimmer switch types match the
firmware (and the Z2M converter): 0 toggle, 1 state, 2 momentary."""

import ast
import re
import subprocess
import sys

from generated_converter import ROOT, custom_converter_js


def quirk() -> str:
    return subprocess.run(
        [sys.executable, "helper_scripts/make_zha_quirk.py", "device_db.yaml"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def enum_members(tree: ast.Module, name: str) -> dict[str, int]:
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    return {
        n.targets[0].id: n.value.value
        for n in cls.body
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
    }


def test_dimmer_switch_types_are_the_firmware_ones():
    source = quirk()
    tree = ast.parse(source)
    assert enum_members(tree, "DimmerSwitchType") == {"Toggle": 0, "State": 1, "Momentary": 2}

    js = custom_converter_js()
    z2m = re.search(r"dimmerSwitchType: \(name, endpointName\) => \{\s*const lookup = \{([^}]*)\}", js).group(1)
    assert {k.lower(): v for k, v in enum_members(tree, "DimmerSwitchType").items()} == {
        k: int(v) for k, v in re.findall(r"(\w+): (\d+)", z2m)
    }

    cluster = source[source.index("class CustomOnOffSwitchCfgCluster") :]
    assert "type=DimmerSwitchType" in cluster[: cluster.index("'''")]
    dimmer_enum = re.search(r"\.enum\(\s*CustomOnOffSwitchCfgCluster\.AttributeDefs\.switch_type\.name,\s*(\w+),", source)
    assert dimmer_enum.group(1) == "DimmerSwitchType"
