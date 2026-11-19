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
# generated converter (deviceEndpoints: name=endpoint id).
ZDMS16_2_EXTENDS = [
    "deviceEndpoints(dimmer_left=1, dimmer_right=2)",
    "romasku.ownMcuOption()",
    "romasku.mcuVersionMeta()",
    *[
        entry
        for dimmer in ("dimmer_left", "dimmer_right")
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
            f"romasku.ownMcuNumeric(soft_dim_end, {dimmer})",
            f"romasku.ownMcuNumeric(hold_time, {dimmer})",
            f"romasku.ownMcuNumeric(hold_dim_end, {dimmer})",
            # Open Dimmer 2.2.0 (shown only for it: a stock MCU's entities stay as they were)
            f"romasku.dimmerCountdown({dimmer})",
            f"romasku.dimmerSwitchMode({dimmer})",
            f"romasku.dimmerStartupLevel({dimmer})",
            f"romasku.dimmerStartBoost({dimmer})",
            f"romasku.dimmerExecuteIfOff({dimmer})",
            f"romasku.dimmerOnLevelLocal({dimmer})",
        )
    ],
    "romasku.dimmerGestures(dimmer_left, dimmer_right)",
    "romasku.dimmerPowerOnPerChannel(dimmer_left, dimmer_right)",
    "romasku.dimmerIdentify(dimmer_left, dimmer_right)",
    "romasku.mcuVersion(mcu_version, dimmer_left)",
    "romasku.mcuFirmware()",
    "romasku.mcuHealth()",
    "romasku.multiPressResetCount(multi_press_reset_count, dimmer_left)",
    "romasku.deviceConfig(device_config, dimmer_left)",
    "romasku.deviceConfigExt(device_config_ext, dimmer_left)",
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
