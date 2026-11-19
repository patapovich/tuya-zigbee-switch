"""The conversion of a stock AVATTO ZDMS16-2 with the fork's converter alone: the stock
definition gets OTA for the fork's boards' stock manufacturer names (so Zigbee2MQTT offers the
-from_tuya image without tuya_with_ota.js), and configure interviews the converted module first
when Zigbee2MQTT still holds the stock firmware's endpoints."""
import json

from generated_converter import run_node

STOCK = """
const stock = definitions.filter((d) => d.model === "ZDMS16-2" && d.description === "Zigbee Module 2 channels Dimmer");
"""


def test_the_stock_module_gets_ota_for_the_boards_stock_names_only():
    out = json.loads(run_node(STOCK + """
console.log(JSON.stringify(stock.map((d) => ({vendor: d.vendor, ota: d.ota, fingerprint: d.fingerprint,
    zigbeeModel: d.zigbeeModel ?? null, exposes: d.exposes, fromZigbee: d.fromZigbee}))));
"""))
    assert out == [{
        "vendor": "AVATTO", "ota": True, "zigbeeModel": None,
        # both ZDMS16-2 boards (device_db stock_manufacturer_name); not the other dimmers the
        # stock definition matches (here _TZE204_o9gyszw2)
        "fingerprint": [{"modelID": "TS0601", "manufacturerName": "_TZE284_jtbgusdc"},
                        {"modelID": "TS0601", "manufacturerName": "_TZE204_jtbgusdc"}],
        # everything else as the stock definition has it
        "exposes": ["stock exposes"], "fromZigbee": ["stock fz"],
    }]


def test_the_converted_module_is_still_the_forks_definition():
    """The fork's own definition comes first: a converted module (modelID TS0601-ZDMS16-2)
    never matches the stock one."""
    out = json.loads(run_node(STOCK + """
const own = definitions.find((d) => d.model === "ZDMS16-2");
console.log(JSON.stringify({ownFirst: own !== stock[0], ownZigbeeModel: own.zigbeeModel,
    index: [definitions.indexOf(own), definitions.indexOf(stock[0])]}));
"""))
    assert out["ownFirst"] and "TS0601-ZDMS16-2" in out["ownZigbeeModel"]
    assert out["index"][0] < out["index"][1]


CONFIGURE_FRESH = """
const def = definitions.find((d) => d.model === "ZDMS16-2");
const run = async (missing) => {
    h.log.length = 0;
    const dev = h.device({versions: ["2.2.3 own"], missing});
    await def.configure(dev, {ID: 1}, console);
    const first = (kind) => h.log.findIndex((e) => e[0] === kind);
    return {interview: h.log.filter((e) => e[0] === "interview"), firstInterview: first("interview"),
            firstRead: first("read"), binds2: h.log.filter((e) => e[0] === "bind" && e[1] === 2).length};
};
"""


def test_configure_interviews_first_when_endpoint_2_is_missing():
    """Right after the conversion Zigbee2MQTT's database can still have endpoints 1 and 242:
    configure interviews the module before it reads or binds anything, then binds endpoint 2."""
    out = json.loads(run_node(CONFIGURE_FRESH + """
(async () => console.log(JSON.stringify(await run([2]))))();
"""))
    assert out["interview"] == [["interview", True]]
    assert out["firstInterview"] < out["firstRead"]
    assert out["binds2"] >= 1


def test_configure_does_not_interview_a_known_module():
    out = json.loads(run_node(CONFIGURE_FRESH + """
(async () => console.log(JSON.stringify(await run([]))))();
"""))
    assert out["interview"] == [] and out["binds2"] >= 1
