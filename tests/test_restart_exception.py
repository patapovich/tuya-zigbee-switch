"""Basic 0xff5c on the ZDMS16-2 and the TS110E: the stack exception that last restarted the module
since its power-up (its event code, 0: none; src/telink/hal/zigbee_network.c). The stub has no
Telink stack, so nothing restarts it: the attribute is there and reads 0."""
import re
from pathlib import Path

import pytest

from client import StubProc
from conftest import Device
from test_dimmer_mcu import ZDMS16_2

ATTR = 0xFF5C
TS110E = "3mpwqzuu;TS110E-ZH-Z06;DM2;WB1B7;Y9600;HKB4C0C3C2;"   # as test_hk_dimmer.py
TS110E_DP_CONFIG = "70V30;71V31;72V32;73V33;74V34;75V35;76V36;77V37;78E38;79E39;"
NETWORK_C = Path(__file__).resolve().parent.parent / "src/telink/hal/zigbee_network.c"


@pytest.mark.parametrize("config, dp_config", [(ZDMS16_2, None), (TS110E, TS110E_DP_CONFIG)],
                         ids=["zdms16_2", "ts110e"])
def test_the_restart_exception_reads_0_without_one(config, dp_config):
    with StubProc(device_config=config, dp_config=dp_config) as proc:
        assert Device(proc).read_zigbee_attr(1, 0x0000, ATTR) == "0"


def test_the_ts110e_keeps_it_apart_from_the_lights_kept_state():
    """The TS110E builds (HK_DIMMER) keep the light's state for a warm start in DEEP_ANA_REG1
    (hal_system_kept_*, telink/hal/system.c): the exception code goes to DEEP_ANA_REG0 there, so
    an exception restart keeps the lights as they are and 0xff5c never reads the kept byte."""
    text = NETWORK_C.read_text()
    m = re.search(r"#ifdef HK_DIMMER\n(?:.*\n)*?#define RESTART_EXCEPTION_ANA_REG\s+(\w+)\n#else\n"
                  r"#define RESTART_EXCEPTION_ANA_REG\s+(\w+)\n#endif", text)
    assert m and m.groups() == ("DEEP_ANA_REG0", "DEEP_ANA_REG1")
    assert "HAL_KEPT_ANA_REG    DEEP_ANA_REG1" in (NETWORK_C.parent / "system.c").read_text()
    uses = re.findall(r"analog_(?:read|write)\((\w+)", text)
    assert uses and set(uses) == {"RESTART_EXCEPTION_ANA_REG"}
