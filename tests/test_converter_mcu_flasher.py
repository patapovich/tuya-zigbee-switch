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
    head = [1, fields.get("state", 2), fields.get("job", 2), fields.get("step", 7),
            fields.get("progress", 100), fields.get("error", 0), fields.get("swd_err", 0),
            fields.get("rdp", 0xAA)]
    words = [fields.get(k, 0) for k in (
        "dpidr", "ap_idr", "cpuid", "idcode", "optr", "sdkr", "wrpr", "option_crc", "uid_crc",
        "rom_crc", "sector0_crc", "boot_id", "boot_info", "app_magic", "app_size", "app_crc",
        "app_info")]
    raw = struct.pack("<8B17I2H", *head, *words, fields.get("started_ms", 45),
                      fields.get("duration_ms", 1234))
    assert len(raw) == 80
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
    assert out["shownStock"] == out["shownOwn"] == ["mcu_flasher", "mcu_flasher_status"]


def test_a_job_is_one_write_of_the_control_attribute():
    out = json.loads(run_node(FLASHER + """
(async () => {
    const dev = h.device();
    console.log(JSON.stringify({
        hidden: await set(dev, "survey_full", {}),
        dp: await set(dev, "survey_dp", {mcu_flasher: "show"}),
        full: await set(dev, "survey_full", {mcu_flasher: "show"}),
        abort: await set(dev, "abort", {mcu_flasher: "show"}),
        bad: await set(dev, "install", {mcu_flasher: "show"}),
    }));
})();
"""))
    assert "show" in out["hidden"]
    assert out["dp"] == [["write", 1, "genBasic", {"65361": {"value": 1, "type": 0x20}}]]
    assert out["full"] == [["write", 1, "genBasic", {"65361": {"value": 2, "type": 0x20}}]]
    assert out["abort"] == [["write", 1, "genBasic", {"65361": {"value": 255, "type": 0x20}}]]
    assert "unknown job" in out["bad"]


def decode(raw: list[int], options: str = '{mcu_flasher: "show"}', endpoint: int = 1):
    return json.loads(run_node(FLASHER + f"""
console.log(JSON.stringify(fz(h.device(), {endpoint}, {{[0xff50]: {json.dumps(raw)}}}, {options})));
"""))


def test_a_full_survey_is_shown_as_text():
    out = decode(status(dpidr=0x0BC11477, ap_idr=0x04770031, cpuid=0x410CC601, idcode=0x60001000,
                        optr=0xF9AA, sdkr=0x1F, wrpr=0xFFFF, option_crc=0x11223344,
                        rom_crc=0xA52232EA, sector0_crc=0x7D215316, boot_id=0x4C42444F,
                        boot_info=0xFFFF0F02, app_magic=0x5A445041, app_size=13288,
                        app_info=(700 << 16) | (0x18 << 8) | 0xA2))
    assert out == [{"mcu_flasher_status":
                    "done survey_full; DPIDR 0x0BC11477; CPUID 0x410CC601, IDCODE 0x60001000, AP 0x04770031; "
                    "OPTR 0x0000F9AA (RDP 0), WRPR 0x0000FFFF, option bytes CRC 0x11223344; "
                    "ROM CRC 0xA52232EA; sector 0 CRC 0x7D215316; bootloader v2; "
                    "app 2.2.2 build 700 (13288 B); 1.2 s"}]


def test_running_failed_and_hidden():
    running = decode(status(state=1, job=1, step=1, progress=0, rdp=0))
    failed = decode(status(state=3, job=2, step=1, error=2, swd_err=1, rdp=0))
    assert running == [{"mcu_flasher_status": "running survey_dp; step 1, 0 %"}]
    assert failed == [{"mcu_flasher_status":
                       "failed survey_full; error: no answer (wires? power?) (SWD 1); 1.2 s"}]
    assert decode(status(), options="{}") == []
    assert decode(status(), endpoint=2) == []
    assert decode(status()[:40]) == []  # too short: not this layout
