"""First contact with the own PY32 firmware in the order and with the content
the real one uses (own_mcu.py): who it is, what it holds, and how a lost
answer, a restart of the MCU alone or its query counter wrapping is handled."""
from own_mcu import CMD_VERSION, OTA_IDENT, OwnMcu, own_mcu_contact
from test_dimmer_mcu import start
from test_mcu_ota import VERSION, newer_version, shown_version
from test_own_mcu_dps import STARTUP_OFF, STARTUP_ON, STARTUP_PREVIOUS, startup
from tuya_mcu import TUYA_CMD_QUERY_ALL, TUYA_CMD_QUERY_NETWORK, parse_frames, pump, take_tx, tuya_frame

OWN = f"{VERSION >> 6}.{(VERSION >> 4) & 3}.{VERSION & 15} own"
DUMP = ["tx"] * 5


def power_on(device) -> tuple[int, int]:
    return startup(device, 1), startup(device, 2)


def test_first_contact_runs_in_the_real_order():
    proc, device = start()
    try:
        mcu = own_mcu_contact(proc, device)
        wire = [d if d == "tx" else f"rx{c:02x}" for d, c in mcu.log]
        assert wire == [
            "tx",                                   # its query (seq 1)
            "rx20", "rx0b",                         # answer, version query
            *DUMP, "tx",                            # its own dump, version
            "rx28", "rxa3",                         # query-all, who are you?
            *DUMP, "tx",                            # dump, identity
            "rx28",                                 # read again as ours
            *DUMP,
        ]
    finally:
        proc.stop()


def test_first_contact_dump_is_read_again_once_the_mcu_is_ours():
    """Both dumps of the contact come before the identity answer, so they are
    read as a stock MCU's: DP 14 (channel 1's power-on) would set both
    channels and channel 2's own DP 104 would be dropped."""
    proc, device = start()
    try:
        own_mcu_contact(proc, device, power_on=(0, 1))
        assert shown_version(device) == OWN
        assert power_on(device) == (STARTUP_OFF, STARTUP_ON)
    finally:
        proc.stop()


def test_mcu_query_after_the_timer_contact_is_not_a_second_contact():
    """The module restarts (an OTA, a config write) while the MCU runs on:
    its own timer makes the contact; the MCU's next query is no reason to do
    it all again."""
    proc, device = start()
    try:
        mcu = OwnMcu(proc, device, power_on=(2, 0), started=False)
        mcu.query_seq = 1234
        device.step_time(2100)
        pump(proc)
        mcu.serve(1000)
        assert shown_version(device) == OWN
        assert power_on(device) == (STARTUP_PREVIOUS, STARTUP_OFF)
        mcu.query_network()
        assert mcu.serve(1000) == [TUYA_CMD_QUERY_NETWORK]     # only the answer
        assert shown_version(device) == OWN
        assert power_on(device) == (STARTUP_PREVIOUS, STARTUP_OFF)
    finally:
        proc.stop()


def test_query_counter_wrap_is_not_a_restart_but_a_restart_after_it_is():
    proc, device = start()
    try:
        mcu = own_mcu_contact(proc, device, power_on=(0, 1))
        for seq in (0xFFFE, 0xFFFF, 0, 1):
            mcu.query_network(seq)
            assert mcu.serve(200) == [TUYA_CMD_QUERY_NETWORK]
        mcu.ch[1]["power_on"] = 2                   # changed on the bench (SWD)
        mcu.restart()
        mcu.query_network()
        sent = mcu.serve(1000)
        assert CMD_VERSION in sent and OTA_IDENT in sent
        assert shown_version(device) == OWN
        assert power_on(device) == (STARTUP_OFF, STARTUP_PREVIOUS)
    finally:
        proc.stop()


def test_lost_version_answer_is_asked_again():
    proc, device = start()
    try:
        mcu = OwnMcu(proc, device)
        mcu.lose[CMD_VERSION] = 1
        mcu.query_seq = 1
        device.step_time(1900)
        pump(proc)
        take_tx(proc)
        mcu.query_network()
        sent = mcu.serve(1500)
        assert sent.count(CMD_VERSION) == 1 and shown_version(device) == ""
        sent = mcu.serve(1500)
        assert sent.count(CMD_VERSION) == 1        # after 2 s
        assert shown_version(device) == OWN
        assert power_on(device) == (STARTUP_OFF, STARTUP_ON)
    finally:
        proc.stop()


def test_version_query_is_given_up_after_three_tries():
    """An MCU that never tells its version is not asked forever."""
    proc, device = start()
    try:
        device.step_time(2100)
        pump(proc)
        sent = []
        for _ in range(40):
            device.step_time(250)
            pump(proc)
            sent += [cmd for cmd, _ in parse_frames(take_tx(proc))]
        assert sent.count(CMD_VERSION) == 3         # the first one + 2 more
    finally:
        proc.stop()


def test_lost_identity_answers_never_show_a_bare_version():
    """A bare version makes the converter take our MCU for a stock one (and
    drop its entities): never while there is still a question out."""
    proc, device = start()
    try:
        mcu = OwnMcu(proc, device)
        mcu.lose[OTA_IDENT] = 3
        mcu.query_seq = 1
        device.step_time(1900)
        pump(proc)
        take_tx(proc)
        mcu.query_network()
        asked = 0
        for _ in range(40):
            asked += mcu.serve(100).count(OTA_IDENT)
            assert shown_version(device) in ("", OWN)
        assert asked == 4
        assert shown_version(device) == OWN
        assert power_on(device) == (STARTUP_OFF, STARTUP_ON)
    finally:
        proc.stop()


def test_mcu_restart_during_identification_is_noticed():
    """It restarts after its version answer, before the identity answer: its
    query numbered 0 starts the contact again, though not known as ours yet."""
    proc, device = start()
    try:
        mcu = OwnMcu(proc, device, version=newer_version(), power_on=(0, 1))
        mcu.answers_ident = False
        mcu.query_seq = 1
        device.step_time(1900)
        pump(proc)
        take_tx(proc)
        mcu.query_network()
        mcu.serve(300)
        assert shown_version(device) == ""
        mcu.restart()                                # back to the carried version
        mcu.version = VERSION
        mcu.answers_ident = True
        mcu.query_network()
        sent = mcu.serve(1000)
        assert CMD_VERSION in sent
        assert shown_version(device) == OWN
        assert power_on(device) == (STARTUP_OFF, STARTUP_ON)
    finally:
        proc.stop()


def test_mcu_that_did_not_say_who_it_is_is_asked_again_after_its_restart():
    """Not identified in time (shown bare): its next start asks again."""
    proc, device = start()
    try:
        mcu = OwnMcu(proc, device)
        mcu.answers_ident = False
        mcu.query_seq = 1
        device.step_time(1900)
        pump(proc)
        take_tx(proc)
        mcu.query_network()
        mcu.serve(8000)
        assert shown_version(device) == OWN.removesuffix(" own")
        mcu.answers_ident = True
        mcu.restart()
        mcu.query_network()
        mcu.serve(1000)
        assert shown_version(device) == OWN
        assert power_on(device) == (STARTUP_OFF, STARTUP_ON)
    finally:
        proc.stop()


def test_stock_2x_mcu_that_numbers_every_query_0_gets_a_few_contacts_only():
    proc, device = start()
    try:
        mcu = OwnMcu(proc, device, version=newer_version())
        mcu.answers_ident = False
        versions = 0
        for _ in range(6):
            mcu.query_network(0)
            versions += mcu.serve(30_000, step_ms=500).count(CMD_VERSION)
        assert versions == 1 + 3
        assert not shown_version(device).endswith("own")
    finally:
        proc.stop()


def test_identified_mcu_gets_one_more_dump_only():
    """An unasked version report (as after pairing) asks who it is again but
    does not read everything again when it already was ours."""
    proc, device = start()
    try:
        mcu = own_mcu_contact(proc, device)
        mcu.send([tuya_frame(CMD_VERSION, bytes([VERSION]))])
        sent = mcu.serve(1000)
        assert OTA_IDENT in sent and TUYA_CMD_QUERY_ALL not in sent
        assert shown_version(device) == OWN
    finally:
        proc.stop()
