#ifndef _TUYA_SECONDARY_MCU_H_
#define _TUYA_SECONDARY_MCU_H_

#include <stdint.h>
#include "hal/uart.h"

/*
 * UART link to a Tuya "secondary MCU" (the chip that drives relays, dimmer
 * triacs or touch keys behind the Zigbee module), Tuya Zigbee module serial
 * protocol:
 *
 *   55 AA 02 <seq:2> <cmd> <len:2> <data:len> <checksum>
 *
 *   - checksum: sum of all preceding bytes, mod 256
 *   - numbers are big-endian
 *   - seq: frames we start count 0..0xFFF0; an answer echoes the seq of the
 *     request it answers
 *   - DP frames (0x04/0x05/0x06) carry one or more DP units:
 *     <dpid> <type> <len:2> <value:len>
 *
 * DP writes and commands we start go out one at a time: the next frame is sent
 * when the MCU has answered the one in flight (a report of that DP, for an
 * on/off or enum DP with the value written; for a command, any report or the
 * same command back) or after a timeout.
 */

typedef enum {
    TUYA_DP_TYPE_RAW   = 0x00,
    TUYA_DP_TYPE_BOOL  = 0x01,
    TUYA_DP_TYPE_VALUE = 0x02, /* 4 byte big-endian */
    TUYA_DP_TYPE_ENUM  = 0x04,
} tuya_dp_type_t;

typedef enum {
    TUYA_MCU_CMD_NETWORK_STATUS = 0x02, /* module -> MCU: network status       */
    TUYA_MCU_CMD_RESET_NETWORK  = 0x03, /* MCU -> module: leave, re-pair       */
    TUYA_MCU_CMD_WRITE          = 0x04, /* module -> MCU: DP write             */
    TUYA_MCU_CMD_REPORT_PASSIVE = 0x05, /* MCU -> module: DP report, answer    */
    TUYA_MCU_CMD_REPORT         = 0x06, /* MCU -> module: DP report            */
    TUYA_MCU_CMD_MCU_VERSION    = 0x0B, /* module -> MCU: firmware version?    */
    TUYA_MCU_CMD_QUERY_NETWORK  = 0x20, /* MCU -> module: network status?      */
    TUYA_MCU_CMD_SYNC_TIME      = 0x24, /* MCU -> module: current time?        */
    TUYA_MCU_CMD_QUERY_GATEWAY  = 0x25, /* MCU -> module: gateway reachable?   */
    TUYA_MCU_CMD_QUERY_ALL_DP   = 0x28, /* module -> MCU: report every DP      */
} tuya_mcu_cmd_t;

/* Data of TUYA_MCU_CMD_RESET_NETWORK asking for a leave + re-pair. */
#define TUYA_MCU_RESET_NETWORK_REJOIN     0x01
/* Values of TUYA_MCU_CMD_NETWORK_STATUS / QUERY_NETWORK / QUERY_GATEWAY. */
#define TUYA_MCU_NETWORK_NOT_CONNECTED    0x00
#define TUYA_MCU_NETWORK_CONNECTED        0x01

/** Start the link on the given UART pins/baud (NULL = platform defaults). */
void tuya_secondary_mcu_init(const hal_uart_config_t *cfg);

/** Drain the UART and dispatch what arrived. Call from the main loop. */
void tuya_secondary_mcu_poll(void);

/** Queue a DP write. A queued write of the same DP is replaced (and moves to
 *  the end of the queue). Returns -1 if the link is down or the queue full. */
int tuya_secondary_mcu_write_dp(uint8_t dpid, uint8_t dp_type,
                                const void *value, uint16_t value_len);

/** Queue a command we start (e.g. TUYA_MCU_CMD_QUERY_ALL_DP). */
int tuya_secondary_mcu_queue_cmd(uint8_t cmd, const uint8_t *payload, uint16_t len);

#ifdef HK_DIMMER

/* 1 while writes to the MCU wait in the queue or for their answer (HK builds: the virtual MCU
 * holds its frames to the dimming MCU meanwhile, so a change the module writes in parts goes
 * out whole). */
uint8_t tuya_secondary_mcu_tx_pending(void);

#endif

/** Send an answer to something the MCU asked, at once, echoing its seq. */
int tuya_secondary_mcu_send_reply(uint8_t cmd, uint16_t seq,
                                  const uint8_t *payload, uint16_t len);

/** The MCU answers every DP write at once (our own firmware): a write that
 *  times out was lost, so it is sent again (twice), then `resync` is called
 *  (the app asks for all DPs). Off for other MCUs: the stock one answers a
 *  level write only after its fade. */
void tuya_secondary_mcu_set_fast_acks(uint8_t on, void (*resync)(void));

/** Reply made of a header and a data part (a firmware block for the MCU's
 *  bootloader): up to 16 + 64 bytes, longer than send_reply takes. */
int tuya_secondary_mcu_send_long_reply(uint8_t cmd, uint16_t seq,
                                       const uint8_t *head, uint16_t head_len,
                                       const uint8_t *data, uint16_t data_len);

/** True while a passive report (0x05) is being dispatched: the MCU answering
 *  something we sent, never a fresh key press. Also while a report held back
 *  during an on/off or enum write is handed on after that write timed out
 *  (see tuya_secondary_mcu_report_is_superseded). */
uint8_t tuya_secondary_mcu_report_is_passive(void);

/** True while a DP is being dispatched that answers our DP write in flight
 *  (same DP; on/off and enum DPs also the same value), or comes late as the
 *  answer to our last on/off or enum write that timed out (within 3 s),
 *  whether the MCU sent it as 0x05 or 0x06 (the stock ZDMS16-2 answers with
 *  0x06): our own change coming back, never a fresh key press. */
uint8_t tuya_secondary_mcu_report_acks_write(void);

/** True while an on/off or enum DP is being dispatched whose value our newest
 *  write of that DP (still queued, or in flight and not answered) changes
 *  again: outdated already, e.g. the late answer to an earlier write. With an
 *  MCU that has no fast acks, such a report of the write in flight that is
 *  not a late answer of ours is handed on again (passive, not superseded)
 *  when that write times out with no newer write of the DP queued: it may be
 *  the MCU's last word (it kept its value, or our answer was lost). */
uint8_t tuya_secondary_mcu_report_is_superseded(void);

/** How many DP reports (0x05 / 0x06 frames with at least one DP) have come
 *  since the start; wraps. */
uint16_t tuya_secondary_mcu_reports(void);

/** Every DP the MCU reports is given to every listener; each one picks the DPs
 *  it owns. Returns -1 when all listener slots are taken (printed; the stub
 *  stops then); callers check it. */
typedef void (*tuya_secondary_mcu_dp_listener_t)(uint8_t dpid, uint8_t dp_type,
                                                 const uint8_t *value,
                                                 uint16_t value_len);
int tuya_secondary_mcu_add_dp_listener(tuya_secondary_mcu_dp_listener_t listener);

/** Frames from the MCU that are not DP reports (network/time queries...). */
typedef void (*tuya_secondary_mcu_command_callback_t)(uint8_t cmd, uint16_t seq,
                                                      const uint8_t *data,
                                                      uint16_t data_len);
void tuya_secondary_mcu_set_command_callback(tuya_secondary_mcu_command_callback_t callback);

/** Called when a DP write actually goes out on the UART (not when queued). */
typedef void (*tuya_secondary_mcu_tx_callback_t)(uint8_t dpid);
void tuya_secondary_mcu_set_tx_callback(tuya_secondary_mcu_tx_callback_t callback);

#ifdef HK_DIMMER

/** The UART set up again as at the start (its pins and rate): the ZH-Z06 dimmers' virtual MCU
 *  (hk_dimmer.c) writes the dimming MCU's frames itself and calls it when they keep failing. */
void tuya_secondary_mcu_uart_reinit(void);

#endif

#endif
