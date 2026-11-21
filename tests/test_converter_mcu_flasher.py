"""The generated Z2M converter's SWD master controls of the ZDMS16-2
(romasku.mcuFlasher, src/zigbee/mcu_flasher.c): nothing at all unless the
device option mcu_flasher is show, then a job enum that writes Basic 0xff51
and a status text decoded from Basic 0xff50 (mcu_flasher_status_t)."""

import json
import struct

from generated_converter import run_node
from test_converter_own_mcu import ZDMS

FLASHER = ZDMS + """
const tz = (key) => def.extend.filter((x) => !x.fake).flatMap((x) => x.toZigbee ?? [])
    .find((t) => t.key.includes(key));
const set = async (dev, value, options) => {
    h.log.length = 0;
    try {
        await tz("mcu_flasher").convertSet(dev.getEndpoint(1), "mcu_flasher", value, {device: dev, mapped: model, options});
        return h.log.filter((l) => l[0] === "write");
    } catch (error) {
        return error.message;
    }
};
const flasherShown = (dev, options) => shown(dev, options).filter((n) => n.startsWith("mcu_flasher"));
"""


def status(**fields) -> list[int]:
    """The attribute's octet string content (mcu_flasher_status_t without its length byte)."""
    head = [2, fields.get("state", 2), fields.get("job", 2), fields.get("step", 7),
            fields.get("error", 0), fields.get("swd_err", 0), fields.get("boot_version", 0),
            fields.get("app_version", 0)]
    raw = struct.pack("<8B5IHH3IHB", *head,
                      *(fields.get(k, 0) for k in ("dpidr", "ap_idr", "cpuid", "idcode", "optr")),
                      fields.get("wrpr", 0), fields.get("app_build", 0),
                      *(fields.get(k, 0) for k in ("option_crc", "rom_crc", "sector0_crc")),
                      fields.get("duration_ms", 1234), fields.get("page", 0))
    assert len(raw) == 47
    return list(raw)


def test_nothing_is_shown_unless_the_option_says_show():
    out = json.loads(run_node(FLASHER + """
const ver = (v) => { const d = h.device(); d.getEndpoint(1).clusters.genBasic.attributes[0xff12] = v; return d; };
console.log(JSON.stringify({
    stock: flasherShown(ver("1.0.7"), {}),
    own: flasherShown(ver("2.2.2 own"), {}),
    hidden: flasherShown(ver("2.2.2 own"), {mcu_flasher: "hide"}),
    shownStock: flasherShown(ver("1.0.7"), {mcu_flasher: "show"}),
    shownOwn: flasherShown(ver("2.2.2 own"), {mcu_flasher: "show", open_dimmer_settings: "hide"}),
}));
"""))
    assert out["stock"] == out["own"] == out["hidden"] == []
    # the stock MCU is the one a probe-free install is for: shown whatever the MCU is
    assert out["shownStock"] == out["shownOwn"] == ["mcu_flasher", "mcu_flasher_confirm", "mcu_flasher_status"]


def test_a_job_is_one_write_of_the_control_attribute():
    out = json.loads(run_node(FLASHER + """
(async () => {
    const dev = h.device();
    console.log(JSON.stringify({
        hidden: await set(dev, "survey_full", {}),
        dp: await set(dev, "survey_dp", {mcu_flasher: "show"}),
        full: await set(dev, "survey_full", {mcu_flasher: "show"}),
        abort: await set(dev, "abort", {mcu_flasher: "show"}),
        bad: await set(dev, "flash_everything", {mcu_flasher: "show"}),
    }));
})();
"""))
    assert "show" in out["hidden"]
    assert out["dp"] == [["write", 1, "genBasic", {"65361": {"value": 1, "type": 0x20}}]]
    assert out["full"] == [["write", 1, "genBasic", {"65361": {"value": 2, "type": 0x20}}]]
    assert out["abort"] == [["write", 1, "genBasic", {"65361": {"value": 255, "type": 0x20}}]]
    assert "unknown job" in out["bad"]


def decode(raw: list[int], options: str = '{mcu_flasher: "show"}', endpoint: int = 1):
    """mcu_flasher_status as published (mcu_firmware's install note: test below)."""
    return json.loads(run_node(FLASHER + f"""
console.log(JSON.stringify(fz(h.device(), {endpoint}, {{[0xff50]: {json.dumps(raw)}}}, {options})
    .filter((r) => r.mcu_flasher_status !== undefined)));
"""))


def test_a_full_survey_is_shown_as_text():
    out = decode(status(dpidr=0x0BC11477, ap_idr=0x04770031, cpuid=0x410CC601, idcode=0x60001000,
                        optr=0xF9AA, wrpr=0xFFFF, option_crc=0x11223344, rom_crc=0xA52232EA,
                        sector0_crc=0x7D215316, boot_version=2, app_version=0xA3, app_build=800))
    assert out == [{"mcu_flasher_status":
                    "done survey_full; DPIDR 0x0BC11477; CPUID 0x410CC601, IDCODE 0x60001000, AP 0x04770031; "
                    "OPTR 0x0000F9AA (RDP 0), WRPR 0x0000FFFF, option bytes CRC 0x11223344; "
                    "ROM CRC 0xA52232EA; sector 0 CRC 0x7D215316; bootloader v2; "
                    "app 2.2.3 build 800; 1.2 s"}]


def test_running_failed_and_hidden():
    running = decode(status(state=1, job=1, step=1))
    failed = decode(status(state=3, job=2, step=1, error=2, swd_err=1))
    assert running == [{"mcu_flasher_status": "running survey_dp; step 1"}]
    assert failed == [{"mcu_flasher_status":
                       "failed survey_full; error: no answer (wires? power?) (SWD 1); 1.2 s"}]
    assert decode(status(), options="{}") == []
    assert decode(status(), endpoint=2) == []
    assert decode(status()[:40]) == []  # too short: not this layout
    assert decode([1] + status()[1:]) == []  # format 1 (80 bytes, never sent again)


SET_ANY = FLASHER + """
const setKey = async (dev, key, value, options) => {
    h.log.length = 0;
    try {
        await tz(key).convertSet(dev.getEndpoint(1), key, value, {device: dev, mapped: model, options});
        return h.log.filter((l) => l[0] === "write");
    } catch (error) {
        return error.message;
    }
};
"""


def test_update_jobs_and_the_confirm_text():
    out = json.loads(run_node(SET_ANY + """
(async () => {
    const dev = h.device(); const show = {mcu_flasher: "show"};
    console.log(JSON.stringify({
        confirm: await setKey(dev, "mcu_flasher_confirm", "update_bootloader", show),
        confirmHidden: await setKey(dev, "mcu_flasher_confirm", "update_bootloader", {}),
        tooLong: await setKey(dev, "mcu_flasher_confirm", "x".repeat(24), show),
        update: await setKey(dev, "mcu_flasher", "update_bootloader", show),
        force: await setKey(dev, "mcu_flasher", "update_bootloader_force", show),
    }));
})();
"""))
    assert out["confirm"] == [["write", 1, "genBasic", {"65362": {"value": "update_bootloader", "type": 0x42}}]]
    assert "show" in out["confirmHidden"] and "23" in out["tooLong"]
    assert out["update"] == [["write", 1, "genBasic", {"65361": {"value": 3, "type": 0x20}}]]
    assert out["force"] == [["write", 1, "genBasic", {"65361": {"value": 4, "type": 0x20}}]]


def test_an_update_shows_its_page_and_its_errors():
    writing = decode(status(state=1, job=3, step=11, page=12))
    page0 = decode(status(state=1, job=4, step=11, page=0))
    refused = decode(status(state=0, job=3, step=0, error=7))
    verify = decode(status(state=3, job=3, step=12, error=11))
    assert writing == [{"mcu_flasher_status": "running update_bootloader; writing page 12/32"}]
    assert page0 == [{"mcu_flasher_status": "running update_bootloader_force; writing page 32/32"}]
    assert "write the job's name to mcu_flasher_confirm" in refused[0]["mcu_flasher_status"]
    assert "stays stopped" in verify[0]["mcu_flasher_status"]


def test_install_and_its_armed_state():
    out = json.loads(run_node(SET_ANY + """
(async () => {
    const dev = h.device(); const show = {mcu_flasher: "show"};
    console.log(JSON.stringify({install: await setKey(dev, "mcu_flasher", "install", show)}));
})();
"""))
    assert out["install"] == [["write", 1, "genBasic", {"65361": {"value": 5, "type": 0x20}}]]
    armed = decode(status(state=5, job=5, step=0, page=3))
    assert armed == [{"mcu_flasher_status":
                      "armed install; power-cycle the module now (3 power-ups left); 1.2 s"}]
    resumed = decode(status(state=5, job=5, step=0, page=255))   # past its option bytes
    assert resumed == [{"mcu_flasher_status": "armed install; power-cycle the module now "
                        "(the install goes on at every power-up); 1.2 s"}]
    wrong = decode(status(state=0, job=5, step=0, error=12))
    assert "not a factory MCU" in wrong[0]["mcu_flasher_status"]
    # abort past the option bytes with a silent MCU: the job stays armed, and the status says so
    kept = decode(status(state=5, job=5, step=11, error=16, page=255))
    assert kept == [{"mcu_flasher_status": "armed install; power-cycle the module now "
                     "(the install goes on at every power-up); error: kept: the MCU may be without "
                     "firmware, so its install stays armed (it goes on at the next power-up; abort "
                     "drops it once the MCU answers); 1.2 s"}]


FIRMWARE = FLASHER + """
const firmware = (dev, data) => fz(dev, 1, data, {}).map((r) => r.mcu_firmware).filter((x) => x !== undefined);
"""


def test_mcu_firmware_tells_an_install_whatever_the_option():
    """An install by the SWD master shows in mcu_firmware (device option mcu_flasher left
    hidden) while the MCU is not Open Dimmer: waiting for a power cycle, running, failed;
    kept in device.meta.mcuInstall so a later version report keeps it; Open Dimmer wins."""
    armed = status(state=5, job=5, step=0, error=1, page=3)
    running = status(state=1, job=5, step=11)
    done = status(state=2, job=5)
    failed = status(state=3, job=5, error=14)
    survey = status(state=1, job=2, step=4)
    out = json.loads(run_node(FIRMWARE + f"""
const dev = h.device({{meta: {{mcuVersion: "1.0.7"}}}});
const steps = [];
for (const [what, data] of [
    ["armed", {{[0xff50]: {json.dumps(armed)}}}],
    ["version", {{[0xff12]: "1.0.7"}}],
    ["running", {{[0xff50]: {json.dumps(running)}}}],
    ["done", {{[0xff50]: {json.dumps(done)}}}],
    ["survey", {{[0xff50]: {json.dumps(survey)}}}],
    ["failed", {{[0xff50]: {json.dumps(failed)}}}],
    ["own", {{[0xff12]: "2.2.3 own"}}],
]) {{
    steps.push([what, firmware(dev, data), dev.meta.mcuInstall ?? null]);
}}
const blank = h.device();
const own = h.device({{meta: {{mcuVersion: "2.2.3 own"}}}});
console.log(JSON.stringify({{steps, saves: dev.saves,
    blank: firmware(blank, {{[0xff50]: {json.dumps(armed)}}}),
    own: firmware(own, {{[0xff50]: {json.dumps(failed)}}}),
    restart: firmware(h.device({{meta: {{mcuVersion: "1.0.7", mcuInstall: "x"}}}}), {{[0xff12]: ""}}),
}}));
"""))
    wait = "power-cycle the module to install Open Dimmer"
    fail = "Open Dimmer install failed (device option mcu_flasher: show tells why)"
    assert out["steps"] == [
        ["armed", [f"Tuya (stock): {wait}"], wait],
        ["version", [f"Tuya (stock): {wait}"], wait],   # a version report keeps it
        ["running", ["Tuya (stock): installing Open Dimmer"], "installing Open Dimmer"],
        ["done", ["Tuya (stock)"], None],
        ["survey", ["Tuya (stock)"], None],               # other jobs: no note
        ["failed", [f"Tuya (stock): {fail}"], fail],
        ["own", ["Open Dimmer"], fail],                    # Open Dimmer wins over a kept note
    ]
    assert out["saves"] == 5          # when the note changes (4), and the kept version once (own)
    assert out["blank"] == [wait]                          # MCU version not known (erased)
    assert out["own"] == ["Open Dimmer"]
    assert out["restart"] == []                            # "" after a restart: nothing, as before


CONFIGURE = FLASHER + """
const run = async (opts) => {
    h.log.length = 0;
    const dev = h.device(opts);
    await def.configure(dev, {ID: 1}, console);
    return {basicBind: h.log.some((e) => e[0] === "bind" && e[1] === 1 && e[2].includes("genBasic")),
            statusRead: h.log.some((e) => e[0] === "read" && e[1] === 1 && e[3].includes(0xff50))};
};
"""


def test_configure_binds_basic_for_the_swd_master_with_a_stock_mcu():
    """With the stock MCU, configure reads the master's status (mcu_firmware tells a waiting
    install) and, if the module has the master, binds the Basic cluster: its status reports
    and the Open Dimmer version after an install then reach Zigbee2MQTT without another
    configure. A module without the master: no bind, as before. Open Dimmer: as before."""
    out = json.loads(run_node(CONFIGURE + f"""
(async () => console.log(JSON.stringify({{
    master: await run({{versions: ["1.0.7"], flasher: {json.dumps(status(state=5, job=5, page=3))}}}),
    none: await run({{versions: ["1.0.7"]}}),
    own: await run({{versions: ["2.2.3 own"], flasher: {json.dumps(status())}}}),
}})))();
"""))
    assert out["master"] == {"basicBind": True, "statusRead": True}
    assert out["none"] == {"basicBind": False, "statusRead": True}
    assert out["own"] == {"basicBind": True, "statusRead": False}
