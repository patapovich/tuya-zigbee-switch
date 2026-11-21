"""The generated Z2M converter's Open Dimmer settings for the ZDMS16-2 (dimming
curve, fade and hold per dimmer, shown per the device option open_dimmer_settings,
old name own_mcu_settings), how it follows the MCU version the module reports
(Basic 0xff12, "x.y.z own" for an MCU it has identified as Open Dimmer), and what
it publishes from it: mcu_version without the marker, mcu_firmware."""

import json
import re

from generated_converter import custom_converter_js, run_node


def zdms16_block() -> str:
    js = custom_converter_js()
    start = js.index('model: "ZDMS16-2"')
    return js[start : js.index("configure:", start)]


# The ZDMS16-2 definition, the exposes that follow the MCU (name_endpoint), and
# a fake MCU version report (the module's attribute cache plus every genBasic
# fromZigbee, as Zigbee2MQTT runs them).
ZDMS = """
const def = definitions.find((d) => d.model === "ZDMS16-2");
const model = {endpoint: () => ({l1: 1, l2: 2})};
const shown = (dev, options = {}) => def.extend.filter((x) => !x.fake)
    .flatMap((x) => x.exposes ?? []).flatMap((x) => (typeof x === "function" ? x(dev, options) : [x]))
    .map((x) => `${x.name}${x.endpoint ? "_" + x.endpoint : ""}`);
const report = (dev, v, options = {}) => {
    dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v;
    const out = {};
    for (const fz of def.extend.flatMap((x) => (x.fake ? [] : x.fromZigbee ?? [])).filter((f) => f.cluster === "genBasic")) {
        Object.assign(out, fz.convert(model, {device: dev, endpoint: {ID: 1}, data: {[0xff12]: v}}, () => {}, options, {device: dev}) ?? {});
    }
    return out;
};
const fz = (dev, ep, data, options = {}) => def.extend.filter((x) => !x.fake).flatMap((x) => x.fromZigbee ?? [])
    .map((f) => f.convert(model, {device: dev, endpoint: {ID: ep}, data}, () => {}, options, {device: dev}))
    .filter((r) => r !== undefined);
"""


def test_curves_are_on_each_dimmer_endpoint_with_their_own_attribute():
    block = zdms16_block()
    curves = re.findall(
        r'ownMcuEnum\(\{\s*name: "dimming_curve",\s*endpointName: "(\w+)",\s*attrId: (0x\w+)', block
    )
    assert curves == [("l1", "0xff30"), ("l2", "0xff31")]
    assert "romasku.ownMcuOption()" in block
    assert "romasku.mcuVersionMeta()" in block
    assert "romasku.mcuFirmware()" in block


def test_own_firmware_is_told_by_the_suffix_only():
    """A vendor 2.x is not ours; a bare 2.x after a known "own" one still is (the module
    could not identify the own MCU: a lost identity answer); a 1.x stock MCU clears it."""
    out = run_node("""
const dev = (cached, meta) => ({ meta: { mcuVersion: meta },
  getEndpoint: () => ({ clusters: { genBasic: { attributes: { 65298: cached } } } }) });
console.log(JSON.stringify([
  c.ownMcuMajorOf(dev("2.0.3 own", undefined)),
  c.ownMcuMajorOf(dev("2.0.3", undefined)),
  c.ownMcuMajorOf(dev("", "2.0.3 own")),
  c.ownMcuMajorOf(dev("1.0.7", "2.0.3 own")),
  c.ownMcuMajorOf(undefined),
  c.ownMcuMajorOf(dev("2.1.10", "2.1.10 own")),
  c.ownMcuMajorOf(dev("2.1.11", "2.1.10 own")),
  c.ownMcuMajorOf(dev("0.0.0", "2.1.10 own")),
  c.ownMcuMajorOf(dev("1.0.7", undefined)),
]));
""")
    assert json.loads(out) == [2, None, 2, None, None, 2, 2, 2, None]


def test_a_bare_version_keeps_the_own_settings():
    """own -> bare 2.x (identity answer lost) -> "" (module restart): the exposes stay
    those of the own MCU, so a Zigbee2MQTT restart does not delete 15 entities per
    module; a stock version (1.x) then replaces it."""
    out = json.loads(run_node(ZDMS + """
const dev = h.device();
const steps = {};
for (const v of ["2.1.10 own", "2.1.10", "", "2.1.10 own", "1.0.7"]) {
    report(dev, v);
    steps[v || "empty"] = [dev.meta.mcuVersion, shown(dev).length, shown(dev).includes("action")];
}
console.log(JSON.stringify(steps));
"""))
    own = ["2.1.10 own", 16, True]                  # 15 Open Dimmer exposes and mcu_firmware
    assert out["2.1.10 own"] == own
    assert out["2.1.10"] == own
    assert out["empty"] == own
    assert out["1.0.7"] == ["1.0.7", 2, False]      # the shared power_on_behavior and mcu_firmware


def test_power_on_per_channel_and_gestures_follow_the_own_mcu():
    out = json.loads(run_node(ZDMS + """
const ver = (v) => { const d = h.device(); d.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v; return d; };
const po = def.extend.find((x) => !x.fake && x.toZigbee?.[0]?.key?.includes("power_on_behavior"));
const pick = (list) => list.filter((n) => n.startsWith("power_on") || n === "action");
console.log(JSON.stringify({
  own: pick(shown(ver("2.1.1 own"))),
  stock: pick(shown(ver("1.0.7"))),
  hide: pick(shown(ver("2.1.1 own"), { open_dimmer_settings: "hide" })),
  show: pick(shown(ver("1.0.7"), { open_dimmer_settings: "show" })),
  gestures: [fz(ver("2.1.1 own"), 2, { presentValue: 0x32 }), fz(ver("2.1.1 own"), 1, { presentValue: 0x15 }),
             fz(ver("2.1.1 own"), 1, { presentValue: 0x10 }), fz(ver("2.1.1 own"), 3, { presentValue: 0x11 })],
  power: [fz(ver("1.0.7"), 1, { startUpOnOff: 255 }), fz(ver("1.0.7"), 2, { startUpOnOff: 0 }), fz(ver("1.0.7"), 2, { onOff: 1 })],
}));
"""))
    assert out["own"] == ["power_on_behavior_l1", "power_on_behavior_l2", "action"]
    assert out["stock"] == ["power_on_behavior"]
    assert out["hide"] == ["power_on_behavior"]
    assert out["show"] == ["power_on_behavior_l1", "power_on_behavior_l2", "action"]
    assert out["gestures"] == [[{"action": "double_l2"}], [{"action": "release_l1"}], [], []]
    assert out["power"] == [
        [{"power_on_behavior_l1": "previous", "power_on_behavior": "previous"}],
        [{"power_on_behavior_l2": "off"}],
        [],
    ]


def test_shared_power_on_sets_every_channel():
    """The shared power_on_behavior (no endpoint) writes every dimmer endpoint: an own
    MCU keeps power-on per channel, so a write on endpoint 1 alone changed only L1. A
    per-channel one writes its own endpoint only."""
    out = json.loads(run_node(ZDMS + """
const po = def.extend.find((x) => !x.fake && x.toZigbee?.[0]?.key?.includes("power_on_behavior")).toZigbee[0];
const dev = h.device();
const run = async (endpointName, entity) => {
    h.log.length = 0;
    const r = await po.convertSet(entity, "power_on_behavior", "on", {endpoint_name: endpointName, device: dev, mapped: model});
    return [h.log.filter((l) => l[0] === "write").map((l) => [l[1], l[3].startUpOnOff]), r.state];
};
(async () => console.log(JSON.stringify({
    shared: await run(undefined, dev.getEndpoint(1)),
    right: await run("l2", dev.getEndpoint(2)),
})))();
"""))
    assert out["shared"] == [
        [[1, 1], [2, 1]],
        {"power_on_behavior": "on", "power_on_behavior_l1": "on", "power_on_behavior_l2": "on"},
    ]
    assert out["right"] == [[[2, 1]], {"power_on_behavior": "on"}]


def test_own_settings_from_a_stock_mcu_are_not_stored():
    """The module answers 0 for the own-MCU attributes when the MCU is stock: those
    must not reach the state as 'linear' / 0, values a stock MCU does not have."""
    out = json.loads(run_node(ZDMS + """
const ver = (v) => { const d = h.device(); d.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v; return d; };
const data = {[0xff30]: 0, [0xff35]: 950};
console.log(JSON.stringify({
    own: fz(ver("2.1.10 own"), 1, data),
    stock: fz(ver("1.0.7"), 1, data),
    unknown: fz(ver(""), 1, data),
    show: fz(ver("1.0.7"), 1, data, {open_dimmer_settings: "show"}),
}));
"""))
    assert out["own"] == [{"dimming_curve_l1": "linear"}, {"hold_time_l1": 0.95}]   # ms, shown in s
    assert out["stock"] == []
    assert out["unknown"] == []
    assert out["show"] == out["own"]


def test_the_old_option_name_still_works():
    """The device option open_dimmer_settings was own_mcu_settings: a value set under
    the old name keeps working, and the new name wins when both are set."""
    out = json.loads(run_node(ZDMS + """
const ver = (v) => { const d = h.device(); d.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v; return d; };
const n = (v, options) => shown(ver(v), options).length;
console.log(JSON.stringify({
    oldHide: n("2.1.10 own", {own_mcu_settings: "hide"}),
    oldShow: n("1.0.7", {own_mcu_settings: "show"}),
    newWins: [n("2.1.10 own", {open_dimmer_settings: "auto", own_mcu_settings: "hide"}),
              n("1.0.7", {open_dimmer_settings: "hide", own_mcu_settings: "show"})],
    oldShowStored: fz(ver("1.0.7"), 1, {[0xff30]: 0}, {own_mcu_settings: "show"}),
    options: def.extend.flatMap((x) => x.options ?? []).map((o) => [o.name, o.values, o.label]),
}));
"""))
    assert out["oldHide"] == 2                  # as a stock MCU: shared power_on_behavior, mcu_firmware
    assert out["oldShow"] == 16 + 23 + 6 + 6    # "show": every Open Dimmer setting, those of 2.2.0,
                                                # the 6 binding settings and 2.2.8's lamp_compensation,
                                                # custom_curve and long_press_from_off
    assert out["newWins"] == [16, 2]
    assert out["oldShowStored"] == [{"dimming_curve_l1": "linear"}]
    assert out["options"] == [["open_dimmer_settings", ["auto", "show", "hide"], "Open Dimmer settings"],
                              ["transition", None, None],    # its own description (dimmerTransition)
                              ["mcu_flasher", ["hide", "show"], "MCU flasher"]]  # the SWD master


# Every genBasic fromZigbee of the definition in Zigbee2MQTT's order, mcu_version's
# text() included (the harness's text() publishes a report as the real one does).
PUBLISH = ZDMS + """
const publish = (dev, v, ep = 1) => {
    if (ep === 1) dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v;
    const out = {};
    for (const f of def.extend.flatMap((x) => x.fromZigbee ?? []).filter((f) => f.cluster === "genBasic")) {
        Object.assign(out, f.convert(model, {device: dev, endpoint: {ID: ep}, data: {[0xff12]: v}}, () => {}, {}, {device: dev}) ?? {});
    }
    return out;
};
"""


def test_mcu_version_and_mcu_firmware_payloads():
    """mcu_version shows the version without the module's Open Dimmer marker, on the
    device (key mcu_version, no endpoint: the MCU is the module's, not a channel's);
    mcu_firmware says which firmware it is. Open Dimmer is sticky as for the
    settings: a bare 2.x after it is still Open Dimmer, only a stock version ends it.
    mcu_firmware publishes nothing while the version is not known ("" or 0.0.0). The
    kept version stays raw ("x.y.z own"): the detection reads the marker."""
    out = json.loads(run_node(PUBLISH + """
const dev = h.device();
const steps = [];
for (const v of ["2.1.10 own", "2.1.10", "", "0.0.0", "2.1.11", "1.0.7", "2.1.10", "2.1.11 own"]) {
    steps.push([v, publish(dev, v), dev.meta.mcuVersion]);
}
console.log(JSON.stringify({
    steps,
    fresh: [publish(h.device(), "2.1.10"), publish(h.device(), "1.0.7"), publish(h.device(), ""),
            publish(h.device(), "2.1.10 own", 2)],
    shown: ["2.1.10 own", "2.1.10", "1.0.7", "", "0.0.0", "2.1.10 own x", undefined].map(c.shownMcuVersion),
}));
"""))
    od, stock = "Open Dimmer", "Tuya (stock)"

    def v(version, firmware=None):
        return {"mcu_version": version, **({"mcu_firmware": firmware} if firmware else {})}

    assert out["steps"] == [
        ["2.1.10 own", v("2.1.10", od), "2.1.10 own"],
        ["2.1.10", v("2.1.10", od), "2.1.10 own"],          # identity answer lost: still Open Dimmer
        ["", v(""), "2.1.10 own"],                          # module restart: mcu_firmware keeps its value
        ["0.0.0", v("0.0.0"), "2.1.10 own"],
        ["2.1.11", v("2.1.11", od), "2.1.10 own"],
        ["1.0.7", v("1.0.7", stock), "1.0.7"],              # a stock MCU now
        ["2.1.10", v("2.1.10", stock), "2.1.10"],           # bare, nothing to stick to
        ["2.1.11 own", v("2.1.11", od), "2.1.11 own"],
    ]
    assert out["fresh"] == [v("2.1.10", stock), v("1.0.7", stock), v(""), {}]
    assert out["shown"] == ["2.1.10", "2.1.10", "1.0.7", "", "0.0.0", "2.1.10 own x", None]


def test_mcu_firmware_is_a_device_level_diagnostic_on_open_dimmer_boards_only():
    """Only boards that carry the Open Dimmer image (secondary_mcu_image) get it; other
    boards with a secondary MCU keep mcu_version alone. mcu_version itself is the
    text() it was (name, endpoint, access, category, attribute; its get is text()'s)."""
    out = json.loads(run_node("""
const dev = h.device();
const firmware = (model) => definitions.filter((d) => d.model === model && d.zigbeeModel).map((d) => d.extend.filter((x) => !x.fake)
    .flatMap((x) => x.exposes ?? []).flatMap((x) => (typeof x === "function" ? x(dev, {}) : [x]))
    .filter((x) => x.name === "mcu_firmware")
    .map(({type, name, label, access, endpoint, category, description}) => ({type, name, label, access, endpoint: endpoint ?? null, category, description})));
const version = (model) => definitions.filter((d) => d.model === model && d.zigbeeModel)
    .map((d) => h.fakeExtends(d).filter((x) => x.args?.name === "mcu_version").map((x) => [x.args, x.toZigbee]));
console.log(JSON.stringify({
    firmware: ["ZDMS16-2", "SFL02-Z-2", "TS0601_switch_8"].map(firmware),
    version: version("ZDMS16-2"),
}));
"""))
    expose = {"type": "text", "name": "mcu_firmware", "label": "MCU firmware", "access": 1, "endpoint": None,
              "category": "diagnostic",
              "description": "Firmware of the secondary MCU (the chip that drives the outputs): Open Dimmer or the stock Tuya "
                             "firmware; while not Open Dimmer, also an Open Dimmer install that waits for a power cycle, runs or failed"}
    assert out["firmware"] == [[[expose], [expose]], [[]], [[]]]
    text_args = {"name": "mcu_version", "access": "STATE_GET", "cluster": "genBasic",   # device level
                 "attribute": {"ID": 0xFF12, "type": 0x42},
                 "description": "Firmware version of the secondary MCU (the chip that drives the outputs)",
                 "entityCategory": "diagnostic"}
    assert out["version"] == [[[text_args, [{"key": ["mcu_version"]}]]]] * 2


def test_no_user_visible_text_says_own():
    """The firmware is called Open Dimmer: no expose, option or setting description
    of the ZDMS16-2 says "own" or "ours" (the module's " own" marker stays inside)."""
    out = json.loads(run_node(ZDMS + """
const exposes = (x) => (x.exposes ?? []).flatMap((e) => (typeof e === "function"
    ? [...e(h.device(), {open_dimmer_settings: "show"}), ...e(h.device(), {open_dimmer_settings: "hide"})] : [e]));
console.log(JSON.stringify(def.extend.flatMap((x) => [
    ...exposes(x).map((e) => e.description), ...(x.options ?? []).map((o) => o.description), x.args?.description,
]).filter((t) => typeof t === "string")));
"""))
    assert len(out) >= 20
    assert sum("Open Dimmer" in t for t in out) >= 17     # option, the 15 Open Dimmer exposes, mcu_firmware
    assert [t for t in out if re.search(r"\b(own|ours)\b", t, re.I)] == []


CONFIGURE = ZDMS + """
const configure = async (versions) => {
    const dev = h.device({versions});
    h.log.length = 0;
    h.delays.length = 0;
    await def.configure(dev, {ID: 1}, def);
    const reads = h.log.filter((l) => l[0] === "read");
    return {
        versionReads: reads.filter((l) => l[2] === "genBasic" && l[3][0] === 0xff12).length,
        // Open Dimmer's settings (not the SWD master's status 0xff50: test_converter_mcu_flasher)
        ownReads: reads.filter((l) => l[2] === "genBasic" && l[3][0] !== 0xff12 && l[3][0] !== 0xff50).map((l) => l[3][0].toString(16)),
        dimmerReads: reads.filter((l) => l[2] !== "genBasic").map((l) => `${l[1]}:${l[2]}:${l[3].join("+")}`),
        reporting: h.log.filter((l) => l[0] === "report").map((l) => [l[1], l[2], ...(l[3].map ? l[3].map((r) => [r.attribute.ID ?? r.attribute, r.minimumReportInterval, r.maximumReportInterval]) : [l[3]])]),
        delays: [...h.delays],
    };
};
"""


def test_configure_waits_for_the_mcu_before_reading_its_settings():
    """Right after a module restart the module answers "" until the MCU has told it:
    configure waits for a real mcu_version before it reads the MCU's settings."""
    out = json.loads(run_node(CONFIGURE + """
(async () => console.log(JSON.stringify(await configure(["", "", "2.1.10 own"]))))();
"""))
    assert out["versionReads"] == 3
    assert out["delays"][:3] == [2000, 2000, 1500]
    assert out["ownReads"] == [f"ff{a:02x}" for a in range(0x30, 0x3C)]
    assert out["dimmerReads"] == [
        f"{ep}:{cluster}" for ep in (1, 2) for cluster in
        ("genOnOff:startUpOnOff", "genLevelCtrl:onLevel", "lightingBallastCfg:minLevel+maxLevel", "genOnOffSwitchCfg:switchType")
    ]


def test_configure_reads_own_settings_only_from_an_own_mcu():
    """A stock MCU (or a bare version: the module did not identify ours) has none
    of them; reading would store the module's 0s."""
    out = json.loads(run_node(CONFIGURE + """
(async () => console.log(JSON.stringify([await configure(["1.0.7"]), await configure(["2.1.10"])])))();
"""))
    for run in out:
        assert run["versionReads"] == 1
        assert run["ownReads"] == []
        assert len(run["dimmerReads"]) == 8


def test_configure_stops_waiting_for_a_module_that_does_not_answer():
    """An offline module: two failed reads 2 s apart and configure goes on (15 reads
    with a pause after each took ~3 min)."""
    out = json.loads(run_node(CONFIGURE + """
(async () => console.log(JSON.stringify(await configure([new Error("timeout")]))))();
"""))
    assert out["versionReads"] == 2
    assert out["ownReads"] == []
    assert out["delays"] == [2000]
    assert len(out["dimmerReads"]) == 8


def test_configure_keeps_waiting_after_single_failed_reads():
    """A failed read now and then (delivery failures while routes re-form after a
    restart) neither ends the wait nor skips the pause before the next try; only two
    failures in a row do."""
    out = json.loads(run_node(CONFIGURE + """
const e = () => new Error("Delivery failed");
(async () => console.log(JSON.stringify(await configure([e(), "", e(), "", e(), "2.1.10 own"]))))();
"""))
    assert out["versionReads"] == 6
    assert out["delays"][:6] == [2000, 2000, 2000, 2000, 2000, 1500]
    assert out["ownReads"] == [f"ff{a:02x}" for a in range(0x30, 0x3C)]


def test_gestures_are_reported_on_change_only():
    """Gestures are events: a maximum report interval repeats the last one (about
    every 18 h with 65000 s) and fires its action again; 0 means changes only.
    No reporting is added for anything else (the module's table holds 12 entries)."""
    out = json.loads(run_node(CONFIGURE + """
(async () => console.log(JSON.stringify((await configure(["2.1.10 own"])).reporting)))();
"""))
    assert out == [
        [1, "genOnOff", {"min": 0, "max": 65000, "change": 1}],
        [1, "genLevelCtrl", ["currentLevel", 0, 65000]],
        [1, "genMultistateInput", [0x55, 0, 0]],
        [2, "genOnOff", {"min": 0, "max": 65000, "change": 1}],
        [2, "genLevelCtrl", ["currentLevel", 0, 65000]],
        [2, "genMultistateInput", [0x55, 0, 0]],
    ]


def test_on_level_is_read_again_when_the_module_announces_itself():
    """OnLevel is kept by the module, not the MCU, and has no reporting: after a module
    restart (it announces itself) the converter reads it again on every dimmer
    endpoint. Both onEvent signatures: Zigbee2MQTT 2.x (event) and 1.x (type, data, device)."""
    for v1 in (False, True):
        out = json.loads(run_node(ZDMS + """
const dev = h.device();
const reads = async (...args) => {
    h.log.length = 0;
    await def.onEvent(...args);
    return h.log.filter((l) => l[0] === "read").map((l) => `${l[1]}:${l[2]}:${l[3].join("+")}`);
};
(async () => console.log(JSON.stringify({
    announce: await reads({type: "deviceAnnounce", data: {device: dev}}),
    announceV1: await reads("deviceAnnounce", {}, dev, {}, {}),
    start: await reads({type: "start", data: {device: dev}}),
    stop: await reads({type: "stop", data: {ieeeAddr: dev.ieeeAddr}}),
})))();
""", z2m_v1=v1))
        on_level = ["1:genLevelCtrl:onLevel", "2:genLevelCtrl:onLevel"]
        assert out == {"announce": on_level, "announceV1": on_level, "start": [], "stop": []}


def test_device_level_numeric_has_no_endpoint_filter():
    """A numeric setting without an endpoint must not filter its reports on
    [undefined] (which matches no endpoint); with one it filters on that one."""
    out = json.loads(run_node("""
const r = c.romasku;
console.log(JSON.stringify([
    r.multiPressResetCount("multi_press_reset_count").args.endpointNames,
    r.multiPressResetCount("multi_press_reset_count", "l1").args.endpointNames,
    r.dimmerMinLevel("min_level").args.endpointNames ?? null,
]));
"""))
    assert out == [None, ["l1"], None]


def test_fade_and_hold_settings_are_per_dimmer_with_their_own_attributes():
    block = zdms16_block()
    found = re.findall(
        r'ownMcuNumeric\(\{\s*name: "(\w+)",\s*endpointName: "(\w+)",\s*attrId: (0x\w+)', block
    )
    names = ["fade_on_time", "fade_off_time", "fade_off_slow_zone", "hold_time", "hold_slow_zone"]
    assert found == [(n, "l1", f"0xff{0x32 + k:02x}") for k, n in enumerate(names)] + [
        (n, "l2", f"0xff{0x37 + k:02x}") for k, n in enumerate(names)
    ]
    assert block.count("minMcu: 2,") == 12          # 2 curves + 10 fade and hold settings


def test_every_dimmer_has_its_light_on_a_line_of_its_own():
    """A light expose that ends up behind a // comment (template whitespace) drops the
    channel's light: Home Assistant then deletes the entity (2026-11-17)."""
    block = zdms16_block()
    lines = [l for l in block.splitlines() if "light({ endpointNames" in l]
    assert [re.search(r'endpointNames: \["(\w+)"\]', l).group(1) for l in lines] == ["l1", "l2"]
    assert all(l.strip().startswith("light(") for l in lines)
