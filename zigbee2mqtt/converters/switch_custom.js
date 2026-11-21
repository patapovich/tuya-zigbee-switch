const {
    numeric,
    enumLookup,
    deviceEndpoints,
    onOff,
    light,
    text,
    binary,
    windowCovering,
} = require("zigbee-herdsman-converters/lib/modernExtend");
const {assertString} = require("zigbee-herdsman-converters/lib/utils");
const globalStore = require("zigbee-herdsman-converters/lib/store");
const reporting = require("zigbee-herdsman-converters/lib/reporting");
const constants = require("zigbee-herdsman-converters/lib/constants");
const Zcl = require('zigbee-herdsman').Zcl;
const exposesLib = require("zigbee-herdsman-converters/lib/exposes");
const e = exposesLib.presets;
const ea = exposesLib.access;

/* A secondary-MCU version as the module reports it in Basic 0xff12 ("x.y.z",
 * or "x.y.z own" once it has identified the MCU as Open Dimmer: " own" is the
 * module's marker for it), not the "" or "0.0.0" it answers right after a
 * restart, before the MCU has told it. Users see the version without the
 * marker (mcu_version) and the firmware's name (mcu_firmware). */
const realMcuVersion = (v) => typeof v === "string" && /^\d+\.\d+\.\d+( own)?$/.test(v) && !v.startsWith("0.0.0");

// A version as mcu_version shows it: "x.y.z own" -> "x.y.z", others as they are.
const shownMcuVersion = (v) => (typeof v === "string" ? v.replace(/^(\d+\.\d+\.\d+) own$/, "$1") : v);

/* The version that says what the MCU is: the newest real one (latest), else
 * the last one kept (kept, device.meta). Open Dimmer is sticky: a bare 2.x or
 * later does not replace a kept "x.y.z own", because the module reports Open
 * Dimmer without the marker when it could not identify it (a lost identity
 * answer). Only a version below 2.x (a stock MCU) means the MCU really changed. */
const mcuVersionOf = (latest, kept) => {
    if (!realMcuVersion(latest)) return realMcuVersion(kept) ? kept : undefined;
    const bareOwnMajor = !latest.endsWith(" own") && Number(latest.split(".")[0]) >= 2;
    if (bareOwnMajor && realMcuVersion(kept) && kept.endsWith(" own")) return kept;
    return latest;
};

/* Major version of the Open Dimmer firmware on the secondary MCU, as last
 * seen: the module reports it as "x.y.z own" (only Open Dimmer answers its
 * identity query; a stock MCU never gets the marker, whatever its version
 * number). Kept in the endpoint's attribute cache, else in device.meta
 * (romasku.mcuVersionMeta). undefined if the MCU does not run Open Dimmer or
 * is not known yet. */
const ownMcuVersionNumbers = (device) => {
    if (!device) return undefined;
    let cached;
    try {
        cached = device.getEndpoint(1)?.clusters?.genBasic?.attributes?.[0xff12];
    } catch (error) {
        cached = undefined;
    }
    return ownVersionNumbers(mcuVersionOf(cached, device.meta?.mcuVersion));
};
const ownMcuMajorOf = (device) => ownMcuVersionNumbers(device)?.[0];

// Whether the MCU is known to be another one than Open Dimmer (a real version kept that is
// not "x.y.z own"); false while no version is known yet.
const knownNotOwnMcu = (device) => {
    if (!device) return false;
    let cached;
    try {
        cached = device.getEndpoint(1)?.clusters?.genBasic?.attributes?.[0xff12];
    } catch (error) {
        cached = undefined;
    }
    const v = mcuVersionOf(cached, device.meta?.mcuVersion);
    return realMcuVersion(v) && ownVersionNumbers(v) === undefined;
};

// [major, minor, patch] of an "x.y.z own" version, else undefined.
const ownVersionNumbers = (v) => {
    const m = typeof v === "string" ? /^(\d+)\.(\d+)\.(\d+) own$/.exec(v) : null;
    return m ? m.slice(1).map(Number) : undefined;
};

// Whether an Open Dimmer version ([major, minor, patch]) is at least minMcu: a major (2),
// "major.minor" ("2.2", the settings of Open Dimmer 2.2.0) or "major.minor.patch" ("2.1.14").
const ownVersionAtLeast = (numbers, minMcu) => {
    if (!numbers) return false;
    const want = String(minMcu).split(".").map(Number);
    for (let i = 0; i < 3; i++) {
        if ((numbers[i] ?? 0) !== (want[i] ?? 0)) return (numbers[i] ?? 0) > (want[i] ?? 0);
    }
    return true;
};

// Whether the settings of Open Dimmer (from version minMcu) are shown: device
// option open_dimmer_settings auto (identified as Open Dimmer) / show / hide.
// own_mcu_settings is its old name, still read when the new one is not set.
const ownMcuShown = (device, options, minMcu) => {
    const mode = options?.open_dimmer_settings ?? options?.own_mcu_settings ?? "auto";
    if (!device || device.isDummyDevice || mode === "show") return true;
    if (mode === "hide") return false;
    return ownVersionAtLeast(ownMcuVersionNumbers(device), minMcu);
};

// Open Dimmer 2.2.0 (build 500): countdown, wall switch mode, level after a power cut, start
// boost, identify, health report. The module sends none of them to an older MCU.
const OPEN_DIMMER_2_2 = "2.2";

// Name of the endpoint a message came from (the deviceEndpoints mapping).
const endpointNameOf = (model, msg) => {
    const map = typeof model.endpoint === "function" ? model.endpoint(msg.device) : {};
    return Object.keys(map).find((name) => map[name] === msg.endpoint.ID);
};

/********************************************************************
  This file (`switch_custom.js`) is generated. 
  
  You can edit it for testing, but for PRs please use:
  - `device_db.yaml`                - add or edit devices
  - `switch_custom.md.jinja`        - update the template
  - `make_z2m_custom_converters.py` - update generation script

  Generate with: `make tools/update_converters`
********************************************************************/

const romasku = {
    switchAction: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            lookup: { on_off: 0, off_on: 1, toggle_simple: 2, toggle_smart_sync: 3, toggle_smart_opposite: 4 },
            cluster: "genOnOffSwitchCfg",
            attribute: {ID: 0x0010, type: 0x30, required: true, write: true, min: 0, max: 4}, // Enum8
            description: `Select how switch should work:
            - on_off: When switch physically moved to position 1 it always generates ON command, and when moved to position 2 it generates OFF command
            - off_on: Same as on_off, but positions are swapped
            - toggle_simple: Any press of physical switch will TOGGLE the relay and send TOGGLE command to binds
            - toggle_smart_sync: Any press of physical switch will TOGGLE the relay and send corresponding ON/OFF command to keep binds in sync with relay
            - toggle_smart_opposite: Any press of physical switch: TOGGLE the relay and send corresponding ON/OFF command to keep binds in the state opposite to the relay`,
            entityCategory: "config",
        }),
    switchMode: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            lookup: { toggle: 0, momentary: 1, momentary_nc: 2 },
            cluster: "genOnOffSwitchCfg",
            attribute: { ID: 0xff00, type: 0x30 }, // Enum8
            description: "Select the type of switch connected to the device",
            entityCategory: "config",
        }),
    relayMode: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            lookup: { detached: 0, press_start: 1, short_press: 3, long_press: 2},
            cluster: "genOnOffSwitchCfg",
            attribute: { ID: 0xff01, type: 0x30 }, // Enum8
            description: "When to turn on/off internal relay",
            entityCategory: "config",
        }),
    relayIndex: (name, endpointName, relay_cnt) =>
        enumLookup({
            name,
            endpointName,
            lookup: Object.fromEntries(
                Array.from({ length: relay_cnt || 2 }, (_, i) => [`relay_${i + 1}`, i + 1])
            ),
            cluster: "genOnOffSwitchCfg",
            attribute: { ID: 0xff02, type: 0x20 }, // uint8
            description: "Which internal relay it should trigger",
            entityCategory: "config",
        }),
    bindedMode: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            lookup: { press_start: 1, short_press: 3, long_press: 2},
            cluster: "genOnOffSwitchCfg",
            attribute: { ID: 0xff05, type: 0x30 }, // Enum8
            description: "When turn on/off binded device",
            entityCategory: "config",
        }),
    longPressDuration: (name, endpointName) =>
        numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: "genOnOffSwitchCfg",
            attribute: { ID: 0xff03, type: 0x21 }, // uint16
            description: "What duration is considerd to be long press",
            valueMin: 0,
            valueMax: 5000,
            entityCategory: "config",
        }),
    levelMoveRate: (name, endpointName) =>
        numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: "genOnOffSwitchCfg",
            attribute: { ID: 0xff04, type: 0x20 }, // uint8
            description: "Level (dim) move rate in steps per ms",
            valueMin: 1,
            valueMax: 255,
            entityCategory: "config",
        }),
    pressAction: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            access: "STATE_GET",
            lookup: { released: 0, press: 1, long_press: 2, position_on: 3, position_off: 4 },
            cluster: "genMultistateInput",
            attribute: "presentValue",
            description: "Action of the switch: 'released' or 'press' or 'long_press'",
            entityCategory: "diagnostic",
        }),
    relayIndicatorMode: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            lookup: { same: 0, opposite: 1, manual: 2 },
            cluster: "genOnOff",
            attribute: { ID: 0xff01, type: 0x30 }, // Enum8
            description: "Mode for the relay indicator LED",
            entityCategory: "config",
        }),
    relayIndicator: (name, endpointName) =>
        binary({
            name,
            endpointName,
            valueOn: ["ON", 1],
            valueOff: ["OFF", 0],
            cluster: "genOnOff",
            attribute: {ID: 0xff02, type: 0x10},  // Boolean
            description: "State of the relay indicator LED",
            access: "ALL",
            entityCategory: "config",
        }),
    batteryPercentage: () => {
        const result = numeric({
            name: "battery",
            cluster: "genPowerCfg",
            attribute: "batteryPercentageRemaining",
            description: "Remaining battery in %",
            valueMin: 0,
            valueMax: 100,
            unit: "%",
            access: "STATE_GET",
            entityCategory: "diagnostic",
        });
        // Patch fromZigbee to convert ZCL 0-200 to 0-100%
        const origConvert = result.fromZigbee[0].convert;
        result.fromZigbee[0].convert = (model, msg, publish, options, meta) => {
            const r = origConvert(model, msg, publish, options, meta);
            if (r && r.battery !== undefined) {
                r.battery = Math.round(r.battery / 2);
            }
            return r;
        };
        return result;
    },
    networkIndicator: (name, endpointName) =>
        binary({
            name,
            endpointName,
            valueOn: ["ON", 1],
            valueOff: ["OFF", 0],
            cluster: "genBasic",
            attribute: {ID: 0xff01, type: 0x10},  // Boolean
            description: "State of the network indicator LED",
            access: "ALL",
            entityCategory: "config",
        }),
    multiPressResetCount: (name, endpointName) =>
        numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: "genBasic",
            attribute: { ID: 0xff02, type: 0x20 }, // uint8
            description: "Number of consecutive presses to trigger factory reset (0 = disabled)",
            valueMin: 0,
            valueMax: 255,
            entityCategory: "config",
        }),
    dpConfig: (name, endpointName) =>
        text({
            name,
            endpointName,
            access: "ALL",
            cluster: "genBasic",
            attribute:  { ID: 0xff11, type: 0x44 }, // long str
            description: "Tuya datapoint to attribute map. Separate from device_config because a single ZCL write caps at ~74 characters.",
            zigbeeCommandOptions: {timeout: 30_000},
            validate: (value) => {
                assertString(value);
                if (value.length > 256) throw new Error("Length of dp config is greater than 256");
                if (value.length && !value.endsWith(";")) throw new Error("Should end with ;");
                for (const part of value.split(";").filter((p) => p.length)) {
                    if (!/^[0-9A-Fa-f]{2}[BEV][0-9A-Fa-f]{2}$/.test(part)) {
                        throw new Error(`Datapoint entry ${part} is invalid. Use <dp:2hex><B|E|V><attr:2hex>, e.g. 24B22`);
                    }
                }
            },
        }),
    mcuVersion: (name, endpointName) => {
        const result = text({
            name,
            endpointName,
            access: "STATE_GET",
            cluster: "genBasic",
            attribute:  { ID: 0xff12, type: 0x42 }, // char str
            description: "Firmware version of the secondary MCU (the chip that drives the outputs)",
            entityCategory: "diagnostic",
        });
        result.exposes = result.exposes.map((expose) => expose.withLabel("MCU version"));
        // The version alone, without the module's Open Dimmer marker (that is
        // mcu_firmware). Only the published value changes: msg.data, which the
        // other converters read, keeps the raw "x.y.z own". On the device (no
        // endpoint) only endpoint 1's Basic cluster carries it, as for mcu_firmware.
        result.fromZigbee = result.fromZigbee.map((converter) => ({
            ...converter,
            convert: (model, msg, publish, options, meta) => {
                if (!endpointName && msg.endpoint?.ID !== 1) return;
                const r = converter.convert(model, msg, publish, options, meta);
                if (r) for (const key of Object.keys(r)) r[key] = shownMcuVersion(r[key]);
                return r;
            },
        }));
        return result;
    },
    // Which firmware the secondary MCU runs (device level), from the same
    // version reports as mcu_version: Open Dimmer when the module has
    // identified it (sticky as in mcuVersionOf), else the stock Tuya firmware.
    // Nothing while the version is not known ("" or 0.0.0).
    // Not Open Dimmer yet: an install by the module's SWD master (status Basic
    // 0xff50, mcu_flasher.c) that waits for a power cycle, runs or failed is
    // told here too, whatever the device option mcu_flasher says: with the
    // wires soldered before the conversion the factory MCU holds its SWD pins
    // until the module is power-cycled, and nothing else would show it. The
    // last such state is kept in device.meta.mcuInstall.
    mcuFirmware: () => {
        const installNote = (raw) => {
            const b = Buffer.from(raw);
            if (b.length < 47 || b[0] !== 2 || b[2] !== 5) return null;   // not an install
            return {1: "installing Open Dimmer",
                    3: "Open Dimmer install failed (device option mcu_flasher: show tells why)",
                    5: "power-cycle the module to install Open Dimmer"}[b[1]] ?? null;
        };
        return {
            isModernExtend: true,
            exposes: [e.text("mcu_firmware", ea.STATE)
                .withLabel("MCU firmware")
                .withDescription("Firmware of the secondary MCU (the chip that drives the outputs): Open Dimmer or the stock Tuya firmware; while not Open Dimmer, also an Open Dimmer install that waits for a power cycle, runs or failed")
                .withCategory("diagnostic")],
            fromZigbee: [{
                cluster: "genBasic",
                type: ["attributeReport", "readResponse"],
                convert: (model, msg) => {
                    const raw = msg.data?.[0xff12];
                    const status = msg.data?.[0xff50];
                    if (msg.endpoint?.ID !== 1 || (!realMcuVersion(raw) && status === undefined)) return;
                    if (status !== undefined && msg.device) {
                        const note = installNote(status);
                        if ((msg.device.meta?.mcuInstall ?? null) !== note) {
                            msg.device.meta.mcuInstall = note ?? undefined;
                            msg.device.save();
                        }
                    }
                    const v = mcuVersionOf(raw, msg.device?.meta?.mcuVersion);
                    if (v?.endsWith(" own")) return {mcu_firmware: "Open Dimmer"};
                    const note = msg.device?.meta?.mcuInstall;
                    if (note) return {mcu_firmware: v ? `Tuya (stock): ${note}` : note};
                    if (v) return {mcu_firmware: "Tuya (stock)"};
                },
            }],
        };
    },
    mcuVersionMeta: () => ({
        isModernExtend: true,
        fromZigbee: [{
            cluster: "genBasic",
            type: ["attributeReport", "readResponse"],
            convert: (model, msg) => {
                // Keep the last real version: right after a restart the module
                // answers "" until the MCU has told it, and a bare 2.x may be
                // Open Dimmer not yet identified (mcuVersionOf). Neither must
                // hide the Open Dimmer settings.
                if (!msg.device) return;
                const v = mcuVersionOf(msg.data?.[0xff12], msg.device.meta?.mcuVersion);
                if (v !== undefined && msg.device.meta?.mcuVersion !== v) {
                    msg.device.meta.mcuVersion = v;
                    msg.device.save();
                }
            },
        }],
    }),
    // Settings only the Open Dimmer firmware has. auto shows them once the MCU
    // has reported a new enough Open Dimmer version (exposes are rebuilt when
    // Zigbee2MQTT restarts or this option changes). Read with the fallback to
    // the old option name own_mcu_settings (ownMcuShown).
    ownMcuOption: () => ({
        isModernExtend: true,
        options: [
            e.enum("open_dimmer_settings", ea.SET, ["auto", "show", "hide"])
                .withLabel("Open Dimmer settings")
                .withDescription("Settings that only the Open Dimmer firmware has (dimming curve, fade and hold, per-channel power-on behavior, wall switch gestures). auto (default): shown once the module has identified the dimming MCU as Open Dimmer (mcu_firmware); show: always; hide: never"),
        ],
    }),
    // Enum datapoint attribute (Basic 0xffXX on endpoint 1) exposed on the
    // endpoint of the channel it belongs to.
    ownMcuEnum: ({name, endpointName, attrId, lookup, description, minMcu}) => {
        const expose = e.enum(name, ea.ALL, Object.keys(lookup))
            .withEndpoint(endpointName)
            .withDescription(description)
            .withCategory("config");
        const property = `${name}_${endpointName}`;
        return {
            isModernExtend: true,
            exposes: [(device, options) => {
                if (!minMcu) return [expose];      // not tied to Open Dimmer
                return ownMcuShown(device, options, minMcu) ? [expose] : [];
            }],
            fromZigbee: [{
                cluster: "genBasic",
                type: ["attributeReport", "readResponse"],
                convert: (model, msg, publish, options) => {
                    if (msg.endpoint.ID !== 1 || msg.data?.[attrId] === undefined) return;
                    // A stock MCU has no such setting: the module answers 0.
                    if (minMcu && !ownMcuShown(msg.device, options, minMcu)) return;
                    const v = msg.data[attrId];
                    const key = Object.keys(lookup).find((k) => lookup[k] === v);
                    return {[property]: key ?? v};
                },
            }],
            toZigbee: [{
                key: [name],
                endpoints: [endpointName],
                convertSet: async (entity, key, value, meta) => {
                    if (!Object.prototype.hasOwnProperty.call(lookup, value)) throw new Error(`${name}: one of ${Object.keys(lookup).join(", ")}`);
                    await meta.device.getEndpoint(1).write("genBasic", {[attrId]: {value: lookup[value], type: Zcl.DataType.ENUM8}});
                    return {state: {[key]: value}};
                },
                convertGet: async (entity, key, meta) => {
                    await meta.device.getEndpoint(1).read("genBasic", [attrId]);
                },
            }],
        };
    },
    // Value datapoint attribute (Basic 0xffXX on endpoint 1, uint32) exposed on
    // the endpoint of the channel it belongs to, shown like ownMcuEnum.
    // divisor: shown = attribute / divisor (1000: an attribute in ms shown in s); valueMin and
    // valueMax are the attribute's. valueStep: the step shown.
    ownMcuNumeric: ({name, endpointName, attrId, description, valueMin, valueMax, unit, minMcu, divisor = 1, valueStep}) => {
        let expose = e.numeric(name, ea.ALL)
            .withEndpoint(endpointName)
            .withValueMin(valueMin / divisor)
            .withValueMax(valueMax / divisor)
            .withDescription(description)
            .withCategory("config");
        if (unit) expose = expose.withUnit(unit);
        if (valueStep !== undefined) expose = expose.withValueStep(valueStep);
        const property = `${name}_${endpointName}`;
        return {
            isModernExtend: true,
            exposes: [(device, options) => (!minMcu || ownMcuShown(device, options, minMcu) ? [expose] : [])],
            fromZigbee: [{
                cluster: "genBasic",
                type: ["attributeReport", "readResponse"],
                convert: (model, msg, publish, options) => {
                    if (msg.endpoint.ID !== 1 || msg.data?.[attrId] === undefined) return;
                    if (minMcu && !ownMcuShown(msg.device, options, minMcu)) return;   // as ownMcuEnum
                    return {[property]: msg.data[attrId] / divisor};
                },
            }],
            toZigbee: [{
                key: [name],
                endpoints: [endpointName],
                convertSet: async (entity, key, value, meta) => {
                    const n = value === "" || value === null ? NaN : Number(value);
                    const v = Math.round(n * divisor);
                    if (!Number.isFinite(n) || (divisor === 1 && !Number.isInteger(n)) || v < valueMin || v > valueMax) {
                        throw new Error(divisor === 1 ? `${name}: an integer ${valueMin}..${valueMax}`
                                                      : `${name}: ${valueMin / divisor}..${valueMax / divisor}${unit ? ` ${unit}` : ""}`);
                    }
                    await meta.device.getEndpoint(1).write("genBasic", {[attrId]: {value: v, type: Zcl.DataType.UINT32}});
                    return {state: {[key]: v / divisor}};
                },
                convertGet: async (entity, key, meta) => {
                    await meta.device.getEndpoint(1).read("genBasic", [attrId]);
                },
            }],
        };
    },
    deviceConfigExt: (name, endpointName) =>
        text({
            name,
            endpointName,
            access: "ALL",
            cluster: "genBasic",
            attribute:  { ID: 0xff16, type: 0x44 }, // long str
            description: "Overflow for device_config: a single ZCL write caps at ~74 characters, so a config string longer than that has to be split, with the remainder written here. Appended onto device_config in RAM before it is parsed, so the result is identical to one long string -- only the over-the-air write needs to happen in two parts.",
            zigbeeCommandOptions: {timeout: 30_000},
            validate: (value) => {
                assertString(value);
                if (value.length > 256) throw new Error("Length of device_config_ext is greater than 256");
                if (value.length && !value.endsWith(";")) throw new Error("Should end with ;");
            },
            entityCategory: "config",
        }),
    deviceConfig: (name, endpointName) =>
        text({
            name,
            endpointName,
            access: "ALL",
            cluster: "genBasic",
            attribute:  { ID: 0xff00, type: 0x44 }, // long str
            description: "Current configuration of the device",
            zigbeeCommandOptions: {timeout: 30_000},
            validate: (value) => {
                assertString(value);
                
                const validatePin = (pin) => {
                    const validPins = [
                        "A0", "A1", "A2", "A3", "A4", "A5", "A6","A7",
                        "B0", "B1", "B2", "B3", "B4", "B5", "B6","B7",
                        "C0", "C1", "C2", "C3", "C4", "C5", "C6","C7",
                        "D0", "D1", "D2", "D3", "D4", "D5", "D6","D7",
                    ];
                    if (!validPins.includes(pin)) throw new Error(`Pin ${pin} is invalid`);
                }

                if (value.length > 256) throw new Error('Length of config is greater than 256');
                if (!value.endsWith(';')) throw new Error('Should end with ;');
                const parts = value.slice(0, -1).split(';');  // Drop last ;
                if (parts.length < 2) throw new Error("Model and/or manufacturer missing");
                for (const part of parts.slice(2)) {
                    if (part == 'SLP') {
                        continue;   
                    } else if (part == 'DR') {
                        continue;   // dimmer levels raw (no min..max scaling)
                    } else if (part.startsWith('LP')) {
                        if (!/^LP\d+$/.test(part)) {
                            throw new Error(`Latching pulse option ${part} is invalid. Use LP<N>, e.g. LP100`);
                        }
                    } else if (part.startsWith('DM')) {
                        if (!/^DM\d+$/.test(part)) {
                            throw new Error(`Dimmer count option ${part} is invalid. Use DM<N>, e.g. DM2`);
                        }
                    } else if (part[0] == 'D') {
                        if (!/^D\d+$/.test(part)) {
                            throw new Error(`Debounce option ${part} is invalid. Use D<N>, e.g. D100 or D0`);
                        }
                    } else if (part.startsWith('BT')) {
                        validatePin(part.slice(2,4));
                    } else if (part.startsWith('ST')) {
                        if (!/^ST[0-9A-Fa-f]{2}$/.test(part)) {
                            throw new Error(`Datapoint ${part} is invalid. Use ST<hh>, e.g. ST01`);
                        }
                    } else if (part.startsWith('SW')) {
                        // SWD pins towards the secondary MCU: SW<clk><dio>, e.g. SWD3D7
                        if (part.length != 6) {
                            throw new Error(`SWD pins ${part} are invalid. Use SW<clk><dio>, e.g. SWD3D7`);
                        }
                        validatePin(part.slice(2,4));
                        validatePin(part.slice(4,6));
                    } else if (part.startsWith('IT')) {
                        if (!/^IT[0-9A-Fa-f]{2}[124]([0-9A-Fa-f]{2}|[0-9A-Fa-f]{8})$/.test(part)) {
                            throw new Error(`Datapoint init ${part} is invalid. Use IT<hh><t><v>, e.g. IT67101`);
                        }
                    } else if (part.startsWith('RT')) {
                        // relay driven through the Tuya secondary MCU: RT<hh> (hex DP id)
                        if (!/^RT[0-9A-Fa-f]{2}([0-9A-Fa-f]{2})?$/.test(part)) {
                            throw new Error(`Datapoint ${part} is invalid. Use RT<hh> or RT<hh><countdown hh>, e.g. RT18 or RT181E`);
                        }
                    } else if (part.startsWith('PT')) {
                        // device-wide power-on-behaviour datapoint: PT<hh>
                        // Must come before the P branch below, which is the
                        // dimmer map and would otherwise swallow it.
                        if (!/^PT[0-9A-Fa-f]{2}$/.test(part)) {
                            throw new Error(`Power-on datapoint ${part} is invalid. Use PT<hh>, e.g. PT26`);
                        }
                    } else if (part[0] == 'W') {
                        // UART pins towards the secondary MCU: W<tx><rx>
                        validatePin(part.slice(1,3));
                        validatePin(part.slice(3,5));
                    } else if (part[0] == 'Y') {
                        // baudrate of that UART
                        if (!/^Y\d+$/.test(part)) {
                            throw new Error(`Baudrate ${part} is invalid. Use Y<n>, e.g. Y9600`);
                        }
                    } else if (part[0] == 'B' || part[0] == 'S') {
                        validatePin(part.slice(1,3));
                        if (!["u", "U", "d", "f"].includes(part[3])) {
                            throw new Error(`Pull up down ${part[3]} is invalid. Valid options are u, U, d, f`);
                        } 
                    } else if (part[0] == 'X') {
                        validatePin(part.slice(1,3));
                        validatePin(part.slice(3,5));
                        if (!["u", "U", "d", "f"].includes(part[5])) {
                            throw new Error(`Pull up down ${part[5]} is invalid. Valid options are u, U, d, f`);
                        }
                    } else if (part[0] == 'C') {
                        validatePin(part.slice(1,3));
                        validatePin(part.slice(3,5));
                    } else if (part[0] == 'L' || part[0] == 'R' || part[0] == 'I') {
                        validatePin(part.slice(1,3));
                    } else if(part[0] == 'M') {
                        ;
                    } else if(part[0] == 'U') {
                        if (!/^U[0-9A-Fa-f]{2}$/.test(part)) {
                            throw new Error(`Universal power-on DPID ${part} is invalid. Use U<hh> (hex), e.g. U0E`);
                        }
                    } else if(part[0] == 'i') {
                        ; // TODO: write validation
                    } else if (part[0] == 'P') {
                        // Pxx... per-dimmer DPID mapping; only the 0-based dimmer
                        // sequence index (2 hex digits) is validated here.
                        if (!/^P[0-9A-Fa-f]{2}[OLSMX][0-9A-Fa-f]{2}/.test(part)) {
                            throw new Error(`Dimmer DPID map ${part} is invalid. Use P<idx>O<oo>L<ll>S<ss>M<mm>X<xx> (hex), e.g. P08O01L08`);
                        }
                    } else {
                        throw new Error(`Invalid entry ${part}. Should start with one of B, BT, C, D, DM, I, IT, L, M, P, PT, R, RT, S, SLP, ST, SW, W, X, Y, Z, i`);
                    }
                }
            },
            entityCategory: "config",
        }),
    coverSwitchPressAction: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            access: "STATE_GET",
            lookup: { 
                released: 0, 
                open: 1, 
                close: 2,
                stop: 3,
                long_open: 4,
                long_close: 5
            },
            cluster: "genMultistateInput",
            attribute: "presentValue",
            description: "Cover switch button press action",
            entityCategory: "diagnostic"
        }),
    coverSwitchType: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            lookup: { toggle: 0, momentary: 1 },
            cluster: 0xFC01,
            attribute: {ID: 0x0000, type: Zcl.DataType.ENUM8},
            description: "Type of cover switch: toggle (rocker) or momentary (push button)",
            entityCategory: "config",
        }),
    coverSwitchCoverIndex: (name, endpointName, output_cnt) =>
        enumLookup({
            name,
            endpointName,
            lookup: Object.fromEntries([
                ['detached', 0],
                ...Array.from({ length: output_cnt || 2 }, (_, i) => [`cover_${i + 1}`, i + 1])
            ]),
            cluster: 0xFC01,
            attribute: {ID: 0x0001, type: Zcl.DataType.UINT8},
            description: "Which cover to control locally (detached = no local control)",
            entityCategory: "config",
        }),
    coverSwitchInvert: (name, endpointName) =>
        binary({
            name,
            endpointName,
            valueOn: ["ON", 1],
            valueOff: ["OFF", 0],
            cluster: 0xFC01,
            attribute: {ID: 0x0002, type: Zcl.DataType.BOOLEAN},
            description: "Inverts UP/DOWN direction for inputs",
            access: "ALL",
            entityCategory: "config",
        }),
    coverSwitchLocalMode: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            lookup: { immediate: 0, short_press: 1, long_press: 2, hybrid: 3 },
            cluster: 0xFC01,
            attribute: {ID: 0x0003, type: Zcl.DataType.ENUM8},
            description: "When to trigger local cover: immediate (start/stop on press), short_press (trigger on release), long_press (trigger after long press duration), hybrid (trigger on release or continuous movement while held). Only affects momentary switches",
            entityCategory: "config",
        }),
    coverSwitchBindedMode: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            lookup: { immediate: 0, short_press: 1, long_press: 2, hybrid: 3 },
            cluster: 0xFC01,
            attribute: {ID: 0x0004, type: Zcl.DataType.ENUM8},
            description: "When to send commands to bound devices: immediate (start/stop on press), short_press (trigger on release), long_press (trigger after long press duration), hybrid (trigger on release or continuous movement while held). Only affects momentary switches",
            entityCategory: "config",
        }),
    coverSwitchLongPressDuration: (name, endpointName) =>
        numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: 0xFC01,
            attribute: {ID: 0x0005, type: Zcl.DataType.UINT16},
            description: "Threshold in milliseconds to distinguish short press from long press",
            valueMin: 0,
            valueMax: 5000,
            entityCategory: "config",
        }),
    coverMoving: (name, endpointName) =>
        enumLookup({
            name,
            endpointName,
            access: "STATE",
            lookup: {
                stopped: 0,
                opening: 1,
                closing: 2
            },
            cluster: "closuresWindowCovering",
            attribute: {ID: 0xff00, type: Zcl.DataType.ENUM8},
            description: "Cover movement status",
            entityCategory: "diagnostic",
        }),
    coverMotorReversal: (name, endpointName) =>
        binary({
            name,
            endpointName,
            valueOn: [true, 1],
            valueOff: [false, 0],
            cluster: "closuresWindowCovering",
            attribute: {ID: 0xff01, type: Zcl.DataType.BOOLEAN},
            description: "Reverse motor direction (swap OPEN/CLOSE relays)",
            entityCategory: "config",
        }),
    coverOpenTime: (name, endpointName) =>
        numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: "closuresWindowCovering",
            attribute: {ID: 0xff02, type: Zcl.DataType.UINT16},
            description: "Travel time for the OPENING direction (0.1 s precision). " +
                         "For symmetric covers you only need to set this value. " +
                         "Set both open_time and close_time to 0 to enter manual mode: " +
                         "position tracking is disabled, the motor runs until stopped or " +
                         "until a 5-minute safety timeout, and position commands are ignored.",
            valueMin: 0,
            valueMax: 300,
            valueStep: 0.1,
            scale: 10,
            unit: "s",
            entityCategory: "config",
        }),
    coverCloseTime: (name, endpointName) =>
        numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: "closuresWindowCovering",
            attribute: {ID: 0xff03, type: Zcl.DataType.UINT16},
            description: "Travel time for the CLOSING direction (0.1 s precision). " +
                         "Set to 0 to use the same value as open_time. " +
                         "Set explicitly only if closing speed differs from opening speed.",
            valueMin: 0,
            valueMax: 300,
            valueStep: 0.1,
            scale: 10,
            unit: "s",
            entityCategory: "config",
        }),
    coverOpenDeadzone: (name, endpointName) =>
        numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: "closuresWindowCovering",
            attribute: {ID: 0xff04, type: Zcl.DataType.UINT16},
            description: "Mechanical deadzone at the OPEN end (100%) as a percentage of " +
                         "total travel. Motor movement within this zone does not change the " +
                         "reported position.",
            valueMin: 0,
            valueMax: 50,
            unit: "%",
            entityCategory: "config",
        }),
    coverClosedDeadzone: (name, endpointName) =>
        numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: "closuresWindowCovering",
            attribute: {ID: 0xff05, type: Zcl.DataType.UINT16},
            description: "Mechanical deadzone at the CLOSED end (0%) as a percentage of " +
                         "total travel. Motor movement within this zone does not change the " +
                         "reported position.",
            valueMin: 0,
            valueMax: 50,
            unit: "%",
            entityCategory: "config",
        }),
    dimmerMinLevel: (name, endpointName) =>
        numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: "lightingBallastCfg",
            attribute: "minLevel",
            description: "Minimum brightness of the dimmer (MCU setting). With level scaling (default) brightness 1 is this level.",
            valueMin: 1,
            valueMax: 254,
            entityCategory: "config",
        }),
    dimmerMaxLevel: (name, endpointName) =>
        numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: "lightingBallastCfg",
            attribute: "maxLevel",
            description: "Maximum brightness of the dimmer (MCU setting). With level scaling (default) brightness 254 is this level.",
            valueMin: 1,
            valueMax: 254,
            entityCategory: "config",
        }),
    dimmerOnLevel: (name, endpointName) => {
        const extend = numeric({
            name,
            endpointNames: endpointName ? [endpointName] : undefined,
            cluster: "genLevelCtrl",
            attribute: "onLevel",
            description: "Brightness the light comes on at, from the wall switch or an On command, set without a round trip through the network. 255 = the level it had before.",
            valueMin: 1,
            valueMax: 255,
            entityCategory: "config",
        });
        extend.exposes = extend.exposes.map((x) => (typeof x === "function" ? x : x.withPreset("previous", 255, "The brightness it had before")));
        return extend;
    },
    dimmerSwitchType: (name, endpointName) => {
        const lookup = { toggle: 0, state: 1, momentary: 2 };
        const extend = enumLookup({
            name,
            endpointName,
            lookup,
            cluster: "genOnOffSwitchCfg",
            attribute: "switchType",
            label: "Wall switch type",
            description: "The wall switch on this channel's S input: toggle (a rocker switch, every flip toggles the light), state (a rocker switch, its position is on or off), momentary (a push button: a press toggles, a hold dims)",
            entityCategory: "config",
        });
        // ZCL marks switchType read-only and zigbee-herdsman refuses to write it
        // by name; the firmware accepts the write, so write it by attribute id.
        // (Overriding genOnOffSwitchCfg with a custom cluster instead left the
        // cluster nameless in z2m, which crashed the frontend's Bind page.)
        extend.toZigbee = extend.toZigbee.map((converter) => ({
            ...converter,
            convertSet: async (entity, key, value, meta) => {
                const raw = lookup[value];
                if (raw === undefined) {
                    throw new Error(`${key}: invalid value '${value}'`);
                }
                await entity.write("genOnOffSwitchCfg", {0x0000: {value: raw, type: Zcl.DataType.ENUM8}});
                return {state: {[key]: value}};
            },
        }));
        return extend;
    },
    // Power-on behaviour of an MCU dimmer that may run Open Dimmer: per
    // channel with it (DPs 103/104), else one setting for all channels (the
    // shared DP 14: a write on any endpoint sets every channel).
    dimmerPowerOnPerChannel: (dimmerNames) => {
        const lookup = {off: 0, on: 1, previous: 255};
        const names = Object.keys(lookup);
        const shared = e.enum("power_on_behavior", ea.ALL, names)
            .withDescription("Behavior when power is restored (previous = memory), all channels")
            .withCategory("config");
        return {
            isModernExtend: true,
            // Per channel with Open Dimmer: in each channel's group (dimmerPowerOnChannel).
            exposes: [(device, options) => (ownMcuShown(device, options, 2) ? [] : [shared])],
            fromZigbee: [{
                cluster: "genOnOff",
                type: ["attributeReport", "readResponse"],
                convert: (model, msg) => {
                    const v = msg.data?.startUpOnOff;
                    const name = endpointNameOf(model, msg);
                    if (v === undefined || !dimmerNames.includes(name)) return;
                    const value = names.find((k) => lookup[k] === v) ?? v;
                    const out = {[`power_on_behavior_${name}`]: value};
                    if (name === dimmerNames[0]) out.power_on_behavior = value;
                    return out;
                },
            }],
            toZigbee: [{
                key: ["power_on_behavior"],
                convertSet: async (entity, key, value, meta) => {
                    if (!Object.prototype.hasOwnProperty.call(lookup, value)) throw new Error(`${key}: one of ${names.join(", ")}`);
                    if (meta.endpoint_name) {
                        // One channel (Open Dimmer): its own power-on DP.
                        await entity.write("genOnOff", {startUpOnOff: lookup[value]});
                        return {state: {[key]: value}};
                    }
                    // The shared setting: written on every channel, so it also
                    // sets every channel of Open Dimmer, which keeps one per
                    // channel (a stock MCU just gets the same value again).
                    const map = typeof meta.mapped?.endpoint === "function" ? meta.mapped.endpoint(meta.device) : {};
                    const channels = dimmerNames.map((name) => [name, map[name] !== undefined ? meta.device.getEndpoint(map[name]) : undefined])
                        .filter(([, endpoint]) => endpoint);
                    const state = {[key]: value};
                    for (const [name, endpoint] of channels.length ? channels : [[undefined, entity]]) {
                        await endpoint.write("genOnOff", {startUpOnOff: lookup[value]});
                        if (name) state[`${key}_${name}`] = value;
                    }
                    return {state};
                },
                convertGet: async (entity) => {
                    await entity.read("genOnOff", ["startUpOnOff"]);
                },
            }],
        };
    },
    // The per-channel power-on expose of one channel (Open Dimmer); its converters
    // are in dimmerPowerOnPerChannel.
    dimmerPowerOnChannel: (endpointName) => {
        const expose = e.enum("power_on_behavior", ea.ALL, ["off", "on", "previous"])
            .withEndpoint(endpointName)
            .withDescription("Behavior of this channel when power is restored (previous = memory; Open Dimmer firmware)")
            .withCategory("config");
        return {
            isModernExtend: true,
            exposes: [(device, options) => (ownMcuShown(device, options, 2) ? [expose] : [])],
        };
    },
    // Wall switch gestures of the Open Dimmer firmware (momentary switch type):
    // genMultistateInput presentValue on the dimmer endpoint = sequence << 4 |
    // gesture. The light itself acts on every press regardless.
    // Open Dimmer 2.2 adds gesture 6, press (fast_press): only its action list has it, so the
    // list of every other device stays as it was.
    dimmerGestures: (dimmerNames) => {
        const kinds = ["single", "double", "triple", "hold", "release"];
        const kinds22 = [...kinds, "press"];
        const description = "Wall switch gesture (Open Dimmer firmware, momentary switch type, or any type in the detached switch_mode); in the normal switch_mode the light acts on every press too";
        const expose = e.action(dimmerNames.flatMap((n) => kinds.map((k) => `${k}_${n}`)))
            .withDescription(description);
        const expose22 = e.action(dimmerNames.flatMap((n) => kinds22.map((k) => `${k}_${n}`)))
            .withDescription(description);
        return {
            isModernExtend: true,
            exposes: [(device, options) => (ownMcuShown(device, options, OPEN_DIMMER_2_2) ? [expose22]
                                            : ownMcuShown(device, options, 2) ? [expose] : [])],
            fromZigbee: [{
                cluster: "genMultistateInput",
                type: ["attributeReport"],
                convert: (model, msg, publish, options) => {
                    const name = endpointNameOf(model, msg);
                    const g = (msg.data?.presentValue ?? 0) & 0x0f;
                    const list = ownMcuShown(msg.device, options, OPEN_DIMMER_2_2) ? kinds22 : kinds;
                    if (!dimmerNames.includes(name) || g < 1 || g > list.length) return;
                    return {action: `${list[g - 1]}_${name}`};
                },
            }],
        };
    },
    // A definition's onEvent for a dimmer board: OnLevel lives in the module,
    // not in the MCU, and a module restart can reset it. It has no reporting
    // (the module's reporting table is small), so read it again when the
    // module announces itself after a restart; the page then shows the value
    // the module really uses.
    dimmerOnLevelRefresh: (endpointIds) => async (...args) => {
        // Zigbee2MQTT 2.x passes one event, 1.x (type, data, device, ...).
        const [first] = args;
        const type = typeof first === "string" ? first : first?.type;
        const device = typeof first === "string" ? args[2] : first?.data?.device;
        if (type !== "deviceAnnounce" || !device) return;
        for (const id of endpointIds) {
            try {
                await device.getEndpoint(id)?.read("genLevelCtrl", ["onLevel"]);
            } catch (error) {
                // not answered now; read at the next announce, configure or refresh
            }
        }
    },
    // A per-channel attribute of Open Dimmer 2.2.0 on the channel's own endpoint: shown, stored
    // and written like the other Open Dimmer settings (ownMcuShown). attr: its id (or name, for
    // one zigbee-herdsman knows), type, and what a report means: fromValue(raw) -> published.
    dimmerOwnAttr: ({expose, endpointName, cluster, attr, type, fromValue, toValue, extraFz,
                     minMcu = OPEN_DIMMER_2_2}) => {
        const property = `${expose.name}_${endpointName}`;
        const shown = (device, options) => ownMcuShown(device, options, minMcu);
        return {
            isModernExtend: true,
            exposes: [(device, options) => (shown(device, options) ? [expose] : [])],
            fromZigbee: [{
                cluster,
                type: ["attributeReport", "readResponse"],
                convert: (model, msg, publish, options) => {
                    if (endpointNameOf(model, msg) !== endpointName || !shown(msg.device, options)) return;
                    const out = {};
                    const raw = msg.data?.[attr.name ?? attr.ID] ?? msg.data?.[attr.ID];
                    if (raw !== undefined) out[property] = fromValue(raw);
                    Object.assign(out, extraFz?.(msg.data, property) ?? {});
                    return Object.keys(out).length ? out : undefined;
                },
            }],
            toZigbee: [{
                key: [expose.name],
                endpoints: [endpointName],
                convertSet: async (entity, key, value, meta) => {
                    // Not shown for this device (a stock MCU, an older Open Dimmer): the
                    // module would not pass it on, so no state that is not there.
                    if (!shown(meta.device, meta.options)) throw new Error(`${key}: an Open Dimmer ${minMcu} setting (see open_dimmer_settings)`);
                    const raw = toValue(value);
                    await entity.write(cluster, {[attr.ID]: {value: raw, type}});
                    return {state: {[key]: fromValue(raw)}};
                },
                convertGet: async (entity) => {
                    await entity.read(cluster, [attr.name ?? attr.ID]);
                },
            }],
        };
    },
    // Auto-off (Open Dimmer 2.2.0, DP 6 / 12 by genOnOff 0xff10): the channel goes off that
    // many seconds after it was set; any off ends it (so an off report publishes 0).
    dimmerCountdown: (endpointName) => {
        const expose = e.numeric("countdown", ea.ALL).withEndpoint(endpointName)
            .withValueMin(0).withValueMax(86400).withUnit("s")
            .withDescription("Auto-off: the channel turns off this many seconds after it was set, while on (0 = none; any off ends it; Open Dimmer firmware)");
        return romasku.dimmerOwnAttr({
            expose, endpointName, cluster: "genOnOff", attr: {ID: 0xff10}, type: Zcl.DataType.UINT32,
            fromValue: (v) => v,
            toValue: (value) => {
                const v = Number(value);
                if (!Number.isInteger(v) || v < 0 || v > 86400) throw new Error("countdown: an integer 0..86400");
                return v;
            },
            extraFz: (data, property) => (data?.onOff === 0 ? {[property]: 0} : undefined),
        });
    },
    // Wall switch mode (Open Dimmer 2.2.0, DP 162 / 163): normal, detached (the switch only
    // reports its gestures), off (ignored).
    dimmerSwitchMode: (endpointName) => {
        const lookup = {normal: 0, detached: 1, off: 2};
        const names = Object.keys(lookup);
        const expose = e.enum("switch_mode", ea.ALL, names).withEndpoint(endpointName).withLabel("Wall switch mode")
            .withDescription("Wall switch: normal (it switches and dims the light), detached (the light only follows the network; the switch's gestures are still reported as action, a toggle or state switch's change as a single) or off (ignored). Open Dimmer firmware")
            .withCategory("config");
        return romasku.dimmerOwnAttr({
            expose, endpointName, cluster: "genOnOffSwitchCfg", attr: {ID: 0xff10}, type: Zcl.DataType.ENUM8,
            fromValue: (v) => names.find((k) => lookup[k] === v) ?? v,
            toValue: (value) => {
                if (!Object.prototype.hasOwnProperty.call(lookup, value)) throw new Error(`switch_mode: one of ${names.join(", ")}`);
                return lookup[value];
            },
        });
    },
    // Level after a power cut (Open Dimmer 2.2.0, DP 164 / 165): ZCL StartUpCurrentLevel,
    // 1..254, 0 the minimum, 255 the previous one. A number throughout (a Home Assistant
    // number entity), the presets name the two ends; their names are taken when written.
    dimmerStartupLevel: (endpointName) => {
        const presets = {minimum: 0, previous: 255};
        const expose = e.numeric("current_level_startup", ea.ALL).withEndpoint(endpointName).withLabel("Power-on level")
            .withValueMin(0).withValueMax(255)
            .withPreset("minimum", 0, "The channel's minimum brightness")
            .withPreset("previous", 255, "The brightness it had before")
            .withDescription("Brightness after a power cut, 1..254; 0 = the minimum, 255 = the one it had before (whether it comes on is power_on_behavior; Open Dimmer firmware)")
            .withCategory("config");
        return romasku.dimmerOwnAttr({
            expose, endpointName, cluster: "genLevelCtrl", attr: {ID: 0x4000, name: "startUpCurrentLevel"},
            type: Zcl.DataType.UINT8,
            fromValue: (v) => v,
            toValue: (value) => {
                if (Object.prototype.hasOwnProperty.call(presets, value)) return presets[value];
                const v = Number(value);
                if (!Number.isInteger(v) || v < 0 || v > 255) throw new Error("current_level_startup: 0..255, minimum or previous");
                return v;
            },
        });
    },
    // Fade time of the brightness changes without a transition while lit, as the time for
    // the whole range (Level Control 0xff12, 1/10 s, kept by the module; 0xFFFF unset: the
    // module's default, the whole range in 2 s, the MCU's own rate). Open Dimmer 2.1.14 and
    // later (its transition time).
    // Unset is shown as 2 (s) and a written 2 is stored as unset: the module's default is the
    // whole range in 2 s at the MCU's own rate, a 2.0 s of its own differs only by the ~6 % the
    // module computes from the channel's min..max instead of 0..1000. Unset also: the preset
    // default, an empty value or a negative one.
    dimmerBrightnessFadeTime: (endpointName) => {
        const expose = e.numeric("brightness_fade_time", ea.ALL).withEndpoint(endpointName)
            .withValueMin(0).withValueMax(60).withValueStep(0.1).withUnit("s")
            .withPreset("default", 2, "The module's default: the whole range in 2 s")
            .withDescription("Fade time of brightness changes from Zigbee2MQTT / Home Assistant while the light is on, as the time for the whole range (a smaller change is quicker). 2 s is the module's default. A transition (in the message or the device option transition) wins. Open Dimmer 2.1.14 and later")
            .withCategory("config");
        return romasku.dimmerOwnAttr({
            expose, endpointName, cluster: "genLevelCtrl", attr: {ID: 0xff12}, type: Zcl.DataType.UINT16,
            minMcu: "2.1.14",
            fromValue: (v) => (v === 0xffff ? 2 : v / 10),
            toValue: (value) => {
                if (value === "default" || value === "" || value === null || value === undefined) return 0xffff;
                const v = Number(value);
                if (Number.isNaN(v) || v > 60) throw new Error("brightness_fade_time: 0..60 s, or default");
                return v < 0 || Math.round(v * 10) === 20 ? 0xffff : Math.round(v * 10);
            },
        });
    },
    // Fast press (Open Dimmer 2.2.0, DP 170 / 171 by genOnOffSwitchCfg 0xff11): in the
    // detached switch_mode a momentary switch's press is reported at once (action press_*).
    dimmerFastPress: (endpointName) => {
        const expose = e.binary("fast_press", ea.ALL, true, false).withEndpoint(endpointName).withLabel("Wall switch fast press")
            .withDescription("In the detached switch_mode a momentary switch's press is reported at once as action press (no single / double / triple then; hold and release as before; Open Dimmer firmware)")
            .withCategory("config");
        return romasku.dimmerOwnAttr({
            expose, endpointName, cluster: "genOnOffSwitchCfg", attr: {ID: 0xff11}, type: Zcl.DataType.BOOLEAN,
            fromValue: (v) => v === 1 || v === true,
            toValue: (value) => {
                if (value === true || value === "ON" || value === "true") return 1;
                if (value === false || value === "OFF" || value === "false") return 0;
                throw new Error("fast_press: true or false");
            },
        });
    },
    // The wall switch's bindings (dimmer-only builds: genOnOffSwitchCfg 0xff12..0xff14, kept
    // by the module): what goes to the lights and groups bound to the channel's wall switch
    // endpoint (3 / 4 on the ZDMS16-2). An enum each, its values by their index.
    dimmerBindSetting: (name, endpointName, attrId, values, label, description) => {
        const expose = e.enum(name, ea.ALL, values).withEndpoint(endpointName).withLabel(label)
            .withDescription(description).withCategory("config");
        return romasku.dimmerOwnAttr({
            expose, endpointName, cluster: "genOnOffSwitchCfg", attr: {ID: attrId}, type: Zcl.DataType.ENUM8,
            fromValue: (v) => values[v] ?? v,
            toValue: (value) => {
                const v = values.indexOf(value);
                if (v < 0) throw new Error(`${name}: one of ${values.join(", ")}`);
                return v;
            },
        });
    },
    // ZCL ExecuteIfOff (Level Control Options bit 0, kept by the module): a brightness change
    // without on/off while off sets the brightness the light comes on at, the light stays off.
    dimmerExecuteIfOff: (endpointName) => {
        const expose = e.binary("execute_if_off", ea.ALL, true, false).withEndpoint(endpointName)
            .withDescription("A brightness change without on/off while the light is off sets the brightness it comes on at (it stays off); off: such a change is ignored while off")
            .withCategory("config");
        return romasku.dimmerOwnAttr({
            expose, endpointName, cluster: "genLevelCtrl", attr: {ID: 0x000f, name: "options"},
            type: Zcl.DataType.BITMAP8,
            fromValue: (v) => (v & 1) === 1,
            toValue: (value) => {
                if (value === true || value === "ON" || value === "true") return 1;
                if (value === false || value === "OFF" || value === "false") return 0;
                throw new Error("execute_if_off: true or false");
            },
        });
    },
    // OnLevel of the wall switch (Level Control 0xff11, kept by the module): a wall switch on
    // goes there, a network On to on_level. 255: as on_level.
    dimmerOnLevelLocal: (endpointName) => {
        const expose = e.numeric("on_level_local", ea.ALL).withEndpoint(endpointName).withLabel("On level (wall switch)")
            .withValueMin(1).withValueMax(255)
            .withDescription("Brightness the light comes on at from the wall switch, set without a round trip through the network. 255 (default) = as on_level")
            .withCategory("config");
        return romasku.dimmerOwnAttr({
            expose, endpointName, cluster: "genLevelCtrl", attr: {ID: 0xff11}, type: Zcl.DataType.UINT8,
            fromValue: (v) => v,
            toValue: (value) => {
                const v = Number(value);
                if (!Number.isInteger(v) || v < 1 || v > 255) throw new Error("on_level_local: an integer 1..255");
                return v;
            },
        });
    },
    // Start boost (Open Dimmer 2.2.0, DP 168 / 169): coming on from dark at full that long.
    // Shown in seconds like the other times; the attribute is in ms (the MCU keeps steps of 50).
    dimmerStartBoost: (endpointName) => {
        const expose = e.numeric("start_boost", ea.ALL).withEndpoint(endpointName)
            .withValueMin(0).withValueMax(0.75).withValueStep(0.05).withUnit("s")
            .withDescription("Coming on from dark the output is at full this long, then goes to the brightness: for LED drivers that need a kick to start (0 = none; Open Dimmer firmware)")
            .withCategory("config");
        return romasku.dimmerOwnAttr({
            expose, endpointName, cluster: "lightingBallastCfg", attr: {ID: 0xff10}, type: Zcl.DataType.UINT16,
            fromValue: (v) => v / 1000,
            toValue: (value) => {
                const v = Number(value);
                if (value === "" || value === null || Number.isNaN(v) || v < 0 || v > 0.75) throw new Error("start_boost: 0..0.75 s");
                return Math.round(v * 1000);
            },
        });
    },
    // Identify (Open Dimmer 2.2.0, DP 166 / 167): every channel blinks for a few seconds (the
    // one with a lamp shows which module this is).
    dimmerIdentify: (dimmerNames) => {
        const expose = e.enum("identify", ea.SET, ["identify"])
            .withDescription("Blink the lamps of this module for 3 seconds (Open Dimmer firmware)")
            .withCategory("config");
        return {
            isModernExtend: true,
            exposes: [(device, options) => (ownMcuShown(device, options, OPEN_DIMMER_2_2) ? [expose] : [])],
            toZigbee: [{
                key: ["identify"],
                convertSet: async (entity, key, value, meta) => {
                    if (!ownMcuShown(meta.device, meta.options, OPEN_DIMMER_2_2)) throw new Error("identify: an Open Dimmer 2.2 setting (see open_dimmer_settings)");
                    const map = typeof meta.mapped?.endpoint === "function" ? meta.mapped.endpoint(meta.device) : {};
                    const ids = dimmerNames.map((name) => map[name]).filter((id) => id !== undefined);
                    for (const id of ids.length ? ids : [entity.ID]) {
                        await meta.device.getEndpoint(id).command("genIdentify", "identify", {identifytime: 3});
                    }
                },
            }],
        };
    },
    // Health report of Open Dimmer (Basic 0xff48 on endpoint 1, octet string format 1; the
    // module sends it about every 10 min to the coordinator once configure has bound genBasic).
    mcuHealth: () => {
        const causes = [[7, "low_power"], [6, "window_watchdog"], [5, "watchdog"], [4, "software"],
                        [3, "power"], [2, "pin"], [1, "option_bytes"]];
        const exposes = [
            e.numeric("mcu_uptime", ea.STATE_GET).withUnit("min").withLabel("MCU uptime")
                .withDescription("Time since the dimming MCU's last start (Open Dimmer firmware)"),
            e.text("mcu_reset_cause", ea.STATE_GET).withLabel("MCU reset cause")
                .withDescription("Why the dimming MCU last started: power (power-up or brown-out), software (an update or a fault), watchdog, pin... (Open Dimmer firmware)"),
            e.numeric("mcu_zero_cross_losses", ea.STATE_GET).withLabel("MCU zero-cross losses")
                .withDescription("Times the dimming MCU lost its lock on the mains zero cross since its start (Open Dimmer firmware)"),
            e.numeric("mcu_uart_rx_losses", ea.STATE_GET).withLabel("MCU UART receive losses")
                .withDescription("Bytes from the Zigbee module the dimming MCU lost since its start (Open Dimmer firmware)"),
            e.numeric("mcu_temperature", ea.STATE_GET).withUnit("°C").withLabel("MCU temperature")
                .withDescription("Die temperature of the dimming MCU (Open Dimmer firmware)"),
            e.binary("mcu_settings_ring_moved", ea.STATE_GET, true, false).withLabel("MCU settings relocated")
                .withDescription("The dimming MCU keeps its settings in the flash area of 2.1.14 and later, so firmware over 12 KB can be installed (Open Dimmer firmware)"),
        ].map((x) => x.withCategory("diagnostic"));
        return {
            isModernExtend: true,
            exposes: [(device, options) => (ownMcuShown(device, options, OPEN_DIMMER_2_2) ? exposes : [])],
            fromZigbee: [{
                cluster: "genBasic",
                type: ["attributeReport", "readResponse"],
                convert: (model, msg, publish, options) => {
                    const raw = msg.data?.[0xff48];
                    if (msg.endpoint?.ID !== 1 || raw === undefined || !ownMcuShown(msg.device, options, OPEN_DIMMER_2_2)) return;
                    const b = Buffer.from(raw);
                    if (b.length < 17 || b[0] !== 1) return;          // empty before the first report
                    const flags = b[14];
                    const reset = b[13];
                    const t = b.readInt16LE(15);
                    // Bytes 17..20 (the temperature sensor's factory calibration) are not shown.
                    return {
                        mcu_uptime: b.readUInt32LE(1),
                        mcu_zero_cross_losses: b.readUInt32LE(5),
                        mcu_uart_rx_losses: b.readUInt32LE(9),
                        mcu_reset_cause: causes.filter(([bit]) => reset & (1 << bit)).map(([, n]) => n).join(",") || "none",
                        mcu_settings_ring_moved: (flags & 0x02) !== 0,
                        mcu_temperature: t === -32768 ? null : t,
                    };
                },
            }],
            toZigbee: [{
                key: exposes.map((x) => x.name),
                convertGet: async (entity, key, meta) => {
                    await meta.device.getEndpoint(1).read("genBasic", [0xff48]);
                },
            }],
        };
    },
    // SWD master of the dimming MCU (src/zigbee/mcu_flasher.c): only on a module whose config
    // names its two wires (SW<clk><dio> token), and only shown with the device option
    // mcu_flasher: show (default hide: no expose, no state). One write of mcu_flasher starts a
    // job (the read-only surveys; update_bootloader also needs mcu_flasher_confirm = its name,
    // Basic 0xff52); mcu_flasher_status shows its result (Basic 0xff50 on endpoint 1, format 2:
    // 47 bytes, mcu_flasher_status_t; reported at every change and about once a second while
    // it runs).
    mcuFlasher: () => {
        const jobs = {survey_dp: 1, survey_full: 2, update_bootloader: 3, update_bootloader_force: 4, install: 5, abort: 0xff};
        const states = ["idle", "running", "done", "failed", "aborted", "armed"];
        const errors = ["", "SWCLK held low (power-cycle the module, then try again)", "no answer (wires? power?)",
                        "not the expected PY32F002A", "SWD transfer failed", "busy", "unknown job",
                        "not confirmed: write the job's name to mcu_flasher_confirm first (valid 60 s)",
                        "read-protected, unexpected option bytes or the bootloader write-protected",
                        "the MCU did not stop", "flash error (unlock, write protection or timeout)",
                        "the bootloader's checksum was wrong after writing it twice: the MCU stays stopped, its outputs off",
                        "not a factory MCU (install takes one that told a 1.x version and is read-protected)",
                        "the option bytes did not read back as written (not loaded; try again)",
                        "no attach in the armed power-ups",
                        "after the option reload not unprotected and erased",
                        "kept: the MCU may be without firmware, so its install stays armed (it goes on at the next power-up; abort drops it once the MCU answers)"];
        const exposes = [
            e.enum("mcu_flasher", ea.SET, Object.keys(jobs)).withLabel("MCU flasher")
                .withDescription("Start a job of the module's SWD link to the dimming MCU: survey_dp reads the debug port's id, survey_full the MCU's identity, option bytes and firmware checksums (both only read); update_bootloader writes the module's bootloader into the MCU if it differs (_force: always); install takes a factory MCU to Open Dimmer (ERASES its firmware and settings; then power-cycle the module when the status says so). Writing jobs need mcu_flasher_confirm first. Needs the SW token in device_config")
                .withCategory("config"),
            e.text("mcu_flasher_confirm", ea.SET).withLabel("MCU flasher confirm")
                .withDescription("A writing job runs only if its name was written here within the last 60 s (update_bootloader, update_bootloader_force or install); used once")
                .withCategory("config"),
            e.text("mcu_flasher_status", ea.STATE_GET).withLabel("MCU flasher status")
                .withDescription("State and result of the last MCU flasher job")
                .withCategory("diagnostic"),
        ];
        const shown = (options) => options?.mcu_flasher === "show";
        const hex = (v) => "0x" + (v >>> 0).toString(16).toUpperCase().padStart(8, "0");
        const describe = (b) => {
            const state = states[b[1]] ?? `state ${b[1]}`;
            const job = Object.keys(jobs).find((k) => jobs[k] === b[2]) ?? `job ${b[2]}`;
            const parts = [`${state} ${job}`];
            if (b[1] === 1) parts.push(b[3] === 11 ? `writing page ${b[46] || 32}/32` : `step ${b[3]}`);
            // page 255: an install past its option bytes, taken up at every power-up until done
            if (b[1] === 5) parts.push(`power-cycle the module now (${b[46] === 255 ? "the install goes on at every power-up" : `${b[46]} power-ups left`})` + (b[4] && b[4] !== 16 ? `; last attach stopped at step ${b[3]}` : ""));
            if (b[4]) parts.push(`error: ${errors[b[4]] ?? b[4]}` + (b[5] ? ` (SWD ${b[5]})` : ""));
            const w = (i) => b.readUInt32LE(i);
            if (w(8)) parts.push(`DPIDR ${hex(w(8))}`);
            if (w(16)) parts.push(`CPUID ${hex(w(16))}, IDCODE ${hex(w(20))}, AP ${hex(w(12))}`);
            if (w(24)) parts.push(`OPTR ${hex(w(24))} (RDP ${b[24] === 0xaa ? 0 : b[24] === 0xcc ? 2 : 1}), WRPR ${hex(b.readUInt16LE(28))}, option bytes CRC ${hex(w(32))}`);
            if (w(36)) parts.push(`ROM CRC ${hex(w(36))}`);
            if (w(40)) parts.push(`sector 0 CRC ${hex(w(40))}`);
            if (b[6]) parts.push(`bootloader v${b[6]}`);
            if (b[7]) parts.push(`app ${b[7] >> 6}.${(b[7] >> 4) & 3}.${b[7] & 15} build ${b.readUInt16LE(30)}`);
            else if (b[2] === 5 && b.readUInt16LE(30)) parts.push(`attached ${b.readUInt16LE(30)} ms after the module's start`);
            if (b[1] >= 2) parts.push(`${(b.readUInt16LE(44) / 1000).toFixed(1)} s`);
            return parts.join("; ");
        };
        return {
            isModernExtend: true,
            options: [
                e.enum("mcu_flasher", ea.SET, ["hide", "show"]).withLabel("MCU flasher")
                    .withDescription("The module's SWD link to the dimming MCU (two extra wires and the SW token in device_config): show its controls. hide (default): nothing shown"),
            ],
            exposes: [(device, options) => (shown(options) ? exposes : [])],
            fromZigbee: [{
                cluster: "genBasic",
                type: ["attributeReport", "readResponse"],
                convert: (model, msg, publish, options) => {
                    const raw = msg.data?.[0xff50];
                    if (msg.endpoint?.ID !== 1 || raw === undefined || !shown(options)) return;
                    const b = Buffer.from(raw);
                    if (b.length < 47 || b[0] !== 2) return;
                    return {mcu_flasher_status: describe(b)};
                },
            }],
            toZigbee: [{
                key: ["mcu_flasher", "mcu_flasher_status", "mcu_flasher_confirm"],
                convertSet: async (entity, key, value, meta) => {
                    if (!shown(meta.options)) throw new Error(`${key}: set the device option mcu_flasher to show first`);
                    if (key === "mcu_flasher_confirm") {
                        const text = String(value);
                        if (text.length > 23) throw new Error("mcu_flasher_confirm: at most 23 characters");
                        await meta.device.getEndpoint(1).write("genBasic", {0xff52: {value: text, type: 0x42}});
                        return;
                    }
                    if (jobs[value] === undefined) throw new Error(`mcu_flasher: unknown job ${value}`);
                    await meta.device.getEndpoint(1).write("genBasic", {0xff51: {value: jobs[value], type: 0x20}});
                },
                convertGet: async (entity, key, meta) => {
                    await meta.device.getEndpoint(1).read("genBasic", [0xff50]);
                },
            }],
        };
    },
    // Transitions of the dimmer channels. zigbee-herdsman-converters sends a level change
    // without a transition (neither the message's nor the device option transition) with time
    // 0, which Open Dimmer takes for "at once": those go out with 0xFFFF ("none": the module's
    // fade settings) instead, unless the MCU is known to be another one (while its version is
    // not known yet too: a stock MCU's module does not use the time, 0xFFFF and 0 give it the
    // same frames); a given time, 0 included, as zhc computes it. A stock MCU's module: zhc's
    // frames as they are. A plain on (no brightness, no transition in the message) goes out
    // as genOnOff on for every MCU, the option set or not (zhc would send a level move to its
    // remembered level when the option is set or after an off with a time: on then a level
    // to a stock MCU, zhc's level to Open Dimmer): the module's own on (fade_on_time, the
    // level it kept); zhc's "turned off with a transition" mark is cleared for it.
    // light()'s own level converters, wrapped, ahead of the channels' light() so they are
    // the ones taken (Zigbee2MQTT takes the first that matches); Move: unchanged (a rate).
    // The definition's transition option says what it means here (the converters' own one
    // is left out: the first option of a name is kept).
    dimmerTransition: (dimmerNames) => {
        const inner = light({endpointNames: dimmerNames, effect: false, powerOnBehavior: false});
        const keys = ["state", "brightness", "brightness_percent", "brightness_step", "brightness_step_onoff"];
        const levelCommands = ["moveToLevel", "moveToLevelWithOnOff", "step", "stepWithOnOff"];
        const unset = (meta) => meta.message?.transition == null &&
            (meta.options?.transition == null || meta.options.transition === "");
        // On without a brightness or a transition in the message (a toggle of a light not on too).
        const plainOn = (meta) => {
            const m = meta.message ?? {};
            const state = typeof m.state === "string" ? m.state.toLowerCase() : undefined;
            return m.brightness == null && m.brightness_percent == null && m.transition == null &&
                (state === "on" || (state === "toggle" && meta.state?.state !== "ON"));
        };
        const noTime = (entity) => new Proxy(entity, {
            get: (target, prop) => {
                if (prop === "command") {
                    return (cluster, command, payload, ...rest) => target.command(cluster, command,
                        cluster === "genLevelCtrl" && levelCommands.includes(command) && payload?.transtime === 0
                            ? {...payload, transtime: 0xffff} : payload, ...rest);
                }
                const v = Reflect.get(target, prop, target);
                return typeof v === "function" && prop !== "constructor" ? v.bind(target) : v;
            },
        });
        return {
            isModernExtend: true,
            options: [
                e.numeric("transition", ea.SET).withValueMin(0).withValueStep(0.1)
                    .withDescription("Transition time in seconds (e.g. 1.2) of brightness changes and off from Zigbee2MQTT / Home Assistant; a transition in the message wins. A plain on (no brightness) uses the module's fade_on_time. Empty: the module's fade settings (fade_on_time from dark, fade_off_time to off, brightness_fade_time while lit; unset, the module's default: the whole range in 2 s). 0: at once. The wall switch always takes the module's fade. Open Dimmer 2.1.14 and later; with the factory MCU the time is not used (the MCU fades by itself)."),
            ],
            toZigbee: inner.toZigbee.filter((c) => c.key?.some((k) => keys.includes(k))).map((c) => ({
                ...c,
                convertSet: async (entity, key, value, meta) => {
                    if (c.key.includes("state") && plainOn(meta)) {
                        globalStore.clearValue(entity, "turnedOffWithTransition");
                        meta = {...meta, options: {...meta.options, transition: undefined}};
                    }
                    return !knownNotOwnMcu(meta.device) && unset(meta)
                        ? c.convertSet(noTime(entity), key, value, meta) : c.convertSet(entity, key, value, meta);
                },
            })),
        };
    },
    dimmerPowerOnBehavior: (name, endpointName) =>
        enumLookup({
            name,
            ...(endpointName ? {endpointName} : {}),
            lookup: { off: 0, on: 1, previous: 255 },
            cluster: "genOnOff",
            attribute: "startUpOnOff",
            description: "Behavior when power is restored (previous = memory)",
            entityCategory: "config",
        }),
};

const definitions = [
    {
        zigbeeModel: [
            "TS0004-MC",
        ],
        model: "TYWB 4ch-RF",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-MC1",
        ],
        model: "TYWB 4ch-RF",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-MC2",
        ],
        model: "TYWB 4ch-RF",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-MC3",
        ],
        model: "TYWB 4ch-RF",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "Tuya-ZG-001",
        ],
        model: "ZG-001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-SC",
        ],
        model: "ZG-2002-RF",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-SC",
        ],
        model: "ZG-2002-RF",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "DEV-ZTU2",
        ],
        model: "Zigbee_SoC_Board_V2_(ZTU)",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-TD",
        ],
        model: "TS011F_din_smart_relay",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "WHD02-Aubess",
            "WHD02-Aubess-ED",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-AUB",
        ],
        model: "TMZ02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-AUB",
        ],
        model: "TS0003_switch_module_2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-custom",
        ],
        model: "TS0004_switch_module",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-AVB",
            "TS0001-Avatto-custom",
            "TS0001-AV-CUS",
        ],
        model: "ZWSM16-1-Zigbee",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-AVB",
            "TS0002-Avatto-custom",
            "TS0002-AV-CUS",
        ],
        model: "ZWSM16-2-Zigbee",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-AVB",
            "TS0003-Avatto-custom",
            "TS0003-AV-CUS",
        ],
        model: "ZWSM16-3-Zigbee",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0601-ZDMS16-2",
        ],
        model: "ZDMS16-2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"l1": 1, "l2": 2, } }),
            romasku.ownMcuOption(),
            romasku.mcuVersionMeta(),
            romasku.dimmerTransition(["l1", "l2"]),
            light({ endpointNames: ["l1"], effect: false, powerOnBehavior: false }),
            romasku.dimmerMinLevel("min_level", "l1"),
            romasku.dimmerMaxLevel("max_level", "l1"),
            romasku.dimmerOnLevel("on_level", "l1"),
            romasku.ownMcuEnum({
                name: "dimming_curve",
                endpointName: "l1",
                attrId: 0xff30,
                lookup: { "incandescent": 3, "led": 1, "led_deep": 4, "led_deeper": 5, "led_soft": 2, "linear": 0 },
                description: "Dimming curve (Open Dimmer firmware). led_soft (default): gentle, suits most LED lamps. led: even perceived steps (CIE lightness). led_deep / led_deeper: more room at the dim end, for LED lamps that get bright soon above their minimum. incandescent: filament and halogen lamps. linear: as the stock firmware, conduction time follows the level.",
                minMcu: 2,
            }),
            romasku.dimmerPowerOnChannel("l1"),
            romasku.dimmerSwitchType("switch_type", "l1"),
            romasku.ownMcuNumeric({
                name: "fade_on_time",
                endpointName: "l1",
                attrId: 0xff32,
                description: "Fade from dark to full when switched on (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "fade_off_time",
                endpointName: "l1",
                attrId: 0xff33,
                description: "Fade from full to dark when switched off (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "fade_off_slow_zone",
                endpointName: "l1",
                attrId: 0xff34,
                description: "Switching off: the fade slows down to an even end in this lowest part of the range above the minimum (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 100,
                unit: "%",
                minMcu: 2,
            }),
            romasku.ownMcuNumeric({
                name: "hold_time",
                endpointName: "l1",
                attrId: 0xff35,
                description: "Wall switch hold over the whole range (Open Dimmer firmware)",
                valueMin: 100,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "hold_slow_zone",
                endpointName: "l1",
                attrId: 0xff36,
                description: "Wall switch hold: dimming slows down in this lowest part of the range above the minimum, to pick a very dim level (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 100,
                unit: "%",
                minMcu: 2,
            }),
            romasku.dimmerCountdown("l1"),
            romasku.dimmerSwitchMode("l1"),
            romasku.dimmerFastPress("l1"),
            romasku.dimmerStartupLevel("l1"),
            romasku.dimmerStartBoost("l1"),
            romasku.dimmerExecuteIfOff("l1"),
            romasku.dimmerOnLevelLocal("l1"),
            romasku.dimmerBrightnessFadeTime("l1"),
            romasku.dimmerBindSetting("bind_mode", "l1", 0xff12, ["auto", "off", "mirror", "remote"], "Bound lights",
                "What goes to the lights and groups bound to this channel's wall switch (in Bind: source endpoint 3): auto = they follow the wall switch in the normal switch_mode (on, off, a hold) and its gestures drive them in detached; off = nothing; mirror = they only follow the wall switch; remote = only its gestures drive them (in the normal switch_mode the channel acts too). Open Dimmer firmware"),
            romasku.dimmerBindSetting("double_press_action", "l1", 0xff13, ["none", "full", "off", "scene_1", "scene_2"], "Double press (bound lights)",
                "What a double press sends to the bound lights when the gestures drive them (bind_mode remote, or auto in the detached switch_mode): full brightness (default), off, or Recall Scene 1 / 2 of the group the Scenes binding goes to (scene ids of scene_store). Open Dimmer firmware"),
            romasku.dimmerBindSetting("triple_press_action", "l1", 0xff14, ["none", "full", "off", "scene_1", "scene_2"], "Triple press (bound lights)",
                "What a triple press sends to the bound lights when the gestures drive them (as double_press_action; default none). Open Dimmer firmware"),
            light({ endpointNames: ["l2"], effect: false, powerOnBehavior: false }),
            romasku.dimmerMinLevel("min_level", "l2"),
            romasku.dimmerMaxLevel("max_level", "l2"),
            romasku.dimmerOnLevel("on_level", "l2"),
            romasku.ownMcuEnum({
                name: "dimming_curve",
                endpointName: "l2",
                attrId: 0xff31,
                lookup: { "incandescent": 3, "led": 1, "led_deep": 4, "led_deeper": 5, "led_soft": 2, "linear": 0 },
                description: "Dimming curve (Open Dimmer firmware). led_soft (default): gentle, suits most LED lamps. led: even perceived steps (CIE lightness). led_deep / led_deeper: more room at the dim end, for LED lamps that get bright soon above their minimum. incandescent: filament and halogen lamps. linear: as the stock firmware, conduction time follows the level.",
                minMcu: 2,
            }),
            romasku.dimmerPowerOnChannel("l2"),
            romasku.dimmerSwitchType("switch_type", "l2"),
            romasku.ownMcuNumeric({
                name: "fade_on_time",
                endpointName: "l2",
                attrId: 0xff37,
                description: "Fade from dark to full when switched on (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "fade_off_time",
                endpointName: "l2",
                attrId: 0xff38,
                description: "Fade from full to dark when switched off (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "fade_off_slow_zone",
                endpointName: "l2",
                attrId: 0xff39,
                description: "Switching off: the fade slows down to an even end in this lowest part of the range above the minimum (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 100,
                unit: "%",
                minMcu: 2,
            }),
            romasku.ownMcuNumeric({
                name: "hold_time",
                endpointName: "l2",
                attrId: 0xff3a,
                description: "Wall switch hold over the whole range (Open Dimmer firmware)",
                valueMin: 100,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "hold_slow_zone",
                endpointName: "l2",
                attrId: 0xff3b,
                description: "Wall switch hold: dimming slows down in this lowest part of the range above the minimum, to pick a very dim level (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 100,
                unit: "%",
                minMcu: 2,
            }),
            romasku.dimmerCountdown("l2"),
            romasku.dimmerSwitchMode("l2"),
            romasku.dimmerFastPress("l2"),
            romasku.dimmerStartupLevel("l2"),
            romasku.dimmerStartBoost("l2"),
            romasku.dimmerExecuteIfOff("l2"),
            romasku.dimmerOnLevelLocal("l2"),
            romasku.dimmerBrightnessFadeTime("l2"),
            romasku.dimmerBindSetting("bind_mode", "l2", 0xff12, ["auto", "off", "mirror", "remote"], "Bound lights",
                "What goes to the lights and groups bound to this channel's wall switch (in Bind: source endpoint 4): auto = they follow the wall switch in the normal switch_mode (on, off, a hold) and its gestures drive them in detached; off = nothing; mirror = they only follow the wall switch; remote = only its gestures drive them (in the normal switch_mode the channel acts too). Open Dimmer firmware"),
            romasku.dimmerBindSetting("double_press_action", "l2", 0xff13, ["none", "full", "off", "scene_1", "scene_2"], "Double press (bound lights)",
                "What a double press sends to the bound lights when the gestures drive them (bind_mode remote, or auto in the detached switch_mode): full brightness (default), off, or Recall Scene 1 / 2 of the group the Scenes binding goes to (scene ids of scene_store). Open Dimmer firmware"),
            romasku.dimmerBindSetting("triple_press_action", "l2", 0xff14, ["none", "full", "off", "scene_1", "scene_2"], "Triple press (bound lights)",
                "What a triple press sends to the bound lights when the gestures drive them (as double_press_action; default none). Open Dimmer firmware"),
            romasku.dimmerGestures(["l1", "l2"]),
            romasku.dimmerPowerOnPerChannel(["l1", "l2"]),
            romasku.dimmerIdentify(["l1", "l2"]),
            // Device: technical settings last, on the device itself (no endpoint: they are the
            // module's, not a channel's; the attributes are endpoint 1's Basic cluster).
            romasku.mcuVersion("mcu_version"),
            romasku.mcuFirmware(),
            romasku.mcuHealth(),
            romasku.mcuFlasher(),
            romasku.multiPressResetCount("multi_press_reset_count"),
            romasku.deviceConfig("device_config"),
            romasku.deviceConfigExt("device_config_ext"),
        ],
        meta: { multiEndpoint: true },
        onEvent: romasku.dimmerOnLevelRefresh([1, 2]),
        configure: async (device, coordinatorEndpoint, logger) => {
            // Right after the conversion from the stock firmware Zigbee2MQTT may still hold the
            // stock firmware's endpoints (1 and 242): its interview after the OTA can run before
            // the converted module is back, and answers from its database. The binds below
            // would then fail on the missing endpoint 2: interview it again first.
            if (!device.getEndpoint(2)) {
                await device.interview(true);
            }
            // Right after a module restart the module answers with defaults (0) until the MCU
            // has sent its values; reading then would leave 0 on the page. Wait for a real MCU
            // version (a few seconds, longer while the module updates the MCU), then read.
            // A module that does not answer at all is offline: stop after two failed reads
            // in a row, 2 s apart (a single failure, e.g. while routes re-form after a
            // restart, only costs one more try).
            const mcuEndpoint = device.getEndpoint(1);
            let mcuVersion;
            for (let attempt = 0, failures = 0; attempt < 15; attempt++) {
                try {
                    const result = await mcuEndpoint.read("genBasic", [0xff12]); // mcu_version
                    mcuVersion = result?.[0xff12];
                    failures = 0;
                } catch (error) {
                    mcuVersion = undefined; // not answered
                    if (++failures >= 2) break;
                }
                if (realMcuVersion(mcuVersion)) {
                    await new Promise((resolve) => setTimeout(resolve, 1500)); // the rest of its DP dump
                    break;
                }
                await new Promise((resolve) => setTimeout(resolve, 2000));
            }
            // Settings only Open Dimmer has ("x.y.z own"): a stock MCU's module answers 0 for them.
            if (typeof mcuVersion === "string" && mcuVersion.endsWith(" own")) {
                try {
                    await mcuEndpoint.read("genBasic", [0xff30]); // dimming_curve
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff31]); // dimming_curve
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff32]); // fade_on_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff33]); // fade_off_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff34]); // fade_off_slow_zone
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff35]); // hold_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff36]); // hold_slow_zone
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff37]); // fade_on_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff38]); // fade_off_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff39]); // fade_off_slow_zone
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff3a]); // hold_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff3b]); // hold_slow_zone
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
            }
            // Open Dimmer (any version): the module sends the MCU version (Basic 0xff12, e.g.
            // after an update of the MCU alone) and, from 2.2.0, the health report to the
            // Basic cluster's bindings (no reporting entry): bound to the coordinator. A stock
            // MCU: nothing of it. From 2.2.0 its settings per channel are read once too.
            const openDimmer = ownVersionNumbers(mcuVersion) !== undefined;
            const openDimmer22 = ownVersionAtLeast(ownVersionNumbers(mcuVersion), OPEN_DIMMER_2_2);
            const openDimmer2114 = ownVersionAtLeast(ownVersionNumbers(mcuVersion), "2.1.14");
            if (openDimmer) {
                try {
                    await reporting.bind(mcuEndpoint, coordinatorEndpoint, ["genBasic"]);
                    if (openDimmer22) await mcuEndpoint.read("genBasic", [0xff48]); // health report
                } catch (error) {
                    // not answered now; bound at the next configure, the report comes within 10 min
                }
            } else if (mcuVersion !== undefined) {
                // A module with the SWD master (the SW token, mcu_flasher.c) reports its status
                // to the Basic cluster's bindings too: bound with the stock MCU as well, so that
                // an install at a later power-up and the Open Dimmer version it brings reach
                // Zigbee2MQTT without another configure. The status is read once: mcu_firmware
                // tells an install that waits for a power cycle. (Not for a module that did not
                // answer at all: offline.)
                try {
                    const result = await mcuEndpoint.read("genBasic", [0xff50]); // mcu_flasher_status
                    if (result?.[0xff50] !== undefined) {
                        await reporting.bind(mcuEndpoint, coordinatorEndpoint, ["genBasic"]);
                    }
                } catch (error) {
                    // no SWD master (no SW token, an older build) or not answered now
                }
            }



            const dimmer1Dp = 1;
            const dimmer1 = device.getEndpoint(dimmer1Dp);
            await reporting.bind(dimmer1, coordinatorEndpoint, ["genOnOff", "genLevelCtrl"]);
            await reporting.onOff(dimmer1, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            await dimmer1.configureReporting("genLevelCtrl", [
                {
                    attribute: "currentLevel",
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // The settings shown on the page, read once so none stays unknown.
            for (const [cluster, attrs] of [["genOnOff", ["startUpOnOff"]], ["genLevelCtrl", ["onLevel"]],
                                            ["lightingBallastCfg", ["minLevel", "maxLevel"]],
                                            ["genOnOffSwitchCfg", ["switchType"]]]) {
                try {
                    await dimmer1.read(cluster, attrs);
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
            }
            try {
                // Wall switch gestures (Open Dimmer firmware); older module builds lack the cluster.
                // Each gesture is an event: report changes only (maximum interval 0), else
                // the stack repeats the last gesture every maximum interval (and Z2M with it).
                await reporting.bind(dimmer1, coordinatorEndpoint, ["genMultistateInput"]);
                await dimmer1.configureReporting("genMultistateInput", [
                    {
                        attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                        minimumReportInterval: 0,
                        maximumReportInterval: 0,
                        reportableChange: 1,
                    },
                ]);
            } catch (error) {
                // not there: no gestures
            }
            if (openDimmer2114) {
                try {
                    await dimmer1.read("genLevelCtrl", [0xff12]); // brightness_fade_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
            }
            if (openDimmer22) {
                for (const [cluster, attrs] of [["genOnOff", [0xff10]], ["genOnOffSwitchCfg", [0xff10, 0xff11]],
                                                ["genLevelCtrl", ["startUpCurrentLevel", "options", 0xff11]],
                                                ["lightingBallastCfg", [0xff10]],
                                                ["genOnOffSwitchCfg", [0xff12, 0xff13, 0xff14]]]) {
                    try {
                        await dimmer1.read(cluster, attrs);
                    } catch (error) {
                        // not answered now; read on the next configure or with a refresh
                    }
                }
            }
            const dimmer2Dp = 2;
            const dimmer2 = device.getEndpoint(dimmer2Dp);
            await reporting.bind(dimmer2, coordinatorEndpoint, ["genOnOff", "genLevelCtrl"]);
            await reporting.onOff(dimmer2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            await dimmer2.configureReporting("genLevelCtrl", [
                {
                    attribute: "currentLevel",
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // The settings shown on the page, read once so none stays unknown.
            for (const [cluster, attrs] of [["genOnOff", ["startUpOnOff"]], ["genLevelCtrl", ["onLevel"]],
                                            ["lightingBallastCfg", ["minLevel", "maxLevel"]],
                                            ["genOnOffSwitchCfg", ["switchType"]]]) {
                try {
                    await dimmer2.read(cluster, attrs);
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
            }
            try {
                // Wall switch gestures (Open Dimmer firmware); older module builds lack the cluster.
                // Each gesture is an event: report changes only (maximum interval 0), else
                // the stack repeats the last gesture every maximum interval (and Z2M with it).
                await reporting.bind(dimmer2, coordinatorEndpoint, ["genMultistateInput"]);
                await dimmer2.configureReporting("genMultistateInput", [
                    {
                        attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                        minimumReportInterval: 0,
                        maximumReportInterval: 0,
                        reportableChange: 1,
                    },
                ]);
            } catch (error) {
                // not there: no gestures
            }
            if (openDimmer2114) {
                try {
                    await dimmer2.read("genLevelCtrl", [0xff12]); // brightness_fade_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
            }
            if (openDimmer22) {
                for (const [cluster, attrs] of [["genOnOff", [0xff10]], ["genOnOffSwitchCfg", [0xff10, 0xff11]],
                                                ["genLevelCtrl", ["startUpCurrentLevel", "options", 0xff11]],
                                                ["lightingBallastCfg", [0xff10]],
                                                ["genOnOffSwitchCfg", [0xff12, 0xff13, 0xff14]]]) {
                    try {
                        await dimmer2.read(cluster, attrs);
                    } catch (error) {
                        // not answered now; read on the next configure or with a refresh
                    }
                }
            }
        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0601-ZDMS16-2",
        ],
        model: "ZDMS16-2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"l1": 1, "l2": 2, } }),
            romasku.ownMcuOption(),
            romasku.mcuVersionMeta(),
            romasku.dimmerTransition(["l1", "l2"]),
            light({ endpointNames: ["l1"], effect: false, powerOnBehavior: false }),
            romasku.dimmerMinLevel("min_level", "l1"),
            romasku.dimmerMaxLevel("max_level", "l1"),
            romasku.dimmerOnLevel("on_level", "l1"),
            romasku.ownMcuEnum({
                name: "dimming_curve",
                endpointName: "l1",
                attrId: 0xff30,
                lookup: { "incandescent": 3, "led": 1, "led_deep": 4, "led_deeper": 5, "led_soft": 2, "linear": 0 },
                description: "Dimming curve (Open Dimmer firmware). led_soft (default): gentle, suits most LED lamps. led: even perceived steps (CIE lightness). led_deep / led_deeper: more room at the dim end, for LED lamps that get bright soon above their minimum. incandescent: filament and halogen lamps. linear: as the stock firmware, conduction time follows the level.",
                minMcu: 2,
            }),
            romasku.dimmerPowerOnChannel("l1"),
            romasku.dimmerSwitchType("switch_type", "l1"),
            romasku.ownMcuNumeric({
                name: "fade_on_time",
                endpointName: "l1",
                attrId: 0xff32,
                description: "Fade from dark to full when switched on (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "fade_off_time",
                endpointName: "l1",
                attrId: 0xff33,
                description: "Fade from full to dark when switched off (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "fade_off_slow_zone",
                endpointName: "l1",
                attrId: 0xff34,
                description: "Switching off: the fade slows down to an even end in this lowest part of the range above the minimum (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 100,
                unit: "%",
                minMcu: 2,
            }),
            romasku.ownMcuNumeric({
                name: "hold_time",
                endpointName: "l1",
                attrId: 0xff35,
                description: "Wall switch hold over the whole range (Open Dimmer firmware)",
                valueMin: 100,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "hold_slow_zone",
                endpointName: "l1",
                attrId: 0xff36,
                description: "Wall switch hold: dimming slows down in this lowest part of the range above the minimum, to pick a very dim level (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 100,
                unit: "%",
                minMcu: 2,
            }),
            romasku.dimmerCountdown("l1"),
            romasku.dimmerSwitchMode("l1"),
            romasku.dimmerFastPress("l1"),
            romasku.dimmerStartupLevel("l1"),
            romasku.dimmerStartBoost("l1"),
            romasku.dimmerExecuteIfOff("l1"),
            romasku.dimmerOnLevelLocal("l1"),
            romasku.dimmerBrightnessFadeTime("l1"),
            romasku.dimmerBindSetting("bind_mode", "l1", 0xff12, ["auto", "off", "mirror", "remote"], "Bound lights",
                "What goes to the lights and groups bound to this channel's wall switch (in Bind: source endpoint 3): auto = they follow the wall switch in the normal switch_mode (on, off, a hold) and its gestures drive them in detached; off = nothing; mirror = they only follow the wall switch; remote = only its gestures drive them (in the normal switch_mode the channel acts too). Open Dimmer firmware"),
            romasku.dimmerBindSetting("double_press_action", "l1", 0xff13, ["none", "full", "off", "scene_1", "scene_2"], "Double press (bound lights)",
                "What a double press sends to the bound lights when the gestures drive them (bind_mode remote, or auto in the detached switch_mode): full brightness (default), off, or Recall Scene 1 / 2 of the group the Scenes binding goes to (scene ids of scene_store). Open Dimmer firmware"),
            romasku.dimmerBindSetting("triple_press_action", "l1", 0xff14, ["none", "full", "off", "scene_1", "scene_2"], "Triple press (bound lights)",
                "What a triple press sends to the bound lights when the gestures drive them (as double_press_action; default none). Open Dimmer firmware"),
            light({ endpointNames: ["l2"], effect: false, powerOnBehavior: false }),
            romasku.dimmerMinLevel("min_level", "l2"),
            romasku.dimmerMaxLevel("max_level", "l2"),
            romasku.dimmerOnLevel("on_level", "l2"),
            romasku.ownMcuEnum({
                name: "dimming_curve",
                endpointName: "l2",
                attrId: 0xff31,
                lookup: { "incandescent": 3, "led": 1, "led_deep": 4, "led_deeper": 5, "led_soft": 2, "linear": 0 },
                description: "Dimming curve (Open Dimmer firmware). led_soft (default): gentle, suits most LED lamps. led: even perceived steps (CIE lightness). led_deep / led_deeper: more room at the dim end, for LED lamps that get bright soon above their minimum. incandescent: filament and halogen lamps. linear: as the stock firmware, conduction time follows the level.",
                minMcu: 2,
            }),
            romasku.dimmerPowerOnChannel("l2"),
            romasku.dimmerSwitchType("switch_type", "l2"),
            romasku.ownMcuNumeric({
                name: "fade_on_time",
                endpointName: "l2",
                attrId: 0xff37,
                description: "Fade from dark to full when switched on (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "fade_off_time",
                endpointName: "l2",
                attrId: 0xff38,
                description: "Fade from full to dark when switched off (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "fade_off_slow_zone",
                endpointName: "l2",
                attrId: 0xff39,
                description: "Switching off: the fade slows down to an even end in this lowest part of the range above the minimum (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 100,
                unit: "%",
                minMcu: 2,
            }),
            romasku.ownMcuNumeric({
                name: "hold_time",
                endpointName: "l2",
                attrId: 0xff3a,
                description: "Wall switch hold over the whole range (Open Dimmer firmware)",
                valueMin: 100,
                valueMax: 10000,
                unit: "s",
                minMcu: 2,
                divisor: 1000,
                valueStep: 0.05,
            }),
            romasku.ownMcuNumeric({
                name: "hold_slow_zone",
                endpointName: "l2",
                attrId: 0xff3b,
                description: "Wall switch hold: dimming slows down in this lowest part of the range above the minimum, to pick a very dim level (Open Dimmer firmware)",
                valueMin: 0,
                valueMax: 100,
                unit: "%",
                minMcu: 2,
            }),
            romasku.dimmerCountdown("l2"),
            romasku.dimmerSwitchMode("l2"),
            romasku.dimmerFastPress("l2"),
            romasku.dimmerStartupLevel("l2"),
            romasku.dimmerStartBoost("l2"),
            romasku.dimmerExecuteIfOff("l2"),
            romasku.dimmerOnLevelLocal("l2"),
            romasku.dimmerBrightnessFadeTime("l2"),
            romasku.dimmerBindSetting("bind_mode", "l2", 0xff12, ["auto", "off", "mirror", "remote"], "Bound lights",
                "What goes to the lights and groups bound to this channel's wall switch (in Bind: source endpoint 4): auto = they follow the wall switch in the normal switch_mode (on, off, a hold) and its gestures drive them in detached; off = nothing; mirror = they only follow the wall switch; remote = only its gestures drive them (in the normal switch_mode the channel acts too). Open Dimmer firmware"),
            romasku.dimmerBindSetting("double_press_action", "l2", 0xff13, ["none", "full", "off", "scene_1", "scene_2"], "Double press (bound lights)",
                "What a double press sends to the bound lights when the gestures drive them (bind_mode remote, or auto in the detached switch_mode): full brightness (default), off, or Recall Scene 1 / 2 of the group the Scenes binding goes to (scene ids of scene_store). Open Dimmer firmware"),
            romasku.dimmerBindSetting("triple_press_action", "l2", 0xff14, ["none", "full", "off", "scene_1", "scene_2"], "Triple press (bound lights)",
                "What a triple press sends to the bound lights when the gestures drive them (as double_press_action; default none). Open Dimmer firmware"),
            romasku.dimmerGestures(["l1", "l2"]),
            romasku.dimmerPowerOnPerChannel(["l1", "l2"]),
            romasku.dimmerIdentify(["l1", "l2"]),
            // Device: technical settings last, on the device itself (no endpoint: they are the
            // module's, not a channel's; the attributes are endpoint 1's Basic cluster).
            romasku.mcuVersion("mcu_version"),
            romasku.mcuFirmware(),
            romasku.mcuHealth(),
            romasku.mcuFlasher(),
            romasku.multiPressResetCount("multi_press_reset_count"),
            romasku.deviceConfig("device_config"),
            romasku.deviceConfigExt("device_config_ext"),
        ],
        meta: { multiEndpoint: true },
        onEvent: romasku.dimmerOnLevelRefresh([1, 2]),
        configure: async (device, coordinatorEndpoint, logger) => {
            // Right after the conversion from the stock firmware Zigbee2MQTT may still hold the
            // stock firmware's endpoints (1 and 242): its interview after the OTA can run before
            // the converted module is back, and answers from its database. The binds below
            // would then fail on the missing endpoint 2: interview it again first.
            if (!device.getEndpoint(2)) {
                await device.interview(true);
            }
            // Right after a module restart the module answers with defaults (0) until the MCU
            // has sent its values; reading then would leave 0 on the page. Wait for a real MCU
            // version (a few seconds, longer while the module updates the MCU), then read.
            // A module that does not answer at all is offline: stop after two failed reads
            // in a row, 2 s apart (a single failure, e.g. while routes re-form after a
            // restart, only costs one more try).
            const mcuEndpoint = device.getEndpoint(1);
            let mcuVersion;
            for (let attempt = 0, failures = 0; attempt < 15; attempt++) {
                try {
                    const result = await mcuEndpoint.read("genBasic", [0xff12]); // mcu_version
                    mcuVersion = result?.[0xff12];
                    failures = 0;
                } catch (error) {
                    mcuVersion = undefined; // not answered
                    if (++failures >= 2) break;
                }
                if (realMcuVersion(mcuVersion)) {
                    await new Promise((resolve) => setTimeout(resolve, 1500)); // the rest of its DP dump
                    break;
                }
                await new Promise((resolve) => setTimeout(resolve, 2000));
            }
            // Settings only Open Dimmer has ("x.y.z own"): a stock MCU's module answers 0 for them.
            if (typeof mcuVersion === "string" && mcuVersion.endsWith(" own")) {
                try {
                    await mcuEndpoint.read("genBasic", [0xff30]); // dimming_curve
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff31]); // dimming_curve
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff32]); // fade_on_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff33]); // fade_off_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff34]); // fade_off_slow_zone
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff35]); // hold_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff36]); // hold_slow_zone
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff37]); // fade_on_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff38]); // fade_off_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff39]); // fade_off_slow_zone
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff3a]); // hold_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
                try {
                    await mcuEndpoint.read("genBasic", [0xff3b]); // hold_slow_zone
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
            }
            // Open Dimmer (any version): the module sends the MCU version (Basic 0xff12, e.g.
            // after an update of the MCU alone) and, from 2.2.0, the health report to the
            // Basic cluster's bindings (no reporting entry): bound to the coordinator. A stock
            // MCU: nothing of it. From 2.2.0 its settings per channel are read once too.
            const openDimmer = ownVersionNumbers(mcuVersion) !== undefined;
            const openDimmer22 = ownVersionAtLeast(ownVersionNumbers(mcuVersion), OPEN_DIMMER_2_2);
            const openDimmer2114 = ownVersionAtLeast(ownVersionNumbers(mcuVersion), "2.1.14");
            if (openDimmer) {
                try {
                    await reporting.bind(mcuEndpoint, coordinatorEndpoint, ["genBasic"]);
                    if (openDimmer22) await mcuEndpoint.read("genBasic", [0xff48]); // health report
                } catch (error) {
                    // not answered now; bound at the next configure, the report comes within 10 min
                }
            } else if (mcuVersion !== undefined) {
                // A module with the SWD master (the SW token, mcu_flasher.c) reports its status
                // to the Basic cluster's bindings too: bound with the stock MCU as well, so that
                // an install at a later power-up and the Open Dimmer version it brings reach
                // Zigbee2MQTT without another configure. The status is read once: mcu_firmware
                // tells an install that waits for a power cycle. (Not for a module that did not
                // answer at all: offline.)
                try {
                    const result = await mcuEndpoint.read("genBasic", [0xff50]); // mcu_flasher_status
                    if (result?.[0xff50] !== undefined) {
                        await reporting.bind(mcuEndpoint, coordinatorEndpoint, ["genBasic"]);
                    }
                } catch (error) {
                    // no SWD master (no SW token, an older build) or not answered now
                }
            }



            const dimmer1Dp = 1;
            const dimmer1 = device.getEndpoint(dimmer1Dp);
            await reporting.bind(dimmer1, coordinatorEndpoint, ["genOnOff", "genLevelCtrl"]);
            await reporting.onOff(dimmer1, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            await dimmer1.configureReporting("genLevelCtrl", [
                {
                    attribute: "currentLevel",
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // The settings shown on the page, read once so none stays unknown.
            for (const [cluster, attrs] of [["genOnOff", ["startUpOnOff"]], ["genLevelCtrl", ["onLevel"]],
                                            ["lightingBallastCfg", ["minLevel", "maxLevel"]],
                                            ["genOnOffSwitchCfg", ["switchType"]]]) {
                try {
                    await dimmer1.read(cluster, attrs);
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
            }
            try {
                // Wall switch gestures (Open Dimmer firmware); older module builds lack the cluster.
                // Each gesture is an event: report changes only (maximum interval 0), else
                // the stack repeats the last gesture every maximum interval (and Z2M with it).
                await reporting.bind(dimmer1, coordinatorEndpoint, ["genMultistateInput"]);
                await dimmer1.configureReporting("genMultistateInput", [
                    {
                        attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                        minimumReportInterval: 0,
                        maximumReportInterval: 0,
                        reportableChange: 1,
                    },
                ]);
            } catch (error) {
                // not there: no gestures
            }
            if (openDimmer2114) {
                try {
                    await dimmer1.read("genLevelCtrl", [0xff12]); // brightness_fade_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
            }
            if (openDimmer22) {
                for (const [cluster, attrs] of [["genOnOff", [0xff10]], ["genOnOffSwitchCfg", [0xff10, 0xff11]],
                                                ["genLevelCtrl", ["startUpCurrentLevel", "options", 0xff11]],
                                                ["lightingBallastCfg", [0xff10]],
                                                ["genOnOffSwitchCfg", [0xff12, 0xff13, 0xff14]]]) {
                    try {
                        await dimmer1.read(cluster, attrs);
                    } catch (error) {
                        // not answered now; read on the next configure or with a refresh
                    }
                }
            }
            const dimmer2Dp = 2;
            const dimmer2 = device.getEndpoint(dimmer2Dp);
            await reporting.bind(dimmer2, coordinatorEndpoint, ["genOnOff", "genLevelCtrl"]);
            await reporting.onOff(dimmer2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            await dimmer2.configureReporting("genLevelCtrl", [
                {
                    attribute: "currentLevel",
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // The settings shown on the page, read once so none stays unknown.
            for (const [cluster, attrs] of [["genOnOff", ["startUpOnOff"]], ["genLevelCtrl", ["onLevel"]],
                                            ["lightingBallastCfg", ["minLevel", "maxLevel"]],
                                            ["genOnOffSwitchCfg", ["switchType"]]]) {
                try {
                    await dimmer2.read(cluster, attrs);
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
            }
            try {
                // Wall switch gestures (Open Dimmer firmware); older module builds lack the cluster.
                // Each gesture is an event: report changes only (maximum interval 0), else
                // the stack repeats the last gesture every maximum interval (and Z2M with it).
                await reporting.bind(dimmer2, coordinatorEndpoint, ["genMultistateInput"]);
                await dimmer2.configureReporting("genMultistateInput", [
                    {
                        attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                        minimumReportInterval: 0,
                        maximumReportInterval: 0,
                        reportableChange: 1,
                    },
                ]);
            } catch (error) {
                // not there: no gestures
            }
            if (openDimmer2114) {
                try {
                    await dimmer2.read("genLevelCtrl", [0xff12]); // brightness_fade_time
                } catch (error) {
                    // not answered now; read on the next configure or with a refresh
                }
            }
            if (openDimmer22) {
                for (const [cluster, attrs] of [["genOnOff", [0xff10]], ["genOnOffSwitchCfg", [0xff10, 0xff11]],
                                                ["genLevelCtrl", ["startUpCurrentLevel", "options", 0xff11]],
                                                ["lightingBallastCfg", [0xff10]],
                                                ["genOnOffSwitchCfg", [0xff12, 0xff13, 0xff14]]]) {
                    try {
                        await dimmer2.read(cluster, attrs);
                    } catch (error) {
                        // not answered now; read on the next configure or with a refresh
                    }
                }
            }
        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-AVB",
            "TS0004-Avatto-custom",
            "TS0004-AV-CUS",
        ],
        model: "ZWSM16-4-Zigbee",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-AVB2",
        ],
        model: "ZWSM16-3-Zigbee",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-AVB2",
        ],
        model: "ZWSM16-4-Zigbee",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-AV-DRY",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-avatto",
            "TS0011-avatto-ED",
        ],
        model: "LZWSM16-1",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0012-avatto",
            "TS0012-avatto-ED",
        ],
        model: "LZWSM16-2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0012-AVB1",
        ],
        model: "LZWSM16-2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0013-AVB",
        ],
        model: "LZWSM16-3",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "EKAC-T3092Z-CUSTOM",
        ],
        model: "EKAC-T3092Z",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0012-EKF",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-GS",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-GS",
        ],
        model: "TS0002",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-NS",
        ],
        model: "L13Z",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-FL",
            "TS0002-FL",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-GRA",
            "TS0003-GR",
        ],
        model: "TS0003_switch_module_2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-GRA",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0012-C",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-GD",
        ],
        model: "TS0001_switch_module",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-GIR",
        ],
        model: "JR-ZDS01",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-GIR",
            "TS0002-custom",
        ],
        model: "TS0002_basic",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS130F-GIR",
        ],
        model: "TS130F_GIRIER",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"cover_switch": 1, "cover": 2, } }),
            romasku.deviceConfig("device_config", "cover_switch"),
            romasku.deviceConfigExt("device_config_ext", "cover_switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "cover_switch"),
            romasku.networkIndicator("network_led", "cover_switch"),
            windowCovering({ 
                controls: ["lift"],
                coverInverted: true,
                configureReporting: true,
                endpointNames: ["cover"]
            }),
            romasku.coverMoving("moving", "cover"),
            romasku.coverMotorReversal("cover_motor_reversal", "cover"),
            romasku.coverOpenTime("cover_open_time", "cover"),
            romasku.coverCloseTime("cover_close_time", "cover"),
            romasku.coverOpenDeadzone("cover_open_deadzone", "cover"),
            romasku.coverClosedDeadzone("cover_closed_deadzone", "cover"),
            romasku.coverSwitchPressAction("cover_switch_press_action", "cover_switch"),
            romasku.coverSwitchType("cover_switch_type", "cover_switch"),
            romasku.coverSwitchInvert("cover_switch_invert", "cover_switch"),
            romasku.coverSwitchCoverIndex("cover_switch_cover_index", "cover_switch", 1),
            romasku.coverSwitchLocalMode("cover_switch_local_mode", "cover_switch"),
            romasku.coverSwitchBindedMode("cover_switch_binded_mode", "cover_switch"),
            romasku.coverSwitchLongPressDuration("cover_switch_long_press_duration", "cover_switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {


            const coverSwitch1 = device.getEndpoint(1);
            await reporting.bind(coverSwitch1, coordinatorEndpoint, ["genMultistateInput"]);
            await coverSwitch1.configureReporting("genMultistateInput", [
                {
                    attribute: "presentValue",
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);

            const cover1 = device.getEndpoint(2);
            await reporting.bind(cover1, coordinatorEndpoint, ["closuresWindowCovering"]);
            await cover1.configureReporting("closuresWindowCovering", [
                {
                    attribute: {ID: 0xff00, type: Zcl.DataType.ENUM8},
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS130F-GIR-DUAL",
        ],
        model: "TS130F_GIRIER_DUAL",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"cover_switch_left": 1, "cover_switch_right": 2, "cover_left": 3, "cover_right": 4, } }),
            romasku.deviceConfig("device_config", "cover_switch_left"),
            romasku.deviceConfigExt("device_config_ext", "cover_switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "cover_switch_left"),
            romasku.networkIndicator("network_led", "cover_switch_left"),
            windowCovering({ 
                controls: ["lift"],
                coverInverted: true,
                configureReporting: true,
                endpointNames: ["cover_left"]
            }),
            romasku.coverMoving("moving", "cover_left"),
            romasku.coverMotorReversal("cover_left_motor_reversal", "cover_left"),
            romasku.coverOpenTime("cover_left_open_time", "cover_left"),
            romasku.coverCloseTime("cover_left_close_time", "cover_left"),
            romasku.coverOpenDeadzone("cover_left_open_deadzone", "cover_left"),
            romasku.coverClosedDeadzone("cover_left_closed_deadzone", "cover_left"),
            windowCovering({ 
                controls: ["lift"],
                coverInverted: true,
                configureReporting: true,
                endpointNames: ["cover_right"]
            }),
            romasku.coverMoving("moving", "cover_right"),
            romasku.coverMotorReversal("cover_right_motor_reversal", "cover_right"),
            romasku.coverOpenTime("cover_right_open_time", "cover_right"),
            romasku.coverCloseTime("cover_right_close_time", "cover_right"),
            romasku.coverOpenDeadzone("cover_right_open_deadzone", "cover_right"),
            romasku.coverClosedDeadzone("cover_right_closed_deadzone", "cover_right"),
            romasku.coverSwitchPressAction("cover_switch_left_press_action", "cover_switch_left"),
            romasku.coverSwitchType("cover_switch_left_type", "cover_switch_left"),
            romasku.coverSwitchInvert("cover_switch_left_invert", "cover_switch_left"),
            romasku.coverSwitchCoverIndex("cover_switch_left_cover_index", "cover_switch_left", 2),
            romasku.coverSwitchLocalMode("cover_switch_left_local_mode", "cover_switch_left"),
            romasku.coverSwitchBindedMode("cover_switch_left_binded_mode", "cover_switch_left"),
            romasku.coverSwitchLongPressDuration("cover_switch_left_long_press_duration", "cover_switch_left"),
            romasku.coverSwitchPressAction("cover_switch_right_press_action", "cover_switch_right"),
            romasku.coverSwitchType("cover_switch_right_type", "cover_switch_right"),
            romasku.coverSwitchInvert("cover_switch_right_invert", "cover_switch_right"),
            romasku.coverSwitchCoverIndex("cover_switch_right_cover_index", "cover_switch_right", 2),
            romasku.coverSwitchLocalMode("cover_switch_right_local_mode", "cover_switch_right"),
            romasku.coverSwitchBindedMode("cover_switch_right_binded_mode", "cover_switch_right"),
            romasku.coverSwitchLongPressDuration("cover_switch_right_long_press_duration", "cover_switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {


            const coverSwitch1 = device.getEndpoint(1);
            await reporting.bind(coverSwitch1, coordinatorEndpoint, ["genMultistateInput"]);
            await coverSwitch1.configureReporting("genMultistateInput", [
                {
                    attribute: "presentValue",
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const coverSwitch2 = device.getEndpoint(2);
            await reporting.bind(coverSwitch2, coordinatorEndpoint, ["genMultistateInput"]);
            await coverSwitch2.configureReporting("genMultistateInput", [
                {
                    attribute: "presentValue",
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);

            const cover1 = device.getEndpoint(3);
            await reporting.bind(cover1, coordinatorEndpoint, ["closuresWindowCovering"]);
            await cover1.configureReporting("closuresWindowCovering", [
                {
                    attribute: {ID: 0xff00, type: Zcl.DataType.ENUM8},
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const cover2 = device.getEndpoint(4);
            await reporting.bind(cover2, coordinatorEndpoint, ["closuresWindowCovering"]);
            await cover2.configureReporting("closuresWindowCovering", [
                {
                    attribute: {ID: 0xff00, type: Zcl.DataType.ENUM8},
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-GIR-1",
        ],
        model: "JR-ZDS01",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-HOBM",
        ],
        model: "ZG-301Z",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-HOBM",
        ],
        model: "ZG-301Z-2CH",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-HOBM",
        ],
        model: "ZG-301Z-3CH",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-HOB1",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-HOB",
        ],
        model: "ZG-301Z",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "Hommyn-RLZBN02",
        ],
        model: "TS0002_limited",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-HOMMYN",
        ],
        model: "TS0011",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "Hommyn-2",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-IHS",
        ],
        model: "_TZ3000_pgq7ormg",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-IHS",
        ],
        model: "TS0002_basic",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-IHS",
            "TS0003-3CH-cus",
        ],
        model: "_TZ3000_mhhxxjrs",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-IHS",
        ],
        model: "_TZ3000_knoj8lpk",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-IHA",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "iHSW02-MiniSmartSw",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-PWR",
        ],
        model: "TS0001_power",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-MSB",
        ],
        model: "ZM-104B-M",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-custom",
        ],
        model: "MS-104CZ",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-MS",
        ],
        model: "TS0011",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-MS",
        ],
        model: "ZM4LT2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-MS",
        ],
        model: "ZM4LT3",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-MS",
        ],
        model: "ZM4LT4",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-nous",
        ],
        model: "B1Z",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "MS105-ZB-CUSTOM",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "ZBMINIL2-custom",
        ],
        model: "ZBMINIL2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-TLED",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-N1J44RTH",
        ],
        model: "TS0002",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-UWHJGNGJ",
        ],
        model: "TS0003",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-B",
        ],
        model: "TS0004",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-C",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-SB",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-C",
            "TS0002-SB",
        ],
        model: "TS0002_basic_2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-SB",
        ],
        model: "SB04-Zigbee",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-SB",
        ],
        model: "SB03-Zigbee",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "WHD02-custom",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "WHD02-custom",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "WHD02-custom",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-CC",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-custom",
        ],
        model: "TS0011_switch_module",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-CUS-2",
        ],
        model: "TS0011_switch_module",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0012-custom",
            "TS0042-CUSTOM",
            "TS0012-custom-end-device",
        ],
        model: "TS0012_switch_module",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "ZB08-custom",
            "ZB08-custom-ED",
        ],
        model: "ZB08",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-QS-custom",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-S05",
        ],
        model: "TS0011_switch_module",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-QS",
        ],
        model: "TS0002_limited",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-Avv",
        ],
        model: "TS0003",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "NovatoZRM01",
        ],
        model: "QS-Zigbee-SEC01-U",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "NovatoZRM02",
        ],
        model: "QS-Zigbee-SEC02-U",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "NovatoZNR01",
        ],
        model: "TS0011",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0012-QS",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS130F-NOV",
        ],
        model: "QS-Zigbee-C03",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"cover_switch": 1, "cover": 2, } }),
            romasku.deviceConfig("device_config", "cover_switch"),
            romasku.deviceConfigExt("device_config_ext", "cover_switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "cover_switch"),
            romasku.networkIndicator("network_led", "cover_switch"),
            windowCovering({ 
                controls: ["lift"],
                coverInverted: true,
                configureReporting: true,
                endpointNames: ["cover"]
            }),
            romasku.coverMoving("moving", "cover"),
            romasku.coverMotorReversal("cover_motor_reversal", "cover"),
            romasku.coverOpenTime("cover_open_time", "cover"),
            romasku.coverCloseTime("cover_close_time", "cover"),
            romasku.coverOpenDeadzone("cover_open_deadzone", "cover"),
            romasku.coverClosedDeadzone("cover_closed_deadzone", "cover"),
            romasku.coverSwitchPressAction("cover_switch_press_action", "cover_switch"),
            romasku.coverSwitchType("cover_switch_type", "cover_switch"),
            romasku.coverSwitchInvert("cover_switch_invert", "cover_switch"),
            romasku.coverSwitchCoverIndex("cover_switch_cover_index", "cover_switch", 1),
            romasku.coverSwitchLocalMode("cover_switch_local_mode", "cover_switch"),
            romasku.coverSwitchBindedMode("cover_switch_binded_mode", "cover_switch"),
            romasku.coverSwitchLongPressDuration("cover_switch_long_press_duration", "cover_switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {


            const coverSwitch1 = device.getEndpoint(1);
            await reporting.bind(coverSwitch1, coordinatorEndpoint, ["genMultistateInput"]);
            await coverSwitch1.configureReporting("genMultistateInput", [
                {
                    attribute: "presentValue",
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);

            const cover1 = device.getEndpoint(2);
            await reporting.bind(cover1, coordinatorEndpoint, ["closuresWindowCovering"]);
            await cover1.configureReporting("closuresWindowCovering", [
                {
                    attribute: {ID: 0xff00, type: Zcl.DataType.ENUM8},
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-C",
        ],
        model: "TS0003",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-custom",
        ],
        model: "TS0001_switch_module",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-OXT-CUS",
        ],
        model: "TS0002_basic",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-custom",
        ],
        model: "TS0002_basic",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-TUYA",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-TS",
        ],
        model: "WHD02",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-ZTU",
        ],
        model: "TS0001_switch_module",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-Avv",
        ],
        model: "TS0004_switch_module",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-NS1",
        ],
        model: "L13Z",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-YNTDQ",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-ZB",
        ],
        model: "TS0002",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-BD-PM",
            "TS011F-BORUIDAPLS-PM",
        ],
        model: "TS011F_plug_1_2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-BS-PM",
        ],
        model: "TS011F_plug_1_2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-BS-PM-1",
        ],
        model: "TS011F_plug_1_2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-BS-PM-2",
        ],
        model: "TS011F_plug_1_2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-BS",
        ],
        model: "_TZ3000_o1jzcxou",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-MG",
        ],
        model: "MG-GPO01",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-MOES",
        ],
        model: "ZK-EU",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-TPM",
        ],
        model: "TS011F_plug_1",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-AB-PM",
        ],
        model: "TS011F_plug_1",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-LK",
        ],
        model: "TS011F_plug_1",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS011F-LIDL-PM",
        ],
        model: "HG08673",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0044-HOB",
        ],
        model: "ZG-101ZS",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-HB",
        ],
        model: "ZG-101ZL",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch": 1, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0041-IH",
        ],
        model: "IH-K663",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch": 1, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0044-CUS",
        ],
        model: "_TZ3000_mh9px7cq",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0046-IH",
        ],
        model: "TS0046",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS004F-LIDL",
        ],
        model: "HG08164",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch": 1, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0041-MA",
        ],
        model: "ZT-B-EU1",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch": 1, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0042-MA",
        ],
        model: "ZT-B-EU2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0043-MA",
        ],
        model: "ZT-B-EU3",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0044-MA",
        ],
        model: "ZT-SR-EU4",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0044-MOES",
        ],
        model: "TS0044",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0041-TB",
        ],
        model: "SH-SC07",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch": 1, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0041-TB2",
        ],
        model: "TS0041",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch": 1, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0041-MOES",
        ],
        model: "TS0041",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch": 1, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0042-MOES",
        ],
        model: "TS0042",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0043-MOES",
        ],
        model: "TS0043",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0043-MB",
        ],
        model: "TS0043",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0044-TUYA",
        ],
        model: "TS0044",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TLSR82xx-2G",
        ],
        model: "TLSR82xx_2btn_remote",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS004F-Loginovo",
        ],
        model: "ZG-101ZL",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch": 1, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS004F-TUYA",
        ],
        model: "TS004F",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            romasku.batteryPercentage(),
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            // Battery reporting
            const batteryEndpoint = device.getEndpoint(1);
            await reporting.bind(batteryEndpoint, coordinatorEndpoint, ["genPowerCfg"]);
            await batteryEndpoint.configureReporting("genPowerCfg", [
                {
                    attribute: {ID: 0x0021, type: 0x20}, // BatteryPercentageRemaining
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.HOUR,
                    reportableChange: 2, // 1% (2 in ZCL 0-200 format)
                },
            ]);
            await batteryEndpoint.read("genPowerCfg", [0x0021, 0x0020]);



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-AVT",
        ],
        model: "RoomsAI_37022454",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-AVT",
        ],
        model: "37022463-2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-AVT",
            "Avatto-3-touch",
        ],
        model: "370224742",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-AVT",
        ],
        model: "TS0004",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-BSDB",
            "TS0001-BS-T",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-BSDB",
            "TS0002-BS-1",
        ],
        model: "TS0002",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-BSDB",
            "TS0003-BSEED",
        ],
        model: "TS0003",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "BSLR1",
        ],
        model: "TS0011",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "BSLR2",
            "Bseed-2-gang-2",
            "Bseed-2-gang-2-ED",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "BSLR3",
            "TS0013-2-BS",
        ],
        model: "TS0013",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-BSMN",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-BSMN",
        ],
        model: "TS0002",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-BSMN",
            "TS0003-BS",
        ],
        model: "TS0003",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-BSMN",
            "TS0004-BS",
        ],
        model: "TS0004",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-CUS-T",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-BS12",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-CUS-T",
        ],
        model: "TS0002",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-BS22",
        ],
        model: "TS0002",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-BS-T",
        ],
        model: "TS0011",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "Bseed-2-gang",
            "Bseed-2-gang-ED",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "Bseed-2-gang-3",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0013-BS",
        ],
        model: "TS0013",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0726-1-BS",
        ],
        model: "EC-GL86ZPCS11",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0726-2-BS",
        ],
        model: "EC-GL86ZPCS21",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0726-3-BS",
        ],
        model: "EC-GL86ZPCS31",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "BS4",
            "TS0726-4-BS",
        ],
        model: "EC-GL86ZPCS41",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
            romasku.relayIndicatorMode("relay_0_indicator_mode", "relay_0"),
            romasku.relayIndicator("relay_0_indicator", "relay_0"),
            romasku.relayIndicatorMode("relay_1_indicator_mode", "relay_1"),
            romasku.relayIndicator("relay_1_indicator", "relay_1"),
            romasku.relayIndicatorMode("relay_2_indicator_mode", "relay_2"),
            romasku.relayIndicator("relay_2_indicator", "relay_2"),
            romasku.relayIndicatorMode("relay_3_indicator_mode", "relay_3"),
            romasku.relayIndicator("relay_3_indicator", "relay_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint7.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint8.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0726-1-BSL",
        ],
        model: "EC-SL-FK86ZPCS11",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0726-2-BSL",
        ],
        model: "EC-SL-FK86ZPCS21",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0726-3-BS",
        ],
        model: "EC-SL-FK86ZPCS31",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "Girier-4-gang",
        ],
        model: "TS0014",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
            romasku.relayIndicatorMode("relay_0_indicator_mode", "relay_0"),
            romasku.relayIndicator("relay_0_indicator", "relay_0"),
            romasku.relayIndicatorMode("relay_1_indicator_mode", "relay_1"),
            romasku.relayIndicator("relay_1_indicator", "relay_1"),
            romasku.relayIndicatorMode("relay_2_indicator_mode", "relay_2"),
            romasku.relayIndicator("relay_2_indicator", "relay_2"),
            romasku.relayIndicatorMode("relay_3_indicator_mode", "relay_3"),
            romasku.relayIndicator("relay_3_indicator", "relay_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint7.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint8.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-HBS",
        ],
        model: "TS0601_switch_1_gang",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-HMT",
        ],
        model: "Homeetec_37022454",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-HMT",
        ],
        model: "37022463-1",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-HMT",
        ],
        model: "37022474_1",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-IHS-T",
        ],
        model: "_TZ3000_qq9ahj6z",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-IHS-T",
        ],
        model: "_TZ3000_zxrfobzw",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-IHS-T",
        ],
        model: "TW-03",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "LerLink-2-gang",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "LerLink-3-gang",
        ],
        model: "TS0013",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "LerLink-4-gang",
        ],
        model: "TS0014",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
            romasku.relayIndicatorMode("relay_0_indicator_mode", "relay_0"),
            romasku.relayIndicator("relay_0_indicator", "relay_0"),
            romasku.relayIndicatorMode("relay_1_indicator_mode", "relay_1"),
            romasku.relayIndicator("relay_1_indicator", "relay_1"),
            romasku.relayIndicatorMode("relay_2_indicator_mode", "relay_2"),
            romasku.relayIndicator("relay_2_indicator", "relay_2"),
            romasku.relayIndicatorMode("relay_3_indicator_mode", "relay_3"),
            romasku.relayIndicator("relay_3_indicator", "relay_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint7.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint8.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS130F-LT",
        ],
        model: "TS130F",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"cover_switch": 1, "cover": 2, } }),
            romasku.deviceConfig("device_config", "cover_switch"),
            romasku.deviceConfigExt("device_config_ext", "cover_switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "cover_switch"),
            romasku.networkIndicator("network_led", "cover_switch"),
            windowCovering({ 
                controls: ["lift"],
                coverInverted: true,
                configureReporting: true,
                endpointNames: ["cover"]
            }),
            romasku.coverMoving("moving", "cover"),
            romasku.coverMotorReversal("cover_motor_reversal", "cover"),
            romasku.coverOpenTime("cover_open_time", "cover"),
            romasku.coverCloseTime("cover_close_time", "cover"),
            romasku.coverOpenDeadzone("cover_open_deadzone", "cover"),
            romasku.coverClosedDeadzone("cover_closed_deadzone", "cover"),
            romasku.coverSwitchPressAction("cover_switch_press_action", "cover_switch"),
            romasku.coverSwitchType("cover_switch_type", "cover_switch"),
            romasku.coverSwitchInvert("cover_switch_invert", "cover_switch"),
            romasku.coverSwitchCoverIndex("cover_switch_cover_index", "cover_switch", 1),
            romasku.coverSwitchLocalMode("cover_switch_local_mode", "cover_switch"),
            romasku.coverSwitchBindedMode("cover_switch_binded_mode", "cover_switch"),
            romasku.coverSwitchLongPressDuration("cover_switch_long_press_duration", "cover_switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {


            const coverSwitch1 = device.getEndpoint(1);
            await reporting.bind(coverSwitch1, coordinatorEndpoint, ["genMultistateInput"]);
            await coverSwitch1.configureReporting("genMultistateInput", [
                {
                    attribute: "presentValue",
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);

            const cover1 = device.getEndpoint(2);
            await reporting.bind(cover1, coordinatorEndpoint, ["closuresWindowCovering"]);
            await cover1.configureReporting("closuresWindowCovering", [
                {
                    attribute: {ID: 0xff00, type: Zcl.DataType.ENUM8},
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-MH",
        ],
        model: "TS0011",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0012-MH",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0013-MH",
        ],
        model: "TS0013",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-MHB",
        ],
        model: "TS0011",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0012-MHB",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0013-MHB",
        ],
        model: "TS0013",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-MIL",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-YBJ",
        ],
        model: "TS0002",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-YBJ",
        ],
        model: "TS0003",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-YBJ",
        ],
        model: "TS0003",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-MIL",
        ],
        model: "TS0004",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
            romasku.relayIndicatorMode("relay_0_indicator_mode", "relay_0"),
            romasku.relayIndicator("relay_0_indicator", "relay_0"),
            romasku.relayIndicatorMode("relay_1_indicator_mode", "relay_1"),
            romasku.relayIndicator("relay_1_indicator", "relay_1"),
            romasku.relayIndicatorMode("relay_2_indicator_mode", "relay_2"),
            romasku.relayIndicator("relay_2_indicator", "relay_2"),
            romasku.relayIndicatorMode("relay_3_indicator_mode", "relay_3"),
            romasku.relayIndicator("relay_3_indicator", "relay_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint7.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint8.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "Moes-1-gang",
            "Moes-1-gang-ED",
        ],
        model: "ZS-EUB_1gang",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "Moes-2-gang",
            "Moes-2-gang-ED",
        ],
        model: "ZS-EUB_2gang",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "Moes-3-gang",
            "Moes-3-gang-ED",
        ],
        model: "TS0013",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "MS4",
        ],
        model: "TS0014",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
            romasku.relayIndicatorMode("relay_0_indicator_mode", "relay_0"),
            romasku.relayIndicator("relay_0_indicator", "relay_0"),
            romasku.relayIndicatorMode("relay_1_indicator_mode", "relay_1"),
            romasku.relayIndicator("relay_1_indicator", "relay_1"),
            romasku.relayIndicatorMode("relay_2_indicator_mode", "relay_2"),
            romasku.relayIndicator("relay_2_indicator", "relay_2"),
            romasku.relayIndicatorMode("relay_3_indicator_mode", "relay_3"),
            romasku.relayIndicator("relay_3_indicator", "relay_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint7.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint8.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "MS33",
        ],
        model: "SR-ZS",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
            romasku.relayIndicatorMode("relay_0_indicator_mode", "relay_0"),
            romasku.relayIndicator("relay_0_indicator", "relay_0"),
            romasku.relayIndicatorMode("relay_1_indicator_mode", "relay_1"),
            romasku.relayIndicator("relay_1_indicator", "relay_1"),
            romasku.relayIndicatorMode("relay_2_indicator_mode", "relay_2"),
            romasku.relayIndicator("relay_2_indicator", "relay_2"),
            romasku.relayIndicatorMode("relay_3_indicator_mode", "relay_3"),
            romasku.relayIndicator("relay_3_indicator", "relay_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint7.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint8.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "ZTS-3W-CUSTOM",
        ],
        model: "WS-US-ZB",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-PST",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-PST",
        ],
        model: "TS0002",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-PS",
            "T441",
        ],
        model: "T441",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-PS",
            "T442",
        ],
        model: "T442",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-PS",
        ],
        model: "ZM-L03E-Z",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-RSH",
        ],
        model: "TS0011",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0012-RSH",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-TA",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-TA1",
            "TS0001-LS",
        ],
        model: "X701A",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-TA",
        ],
        model: "X702A",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-TA",
        ],
        model: "X703A",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-TA1",
        ],
        model: "TS0003",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_middle_indicator_mode", "relay_middle"),
            romasku.relayIndicator("relay_middle_indicator", "relay_middle"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-TA",
        ],
        model: "TS0004",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
            romasku.relayIndicatorMode("relay_0_indicator_mode", "relay_0"),
            romasku.relayIndicator("relay_0_indicator", "relay_0"),
            romasku.relayIndicatorMode("relay_1_indicator_mode", "relay_1"),
            romasku.relayIndicator("relay_1_indicator", "relay_1"),
            romasku.relayIndicatorMode("relay_2_indicator_mode", "relay_2"),
            romasku.relayIndicator("relay_2_indicator", "relay_2"),
            romasku.relayIndicatorMode("relay_3_indicator_mode", "relay_3"),
            romasku.relayIndicator("relay_3_indicator", "relay_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint5.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint6.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint7.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint8.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0002-TB",
        ],
        model: "TS0002",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0001-CUS",
        ],
        model: "TS0001",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            romasku.networkIndicator("network_led", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0004-CUS",
        ],
        model: "TS0004",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_0": 1, "switch_1": 2, "switch_2": 3, "switch_3": 4, "relay_0": 5, "relay_1": 6, "relay_2": 7, "relay_3": 8, } }),
            romasku.deviceConfig("device_config", "switch_0"),
            romasku.deviceConfigExt("device_config_ext", "switch_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_0"),
            romasku.networkIndicator("network_led", "switch_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3"] }),
            romasku.pressAction("switch_0_press_action", "switch_0"),
            romasku.switchMode("switch_0_mode", "switch_0"),
            romasku.switchAction("switch_0_action_mode", "switch_0"),
            romasku.relayMode("switch_0_relay_mode", "switch_0"),
            romasku.relayIndex("switch_0_relay_index", "switch_0", 4),
            romasku.bindedMode("switch_0_binded_mode", "switch_0"),
            romasku.longPressDuration("switch_0_long_press_duration", "switch_0"),
            romasku.levelMoveRate("switch_0_level_move_rate", "switch_0"),
            romasku.pressAction("switch_1_press_action", "switch_1"),
            romasku.switchMode("switch_1_mode", "switch_1"),
            romasku.switchAction("switch_1_action_mode", "switch_1"),
            romasku.relayMode("switch_1_relay_mode", "switch_1"),
            romasku.relayIndex("switch_1_relay_index", "switch_1", 4),
            romasku.bindedMode("switch_1_binded_mode", "switch_1"),
            romasku.longPressDuration("switch_1_long_press_duration", "switch_1"),
            romasku.levelMoveRate("switch_1_level_move_rate", "switch_1"),
            romasku.pressAction("switch_2_press_action", "switch_2"),
            romasku.switchMode("switch_2_mode", "switch_2"),
            romasku.switchAction("switch_2_action_mode", "switch_2"),
            romasku.relayMode("switch_2_relay_mode", "switch_2"),
            romasku.relayIndex("switch_2_relay_index", "switch_2", 4),
            romasku.bindedMode("switch_2_binded_mode", "switch_2"),
            romasku.longPressDuration("switch_2_long_press_duration", "switch_2"),
            romasku.levelMoveRate("switch_2_level_move_rate", "switch_2"),
            romasku.pressAction("switch_3_press_action", "switch_3"),
            romasku.switchMode("switch_3_mode", "switch_3"),
            romasku.switchAction("switch_3_action_mode", "switch_3"),
            romasku.relayMode("switch_3_relay_mode", "switch_3"),
            romasku.relayIndex("switch_3_relay_index", "switch_3", 4),
            romasku.bindedMode("switch_3_binded_mode", "switch_3"),
            romasku.longPressDuration("switch_3_long_press_duration", "switch_3"),
            romasku.levelMoveRate("switch_3_level_move_rate", "switch_3"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.bind(endpoint4, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint4.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint7 = device.getEndpoint(7);
            await reporting.onOff(endpoint7, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint8 = device.getEndpoint(8);
            await reporting.onOff(endpoint8, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0003-TB",
        ],
        model: "TS0003",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_middle": 2, "switch_right": 3, "relay_left": 4, "relay_middle": 5, "relay_right": 6, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_middle", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 3),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_middle_press_action", "switch_middle"),
            romasku.switchMode("switch_middle_mode", "switch_middle"),
            romasku.switchAction("switch_middle_action_mode", "switch_middle"),
            romasku.relayMode("switch_middle_relay_mode", "switch_middle"),
            romasku.relayIndex("switch_middle_relay_index", "switch_middle", 3),
            romasku.bindedMode("switch_middle_binded_mode", "switch_middle"),
            romasku.longPressDuration("switch_middle_long_press_duration", "switch_middle"),
            romasku.levelMoveRate("switch_middle_level_move_rate", "switch_middle"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 3),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.bind(endpoint3, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint3.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "Zemi-2-gang",
            "Zemi-2-gang-ED",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            romasku.networkIndicator("network_led", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0011-ZS",
        ],
        model: "TS0011",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch": 1, "relay": 2, } }),
            romasku.deviceConfig("device_config", "switch"),
            romasku.deviceConfigExt("device_config_ext", "switch"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch"),
            onOff({ endpointNames: ["relay"] }),
            romasku.pressAction("switch_press_action", "switch"),
            romasku.switchMode("switch_mode", "switch"),
            romasku.switchAction("switch_action_mode", "switch"),
            romasku.relayMode("switch_relay_mode", "switch"),
            romasku.relayIndex("switch_relay_index", "switch", 1),
            romasku.bindedMode("switch_binded_mode", "switch"),
            romasku.longPressDuration("switch_long_press_duration", "switch"),
            romasku.levelMoveRate("switch_level_move_rate", "switch"),
            romasku.relayIndicatorMode("relay_indicator_mode", "relay"),
            romasku.relayIndicator("relay_indicator", "relay"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint2.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0012-ZS",
        ],
        model: "TS0012",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
            romasku.relayIndicatorMode("relay_left_indicator_mode", "relay_left"),
            romasku.relayIndicator("relay_left_indicator", "relay_left"),
            romasku.relayIndicatorMode("relay_right_indicator_mode", "relay_right"),
            romasku.relayIndicator("relay_right_indicator", "relay_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });

            await endpoint3.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            await endpoint4.configureReporting("genOnOff", [
                {
                    attribute: {ID: 0xff02, type: 0x10}, // Boolean
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);


        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0601-6G",
        ],
        model: "TS0601_switch_8",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"relay_0": 1, "relay_1": 2, "relay_2": 3, "relay_3": 4, "relay_4": 5, "relay_5": 6, } }),
            romasku.deviceConfig("device_config", "relay_0"),
            romasku.deviceConfigExt("device_config_ext", "relay_0"),
            romasku.dpConfig("dp_config", "relay_0"),
            romasku.mcuVersion("mcu_version", "relay_0"),
            romasku.multiPressResetCount("multi_press_reset_count", "relay_0"),
            onOff({ endpointNames: ["relay_0", "relay_1", "relay_2", "relay_3", "relay_4", "relay_5"] }),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.onOff(endpoint1, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint2 = device.getEndpoint(2);
            await reporting.onOff(endpoint2, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint5 = device.getEndpoint(5);
            await reporting.onOff(endpoint5, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint6 = device.getEndpoint(6);
            await reporting.onOff(endpoint6, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
    {
        zigbeeModel: [
            "TS0601-TPZ2",
        ],
        model: "SFL02-Z-2",
        vendor: "Tuya-custom",
        description: "Custom switch (https://github.com/romasku/tuya-zigbee-switch)",
        extend: [
            deviceEndpoints({ endpoints: {"switch_left": 1, "switch_right": 2, "relay_left": 3, "relay_right": 4, } }),
            romasku.deviceConfig("device_config", "switch_left"),
            romasku.deviceConfigExt("device_config_ext", "switch_left"),
            romasku.dpConfig("dp_config", "switch_left"),
            romasku.mcuVersion("mcu_version", "switch_left"),
            enumLookup({
                name: "mode_l1",
                endpointName: "switch_left",
                lookup: { "switch_1": 0, "scene_1": 1 },
                cluster: "genBasic",
                attribute: { ID: 0xff20, type: 0x30 }, // enum8
                description: "Switch 1 mode",
                entityCategory: "config",
            }),
            enumLookup({
                name: "mode_l2",
                endpointName: "switch_left",
                lookup: { "switch_2": 0, "scene_2": 1 },
                cluster: "genBasic",
                attribute: { ID: 0xff21, type: 0x30 }, // enum8
                description: "Switch 2 mode",
                entityCategory: "config",
            }),
            binary({
                name: "backlight_mode",
                endpointName: "switch_left",
                cluster: "genBasic",
                attribute: { ID: 0xff22, type: 0x10 }, // boolean
                valueOn: ["ON", 1],
                valueOff: ["OFF", 0],
                description: "Backlight mode",
                entityCategory: "config",
            }),
            enumLookup({
                name: "indicator_status",
                endpointName: "switch_left",
                lookup: { "off": 0, "relay": 1, "invert": 2 },
                cluster: "genBasic",
                attribute: { ID: 0xff23, type: 0x30 }, // enum8
                description: "Indicator status",
                entityCategory: "config",
            }),
            binary({
                name: "induction_mode",
                endpointName: "switch_left",
                cluster: "genBasic",
                attribute: { ID: 0xff24, type: 0x10 }, // boolean
                valueOn: ["ON", 1],
                valueOff: ["OFF", 0],
                description: "Induction mode",
                entityCategory: "config",
            }),
            enumLookup({
                name: "vibration_mode",
                endpointName: "switch_left",
                lookup: { "Gear 0": 0, "Gear 1": 1, "Gear 2": 2, "Gear 3": 3 },
                cluster: "genBasic",
                attribute: { ID: 0xff25, type: 0x30 }, // enum8
                description: "Vibration",
                entityCategory: "config",
            }),
            numeric({
                name: "momentary_1",
                endpointNames: ["switch_left"],
                cluster: "genBasic",
                attribute: { ID: 0xff26, type: 0x23 }, // uint32
                description: "Momentary switch timer 1",
                valueMin: 0,
                valueMax: 3600,
                unit: "s",
                entityCategory: "config",
            }),
            numeric({
                name: "momentary_2",
                endpointNames: ["switch_left"],
                cluster: "genBasic",
                attribute: { ID: 0xff27, type: 0x23 }, // uint32
                description: "Momentary switch timer 2",
                valueMin: 0,
                valueMax: 3600,
                unit: "s",
                entityCategory: "config",
            }),
            romasku.multiPressResetCount("multi_press_reset_count", "switch_left"),
            onOff({ endpointNames: ["relay_left", "relay_right"] }),
            romasku.pressAction("switch_left_press_action", "switch_left"),
            romasku.switchMode("switch_left_mode", "switch_left"),
            romasku.switchAction("switch_left_action_mode", "switch_left"),
            romasku.relayMode("switch_left_relay_mode", "switch_left"),
            romasku.relayIndex("switch_left_relay_index", "switch_left", 2),
            romasku.bindedMode("switch_left_binded_mode", "switch_left"),
            romasku.longPressDuration("switch_left_long_press_duration", "switch_left"),
            romasku.levelMoveRate("switch_left_level_move_rate", "switch_left"),
            romasku.pressAction("switch_right_press_action", "switch_right"),
            romasku.switchMode("switch_right_mode", "switch_right"),
            romasku.switchAction("switch_right_action_mode", "switch_right"),
            romasku.relayMode("switch_right_relay_mode", "switch_right"),
            romasku.relayIndex("switch_right_relay_index", "switch_right", 2),
            romasku.bindedMode("switch_right_binded_mode", "switch_right"),
            romasku.longPressDuration("switch_right_long_press_duration", "switch_right"),
            romasku.levelMoveRate("switch_right_level_move_rate", "switch_right"),
        ],
        meta: { multiEndpoint: true },
        configure: async (device, coordinatorEndpoint, logger) => {
            const endpoint1 = device.getEndpoint(1);
            await reporting.bind(endpoint1, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint1.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint2 = device.getEndpoint(2);
            await reporting.bind(endpoint2, coordinatorEndpoint, ["genMultistateInput"]);
            // switch action:
            await endpoint2.configureReporting("genMultistateInput", [
                {
                    attribute: {ID: 0x0055 /* presentValue */, type: 0x21}, // uint16
                    minimumReportInterval: 0,
                    maximumReportInterval: constants.repInterval.MAX,
                    reportableChange: 1,
                },
            ]);
            const endpoint3 = device.getEndpoint(3);
            await reporting.onOff(endpoint3, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });
            const endpoint4 = device.getEndpoint(4);
            await reporting.onOff(endpoint4, {
                min: 0,
                max: constants.repInterval.MAX,
                change: 1,
            });



        },
        ota: true,
    },
];

// The stock modules of the boards that carry the Open Dimmer image (AVATTO ZDMS16-2):
// zigbee-herdsman-converters' own definition, with OTA, for those boards' stock manufacturer
// names only. Zigbee2MQTT then offers the conversion (the -from_tuya image of the OTA index)
// with this file alone; tuya_with_ota.js gives OTA to some 60 stock models at once.
{
    let stockDefinitions = require("zigbee-herdsman-converters/devices/avatto");
    stockDefinitions = stockDefinitions.definitions ?? stockDefinitions;
    const stock = stockDefinitions.find((d) => d.model === "ZDMS16-2" && d.vendor === "AVATTO");
    if (stock) {
        const {zigbeeModel, ...stockWithoutIds} = stock;   // matched by the fingerprints below only
        definitions.push({
            ...stockWithoutIds,
            fingerprint: [
                {modelID: "TS0601", manufacturerName: "_TZE284_jtbgusdc"},
                {modelID: "TS0601", manufacturerName: "_TZE204_jtbgusdc"},
            ],
            ota: true,
        });
    } else {
        console.warn("switch_custom.js: no stock AVATTO ZDMS16-2 definition in zigbee-herdsman-converters: the conversion OTA is not offered");
    }
}

module.exports = definitions;
