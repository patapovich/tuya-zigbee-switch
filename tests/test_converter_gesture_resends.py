"""The wall switch gestures (genMultistateInput presentValue = sequence << 4 | gesture) and the
module's resends of a report (src/telink/patch_sdk/zigbee/zcl/zcl_reporting.c.patch: reports ask
for an APS ACK; the gestures' resends are kept, each one is an event). A resend can reach
Zigbee2MQTT as a copy (the hub's ACK was lost) or late (after a newer gesture: a hold lost on a
hop, the release delivered, then the hold again). The converter takes neither as a new gesture:
within 10 s of the last one it took, the same value again or a sequence 1..7 behind it is
dropped; after that any value counts (the sequence starts over at a module restart). Per
endpoint. On the ZDMS16-2 (Open Dimmer 2.2) and the TS110E (the virtual MCU)."""
import json

import pytest
from generated_converter import run_node
from test_converter_own_mcu import ZDMS

SCRIPT = ZDMS + """
const pick = (model) => definitions.find((d) => d.model === model);
let now = 1000000;
Date.now = () => now;
const gestures = (modelName, steps) => {
    h.globalStore.clear();
    const d = pick(modelName);
    const dev = h.device();
    dev.getEndpoint(1).clusters.genBasic.attributes[0xff12] = modelName === "ZDMS16-2" ? "2.2.0 own" : "1.0.0";
    const ep = (id) => ({ID: id, deviceIeeeAddress: dev.ieeeAddr});
    return steps.map(([after, id, value]) => {
        now += after;
        return d.extend.filter((x) => !x.fake).flatMap((x) => x.fromZigbee ?? [])
            .filter((f) => f.cluster === "genMultistateInput")
            .map((f) => f.convert({endpoint: () => ({l1: 1, l2: 2})},
                                  {device: dev, endpoint: ep(id), data: {presentValue: value}},
                                  () => {}, {}, {device: dev}))
            .filter((r) => r !== undefined).map((r) => r.action);
    });
};
"""

MODELS = ["ZDMS16-2", "TS110E_2gang_2"]


def run(model: str, steps: list[tuple[int, int, int]]) -> list[list[str]]:
    return json.loads(run_node(SCRIPT + f"""
console.log(JSON.stringify(gestures({json.dumps(model)}, {json.dumps(steps)})));
"""))


@pytest.mark.parametrize("model", MODELS)
def test_a_copy_of_a_gesture_is_not_a_new_one(model):
    """The hub's ACK lost: the module sends the same frame again 1-2 s later, up to 3 times."""
    assert run(model, [(0, 1, 0x11), (1500, 1, 0x11), (2000, 1, 0x11), (2000, 1, 0x11)]) == [
        ["single_l1"], [], [], []]


@pytest.mark.parametrize("model", MODELS)
def test_a_late_hold_after_its_release_is_dropped(model):
    """The hold lost on a hop past the first, the release delivered, the hold's resend after it:
    the automation sees hold then release, never a hold that does not end."""
    assert run(model, [(0, 2, 0x25), (1500, 2, 0x14)]) == [["release_l2"], []]
    # 7 behind (the most a resend can be: far fewer gestures fit in its ~7 s)
    assert run(model, [(0, 1, 0x81), (500, 1, 0x11)]) == [["single_l1"], []]


@pytest.mark.parametrize("model", MODELS)
def test_gestures_in_order_are_all_taken_over_the_wrap(model):
    steps = [(300, 1, (seq << 4) | 1) for seq in (13, 14, 15, 0, 1, 2)]
    assert run(model, steps) == [["single_l1"]] * 6
    # the same sequence with another gesture is no copy
    assert run(model, [(0, 1, 0x34), (300, 1, 0x35)]) == [["hold_l1"], ["release_l1"]]


@pytest.mark.parametrize("model", MODELS)
def test_after_10_s_any_value_counts(model):
    """A module restart starts the sequence over (its first gesture is 1): taken once the last
    gesture is 10 s old, even 4 behind or the same value."""
    assert run(model, [(0, 1, 0x51), (10001, 1, 0x11), (10001, 1, 0x11)]) == [
        ["single_l1"], ["single_l1"], ["single_l1"]]
    assert run(model, [(0, 1, 0x51), (9999, 1, 0x11)]) == [["single_l1"], []]


@pytest.mark.parametrize("model", MODELS)
def test_each_endpoint_has_its_own(model):
    assert run(model, [(0, 1, 0x14), (100, 2, 0x14), (100, 2, 0x14), (100, 1, 0x25)]) == [
        ["hold_l1"], ["hold_l2"], [], ["release_l1"]]


def test_the_template_says_why():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "helper_scripts" / "templates" /
            "switch_custom.js.jinja").read_text()
    block = text[text.index("dimmerGestures: (dimmerNames"):]
    block = block[:block.index("dimmerOnLevelRefresh")]
    assert "globalStore.getValue(msg.endpoint, \"dimmerGestureLast\")" in block
    assert "now - last.at < 10000" in block and "behind >= 1 && behind <= 7" in block
