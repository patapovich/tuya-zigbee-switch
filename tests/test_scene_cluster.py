"""Scenes server of the dimmer endpoints (src/zigbee/scene_cluster.c, DIMMER_SCENES): Add / View /
Remove / Remove All / Store / Recall / Get Scene Membership and their Enhanced forms; group 0
always valid, another group only for a member endpoint (INVALID_FIELD); one table of 16 entries
for every endpoint, kept in NV; a scene holds On/Off and level; a recall makes the writes of the
command that sets that state (MoveToLevelWithOnOff, On, MoveToLevel), with the scene's time (0:
the fade settings) or the command's; no response to a group command."""
import pytest
from conftest import ZCLCommandEvent, wait_for
from own_mcu import own_mcu_contact
from test_dimmer_mcu import ack_writes, level, onoff, start
from tuya_mcu import take_tx

SCENES, ON_OFF, LEVEL = 0x0005, 0x0006, 0x0008
ADD, VIEW, REMOVE, REMOVE_ALL, STORE, RECALL, MEMBERSHIP = range(7)
ENH_ADD, ENH_VIEW, COPY = 0x40, 0x41, 0x42
MOVE_TO_LEVEL_WITH_ONOFF, OFF, ON = 0x04, 0x00, 0x01
SUCCESS, INVALID_FIELD, INSUFFICIENT_SPACE, NOT_FOUND = 0x00, 0x85, 0x89, 0x8B
ATTR_COUNT, ATTR_SCENE, ATTR_GROUP, ATTR_VALID, ATTR_NAMES = range(5)
DP_ON, DP_LEVEL, DP_TIME = 1, 2, 160


def u16(v: int) -> bytes:
    return v.to_bytes(2, "little")


def ext(on: int | None = None, lvl: int | None = None) -> bytes:
    out = b""
    if on is not None:
        out += u16(ON_OFF) + bytes([1, on])
    if lvl is not None:
        out += u16(LEVEL) + bytes([1, lvl])
    return out


def add_payload(group: int, scene: int, tt: int = 0, fields: bytes = b"", name: bytes = b"") -> bytes:
    return u16(group) + bytes([scene]) + u16(tt) + bytes([len(name)]) + name + fields


def responses(device) -> list[ZCLCommandEvent]:
    return [ZCLCommandEvent.from_event(e) for e in device._events if e.kind == "zcl_cmd_resp"]


def call(device, cmd: int, payload: bytes, ep: int = 1, result: str = "PROCESSED") -> bytes | None:
    """The command; its response's payload (None: none came)."""
    before = len(responses(device))
    device.call_zigbee_cmd(ep, SCENES, cmd, payload, expected_result=result)
    try:
        wait_for(lambda: len(responses(device)) > before, timeout=0.3, interval=0.02)
    except Exception:
        return None
    r = responses(device)[before]
    assert (r.ep, r.cluster, r.cmd) == (ep, SCENES, cmd)
    return r.data


def attr(device, a: int, ep: int = 1) -> int:
    return int(device.read_zigbee_attr(ep, SCENES, a))


@pytest.fixture
def dev():
    proc, device = start()
    yield proc, device
    proc.stop()


def test_dimmer_endpoints_have_the_scenes_cluster(dev):
    _, device = dev
    for ep in (1, 2):
        assert attr(device, ATTR_COUNT, ep) == 0
        assert attr(device, ATTR_NAMES, ep) == 0
        assert attr(device, ATTR_VALID, ep) == 0


def test_add_and_view(dev):
    _, device = dev
    assert call(device, ADD, add_payload(0, 1, 2, ext(1, 100), b"evening")) == bytes([SUCCESS]) + u16(0) + b"\x01"
    assert call(device, VIEW, u16(0) + b"\x01") == (bytes([SUCCESS]) + u16(0) + b"\x01" + u16(2) + b"\x00"
                                                    + ext(1, 100))
    assert call(device, ENH_VIEW, u16(0) + b"\x01") == (bytes([SUCCESS]) + u16(0) + b"\x01" + u16(20)
                                                        + b"\x00" + ext(1, 100))
    assert attr(device, ATTR_COUNT) == 1 and attr(device, ATTR_COUNT, 2) == 1  # the whole table


def test_enhanced_add_in_tenths_and_unknown_fields_ignored(dev):
    _, device = dev
    fields = u16(0x0300) + bytes([3, 1, 2, 3]) + ext(lvl=0) + ext(on=0)      # color: ignored
    assert call(device, ENH_ADD, add_payload(0, 2, 15, fields))[0] == SUCCESS
    assert call(device, VIEW, u16(0) + b"\x02")[4:] == u16(1) + b"\x00" + ext(0) + ext(lvl=1)  # 1.5 s -> 1
    assert call(device, ENH_VIEW, u16(0) + b"\x02")[4:6] == u16(15)


def test_view_and_remove_of_a_missing_scene(dev):
    _, device = dev
    assert call(device, VIEW, u16(0) + b"\x07")[0] == NOT_FOUND
    assert call(device, REMOVE, u16(0) + b"\x07") == bytes([NOT_FOUND]) + u16(0) + b"\x07"
    call(device, ADD, add_payload(0, 7, 0, ext(1)))
    assert call(device, REMOVE, u16(0) + b"\x07")[0] == SUCCESS
    assert call(device, VIEW, u16(0) + b"\x07")[0] == NOT_FOUND
    assert attr(device, ATTR_COUNT) == 0


def test_a_group_the_endpoint_is_not_in_is_invalid(dev):
    proc, device = dev
    assert call(device, ADD, add_payload(5, 1, 0, ext(1))) == bytes([INVALID_FIELD]) + u16(5) + b"\x01"
    assert call(device, MEMBERSHIP, u16(5)) == bytes([INVALID_FIELD, 16]) + u16(5)
    device.call_zigbee_cmd(1, SCENES, RECALL, u16(5) + b"\x01", expected_result="INVALID_FIELD")
    assert proc.exec("zcl_group add 1 5").ok
    assert call(device, ADD, add_payload(5, 1, 0, ext(1)))[0] == SUCCESS
    assert call(device, ADD, add_payload(5, 1, 0, ext(1)), ep=2)[0] == INVALID_FIELD  # ep 2 not in it


def test_membership_and_remove_all(dev):
    proc, device = dev
    assert proc.exec("zcl_group add 1 9").ok
    for scene in (3, 1):
        call(device, ADD, add_payload(9, scene, 0, ext(1)))
    call(device, ADD, add_payload(0, 4, 0, ext(1)))
    call(device, ADD, add_payload(0, 4, 0, ext(1)), ep=2)
    assert call(device, MEMBERSHIP, u16(9)) == bytes([SUCCESS, 12]) + u16(9) + bytes([2, 3, 1])
    assert call(device, MEMBERSHIP, u16(0)) == bytes([SUCCESS, 12]) + u16(0) + bytes([1, 4])
    assert call(device, REMOVE_ALL, u16(9)) == bytes([SUCCESS]) + u16(9)
    assert call(device, MEMBERSHIP, u16(9)) == bytes([SUCCESS, 14]) + u16(9) + bytes([0])
    assert call(device, MEMBERSHIP, u16(0), ep=2) == bytes([SUCCESS, 14]) + u16(0) + bytes([1, 4])


def test_a_full_table(dev):
    _, device = dev
    for scene in range(16):
        assert call(device, ADD, add_payload(0, scene, 0, ext(1)))[0] == SUCCESS
    assert call(device, ADD, add_payload(0, 16, 0, ext(1)))[0] == INSUFFICIENT_SPACE
    assert call(device, ADD, add_payload(0, 16, 0, ext(1)), ep=2)[0] == INSUFFICIENT_SPACE
    assert call(device, ADD, add_payload(0, 5, 3, ext(0)))[0] == SUCCESS          # replaces one
    assert call(device, STORE, u16(0) + b"\x11")[0] == INSUFFICIENT_SPACE
    assert call(device, MEMBERSHIP, u16(0))[1] == 0


def test_malformed_and_unsupported(dev):
    _, device = dev
    device.call_zigbee_cmd(1, SCENES, ADD, u16(0) + b"\x01\x00\x00", expected_result="MALFORMED_COMMAND")
    device.call_zigbee_cmd(1, SCENES, ADD, add_payload(0, 1)[:-1] + b"\x05ab",
                           expected_result="MALFORMED_COMMAND")              # name past the end
    device.call_zigbee_cmd(1, SCENES, VIEW, u16(0), expected_result="MALFORMED_COMMAND")
    device.call_zigbee_cmd(1, SCENES, COPY, bytes(7), expected_result="SKIPPED")
    assert responses(device) == []


def test_store_takes_the_light_and_scene_valid_follows_it(dev):
    proc, device = dev
    device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([120]) + u16(0xFFFF))
    ack_writes(proc, device)
    assert call(device, STORE, u16(0) + b"\x03") == bytes([SUCCESS]) + u16(0) + b"\x03"
    assert call(device, VIEW, u16(0) + b"\x03")[4:] == u16(0) + b"\x00" + ext(1, 120)
    assert (attr(device, ATTR_SCENE), attr(device, ATTR_GROUP), attr(device, ATTR_VALID)) == (3, 0, 1)
    assert attr(device, ATTR_VALID, 2) == 0
    device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([60]) + u16(0xFFFF))
    assert attr(device, ATTR_VALID) == 0
    # A store keeps the scene's time.
    call(device, ADD, add_payload(0, 4, 7, ext(0)))
    call(device, STORE, u16(0) + b"\x04")
    assert call(device, VIEW, u16(0) + b"\x04")[4:6] == u16(7)


def test_store_while_off(dev):
    proc, device = dev
    device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([90]) + u16(0xFFFF))
    ack_writes(proc, device)
    device.call_zigbee_cmd(1, ON_OFF, OFF)
    ack_writes(proc, device)
    call(device, STORE, u16(0) + b"\x01")
    assert call(device, VIEW, u16(0) + b"\x01")[7:] == ext(0, 90)


def recall_writes(scene_fields: bytes, recall: bytes, before=None, tt: int = 0, build=500):
    """Open Dimmer of that build: the DP writes of a recall of scene 1 (time tt, s)."""
    proc, device = start()
    try:
        mcu = own_mcu_contact(proc, device, build=build)
        take_tx(proc)
        if before:
            before(device, mcu)
        call(device, ADD, add_payload(0, 1, tt, scene_fields))
        mcu.writes.clear()
        device.call_zigbee_cmd(1, SCENES, RECALL, recall)
        mcu.serve()
        device.step_time(2000)
        return list(mcu.writes), int(onoff(device)), level(device), attr(device, ATTR_VALID)
    finally:
        proc.stop()


def command_writes(cluster: int, cmd: int, payload: bytes, before=None, build=500):
    proc, device = start()
    try:
        mcu = own_mcu_contact(proc, device, build=build)
        take_tx(proc)
        if before:
            before(device, mcu)
        mcu.writes.clear()
        device.call_zigbee_cmd(1, cluster, cmd, payload)
        mcu.serve()
        device.step_time(2000)
        return list(mcu.writes)
    finally:
        proc.stop()


def lit(device, mcu, lvl=200):
    device.call_zigbee_cmd(1, LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([lvl]) + u16(0xFFFF))
    mcu.serve()
    device.step_time(2000)


@pytest.mark.parametrize("build", [500, 400, 0])
def test_recall_writes_what_the_equivalent_command_writes(build):
    """On at a level: MoveToLevelWithOnOff with the time (DP 160 only from the transition build)."""
    writes, on, lvl, valid = recall_writes(ext(1, 150), u16(0) + b"\x01", tt=3, build=build)
    assert (on, lvl, valid) == (1, 150, 1)
    assert writes == command_writes(LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([150]) + u16(30), build=build)
    assert ((DP_TIME, 3000) in writes) == (build >= 400)


def test_recall_off_and_on_alone():
    writes, on, _, _ = recall_writes(ext(0, 150), u16(0) + b"\x01", before=lit)
    assert on == 0
    assert writes == command_writes(LEVEL, MOVE_TO_LEVEL_WITH_ONOFF, bytes([0]) + u16(0xFFFF), before=lit)
    writes, on, _, _ = recall_writes(ext(1), u16(0) + b"\x01")
    assert on == 1
    assert writes == command_writes(ON_OFF, ON, b"")


def test_recall_level_alone_moves_only_a_lit_light():
    writes, on, lvl, _ = recall_writes(ext(lvl=80), u16(0) + b"\x01", before=lit)
    assert (on, lvl) == (1, 80)
    writes, on, _, _ = recall_writes(ext(lvl=80), u16(0) + b"\x01")
    assert on == 0 and (DP_ON, 1) not in writes


def test_recall_time_scene_0_is_the_fade_settings_a_command_time_wins():
    writes, *_ = recall_writes(ext(1, 150), u16(0) + b"\x01", tt=0)
    assert not [w for w in writes if w[0] == DP_TIME and w[1] != 0]
    writes, *_ = recall_writes(ext(1, 150), u16(0) + b"\x01" + u16(0), tt=3)
    assert (DP_TIME, 1) in writes                        # 0 in the command: at once
    writes, *_ = recall_writes(ext(1, 150), u16(0) + b"\x01" + u16(12), tt=3)
    assert (DP_TIME, 1200) in writes
    writes, *_ = recall_writes(ext(1, 150), u16(0) + b"\x01" + u16(0xFFFF), tt=3)
    assert (DP_TIME, 3000) in writes                     # 0xFFFF: the scene's


def test_recall_of_a_missing_scene(dev):
    _, device = dev
    device.call_zigbee_cmd(1, SCENES, RECALL, u16(0) + b"\x09", expected_result="NOT_FOUND")
    device.call_zigbee_cmd(1, SCENES, RECALL, u16(0), expected_result="MALFORMED_COMMAND")


def test_kept_over_a_restart():
    proc, device = start()
    try:
        call(device, ADD, add_payload(0, 2, 4, ext(1, 77)))
        call(device, ADD, add_payload(0, 3, 0, ext(0)), ep=2)
    finally:
        proc.stop()
    proc, device = start()
    try:
        assert attr(device, ATTR_COUNT) == 2
        assert call(device, VIEW, u16(0) + b"\x02")[4:] == u16(4) + b"\x00" + ext(1, 77)
        assert call(device, VIEW, u16(0) + b"\x03", ep=2)[0] == SUCCESS
        assert call(device, VIEW, u16(0) + b"\x03")[0] == NOT_FOUND
    finally:
        proc.stop()


def test_removing_a_group_drops_its_scenes(dev):
    proc, device = dev
    for g in (5, 6):
        assert proc.exec(f"zcl_group add 1 {g}").ok
        call(device, ADD, add_payload(g, 1, 0, ext(1)))
    call(device, ADD, add_payload(0, 1, 0, ext(1)))
    assert proc.exec("zcl_group remove 1 5").ok
    assert proc.exec("zcl_group add 1 5").ok                              # back: no old scenes
    assert call(device, MEMBERSHIP, u16(5))[4:] == bytes([0])
    assert call(device, MEMBERSHIP, u16(6))[4:] == bytes([1, 1])
    assert proc.exec("zcl_group remove_all 1").ok
    assert proc.exec("zcl_group add 1 6").ok
    assert call(device, MEMBERSHIP, u16(6))[4:] == bytes([0])
    assert call(device, MEMBERSHIP, u16(0))[4:] == bytes([1, 1])         # group 0 stays


def test_a_group_command_reaches_every_member_without_responses(dev):
    proc, device = dev
    for ep in (1, 2):
        assert proc.exec(f"zcl_group add {ep} 7").ok
    payload = add_payload(7, 1, 0, ext(1, 50))
    res = proc.exec(f"zcl_group_cmd 7 0x0005 0x00 {' '.join(f'{b:02X}' for b in payload)}")
    assert res.ok and res.payload["result"] == "PROCESSED"
    assert responses(device) == []
    for ep in (1, 2):
        assert call(device, VIEW, u16(7) + b"\x01", ep=ep)[0] == SUCCESS
