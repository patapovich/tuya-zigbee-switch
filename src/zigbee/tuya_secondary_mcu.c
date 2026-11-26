#include "hk_dimmer.h"
#include "zigbee/tuya_secondary_mcu.h"
#include "zigbee/tuya_mcu_diag.h"
#include "zigbee/tuya_mcu_ota.h"
#include "hal/printf_selector.h"
#include "hal/tasks.h"
#include "hal/timer.h"
#include "hal/uart.h"

#include <string.h>
#ifdef HAL_STUB
#include <stdlib.h>
#endif

/* ---- framing -------------------------------------------------------------- */

#define FRAME_HEADER_LEN    8 /* 55 AA 02 seq seq cmd len len */
#define FRAME_VERSION       0x02

static uint8_t           g_enabled           = 0;
static hal_uart_config_t g_uart_cfg          = { 0 };
static uint16_t          g_tx_seq            = 0;
static uint8_t           g_report_is_passive = 0;

static uint16_t next_tx_seq(void) {
    uint16_t seq = g_tx_seq;

    g_tx_seq = (g_tx_seq >= 0xFFF0) ? 0 : (uint16_t)(g_tx_seq + 1);
    return seq;
}

static uint8_t checksum(const uint8_t *buf, uint16_t len) {
    uint8_t sum = 0;

    for (uint16_t i = 0; i < len; i++) {
        sum += buf[i];
    }
    return sum;
}

/* A write the UART will not take means the link is wedged (pinmux reassigned,
 * clock moved, odd state after a re-init). Silence is not evidence of that:
 * the MCU is quiet by design. Repeated write failures are, so they trigger a
 * re-init. */
#define UART_FAILURES_BEFORE_REINIT    3
static uint8_t g_write_failures = 0;

static int uart_send(const uint8_t *buf, uint16_t len) {
#ifdef HK_DIMMER
    if (hk_dimmer_active()) {
        /* The virtual MCU of the ZH-Z06 dimmers takes the frame; nothing goes on the wire. */
        hk_dimmer_from_module(buf, len);
        return 0;
    }
#endif
    if (hal_uart_write(buf, len, NULL) == HAL_UART_OK) {
        g_write_failures = 0;
        return 0;
    }
    if (++g_write_failures >= UART_FAILURES_BEFORE_REINIT) {
        printf("Secondary MCU link stuck, reinitialising UART\r\n");
        tuya_mcu_diag.uart_reinit++;
        tuya_mcu_diag_log(TUYA_MCU_DIAG_UART_REINIT, 0, 0);
        g_write_failures = 0;
        hal_uart_deinit();
        hal_uart_init(&g_uart_cfg);
    }
    return -1;
}

/* ---- burst gate (contract item 6, tuya_mcu_ota.h) --------------------------
 *
 * The UART writes a frame byte by byte as it is built, so frames we write in
 * one pass (a reply and the queued frame it starts, two replies) are back to
 * back on the wire. A factory MCU's USART ring holds 23 bytes: frames less
 * than BURST_GAP_MS apart must not add up to more. A frame that would is held
 * back, in order with what follows it, and goes out BURST_GAP_MS after the
 * last one, well within the 50 ms of phase 1's timing that the factory MCUs
 * are kept to. An answer of the MCU (a DP report, its echo of our command)
 * ends a burst: it has read what it answers, and the next frame (the queued
 * one its answer lets go) starts a new one. Open Dimmer (fast acks: it said
 * who it is) takes any burst.
 *
 * Nothing goes past a held frame: it would overtake it and add to its burst.
 * So while frames are held, the MCU's frames wait in the RX buffer (each
 * could start a reply), and a queued frame waits in the TX queue until the
 * gate has room. That leaves the gate the frames one MCU frame starts (a
 * reply and the queued frame it lets go) and the one queued frame in flight.
 * A frame that still finds the gate full is dropped (TX_DROP), never sent.
 */
#define BURST_GAP_MS    2
#define GATE_FRAMES     3
#define GATE_FULL       (-2)    /* gate_send(): dropped, the gate is full */

static uint8_t    g_fast_acks;
static uint32_t   g_burst_ms;   /* when the last frame was written */
static uint16_t   g_burst_len;  /* bytes written since the last gap */
static uint8_t    g_gate[GATE_FRAMES][TUYA_MCU_FACTORY_FRAME_MAX];
static uint8_t    g_gate_len[GATE_FRAMES];
static uint8_t    g_gate_count;
static hal_task_t g_gate_timer;

static void burst_written(uint16_t len) {
    uint32_t now = hal_millis();

    if (now - g_burst_ms >= BURST_GAP_MS) {
        g_burst_len = 0;
    }
    g_burst_len = (uint16_t)(g_burst_len + len);
    g_burst_ms  = now;
}

static uint8_t burst_too_long(uint16_t len) {
    return !g_fast_acks && g_burst_len > 0 && hal_millis() - g_burst_ms < BURST_GAP_MS &&
           g_burst_len + len > TUYA_MCU_FACTORY_BURST_MAX;
}

static uint8_t gate_has_room(void) {
    return g_gate_count < GATE_FRAMES;
}

static void tx_kick(void);

static int gate_send(const uint8_t *buf, uint16_t len) {
    if (g_gate_count > 0 || burst_too_long(len)) {
        if (!gate_has_room()) {
            return GATE_FULL;
        }
        memcpy(g_gate[g_gate_count], buf, len);
        g_gate_len[g_gate_count++] = (uint8_t)len;
        if (g_gate_count == 1) {
            hal_tasks_schedule(&g_gate_timer, BURST_GAP_MS);
        }
        return 0;
    }
    if (uart_send(buf, len) != 0) {
        return -1;
    }
    burst_written(len);
    return 0;
}

static void gate_fired(void *arg) {
    (void)arg;
    while (g_gate_count > 0) {
        if (burst_too_long(g_gate_len[0])) {
            hal_tasks_schedule(&g_gate_timer, BURST_GAP_MS);
            break;
        }
        if (uart_send(g_gate[0], g_gate_len[0]) == 0) {
            burst_written(g_gate_len[0]);
        } else {
            tuya_mcu_diag.tx_fail++;
        }
        g_gate_count--;
        memmove(g_gate[0], g_gate[1], g_gate_count * sizeof(g_gate[0]));
        memmove(g_gate_len, g_gate_len + 1, g_gate_count);
    }
    tx_kick();                  /* a queued frame that waited for room */
}

/* Builds and sends one frame. For a DP write `data` is the DP value and the
 * DP unit header is added here. Every frame but a block answer (send_long_reply)
 * is built here, so none is longer than a factory MCU takes (contract item 6,
 * tuya_mcu_ota.h): its parser wedges on a frame of 32..38 bytes. */
static int send_frame(uint8_t cmd, uint16_t seq, uint8_t dpid, uint8_t dp_type,
                      const uint8_t *data, uint16_t data_len) {
    uint8_t  buf[TUYA_MCU_FACTORY_FRAME_MAX];
    uint16_t body = (uint16_t)(cmd == TUYA_MCU_CMD_WRITE ? data_len + 4 : data_len);
    uint16_t idx  = 0;

    if (data_len > TUYA_MCU_FACTORY_FRAME_MAX ||
        FRAME_HEADER_LEN + body + 1 > TUYA_MCU_FACTORY_FRAME_MAX ||
        (data_len > 0 && data == NULL)) {
        return -1;
    }
    buf[idx++] = 0x55;
    buf[idx++] = 0xAA;
    buf[idx++] = FRAME_VERSION;
    buf[idx++] = (uint8_t)(seq >> 8);
    buf[idx++] = (uint8_t)seq;
    buf[idx++] = cmd;
    buf[idx++] = (uint8_t)(body >> 8);
    buf[idx++] = (uint8_t)body;
    if (cmd == TUYA_MCU_CMD_WRITE) {
        buf[idx++] = dpid;
        buf[idx++] = dp_type;
        buf[idx++] = (uint8_t)(data_len >> 8);
        buf[idx++] = (uint8_t)data_len;
    }
    if (data_len > 0) {
        memcpy(&buf[idx], data, data_len);
        idx = (uint16_t)(idx + data_len);
    }
    buf[idx] = checksum(buf, idx);
    idx++;

    int     sent = gate_send(buf, idx);
    uint8_t id   = cmd == TUYA_MCU_CMD_WRITE ? dpid : cmd;
    if (sent == GATE_FULL) {
        tuya_mcu_diag.tx_drop++;
        tuya_mcu_diag_log(TUYA_MCU_DIAG_TX_DROP, id, 0);
        return -1;
    }
    if (sent != 0) {
        tuya_mcu_diag.tx_fail++;
        tuya_mcu_diag_log(TUYA_MCU_DIAG_TX_FAIL, id, 0);
        return -1;
    }
    if (cmd == TUYA_MCU_CMD_WRITE) {
        tuya_mcu_diag.tx_dp++;
        tuya_mcu_diag_log(TUYA_MCU_DIAG_TX_DP, id, tuya_mcu_diag_number(data, data_len));
    } else {
        tuya_mcu_diag.tx_cmd++;
        tuya_mcu_diag_log(TUYA_MCU_DIAG_TX_CMD, id, tuya_mcu_diag_number(data, data_len));
    }
    return 0;
}

int tuya_secondary_mcu_send_reply(uint8_t cmd, uint16_t seq,
                                  const uint8_t *payload, uint16_t len) {
    if (!g_enabled) {
        return -1;
    }
    return send_frame(cmd, seq, 0, 0, payload, len);
}

#define LONG_REPLY_HEAD_MAX    16
#define LONG_REPLY_DATA_MAX    64

int tuya_secondary_mcu_send_long_reply(uint8_t cmd, uint16_t seq,
                                       const uint8_t *head, uint16_t head_len,
                                       const uint8_t *data, uint16_t data_len) {
    uint8_t  buf[FRAME_HEADER_LEN + LONG_REPLY_HEAD_MAX + LONG_REPLY_DATA_MAX + 1];
    uint16_t body = (uint16_t)(head_len + data_len);
    uint16_t idx  = 0;

    if (!g_enabled || head_len > LONG_REPLY_HEAD_MAX || data_len > LONG_REPLY_DATA_MAX ||
        (head_len && head == NULL) || (data_len && data == NULL)) {
        return -1;
    }
    buf[idx++] = 0x55;
    buf[idx++] = 0xAA;
    buf[idx++] = FRAME_VERSION;
    buf[idx++] = (uint8_t)(seq >> 8);
    buf[idx++] = (uint8_t)seq;
    buf[idx++] = cmd;
    buf[idx++] = (uint8_t)(body >> 8);
    buf[idx++] = (uint8_t)body;
    if (head_len) {
        memcpy(&buf[idx], head, head_len);
        idx = (uint16_t)(idx + head_len);
    }
    if (data_len) {
        memcpy(&buf[idx], data, data_len);
        idx = (uint16_t)(idx + data_len);
    }
    buf[idx] = checksum(buf, idx);
    idx++;
    if (uart_send(buf, idx) != 0) {
        tuya_mcu_diag.tx_fail++;
        return -1;
    }
    burst_written(idx);         /* a direct reply: never held back, but counted */
    tuya_mcu_diag.tx_cmd++;
    return 0;
}

/* ---- TX queue: one frame in flight --------------------------------------
 *
 * The MCU answers every DP write with a report of that DP. Writing the next
 * frame before that answer arrives made this MCU family unreliable (OpenBeken
 * needed its "TuyaMCU queue" flag for it), so frames we start go out one at a
 * time: the next one when the MCU has answered, or after TX_ACK_TIMEOUT_MS
 * (a brightness write to a lit channel is answered only after the MCU's own
 * fade, ~1 s; the timeout lets the next write through meanwhile).
 */
#define TX_QUEUE_LEN         8
#define TX_ACK_TIMEOUT_MS    150

typedef struct {
    uint8_t cmd;     /* TUYA_MCU_CMD_WRITE or a command we start */
    uint8_t dpid;    /* DP writes only */
    uint8_t dp_type;
    uint8_t len;
    uint8_t value[16];
} tx_item_t;

/* The longest queued frame, a DP write of 16 bytes, is what a factory MCU
 * takes. A negative array size stops the build. */
typedef char tx_item_fits_a_factory_mcu[
        FRAME_HEADER_LEN + 4 + sizeof(((tx_item_t *) 0)->value) + 1 <=
        TUYA_MCU_FACTORY_FRAME_MAX ? 1 : -1];

static tx_item_t  g_txq[TX_QUEUE_LEN];
static uint8_t    g_txq_head  = 0;
static uint8_t    g_txq_count = 0;
static uint8_t    g_tx_busy   = 0;
static tx_item_t  g_tx_in_flight;
static uint32_t   g_tx_sent_ms = 0;
static hal_task_t g_tx_timer;
static tuya_secondary_mcu_tx_callback_t g_tx_callback = NULL;

static uint8_t tx_item_id(const tx_item_t *item) {
    return item->cmd == TUYA_MCU_CMD_WRITE ? item->dpid : item->cmd;
}

static void tx_arm_timer(void) {
    hal_tasks_unschedule(&g_tx_timer);
    hal_tasks_schedule(&g_tx_timer, TX_ACK_TIMEOUT_MS);
}

#define TX_RETRIES    2

static uint8_t g_tx_retries;
static void    (*g_resync)(void);

void tuya_secondary_mcu_set_fast_acks(uint8_t on, void (*resync)(void)) {
    g_fast_acks = on;
    g_resync    = resync;
}

static int tx_send_now(const tx_item_t *item);
static uint8_t tx_value_matched(const tx_item_t *item);
static void tx_write_timed_out(void);

/* ---- answers that do not match the write in flight -------------------------
 *
 * An on/off or enum DP write is answered with the value written (see
 * tx_value_matched). A report of that DP with another value while the write
 * is in flight is, with an MCU that has no fast acks (the stock one), either
 * the late answer to an earlier write of ours, or the MCU's last word on the
 * DP: it kept or refused the value, or the wall switch was used and the
 * answer to our write is lost. The last write that timed out is kept, so its
 * late answer is still known as ours; any other such report is held back
 * while our write may still be answered, and handed on as the DP's state
 * when the write times out (else nothing would show it until the next
 * report, and the stock MCU sends none by itself).
 */
#define TX_LATE_ANSWER_MS    3000 /* the stock MCU's fade takes about 1 s */

typedef struct {
    uint8_t dpid;    /* 0: empty */
    uint8_t dp_type;
    uint8_t len;
    uint8_t value[4];
} tx_dp_note_t;

static tx_dp_note_t g_tx_held;         /* report held back, of the write in flight */
static tx_dp_note_t g_tx_timed_out;    /* our last on/off or enum write that timed out */
static uint32_t     g_tx_timed_out_ms;

static void tx_dp_note_set(tx_dp_note_t *note, uint8_t dpid, uint8_t dp_type,
                           const uint8_t *value, uint16_t len) {
    if (len > sizeof(note->value)) {
        note->dpid = 0;
        return;
    }
    note->dpid    = dpid;
    note->dp_type = dp_type;
    note->len     = (uint8_t)len;
    memcpy(note->value, value, len);
}

/* Ack timeout of the frame in flight, or the retry after a failed write. */
static void tx_timer_fired(void *arg) {
    (void)arg;
    if (g_tx_busy) {
#ifdef TUYA_MCU_SLOW_VALUE_ANSWERS
        /* A dimmer's factory MCU answers a brightness write to a lit channel only after its
         * fade (~1 s, TX_LATE_ANSWER_MS): no answer in time is its way, not a lost frame, so
         * neither counted nor logged (it would fill the log at every wall switch on). */
        if (g_fast_acks || g_tx_in_flight.cmd != TUYA_MCU_CMD_WRITE ||
            g_tx_in_flight.dp_type != TUYA_DP_TYPE_VALUE)
#endif
        {
            tuya_mcu_diag.tx_timeout++;
            tuya_mcu_diag_log(TUYA_MCU_DIAG_ACK_TIMEOUT, tx_item_id(&g_tx_in_flight), 0);
        }
        g_tx_busy = 0;
        if (g_fast_acks && g_tx_in_flight.cmd == TUYA_MCU_CMD_WRITE) {
            /* Our MCU answers at once: no answer means the frame was lost
             * (e.g. it arrived while the MCU was writing its flash). */
            if (g_tx_retries < TX_RETRIES) {
                tx_item_t again = g_tx_in_flight;
                g_tx_retries++;
                if (tx_send_now(&again) == 0) {
                    return;
                }
            } else if (g_resync != NULL) {
                g_tx_retries = 0;
                g_resync();          /* state may differ now: read it all */
            }
        } else if (g_tx_in_flight.cmd == TUYA_MCU_CMD_WRITE &&
                   tx_value_matched(&g_tx_in_flight)) {
            tx_write_timed_out();
        }
    }
    tx_kick();
}

static int tx_send_now(const tx_item_t *item) {
    uint16_t seq = next_tx_seq();

    if (send_frame(item->cmd, seq, item->dpid, item->dp_type, item->value, item->len) != 0) {
        return -1;
    }
    g_tx_held.dpid = 0;              /* held reports belong to the frame in flight */
    g_tx_in_flight = *item;
    g_tx_busy      = 1;
    g_tx_sent_ms   = hal_millis();
    tx_arm_timer();
    if (item->cmd == TUYA_MCU_CMD_WRITE && g_tx_callback != NULL) {
        g_tx_callback(item->dpid);
    }
    return 0;
}

static void tx_kick(void) {
    /* With the burst gate full the next frame waits here: gate_fired() kicks
     * again once it has room. */
    while (!g_tx_busy && g_txq_count > 0 && gate_has_room()) {
        tx_item_t item = g_txq[g_txq_head];
        g_txq_head = (uint8_t)((g_txq_head + 1) % TX_QUEUE_LEN);
        g_txq_count--;
        g_tx_retries = 0;            /* a new frame, not a retry */
        if (tx_send_now(&item) != 0) {
            /* The UART would not take it (the write already spun up to its
             * limit): retry the rest later, not again in this pass. */
            tx_arm_timer();
            break;
        }
    }
}

#ifdef HK_DIMMER
uint8_t tuya_secondary_mcu_tx_pending(void) {
    return g_tx_busy || g_txq_count > 0;
}

#endif

/* What the DP unit being dispatched is to us (see the getters in the header). */
static uint8_t g_report_acks_write = 0;
static uint8_t g_report_superseded = 0;

/* On/off and enum DPs are answered with the value written, so a report of the
 * same DP with another value answers something else: an older write whose
 * answer came late (after its timeout, or while a newer write was queued), or
 * the wall switch. A brightness write is answered after the MCU's fade, with
 * wherever the fade ended: the DP alone matches it. */
static uint8_t tx_value_matched(const tx_item_t *item) {
    return item->dp_type == TUYA_DP_TYPE_BOOL || item->dp_type == TUYA_DP_TYPE_ENUM;
}

static uint8_t tx_values_equal(uint8_t dp_type, const uint8_t *written, uint16_t written_len,
                               const uint8_t *value, uint16_t len) {
    uint32_t reported = tuya_mcu_diag_number(value, len);
    uint32_t wrote    = tuya_mcu_diag_number(written, written_len);

    if (dp_type == TUYA_DP_TYPE_BOOL) {
        return (reported != 0) == (wrote != 0);
    }
    return reported == wrote;
}

static uint8_t tx_same_value(const tx_item_t *item, const uint8_t *value, uint16_t len) {
    return tx_values_equal(item->dp_type, item->value, item->len, value, len);
}

/* The late answer to our last on/off or enum write that timed out: our own
 * change coming back (taken once). */
static uint8_t tx_late_answer(uint8_t dpid, const uint8_t *value, uint16_t len) {
    if (g_tx_timed_out.dpid == 0 || g_tx_timed_out.dpid != dpid) {
        return 0;
    }
    if (hal_millis() - g_tx_timed_out_ms > TX_LATE_ANSWER_MS) {
        g_tx_timed_out.dpid = 0;
        return 0;
    }
    if (!tx_values_equal(g_tx_timed_out.dp_type, g_tx_timed_out.value, g_tx_timed_out.len,
                         value, len)) {
        return 0;
    }
    g_tx_timed_out.dpid = 0;
    return 1;
}

/* Our newest write of `dpid` not answered yet: the queued one (newer; the
 * queue holds at most one per DP), else the one in flight. */
static const tx_item_t *tx_pending_write(uint8_t dpid) {
    for (uint8_t i = 0; i < g_txq_count; i++) {
        const tx_item_t *queued = &g_txq[(g_txq_head + i) % TX_QUEUE_LEN];
        if (queued->cmd == TUYA_MCU_CMD_WRITE && queued->dpid == dpid) {
            return queued;
        }
    }
    if (g_tx_busy && g_tx_in_flight.cmd == TUYA_MCU_CMD_WRITE && g_tx_in_flight.dpid == dpid) {
        return &g_tx_in_flight;
    }
    return NULL;
}

/* A reported on/off or enum value that our newest write of that DP changes
 * again: outdated already, whatever it answers. */
static uint8_t tx_report_superseded(uint8_t dpid, const uint8_t *value, uint16_t len) {
    const tx_item_t *pending = tx_pending_write(dpid);

    return pending != NULL && tx_value_matched(pending) && !tx_same_value(pending, value, len);
}

/* Every reported DP: a report of the DP in flight (on/off and enum DPs: with
 * the value written) is the MCU's answer to it; for a command we started
 * (query-all) the first report starts the answer. Returns 1 when the report
 * answers a DP write of ours: the one in flight, or late the last one that
 * timed out. */
static uint8_t tx_note_report(uint8_t dpid, uint8_t dp_type, const uint8_t *value,
                              uint16_t len) {
    uint8_t write = g_tx_in_flight.cmd == TUYA_MCU_CMD_WRITE;

    if (g_tx_busy && write && dpid == g_tx_in_flight.dpid &&
        tx_value_matched(&g_tx_in_flight) && !tx_same_value(&g_tx_in_flight, value, len)) {
        if (tx_late_answer(dpid, value, len)) {
            return 1;
        }
        if (!g_fast_acks) {
            tx_dp_note_set(&g_tx_held, dpid, dp_type, value, len);
        }
        return 0;
    }
    if (!g_tx_busy || (write && dpid != g_tx_in_flight.dpid)) {
        return tx_late_answer(dpid, value, len);
    }
    uint32_t latency = hal_millis() - g_tx_sent_ms;
    if (latency > 0xFFFF) {
        latency = 0xFFFF;
    }
    tuya_mcu_diag.tx_ack++;
    tuya_mcu_diag.ack_latency_last_ms = (uint16_t)latency;
    if (latency > tuya_mcu_diag.ack_latency_max_ms) {
        tuya_mcu_diag.ack_latency_max_ms = (uint16_t)latency;
    }
    tuya_mcu_diag_log(TUYA_MCU_DIAG_ACK, tx_item_id(&g_tx_in_flight), latency);
    g_tx_busy = 0;
    hal_tasks_unschedule(&g_tx_timer);
    return write ? 1 : tx_late_answer(dpid, value, len);
}

/* A command we started that the MCU answers with the same command (e.g. the
 * firmware version query). */
static void tx_note_command_answer(uint8_t cmd) {
    if (!g_tx_busy || g_tx_in_flight.cmd == TUYA_MCU_CMD_WRITE || g_tx_in_flight.cmd != cmd) {
        return;
    }
    g_burst_len = 0;
    g_tx_busy   = 0;
    hal_tasks_unschedule(&g_tx_timer);
}

static int tx_enqueue(uint8_t cmd, uint8_t dpid, uint8_t dp_type,
                      const void *value, uint16_t value_len) {
    tx_item_t item;

    if (!g_enabled || value_len > sizeof(item.value) || (value_len > 0 && value == NULL)) {
        return -1;
    }
    item.cmd     = cmd;
    item.dpid    = dpid;
    item.dp_type = dp_type;
    item.len     = (uint8_t)value_len;
    if (value_len > 0) {
        memcpy(item.value, value, value_len);
    }

    /* A newer write of something still waiting replaces it and moves to the
     * tail, so the MCU sees the latest writes in the order they were made
     * (off, on at 80, off must not become: off, level 80). */
    for (uint8_t i = 0; i < g_txq_count; i++) {
        tx_item_t *queued = &g_txq[(g_txq_head + i) % TX_QUEUE_LEN];
        if (queued->cmd == cmd && tx_item_id(queued) == tx_item_id(&item)) {
            for (uint8_t j = i; j + 1 < g_txq_count; j++) {
                g_txq[(g_txq_head + j) % TX_QUEUE_LEN] =
                    g_txq[(g_txq_head + j + 1) % TX_QUEUE_LEN];
            }
            g_txq_count--;
            tuya_mcu_diag.tx_coalesced++;
            break;
        }
    }

    if (!g_tx_busy && g_txq_count == 0 && gate_has_room()) {
        return tx_send_now(&item);
    }
    if (g_txq_count >= TX_QUEUE_LEN) {
        printf("Secondary MCU TX queue full, dropping %d\r\n", tx_item_id(&item));
        tuya_mcu_diag.tx_drop++;
        tuya_mcu_diag_log(TUYA_MCU_DIAG_TX_DROP, tx_item_id(&item), 0);
        return -1;
    }
    g_txq[(g_txq_head + g_txq_count) % TX_QUEUE_LEN] = item;
    g_txq_count++;
    return 0;
}

int tuya_secondary_mcu_write_dp(uint8_t dpid, uint8_t dp_type,
                                const void *value, uint16_t value_len) {
    return tx_enqueue(TUYA_MCU_CMD_WRITE, dpid, dp_type, value, value_len);
}

int tuya_secondary_mcu_queue_cmd(uint8_t cmd, const uint8_t *payload, uint16_t len) {
    return tx_enqueue(cmd, 0, 0, payload, len);
}

void tuya_secondary_mcu_set_tx_callback(tuya_secondary_mcu_tx_callback_t callback) {
    g_tx_callback = callback;
}

/* ---- RX ------------------------------------------------------------------- */

/* Today's users: the dimmers, dp attributes, DP relays, Open Dimmer's health report. */
#define MAX_DP_LISTENERS    6
static tuya_secondary_mcu_dp_listener_t g_listeners[MAX_DP_LISTENERS];
static uint8_t g_listener_cnt = 0;
static tuya_secondary_mcu_command_callback_t g_command_callback = NULL;

int tuya_secondary_mcu_add_dp_listener(tuya_secondary_mcu_dp_listener_t listener) {
    for (uint8_t i = 0; i < g_listener_cnt; i++) {
        if (g_listeners[i] == listener) {
            return 0;
        }
    }
    if (listener == NULL || g_listener_cnt >= MAX_DP_LISTENERS) {
        /* A listener left out would miss its DPs quietly: say so, and stop the stub. */
        printf("DP listener table full (%d): one left out\r\n", MAX_DP_LISTENERS);
#ifdef HAL_STUB
        abort();
#endif
        return -1;
    }
    g_listeners[g_listener_cnt++] = listener;
    return 0;
}

void tuya_secondary_mcu_set_command_callback(tuya_secondary_mcu_command_callback_t callback) {
    g_command_callback = callback;
}

uint8_t tuya_secondary_mcu_report_is_passive(void) {
    return g_report_is_passive;
}

uint8_t tuya_secondary_mcu_report_acks_write(void) {
    return g_report_acks_write;
}

uint8_t tuya_secondary_mcu_report_is_superseded(void) {
    return g_report_superseded;
}

/* Frame assembly: one frame and what follows it. The own ZDMS16-2 MCU's
 * status dump is 234 bytes in five frames, the largest 61 bytes; what does
 * not fit waits in the HAL's RX ring for the next pass. */
#define RX_CAPACITY            256

/* Silence after which an incomplete frame is given up (lost DMA chunk, ring
 * overflow, line glitch). Bytes of one frame arrive back to back, ~1 ms apart
 * even at 9600 baud. */
#define RX_FRAME_TIMEOUT_MS    50

static uint8_t  g_rx[RX_CAPACITY];
static uint16_t g_rx_len     = 0;
static uint32_t g_rx_last_ms = 0;

static void rx_drop(uint16_t n) {
    if (n > g_rx_len) {
        n = g_rx_len;
    }
    memmove(g_rx, g_rx + n, g_rx_len - n);
    g_rx_len -= n;
}

/* Waiting for the rest of the frame at the head: if nothing came for a while,
 * drop its sync bytes so it cannot swallow the next frame. */
static uint8_t rx_drop_if_stale(void) {
    if (g_rx_len == 0 || (hal_millis() - g_rx_last_ms) < RX_FRAME_TIMEOUT_MS) {
        return 0;
    }
    tuya_mcu_diag.rx_bad++;
    tuya_mcu_diag_log(TUYA_MCU_DIAG_RX_BAD, g_rx_len > 5 ? g_rx[5] : 0, 0);
    rx_drop(2);
    return 1;
}

static uint16_t g_reports = 0;

uint16_t tuya_secondary_mcu_reports(void) {
    return g_reports;
}

static void rx_dispatch_report(uint8_t cmd, uint16_t frame_len) {
    uint16_t off = FRAME_HEADER_LEN;
    uint16_t end = (uint16_t)(frame_len - 1);

    if (off + 4 <= end) {
        g_reports++;
    }
    g_burst_len = 0;            /* an answer: see the burst gate */

    /* One report may carry several DPs back to back. */
    g_report_is_passive = cmd == TUYA_MCU_CMD_REPORT_PASSIVE;
    while (off + 4 <= end) {
        uint8_t  dpid  = g_rx[off];
        uint8_t  dtype = g_rx[off + 1];
        uint16_t vlen  = (uint16_t)((g_rx[off + 2] << 8) | g_rx[off + 3]);
        if (off + 4 + vlen > end) {
            break; /* truncated unit */
        }
        const uint8_t *value = &g_rx[off + 4];
        tuya_mcu_diag.rx_dps++;
        tuya_mcu_diag_log(g_report_is_passive ? TUYA_MCU_DIAG_RX_DP_PASSIVE : TUYA_MCU_DIAG_RX_DP,
                          dpid, tuya_mcu_diag_number(value, vlen));
        g_report_acks_write = tx_note_report(dpid, dtype, value, vlen);
        g_report_superseded = tx_report_superseded(dpid, value, vlen);
        for (uint8_t i = 0; i < g_listener_cnt; i++) {
            g_listeners[i](dpid, dtype, value, vlen);
        }
        off = (uint16_t)(off + 4 + vlen);
    }
    g_report_is_passive = 0;
    g_report_acks_write = 0;
    g_report_superseded = 0;
}

/* The on/off or enum write in flight got no answer in time: keep it, so its
 * late answer is still taken as ours, and hand on the report of that DP held
 * back meanwhile as its state, unless a newer write of ours to it waits in
 * the queue. Handed on as passive: never a key press. */
static void tx_write_timed_out(void) {
    tx_dp_note_t held = g_tx_held;

    g_tx_held.dpid = 0;
    tx_dp_note_set(&g_tx_timed_out, g_tx_in_flight.dpid, g_tx_in_flight.dp_type,
                   g_tx_in_flight.value, g_tx_in_flight.len);
    g_tx_timed_out_ms = hal_millis();
    if (held.dpid == 0 || held.dpid != g_tx_in_flight.dpid || tx_pending_write(held.dpid) != NULL) {
        return;
    }
    g_report_is_passive = 1;
    for (uint8_t i = 0; i < g_listener_cnt; i++) {
        g_listeners[i](held.dpid, held.dp_type, held.value, held.len);
    }
    g_report_is_passive = 0;
}

static void rx_process(void) {
    for (;;) {
        /* Frames held in the burst gate go first: the MCU's next frame may
         * start a reply that would overtake them. It waits in the buffer
         * until the gate is empty (BURST_GAP_MS per held frame). */
        if (g_gate_count > 0) {
            return;
        }

        /* Resync on the 55 AA header. */
        while (g_rx_len >= 2 && (g_rx[0] != 0x55 || g_rx[1] != 0xAA)) {
            rx_drop(1);
            tuya_mcu_diag.rx_resync_bytes++;
        }
        if (g_rx_len < FRAME_HEADER_LEN) {
            if (rx_drop_if_stale()) {
                continue;
            }
            return;
        }

        uint16_t dlen = (uint16_t)((g_rx[6] << 8) | g_rx[7]);
        /* 32 bit: in 16 bit, 8 + 0xFFF7 + 1 wraps to 0 and passes the check. */
        uint32_t frame_len32 = FRAME_HEADER_LEN + (uint32_t)dlen + 1u;
        if (frame_len32 > RX_CAPACITY) {
            tuya_mcu_diag.rx_oversize++;
            rx_drop(2);
            continue;
        }
        uint16_t frame_len = (uint16_t)frame_len32;
        if (g_rx_len < frame_len) {
            if (rx_drop_if_stale()) {
                continue;
            }
            return;
        }

        uint8_t cmd = g_rx[5];
        if (g_rx[2] != FRAME_VERSION ||
            checksum(g_rx, (uint16_t)(frame_len - 1)) != g_rx[frame_len - 1]) {
            /* Damaged, or not a frame after all: skip only the sync bytes, a
             * real frame may start inside. */
            tuya_mcu_diag.rx_bad++;
            tuya_mcu_diag_log(TUYA_MCU_DIAG_RX_BAD, cmd, frame_len);
            rx_drop(2);
            continue;
        }
        tuya_mcu_diag.rx_frames++;

        if (cmd == TUYA_MCU_CMD_REPORT || cmd == TUYA_MCU_CMD_REPORT_PASSIVE) {
            rx_dispatch_report(cmd, frame_len);
        } else {
            uint16_t seq = (uint16_t)((g_rx[3] << 8) | g_rx[4]);
            tx_note_command_answer(cmd);

            /* Block requests come in runs of hundreds (a rescue window asks
             * every 500 ms): tuya_mcu_ota.c logs each run once, so they do
             * not push everything else out of the log. */
            if (cmd != TUYA_MCU_CMD_OTA_BLOCK) {
                tuya_mcu_diag_log(TUYA_MCU_DIAG_RX_CMD, cmd,
                                  tuya_mcu_diag_number(&g_rx[FRAME_HEADER_LEN], dlen));
            }
            if (g_command_callback != NULL) {
                g_command_callback(cmd, seq, &g_rx[FRAME_HEADER_LEN], dlen);
            }
        }
        rx_drop(frame_len);

        /* Listeners may have queued writes; an answer may have freed the slot. */
        tx_kick();
    }
}

void tuya_secondary_mcu_poll(void) {
    if (!g_enabled) {
        return;
    }
#ifdef HK_DIMMER
    if (hk_dimmer_active()) {
        hk_dimmer_poll();
        uint16_t n = hk_dimmer_to_module(g_rx + g_rx_len, (uint16_t)(RX_CAPACITY - g_rx_len));
        if (n > 0) {
            g_rx_len     = (uint16_t)(g_rx_len + n);
            g_rx_last_ms = hal_millis();
        }
        rx_process();
        return;
    }
#endif
    while (g_rx_len < RX_CAPACITY && hal_uart_rx_available() > 0) {
        uint16_t read_len = 0;
        if (hal_uart_read(g_rx + g_rx_len, (uint16_t)(RX_CAPACITY - g_rx_len),
                          &read_len) != HAL_UART_OK || read_len == 0) {
            break;
        }
        g_rx_len     = (uint16_t)(g_rx_len + read_len);
        g_rx_last_ms = hal_millis();
    }
    tuya_mcu_diag.rx_hal_dropped = hal_uart_rx_dropped();
    rx_process();
}

#ifdef HK_DIMMER
void tuya_secondary_mcu_uart_reinit(void) {
    printf("Dimming MCU link stuck, reinitialising UART\r\n");
    tuya_mcu_diag.uart_reinit++;
    hal_uart_deinit();
    hal_uart_init(&g_uart_cfg);
}

#endif

void tuya_secondary_mcu_init(const hal_uart_config_t *cfg) {
    if (cfg != NULL) {
        g_uart_cfg = *cfg;
    }
    g_tx_timer.handler = tx_timer_fired;
    g_tx_timer.arg     = NULL;
    hal_tasks_init(&g_tx_timer);
    g_gate_timer.handler = gate_fired;
    g_gate_timer.arg     = NULL;
    hal_tasks_init(&g_gate_timer);
    hal_uart_init(cfg);
    g_enabled = 1;
#ifdef HK_DIMMER
    if (hk_dimmer_active()) {
        hk_dimmer_start();
    }
#endif
}
