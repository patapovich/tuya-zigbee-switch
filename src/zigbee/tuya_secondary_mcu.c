#include "zigbee/tuya_secondary_mcu.h"
#include "hal/uart.h"
#include "hal/printf_selector.h"
#include "hal/tasks.h"
#include "hal/timer.h"
#include "zigbee/consts.h"

#include <stdbool.h>
#include <string.h>

static bool g_tuya_secondary_mcu_enabled = false;

/* A write the peripheral will not accept means the link is wedged: the
   pinmux was reassigned under us, the clock moved, an earlier re-init left
   it odd. Silence is NOT evidence of that -- an event driven MCU is quiet
   by design, and a timer based watchdog would churn the UART for nothing.
   Repeated write failures are, so that is the trigger. */
#define UART_FAILURES_BEFORE_REINIT    3

/* ---- diagnostics ---------------------------------------------------------
 * See the header for the attribute layout. Everything is updated in place, so
 * a ZCL read returns the current values without any read hook. */
typedef struct __attribute__((packed))
{
  uint8_t  len;       /* ZCL octet string length byte */
  uint8_t  format;    /* layout version of what follows */
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
} tuya_mcu_diag_counters_t;

#define DIAG_FORMAT           1
#define DIAG_ENTRY_SIZE       8
#define DIAG_PAGE_ENTRIES     6
#define DIAG_LOG_ENTRIES      (DIAG_PAGE_ENTRIES * TUYA_MCU_DIAG_LOG_PAGES)

typedef struct
{
  uint8_t len;
  uint8_t data[DIAG_PAGE_ENTRIES * DIAG_ENTRY_SIZE];
} tuya_mcu_diag_page_t;

static tuya_mcu_diag_counters_t g_diag;
static tuya_mcu_diag_page_t     g_diag_pages[TUYA_MCU_DIAG_LOG_PAGES];
static uint8_t                  g_diag_log[DIAG_LOG_ENTRIES * DIAG_ENTRY_SIZE];
static uint8_t                  g_diag_log_count = 0;
static uint8_t                  g_diag_control   = 0;

static void diag_clear(void)
{
  memset(&g_diag, 0, sizeof(g_diag));
  g_diag.len    = (uint8_t)(sizeof(g_diag) - 1);
  g_diag.format = DIAG_FORMAT;
  memset(g_diag_pages, 0, sizeof(g_diag_pages));
  g_diag_log_count = 0;
}

static uint32_t diag_be_number(const uint8_t *data, uint16_t len)
{
  uint32_t value = 0;
  for (uint16_t i = 0; i < len && i < 4; i++)
  {
    value = (value << 8) | data[i];
  }
  return value;
}

static void diag_log(uint8_t kind, uint8_t id, uint32_t value)
{
  if (g_diag.len == 0) { diag_clear(); }

  uint32_t now = hal_millis();
  g_diag.last_event_ms = now;
  g_diag.log_total++;

  if (g_diag_log_count == DIAG_LOG_ENTRIES)
  {
    memmove(g_diag_log, g_diag_log + DIAG_ENTRY_SIZE,
            (DIAG_LOG_ENTRIES - 1) * DIAG_ENTRY_SIZE);
    g_diag_log_count--;
  }
  uint8_t *e = &g_diag_log[g_diag_log_count * DIAG_ENTRY_SIZE];
  e[0] = (uint8_t)now;
  e[1] = (uint8_t)(now >> 8);
  e[2] = kind;
  e[3] = id;
  e[4] = (uint8_t)value;
  e[5] = (uint8_t)(value >> 8);
  e[6] = (uint8_t)(value >> 16);
  e[7] = (uint8_t)(value >> 24);
  g_diag_log_count++;

  /* Rebuild the pages: oldest entry first, page 0 first. */
  uint16_t total = (uint16_t)(g_diag_log_count * DIAG_ENTRY_SIZE);
  for (uint8_t k = 0; k < TUYA_MCU_DIAG_LOG_PAGES; k++)
  {
    uint16_t off = (uint16_t)(k * sizeof(g_diag_pages[k].data));
    uint16_t n   = total > off ? (uint16_t)(total - off) : 0;
    if (n > sizeof(g_diag_pages[k].data)) { n = sizeof(g_diag_pages[k].data); }
    g_diag_pages[k].len = (uint8_t)n;
    if (n > 0) { memcpy(g_diag_pages[k].data, g_diag_log + off, n); }
  }
}

uint8_t tuya_secondary_mcu_diag_register(hal_zigbee_attribute *table,
                                         uint8_t start_index)
{
  uint8_t i = start_index;

  if (g_diag.len == 0) { diag_clear(); }

  table[i].attribute_id = ZCL_ATTR_BASIC_MCU_DIAG_COUNTERS;
  table[i].data_type_id = ZCL_DATA_TYPE_OCTET_STR;
  table[i].flag         = ATTR_READONLY;
  table[i].size         = (uint8_t)sizeof(g_diag);
  table[i].value        = (uint8_t *)&g_diag;
  i++;
  for (uint8_t k = 0; k < TUYA_MCU_DIAG_LOG_PAGES; k++)
  {
    table[i].attribute_id = (uint16_t)(ZCL_ATTR_BASIC_MCU_DIAG_LOG_FIRST + k);
    table[i].data_type_id = ZCL_DATA_TYPE_OCTET_STR;
    table[i].flag         = ATTR_READONLY;
    table[i].size         = (uint8_t)sizeof(g_diag_pages[k]);
    table[i].value        = (uint8_t *)&g_diag_pages[k];
    i++;
  }
  table[i].attribute_id = ZCL_ATTR_BASIC_MCU_DIAG_CONTROL;
  table[i].data_type_id = ZCL_DATA_TYPE_UINT8;
  table[i].flag         = ATTR_WRITABLE;
  table[i].size         = (uint8_t)sizeof(g_diag_control);
  table[i].value        = &g_diag_control;
  i++;
  return (uint8_t)(i - start_index);
}

void tuya_secondary_mcu_diag_on_write(uint16_t attribute_id)
{
  if (attribute_id != ZCL_ATTR_BASIC_MCU_DIAG_CONTROL) { return; }
  if (g_diag_control == 1) { diag_clear(); }
  g_diag_control = 0;
}

static hal_uart_config_t g_uart_config = {0};
static uint8_t           uart_write_failures = 0;

static void tuya_uart_note_write(int ok)
{
  if (ok)
  {
    uart_write_failures = 0;
    return;
  }
  if (++uart_write_failures < UART_FAILURES_BEFORE_REINIT) { return; }
  printf("Secondary MCU link stuck, reinitialising UART\r\n");
  g_diag.uart_reinit++;
  diag_log(TUYA_MCU_DIAG_UART_REINIT, 0, 0);
  uart_write_failures = 0;
  hal_uart_deinit();
  hal_uart_init(&g_uart_config);
}

// Outgoing sequence number, cycling 0..0xfff0 per the Tuya protocol. The
// original firmware hardcoded 0x0100 for module->MCU frames, but the protocol
// expects a proper incrementing sequence on both directions.
static uint16_t g_tx_seq = 0;
/* Set while a 0x05 report is being dispatched, see the header. */
static uint8_t g_report_is_passive = 0;

uint16_t tuya_secondary_mcu_next_tx_seq(void)
{
  uint16_t seq = g_tx_seq;
  g_tx_seq = (g_tx_seq >= 0xFFF0) ? 0 : (uint16_t)(g_tx_seq + 1);
  return seq;
}

uint8_t tuya_secondary_mcu_report_is_passive(void)
{
  return g_report_is_passive;
}

static uint8_t tuya_checksum(const uint8_t *buf, uint16_t len)
{
  // Checksum is the plain sum of all preceding bytes mod 256
  uint8_t sum = 0;

  for (uint16_t i = 0; i < len; i++)
  {
    sum += buf[i];
  }
  return sum;
}

int tuya_secondary_mcu_encode_frame(const tuya_secondary_mcu_frame_t *frame,
                                    uint8_t *out, uint16_t out_len,
                                    uint16_t *written)
{
  if (!frame || !out || out_len < 12)
  {
    return -1;
  }

  uint16_t data_len = 1 + 1 + 2 + frame->value_len;
  uint8_t frame_buf[64];
  uint16_t idx = 0;

  frame_buf[idx++] = 0x55;
  frame_buf[idx++] = 0xAA;
  frame_buf[idx++] = 0x02;
  frame_buf[idx++] = (uint8_t)(frame->seq >> 8);
  frame_buf[idx++] = (uint8_t)frame->seq;

  if (out_len < 11 + frame->value_len)
  {
    return -1;
  }

  frame_buf[idx++] = (uint8_t)frame->cmd;
  frame_buf[idx++] = (uint8_t)(data_len >> 8);
  frame_buf[idx++] = (uint8_t)(data_len & 0xFF);
  frame_buf[idx++] = frame->dpid;
  frame_buf[idx++] = frame->dp_type;
  frame_buf[idx++] = (uint8_t)((frame->value_len >> 8) & 0xFF);
  frame_buf[idx++] = (uint8_t)(frame->value_len & 0xFF);

  for (uint16_t i = 0; i < frame->value_len; i++)
  {
    frame_buf[idx++] = frame->value[i];
  }

  // Compute the checksum length before incrementing idx: combining both in
  // one expression is unsequenced (undefined which value idx has when the
  // checksum call is evaluated).
  uint8_t checksum = tuya_checksum(frame_buf, idx);
  frame_buf[idx++] = checksum;

  if (idx > out_len)
  {
    return -1;
  }

  memcpy(out, frame_buf, idx);
  if (written)
  {
    *written = idx;
  }

  return 0;
}

int tuya_secondary_mcu_decode_frame(const uint8_t *raw, uint16_t raw_len,
                                    tuya_secondary_mcu_frame_t *frame)
{
  /* Smallest valid frame is header(8) + checksum(1); command frames such as
     0x20 "query network status" carry no payload at all. The previous
     implementation required 12 bytes and unconditionally parsed a datapoint
     header, so every non-DP command was silently dropped. */
  if (!raw || !frame || raw_len < 9)
  {
    return -1;
  }

  memset(frame, 0, sizeof(*frame));

  if (raw[0] != 0x55 || raw[1] != 0xAA || raw[2] != 0x02)
  {
    return -1;
  }

  /* 55 AA 02 <seq_hi> <seq_lo> <cmd> <dlen_hi> <dlen_lo> <data...> <chk> */
  frame->seq = (uint16_t)((raw[3] << 8) | raw[4]);
  frame->cmd = raw[5];

  uint16_t dlen = (uint16_t)((raw[6] << 8) | raw[7]);
  uint16_t payload_off = 8;

  if (raw_len < (uint16_t)(payload_off + dlen + 1))
  {
    return -1;
  }

  frame->checksum = raw[payload_off + dlen];
  if (frame->checksum != tuya_checksum(raw, (uint16_t)(payload_off + dlen)))
  {
    return -1;
  }

  if (frame->cmd == TUYA_MCU_CMD_REPORT || frame->cmd == TUYA_MCU_CMD_WRITE ||
      frame->cmd == TUYA_MCU_CMD_REPORT_PASSIVE)
  {
    /* Datapoint frame: dpid | type | len(2) | value */
    if (dlen < 4)
    {
      return -1;
    }
    frame->dpid = raw[payload_off];
    frame->dp_type = raw[payload_off + 1];
    frame->value_len = (uint16_t)((raw[payload_off + 2] << 8) | raw[payload_off + 3]);
    if (frame->value_len > sizeof(frame->value) ||
        frame->value_len > (uint16_t)(dlen - 4))
    {
      return -1;
    }
    if (frame->value_len > 0)
    {
      memcpy(frame->value, &raw[payload_off + 4], frame->value_len);
    }
  }
  else
  {
    /* Generic command frame: the payload is raw bytes. */
    frame->dpid = 0;
    frame->dp_type = 0;
    frame->value_len = dlen > sizeof(frame->value) ? sizeof(frame->value) : dlen;
    if (frame->value_len > 0)
    {
      memcpy(frame->value, &raw[payload_off], frame->value_len);
    }
  }

  return 0;
}

int tuya_secondary_mcu_send_dp(uint8_t dpid, uint8_t dp_type,
                               const void *value, uint16_t value_len,
                               uint8_t *out, uint16_t out_len,
                               uint16_t *written)
{
  tuya_secondary_mcu_frame_t frame;

  memset(&frame, 0, sizeof(frame));

  if (value_len > sizeof(frame.value) || (value_len > 0 && value == NULL))
  {
    return -1;
  }

  frame.seq = g_tx_seq;
  /* Tuya MCU sequence: +1 each frame, wrap from 0xFFF0 back to 0x0000 */
  g_tx_seq = (g_tx_seq >= 0xFFF0) ? 0 : (uint16_t)(g_tx_seq + 1);
  frame.cmd = TUYA_MCU_CMD_WRITE;
  frame.dpid = dpid;
  frame.dp_type = dp_type;
  frame.value_len = value_len;
  memcpy(frame.value, value, value_len);

  return tuya_secondary_mcu_encode_frame(&frame, out, out_len, written);
}

bool tuya_secondary_mcu_is_enabled(void)
{
  return g_tuya_secondary_mcu_enabled;
}

void tuya_secondary_mcu_enable(void)
{
  g_tuya_secondary_mcu_enabled = true;
}

void tuya_secondary_mcu_disable(void)
{
  g_tuya_secondary_mcu_enabled = false;
}

int tuya_secondary_mcu_init(const hal_uart_config_t *cfg)
{
  if (cfg != NULL) { g_uart_config = *cfg; }
  hal_uart_init(cfg);
  tuya_secondary_mcu_enable();
  return 0;
}

int tuya_secondary_mcu_send_cmd(uint8_t cmd, uint16_t seq,
                                const uint8_t *payload, uint16_t len)
{
  if (!tuya_secondary_mcu_is_enabled() || len > 32)
  {
    return -1;
  }

  uint8_t buf[48];
  uint16_t idx = 0;
  buf[idx++] = 0x55;
  buf[idx++] = 0xAA;
  buf[idx++] = 0x02;
  buf[idx++] = (uint8_t)(seq >> 8);
  buf[idx++] = (uint8_t)seq;
  buf[idx++] = cmd;
  buf[idx++] = (uint8_t)(len >> 8);
  buf[idx++] = (uint8_t)len;
  for (uint16_t i = 0; i < len; i++)
  {
    buf[idx++] = payload[i];
  }
  uint8_t chk = tuya_checksum(buf, idx);
  buf[idx++] = chk;

  int ok = (hal_uart_write(buf, idx, NULL) == HAL_UART_OK);
  tuya_uart_note_write(ok);
  if (ok)
  {
    g_diag.tx_cmd++;
    diag_log(TUYA_MCU_DIAG_TX_CMD, cmd, diag_be_number(payload, len));
  }
  else
  {
    g_diag.tx_fail++;
    diag_log(TUYA_MCU_DIAG_TX_FAIL, cmd, 0);
  }
  return ok ? 0 : -1;
}

/* ---- TX queue ------------------------------------------------------------
 *
 * The MCU answers every DP write with a report of that DP. Writing the next
 * frame before that answer arrives is what OpenBeken users saw as unstable
 * dimming on this MCU family (it needed its "TuyaMCU queue" flag), so DP
 * writes go out one at a time: the next frame is sent when the MCU reports
 * the DP in flight, or after TUYA_TX_ACK_TIMEOUT_MS if it never does.
 *
 * A write for a DP that is already waiting in the queue replaces the queued
 * value instead of adding a second entry, so a fast ramp cannot build a
 * backlog of stale levels.
 */
#define TUYA_TX_QUEUE_LEN          8
#define TUYA_TX_ACK_TIMEOUT_MS     150

typedef struct
{
  uint8_t  dpid;
  uint8_t  dp_type;
  uint8_t  len;
  uint8_t  value[16];
} tuya_tx_item_t;

static tuya_tx_item_t g_txq[TUYA_TX_QUEUE_LEN];
static uint8_t        g_txq_head   = 0;
static uint8_t        g_txq_count  = 0;
static uint8_t        g_tx_in_flight_dpid = 0; /* 0 = nothing in flight */
static hal_task_t     g_tx_timeout_task;
static uint8_t        g_tx_task_ready = 0;

static void tuya_tx_kick(void);

static uint32_t       g_tx_sent_ms = 0;

static void tuya_tx_timeout(void *arg)
{
  (void)arg;
  g_diag.tx_timeout++;
  diag_log(TUYA_MCU_DIAG_ACK_TIMEOUT, g_tx_in_flight_dpid, 0);
  g_tx_in_flight_dpid = 0;
  tuya_tx_kick();
}

static void tuya_tx_task_init(void)
{
  if (g_tx_task_ready) { return; }
  g_tx_timeout_task.handler = tuya_tx_timeout;
  g_tx_timeout_task.arg     = NULL;
  hal_tasks_init(&g_tx_timeout_task);
  g_tx_task_ready = 1;
}

static int tuya_tx_send_now(uint8_t dpid, uint8_t dp_type,
                            const void *value, uint16_t value_len)
{
  uint8_t buffer[64];
  uint16_t written = 0;
  int status = tuya_secondary_mcu_send_dp(dpid, dp_type, value, value_len,
                                          buffer, sizeof(buffer), &written);
  if (status != 0)
  {
    return status;
  }
  int ok = (hal_uart_write(buffer, written, NULL) == HAL_UART_OK);
  tuya_uart_note_write(ok);
  if (!ok)
  {
    g_diag.tx_fail++;
    diag_log(TUYA_MCU_DIAG_TX_FAIL, dpid, 0);
    return -1;
  }
  g_diag.tx_dp++;
  diag_log(TUYA_MCU_DIAG_TX_DP, dpid,
           diag_be_number((const uint8_t *)value, value_len));
  g_tx_sent_ms = hal_millis();
  tuya_tx_task_init();
  g_tx_in_flight_dpid = dpid;
  hal_tasks_unschedule(&g_tx_timeout_task);
  hal_tasks_schedule(&g_tx_timeout_task, TUYA_TX_ACK_TIMEOUT_MS);
  return 0;
}

static void tuya_tx_kick(void)
{
  while (g_tx_in_flight_dpid == 0 && g_txq_count > 0)
  {
    tuya_tx_item_t item = g_txq[g_txq_head];
    g_txq_head = (uint8_t)((g_txq_head + 1) % TUYA_TX_QUEUE_LEN);
    g_txq_count--;
    /* A failed write leaves nothing in flight; try the next entry. */
    tuya_tx_send_now(item.dpid, item.dp_type, item.value, item.len);
  }
}

/* Called for every DP the MCU reports: a report of the DP in flight is the
 * MCU's answer, so the slot is free again. */
static void tuya_tx_note_report(uint8_t dpid)
{
  if (g_tx_in_flight_dpid != 0 && dpid == g_tx_in_flight_dpid)
  {
    uint32_t latency = hal_millis() - g_tx_sent_ms;
    if (latency > 0xFFFF) { latency = 0xFFFF; }
    g_diag.tx_ack++;
    g_diag.ack_latency_last_ms = (uint16_t)latency;
    if (latency > g_diag.ack_latency_max_ms)
    {
      g_diag.ack_latency_max_ms = (uint16_t)latency;
    }
    diag_log(TUYA_MCU_DIAG_ACK, dpid, latency);
    g_tx_in_flight_dpid = 0;
    hal_tasks_unschedule(&g_tx_timeout_task);
  }
}

int tuya_secondary_mcu_write_dp(uint8_t dpid, uint8_t dp_type,
                                const void *value, uint16_t value_len)
{
  if (!tuya_secondary_mcu_is_enabled())
  {
    return -1;
  }
  if (value_len > sizeof(g_txq[0].value) || (value_len > 0 && value == NULL))
  {
    return -1;
  }

  /* Coalesce with a queued (not yet sent) write of the same DP. */
  for (uint8_t i = 0; i < g_txq_count; i++)
  {
    tuya_tx_item_t *item = &g_txq[(g_txq_head + i) % TUYA_TX_QUEUE_LEN];
    if (item->dpid == dpid)
    {
      item->dp_type = dp_type;
      item->len     = (uint8_t)value_len;
      memcpy(item->value, value, value_len);
      g_diag.tx_coalesced++;
      return 0;
    }
  }

  if (g_tx_in_flight_dpid == 0 && g_txq_count == 0)
  {
    return tuya_tx_send_now(dpid, dp_type, value, value_len);
  }

  if (g_txq_count >= TUYA_TX_QUEUE_LEN)
  {
    printf("Secondary MCU TX queue full, dropping DP %d\r\n", dpid);
    g_diag.tx_drop++;
    diag_log(TUYA_MCU_DIAG_TX_DROP, dpid, 0);
    return -1;
  }
  tuya_tx_item_t *item =
      &g_txq[(g_txq_head + g_txq_count) % TUYA_TX_QUEUE_LEN];
  item->dpid    = dpid;
  item->dp_type = dp_type;
  item->len     = (uint8_t)value_len;
  memcpy(item->value, value, value_len);
  g_txq_count++;
  return 0;
}

static tuya_secondary_mcu_dp_report_callback_t g_dp_report_callback = NULL;
static tuya_secondary_mcu_command_callback_t g_command_callback = NULL;

void tuya_secondary_mcu_register_dp_report_callback(
    tuya_secondary_mcu_dp_report_callback_t callback)
{
  g_dp_report_callback = callback;
}

tuya_secondary_mcu_dp_report_callback_t
tuya_secondary_mcu_get_dp_report_callback(void)
{
  return g_dp_report_callback;
}

void tuya_secondary_mcu_register_command_callback(
    tuya_secondary_mcu_command_callback_t callback)
{
  g_command_callback = callback;
}

// Assembly buffer for reconstructing frames arriving byte-by-byte over UART.
/* A full status dump of a two channel dimmer (13 DPs) is ~100 bytes. */
#define TUYA_RX_ASSEMBLY_CAPACITY 256
static uint8_t g_rx_assembly[TUYA_RX_ASSEMBLY_CAPACITY];
static uint16_t g_rx_assembly_len = 0;

static void tuya_secondary_mcu_process_assembly(void)
{
  for (;;)
  {
    // Resync on the 55 AA magic header, discarding stray bytes.
    while (g_rx_assembly_len >= 2 &&
           (g_rx_assembly[0] != 0x55 || g_rx_assembly[1] != 0xAA))
    {
      memmove(g_rx_assembly, g_rx_assembly + 1, --g_rx_assembly_len);
      g_diag.rx_resync_bytes++;
    }

    // Need the fixed 8-byte header (incl. dlen) to know the full frame size.
    if (g_rx_assembly_len < 8)
    {
      return;
    }

    uint16_t dlen = (uint16_t)((g_rx_assembly[6] << 8) | g_rx_assembly[7]);
    uint16_t frame_len = 8 + dlen + 1; // header + payload + checksum

    if (frame_len > TUYA_RX_ASSEMBLY_CAPACITY)
    {
      // Corrupt/oversized frame: drop the sync bytes and try to resync.
      g_diag.rx_oversize++;
      memmove(g_rx_assembly, g_rx_assembly + 2, g_rx_assembly_len - 2);
      g_rx_assembly_len -= 2;
      continue;
    }

    if (g_rx_assembly_len < frame_len)
    {
      return; // wait for the rest of the frame
    }

    uint8_t cmd = g_rx_assembly[5];
    uint8_t frame_ok =
        g_rx_assembly[2] == 0x02 &&
        tuya_checksum(g_rx_assembly, (uint16_t)(frame_len - 1)) ==
            g_rx_assembly[frame_len - 1];

    if (frame_ok)
    {
      g_diag.rx_frames++;
    }
    else
    {
      g_diag.rx_bad++;
      diag_log(TUYA_MCU_DIAG_RX_BAD, cmd, frame_len);
    }

    if (frame_ok &&
        (cmd == TUYA_MCU_CMD_REPORT || cmd == TUYA_MCU_CMD_REPORT_PASSIVE))
    {
      /* A report may carry several datapoints back to back (the answer to
         query-all carries all of them), so walk the whole payload rather
         than decoding only the first one. */
      g_report_is_passive = (cmd == TUYA_MCU_CMD_REPORT_PASSIVE) ? 1 : 0;
      uint16_t off = 8;
      uint16_t end = (uint16_t)(frame_len - 1);
      while (off + 4 <= end)
      {
        uint8_t  dpid  = g_rx_assembly[off];
        uint8_t  dtype = g_rx_assembly[off + 1];
        uint16_t vlen  = (uint16_t)((g_rx_assembly[off + 2] << 8) |
                                    g_rx_assembly[off + 3]);
        if (off + 4 + vlen > end)
        {
          break; /* truncated unit: drop the rest of the frame */
        }
        g_diag.rx_dps++;
        diag_log(cmd == TUYA_MCU_CMD_REPORT_PASSIVE ? TUYA_MCU_DIAG_RX_DP_PASSIVE
                                                    : TUYA_MCU_DIAG_RX_DP,
                 dpid, diag_be_number(&g_rx_assembly[off + 4], vlen));
        tuya_tx_note_report(dpid);
        if (g_dp_report_callback != NULL)
        {
          g_dp_report_callback(dpid, dtype, &g_rx_assembly[off + 4], vlen);
        }
        off = (uint16_t)(off + 4 + vlen);
      }
      g_report_is_passive = 0;
    }
    else if (frame_ok)
    {
      // Other MCU-originated commands (0x02 idle/network query, 0x03 leave
      // network / rejoin, ...). Let the application decide.
      diag_log(TUYA_MCU_DIAG_RX_CMD, cmd,
               diag_be_number(&g_rx_assembly[8], (uint16_t)(frame_len - 9)));
      tuya_secondary_mcu_frame_t frame;
      if (g_command_callback != NULL &&
          tuya_secondary_mcu_decode_frame(g_rx_assembly, frame_len, &frame) == 0)
      {
        g_command_callback(frame.cmd, frame.seq, frame.value, frame.value_len);
      }
    }

    // Consume this frame regardless of decode success, so a checksum
    // mismatch can't get us stuck resyncing on the same bytes forever.
    memmove(g_rx_assembly, g_rx_assembly + frame_len,
            g_rx_assembly_len - frame_len);
    g_rx_assembly_len -= frame_len;

    /* Callbacks may have queued writes; an ack may have freed the slot. */
    tuya_tx_kick();
  }
}

void tuya_secondary_mcu_poll(void)
{
  if (!tuya_secondary_mcu_is_enabled())
  {
    return;
  }

  while (g_rx_assembly_len < TUYA_RX_ASSEMBLY_CAPACITY &&
         hal_uart_rx_available() > 0)
  {
    uint16_t read_len = 0;
    hal_uart_status_t st =
        hal_uart_read(g_rx_assembly + g_rx_assembly_len,
                      (uint16_t)(TUYA_RX_ASSEMBLY_CAPACITY - g_rx_assembly_len),
                      &read_len);
    if (st != HAL_UART_OK || read_len == 0)
    {
      break;
    }
    g_rx_assembly_len += read_len;
  }

  g_diag.rx_hal_dropped = hal_uart_rx_dropped();
  tuya_secondary_mcu_process_assembly();
}
