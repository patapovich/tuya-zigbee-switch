// Loads a generated Z2M converter with small stand-ins for zigbee-herdsman and
// zigbee-herdsman-converters, so the tests can run the converter's own code
// (romasku.* extends, configure, onEvent) with plain node. The stand-ins only
// record what they are given: what zigbee-herdsman-converters does with it is
// not tested here.
//
//   const h = require("./converter_harness.js");
//   const definitions = h.load("/path/to/switch_custom.js");
const Module = require("module");

const fakeExpose = (type, name, access, values) => {
    const x = {type, name, access, values};
    const set = (key) => (v) => { x[key] = v; return x; };
    Object.assign(x, {
        withEndpoint: set("endpoint"), withDescription: set("description"), withCategory: set("category"),
        withValueMin: set("value_min"), withValueMax: set("value_max"), withUnit: set("unit"),
    });
    return x;
};

const modernExtend = {};
for (const fn of ["numeric", "enumLookup", "deviceEndpoints", "onOff", "light", "text", "binary", "windowCovering"]) {
    modernExtend[fn] = (args) => ({
        isModernExtend: true,
        fake: fn,
        args,
        fromZigbee: [{cluster: args?.cluster, convert: () => undefined}],
        toZigbee: [{key: [args?.name]}],
        exposes: [],
    });
}

const log = [];        // what the converter did, in order
const delays = [];     // setTimeout delays it asked for

const fakes = {
    "zigbee-herdsman-converters/lib/modernExtend": modernExtend,
    "zigbee-herdsman-converters/lib/utils": {assertString: (v) => { if (typeof v !== "string") throw new Error("not a string"); }},
    "zigbee-herdsman-converters/lib/reporting": {
        bind: async (endpoint, coordinator, clusters) => log.push(["bind", endpoint.ID, clusters]),
        onOff: async (endpoint, config) => log.push(["report", endpoint.ID, "genOnOff", config]),
    },
    "zigbee-herdsman-converters/lib/constants": {repInterval: {MAX: 65000, HOUR: 3600}},
    "zigbee-herdsman-converters/lib/exposes": {
        presets: {
            enum: (name, access, values) => fakeExpose("enum", name, access, values),
            numeric: (name, access) => fakeExpose("numeric", name, access),
            action: (values) => fakeExpose("enum", "action", 1, values),
        },
        access: {STATE: 1, SET: 2, STATE_SET: 3, STATE_GET: 5, ALL: 7},
    },
    "zigbee-herdsman-converters/lib/ota": {zigbeeOTA: {}},
    "zigbee-herdsman": {Zcl: {DataType: {BOOLEAN: 0x10, UINT8: 0x20, UINT16: 0x21, UINT32: 0x23, ENUM8: 0x30}}},
};

const load = Module._load;
Module._load = function (request, ...rest) {
    return Object.prototype.hasOwnProperty.call(fakes, request) ? fakes[request] : load.call(this, request, ...rest);
};

// No real waiting: configure's pauses are recorded and run at once.
const realSetTimeout = setTimeout;
global.setTimeout = (fn, ms) => { delays.push(ms); return realSetTimeout(fn, 0); };

/* A device whose endpoint 1 answers mcu_version reads with the next of
 * versions (the last one repeats; an Error is thrown), and every other read
 * with {}. Reads, writes and reporting land in log. */
const device = ({versions = [""], meta = {}} = {}) => {
    const cache = {};
    const queue = [...versions];
    const endpoints = {};
    const endpoint = (id) => (endpoints[id] ??= {
        ID: id,
        clusters: {genBasic: {attributes: cache}},
        read: async (cluster, attrs) => {
            log.push(["read", id, cluster, attrs]);
            if (id === 1 && cluster === "genBasic" && attrs[0] === 0xff12) {
                const v = queue.length > 1 ? queue.shift() : queue[0];
                if (v instanceof Error) throw v;
                cache[0xff12] = v;
                return {[0xff12]: v};
            }
            return {};
        },
        write: async (cluster, payload) => log.push(["write", id, cluster, payload]),
        configureReporting: async (cluster, items) => log.push(["report", id, cluster, items]),
    });
    return {ieeeAddr: "0x0000000000000001", meta, saves: 0, save() { this.saves++; }, getEndpoint: endpoint, endpoints: []};
};

/* The converter's definitions and, for the tests, some of its internals. */
const loadConverter = (path) => {
    const fs = require("fs");
    const m = new Module(path, module);
    m.filename = path;
    m.paths = Module._nodeModulePaths(require("path").dirname(path));
    m._compile(fs.readFileSync(path, "utf8") +
        "\nmodule.exports = {definitions, romasku, realMcuVersion, mcuVersionOf, ownMcuMajorOf, ownMcuShown};\n", path);
    return m.exports;
};

module.exports = {
    load: loadConverter,
    device,
    log,
    delays,
    // The extends of a definition built from the fake modernExtend functions.
    fakeExtends: (definition) => definition.extend.filter((x) => x.fake),
};
