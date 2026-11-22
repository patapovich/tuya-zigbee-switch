#include <getopt.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef HAL_STUB
#define HAL_STUB
#endif

#include "commands.h"
#include "simple_repl.h"
#include "stub/hal/stub.h"
#include "stub/hal/swd_target.h"
#include "stub_app.h"
#include "zigbee/stock_nv.h"

volatile sig_atomic_t g_should_exit = 0;

/* STUB_FACTORY_FLASH: a dump of a module's 1 MB flash as the factory NV the start-up reads
 * (the Telink start-up's flash read and 1 MB chip; zigbee/factory_identity.h). */
static uint8_t  s_factory_flash[0x100000];
static uint32_t s_factory_len;

static void factory_flash_read(uint32_t addr, uint32_t len, uint8_t *buf) {
    for (uint32_t i = 0; i < len; i++) {
        buf[i] = addr + i < s_factory_len ? s_factory_flash[addr + i] : 0xFF;
    }
}

static void factory_flash_start(void) {
    const char *path = getenv("STUB_FACTORY_FLASH");
    FILE *      f;

    if (!path || !*path) {
        return;
    }
    f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "[STUB] cannot read STUB_FACTORY_FLASH '%s'\n", path);
        return;
    }
    s_factory_len = (uint32_t)fread(s_factory_flash, 1, sizeof(s_factory_flash), f);
    fclose(f);
    g_stock_nv_flash_read     = factory_flash_read;
    g_stock_nv_diag.flash_cap = STOCK_NV_FLASH_CAP_1M;
}

static void on_sigint(int sig) {
    (void)sig;
    g_should_exit = 1;
}

static void poll_wrapper(void *u) {
    (void)u;
    stub_app_poll();
}

static void print_usage(const char *prog) {
    printf("Usage: %s [--device-config <string>] [--dp-config <string>] "
           "[--device-config-ext <string>] [--help]\n", prog);
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    signal(SIGINT, on_sigint);

    const struct option long_opts[] = {
        { "device-config",     required_argument, 0, 'd' },
        { "dp-config",         required_argument, 0, 'p' },
        { "device-config-ext", required_argument, 0, 'e' },
        { "not-joined",        no_argument,       0, 'j' },
        { "freeze-time",       no_argument,       0, 'f' },
        { "help",              no_argument,       0, 'h' },
        {                   0,                 0, 0,   0 }
    };

    char device_conf_buf[APP_DEVICE_CONF_MAX];
    device_conf_buf[0] = '\0';
    char dp_conf_buf[APP_DEVICE_CONF_MAX];
    dp_conf_buf[0] = '\0';
    char device_conf_ext_buf[APP_DEVICE_CONF_MAX];
    device_conf_ext_buf[0] = '\0';
    bool joined = true;
    for (;;) {
        int opt = getopt_long(argc, argv, "d:p:e:j:f:h", long_opts, NULL);
        if (opt == -1)
            break;
        switch (opt) {
        case 'd':
            snprintf(device_conf_buf, sizeof(device_conf_buf), "%s", optarg);
            break;
        case 'p':
            snprintf(dp_conf_buf, sizeof(dp_conf_buf), "%s", optarg);
            break;
        case 'e':
            snprintf(device_conf_ext_buf, sizeof(device_conf_ext_buf), "%s", optarg);
            break;
        case 'j':
            joined = false;
            break;
        case 'f':
            stub_millis_freeze();
            break;
        case 'h':
        default:
            print_usage(argv[0]);
            return 0;
        }
    }

    /* The dimmer MCU model from its environment, before the app can use it. */
    swd_target_start();
    factory_flash_start();

    stub_app_init(device_conf_buf[0] ? device_conf_buf : NULL,
                  dp_conf_buf[0] ? dp_conf_buf : NULL,
                  device_conf_ext_buf[0] ? device_conf_ext_buf : NULL, joined);

    puts("[STUB] Entering interactive mode. Type 'h' for help.");
    commands_print_help(); // auto-generated from table

    SimpleReplConfig cfg = {
        .commands      = commands_table(),
        .command_count = commands_count(),
        .poll_cb       = poll_wrapper,
        .poll_user     = NULL,
        .should_exit   = &g_should_exit,
    };
    (void)simple_repl_run(&cfg);

    stub_app_shutdown();
    return 0;
}
