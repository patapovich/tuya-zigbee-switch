// Loads a generated Z2M converter with small stand-ins for zigbee-herdsman and
// zigbee-herdsman-converters, so the tests can run the converter's own code
// (romasku.* extends, configure, onEvent) with plain node. The stand-ins only
// record what they are given: what zigbee-herdsman-converters does with it is
// not tested here. One exception: text() publishes a report the way the real
// one does, so what a romasku extend makes of that value can be tested.
//
//   const h = require("./converter_harness.js");
//   const definitions = h.load("/path/to/switch_custom.js");
const Module = require("module");

const fakeExpose = (type, name, access, values) => {
    const x = {type, name, access, values};
    const set = (key) => (v) => { x[key] = v; return x; };
    Object.assign(x, {
        withEndpoint: set("endpoint"), withDescription: set("description"), withCategory: set("category"),
        withLabel: set("label"),
        withValueMin: set("value_min"), withValueMax: set("value_max"), withUnit: set("unit"),
        withValueStep: set("value_step"),
        withPreset: (name, value, description) => { (x.presets ??= []).push({name, value, description}); return x; },
    });
    return x;
};

// What a fake extend publishes from a report: nothing, except text(): the
// attribute's value under name[_endpoint] when it comes from that endpoint (the
// real text() in zigbee-herdsman-converters' modernExtend).
const fakeConvert = (fn, args) => {
    if (fn !== "text") return () => undefined;
    const id = typeof args.attribute === "string" ? args.attribute : args.attribute.ID;
    const property = args.endpointName ? `${args.name}_${args.endpointName}` : args.name;
    return (model, msg) => {
        const map = typeof model.endpoint === "function" ? model.endpoint(msg.device) : {};
        if (msg.data?.[id] === undefined || (args.endpointName && map[args.endpointName] !== msg.endpoint.ID)) return undefined;
        return {[property]: msg.data[id]};
    };
};

/* light()'s toZigbee as zigbee-herdsman-converters 26.125.0 has them (dist/converters/
 * toZigbee.js light_onoff_brightness, on_off, light_brightness_step, light_brightness_move;
 * lib/utils.js getTransition), ported for the paths the dimmers use (no
 * moveToLevelWithOnOffDisable, no turnsOffAtBrightness1, no IKEA): the time is the message's
 * transition, else the device option, in 1/10 s ("specified"), else 0. Off with a time:
 * moveToLevelWithOnOff 0, and the level is remembered (globalStore brightness,
 * turnedOffWithTransition); a plain on with a time, or after an off with one: OnLevel (read
 * from the device) or the remembered level as moveToLevelWithOnOff; else genOnOff. */
const getTransition = (meta) => {
    if (meta.message?.transition != null) return {time: Number(meta.message.transition) * 10, specified: true};
    if (meta.options?.transition != null && meta.options.transition !== "") {
        return {time: Number(meta.options.transition) * 10, specified: true};
    }
    return {time: 0, specified: false};
};
/* zhc's lib/store: values per endpoint, by "<ieee>_<endpoint id>" (so a wrapped endpoint, a
 * Proxy, is the same one). */
const storeMap = new Map();
const storeKey = (entity) => `${entity.deviceIeeeAddress}_${entity.ID}`;
const globalStore = {
    getValue: (entity, key, fallback = undefined) => {
        const entry = storeMap.get(storeKey(entity));
        return entry !== undefined && key in entry ? entry[key] : fallback;
    },
    putValue: (entity, key, value) => {
        const k = storeKey(entity);
        if (!storeMap.has(k)) storeMap.set(k, {});
        storeMap.get(k)[key] = value;
    },
    clearValue: (entity, key) => { delete storeMap.get(storeKey(entity))?.[key]; },
    hasValue: (entity, key) => storeMap.get(storeKey(entity)) !== undefined && key in storeMap.get(storeKey(entity)),
    clear: () => storeMap.clear(),
};
const onOff = async (entity, state, meta) => {
    if (state === "on" && (meta.message.on_time != null || meta.message.off_wait_time != null)) {
        await entity.command("genOnOff", "onWithTimedOff", {ctrlbits: 0, ontime: Math.round((meta.message.on_time ?? 0) * 10),
                                                            offwaittime: Math.round((meta.message.off_wait_time ?? 0) * 10)}, {});
        return undefined;
    }
    await entity.command("genOnOff", state, {}, {});
    if (state === "toggle") {
        const current = meta.state?.[`state${meta.endpoint_name ? `_${meta.endpoint_name}` : ""}`];
        return current ? {state: {state: current === "OFF" ? "ON" : "OFF"}} : {};
    }
    return {state: {state: state.toUpperCase()}};
};
const lightOnOffBrightness = async (entity, key, value, meta) => {
    const {message} = meta;
    const transition = getTransition(meta);
    const optionalParams = {optionsMask: 0, optionsOverride: 0};
    let state = message.state !== undefined ? (typeof message.state === "string" ? message.state.toLowerCase() : null) : undefined;
    let brightness;
    if (message.brightness != null) brightness = Number(message.brightness);
    else if (message.brightness_percent != null) brightness = Math.round(Number(message.brightness_percent) * 255 / 100);
    if (brightness === 255) brightness = 254;
    if (state === undefined) state = meta.message.state = brightness === 0 ? "off" : "on";
    let publishBrightness = brightness !== undefined;
    const st = meta.state;
    const targetState = state === "toggle" ? (st.state === "ON" ? "off" : "on") : state;
    if (targetState === "off") {
        brightness = transition.specified || brightness === 0 ? 0 : undefined;
        if (st.brightness !== undefined && st.state === "ON") {
            globalStore.putValue(entity, "brightness", st.brightness);
            globalStore.putValue(entity, "turnedOffWithTransition", brightness !== undefined);
        }
    } else if (targetState === "on" && brightness === undefined) {
        if (transition.specified || globalStore.getValue(entity, "turnedOffWithTransition") === true) {
            let onLevel = st.level_config?.on_level ?? 0;
            if (onLevel === 0) {
                const read = await entity.read("genLevelCtrl", ["onLevel"]);
                if (read !== undefined) onLevel = read.onLevel;
            }
            if (onLevel === 0) onLevel = "previous";
            brightness = onLevel === 255 || onLevel === "previous"
                ? globalStore.getValue(entity, "brightness", st.brightness ?? 254) : onLevel;
            publishBrightness = true;
        }
    }
    if (brightness === undefined) return onOff(entity, state, meta);
    if (brightness === 0 && (targetState === "on" || state === null)) brightness = 1;
    if (targetState !== "off") {
        globalStore.putValue(entity, "brightness", brightness);
        globalStore.clearValue(entity, "turnedOffWithTransition");
    }
    await entity.command("genLevelCtrl", state === null ? "moveToLevel" : "moveToLevelWithOnOff",
                         {level: Number(brightness), transtime: transition.time, ...optionalParams}, {});
    const result = {state: {}};
    if (publishBrightness) result.state.brightness = Number(brightness);
    if (state !== null) result.state.state = brightness === 0 ? "OFF" : "ON";
    return result;
};
const lightToZigbee = (args) => [
    {
        key: ["state", "brightness", "brightness_percent", "on_time", "off_wait_time"],
        ...(args?.endpointNames ? {endpoints: args.endpointNames} : {}),
        convertSet: lightOnOffBrightness,
    },
    {
        key: ["brightness_step", "brightness_step_onoff"],
        convertSet: async (entity, key, value, meta) => {
            await entity.command("genLevelCtrl", key.endsWith("_onoff") ? "stepWithOnOff" : "step",
                                 {stepmode: value > 0 ? 0 : 1, stepsize: Math.abs(value),
                                  transtime: getTransition(meta).time, optionsMask: 0, optionsOverride: 0}, {});
        },
    },
    {
        key: ["brightness_move", "brightness_move_onoff"],
        convertSet: async (entity, key, value, meta) => {
            await entity.command("genLevelCtrl", key.endsWith("onoff") ? "moveWithOnOff" : "move",
                                 {movemode: value > 0 ? 0 : 1, rate: Math.abs(value)}, {});
        },
    },
];

const modernExtend = {};
for (const fn of ["numeric", "enumLookup", "deviceEndpoints", "onOff", "light", "text", "binary", "windowCovering"]) {
    modernExtend[fn] = (args) => ({
        isModernExtend: true,
        fake: fn,
        args,
        fromZigbee: [{cluster: args?.cluster, type: ["attributeReport", "readResponse"], convert: fakeConvert(fn, args)}],
        toZigbee: fn === "light" ? lightToZigbee(args) : [{key: [args?.name]}],
        exposes: [],
    });
}

const log = [];        // what the converter did, in order
const delays = [];     // setTimeout delays it asked for

const fakes = {
    "zigbee-herdsman-converters/lib/modernExtend": modernExtend,
    "zigbee-herdsman-converters/lib/utils": {
        assertString: (v) => { if (typeof v !== "string") throw new Error("not a string"); },
        getOptions: () => ({}),
    },
    "zigbee-herdsman-converters/lib/store": globalStore,
    "zigbee-herdsman-converters/lib/reporting": {
        bind: async (endpoint, coordinator, clusters) => log.push(["bind", endpoint.ID, clusters]),
        onOff: async (endpoint, config) => log.push(["report", endpoint.ID, "genOnOff", config]),
    },
    "zigbee-herdsman-converters/lib/constants": {repInterval: {MAX: 65000, HOUR: 3600}},
    "zigbee-herdsman-converters/lib/exposes": {
        presets: {
            enum: (name, access, values) => fakeExpose("enum", name, access, values),
            numeric: (name, access) => fakeExpose("numeric", name, access),
            text: (name, access) => fakeExpose("text", name, access),
            action: (values) => fakeExpose("enum", "action", 1, values),
            binary: (name, access, valueOn, valueOff) => Object.assign(fakeExpose("binary", name, access),
                                                                        {value_on: valueOn, value_off: valueOff}),
        },
        access: {STATE: 1, SET: 2, STATE_SET: 3, STATE_GET: 5, ALL: 7},
    },
    "zigbee-herdsman-converters/lib/ota": {zigbeeOTA: {}},
    // the stock module's own definition (zigbee-herdsman-converters devices/avatto.js): a few of
    // its fingerprints and what the converter must keep of it
    "zigbee-herdsman-converters/devices/avatto": {definitions: [
        {zigbeeModel: ["other"], model: "ZMS-other", vendor: "AVATTO", description: "another one"},
        {fingerprint: [{modelID: "TS0601", manufacturerName: "_TZE204_o9gyszw2"},
                       {modelID: "TS0601", manufacturerName: "_TZE284_jtbgusdc"},
                       {modelID: "TS0601", manufacturerName: "_TZE204_xxxxxxxx"}],
         model: "ZDMS16-2", vendor: "AVATTO", description: "Zigbee Module 2 channels Dimmer",
         exposes: ["stock exposes"], fromZigbee: ["stock fz"], toZigbee: ["stock tz"]},
    ]},
    "zigbee-herdsman": {Zcl: {DataType: {BOOLEAN: 0x10, BITMAP8: 0x18, UINT8: 0x20, UINT16: 0x21, UINT32: 0x23, ENUM8: 0x30}}},
};

const load = Module._load;
Module._load = function (request, ...rest) {
    return Object.prototype.hasOwnProperty.call(fakes, request) ? fakes[request] : load.call(this, request, ...rest);
};

// No real waiting: configure's pauses are recorded and run at once.
const realSetTimeout = setTimeout;
global.setTimeout = (fn, ms) => { delays.push(ms); return realSetTimeout(fn, 0); };

/* A device whose endpoint 1 answers mcu_version reads with the next of
 * versions (the last one repeats; an Error is thrown), a genLevelCtrl onLevel read with
 * onLevel (255: none, as the module has it by default), a read of the SWD master's
 * status (Basic 0xff50) with flasher (a byte array; undefined: a module without the
 * master answers nothing), and every other read with {}.
 * Reads, writes and reporting land in log. */
const device = ({versions = [""], meta = {}, onLevel = 255, missing = [], flasher} = {}) => {
    const cache = {};
    const queue = [...versions];
    const endpoints = {};
    // missing: endpoints Zigbee2MQTT's database does not have (yet), until an interview
    const absent = new Set(missing);
    const endpoint = (id) => absent.has(id) ? undefined : (endpoints[id] ??= {
        ID: id,
        deviceIeeeAddress: "0x0000000000000001",
        clusters: {genBasic: {attributes: cache}},
        read: async (cluster, attrs) => {
            log.push(["read", id, cluster, attrs]);
            if (id === 1 && cluster === "genBasic" && attrs[0] === 0xff12) {
                const v = queue.length > 1 ? queue.shift() : queue[0];
                if (v instanceof Error) throw v;
                cache[0xff12] = v;
                return {[0xff12]: v};
            }
            if (cluster === "genLevelCtrl" && attrs.length === 1 && attrs[0] === "onLevel") return {onLevel};
            if (id === 1 && cluster === "genBasic" && attrs[0] === 0xff50 && flasher !== undefined) return {[0xff50]: flasher};
            return {};
        },
        write: async (cluster, payload) => log.push(["write", id, cluster, payload]),
        command: async (cluster, command, payload) => log.push(["command", id, cluster, command, payload]),
        configureReporting: async (cluster, items) => log.push(["report", id, cluster, items]),
    });
    return {ieeeAddr: "0x0000000000000001", meta, saves: 0, save() { this.saves++; }, getEndpoint: endpoint, endpoints: [],
            interview: async (ignoreCache) => { log.push(["interview", ignoreCache]); absent.clear(); }};
};

/* The converter's definitions and, for the tests, some of its internals. */
const loadConverter = (path) => {
    const fs = require("fs");
    const m = new Module(path, module);
    m.filename = path;
    m.paths = Module._nodeModulePaths(require("path").dirname(path));
    m._compile(fs.readFileSync(path, "utf8") +
        "\nmodule.exports = {definitions, romasku, realMcuVersion, shownMcuVersion, mcuVersionOf, ownMcuMajorOf, ownMcuShown};\n", path);
    return m.exports;
};

module.exports = {
    load: loadConverter,
    device,
    log,
    delays,
    globalStore,
    // The extends of a definition built from the fake modernExtend functions.
    fakeExtends: (definition) => definition.extend.filter((x) => x.fake),
};
