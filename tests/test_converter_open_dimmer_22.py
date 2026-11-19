"""The generated Z2M converter's Open Dimmer 2.2.0 exposes for the ZDMS16-2: per channel the
countdown (genOnOff 0xff10), switch_mode (genOnOffSwitchCfg 0xff10), current_level_startup
(genLevelCtrl startUpCurrentLevel), start_boost (lightingBallastCfg 0xff10), execute_if_off
(genLevelCtrl options) and on_level_local (genLevelCtrl 0xff11); identify (both
channels); the health report sensors (genBasic 0xff48). Shown only for an MCU identified as Open
Dimmer 2.2 or later (or with open_dimmer_settings: show): a stock MCU's and an older Open
Dimmer's exposes, reads and reporting stay as they were."""

import json

from generated_converter import run_node
from test_converter_own_mcu import CONFIGURE, ZDMS

NEW_PER_CHANNEL = ["countdown", "switch_mode", "fast_press", "current_level_startup",
                   "start_boost", "execute_if_off", "on_level_local"]
NEW_DEVICE = ["identify", "mcu_uptime", "mcu_reset_cause", "mcu_zero_cross_losses",
              "mcu_uart_rx_losses", "mcu_temperature", "mcu_settings_ring_moved"]
NEW = [f"{n}_{ch}" for ch in ("l1", "l2") for n in NEW_PER_CHANNEL] + NEW_DEVICE

# Every expose of the definition, the fake (static) extends' too, by name_endpoint.
ALL = ZDMS + """
const ver = (v) => { const d = h.device(); d.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v; return d; };
const all = (dev, options = {}) => [
    ...h.fakeExtends(def).map((x) => `${x.fake}:${x.args?.name ?? ""}:${x.args?.endpointName ?? x.args?.endpointNames ?? ""}`),
    ...shown(dev, options)];
"""

# The 2.1.x exposes before this change (test_converter_own_mcu: 15 Open Dimmer ones and
# mcu_firmware) and a stock MCU's (the shared power_on_behavior and mcu_firmware).
OWN_2_1 = ["dimming_curve_l1", "power_on_behavior_l1", "fade_on_time_l1",
           "fade_off_time_l1", "fade_off_slow_zone_l1", "hold_time_l1",
           "hold_slow_zone_l1", "dimming_curve_l2", "power_on_behavior_l2",
           "fade_on_time_l2", "fade_off_time_l2", "fade_off_slow_zone_l2",
           "hold_time_l2", "hold_slow_zone_l2", "action", "mcu_firmware"]
STOCK = ["power_on_behavior", "mcu_firmware"]
FADE = ["brightness_fade_time_l1", "brightness_fade_time_l2"]   # from 2.1.14


def test_the_new_exposes_follow_the_open_dimmer_version():
    out = json.loads(run_node(ALL + """
console.log(JSON.stringify({
    own22: shown(ver("2.2.0 own")), own23: shown(ver("2.3.1 own")), own3: shown(ver("3.0.0 own")),
    own2114: shown(ver("2.1.14 own")), bare22: shown(ver("2.2.0")), stock: shown(ver("1.0.7")),
    hide: shown(ver("2.2.0 own"), {open_dimmer_settings: "hide"}),
    show: shown(ver("1.0.7"), {open_dimmer_settings: "show"}),
    fakeStock: all(ver("1.0.7")).filter((n) => n.includes(":")),
    fakeOwn: all(ver("2.2.0 own")).filter((n) => n.includes(":")),
}));
"""))
    assert sorted(out["own22"]) == sorted(OWN_2_1 + NEW + FADE)
    assert out["own23"] == out["own3"] == out["own22"]
    assert sorted(out["own2114"]) == sorted(OWN_2_1 + FADE)  # no 2.2.0 setting for 2.1.x
    assert out["bare22"] == STOCK                         # not identified: as a stock MCU
    assert out["stock"] == STOCK                          # the house units: unchanged
    assert out["hide"] == STOCK
    assert sorted(out["show"]) == sorted(OWN_2_1 + NEW + FADE)
    assert out["fakeStock"] == out["fakeOwn"]             # the static extends are the same


def test_the_new_exposes_are_described_and_ranged():
    out = json.loads(run_node(ZDMS + """
const dev = h.device(); dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = "2.2.0 own";
const ex = def.extend.filter((x) => !x.fake).flatMap((x) => x.exposes ?? [])
    .flatMap((x) => (typeof x === "function" ? x(dev, {}) : [x]));
const pick = (name, ep) => ex.find((x) => x.name === name && x.endpoint === ep);
const brief = (x) => x && ({type: x.type, access: x.access, min: x.value_min, max: x.value_max,
    step: x.value_step, unit: x.unit, values: x.values, presets: x.presets?.map((p) => [p.name, p.value]),
    category: x.category ?? null});
console.log(JSON.stringify({
    countdown: brief(pick("countdown", "l2")), mode: brief(pick("switch_mode", "l1")),
    startup: brief(pick("current_level_startup", "l1")), boost: brief(pick("start_boost", "l2")),
    eio: brief(pick("execute_if_off", "l1")), local: brief(pick("on_level_local", "l2")),
    identify: brief(pick("identify", undefined)), uptime: brief(pick("mcu_uptime", undefined)),
    temperature: brief(pick("mcu_temperature", undefined)), moved: brief(pick("mcu_settings_ring_moved", undefined)),
    descriptions: ex.filter((x) => x.description?.includes("Open Dimmer")).length,
}));
"""))
    assert out["countdown"] == {"type": "numeric", "access": 7, "min": 0, "max": 86400, "unit": "s",
                                "category": None}
    assert out["mode"] == {"type": "enum", "access": 7, "values": ["normal", "detached", "off"],
                           "category": "config"}
    assert out["startup"] == {"type": "numeric", "access": 7, "min": 0, "max": 255,
                              "presets": [["minimum", 0], ["previous", 255]], "category": "config"}
    assert out["eio"] == {"type": "binary", "access": 7, "category": "config"}
    assert out["local"] == {"type": "numeric", "access": 7, "min": 1, "max": 255, "category": "config"}
    assert out["boost"] == {"type": "numeric", "access": 7, "min": 0, "max": 0.75, "step": 0.05,
                            "unit": "s", "category": "config"}
    assert out["identify"] == {"type": "enum", "access": 2, "values": ["identify"], "category": "config"}
    assert out["uptime"] == {"type": "numeric", "access": 5, "unit": "min", "category": "diagnostic"}
    assert out["temperature"] == {"type": "numeric", "access": 5, "unit": "°C", "category": "diagnostic"}
    assert out["moved"] == {"type": "binary", "access": 5, "category": "diagnostic"}
    assert out["descriptions"] >= 30


def test_reports_of_the_new_attributes():
    out = json.loads(run_node(ALL + """
const own = ver("2.2.0 own"), stock = ver("1.0.7");
console.log(JSON.stringify({
    countdown: fz(own, 1, {[0xff10]: 30}),
    off: fz(own, 2, {onOff: 0}),
    on: fz(own, 2, {onOff: 1}),
    mode: fz(own, 2, {[0xff10]: 1}),
    startup: [fz(own, 1, {startUpCurrentLevel: 0}), fz(own, 1, {startUpCurrentLevel: 255}),
              fz(own, 2, {startUpCurrentLevel: 128})],
    eio: [fz(own, 1, {options: 1}), fz(own, 2, {options: 2})],
    local: fz(own, 2, {[0xff11]: 200}),
    stock: [fz(stock, 1, {[0xff10]: 30}), fz(stock, 2, {onOff: 0}), fz(stock, 1, {startUpCurrentLevel: 0})],
}));
"""))
    # fz runs every converter of the definition on the message, whatever its cluster: genOnOff
    # 0xff10 also matches the other clusters' 0xff10 converters on that endpoint.
    assert {"countdown_l1": 30} in out["countdown"]
    assert out["off"] == [{"countdown_l2": 0}]
    assert out["on"] == []
    assert {"switch_mode_l2": "detached"} in out["mode"]
    assert out["startup"] == [[{"current_level_startup_l1": 0}],
                              [{"current_level_startup_l1": 255}],
                              [{"current_level_startup_l2": 128}]]
    assert out["eio"] == [[{"execute_if_off_l1": True}], [{"execute_if_off_l2": False}]]
    assert {"on_level_local_l2": 200} in out["local"]   # (0xff11 of genOnOffSwitchCfg too)
    assert out["stock"] == [[], [], []]


def test_each_report_comes_from_its_own_cluster():
    """Per cluster, as Zigbee2MQTT runs them (fromZigbee filtered by the message's cluster)."""
    out = json.loads(run_node(ZDMS + """
const dev = h.device(); dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = "2.2.0 own";
const fzc = (cluster, ep, data) => def.extend.filter((x) => !x.fake).flatMap((x) => x.fromZigbee ?? [])
    .filter((f) => f.cluster === cluster)
    .map((f) => f.convert(model, {device: dev, endpoint: {ID: ep}, data}, () => {}, {}, {device: dev}))
    .filter((r) => r !== undefined);
console.log(JSON.stringify([fzc("genOnOff", 1, {[0xff10]: 30}), fzc("genOnOffSwitchCfg", 1, {[0xff10]: 2}),
                            fzc("lightingBallastCfg", 2, {[0xff10]: 300})]));
"""))
    assert out == [[{"countdown_l1": 30}], [{"switch_mode_l1": "off"}],
                   [{"start_boost_l2": 0.3}]]


SET = ZDMS + """
const dev = h.device(); dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = "2.2.0 own";
const tz = (key) => def.extend.filter((x) => !x.fake).flatMap((x) => x.toZigbee ?? [])
    .find((t) => t.key.includes(key));
const set = async (key, value, ep = 1) => {
    h.log.length = 0;
    try {
        const r = await tz(key).convertSet(dev.getEndpoint(ep), key, value, {device: dev, mapped: model, options: {}});
        return [h.log.filter((l) => l[0] === "write" || l[0] === "command"), r?.state ?? null];
    } catch (error) {
        return error.message;
    }
};
"""


def test_writes_of_the_new_settings():
    out = json.loads(run_node(SET + """
(async () => console.log(JSON.stringify({
    countdown: await set("countdown", 90, 2),
    countdownBad: await set("countdown", 86401),
    mode: await set("switch_mode", "off"),
    modeBad: await set("switch_mode", "sometimes"),
    startup: [await set("current_level_startup", "previous"), await set("current_level_startup", "minimum"),
              await set("current_level_startup", 77, 2)],
    startupBad: await set("current_level_startup", 256),
    eio: [await set("execute_if_off", true), await set("execute_if_off", false, 2)],
    eioBad: await set("execute_if_off", "maybe"),
    local: await set("on_level_local", 255),
    localBad: await set("on_level_local", 0),
    boost: await set("start_boost", 0.3),
    boostText: await set("start_boost", "0.25"),
    boostBad: [await set("start_boost", 0.8), await set("start_boost", ""), await set("start_boost", -0.05)],
    identify: await set("identify", "identify"),
})))();
"""))
    assert out["countdown"] == [[["write", 2, "genOnOff", {str(0xff10): {"value": 90, "type": 0x23}}]],
                                {"countdown": 90}]
    assert "0..86400" in out["countdownBad"]
    assert out["mode"] == [[["write", 1, "genOnOffSwitchCfg", {str(0xff10): {"value": 2, "type": 0x30}}]],
                           {"switch_mode": "off"}]
    assert "normal, detached, off" in out["modeBad"]
    assert out["startup"] == [
        [[["write", 1, "genLevelCtrl", {str(0x4000): {"value": 255, "type": 0x20}}]], {"current_level_startup": 255}],
        [[["write", 1, "genLevelCtrl", {str(0x4000): {"value": 0, "type": 0x20}}]], {"current_level_startup": 0}],
        [[["write", 2, "genLevelCtrl", {str(0x4000): {"value": 77, "type": 0x20}}]], {"current_level_startup": 77}],
    ]
    assert "0..255" in out["startupBad"]
    assert out["eio"] == [
        [[["write", 1, "genLevelCtrl", {str(0x000f): {"value": 1, "type": 0x18}}]], {"execute_if_off": True}],
        [[["write", 2, "genLevelCtrl", {str(0x000f): {"value": 0, "type": 0x18}}]], {"execute_if_off": False}],
    ]
    assert "true or false" in out["eioBad"]
    assert out["local"] == [[["write", 1, "genLevelCtrl", {str(0xff11): {"value": 255, "type": 0x20}}]],
                            {"on_level_local": 255}]
    assert "1..255" in out["localBad"]
    # Shown in s, the attribute in ms.
    assert out["boost"] == [[["write", 1, "lightingBallastCfg", {str(0xff10): {"value": 300, "type": 0x21}}]],
                            {"start_boost": 0.3}]
    assert out["boostText"][0][0][3] == {str(0xff10): {"value": 250, "type": 0x21}}
    assert all("0..0.75 s" in r for r in out["boostBad"]), out["boostBad"]
    assert out["identify"] == [[["command", 1, "genIdentify", "identify", {"identifytime": 3}],
                                ["command", 2, "genIdentify", "identify", {"identifytime": 3}]], None]


def health_report(fields: dict) -> str:
    """The octet string of Basic 0xff48 (format 1) as a JS array; without "cal" as an older
    build of this firmware made it (17 bytes)."""
    b = bytes([1]) + fields["uptime"].to_bytes(4, "little") + fields["zc"].to_bytes(4, "little") \
        + fields["uart"].to_bytes(4, "little") + bytes([fields["reset"], fields["flags"]]) \
        + fields["temperature"].to_bytes(2, "little", signed=True)
    if "cal" in fields:
        b += fields["cal"].to_bytes(4, "little")
    return json.dumps(list(b))


def test_the_health_report_is_decoded():
    report = health_report({"uptime": 4242, "zc": 3, "uart": 17, "reset": 0x18, "flags": 0x03,
                            "temperature": -5, "cal": 0x047203B5})
    none = health_report({"uptime": 1, "zc": 0, "uart": 0, "reset": 0x20, "flags": 0x01,
                          "temperature": -32768})
    out = json.loads(run_node(ALL + f"""
const own = ver("2.2.0 own"), stock = ver("1.0.7");
console.log(JSON.stringify({{
    own: fz(own, 1, {{[0xff48]: Buffer.from({report})}}),
    none: fz(own, 1, {{[0xff48]: Buffer.from({none})}}),
    empty: fz(own, 1, {{[0xff48]: Buffer.from([])}}),
    otherEndpoint: fz(own, 2, {{[0xff48]: Buffer.from({report})}}),
    stock: fz(stock, 1, {{[0xff48]: Buffer.from({report})}}),
}}));
"""))
    # The sensor's factory calibration (bytes 17..20) is not published.
    assert out["own"] == [{"mcu_uptime": 4242, "mcu_zero_cross_losses": 3, "mcu_uart_rx_losses": 17,
                           "mcu_reset_cause": "software,power", "mcu_settings_ring_moved": True,
                           "mcu_temperature": -5}]
    assert out["none"] == [{"mcu_uptime": 1, "mcu_zero_cross_losses": 0, "mcu_uart_rx_losses": 0,
                            "mcu_reset_cause": "watchdog", "mcu_settings_ring_moved": False,
                            "mcu_temperature": None}]
    assert out["empty"] == out["otherEndpoint"] == out["stock"] == []


def test_configure_binds_basic_for_open_dimmer_and_reads_the_new_settings_of_2_2_only():
    out = json.loads(run_node(CONFIGURE + """
(async () => {
    const runs = {};
    for (const v of ["2.2.0 own", "2.1.14 own", "1.0.7", "2.2.0"]) {
        const r = await configure([v]);
        r.binds = h.log.filter((l) => l[0] === "bind").map((l) => `${l[1]}:${l[2].join("+")}`);
        runs[v] = r;
    }
    console.log(JSON.stringify(runs));
})();
"""))
    own = out["2.2.0 own"]
    assert own["ownReads"] == [f"ff{a:02x}" for a in range(0x30, 0x3C)] + ["ff48"]
    assert "1:genBasic" in own["binds"]
    new_reads = [f"{ep}:{cluster}" for ep in (1, 2) for cluster in
                 ("genOnOff:65296", "genOnOffSwitchCfg:65296+65297",
                  "genLevelCtrl:startUpCurrentLevel+options+65297", "lightingBallastCfg:65296")]
    assert [r for r in own["dimmerReads"] if r in new_reads] == new_reads
    assert len(own["dimmerReads"]) == 8 + 8 + 2
    assert "1:genBasic" in out["2.1.14 own"]["binds"]     # any Open Dimmer: its version reports
    for v in ("1.0.7", "2.2.0"):                            # stock, not identified: no bind
        assert "1:genBasic" not in out[v]["binds"], v
    assert out["2.1.14 own"]["dimmerReads"][-1] == "2:genLevelCtrl:65298"   # brightness_fade_time
    for v in ("2.1.14 own", "1.0.7", "2.2.0"):
        run = out[v]
        assert "ff48" not in run["ownReads"], v
        assert len(run["dimmerReads"]) == (10 if v == "2.1.14 own" else 8), v
        assert run["reporting"] == own["reporting"]      # no reporting added for any of it
    assert len(own["reporting"]) == 6


def test_a_setting_that_is_not_shown_is_not_written():
    """A stock unit or an older Open Dimmer (no 2.2 settings shown): a write is refused before
    it goes out, so no state the module would not keep; "show" lets it through."""
    out = json.loads(run_node(ZDMS + """
const tz = (key) => def.extend.filter((x) => !x.fake).flatMap((x) => x.toZigbee ?? []).find((t) => t.key.includes(key));
const run = async (v, key, value, options = {}) => {
    const dev = h.device(); dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v;
    h.log.length = 0;
    try {
        await tz(key).convertSet(dev.getEndpoint(1), key, value, {device: dev, mapped: model, options});
        return h.log.filter((l) => l[0] === "write" || l[0] === "command").length;
    } catch (error) {
        return error.message;
    }
};
(async () => console.log(JSON.stringify({
    stock: [await run("1.0.7", "countdown", 60), await run("1.0.7", "switch_mode", "off"),
            await run("1.0.7", "identify", "identify"), await run("1.0.7", "fast_press", true)],
    od21: await run("2.1.14 own", "start_boost", 0.3),
    show: await run("1.0.7", "countdown", 60, {open_dimmer_settings: "show"}),
    own: await run("2.2.0 own", "fast_press", true),
})))();
"""))
    assert all("Open Dimmer 2.2 setting" in r for r in out["stock"]), out["stock"]
    assert "Open Dimmer 2.2 setting" in out["od21"]
    assert out["show"] == 1 and out["own"] == 1


def test_the_press_gesture_is_an_action_of_open_dimmer_2_2_only():
    out = json.loads(run_node(ALL + """
const actions = (v) => def.extend.filter((x) => !x.fake).flatMap((x) => x.exposes ?? [])
    .flatMap((x) => (typeof x === "function" ? x(ver(v), {}) : [x])).filter((x) => x.name === "action")
    .map((x) => x.values);
console.log(JSON.stringify({
    od22: actions("2.2.0 own"), od21: actions("2.1.14 own"), stock: actions("1.0.7"),
    press: fz(ver("2.2.0 own"), 2, {presentValue: 0x36}), hold: fz(ver("2.2.0 own"), 1, {presentValue: 0x24}),
    press21: fz(ver("2.1.14 own"), 2, {presentValue: 0x36}),
}));
"""))
    names = ("l1", "l2")
    assert out["od22"] == [[f"{k}_{n}" for n in names for k in ("single", "double", "triple", "hold",
                                                                "release", "press")]]
    assert out["od21"] == [[f"{k}_{n}" for n in names for k in ("single", "double", "triple", "hold",
                                                                "release")]]
    assert out["stock"] == []
    assert out["press"] == [{"action": "press_l2"}]
    assert out["hold"] == [{"action": "hold_l1"}]
    assert out["press21"] == []


# What goes out for a message (light()'s converters as zhc has them: converter_harness.js),
# through the converter the definition takes first for the key, as Zigbee2MQTT picks it.
SEND = ZDMS + """
const tzFor = (key, ep) => def.extend.flatMap((x) => x.toZigbee ?? [])
    .find((t) => t.key?.includes(key) && (!t.endpoints || t.endpoints.includes(ep)));
const send = async (version, message, options = {}, ep = "l2", state = {state: "ON", brightness: 100}) => {
    h.globalStore.clear();
    const dev = h.device(); dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = version;
    const key = Object.keys(message).find((k) => k !== "transition");
    h.log.length = 0;
    await tzFor(key, ep).convertSet(dev.getEndpoint(2), key, message[key], {device: dev, message, options, state,
                                                                         endpoint_name: ep, mapped: model});
    return h.log.filter((l) => l[0] === "command").map((l) => [l[2], l[3], l[4].transtime ?? null]);
};
"""


def test_a_level_change_without_a_transition_goes_out_as_none_for_open_dimmer():
    out = json.loads(run_node(SEND + """
(async () => {
    const r = {};
    for (const v of ["2.2.0 own", "2.1.14 own", "1.0.7"]) {
        r[v] = {
            brightness: await send(v, {brightness: 100}),
            on100: await send(v, {state: "ON", brightness: 100}),
            step: await send(v, {brightness_step: 20}),
            stepOnOff: await send(v, {brightness_step_onoff: -20}),
            on: await send(v, {state: "ON"}), off: await send(v, {state: "OFF"}),
            given0: await send(v, {brightness: 100, transition: 0}),
            given12: await send(v, {brightness: 100, transition: 1.2}),
            option12: await send(v, {brightness: 100}, {transition: 1.2}),
            option12Off: await send(v, {state: "OFF"}, {transition: 1.2}),
            option0On: await send(v, {state: "ON"}, {transition: 0}),
            option12On: await send(v, {state: "ON"}, {transition: 1.2}, "l2", {state: "OFF", brightness: 100}),
            option12Toggle: await send(v, {state: "TOGGLE"}, {transition: 1.2}, "l2", {state: "OFF", brightness: 100}),
            option12ToggleOff: await send(v, {state: "TOGGLE"}, {transition: 1.2}),
            given12On: await send(v, {state: "ON", transition: 1.2}, {}, "l2", {state: "OFF", brightness: 100}),
            emptyOption: await send(v, {brightness: 100}, {transition: ""}),
            move: await send(v, {brightness_move: 40}),
        };
    }
    console.log(JSON.stringify(r));
})();
"""))
    mtl = "moveToLevelWithOnOff"
    for v in ("2.2.0 own", "2.1.14 own"):                    # Open Dimmer
        r = out[v]
        assert r["brightness"] == r["on100"] == [["genLevelCtrl", mtl, 0xFFFF]], v
        assert r["step"] == [["genLevelCtrl", "step", 0xFFFF]]
        assert r["stepOnOff"] == [["genLevelCtrl", "stepWithOnOff", 0xFFFF]]
        assert r["emptyOption"] == [["genLevelCtrl", mtl, 0xFFFF]]
        assert r["on"] == [["genOnOff", "on", None]] and r["off"] == [["genOnOff", "off", None]]
        assert r["given0"] == [["genLevelCtrl", mtl, 0]]          # explicit 0: at once
        assert r["given12"] == r["option12"] == [["genLevelCtrl", mtl, 12]]
        assert r["option12Off"] == r["option12ToggleOff"] == [["genLevelCtrl", mtl, 12]]  # off: a level move
        # A plain on: the module's own on (fade_on_time), the option set or not.
        assert r["option0On"] == r["option12On"] == [["genOnOff", "on", None]]
        assert r["option12Toggle"] == [["genOnOff", "toggle", None]]
        assert r["given12On"] == [["genLevelCtrl", mtl, 12]]     # a time in the message wins
        assert r["move"] == [["genLevelCtrl", "move", None]]    # a rate: unchanged
    s = out["1.0.7"]                                          # stock: zhc's frames as they are
    assert s["brightness"] == s["on100"] == s["emptyOption"] == [["genLevelCtrl", mtl, 0]]
    assert s["step"] == [["genLevelCtrl", "step", 0]]
    assert s["given12"] == s["option12"] == [["genLevelCtrl", mtl, 12]]
    assert s["on"] == s["option0On"] == s["option12On"] == [["genOnOff", "on", None]]
    assert s["option12Toggle"] == [["genOnOff", "toggle", None]]
    assert s["option12Off"] == [["genLevelCtrl", mtl, 12]]


def test_after_an_off_with_a_time_a_plain_on_is_still_an_on():
    """zhc marks an off with a time ("turned off with a transition") and turns the next plain
    on into a level move to the level it remembered, a time given or not. The plain on goes
    out as genOnOff on all the same (the module keeps the level over an off with a time) and
    the mark is cleared; a brightness after it as zhc sends it."""
    out = json.loads(run_node(SEND + """
(async () => {
    const r = {};
    for (const v of ["2.1.14 own", "1.0.7"]) {
        const seq = async (steps) => {
            h.globalStore.clear();
            const dev = h.device(); dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v;
            const got = [];
            for (const [message, options, state] of steps) {
                const key = Object.keys(message).find((k) => k !== "transition");
                h.log.length = 0;
                await tzFor(key, "l1").convertSet(dev.getEndpoint(1), key, message[key],
                    {device: dev, message, options, state, endpoint_name: "l1", mapped: model});
                got.push(h.log.filter((l) => l[0] === "command").map((l) => [l[2], l[3], l[4].level ?? null, l[4].transtime ?? null]));
            }
            return {got, mark: h.globalStore.getValue(dev.getEndpoint(1), "turnedOffWithTransition") ?? null};
        };
        const lit = {state: "ON", brightness: 120}, dark = {state: "OFF", brightness: 120};
        r[v] = {
            message: await seq([[{state: "OFF", transition: 2}, {}, lit], [{state: "ON"}, {}, dark]]),
            option: await seq([[{state: "OFF"}, {transition: 2}, lit], [{state: "ON"}, {transition: 2}, dark],
                               [{brightness: 60}, {transition: 2}, lit]]),
        };
    }
    console.log(JSON.stringify(r));
})();
"""))
    mtl = "moveToLevelWithOnOff"
    for v in ("2.1.14 own", "1.0.7"):
        assert out[v]["message"] == {"got": [[["genLevelCtrl", mtl, 0, 20]], [["genOnOff", "on", None, None]]],
                                     "mark": None}, v
        assert out[v]["option"]["got"] == [[["genLevelCtrl", mtl, 0, 20]], [["genOnOff", "on", None, None]],
                                           [["genLevelCtrl", mtl, 60, 20]]], v


def test_the_transition_option_says_what_it_does_here():
    out = json.loads(run_node(ZDMS + """
console.log(JSON.stringify(def.extend.flatMap((x) => x.options ?? []).filter((o) => o.name === "transition")
    .map((o) => ({type: o.type, access: o.access, min: o.value_min, step: o.value_step, description: o.description}))));
"""))
    assert len(out) == 1
    o = out[0]
    assert (o["type"], o["access"], o["min"], o["step"]) == ("numeric", 2, 0, 0.1)
    for words in ("of brightness changes and off", "A plain on (no brightness) uses the module's fade_on_time",
                  "Empty: the module's fade settings", "brightness_fade_time while lit",
                  "the whole range in 2 s", "0: at once", "factory MCU", "wall switch"):
        assert words in o["description"], words
    assert "brightness_transition" not in o["description"]



def test_brightness_fade_time_is_an_open_dimmer_2_1_14_setting_in_seconds():
    out = json.loads(run_node(SET.replace('"2.2.0 own"', '"2.1.14 own"') + """
const ver = (v) => { const d = h.device(); d.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v; return d; };
(async () => console.log(JSON.stringify({
    set12: await set("brightness_fade_time", 1.2, 2),
    set0: await set("brightness_fade_time", 0),
    unset: [await set("brightness_fade_time", "default"), await set("brightness_fade_time", ""),
            await set("brightness_fade_time", -1)],
    bad: await set("brightness_fade_time", 61),
    reports: [fz(ver("2.1.14 own"), 1, {[0xff12]: 25}), fz(ver("2.1.14 own"), 2, {[0xff12]: 0xffff}),
              fz(ver("2.1.13 own"), 1, {[0xff12]: 25}), fz(ver("1.0.7"), 1, {[0xff12]: 25})],
    presets: def.extend.flatMap((x) => x.exposes ?? []).flatMap((x) => (typeof x === "function" ? x(ver("2.1.14 own"), {}) : [x]))
        .filter((x) => x.name === "brightness_fade_time").map((x) => [x.endpoint, x.value_min, x.value_max, x.value_step, x.unit, x.presets.map((p) => p.name), x.description]),
})))();
"""))
    w = lambda ep, v: [["write", ep, "genLevelCtrl", {str(0xff12): {"value": v, "type": 0x21}}]]
    assert out["set12"] == [w(2, 12), {"brightness_fade_time": 1.2}]
    assert out["set0"] == [w(1, 0), {"brightness_fade_time": 0}]
    assert out["unset"] == [[w(1, 0xFFFF), {"brightness_fade_time": None}]] * 3
    assert "0..60" in out["bad"]
    assert out["reports"] == [[{"brightness_fade_time_l1": 2.5}],
                              [{"brightness_fade_time_l2": None}], [], []]
    assert [p[:6] for p in out["presets"]] == [["l1", 0, 60, 0.1, "s", ["default"]],
                                               ["l2", 0, 60, 0.1, "s", ["default"]]]
    for words in ("as the time for the whole range", "Empty: the module's default, the whole range in 2 s",
                  "A transition (in the message or the device option transition) wins", "Open Dimmer 2.1.14"):
        assert all(words in p[6] for p in out["presets"]), words
