#include "telink_size_t_hack.h"
#pragma pack(push, 1)
#include "tl_common.h"
#pragma pack(pop)

#include "hal/uart.h"

#include <string.h>

// Secondary-MCU UART for the ZTU (TLSR8258) module.
//
// The Tuya secondary MCU (e.g. Puya PY32F002A) is driven over a UART on the
// module's PB1 (TX to MCU) and PB7 (RX from MCU) pins. Both pins are part of
// the UART1 module's pinmux (PB1 = UART_TX_PB1, PB7 = UART_RX_PB7).
//
// RX uses the SDK's DMA receive path (drv_uart), which is wired into the
// main-build ISR via drv_uart_rx_irq_handler(). DMA moves incoming bytes into
// a RAM buffer as they arrive - the 8258's 4-byte hardware FIFO would
// otherwise overflow when the main loop is busy with the Zigbee stack. The
// ISR callback drains the DMA buffer into a software ring buffer, which
// hal_uart_read() consumes in the main loop.
//
// TX keeps the proven NDMA byte-push path (uart_ndma_send_byte).

#define MCU_UART_TX_PIN      UART_TX_PB1
#define MCU_UART_RX_PIN      UART_RX_PB7
#define MCU_UART_BAUDRATE    115200

/* DMA receive buffer used by drv_uart. The first 4 bytes hold the received
 * length (little-endian), the payload follows at +4, so the usable payload
 * capacity is (size - 4). Must be 4-byte aligned and a multiple of 16 (the
 * DMA size register counts 16 byte units).
 *
 * The DMA hands a chunk over once the line has been idle for a few byte
 * times, so frames the MCU sends back to back arrive as one chunk, and a
 * chunk larger than the buffer is lost whole. The own ZDMS16-2 MCU answers a
 * query-all with a 234 byte dump and sends its version (10 B) or identity
 * (16 B) answer right behind it: 244 and 250 bytes, against 252 usable with
 * 256. One more frame in the same burst (a wall switch report) or one more DP
 * per channel then dropped the whole burst. 512 (508 usable) leaves room for
 * both. End devices keep 256: deep retention bounds their RAM.
 * tests/test_uart_hal.py reads the router value from this file. */
#ifdef END_DEVICE
#define UART_RX_DMA_BUF_SIZE    256
#else
#define UART_RX_DMA_BUF_SIZE    512
#endif
static uint8_t g_uart_rx_dma_buf[UART_RX_DMA_BUF_SIZE] __attribute__((aligned(4)));

/* Software ring buffer holding bytes drained from the DMA buffer in the ISR:
 * at least one full chunk. The size must divide 65536 (free-running uint16_t
 * indices). */
#define UART_RX_RING_SIZE    UART_RX_DMA_BUF_SIZE
static uint8_t           g_uart_rx_ring[UART_RX_RING_SIZE];
static volatile uint16_t g_uart_rx_head = 0;
static volatile uint16_t g_uart_rx_tail = 0;

static volatile uint16_t g_uart_rx_dropped = 0;

uint16_t hal_uart_rx_dropped(void) {
    return g_uart_rx_dropped;
}

static uint16_t uart_ring_used(void) {
    uint16_t head = (uint16_t)g_uart_rx_head;
    uint16_t tail = (uint16_t)g_uart_rx_tail;

    return (uint16_t)(head - tail);
}

/* Interrupts masked: the DMA callback must not push half way through. */
static void uart_ring_clear(void) {
    unsigned char r = irq_disable();

    g_uart_rx_head = 0;
    g_uart_rx_tail = 0;
    irq_restore(r);
}

static void uart_ring_push(const uint8_t *data, uint16_t len) {
    for (uint16_t i = 0; i < len; i++) {
        /* Drop bytes if the ring is full rather than corrupting it. */
        if (uart_ring_used() >= (UART_RX_RING_SIZE - 1)) {
            g_uart_rx_dropped += (uint16_t)(len - i);
            return;
        }
        uint16_t idx = (uint16_t)(g_uart_rx_head % UART_RX_RING_SIZE);
        g_uart_rx_ring[idx] = data[i];
        g_uart_rx_head++;
    }
}

/* Called from the SDK ISR (drv_uart_rx_irq_handler) when DMA receives data. */
static void uart_dma_rx_callback(void) {
    uint32_t dataLen =
        ((uint32_t)g_uart_rx_dma_buf[0]) |
        ((uint32_t)g_uart_rx_dma_buf[1] << 8) |
        ((uint32_t)g_uart_rx_dma_buf[2] << 16) |
        ((uint32_t)g_uart_rx_dma_buf[3] << 24);

    if (dataLen && dataLen <= (UART_RX_DMA_BUF_SIZE - 4)) {
        uart_ring_push(&g_uart_rx_dma_buf[4], (uint16_t)dataLen);
    } else if (dataLen) {
        g_uart_rx_dropped += (uint16_t)dataLen;
    }
}

static uint32_t g_tx_pin = MCU_UART_TX_PIN;
static uint32_t g_rx_pin = MCU_UART_RX_PIN;
static uint32_t g_baud   = MCU_UART_BAUDRATE;

static uint8_t uart_tx_pin_is_valid(uint32_t p) {
    return p == UART_TX_PA2 || p == UART_TX_PB1 || p == UART_TX_PC2 ||
           p == UART_TX_PD0 || p == UART_TX_PD3 || p == UART_TX_PD7;
}

static uint8_t uart_rx_pin_is_valid(uint32_t p) {
    return p == UART_RX_PA0 || p == UART_RX_PB0 || p == UART_RX_PB7 ||
           p == UART_RX_PC3 || p == UART_RX_PC5 || p == UART_RX_PD6;
}

void hal_uart_init(const hal_uart_config_t *cfg) {
    if (cfg != NULL) {
        if (uart_tx_pin_is_valid(cfg->tx_pin)) {
            g_tx_pin = cfg->tx_pin;
        }
        if (uart_rx_pin_is_valid(cfg->rx_pin)) {
            g_rx_pin = cfg->rx_pin;
        }
        if (cfg->baudrate != 0) {
            g_baud = cfg->baudrate;
        }
    }

    /* Empty the ring before RX DMA (re)starts: its interrupt may push a chunk
     * as soon as drv_uart_init returns, and clearing after that would cut
     * the chunk in half. */
    uart_ring_clear();

    /* drv_uart_init sets up the DMA receive path and registers the ISR
     * callback. It enables both RX and TX DMA; we re-disable TX DMA below so
     * the NDMA TX path in hal_uart_write keeps working. */
    uart_gpio_set(g_tx_pin, g_rx_pin);

    /* Pull the UART lines high and strengthen the TX drive. Without this, a
     * passive tap (e.g. a USB-serial adapter with a pull-down on its input)
     * can clamp the idle level low and block data flowing past the tap point
     * to the MCU/module. 10K pull-ups hold the idle-high level; strong drive
     * keeps the TX line from being pulled down by an adapter's input. */
    gpio_setup_up_down_resistor(g_tx_pin, PM_PIN_PULLUP_10K);
    gpio_setup_up_down_resistor(g_rx_pin, PM_PIN_PULLUP_10K);
    gpio_set_data_strength(g_tx_pin, 1);

    if (drv_uart_init(g_baud, g_uart_rx_dma_buf,
                      UART_RX_DMA_BUF_SIZE, uart_dma_rx_callback) != 0) {
        /* Fall back to plain NDMA init so TX still works even if the DMA RX
         * setup failed. */
        uart_reset();
        uart_gpio_set(g_tx_pin, g_rx_pin);
        uart_init_baudrate(g_baud, CLOCK_SYS_CLOCK_HZ, PARITY_NONE,
                           STOP_BIT_ONE);
    }
    uart_dma_enable(1, 0); /* RX DMA on, TX stays NDMA */

    /* uart_reset() (in drv_uart_init, or above) puts the hardware back on
     * the first of its four TX data registers, but the SDK keeps its own
     * index into them for uart_ndma_send_byte(). After a re-init with the
     * index elsewhere the bytes go to the wrong register and the line stays
     * wedged, so the re-init that is meant to recover TX could not. */
    uart_ndma_clear_tx_index();
}

void hal_uart_deinit(void) {
    uart_reset();
    uart_ring_clear();
}

uint16_t hal_uart_rx_available(void) {
    return uart_ring_used();
}

void hal_uart_flush(void) {
    uart_ring_clear();
    uart_ndma_clear_tx_index();
}

/* The watchdog fires at one second. An unbounded spin here turns any
 * stalled UART -- wrong pinmux, bad clock, a peripheral left in a odd
 * state by a re-init -- into a reboot, and every relay command and
 * datapoint write passes through this path. Give up instead: a dropped
 * frame is recoverable, a reset is not.
 *
 * The limit is one time budget for the whole write, from the system timer:
 * twice the time its bytes need on the line plus 2 ms, at most 200 ms. (It
 * was a spin count per byte, of the order of the 1 s watchdog per byte at
 * 24 MHz, so a slow or stuck line could reach the watchdog.) The caller
 * re-inits the UART after three failed writes in a row. */
#define UART_TX_BUDGET_SLACK_US    2000u
#define UART_TX_BUDGET_MAX_US      200000u

static uint32_t uart_tx_budget_us(uint16_t len) {
    uint32_t baud    = g_baud ? g_baud : MCU_UART_BAUDRATE;
    uint32_t byte_us = 10u * 1000000u / baud + 1u; /* 8N1: 87 us at 115200 */
    uint32_t us      = 2u * byte_us * ((uint32_t)len + 1u) + UART_TX_BUDGET_SLACK_US;

    return us < UART_TX_BUDGET_MAX_US ? us : UART_TX_BUDGET_MAX_US;
}

static uint8_t uart_tx_wait_idle(uint32_t start, uint32_t budget_us) {
    while (uart_tx_is_busy()) {
        if (clock_time_exceed(start, budget_us)) {
            return 0;
        }
    }
    return 1;
}

hal_uart_status_t hal_uart_write(const uint8_t *data, uint16_t len,
                                 uint16_t *written) {
    if (!data && len != 0) {
        return HAL_UART_ERR_INVALID_ARG;
    }

    uint32_t start     = clock_time();
    uint32_t budget_us = uart_tx_budget_us(len);
    uint16_t actual    = 0;
    for (uint16_t i = 0; i < len; i++) {
        // Wait for the previous byte to finish shifting out before writing the
        // next one. NDMA mode cycles through the four TX data registers.
        if (!uart_tx_wait_idle(start, budget_us)) {
            if (written) {
                *written = actual;
            }
            return HAL_UART_ERR_IO;
        }
        uart_ndma_send_byte(data[i]);
        actual++;
    }
    // Wait for the final byte to be fully transmitted before returning, so a
    // subsequent read/command isn't corrupted by an in-flight frame.
    if (!uart_tx_wait_idle(start, budget_us)) {
        if (written) {
            *written = actual;
        }
        return HAL_UART_ERR_IO;
    }

    if (written) {
        *written = actual;
    }
    return HAL_UART_OK;
}

hal_uart_status_t hal_uart_read(uint8_t *data, uint16_t len,
                                uint16_t *read_len) {
    if (!data && len != 0) {
        return HAL_UART_ERR_INVALID_ARG;
    }

    uint16_t actual = 0;
    while (actual < len && uart_ring_used() > 0) {
        uint16_t idx = (uint16_t)(g_uart_rx_tail % UART_RX_RING_SIZE);
        data[actual++] = g_uart_rx_ring[idx];
        g_uart_rx_tail++;
    }

    if (read_len) {
        *read_len = actual;
    }
    return HAL_UART_OK;
}

hal_uart_status_t hal_uart_write_byte(uint8_t byte) {
    uint16_t written = 0;

    return hal_uart_write(&byte, 1, &written);
}

hal_uart_status_t hal_uart_read_byte(uint8_t *byte) {
    if (!byte) {
        return HAL_UART_ERR_INVALID_ARG;
    }
    uint16_t          read_len = 0;
    hal_uart_status_t st       = hal_uart_read(byte, 1, &read_len);
    return read_len ? HAL_UART_OK : st;
}
