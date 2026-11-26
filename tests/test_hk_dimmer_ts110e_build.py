"""The HK tests again on a stub built with the TS110E's flags: DIMMER_ONLY (with it the scenes and
the wall switch's bindings) and HK_DIMMER, without SECONDARY_MCU_IMAGE (and without the stub's
MCU dump). The stub of the other tests is built with SECONDARY_MCU_IMAGE as well, so a path the
virtual MCU needs that sits in an Open Dimmer block of the image builds only (dimmer_cluster.c:
DIMMER_OD_PATHS, not SECONDARY_MCU_IMAGE) would pass there and be missing on the TS110E.

Left out: the tests that read the link's counters (Basic 0xff40, a part of the debug builds
only, not of a release), and test_hk_dimmer_trace.py (the debug builds' trace in the link's log).
The long transitions of test_hk_dimmer_long_transitions.py run here too, from a lit level and right
after a hold, at every time.

Also: what the TS110E's converter reads, writes, binds and reports is all in this build's ZCL
tables, and the module's own attributes in them are all on the converter's page (or said why
not)."""
import inspect
import json
import re
import subprocess
from pathlib import Path

import pytest
import test_factory_identity
import test_hk_dimmer
import test_hk_dimmer_long_transitions
import test_hk_dimmer_od_features
import test_hk_dimmer_od_paths
import test_hk_dimmer_od_switch

ROOT = Path(__file__).resolve().parent.parent
FLAGS = "EXTRA_CFLAGS=-DDIMMER_ONLY -USECONDARY_MCU_IMAGE -UMCU_FLASHER_DUMP"

# All of test_hk_dimmer.py (but for the two that read the debug counters), of
# test_hk_dimmer_od_paths.py, test_hk_dimmer_od_features.py (Open Dimmer 2.2.0's settings: their
# attributes, the Identify and Multistate clusters) and test_hk_dimmer_od_switch.py (2.2.8's
# lamp compensation and switch options among them).
DIAG = ("test_a_frame_the_uart_did_not_take_goes_again_as_it_was",
        "test_a_dp_the_virtual_mcu_does_not_keep_gets_no_answer")


def selected(module, words=None) -> list:
    """Its tests that take no arguments; not one marked xfail (a known limit: called here it
    would fail)."""
    return [f for name, f in inspect.getmembers(module, inspect.isfunction)
            if name.startswith("test_") and f.__module__ == module.__name__ and name not in DIAG
            and not inspect.signature(f).parameters
            and not any(m.name == "xfail" for m in getattr(f, "pytestmark", []))
            and (words is None or any(w in name for w in words))]


TESTS = (selected(test_hk_dimmer) + selected(test_hk_dimmer_od_paths) +
         selected(test_hk_dimmer_od_features) + selected(test_hk_dimmer_od_switch) +
         selected(test_hk_dimmer_long_transitions))
# test_factory_identity.py's TS110E ones (they take monkeypatch / tmp_path)
IDENTITY = [f for name, f in inspect.getmembers(test_factory_identity, inspect.isfunction)
            if name.startswith("test_hk_")]


@pytest.fixture(scope="module")
def ts110e_stub(tmp_path_factory) -> str:
    binary = tmp_path_factory.mktemp("ts110e") / "stub_device"
    subprocess.run(["make", "-s", "-C", str(ROOT / "src/stub"), "build", f"BINARY={binary}", FLAGS],
                   check=True, capture_output=True)
    return str(binary)


def test_the_selection_has_the_paths_of_this_build():
    names = {f.__name__ for f in TESTS}
    assert len(TESTS) > 150 and len(IDENTITY) >= 5
    assert "test_boot_sends_the_range_and_both_channels_twice" in names
    assert "test_remote_a_double_press_sends_full_and_a_triple_its_action" in names
    assert "test_a_hold_goes_to_the_bound_lights_as_a_move_and_its_end" in names
    assert "test_identify_blinks_the_channel_dark_and_full_then_it_is_as_it_was" in names
    assert "test_a_momentary_switchs_gestures_are_the_action" in names
    assert "test_lamp_compensation_lowers_the_output_after_a_dim_down_only" in names
    assert "test_a_state_switch_closed_at_the_start_turns_the_light_on" in names
    assert "test_a_time_over_600_s_is_capped_at_600_s" in names


@pytest.mark.parametrize("test", TESTS, ids=lambda f: f.__name__)
def test_on_the_ts110e_flags(ts110e_stub, monkeypatch, test):
    monkeypatch.setenv("STUB_DEVICE", ts110e_stub)
    test()


@pytest.mark.parametrize("tenths", test_hk_dimmer_long_transitions.TIMES)
def test_long_transitions_on_the_ts110e_flags(ts110e_stub, monkeypatch, tenths):
    monkeypatch.setenv("STUB_DEVICE", ts110e_stub)
    test_hk_dimmer_long_transitions.test_a_long_transition_from_a_lit_level_ends_at_its_time(tenths)
    test_hk_dimmer_long_transitions.test_a_long_transition_right_after_a_hold_fades_in_its_time(tenths)


@pytest.mark.parametrize("test", IDENTITY, ids=lambda f: f.__name__)
def test_factory_identity_on_the_ts110e_flags(ts110e_stub, monkeypatch, tmp_path, test):
    monkeypatch.setenv("STUB_DEVICE", ts110e_stub)
    args = {"monkeypatch": monkeypatch, "tmp_path": tmp_path}
    test(**{k: v for k, v in args.items() if k in inspect.signature(test).parameters})


def test_the_ts110e_build_fits_the_zcl_tables(ts110e_stub, monkeypatch, tmp_path):
    """Its own layout (the other stub's has Open Dimmer's custom curve attribute too): per dimmer
    endpoint Identify and Multistate Input (the gestures), the 2.2.0 settings' attributes and
    2.2.8's lamp compensation, long press from off and double tap; on Basic the custom curves, the
    slow zones, the NWK command security and the restart exception (0xff5c); within the Telink
    HAL's tables, with room left."""
    from test_hk_dimmer import TS110E, TS110E_DP_CONFIG
    from test_zcl_tables import counts
    monkeypatch.setenv("STUB_DEVICE", ts110e_stub)
    got = counts(tmp_path, TS110E, TS110E_DP_CONFIG)
    assert got == {"endpoints": (4, 8), "in_clusters": (17, 32), "out_clusters": (7, 32),
                   "zcl_clusters": (24, 32), "attributes": (108, 128)}


# ---- the converter against this build's ZCL tables ------------------------------------------

CLUSTERS = {"genBasic": 0x0000, "genIdentify": 0x0003, "genOnOff": 0x0006, "genOnOffSwitchCfg": 0x0007,
            "genLevelCtrl": 0x0008, "genMultistateInput": 0x0012, "lightingBallastCfg": 0x0301}
ATTRS = {("genOnOff", "onOff"): 0x0000, ("genOnOff", "startUpOnOff"): 0x4003,
         ("genLevelCtrl", "currentLevel"): 0x0000, ("genLevelCtrl", "onLevel"): 0x0011,
         ("genLevelCtrl", "options"): 0x000F, ("genLevelCtrl", "startUpCurrentLevel"): 0x4000,
         ("lightingBallastCfg", "minLevel"): 0x0010, ("lightingBallastCfg", "maxLevel"): 0x0011,
         ("genOnOffSwitchCfg", "switchType"): 0x0000, ("genMultistateInput", "presentValue"): 0x0055}

# What the converter of the TS110E reads (configure, every get), writes (zhc's extends: their
# cluster and attribute), binds and reports: [what, endpoint, cluster, attribute or null].
USES = """
const def = definitions.find((d) => d.extend && d.model === "TS110E_2gang_2");
const epOf = (name) => name === undefined ? 1 : ({l1: 1, l2: 2}[name] ?? name);
const seen = [];
for (const x of def.extend.filter((x) => x.fake && x.args?.cluster)) {
    const a = x.args;
    for (const ep of a.endpointNames ?? [a.endpointName]) {
        seen.push(["extend " + x.fake + " " + a.name, epOf(ep), a.cluster, a.attribute?.ID ?? a.attribute]);
    }
}
(async () => {
    const dev = h.device({versions: ["1.0.0"]});
    for (const t of def.extend.filter((x) => !x.fake).flatMap((x) => x.toZigbee ?? [])) {
        for (const ep of (t.endpoints ?? [undefined])) {
            if (!t.convertGet) continue;
            h.log.length = 0;
            await t.convertGet(dev.getEndpoint(epOf(ep)), t.key[0],
                               {device: dev, endpoint_name: ep, message: {}, options: {}, state: {}});
            for (const l of h.log) if (l[0] === "read") for (const a of l[3]) seen.push(["get " + t.key[0], l[1], l[2], a]);
        }
    }
    h.log.length = 0;
    await def.configure(dev, {ID: 1}, console);
    for (const l of h.log) {
        if (l[0] === "read") for (const a of l[3]) seen.push(["configure read", l[1], l[2], a]);
        if (l[0] === "bind") for (const c of l[2]) seen.push(["configure bind", l[1], c, null]);
        if (l[0] === "report" && Array.isArray(l[3])) {
            for (const r of l[3]) seen.push(["configure report", l[1], l[2], r.attribute?.ID ?? r.attribute]);
        }
        if (l[0] === "report" && !Array.isArray(l[3])) seen.push(["configure report", l[1], l[2], "onOff"]);
    }
    console.log(JSON.stringify(seen));
})();
"""


def listing(binary: str, cwd: Path) -> dict[tuple[int, int], set[int]]:
    """(endpoint, server cluster) -> its attributes, as this build registers them."""
    from test_hk_dimmer import TS110E, TS110E_DP_CONFIG
    cwd.mkdir(exist_ok=True)
    out = subprocess.run([binary, "--device-config", TS110E, "--dp-config", TS110E_DP_CONFIG, "--freeze-time"],
                         input="zcl_list_attrs\nq\n", capture_output=True, text=True, timeout=10, cwd=cwd,
                         check=True).stdout
    tables, key = {}, None
    for line in out[out.index("=== Zigbee Attributes ==="):].splitlines():
        if m := re.match(r"Endpoint (\d+):", line):
            ep = int(m[1])
        elif m := re.match(r"\s+Cluster 0x([0-9a-f]{4}) \((server|client)\)", line):
            key = (ep, int(m[1], 16)) if m[2] == "server" else None
            if key:
                tables[key] = set()
        elif (m := re.match(r"\s+Attr 0x([0-9a-f]{4})", line)) and key:
            tables[key].add(int(m[1], 16))
    return tables


def uses() -> list[tuple[str, int, int, int | None]]:
    from generated_converter import run_node
    out = []
    for what, ep, cluster, attr in json.loads(run_node(USES)):
        if isinstance(attr, str):
            attr = ATTRS[(cluster, attr)]
        out.append((what, ep, CLUSTERS[cluster], attr))
    return out


def test_everything_the_ts110e_converter_uses_is_in_the_ts110e_build(ts110e_stub, tmp_path):
    tables = listing(ts110e_stub, tmp_path / "list")
    used = uses()
    assert len(used) > 100
    missing = [(what, ep, hex(c), None if a is None else hex(a)) for what, ep, c, a in used
               if (ep, c) not in tables or (a is not None and a not in tables[(ep, c)])]
    assert missing == []


# The module's own attributes (0xff00 and up) of this build that the converter does not show.
NOT_SHOWN = {
    (1, 0x0000, 0xFF55): "the start-up's look at the factory NV (W4c), a diagnostic read by hand "
                         "(docs/ts110e.md)",
    (1, 0x0000, 0xFF11): "dp_config: the DP map of the board's settings (device_db dp_attributes), "
                         "shown only for boards with DP relays (as on the ZDMS16-2)",
    (1, 0x0000, 0xFF5C): "the stack exception that last restarted the module, a diagnostic read by "
                         "hand (as on the ZDMS16-2: no expose there either)",
}


def test_every_own_attribute_of_the_ts110e_build_is_on_the_converters_page(ts110e_stub, tmp_path):
    tables = listing(ts110e_stub, tmp_path / "list")
    used = {(ep, c, a) for _, ep, c, a in uses()}
    own = {(ep, c, a) for (ep, c), attrs in tables.items() for a in attrs
           if 0xFF00 <= a < 0xFFF0}                 # not the global ones (cluster revision 0xfffd)
    assert sorted(hex(a) for ep, c, a in own - used - set(NOT_SHOWN)) == []
    assert set(NOT_SHOWN) <= own                    # (none left over once it gets an expose)
