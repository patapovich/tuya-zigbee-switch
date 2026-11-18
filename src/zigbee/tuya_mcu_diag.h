#ifndef _TUYA_MCU_DIAG_H_
#define _TUYA_MCU_DIAG_H_

#include <stdint.h>
#include "hal/zigbee.h"

/*
 * Secondary-MCU link diagnostics, readable over Zigbee: manufacturer specific
 * attributes on the Basic cluster of the first endpoint, registered only on
 * devices that use a secondary MCU (see consts.h for the attribute ids):
 *
 *   counters    octet string, tuya_mcu_diag_counters_t without its length
 *               byte, little-endian; new fields are appended (older
 *               firmware sends fewer), the format byte changes only if
 *               existing ones move
 *   log pages   octet strings of 8 byte entries, oldest first across pages:
 *               t_ms (u16, hal_millis() low bits), kind (u8), id (u8, DP id
 *               or command), value (u32)
 *   control     uint8: 1 clears counters and log, 2 asks the MCU for a full
 *               DP dump
 *
 * Decoder: helper_scripts/decode_mcu_diag.py
 */

typedef enum {
    TUYA_MCU_DIAG_TX_DP         = 1,  /* id = dpid, value = DP value          */
    TUYA_MCU_DIAG_TX_CMD        = 2,  /* id = cmd, value = first data bytes   */
    TUYA_MCU_DIAG_RX_DP         = 3,  /* 0x06 report, id = dpid, value        */
    TUYA_MCU_DIAG_RX_DP_PASSIVE = 4,  /* 0x05 report, id = dpid, value        */
    TUYA_MCU_DIAG_RX_CMD        = 5,  /* id = cmd, value = first data bytes   */
    TUYA_MCU_DIAG_ACK           = 6,  /* id = dpid or cmd, value = ms         */
    TUYA_MCU_DIAG_ACK_TIMEOUT   = 7,  /* id = dpid or cmd                     */
    TUYA_MCU_DIAG_RX_BAD        = 8,  /* id = cmd, value = frame length       */
    TUYA_MCU_DIAG_TX_DROP       = 9,  /* queue full, id = dpid or cmd         */
    TUYA_MCU_DIAG_TX_FAIL       = 10, /* UART write failed, id = dpid or cmd  */
    TUYA_MCU_DIAG_UART_REINIT   = 11,
} tuya_mcu_diag_kind_t;

typedef struct __attribute__((packed)) {
    uint8_t  len;    /* ZCL octet string length byte */
    uint8_t  format; /* layout version of what follows */
    uint32_t last_event_ms;
    uint16_t rx_frames;
    uint16_t rx_dps;
    uint16_t rx_bad;
    uint16_t rx_resync_bytes;
    uint16_t rx_oversize;
    uint16_t rx_hal_dropped;
    uint16_t tx_dp;
    uint16_t tx_cmd;
    uint16_t tx_ack;
    uint16_t tx_timeout;
    uint16_t tx_coalesced;
    uint16_t tx_drop;
    uint16_t tx_fail;
    uint16_t uart_reinit;
    uint16_t ack_latency_last_ms;
    uint16_t ack_latency_max_ms;
    uint16_t log_total;

    /* Update of an own MCU firmware (state, kept by a clear): failed results
     * since the last good one, and the current pause in minutes (0: the
     * image is served). */
    uint16_t ota_failures;
    uint16_t ota_pause_min;
} tuya_mcu_diag_counters_t;

/* Counters, updated in place by the link. */
extern tuya_mcu_diag_counters_t tuya_mcu_diag;

#define TUYA_MCU_DIAG_CONTROL_CLEAR        1
#define TUYA_MCU_DIAG_CONTROL_QUERY_ALL    2

void tuya_mcu_diag_clear(void);

/** Append an event to the log (value: up to the first 4 bytes, big-endian,
 *  as tuya_mcu_diag_number() builds them). */
void tuya_mcu_diag_log(uint8_t kind, uint8_t id, uint32_t value);
uint32_t tuya_mcu_diag_number(const uint8_t *data, uint16_t len);

/** Append the attributes to a Basic cluster table; returns how many. */
uint8_t tuya_mcu_diag_register(hal_zigbee_attribute *table, uint8_t start_index);

/** Basic cluster write hook. Returns the control command written (0 if the
 *  write was another attribute). Clearing is done here; the DP dump is the
 *  caller's job. */
uint8_t tuya_mcu_diag_on_write(uint16_t attribute_id);

#endif
