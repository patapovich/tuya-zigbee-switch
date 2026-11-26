#include "commands.h"
#include "machine_io.h"

#include "hal/swd_io.h"
#include "hal/system.h"
#include "hal/timer.h"
#include "hal/uart.h"
#include "hal/zigbee.h"

#include "stub/hal/stub.h"
#include "stub/hal/swd_target.h"

#include "stub/stub_app.h"
#include "zigbee/consts.h"
#include "zigbee/nwk_sec.h"
#include "zigbee/stock_nv.h"
#include "zigbee/tclk_check.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int parse_u8_dec(const char *s, uint8_t *out) {
    char *e = NULL;
    long  v = strtol(s, &e, 10);

    if (*s == '\0' || *e || v < 0 || v > 255)
        return -1;

    *out = (uint8_t)v;
    return 0;
}

static int parse_u16_hex(const char *s, uint16_t *out) {
    char *e = NULL;
    long  v = strtol(s, &e, 16);

    if (*s == '\0' || *e || v < 0 || v > 0xFFFF)
        return -1;

    *out = (uint16_t)v;
    return 0;
}

extern volatile sig_atomic_t g_should_exit;

static int cmd_machine(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: machine on|off\n");
        io_res_err("usage");
        return -1;
    }
    if (strcmp(argv[1], "on") == 0) {
        g_machine_mode = true;
        io_res_ok(NULL);
        return 0;
    }
    if (strcmp(argv[1], "off") == 0) {
        io_res_ok(NULL);
        g_machine_mode = false;
        fprintf(stderr, "machine: off\n");
        return 0;
    }
    io_res_err("arg");
    return -1;
}

static int cmd_help(int argc, char **argv) {
    (void)argc;
    (void)argv;
    stub_app_print_help();
    io_res_ok(NULL);
    return 0;
}

static int cmd_status(int argc, char **argv) {
    (void)argc;
    (void)argv;
    stub_app_show_status();
    io_res_ok("uptime_ms=%u joined=%d poll_rate_ms=%u", hal_millis(),
              hal_zigbee_get_network_status(), hal_zigbee_get_poll_rate_ms());
    return 0;
}

static int cmd_quit(int argc, char **argv) {
    (void)argc;
    (void)argv;
    (void)swd_target_save(); /* before the answer: a test may restart at once */
    stub_system_power_cut(); /* the process ends as a power cut does */
    g_should_exit = 1;
    io_res_ok(NULL);
    return 0;
}

/* reboot: the chip resets alone (as its watchdog does: nothing saved first), the process ends;
 * what a power cut clears (the kept byte) stays for the next start. */
static int cmd_reboot(int argc, char **argv) {
    (void)argc;
    (void)argv;
    io_res_ok(NULL);
    fflush(stdout);
    hal_system_reset();
}

static int cmd_net(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: net <0|1|2>\n");
        io_res_err("usage");
        return -1;
    }
    char *e   = NULL;
    long  val = strtol(argv[1], &e, 10);
    if (*argv[1] == '\0' || *e || (val < 0 || val > 2)) {
        fprintf(stderr,
                "Value must be 0 (NOT_JOINED), 1 (JOINED), or 2 (JOINING)\n");
        io_res_err("bad_value=%s", argv[1]);
        return -1;
    }
    stub_zigbee_set_network_status((int)val);
    const char *status_str;
    switch ((hal_zigbee_network_status_t)val) {
    case HAL_ZIGBEE_NETWORK_NOT_JOINED:
        status_str = "NOT_JOINED";
        break;
    case HAL_ZIGBEE_NETWORK_JOINED:
        status_str = "JOINED";
        break;
    case HAL_ZIGBEE_NETWORK_JOINING:
        status_str = "JOINING";
        break;
    default:
        status_str = "UNKNOWN";
        break;
    }
    printf("Network status: %s\n", status_str);
    io_res_ok("joined=%ld status=%s", val, status_str);
    return 0;
}

static int cmd_pin(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "Usage: p <pin> <0|1>\n");
        io_res_err("usage");
        return -1;
    }
    char *e   = NULL;
    long  pin = strtol(argv[1], &e, 10);
    if (*argv[1] == '\0' || *e) {
        fprintf(stderr, "Bad pin\n");
        io_res_err("bad_pin=%s", argv[1]);
        return -1;
    }
    long val = strtol(argv[2], &e, 10);
    if (*argv[2] == '\0' || *e || (val != 0 && val != 1)) {
        fprintf(stderr, "Value must be 0 or 1\n");
        io_res_err("bad_value=%s", argv[2]);
        return -1;
    }
    stub_gpio_simulate_input((int)pin, (int)val);
    printf("Pin %ld <= %ld\n", pin, val);
    io_res_ok("pin=%ld value=%ld", pin, val);
    return 0;
}

static int cmd_zcl_list_attrs(int argc, char **argv) {
    (void)argc;
    (void)argv;
    stub_app_list_attrs();
    io_res_ok(NULL);
    return 0;
}

static int cmd_zcl_read(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "Usage: zcl_read <ep:dec> <cluster:hex> <attr:hex>\n");
        io_res_err("usage");
        return -1;
    }
    uint8_t  ep;
    uint16_t cl, at;
    if (parse_u8_dec(argv[1], &ep) || parse_u16_hex(argv[2], &cl) ||
        parse_u16_hex(argv[3], &at)) {
        fprintf(stderr, "Bad args\n");
        io_res_err("bad_args");
        return -1;
    }
    hal_zigbee_attribute *attr = stub_app_find_attr(ep, cl, at);
    if (attr) {
        char        buf[256];
        const char *val_str =
            stub_app_attribute_value_to_string(attr, buf, sizeof(buf));
        stub_app_print_attribute_value(attr);
        io_res_ok("ep=%d cluster=0x%04X attr=0x%04X value=%s", ep, cl, at, val_str);
    } else {
        puts("Attribute not found");
        io_res_err("attr_not_found ep=%d cluster=0x%04X attr=0x%04X", ep, cl, at);
    }
    return 0;
}

static int cmd_zcl_write(int argc, char **argv) {
    if (argc != 5) {
        fprintf(stderr,
                "Usage: zcl_write <ep:dec> <cluster:hex> <attr:hex> <value>\n");
        io_res_err("usage");
        return -1;
    }
    uint8_t  ep;
    uint16_t cl, at;
    if (parse_u8_dec(argv[1], &ep) || parse_u16_hex(argv[2], &cl) ||
        parse_u16_hex(argv[3], &at)) {
        fprintf(stderr, "Bad args\n");
        io_res_err("bad_args");
        return -1;
    }

    // Find attribute to determine its type
    hal_zigbee_attribute *attr = stub_app_find_attr(ep, cl, at);
    if (!attr) {
        fprintf(stderr,
                "Attribute not found (ep=%u, cluster=0x%04X, attr=0x%04X)\n", ep,
                cl, at);
        io_res_err("attr_not_found ep=%u cluster=0x%04X attr=0x%04X", ep, cl, at);
        return -1;
    }

    // Like the real stacks: a read-only attribute is refused (status
    // READ_ONLY), keeps its value and is not passed on as a change.
    if (attr->flag == ATTR_READONLY) {
        fprintf(stderr, "Attribute is read-only (ep=%u, cluster=0x%04X, attr=0x%04X)\n",
                ep, cl, at);
        io_res_err("read_only ep=%u cluster=0x%04X attr=0x%04X", ep, cl, at);
        return -1;
    }

    int ret = stub_app_string_to_attribute_value(attr, argv[4]);
    if (ret != 0) {
        fprintf(stderr, "Failed to parse value for attribute (code %d)\n", ret);
        io_res_err("bad_value ep=%u cluster=0x%04X attr=0x%04X", ep, cl, at);
        return -1;
    }

    stub_simulate_zigbee_attribute_write(ep, cl, at);
    io_res_ok("ep=%d cluster=0x%04X attr=0x%04X value=%s", ep, cl, at, argv[4]);

    return 0;
}

static int cmd_read_pin(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: read_pin <pin>\n");
        io_res_err("usage");
        return -1;
    }
    char *e   = NULL;
    int   pin = strtol(argv[1], &e, 10);
    if (*argv[1] == '\0' || *e) {
        fprintf(stderr, "Bad pin\n");
        io_res_err("bad_pin=%s", argv[1]);
        return -1;
    }
    int val = stub_gpio_get_output(pin);
    printf("Pin %d => %d\n", pin, val);
    io_res_ok("pin=%d value=%d", pin, val);
    return 0;
}

/* One injection: the hex tokens are joined. With uart_dma set, one injection
 * is one DMA chunk, i.e. bytes the MCU sent back to back. */
#define UART_RX_INJECT_MAX    2048
#define UART_TX_TAKE_MAX      4096

static int cmd_uart_rx(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: uart_rx <hexbytes> [<hexbytes>...]\n");
        io_res_err("usage");
        return -1;
    }
    static uint8_t buf[UART_RX_INJECT_MAX];
    uint16_t       len = 0;
    for (int a = 1; a < argc; a++) {
        const char *hex = argv[a];
        size_t      n   = strlen(hex);
        if (n == 0 || n % 2) {
            io_res_err("bad_hex");
            return -1;
        }
        if (len + n / 2 > sizeof(buf)) {
            io_res_err("too_long");
            return -1;
        }
        for (size_t i = 0; i < n; i += 2) {
            char  pair[3] = { hex[i], hex[i + 1], 0 };
            char *e       = NULL;
            long  v       = strtol(pair, &e, 16);
            if (*e) {
                io_res_err("bad_hex");
                return -1;
            }
            buf[len++] = (uint8_t)v;
        }
    }
    stub_uart_inject_rx(buf, len);
    io_res_ok("injected=%u", len);
    return 0;
}

static int cmd_uart_tx(int argc, char **argv) {
    (void)argc;
    (void)argv;
    static uint8_t buf[UART_TX_TAKE_MAX];
    static char    hex[2 * UART_TX_TAKE_MAX + 1];
    uint16_t       len = stub_uart_take_tx(buf, sizeof(buf));
    for (uint16_t i = 0; i < len; i++) {
        snprintf(hex + 2 * i, 3, "%02X", buf[i]);
    }
    hex[2 * len] = '\0';
    io_res_ok("tx=%s", hex);
    return 0;
}

static int parse_u16_dec(const char *s, uint16_t *out) {
    char *e = NULL;
    long  v = strtol(s, &e, 10);

    if (*s == '\0' || *e || v < 0 || v > 0xFFFF) {
        return -1;
    }
    *out = (uint16_t)v;
    return 0;
}

static int cmd_uart_dma(int argc, char **argv) {
    uint16_t max;

    if (argc != 2 || parse_u16_dec(argv[1], &max)) {
        fprintf(stderr, "Usage: uart_dma <max chunk bytes, 0 = byte stream>\n");
        io_res_err("usage");
        return -1;
    }
    stub_uart_set_dma_chunk_max(max);
    io_res_ok("dma_chunk_max=%u", max);
    return 0;
}

static int cmd_uart_fail_writes(int argc, char **argv) {
    uint16_t count;

    if (argc != 2 || parse_u16_dec(argv[1], &count)) {
        fprintf(stderr, "Usage: uart_fail_writes <count>\n");
        io_res_err("usage");
        return -1;
    }
    stub_uart_fail_writes(count);
    io_res_ok("fail_writes=%u", count);
    return 0;
}

static int cmd_uart_info(int argc, char **argv) {
    (void)argc;
    (void)argv;
    io_res_ok("inits=%u rx_dropped=%u", stub_uart_init_count(), hal_uart_rx_dropped());
    return 0;
}

static int cmd_zcl_cmd_impl(int argc, char **argv, bool trigger_activity, bool to_group) {
    uint16_t group = 0;

    if (to_group) {
        /* zcl_group_cmd <group:dec> <cluster> <cmd> [payload]: the ep argument is the group */
        if (argc < 4 || parse_u16_dec(argv[1], &group)) {
            io_res_err("usage");
            return -1;
        }
        argv[1] = "0";
    }
    if (argc < 4) {
        fprintf(stderr, "Usage: zcl_cmd <ep:dec> <cluster:hex> <cmd:hex> "
                "[payload:hex_bytes...]\n");
        io_res_err("usage");
        return -1;
    }

    uint8_t  ep;
    uint16_t cluster, cmd_id;

    if (parse_u8_dec(argv[1], &ep) || parse_u16_hex(argv[2], &cluster) ||
        parse_u16_hex(argv[3], &cmd_id)) {
        fprintf(stderr, "Bad args\n");
        io_res_err("bad_args");
        return -1;
    }

    // Parse optional payload bytes
    uint8_t payload[64] = { 0 }; // Max payload size
    int     payload_len = 0;

    for (int i = 4; i < argc && payload_len < sizeof(payload); i++) {
        char *e        = NULL;
        long  byte_val = strtol(argv[i], &e, 16);
        if (*argv[i] == '\0' || *e || byte_val < 0 || byte_val > 0xFF) {
            fprintf(stderr, "Bad payload byte: %s\n", argv[i]);
            io_res_err("bad_payload_byte=%s", argv[i]);
            return -1;
        }
        payload[payload_len++] = (uint8_t)byte_val;
    }

    hal_zigbee_cmd_result_t result;
    if (to_group) {
        result = stub_zigbee_simulate_group_command(group, cluster, cmd_id,
                                                    payload_len > 0 ? payload : NULL,
                                                    payload_len);
    } else if (trigger_activity) {
        result = stub_zigbee_simulate_command(ep, cluster, cmd_id,
                                              payload_len > 0 ? payload : NULL,
                                              payload_len);
    } else {
        result = stub_zigbee_simulate_command_without_activity(
            ep, cluster, cmd_id, payload_len > 0 ? payload : NULL,
            payload_len);
    }

    const char *result_str;
    switch (result) {
    case HAL_ZIGBEE_CMD_PROCESSED:
        result_str = "PROCESSED";
        break;
    case HAL_ZIGBEE_INVALID_VALUE:
        result_str = "INVALID_VALUE";
        break;
    case HAL_ZIGBEE_MALFORMED_COMMAND:
        result_str = "MALFORMED_COMMAND";
        break;
    case HAL_ZIGBEE_ACTION_DENIED:
        result_str = "ACTION_DENIED";
        break;
    case HAL_ZIGBEE_CMD_SKIPPED:
        result_str = "SKIPPED";
        break;
#ifdef DIMMER_SCENES
    case HAL_ZIGBEE_NOT_FOUND:
        result_str = "NOT_FOUND";
        break;
    case HAL_ZIGBEE_INVALID_FIELD:
        result_str = "INVALID_FIELD";
        break;
#endif
    default:
        result_str = "UNKNOWN";
        break;
    }

    printf("ZCL command result: %s (ep=%d, cluster=0x%04X, cmd=0x%02X, "
           "payload_len=%d)\n",
           result_str, ep, cluster, cmd_id, payload_len);
    io_res_ok("ep=%d cluster=0x%04X cmd=0x%02X result=%s payload_len=%d", ep,
              cluster, cmd_id, result_str, payload_len);

    return 0;
}

static int cmd_zcl_cmd(int argc, char **argv) {
    return cmd_zcl_cmd_impl(argc, argv, true, false);
}

static int cmd_zcl_cmd_no_activity(int argc, char **argv) {
    return cmd_zcl_cmd_impl(argc, argv, false, false);
}

static int cmd_freeze_time(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: freeze_time <0|1>\n");
        io_res_err("usage");
        return -1;
    }
    int freeze = atoi(argv[1]);
    if (freeze != 0 && freeze != 1) {
        fprintf(stderr, "Invalid argument: %s\n", argv[1]);
        io_res_err("bad_args");
        return -1;
    }
    if (freeze) {
        stub_millis_freeze();
    } else {
        stub_millis_unfreeze();
    }
    io_res_ok("freeze_time=%d", freeze);
    return 0;
}

static int cmd_step_time(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: step_time <milliseconds>\n");
        io_res_err("usage");
        return -1;
    }
    char *e    = NULL;
    long  step = strtol(argv[1], &e, 10);
    if (*argv[1] == '\0' || *e || step < 0) {
        fprintf(stderr, "Bad step value: %s\n", argv[1]);
        io_res_err("bad_step=%s", argv[1]);
        return -1;
    }
    stub_millis_step((uint64_t)step);
    io_res_ok("stepped_ms=%ld", step);
    return 0;
}

/* millis_skew <reads>: the frozen clock moves on 1 ms at each of its next reads (stub_millis_skew). */
static int cmd_millis_skew(int argc, char **argv) {
    char *e = NULL;
    long  n = argc == 2 ? strtol(argv[1], &e, 10) : -1;

    if (argc != 2 || *argv[1] == '\0' || *e || n < 0) {
        fprintf(stderr, "Usage: millis_skew <reads>\n");
        io_res_err("usage");
        return -1;
    }
    stub_millis_skew((uint32_t)n);
    io_res_ok("millis_skew=%ld", n);
    return 0;
}

/* hold_polls <0|1>: no main loop pass between the commands while 1 (stub_app_hold_polls). */
static int cmd_hold_polls(int argc, char **argv) {
    if (argc != 2 || (strcmp(argv[1], "0") != 0 && strcmp(argv[1], "1") != 0)) {
        fprintf(stderr, "Usage: hold_polls <0|1>\n");
        io_res_err("usage");
        return -1;
    }
    stub_app_hold_polls(argv[1][0] == '1');
    io_res_ok("hold_polls=%c", argv[1][0]);
    return 0;
}

/* poll <n>: n main loop passes now, also while they are held (stub_app_pass). */
static int cmd_poll(int argc, char **argv) {
    char *e = NULL;
    long  n = argc == 2 ? strtol(argv[1], &e, 10) : -1;

    if (argc != 2 || *argv[1] == '\0' || *e || n < 0 || n > 1000) {
        fprintf(stderr, "Usage: poll <n>\n");
        io_res_err("usage");
        return -1;
    }
    for (long i = 0; i < n; i++) {
        stub_app_pass();
    }
    io_res_ok("polled=%ld", n);
    return 0;
}

/* stock_nv <file> <module> <item> <len>: the factory NV parser (zigbee/stock_nv.c) over a dump
 * of a module's flash. */
static uint8_t  s_stock_flash[0x100000];
static uint32_t s_stock_len;

static void stock_flash_read(uint32_t addr, uint32_t len, uint8_t *buf) {
    for (uint32_t i = 0; i < len; i++) {
        buf[i] = addr + i < s_stock_len ? s_stock_flash[addr + i] : 0xFF;
    }
}

static int stock_flash_load(const char *path) {
    FILE *f = fopen(path, "rb");

    if (!f) {
        io_res_err("cannot_read=%s", path);
        return -1;
    }
    s_stock_len = (uint32_t)fread(s_stock_flash, 1, sizeof(s_stock_flash), f);
    fclose(f);
    return 0;
}

static int cmd_stock_nv(int argc, char **argv) {
    if (argc != 5) {
        io_res_err("usage");
        return -1;
    }
    if (stock_flash_load(argv[1])) {
        return -1;
    }
    uint16_t len = (uint16_t)strtoul(argv[4], NULL, 0);
    uint8_t  buf[256];
    char     hex[2 * sizeof(buf) + 1];

    if (len > sizeof(buf)) {
        io_res_err("len");
        return -1;
    }
    uint8_t found = stock_nv_item(stock_flash_read, (uint8_t)strtoul(argv[2], NULL, 0),
                                  (uint8_t)strtoul(argv[3], NULL, 0), buf, len);
    hex[0] = 0;
    for (uint16_t i = 0; found && i < len; i++) {
        sprintf(hex + 2 * i, "%02x", buf[i]);
    }
    io_res_ok("found=%u data=%s", found, hex);
    return 0;
}

/* stock_nv_take_ss <hex 81 bytes> [<hex 36 bytes>]: the take's check and mapping of a factory
 * security record (zigbee/stock_nv.c), with the TC link key pair or without. */
static int hex_bytes(const char *s, uint8_t *buf, size_t len) {
    if (strlen(s) != 2 * len) {
        return -1;
    }
    for (size_t i = 0; i < len; i++) {
        char          two[3] = { s[2 * i], s[2 * i + 1], 0 };
        char *        e      = NULL;
        unsigned long v      = strtoul(two, &e, 16);

        if (*e) {
            return -1;
        }
        buf[i] = (uint8_t)v;
    }
    return 0;
}

static int cmd_stock_nv_take_ss(int argc, char **argv) {
    uint8_t ss[STOCK_NV_SS_BYTES];
    uint8_t pair[STOCK_NV_PAIR_BYTES];
    char    hex[2 * sizeof(ss) + 1];

    if ((argc != 2 && argc != 3) || hex_bytes(argv[1], ss, sizeof(ss)) ||
        (argc == 3 && hex_bytes(argv[2], pair, sizeof(pair)))) {
        io_res_err("usage");
        return -1;
    }
    uint8_t r = stock_nv_take_ss(ss, argc == 3 ? pair : NULL);

    for (size_t i = 0; i < sizeof(ss); i++) {
        sprintf(hex + 2 * i, "%02x", ss[i]);
    }
    io_res_ok("r=%u ss=%s", r, hex);
    return 0;
}

#ifdef MCU_FLASHER_DUMP

/* stock_nv_log <file>: the debug builds' look at the W4c marker's log (zigbee/stock_nv.c
 * stock_nv_log_scan) over a flash image. */
static int cmd_stock_nv_log(int argc, char **argv) {
    uint32_t at   = 0;
    uint8_t  boot = 0;

    if (argc != 2) {
        io_res_err("usage");
        return -1;
    }
    if (stock_flash_load(argv[1])) {
        return -1;
    }
    uint8_t stale = stock_nv_log_scan(stock_flash_read, &at, &boot);

    io_res_ok("stale=%u at=%lu boot=%u", stale, (unsigned long)at, boot);
    return 0;
}

#endif

/* ssib: the stack's security record, byte 62 (src/stub/hal/zigbee.c), and the HAL's state of the
 * module's own NWK commands (zigbee/nwk_sec.h); ssib ram|nv|both <hex>: set it, as a record
 * taken over as it was leaves it (NV: what the next start-up takes). */
static int cmd_ssib(int argc, char **argv) {
    if (argc == 3) {
        char *        e   = NULL;
        unsigned long v   = strtoul(argv[2], &e, 16);
        bool          ram = strcmp(argv[1], "ram") == 0 || strcmp(argv[1], "both") == 0;
        bool          nv  = strcmp(argv[1], "nv") == 0 || strcmp(argv[1], "both") == 0;

        if (*argv[2] == '\0' || *e || v > 0xFF || !(ram || nv)) {
            io_res_err("usage");
            return -1;
        }
        stub_zigbee_ssib_set((uint8_t)v, ram, nv);
    } else if (argc != 1) {
        fprintf(stderr, "Usage: ssib [ram|nv|both <hex>]\n");
        io_res_err("usage");
        return -1;
    }
    int nv = stub_zigbee_ssib_nv();

    if (nv < 0) {
        io_res_ok("ram=0x%02X nv=none state=%u", stub_zigbee_ssib_ram(), g_nwk_sec_state);
    } else {
        io_res_ok("ram=0x%02X nv=0x%02X state=%u", stub_zigbee_ssib_ram(), nv, g_nwk_sec_state);
    }
    return 0;
}

/* tclk permit <network_wide> <s>: a Mgmt_Permit_Joining came, as the router's wrap tells the
 * trust centre link key check (zigbee/tclk_check.h): network_wide 1 for the coordinator's
 * broadcast, 0 for a request to this router alone; s the time, for which the router then allows
 * joins (0: a close). tclk upddev <status> <type>: the stack sends an Update Device for a child
 * (the request's status, ss_ib.tcLinkKeyType), with whether the router allows joins now; tclk
 * tunnel: the trust centre's tunnel came. Prints the state of Basic 0xff5d (zigbee/nwk_sec.h). */
static uint32_t tclk_permit_since, tclk_permit_ms;

static int tclk_number(const char *arg, unsigned long max, unsigned long *out) {
    char *end = NULL;

    *out = strtoul(arg, &end, 0);
    return *arg != '\0' && !*end && *out <= max;
}

static int cmd_tclk(int argc, char **argv) {
    unsigned long a, b;

    if (argc == 4 && strcmp(argv[1], "upddev") == 0) {
        if (!tclk_number(argv[2], 0xFF, &a) || !tclk_number(argv[3], 0xFF, &b)) {
            io_res_err("usage");
            return -1;
        }
        tclk_check_update_device((unsigned)a, (unsigned)b,
                                 hal_millis() - tclk_permit_since < tclk_permit_ms);
    } else if (argc == 4 && strcmp(argv[1], "permit") == 0) {
        if (!tclk_number(argv[2], 1, &a) || !tclk_number(argv[3], 0xFF, &b)) {
            io_res_err("usage");
            return -1;
        }
        tclk_permit_since = hal_millis();
        tclk_permit_ms    = (uint32_t)b * 1000u;
        tclk_check_permit_joining(a && b);
    } else if (argc == 2 && strcmp(argv[1], "tunnel") == 0) {
        tclk_check_ok();
    } else {
        fprintf(stderr, "Usage: tclk upddev <status> <type> | tclk permit <network_wide> <s> | "
                "tclk tunnel\n");
        io_res_err("usage");
        return -1;
    }
    io_res_ok("state=%u", g_nwk_sec_state);
    return 0;
}

static int cmd_set_battery_voltage(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: set_battery_voltage <millivolts>\n");
        io_res_err("usage");
        return -1;
    }
    char *e   = NULL;
    long  val = strtol(argv[1], &e, 10);
    if (*argv[1] == '\0' || *e || val < 0 || val > 0xFFFF) {
        fprintf(stderr, "Bad voltage value: %s\n", argv[1]);
        io_res_err("bad_value=%s", argv[1]);
        return -1;
    }
    stub_set_battery_voltage_mv((uint16_t)val);
    printf("Battery voltage set to %ld mV\n", val);
    io_res_ok("voltage_mv=%ld", val);
    return 0;
}

static int cmd_zcl_group_cmd(int argc, char **argv) {
    return cmd_zcl_cmd_impl(argc, argv, true, true);
}

/* zcl_group <add|remove|remove_all> <ep> [<group:dec>]: the stack's group table. */
static int cmd_zcl_group(int argc, char **argv) {
    uint8_t  ep;
    uint16_t group = 0;
    bool     all   = argc == 3 && strcmp(argv[1], "remove_all") == 0;

    if ((argc != 4 && !all) || parse_u8_dec(argv[2], &ep) ||
        (!all && parse_u16_dec(argv[3], &group))) {
        io_res_err("usage");
        return -1;
    }
    if (strcmp(argv[1], "add") == 0) {
        if (stub_zigbee_group_add(ep, group)) {
            io_res_err("table_full");
            return -1;
        }
    } else if (all || strcmp(argv[1], "remove") == 0) {
        stub_zigbee_group_remove(ep, group, all);
    } else {
        io_res_err("usage");
        return -1;
    }
    io_res_ok("ep=%u group=%u", ep, group);
    return 0;
}

/* zcl_leave: the device leaves its network (as asked by the coordinator). */
static int cmd_zcl_leave(int argc, char **argv) {
    (void)argc;
    (void)argv;
    hal_zigbee_leave_network();
    io_res_ok("left");
    return 0;
}

/* zcl_bind_group <ep> <cluster:hex> <group:dec>: a binding of the endpoint's cluster to a group. */
static int cmd_zcl_bind_group(int argc, char **argv) {
    uint8_t  ep;
    uint16_t cl, group;

    if (argc != 4 || parse_u8_dec(argv[1], &ep) || parse_u16_hex(argv[2], &cl) ||
        parse_u16_dec(argv[3], &group)) {
        io_res_err("usage");
        return -1;
    }
    stub_zigbee_add_group_binding(ep, cl, group);
    io_res_ok("ep=%u cluster=0x%04X group=%u", ep, cl, group);
    return 0;
}

static int parse_u32(const char *s, int base, uint32_t *out) {
    char *e = NULL;
    unsigned long long v = strtoull(s, &e, base);

    if (*s == '\0' || *s == '-' || *e || v > 0xFFFFFFFFull)
        return -1;

    *out = (uint32_t)v;
    return 0;
}

static int parse_bit(const char *s, uint8_t *out) {
    if (strcmp(s, "0") == 0 || strcmp(s, "1") == 0) {
        *out = (uint8_t)(s[0] - '0');
        return 0;
    }
    return -1;
}

/* Raw file into the target's memory map (backdoor). */
#define SWD_TARGET_LOAD_MAX    (64u * 1024u)

static int swd_target_load_file(uint32_t addr, const char *path, uint32_t *len) {
    static uint8_t buf[SWD_TARGET_LOAD_MAX + 1];
    FILE *         f = fopen(path, "rb");

    if (!f)
        return -1;

    size_t n = fread(buf, 1, sizeof(buf), f);
    fclose(f);
    if (n > SWD_TARGET_LOAD_MAX)
        return -1;

    *len = (uint32_t)n;
    return swd_target_load(addr, buf, (uint32_t)n);
}

static void swd_target_print_stats(void) {
    swd_target_stats_t st;
    swd_target_state_t cs;
    char dp[512];

    swd_target_get_stats(&st);
    swd_target_get_state(&cs);
    snprintf(dp, sizeof(dp),
             "line_resets=%u dpidr_reads=%u transfers_ok=%u waits=%u faults=%u "
             "protocol_errors=%u parity_errors=%u contention=%u mem_writes=%u "
             "powered=%u bus_errors=%u jtag_to_swd=%u sys_resets=%u "
             "untaken_ops=%u edges=%u lockout=%u ctrl_stat=0x%08X",
             st.line_resets, st.dpidr_reads, st.transfers_ok, st.waits,
             st.faults, st.protocol_errors, st.parity_errors, st.contention,
             st.mem_writes, swd_target_powered(), st.bus_errors,
             st.jtag_to_swd, st.sys_resets, st.untaken_ops, st.edges,
             swd_target_lockout(), swd_target_ctrl_stat());
    io_res_ok("%s erases=%u programs=%u wrperr=%u power_cycles=%u core=%s "
              "pc=0x%08X msp=0x%08X xpsr=0x%08X gates_low=%u iwdg_frozen=%u "
              "timing_set=%u flash_locked=%u key_lockout=%u flash_busy=%u "
              "swd_lost=%u rdp2_written=%u obl_launches=%u option_programs=%u "
              "mass_erases=%u preset=%s rdp=%u optlock=%u optkey_lockout=%u "
              "stock_fw=%u pins_taken=%u stock_pins_ms=%u uptime_ms=%u",
              dp, st.erases, st.programs, st.wrperr, st.power_cycles, cs.core,
              cs.pc, cs.msp, cs.xpsr, cs.gates_low, cs.iwdg_frozen,
              cs.timing_set, cs.flash_locked, cs.key_lockout, cs.flash_busy,
              cs.swd_lost, st.rdp2_written, st.obl_launches,
              st.option_programs, st.mass_erases, cs.preset, cs.rdp,
              cs.optlock, cs.optkey_lockout, cs.stock_fw, cs.pins_taken,
              cs.stock_pins_ms, cs.uptime_ms);
}

/* count=<n> dropped=<n> list=<op>:<addr>,... (oldest first) */
static void swd_target_print_flash_log(void) {
    uint32_t n   = swd_target_flash_log_count();
    char *   buf = malloc((size_t)n * 26u + 1u);
    size_t   pos = 0;

    if (!buf) {
        io_res_err("no_memory");
        return;
    }
    buf[0] = '\0';
    for (uint32_t i = 0; i < n; i++) {
        const char *op;
        uint32_t    addr;
        swd_target_flash_log_at(i, &op, &addr);
        pos += (size_t)snprintf(buf + pos, 27, "%s%s:0x%08X", i ? "," : "", op,
                                addr);
    }
    io_res_ok("count=%u dropped=%u list=%s", n, swd_target_flash_log_dropped(),
              buf);
    free(buf);
}

/* count=<n> dropped=<n> list=<addr>=<value>,... (oldest first, hex) */
static void swd_target_print_writes(void) {
    uint32_t n   = swd_target_write_count();
    char *   buf = malloc((size_t)n * 22u + 1u);
    size_t   pos = 0;

    if (!buf) {
        io_res_err("no_memory");
        return;
    }
    buf[0] = '\0';
    for (uint32_t i = 0; i < n; i++) {
        uint32_t addr, value;
        swd_target_write_at(i, &addr, &value);
        pos += (size_t)snprintf(buf + pos, 23, "%s0x%08X=0x%08X", i ? "," : "",
                                addr, value);
    }
    io_res_ok("count=%u dropped=%u list=%s", n, swd_target_writes_dropped(), buf);
    free(buf);
}

static int cmd_swd_target(int argc, char **argv) {
    const char *sub = argc >= 2 ? argv[1] : "";
    uint32_t    a, v;
    uint8_t     bit;

    if (strcmp(sub, "reset") == 0 && argc == 2) {
        swd_target_reset();
        io_res_ok(NULL);
    } else if (strcmp(sub, "clear") == 0 && argc == 2) {
        swd_target_clear();
        io_res_ok(NULL);
    } else if (strcmp(sub, "stats") == 0 && argc == 2) {
        swd_target_print_stats();
    } else if (strcmp(sub, "writes") == 0 && argc == 2) {
        swd_target_print_writes();
    } else if (strcmp(sub, "flash_log") == 0 && argc == 2) {
        swd_target_print_flash_log();
    } else if (strcmp(sub, "power_cycle") == 0 && argc == 2) {
        swd_target_power_cycle();
        io_res_ok(NULL);
    } else if (strcmp(sub, "cut_after") == 0 && argc == 3 &&
               parse_u32(argv[2], 10, &v) == 0) {
        swd_target_set_cut_after(v);
        io_res_ok("cut_after=%u", v);
    } else if (strcmp(sub, "busy_reads") == 0 && argc == 3 &&
               parse_u32(argv[2], 10, &v) == 0) {
        swd_target_set_busy_reads(v);
        io_res_ok("busy_reads=%u", v);
    } else if (strcmp(sub, "busy_reads_ob") == 0 && argc == 3 &&
               parse_u32(argv[2], 10, &v) == 0) {
        swd_target_set_busy_reads_ob(v);
        io_res_ok("busy_reads_ob=%u", v);
    } else if (strcmp(sub, "stock_pins_ms") == 0 && argc == 3 &&
               parse_u32(argv[2], 10, &v) == 0) {
        swd_target_set_stock_pins_ms(v);
        io_res_ok("stock_pins_ms=%u", v);
    } else if (strcmp(sub, "preset") == 0 && argc == 3) {
        if (swd_target_preset(argv[2])) {
            io_res_err("unknown_preset=%s", argv[2]);
            return -1;
        }
        io_res_ok("preset=%s", argv[2]);
    } else if (strcmp(sub, "save") == 0 && argc == 2) {
        if (swd_target_save()) {
            io_res_err("not_saved (SWD_TARGET_STATE unset or not writable)");
            return -1;
        }
        io_res_ok(NULL);
    } else if (strcmp(sub, "connect") == 0 && argc == 3 &&
               parse_bit(argv[2], &bit) == 0) {
        swd_target_set_connect(bit);
        io_res_ok("connect=%u", bit);
    } else if (strcmp(sub, "clk_held_low") == 0 && argc == 3 &&
               parse_bit(argv[2], &bit) == 0) {
        swd_target_set_clk_held_low(bit);
        io_res_ok("clk_held_low=%u", bit);
    } else if (strcmp(sub, "rdp") == 0 && argc == 3 &&
               parse_bit(argv[2], &bit) == 0) {
        swd_target_set_rdp(bit);
        io_res_ok("rdp=%u", bit);
    } else if (strcmp(sub, "wait_ap") == 0 && argc == 3 &&
               parse_u32(argv[2], 10, &v) == 0) {
        swd_target_set_wait_ap(v);
        io_res_ok("wait_ap=%u", v);
    } else if (strcmp(sub, "poke32") == 0 && argc == 4 &&
               parse_u32(argv[2], 16, &a) == 0 && parse_u32(argv[3], 16, &v) == 0) {
        if (swd_target_poke32(a, v)) {
            io_res_err("unmapped addr=0x%08X", a);
            return -1;
        }
        io_res_ok("addr=0x%08X value=0x%08X", a, v);
    } else if (strcmp(sub, "peek32") == 0 && argc == 3 &&
               parse_u32(argv[2], 16, &a) == 0) {
        if (swd_target_peek32(a, &v)) {
            io_res_err("unmapped addr=0x%08X", a);
            return -1;
        }
        io_res_ok("addr=0x%08X value=0x%08X", a, v);
    } else if (strcmp(sub, "load") == 0 && argc == 4 &&
               parse_u32(argv[2], 16, &a) == 0) {
        if (swd_target_load_file(a, argv[3], &v)) {
            io_res_err("load_failed addr=0x%08X", a);
            return -1;
        }
        io_res_ok("addr=0x%08X len=%u", a, v);
    } else {
        fprintf(stderr, "Usage: swd_target reset|clear|stats|writes|flash_log|"
                "power_cycle|save | preset stock|open_dimmer | connect <0|1> | "
                "clk_held_low <0|1> | rdp <0|1> | wait_ap <n> | busy_reads <n> | "
                "busy_reads_ob <n> | stock_pins_ms <ms> | cut_after <n> | "
                "poke32 <addr> <val> | peek32 <addr> | load <addr> <path>\n");
        io_res_err("usage");
        return -1;
    }
    return 0;
}

/* The pin layer by hand, for a reference SWD host in the tests. Pins are
 * numbers as in set_pin / read_pin. */
static int cmd_swd_pin(int argc, char **argv) {
    const char *sub = argc >= 2 ? argv[1] : "";
    uint32_t    clk, dio;
    uint8_t     bit;

    if (strcmp(sub, "idle") == 0 && argc == 3 &&
        parse_u32(argv[2], 10, &clk) == 0 && clk < HAL_INVALID_PIN) {
        bit = hal_swd_clk_idle_level((hal_gpio_pin_t)clk);
        io_res_ok("clk=%u level=%u", clk, bit);
    } else if (strcmp(sub, "take") == 0 && argc == 4 &&
               parse_u32(argv[2], 10, &clk) == 0 && clk < HAL_INVALID_PIN &&
               parse_u32(argv[3], 10, &dio) == 0 && dio < HAL_INVALID_PIN) {
        hal_swd_take((hal_gpio_pin_t)clk, (hal_gpio_pin_t)dio);
        io_res_ok("clk=%u dio=%u", clk, dio);
    } else if (strcmp(sub, "release") == 0 && argc == 2) {
        hal_swd_release();
        io_res_ok(NULL);
    } else if (strcmp(sub, "clk") == 0 && argc == 3 &&
               parse_bit(argv[2], &bit) == 0) {
        hal_swd_clk(bit);
        io_res_ok(NULL);
    } else if (strcmp(sub, "drive") == 0 && argc == 3 &&
               parse_bit(argv[2], &bit) == 0) {
        hal_swd_dio_drive(bit);
        io_res_ok(NULL);
    } else if (strcmp(sub, "set") == 0 && argc == 3 &&
               parse_bit(argv[2], &bit) == 0) {
        hal_swd_dio_set(bit);
        io_res_ok(NULL);
    } else if (strcmp(sub, "get") == 0 && argc == 2) {
        io_res_ok("level=%u", hal_swd_dio_get());
    } else {
        fprintf(stderr, "Usage: swd_pin idle <clk> | take <clk> <dio> | release "
                "| clk <0|1> | drive <0|1> | set <0|1> | get\n");
        io_res_err("usage");
        return -1;
    }
    return 0;
}

/* Command table */
static const SimpleReplCommand kCmds[] = {
    { "machine",             cmd_machine             },
    { "help",                cmd_help                },
    { "s",                   cmd_status              },
    { "status",              cmd_status              },
    { "net",                 cmd_net                 },
    { "set_pin",             cmd_pin                 },
    { "read_pin",            cmd_read_pin            },
    { "uart_rx",             cmd_uart_rx             },
    { "uart_tx",             cmd_uart_tx             },
    { "uart_dma",            cmd_uart_dma            },
    { "uart_fail_writes",    cmd_uart_fail_writes    },
    { "uart_info",           cmd_uart_info           },
    { "zcl_read",            cmd_zcl_read            },
    { "zcl_write",           cmd_zcl_write           },
    { "zcl_list_attrs",      cmd_zcl_list_attrs      },
    { "zcl_cmd",             cmd_zcl_cmd             },
    { "zcl_cmd_no_activity", cmd_zcl_cmd_no_activity },
    { "zcl_group_cmd",       cmd_zcl_group_cmd       },
    { "zcl_group",           cmd_zcl_group           },
    { "zcl_bind_group",      cmd_zcl_bind_group      },
    { "zcl_leave",           cmd_zcl_leave           },
    { "freeze_time",         cmd_freeze_time         },
    { "step_time",           cmd_step_time           },
    { "millis_skew",         cmd_millis_skew         },
    { "hold_polls",          cmd_hold_polls          },
    { "poll",                cmd_poll                },
    { "stock_nv",            cmd_stock_nv            },
    { "stock_nv_take_ss",    cmd_stock_nv_take_ss    },
#ifdef MCU_FLASHER_DUMP
    { "stock_nv_log",        cmd_stock_nv_log        },
#endif
    { "ssib",                cmd_ssib                },
    { "tclk",                cmd_tclk                },
    { "set_battery_voltage", cmd_set_battery_voltage },
    { "swd_target",          cmd_swd_target          },
    { "swd_pin",             cmd_swd_pin             },
    { "reboot",              cmd_reboot              },
    { "q",                   cmd_quit                },
    { "quit",                cmd_quit                },
};

const SimpleReplCommand *commands_table(void) {
    return kCmds;
}

size_t commands_count(void) {
    return sizeof(kCmds) / sizeof(kCmds[0]);
}

void commands_print_help(void) {
    stub_app_print_help();
}
