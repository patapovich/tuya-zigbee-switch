#include "hal/uart.h"
#include "stub/hal/stub.h"
#include "stub/machine_io.h"

#include <stddef.h>
#include <stdint.h>

/* RX: the software ring of the Telink HAL (src/telink/hal/uart.c): the same
 * size and the same rule, a full ring drops the rest of a chunk. */
#define HAL_UART_STUB_RX_DEPTH    512
/* TX: what the firmware wrote and the test has not taken yet. */
#define HAL_UART_STUB_TX_DEPTH    4096

/* Free-running indices, as in the Telink HAL: used = head - tail never
 * confuses a full ring with an empty one (head == tail is always empty).
 * Both depths divide 65536, so the uint16_t wrap keeps the index in step. */
typedef struct {
    uint8_t  rx_buf[HAL_UART_STUB_RX_DEPTH];
    uint8_t  tx_buf[HAL_UART_STUB_TX_DEPTH];
    uint16_t rx_head;
    uint16_t rx_tail;
    uint16_t tx_head;
    uint16_t tx_tail;
    uint8_t  initialized;
} hal_uart_stub_t;

static hal_uart_stub_t g_hal_uart_stub;

/* Test settings: they model the hardware and the line, so a re-init of the
 * UART by the firmware keeps them. A DMA chunk max of 0 is a plain byte
 * stream. */
static uint16_t g_hal_uart_stub_rx_dropped = 0;
static uint16_t g_dma_chunk_max            = 0;
static uint16_t g_fail_writes = 0;
static uint16_t g_init_count  = 0;

static uint16_t rx_used(void) {
    return (uint16_t)(g_hal_uart_stub.rx_head - g_hal_uart_stub.rx_tail);
}

static uint16_t tx_used(void) {
    return (uint16_t)(g_hal_uart_stub.tx_head - g_hal_uart_stub.tx_tail);
}

/* Bytes received and not read yet are lost on a (re-)init, as on the Telink
 * HAL. Bytes already written stay in the TX capture: they are on the wire. */
static void rx_clear(void) {
    g_hal_uart_stub.rx_head = 0;
    g_hal_uart_stub.rx_tail = 0;
}

void hal_uart_init(const hal_uart_config_t *cfg) {
    rx_clear();
    g_hal_uart_stub.initialized = 1;
    g_init_count++;

    if (cfg) {
        io_log("UART", "init baudrate=%lu data_bits=%u stop_bits=%u parity=%u",
               (unsigned long)cfg->baudrate, cfg->data_bits, cfg->stop_bits,
               cfg->parity);
    } else {
        io_log("UART", "init default config baudrate=115200");
    }
}

void hal_uart_deinit(void) {
    rx_clear();
    g_hal_uart_stub.initialized = 0;
    io_log("UART", "deinit");
}

uint16_t hal_uart_rx_dropped(void) {
    return g_hal_uart_stub_rx_dropped;
}

uint16_t hal_uart_rx_available(void) {
    if (!g_hal_uart_stub.initialized) {
        return 0;
    }
    return rx_used();
}

void hal_uart_flush(void) {
    rx_clear();
    io_log("UART", "flush");
}

hal_uart_status_t hal_uart_write(const uint8_t *data, uint16_t len,
                                 uint16_t *written) {
    if (!data && len != 0) {
        return HAL_UART_ERR_INVALID_ARG;
    }
    if (!g_hal_uart_stub.initialized) {
        return HAL_UART_ERR_IO;
    }
    if (g_fail_writes > 0) {
        /* A wedged UART: the Telink write gives up before the first byte. */
        g_fail_writes--;
        if (written) {
            *written = 0;
        }
        io_log("UART", "write of %u byte(s) failed (test)", len);
        return HAL_UART_ERR_IO;
    }

    uint16_t actual = 0;
    while (actual < len && tx_used() < HAL_UART_STUB_TX_DEPTH) {
        g_hal_uart_stub.tx_buf[g_hal_uart_stub.tx_head % HAL_UART_STUB_TX_DEPTH] =
            data[actual++];
        g_hal_uart_stub.tx_head++;
    }

    if (written) {
        *written = actual;
    }
    if (actual < len) {
        /* Never silently: a test that lets this much build up must take it. */
        io_log("UART", "TX capture full, %u of %u byte(s) written", actual, len);
        return HAL_UART_ERR_BUSY;
    }
    io_log("UART", "write %u byte(s)", actual);
    return HAL_UART_OK;
}

hal_uart_status_t hal_uart_read(uint8_t *data, uint16_t len,
                                uint16_t *read_len) {
    if (!data && len != 0) {
        return HAL_UART_ERR_INVALID_ARG;
    }
    if (!g_hal_uart_stub.initialized) {
        return HAL_UART_ERR_IO;
    }

    uint16_t actual = 0;
    while (actual < len && rx_used() > 0) {
        data[actual++] =
            g_hal_uart_stub.rx_buf[g_hal_uart_stub.rx_tail % HAL_UART_STUB_RX_DEPTH];
        g_hal_uart_stub.rx_tail++;
    }

    if (read_len) {
        *read_len = actual;
    }

    if (actual > 0) {
        io_log("UART", "read %u byte(s)", actual);
    }

    return HAL_UART_OK;
}

hal_uart_status_t hal_uart_write_byte(uint8_t byte) {
    uint16_t written = 0;

    return hal_uart_write(&byte, 1, &written);
}

hal_uart_status_t hal_uart_read_byte(uint8_t *byte) {
    uint16_t read_len = 0;

    if (!byte) {
        return HAL_UART_ERR_INVALID_ARG;
    }
    return hal_uart_read(byte, 1, &read_len);
}

/* ---- test hooks -------------------------------------------------------- */

/* The Tuya secondary MCU bridge had no runtime coverage at all: everything was
 * asserted against the source text. These let a test drive both directions. */

void stub_uart_inject_rx(const uint8_t *data, uint16_t len) {
    if (!data || !g_hal_uart_stub.initialized) {
        return;
    }
    if (g_dma_chunk_max != 0 && len > g_dma_chunk_max) {
        /* The Telink RX DMA hands over what came in until the line went
         * idle; a chunk larger than its buffer is thrown away whole. */
        g_hal_uart_stub_rx_dropped += len;
        io_log("UART", "DMA chunk of %u byte(s) dropped (max %u)", len,
               g_dma_chunk_max);
        return;
    }
    for (uint16_t i = 0; i < len; i++) {
        if (rx_used() >= HAL_UART_STUB_RX_DEPTH - 1) {
            g_hal_uart_stub_rx_dropped += (uint16_t)(len - i);
            break;
        }
        g_hal_uart_stub.rx_buf[g_hal_uart_stub.rx_head % HAL_UART_STUB_RX_DEPTH] = data[i];
        g_hal_uart_stub.rx_head++;
    }
}

uint16_t stub_uart_take_tx(uint8_t *out, uint16_t max) {
    uint16_t actual = 0;

    if (!out) {
        return 0;
    }
    while (actual < max && tx_used() > 0) {
        out[actual++] =
            g_hal_uart_stub.tx_buf[g_hal_uart_stub.tx_tail % HAL_UART_STUB_TX_DEPTH];
        g_hal_uart_stub.tx_tail++;
    }
    return actual;
}

void stub_uart_set_dma_chunk_max(uint16_t max) {
    g_dma_chunk_max = max;
}

void stub_uart_fail_writes(uint16_t count) {
    g_fail_writes = count;
}

uint16_t stub_uart_init_count(void) {
    return g_init_count;
}
