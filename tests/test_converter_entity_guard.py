"""Guards against Home Assistant entity loss through the generated Z2M converter.

Home Assistant deletes an entity when its expose disappears from a definition:
a template whitespace slip once put a light() behind a // comment, and Home
Assistant deleted the light entities of every module using the converter. A
rename or a dropped extend does the same.
These tests need no zigbee-herdsman-converters: they read the generated text.

When a change to the ZDMS16-2 entities is intended, update ZDMS16_2_EXTENDS
here and plan the Home Assistant side (entity ids, automations) with it.
"""

import re

from generated_converter import custom_converter_js

# The ZDMS16-2's extends in order: function(name, endpoint...) as in the
# generated converter (deviceEndpoints: name=endpoint id). Channels l1 / l2 as the stock
# definition of zigbee-herdsman-converters names them; the module's own settings
# (mcu_version, multi_press_reset_count, device_config*) on the device, not on l1.
ZDMS16_2_EXTENDS = [
    "deviceEndpoints(l1=1, l2=2)",
    "romasku.ownMcuOption()",
    "romasku.mcuVersionMeta()",
    "romasku.dimmerTransition(l1, l2)",
    *[
        entry
        for dimmer in ("l1", "l2")
        for entry in (
            f"light({dimmer})",
            f"romasku.dimmerMinLevel(min_level, {dimmer})",
            f"romasku.dimmerMaxLevel(max_level, {dimmer})",
            f"romasku.dimmerOnLevel(on_level, {dimmer})",
            f"romasku.ownMcuEnum(dimming_curve, {dimmer})",
            f"romasku.dimmerPowerOnChannel({dimmer})",
            f"romasku.dimmerSwitchType(switch_type, {dimmer})",
            f"romasku.ownMcuNumeric(fade_on_time, {dimmer})",
            f"romasku.ownMcuNumeric(fade_off_time, {dimmer})",
            f"romasku.ownMcuNumeric(fade_off_slow_zone, {dimmer})",
            f"romasku.ownMcuNumeric(hold_time, {dimmer})",
            f"romasku.ownMcuNumeric(hold_slow_zone, {dimmer})",
            # Open Dimmer 2.2.0 (shown only for it: a stock MCU's entities stay as they were)
            f"romasku.dimmerCountdown({dimmer})",
            f"romasku.dimmerSwitchMode({dimmer})",
            f"romasku.dimmerFastPress({dimmer})",
            f"romasku.dimmerLongPressFromOff({dimmer})",
            f"romasku.dimmerStartupLevel({dimmer})",
            f"romasku.dimmerStartBoost({dimmer})",
            f"romasku.dimmerLampCompensation({dimmer})",   # 2.2.8: a new entity, none lost
            f"romasku.dimmerCustomCurve({dimmer})",        # 2.2.8: a new entity, none lost
            f"romasku.dimmerExecuteIfOff({dimmer})",
            f"romasku.dimmerOnLevelLocal({dimmer})",
            f"romasku.dimmerBrightnessFadeTime({dimmer})",
            # the wall switch's bindings (dimmer-only builds; shown as Open Dimmer settings)
            f"romasku.dimmerBindSetting(bind_mode, {dimmer}, auto, off, mirror, remote)",
            f"romasku.dimmerBindSetting(double_press_action, {dimmer}, none, full, off, scene_1, scene_2)",
            f"romasku.dimmerBindSetting(triple_press_action, {dimmer}, none, full, off, scene_1, scene_2)",
        )
    ],
    "romasku.dimmerGestures(l1, l2)",
    "romasku.dimmerPowerOnPerChannel(l1, l2)",
    "romasku.dimmerIdentify(l1, l2)",
    "romasku.mcuVersion(mcu_version)",
    "romasku.mcuFirmware()",
    "romasku.mcuHealth()",
    # the SWD master: no expose unless the device option mcu_flasher is show
    "romasku.mcuFlasher()",
    "romasku.multiPressResetCount(multi_press_reset_count)",
    "romasku.deviceConfig(device_config)",
    "romasku.deviceConfigExt(device_config_ext)",
]


def definitions() -> list[str]:
    """The text of every definition in the generated converter."""
    js = custom_converter_js()
    body = js[js.index("const definitions = [") : js.index("module.exports")]
    return re.split(r"\n    \{\n(?=        zigbeeModel:)", body)[1:]


def model_of(definition: str) -> str:
    return re.search(r'^        model: "([^"]+)"', definition, re.M).group(1)


def extend_entries(definition: str) -> list[str]:
    """The top-level entries of the extend: [...] array, comments dropped."""
    i = definition.index("extend: [") + len("extend: [")
    depth, entry, entries = 0, "", []
    while True:
        ch = definition[i]
        if ch in "\"'`":
            end = i + 1
            while definition[end] != ch:
                end += 2 if definition[end] == "\\" else 1
            entry += definition[i : end + 1]
            i = end + 1
            continue
        if definition.startswith("//", i):
            i = definition.index("\n", i)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        if ch == "," and depth == 0:
            entries.append(entry.strip())
            entry = ""
        else:
            entry += ch
        i += 1
    if entry.strip():
        entries.append(entry.strip())
    return entries


def summary(entry: str) -> str:
    """function(name, endpoint...) of one extend entry."""
    fn = re.match(r"([\w.]+)\(", entry).group(1)
    if fn == "deviceEndpoints":
        args = [f"{name}={ep}" for name, ep in re.findall(r'"(\w+)":\s*(\d+)', entry)]
    elif re.match(r"[\w.]+\(\s*\{", entry):  # one object argument
        name = re.search(r'\bname:\s*"(\w+)"', entry)
        endpoints = re.search(r'\bendpointNames?:\s*(\[[^\]]*\]|"\w+")', entry)
        args = ([name.group(1)] if name else []) + (re.findall(r'"(\w+)"', endpoints.group(1)) if endpoints else [])
    else:
        args = re.findall(r'"(\w+)"', entry)
    return f"{fn}({', '.join(args)})"


def zdms16_2_definitions() -> list[str]:
    return [d for d in definitions() if model_of(d) == "ZDMS16-2"]


def test_zdms16_2_entities_are_as_deployed():
    """A rename, a dropped or a reordered extend fails here before it reaches Home
    Assistant (mutations that passed every test before: min_level renamed,
    switch_type or mcu_version removed)."""
    blocks = zdms16_2_definitions()
    assert len(blocks) == 2  # _TZE284 and _TZE204
    for block in blocks:
        assert [summary(e) for e in extend_entries(block)] == ZDMS16_2_EXTENDS


def test_both_zdms16_2_definitions_are_identical():
    """Zigbee2MQTT uses the last definition of a model; the tests read the first."""
    first, second = zdms16_2_definitions()
    assert first == second


def test_no_extend_is_commented_out():
    """A // comment that holds an extend means the template joined a comment and
    the next line: that extend, and its entities, are gone."""
    bad = []
    for definition in definitions():
        for line in definition.splitlines():
            code = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)  # strings may hold //
            comment = code.partition("//")[2]
            if "romasku." in comment or "light(" in comment:
                bad.append(f"{model_of(definition)}: {line.strip()}")
    assert not bad, "\n".join(bad)


def test_the_entry_parser_sees_a_commented_out_extend():
    """The guards above rely on the parser dropping comments."""
    definition = 'extend: [\n    a("x", "y"),\n    // b("z"), light({ endpointNames: ["q"] }),\n    c(["p"]),\n],'
    assert [summary(e) for e in extend_entries(definition)] == ["a(x, y)", "c(p)"]


def test_zdms16_2_matching_and_the_entities_of_a_stock_unit_stay():
    """Zigbee2MQTT picks the definition by the model id (the device id and the new clusters do
    not take part); a module with the stock MCU (the house units) keeps exactly the entities it
    had: no Open Dimmer expose, no action. An Open Dimmer before 2.2 keeps its action list."""
    import json

    from generated_converter import run_node
    out = json.loads(run_node("""
const defs = definitions.filter((d) => d.model === "ZDMS16-2" && d.zigbeeModel);
const ver = (v) => { const d = h.device(); d.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v; return d; };
const dynamic = (def, dev) => def.extend.filter((x) => !x.fake).flatMap((x) => x.exposes ?? [])
    .flatMap((x) => (typeof x === "function" ? x(dev, {}) : [x]))
    .map((x) => [x.name, x.endpoint ?? null, x.values ?? null]);
console.log(JSON.stringify({
    models: defs.map((d) => d.zigbeeModel),
    stock: defs.map((d) => dynamic(d, ver("1.0.7"))),
    od21: dynamic(defs[0], ver("2.1.14 own")).filter(([n]) => n === "action"),
}));
"""))
    assert out["models"] == [["TS0601-ZDMS16-2"], ["TS0601-ZDMS16-2"]]
    stock = [["power_on_behavior", None, ["off", "on", "previous"]], ["mcu_firmware", None, None]]
    assert out["stock"] == [stock, stock]
    assert out["od21"] == [["action", None, [f"{k}_{n}" for n in ("l1", "l2")
                                             for k in ("single", "double", "triple", "hold", "release")]]]


# What configure does for a module with the stock MCU (the house units), as the deployed
# converter of 4c3558a1 does it: the version read, then per dimmer bind, reporting, the reads.
# Since the SWD master: one read of its status (Basic 0xff50) after the version; a module
# without the master (no SW token) does not have it, so no genBasic bind follows
# (test_converter_mcu_flasher: with the master the Basic cluster is bound).
STOCK_CONFIGURE = [
    ["read", 1, "genBasic", [0xFF12]],
    ["read", 1, "genBasic", [0xFF50]],
    *[call for ep in (1, 2) for call in (
        ["bind", ep, ["genOnOff", "genLevelCtrl"]],
        ["report", ep, "genOnOff", {"min": 0, "max": 65000, "change": 1}],
        ["report", ep, "genLevelCtrl", [{"attribute": "currentLevel", "minimumReportInterval": 0,
                                         "maximumReportInterval": 65000, "reportableChange": 1}]],
        ["read", ep, "genOnOff", ["startUpOnOff"]],
        ["read", ep, "genLevelCtrl", ["onLevel"]],
        ["read", ep, "lightingBallastCfg", ["minLevel", "maxLevel"]],
        ["read", ep, "genOnOffSwitchCfg", ["switchType"]],
        ["bind", ep, ["genMultistateInput"]],
        ["report", ep, "genMultistateInput", [{"attribute": {"ID": 0x55, "type": 0x21},
                                               "minimumReportInterval": 0, "maximumReportInterval": 0,
                                               "reportableChange": 1}]],
    )],
]


def test_configure_of_a_stock_unit_stays():
    """No genBasic bind, no Open Dimmer read: the deployed converter's calls (and the
    SWD master's status read)."""
    import json

    from generated_converter import run_node
    out = json.loads(run_node("""
const def = definitions.find((d) => d.model === "ZDMS16-2");
(async () => {
    const dev = h.device({versions: ["1.0.7"]});
    h.log.length = 0;
    await def.configure(dev, {ID: 1}, def);
    console.log(JSON.stringify(h.log));
})();
"""))
    assert out == STOCK_CONFIGURE
