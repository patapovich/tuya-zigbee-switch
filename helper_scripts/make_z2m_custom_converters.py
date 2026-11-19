import argparse
import re
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape

ROOT = Path(__file__).resolve().parent.parent


class RenderStrictUndefined(Undefined):
    """A missing value fails when it is printed. The default Undefined prints
    nothing and leaves invalid JS ("valueMin: ,"); Zigbee2MQTT then refuses the
    whole converter and drops every model in it. Tests such as {% if dp.unit %}
    stay allowed: jinja2.StrictUndefined refuses those too, and the template
    relies on them."""

    __slots__ = ()
    __str__ = Undefined._fail_with_undefined_error


env = Environment(
    loader=FileSystemLoader(ROOT / "helper_scripts/templates"),
    autoescape=select_autoescape(),
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=RenderStrictUndefined,
)


def c_define(header: str, name: str) -> str:
    """Value of a #define in the firmware sources."""
    m = re.search(rf"^#define\s+{name}\s+(\S+)", (ROOT / header).read_text(), re.M)
    if not m:
        raise SystemExit(f"{header}: no #define {name}")
    return m.group(1)


# The firmware maps at most this many dp attributes; it drops the rest while
# Z2M would still expose them.
MAX_DP_ATTRS = int(c_define("src/zigbee/dp_attr.h", "MAX_DP_ATTRS"))


def reserved_basic_attrs() -> set[int]:
    """Basic cluster attribute ids 0xffXX the firmware uses itself."""
    consts = (ROOT / "src/zigbee/consts.h").read_text()
    ids = {
        int(v, 16)
        for v in re.findall(r"^#define\s+ZCL_ATTR_BASIC_\w+\s+0x([fF]{2}[0-9a-fA-F]{2})\b", consts, re.M)
    }
    first = int(c_define("src/zigbee/consts.h", "ZCL_ATTR_BASIC_MCU_DIAG_LOG_FIRST"), 16)
    pages = int(c_define("src/zigbee/consts.h", "TUYA_MCU_DIAG_LOG_PAGES"))
    return ids | set(range(first, first + pages))


RESERVED_BASIC_ATTRS = reserved_basic_attrs()


def dp_attribute_errors(device: dict) -> list[str]:
    """What is wrong with a board's dp_attributes; each would make the firmware
    and the converter disagree, or the converter invalid JS."""
    errors = []
    dps = device.get("dp_attributes") or []
    dimmer_total = sum(
        int(p[2:]) for p in device["config_str"].rstrip(";").split(";")[2:] if p[:2] == "DM"
    )
    if len(dps) > MAX_DP_ATTRS:
        errors.append(f"{len(dps)} dp attributes, the firmware maps at most {MAX_DP_ATTRS} (MAX_DP_ATTRS)")
    seen_dp, seen_attr, seen_expose = {}, {}, {}
    for i, dp in enumerate(dps):
        name = dp.get("name")
        where = f"dp_attributes[{i}] ({name})"
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            errors.append(f"{where}: name must be lower case letters, digits and _")
        if not isinstance(dp.get("label"), str) or not dp["label"]:
            errors.append(f"{where}: needs a label (the Z2M description)")
        dp_id, attr = dp.get("dp"), dp.get("attr")
        if not isinstance(dp_id, str) or not re.fullmatch(r"[0-9A-Fa-f]{2}", dp_id) or int(dp_id, 16) == 0:
            errors.append(f"{where}: dp must be two hex digits, not 00")
        elif int(dp_id, 16) in seen_dp:
            errors.append(f"{where}: dp {dp_id} also in dp_attributes[{seen_dp[int(dp_id, 16)]}]")
        else:
            seen_dp[int(dp_id, 16)] = i
        if not isinstance(attr, str) or not re.fullmatch(r"[0-9A-Fa-f]{2}", attr):
            errors.append(f"{where}: attr must be two hex digits")
        elif 0xFF00 | int(attr, 16) in RESERVED_BASIC_ATTRS:
            errors.append(f"{where}: attr {attr} is Basic 0x{0xFF00 | int(attr, 16):04x}, which the firmware uses itself")
        elif int(attr, 16) in seen_attr:
            errors.append(f"{where}: attr {attr} also in dp_attributes[{seen_attr[int(attr, 16)]}]")
        else:
            seen_attr[int(attr, 16)] = i
        kind = dp.get("type")
        if kind not in ("B", "E", "V"):
            errors.append(f"{where}: type must be B, E or V")
        if kind == "E":
            values = dp.get("values")
            if not isinstance(values, dict) or not values:
                errors.append(f"{where}: an enum needs values")
            else:
                for key, value in values.items():
                    # YAML 1.1 turns bare off/on/yes/no into booleans, so an enum
                    # label like "off" silently becomes False and reaches z2m as "False".
                    if not isinstance(key, str):
                        errors.append(f"{where}: enum label {key!r} is not a string. Quote it in device_db.yaml.")
                    if type(value) is not int or not 0 <= value <= 255:
                        errors.append(f"{where}: enum value {value!r} of {key!r} is not 0..255")
        if kind == "V":
            lo, hi = dp.get("min"), dp.get("max")
            if not all(type(v) in (int, float) for v in (lo, hi)):
                errors.append(f"{where}: a value needs min and max")
            elif lo > hi:
                errors.append(f"{where}: min {lo} > max {hi}")
        if "unit" in dp and not isinstance(dp["unit"], str):
            errors.append(f"{where}: unit must be a string")
        # divisor / step: only the Open Dimmer value converter (ownMcuNumeric) takes them.
        for key in ("divisor", "step"):
            if key not in dp:
                continue
            if type(dp[key]) not in (int, float) or not dp[key] > 0:
                errors.append(f"{where}: {key} must be a number above 0")
            elif kind != "V" or not (dp.get("dimmer") or dp.get("min_mcu")):
                errors.append(f"{where}: {key} only for a value (type V) with dimmer or min_mcu")
        if dp.get("dimmer") or dp.get("min_mcu"):
            if kind not in ("E", "V"):
                errors.append(f"{where}: dimmer / min_mcu settings must be enums or values (type E / V)")
            if dp.get("dimmer") and not 1 <= dp["dimmer"] <= dimmer_total:
                errors.append(f"{where}: dimmer {dp['dimmer']} but the board has {dimmer_total}")
        expose = (name, dp.get("dimmer") or 0)
        if expose in seen_expose:
            errors.append(f"{where}: same name and endpoint as dp_attributes[{seen_expose[expose]}]")
        else:
            seen_expose[expose] = i
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create Zigbee2mqtt converter for custom devices",
        epilog="Generates a js file that adds support of re-flashed devices to z2m",
    )
    parser.add_argument(
        "db_file", metavar="INPUT", type=str, help="File with device db"
    )
    parser.add_argument(
        "--z2m-v1", action=argparse.BooleanOptionalAction, help="Use old z2m"
    )

    args = parser.parse_args()

    db_str = Path(args.db_file).read_text()
    db = yaml.safe_load(db_str)

    devices = []
    errors = []

    for board, device in db.items():
        # Skip if build == no. Defaults to yes
        if not device.get("build", True):
            continue

        errors += [f"{board}: {error}" for error in dp_attribute_errors(device)]

        config = device["config_str"]
        zb_manufacturer, zb_model, *peripherals = config.rstrip(";").split(";")

        relay_cnt = 0
        switch_cnt = 0
        dimmer_cnt = 0
        cover_switch_cnt = 0
        cover_cnt = 0
        indicators_cnt = 0
        dp_relay_cnt = 0
        has_dedicated_net_led = False
        has_battery_cluster = False
        has_secondary_mcu = bool(device.get("dp_attributes"))
        for peripheral in peripherals:
            if peripheral == "SLP" or peripheral == "M":
                continue
            if peripheral[:2] == "SW":
                continue  # SW<clk><dio>: the SWD master's pins (mcu_flasher.c), not a switch
            if peripheral[0] == "R":
                relay_cnt += 1
            if peripheral[:2] == "RT":
                dp_relay_cnt += 1
            if peripheral[0] == "S":
                switch_cnt += 1
            if peripheral[:2] == "DM":
                dimmer_cnt = max(dimmer_cnt, int(peripheral[2:]))
            if peripheral[0] == "X":
                cover_switch_cnt += 1
            if peripheral[0] == "C":
                cover_cnt += 1
            if peripheral[0] == "I" and peripheral[:2] != "IT":
                indicators_cnt += 1
            if peripheral[0] == "L":
                has_dedicated_net_led = True
            if peripheral[:2] == "BT":
                has_battery_cluster = True
            if peripheral[:2] in ("RT", "ST", "DM", "IT"):
                has_secondary_mcu = True

        if switch_cnt == 1:
            switch_names = ["switch"]
        elif switch_cnt == 2:
            switch_names = ["switch_left", "switch_right"]
        elif switch_cnt == 3:
            switch_names = ["switch_left", "switch_middle", "switch_right"]
        else:
            switch_names = [f"switch_{index}" for index in range(switch_cnt)]

        if relay_cnt == 1:
            relay_names = ["relay"]
        elif relay_cnt == 2:
            relay_names = ["relay_left", "relay_right"]
        elif relay_cnt == 3:
            relay_names = ["relay_left", "relay_middle", "relay_right"]
        else:
            relay_names = [f"relay_{index}" for index in range(relay_cnt)]

        if cover_switch_cnt == 1:
            cover_switch_names = ["cover_switch"]
        elif cover_switch_cnt == 2:
            cover_switch_names = ["cover_switch_left", "cover_switch_right"]
        elif cover_switch_cnt == 3:
            cover_switch_names = [
                "cover_switch_left",
                "cover_switch_middle",
                "cover_switch_right",
            ]
        else:
            cover_switch_names = [
                f"cover_switch_{i + 1}" for i in range(cover_switch_cnt)
            ]

        if cover_cnt == 1:
            cover_names = ["cover"]
        elif cover_cnt == 2:
            cover_names = ["cover_left", "cover_right"]
        elif cover_cnt == 3:
            cover_names = ["cover_left", "cover_middle", "cover_right"]
        else:
            cover_names = [f"cover_{index}" for index in range(cover_cnt)]

        # Channels l1, l2, ... as the boards mark their outputs (L1, L2) and as
        # zigbee-herdsman-converters' own stock definitions name them (ZDMS16-2:
        # state_l1, switch_type_l1): a module moved from the stock firmware keeps
        # the light's and the switch type's entities in Home Assistant.
        if dimmer_cnt == 1:
            dimmer_names = ["dimmer"]
        else:
            dimmer_names = [f"l{index + 1}" for index in range(dimmer_cnt)]

        devices.append(
            {
                "zb_models": [zb_model] + (device.get("old_zb_models") or []),
                "model": device.get("override_z2m_device")
                or device["stock_converter_model"],
                "switchNames": switch_names,
                "relayNames": relay_names,
                "relayIndicatorNames": relay_names[:indicators_cnt],
                "dp_relay_cnt": dp_relay_cnt,
                # Datapoint metadata drives both the compiled dp config
                # string and the exposes below. Single source of truth.
                "dp_attributes": device.get("dp_attributes") or [],
                "coverSwitchNames": cover_switch_names,
                "coverNames": cover_names,
                "dimmerNames": dimmer_names,
                "has_dedicated_net_led": has_dedicated_net_led,
                "has_battery_cluster": has_battery_cluster,
                "has_secondary_mcu": has_secondary_mcu,
                # Carries the Open Dimmer firmware (PY32): its per-channel
                # power-on and wall switch gestures (shown per the device option
                # open_dimmer_settings) and mcu_firmware.
                "secondary_mcu_image": bool(device.get("secondary_mcu_image")),
            }
        )

    if errors:
        raise SystemExit("device_db.yaml:\n  " + "\n  ".join(errors))

    # Stock modules of the boards that carry an MCU image (the AVATTO ZDMS16-2): their
    # zigbee-herdsman-converters definition gets OTA for these boards' stock manufacturer
    # names only, so the conversion (the -from_tuya image) is offered with this one file.
    stock_ota = {}
    for device in db.values():
        if not device.get("build", True) or not device.get("secondary_mcu_image"):
            continue
        if not (device.get("stock_converter_model") and device.get("stock_manufacturer_name")):
            continue
        vendor = device.get("stock_converter_manufacturer", "Tuya")
        key = (vendor, device["stock_converter_model"], device.get("stock_model_name", "TS0601"))
        names = stock_ota.setdefault(key, [])
        if device["stock_manufacturer_name"] not in names:
            names.append(device["stock_manufacturer_name"])
    stock_ota = [
        {"module": vendor.lower(), "vendor": vendor, "model": model, "zb_model": zb_model,
         "manufacturer_names": names}
        for (vendor, model, zb_model), names in sorted(stock_ota.items())
    ]

    template = env.get_template("switch_custom.js.jinja")

    print(template.render(devices=devices, stock_ota=stock_ota, z2m_v1=args.z2m_v1))

    exit(0)
