"""The TS110E's converter (the ZH-Z06 dimmers' virtual MCU, src/zigbee/hk_dimmer.c, version
"1.0.0"): its transitions (a brightness without a transition goes out as 0xFFFF, "none", so the
module fades by its settings instead of jumping), end to end on the stub module, and what
configure reads (every setting the page shows, nothing the build does not have)."""
import json

from generated_converter import run_node
from test_converter_stock_uart import zcl
from test_hk_dimmer import MIN, falling, hk, hk_out, ramps, rising, run_t, zcl_value
from test_hk_dimmer import start as hk_start

TS110E = """
const def = definitions.find((d) => d.extend && d.model === "TS110E_2gang_2");
const model = {endpoint: () => ({l1: 1, l2: 2})};
const tzFor = (key) => def.extend.flatMap((x) => x.toZigbee ?? [])
    .find((t) => t.key?.includes(key) && (!t.endpoints || t.endpoints.includes("l1")));
const run = async (version, steps, options = {}) => {
    h.globalStore.clear();
    const dev = h.device();
    if (version !== undefined) dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = version;
    const out = [];
    for (const [message, state] of steps) {
        const key = Object.keys(message).find((k) => k !== "transition");
        h.log.length = 0;
        await tzFor(key).convertSet(dev.getEndpoint(1), key, message[key],
            {device: dev, message: {...message}, options, state, endpoint_name: "l1", mapped: model});
        out.push(h.log.filter((l) => l[0] === "command").map((l) => [l[2], l[3], l[4]]));
    }
    return out;
};
"""

DARK = {"state": "OFF", "brightness": 100}
LIT = {"state": "ON", "brightness": 200}


def converter(version, steps, options=None):
    return json.loads(run_node(TS110E + f"""
(async () => console.log(JSON.stringify(await run({json.dumps(version)}, {json.dumps(steps)},
                                                  {json.dumps(options or {})}))))();
"""))


def test_a_brightness_without_a_transition_goes_out_as_none():
    """"1.0.0" is a real version that is not Open Dimmer's, yet the module fades by itself: no
    0 ("at once") for "no transition", whatever the version; a given time as zhc makes it; a
    plain on as genOnOff (the module's fade_on_time to the level it kept)."""
    steps = [[{"brightness": 200}, DARK], [{"brightness": 60}, LIT], [{"brightness_step": -20}, LIT],
             [{"state": "OFF"}, LIT], [{"state": "ON"}, DARK]]
    for version in ("1.0.0", None):
        out = converter(version, steps)
        assert [c[2].get("transtime") for m in out for c in m] == [0xFFFF] * 3 + [None, None], version
        assert [m[0][:2] for m in out[3:]] == [["genOnOff", "off"], ["genOnOff", "on"]]
    timed = converter("1.0.0", steps, {"transition": 2})
    assert [c[2].get("transtime") for m in timed for c in m][:4] == [20, 20, 20, 20]
    assert timed[4][0][:2] == ["genOnOff", "on"]                    # a plain on: the module's own
    assert converter("1.0.0", [[{"brightness": 90, "transition": 0}, LIT]])[0][0][2]["transtime"] == 0


def test_the_transition_option_says_what_it_does_here():
    out = json.loads(run_node(TS110E + """
const o = def.extend.flatMap((x) => x.options ?? []).filter((x) => x.name === "transition");
console.log(JSON.stringify(o.map((x) => [x.value_min, x.value_max, x.description])));
"""))
    assert len(out) == 1
    low, high, text = out[0]
    assert (low, high) == (0, 600)
    assert "Open Dimmer" not in text and "brightness_fade_time while lit" in text and "module button" in text


def test_a_brightness_without_a_transition_fades_on_the_module():
    """End to end: the converter's commands on the stub module drive the dimming MCU in a fade
    (frames at ramp 0, from the minimum when dark), not in one jump."""
    cmds = converter("1.0.0", [[{"brightness": 200}, DARK], [{"brightness": 60}, LIT]])
    proc, device, _ = hk_start()
    try:
        out = []
        for message in cmds:
            for cluster, command, payload in message:
                device.call_zigbee_cmd(1, *zcl(cluster, command, payload))
            out.append([f for _, f in run_t(proc, device, 1500)])
    finally:
        proc.stop()
    up, down = hk(out[0]), hk(out[1])
    assert up[0] == MIN and rising(up) and len(up) > 10 and up[-1] == hk_out(zcl_value(200))
    assert falling(down) and len(down) > 5 and down[-1] == hk_out(zcl_value(60))
    assert all(r == b"\x00\x00" for r in ramps(out[0] + out[1]))


CONFIGURE = TS110E + """
const configure = async (versions) => {
    const dev = h.device({versions});
    h.log.length = 0;
    await def.configure(dev, {ID: 1}, def);
    const hex = (a) => (typeof a === "number" ? a.toString(16) : a);
    return h.log.filter((l) => l[0] === "read" || l[0] === "bind" ||
                               (l[0] === "report" && l[2] === "genMultistateInput"))
        .map((l) => (l[0] === "read" ? `read ${l[1]}:${l[2]}:${l[3].map(hex).join("+")}`
                     : l[0] === "bind" ? `bind ${l[1]}:${l[2].join("+")}`
                     : `report ${l[1]}:${l[2]}:${l[3].map((x) => `${x.attribute.ID.toString(16)}/${x.maximumReportInterval}`).join("+")}`));
};
"""


def test_configure_reads_every_setting_the_ts110e_shows():
    out = json.loads(run_node(CONFIGURE + """
(async () => console.log(JSON.stringify({
    online: await configure(["1.0.0"]),
    offline: await configure([new Error("timeout")]),
})))();
"""))
    assert out["online"] == [
        "read 1:genBasic:ff12",
        "read 1:genBasic:ff30+ff32+ff34+ff36+ff38",       # L1: ramp, fade on / off, hold, curve
        "read 1:genBasic:ff31+ff33+ff35+ff37+ff39",       # L2
        "read 1:genBasic:ff3a", "read 1:genBasic:ff3b",   # the custom curves, one each
        "read 1:genBasic:ff3c+ff3d+ff3e+ff3f",            # the slow zones (off L1, L2, hold L1, L2)
        "read 1:genBasic:ff59",                           # factory_identity
        "read 1:genBasic:ff5d",                           # nwk_command_security
        "bind 1:genBasic",                                # its report (tc_link_key_stale)
        *[call for ep in (1, 2) for call in (
            f"bind {ep}:genOnOff+genLevelCtrl",
            f"read {ep}:genOnOff:startUpOnOff", f"read {ep}:genLevelCtrl:onLevel",
            f"read {ep}:lightingBallastCfg:minLevel+maxLevel", f"read {ep}:genOnOffSwitchCfg:switchType",
            f"read {ep}:genLevelCtrl:options+ff11+ff12",          # execute_if_off, on_level_local,
                                                                  # brightness_fade_time
            f"read {ep}:genOnOff:ff10",                           # countdown
            f"read {ep}:genOnOffSwitchCfg:ff10+ff11",             # switch_mode, fast_press
            f"read {ep}:genLevelCtrl:startUpCurrentLevel",        # current_level_startup
            f"read {ep}:lightingBallastCfg:ff10+ff11",            # start_boost, lamp_compensation
            f"read {ep}:genOnOffSwitchCfg:ff15+ff16",             # long_press_from_off,
                                                                  # double_tap_full
            f"read {ep}:genOnOffSwitchCfg:ff12+ff13+ff14",        # the wall switch's bindings
            f"read {ep}:genOnOffSwitchCfg:ff17+ff18+ff19",        # follow
            # the gestures (action): each one an event, no periodic report of the last one
            f"bind {ep}:genMultistateInput", f"report {ep}:genMultistateInput:55/0")],
    ]
    # Not the ballast custom curve (Open Dimmer 2.2.8's: the module's own are Basic 0xff3a / 0xff3b);
    # one Basic bind, for nwk_command_security's report (no health report, no SWD master).
    assert not [c for c in out["online"] if "lightingBallastCfg:ff10+ff11+" in c]
    assert [c for c in out["online"] if "genBasic" in c and c.startswith("bind")] == ["bind 1:genBasic"]
    # Offline: the version read gives up, no setting of the virtual MCU is read, nothing bound.
    assert [c for c in out["offline"] if c.startswith("read 1:genBasic")] == ["read 1:genBasic:ff12"] * 2
    assert "bind 1:genBasic" not in out["offline"]


# ---- the settings of the virtual MCU's Open Dimmer paths (with any version) -------------------

HK_SET = TS110E + """
const ver = (v) => {
    const d = h.device();
    if (v !== undefined) d.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v;
    return d;
};
const dev = ver("1.0.0");
const tz = (key, ep) => def.extend.flatMap((x) => x.toZigbee ?? []).filter((t) => t.convertSet)
    .find((t) => t.key?.includes(key) && (!t.endpoints || t.endpoints.includes(`l${ep}`)));
const set = async (key, value, ep = 1) => {
    h.log.length = 0;
    try {
        const r = await tz(key, ep).convertSet(dev.getEndpoint(ep), key, value,
                                               {device: dev, mapped: model, options: {}});
        return [h.log.filter((l) => l[0] === "write" || l[0] === "read").map((l) => [l[0], l[1], l[2], l[3]]),
                r?.state ?? null];
    } catch (error) {
        return error.message;
    }
};
const exposes = (d) => def.extend.filter((x) => !x.fake).flatMap((x) => x.exposes ?? [])
    .flatMap((x) => (typeof x === "function" ? x(d, {}) : [x]));
const fz = (cluster, ep, data) => def.extend.filter((x) => !x.fake).flatMap((x) => x.fromZigbee ?? [])
    .filter((f) => f.cluster === cluster)
    .map((f) => f.convert(model, {device: dev, endpoint: {ID: ep}, data}, () => {}, {}, {device: dev}))
    .filter((r) => r !== undefined);
"""

HK_SETTINGS = ["execute_if_off", "on_level_local", "brightness_fade_time", "bind_mode",
               "double_press_action", "triple_press_action", "follow", "follow_on_level",
               "follow_off_level",
               # Open Dimmer 2.2.0's, which the virtual MCU has
               "countdown", "switch_mode", "fast_press", "current_level_startup", "start_boost",
               "power_on_behavior",
               # and 2.2.8's lamp compensation and switch options, its slow zones (the module's own)
               "lamp_compensation", "long_press_from_off", "double_tap_full", "fade_off_slow_zone",
               "hold_slow_zone"]


def hk_converter(script: str):
    return json.loads(run_node(HK_SET + script))


def test_the_ts110e_shows_its_settings_with_any_version_and_no_open_dimmer_ones():
    """The module's settings and Open Dimmer's that the virtual MCU has, with any version ("1.0.0"
    is no Open Dimmer's); not the ballast custom curve, no MCU firmware, health or SWD master; no
    text calls the chip Open Dimmer."""
    out = hk_converter("""
const names = (d) => exposes(d).map((x) => `${x.name}_${x.endpoint ?? "-"}`);
const opts = (d) => def.extend.flatMap((x) => x.options ?? []).map((x) => x.name);
console.log(JSON.stringify({
    v100: names(ver("1.0.0")), none: names(ver(undefined)),
    texts: exposes(dev).map((x) => [x.name, x.description ?? ""]),
    options: opts(dev),
    version: def.extend.filter((x) => x.fake === "text").map((x) => [x.args.name, x.args.description]),
    presses: def.extend.filter((x) => x.fake === "numeric" && x.args.name === "multi_press_reset_count")
        .map((x) => x.args.description),
}));
""")
    for names in (out["v100"], out["none"]):
        for name in HK_SETTINGS:
            assert f"{name}_l1" in names and f"{name}_l2" in names, name
        for name in ("identify_-", "action_-", "power_on_behavior_-"):
            assert name in names, name
        for name in ("mcu_firmware", "mcu_uptime", "mcu_flasher"):
            assert not any(n.startswith(name + "_") for n in names), name
    assert not [n for n, text in out["texts"] if "Open Dimmer firmware" in text or
                "Open Dimmer 2." in text]
    assert [n for n in out["v100"] if n.startswith("custom_curve_")] == ["custom_curve_l1", "custom_curve_l2"]
    assert "open_dimmer_settings" not in out["options"]
    version = dict(out["version"])["mcu_version"]       # (text(): zhc's own, a stand-in here)
    assert "virtual MCU" in version and "factory firmware" in version
    assert "Firmware version of the secondary MCU" not in version
    identity = dict(out["version"])["factory_identity"]   # the name the module kept; no MCU version
    assert "manufacturer name of its factory Zigbee firmware" in identity
    assert "tells no version" in identity and "factory MCU's version" not in identity
    [presses] = out["presses"]                         # the virtual MCU's count (as Open Dimmer's)
    assert "each within 10 s of the first" in presses and "1..7 act as 8" in presses
    texts = dict(out["texts"])
    double = texts["double_press_action"]              # the channel's own double tap: full
    assert "double_tap_full" in double and "toggles at each press" not in double
    assert "from the minimum to the maximum" in texts["hold_time"]     # min..max (profile_set)


def test_the_ts110e_action_has_every_gesture_with_any_version():
    out = hk_converter("""
const action = exposes(ver("1.0.0")).find((x) => x.name === "action");
console.log(JSON.stringify({
    values: action.values,
    got: [1, 2, 3, 4, 5, 6, 7].map((g) => fz("genMultistateInput", 2, {presentValue: 0x30 | g})),
}));
""")
    assert out["values"] == [f"{k}_{ch}" for ch in ("l1", "l2")
                             for k in ("single", "double", "triple", "hold", "release", "press")]
    assert out["got"] == [[{"action": f"{k}_l2"}] for k in
                          ("single", "double", "triple", "hold", "release", "press")] + [[]]


def test_the_ts110e_power_on_behavior_per_channel_and_for_both():
    """The device's power_on_behavior (the entity the converted units have) stays: written to
    both channels, it shows L1's; each channel has its own beside it."""
    out = hk_converter("""
(async () => {
    h.log.length = 0;
    const both = await tz("power_on_behavior", 1).convertSet(dev.getEndpoint(1), "power_on_behavior",
        "previous", {device: dev, mapped: model, options: {}});
    const writes = h.log.filter((l) => l[0] === "write").map((l) => [l[1], l[3]]);
    h.log.length = 0;
    const one = await tz("power_on_behavior", 2).convertSet(dev.getEndpoint(2), "power_on_behavior",
        "on", {device: dev, mapped: model, options: {}, endpoint_name: "l2"});
    const single = h.log.filter((l) => l[0] === "write").map((l) => [l[1], l[3]]);
    console.log(JSON.stringify({
        both: [both.state, writes], one: [one.state, single],
        reports: [fz("genOnOff", 1, {startUpOnOff: 255}), fz("genOnOff", 2, {startUpOnOff: 0})],
    }));
})();
""")
    assert out["both"] == [{"power_on_behavior": "previous", "power_on_behavior_l1": "previous",
                            "power_on_behavior_l2": "previous"},
                           [[1, {"startUpOnOff": 255}], [2, {"startUpOnOff": 255}]]]
    assert out["one"] == [{"power_on_behavior": "on"}, [[2, {"startUpOnOff": 1}]]]
    reports = [r for rs in out["reports"] for r in rs]
    assert {"power_on_behavior_l1": "previous", "power_on_behavior": "previous"} in reports
    assert {"power_on_behavior_l2": "off"} in reports


def test_the_ts110e_open_dimmer_settings_are_written_with_any_version():
    out = hk_converter("""
(async () => console.log(JSON.stringify([
    await set("countdown", 30, 2), await set("switch_mode", "detached"),
    await set("fast_press", true, 2), await set("current_level_startup", "minimum"),
    await set("start_boost", 0.25, 2), await set("lamp_compensation", 5, 2),
    await set("long_press_from_off", false), await set("double_tap_full", false, 2),
    await set("fade_off_slow_zone", 30, 2), await set("hold_slow_zone", 0),
    await set("hold_slow_zone", 101),
])))();
""")
    def w(ep, cluster, attr, value, kind):
        return [["write", ep, cluster, {str(attr): {"value": value, "type": kind}}]]
    assert out == [
        [w(2, "genOnOff", 0xFF10, 30, 0x23), {"countdown": 30}],
        [w(1, "genOnOffSwitchCfg", 0xFF10, 1, 0x30), {"switch_mode": "detached"}],
        [w(2, "genOnOffSwitchCfg", 0xFF11, 1, 0x10), {"fast_press": True}],
        [w(1, "genLevelCtrl", 0x4000, 0, 0x20), {"current_level_startup": 0}],
        [w(2, "lightingBallastCfg", 0xFF10, 250, 0x21), {"start_boost": 0.25}],
        [w(2, "lightingBallastCfg", 0xFF11, 5, 0x20), {"lamp_compensation": 5}],
        [w(1, "genOnOffSwitchCfg", 0xFF15, 0, 0x10), {"long_press_from_off": False}],
        [w(2, "genOnOffSwitchCfg", 0xFF16, 0, 0x10), {"double_tap_full": False}],
        [[["write", 1, "genBasic", {str(0xFF3D): {"value": 30, "type": 0x23}}]], {"fade_off_slow_zone": 30}],
        [[["write", 1, "genBasic", {str(0xFF3E): {"value": 0, "type": 0x23}}]], {"hold_slow_zone": 0}],
        "hold_slow_zone: an integer 0..100",
    ]


def test_brightness_fade_time_on_the_ts110e_shows_unset_as_unset_and_2_s_as_2_s():
    """Unset there is the virtual MCU's own rate (Open Dimmer's, 2 s for 0..1000), not 2 s for
    min_level..max_level: no fold of 2 to unset (as the ZDMS16-2's), unset published as null."""
    out = hk_converter("""
(async () => console.log(JSON.stringify({
    two: await set("brightness_fade_time", 2, 2),
    zero: await set("brightness_fade_time", 0),
    unset: [await set("brightness_fade_time", "default"), await set("brightness_fade_time", ""),
            await set("brightness_fade_time", -1)],
    bad: await set("brightness_fade_time", 61),
    reports: [fz("genLevelCtrl", 1, {[0xff12]: 0xffff}), fz("genLevelCtrl", 2, {[0xff12]: 20})],
    expose: exposes(dev).filter((x) => x.name === "brightness_fade_time")
        .map((x) => [x.endpoint, x.value_min, x.value_max, x.presets, x.description]),
})))();
""")
    w = lambda ep, v: [["write", ep, "genLevelCtrl", {str(0xff12): {"value": v, "type": 0x21}}]]
    assert out["two"] == [w(2, 20), {"brightness_fade_time": 2}]
    assert out["zero"] == [w(1, 0), {"brightness_fade_time": 0}]
    assert out["unset"] == [[w(1, 0xFFFF), {"brightness_fade_time": None}]] * 3
    assert "0..60" in out["bad"]
    reports = [r for rs in out["reports"] for r in rs]
    assert {"brightness_fade_time_l1": None} in reports and {"brightness_fade_time_l2": 2} in reports
    assert [x[:3] for x in out["expose"]] == [["l1", 0, 60], ["l2", 0, 60]]
    preset, text = out["expose"][0][3], out["expose"][0][4]
    assert [p["name"] for p in preset] == ["default"] and preset[0]["value"] < 0
    assert "the module's own rate, as Open Dimmer's, 2 s for the whole range" in text
    assert "share" not in text and "2 s is the module's default" not in text


def test_the_ts110e_settings_are_written_to_the_module():
    out = hk_converter("""
(async () => console.log(JSON.stringify([
    await set("execute_if_off", true, 2), await set("on_level_local", 200),
    await set("bind_mode", "remote", 2), await set("double_press_action", "off"),
    await set("triple_press_action", "scene_1"),
])))();
""")
    def w(ep, cluster, attr, value, kind):
        return [["write", ep, cluster, {str(attr): {"value": value, "type": kind}}]]
    assert out == [
        [w(2, "genLevelCtrl", 0x000F, 1, 0x18), {"execute_if_off": True}],
        [w(1, "genLevelCtrl", 0xFF11, 200, 0x20), {"on_level_local": 200}],
        [w(2, "genOnOffSwitchCfg", 0xFF12, 3, 0x30), {"bind_mode": "remote"}],
        [w(1, "genOnOffSwitchCfg", 0xFF13, 2, 0x30), {"double_press_action": "off"}],
        [w(1, "genOnOffSwitchCfg", 0xFF14, 3, 0x30), {"triple_press_action": "scene_1"}],
    ]


def test_a_min_or_max_level_write_reads_both_back():
    """The virtual MCU pushes the other limit along when they cross: both are read after a write,
    so the page shows what the module keeps (a ZDMS16-2 writes only)."""
    out = hk_converter("""
(async () => console.log(JSON.stringify([await set("min_level", 200, 2), await set("max_level", 50)])))();
""")
    assert out == [
        [[["write", 2, "lightingBallastCfg", {"minLevel": 200}],
          ["read", 2, "lightingBallastCfg", ["minLevel", "maxLevel"]]], {"min_level": 200}],
        [[["write", 1, "lightingBallastCfg", {"maxLevel": 50}],
          ["read", 1, "lightingBallastCfg", ["minLevel", "maxLevel"]]], {"max_level": 50}],
    ]
    zdms = json.loads(run_node("""
const def = definitions.find((d) => d.extend && d.model === "ZDMS16-2");
const t = def.extend.flatMap((x) => x.toZigbee ?? []).find((t) => t.key?.includes("min_level"));
(async () => {
    const dev = h.device();
    h.log.length = 0;
    await t.convertSet(dev.getEndpoint(1), "min_level", 100, {device: dev, options: {}});
    console.log(JSON.stringify(h.log.map((l) => l[0])));
})();
"""))
    assert zdms == ["write"]


def test_the_ts110e_device_config_must_keep_its_hk_token():
    """Without the HK token the module would send Tuya frames to the factory dimming MCU (it
    takes level frames only): the light stops working with no error. The converter refuses
    such a config; with the token any other change is checked as before."""
    out = hk_converter("""
const cfg = def.extend.find((x) => x.fake === "text" && x.args.name === "device_config").args;
const check = (v) => { try { cfg.validate(v); return "ok"; } catch (e) { return e.message; } };
console.log(JSON.stringify({
    keep: check("3mpwqzuu;TS110E-ZH-Z06;DM2;WB1B7;Y9600;HKB4C0C3C2;"),
    moved: check("3mpwqzuu;TS110E-ZH-Z06;DM2;WB1B7;Y9600;HKB5C0C3C2;"),
    without: check("3mpwqzuu;TS110E-ZH-Z06;DM2;WB1B7;Y9600;"),
    badpin: check("3mpwqzuu;TS110E-ZH-Z06;DM2;WB1B7;Y9600;HKB4C0C3Z9;"),
    description: cfg.description,
    definition: def.description,
}));
""")
    assert out["keep"] == "ok" and out["moved"] == "ok"
    assert "Keep the HK token" in out["without"]
    assert "Pin Z9 is invalid" in out["badpin"]
    assert "Keep its HK token" in out["description"]
    assert out["definition"].startswith("Custom dimmer")


def test_the_zdms16_2_device_config_needs_no_hk_token():
    out = json.loads(run_node("""
const def = definitions.find((d) => d.model === "ZDMS16-2" && d.zigbeeModel);
const cfg = def.extend.find((x) => x.fake === "text" && x.args.name === "device_config").args;
let r = "ok";
try { cfg.validate("jtbgusdc;TS0601-ZDMS16-2;DM2;WB1B7;Y9600;"); } catch (e) { r = e.message; }
console.log(JSON.stringify({r, description: cfg.description, definition: def.description}));
"""))
    assert out == {"r": "ok", "description": "Current configuration of the device",
                   "definition": "Custom switch (https://github.com/romasku/tuya-zigbee-switch)"}
