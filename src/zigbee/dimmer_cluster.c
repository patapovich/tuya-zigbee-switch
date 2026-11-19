#include "zigbee/dimmer_cluster.h"
#include "device_config/nvm_items.h"
#include "hal/nvm.h"
#include "zigbee/cluster_common.h"
#include "zigbee/consts.h"
#include "hal/timer.h"
#include "zigbee/tuya_mcu_app.h"
#include "zigbee/tuya_mcu_ota.h"
#include "zigbee/tuya_secondary_mcu.h"

#ifdef SECONDARY_MCU_IMAGE
static const uint16_t dimmer_gesture_states         = 6;   /* none + 5 gestures */
static const uint8_t  dimmer_gesture_out_of_service = 0;
static const uint8_t  dimmer_gesture_flags          = 0;
#endif

#define DIMMER_MAX_ENDPOINTS    10
static zigbee_dimmer_cluster *dimmer_cluster_by_endpoint[DIMMER_MAX_ENDPOINTS];

static zigbee_dimmer_cluster *dimmer_by_endpoint(uint8_t endpoint) {
    return endpoint < DIMMER_MAX_ENDPOINTS ? dimmer_cluster_by_endpoint[endpoint]
                                           : NULL;
}

/* The MCU's brightness DPs use 10..1000 (Tuya schema of the DMS16 family:
 * bright_value / brightness_min / brightness_max all declare min 10), ZCL
 * levels use 1..254 for an "on" light. */
#define TUYA_BRIGHTNESS_MIN    10
#define TUYA_BRIGHTNESS_MAX    1000
#define ZCL_LEVEL_MIN          1
#define ZCL_LEVEL_MAX          254

static uint8_t dimmer_clamp_level(int level) {
    if (level < ZCL_LEVEL_MIN)
        return ZCL_LEVEL_MIN;

    if (level > ZCL_LEVEL_MAX)
        return ZCL_LEVEL_MAX;

    return (uint8_t)level;
}

/* Absolute mapping, used for the ballast min/max attributes and when level
 * scaling is off: 1..254 <-> 10..1000. */
static uint32_t dimmer_zcl_level_to_tuya_value(uint8_t zcl_level) {
    uint32_t value = ((uint32_t)dimmer_clamp_level(zcl_level) * TUYA_BRIGHTNESS_MAX +
                      ZCL_LEVEL_MAX / 2) / ZCL_LEVEL_MAX;

    return value < TUYA_BRIGHTNESS_MIN ? TUYA_BRIGHTNESS_MIN : value;
}

static uint8_t dimmer_tuya_value_to_zcl_level(uint32_t tuya_value) {
    if (tuya_value > TUYA_BRIGHTNESS_MAX)
        tuya_value = TUYA_BRIGHTNESS_MAX;
    return dimmer_clamp_level((int)((tuya_value * ZCL_LEVEL_MAX +
                                     TUYA_BRIGHTNESS_MAX / 2) / TUYA_BRIGHTNESS_MAX));
}

/* Current level <-> MCU brightness. With scaling on and the MCU's range
 * known, level 1 is the MCU's minimum and 254 its maximum, so the whole
 * slider does something. */
static uint8_t dimmer_scaling_active(const zigbee_dimmer_cluster *cluster) {
    return cluster->scale_levels &&
           cluster->mcu_min >= TUYA_BRIGHTNESS_MIN &&
           cluster->mcu_max <= TUYA_BRIGHTNESS_MAX &&
           cluster->mcu_max > cluster->mcu_min;
}

static uint32_t dimmer_level_to_value(const zigbee_dimmer_cluster *cluster, uint8_t level) {
    if (!dimmer_scaling_active(cluster))
        return dimmer_zcl_level_to_tuya_value(level);

    uint32_t span = (uint32_t)(cluster->mcu_max - cluster->mcu_min);
    uint32_t step = (uint32_t)(dimmer_clamp_level(level) - ZCL_LEVEL_MIN);

    return cluster->mcu_min +
           (step * span + (ZCL_LEVEL_MAX - ZCL_LEVEL_MIN) / 2) / (ZCL_LEVEL_MAX - ZCL_LEVEL_MIN);
}

static uint8_t dimmer_value_to_level(const zigbee_dimmer_cluster *cluster, uint32_t value) {
    if (!dimmer_scaling_active(cluster))
        return dimmer_tuya_value_to_zcl_level(value);

    if (value <= cluster->mcu_min)
        return ZCL_LEVEL_MIN;

    if (value >= cluster->mcu_max)
        return ZCL_LEVEL_MAX;

    uint32_t span = (uint32_t)(cluster->mcu_max - cluster->mcu_min);
    return dimmer_clamp_level(ZCL_LEVEL_MIN +
                              (int)(((value - cluster->mcu_min) * (ZCL_LEVEL_MAX - ZCL_LEVEL_MIN) +
                                     span / 2) / span));
}

static void dimmer_encode_tuya_value(uint32_t value, uint8_t out[4]) {
    out[0] = (uint8_t)(value >> 24);
    out[1] = (uint8_t)(value >> 16);
    out[2] = (uint8_t)(value >> 8);
    out[3] = (uint8_t)value;
}

static uint32_t dimmer_decode_tuya_value(const uint8_t *value, uint16_t value_len) {
    uint32_t result = 0;

    for (uint16_t i = 0; i < value_len && i < 4; i++) {
        result = (result << 8) | value[i];
    }
    return result;
}

static void dimmer_write_tuya_value(uint8_t dpid, uint32_t value) {
    uint8_t bytes[4];

    dimmer_encode_tuya_value(value, bytes);
    tuya_secondary_mcu_write_dp(dpid, TUYA_DP_TYPE_VALUE, bytes, sizeof(bytes));
}

/* ---- local state + notifications ---------------------------------------- */

static void dimmer_notify_onoff(zigbee_dimmer_cluster *cluster) {
    hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_ON_OFF,
                                        ZCL_ATTR_ONOFF);
}

static void dimmer_notify_level(zigbee_dimmer_cluster *cluster) {
    hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_LEVEL_CONTROL,
                                        ZCL_ATTR_LEVEL_CURRENT_LEVEL);
}

static void dimmer_set_current_level(zigbee_dimmer_cluster *cluster, uint8_t level) {
    if (cluster->current_level == level)
        return;

    cluster->current_level = level;
    dimmer_notify_level(cluster);
}

static void dimmer_set_on(zigbee_dimmer_cluster *cluster, uint8_t on) {
    on = on ? 1 : 0;
    if (cluster->on == on)
        return;

    cluster->on = on;
    if (on)
        cluster->pending_level = 0; /* applied by whoever switched it on */
    dimmer_notify_onoff(cluster);
    /* Open Dimmer: any off (ours, the wall switch, the countdown's own end) ends it. */
    if (!on && cluster->countdown) {
        cluster->countdown = 0;
        hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_ON_OFF,
                                            ZCL_ATTR_ONOFF_COUNTDOWN);
    }
}

/* ---- MCU writes ---------------------------------------------------------- */

/* How long reports of the level DP are treated as the MCU's own fade towards
 * a level we wrote. The MCU always fades (both the WiFi and Zigbee DMS16
 * variants); a fade takes about 1 s, 3 s leaves margin. */
#define DIMMER_FADE_HOLDOFF_MS    3000

static void dimmer_start_holdoff(zigbee_dimmer_cluster *cluster) {
    cluster->level_holdoff   = 1;
    cluster->has_held_report = 0;
    hal_tasks_unschedule(&cluster->holdoff_task);
    hal_tasks_schedule(&cluster->holdoff_task, DIMMER_FADE_HOLDOFF_MS);
}

static void dimmer_holdoff_done(void *arg) {
    zigbee_dimmer_cluster *cluster = (zigbee_dimmer_cluster *)arg;

    cluster->level_holdoff = 0;
    if (!cluster->has_held_report)
        return;

    cluster->has_held_report = 0;

    /* The fade has settled somewhere else than where we sent it (e.g. the MCU
     * clamped to its own min/max, or the wall switch was used meanwhile):
     * the MCU is the truth. */
    if (cluster->held_report_value != cluster->last_written_value) {
        cluster->level_value = cluster->held_report_value;
        dimmer_set_current_level(cluster,
                                 dimmer_value_to_level(cluster, cluster->held_report_value));
    }
}

static void dimmer_write_level(zigbee_dimmer_cluster *cluster, uint8_t level) {
    uint32_t value = dimmer_level_to_value(cluster, level);

    dimmer_write_tuya_value(cluster->level_dpid, value);
    cluster->last_written_value = value;
    cluster->level_value        = value;
    dimmer_start_holdoff(cluster);
}

static void dimmer_write_onoff(zigbee_dimmer_cluster *cluster, uint8_t on) {
    uint8_t value = on ? 1 : 0;

    tuya_secondary_mcu_write_dp(cluster->onoff_dpid, TUYA_DP_TYPE_BOOL,
                                &value, sizeof(value));
}

static void dimmer_stop_move(zigbee_dimmer_cluster *cluster) {
    cluster->move_active = 0;
    hal_tasks_unschedule(&cluster->ramp_task);
}

static uint8_t dimmer_own_mcu(const zigbee_dimmer_cluster *cluster);

/* Open Dimmer of at least that build (OPEN_DIMMER_*_BUILD) on this dimmer: one of its two
 * channels (a third dimmer of a config has no DPs of its own there). */
#define OPEN_DIMMER_CHANNELS    2

static uint8_t dimmer_od_from(const zigbee_dimmer_cluster *cluster, uint16_t build) {
    return dimmer_own_mcu(cluster) && cluster->dimmer_idx < OPEN_DIMMER_CHANNELS &&
           tuya_mcu_ota_own_build() >= build;
}

/* ---- transition time -------------------------------------------------------
 *
 * A ZCL transition time (1/10 s, little endian at tt) for the change written next: Open Dimmer
 * from OPEN_DIMMER_TRANSITION_BUILD takes it as the ms its network changes of the next second
 * take (DP 160 / 161, before the on / off / level writes). Any other MCU fades by its own
 * settings, as before, and gets nothing more. 0 and 0xFFFF (none given; Zigbee2MQTT sends 0
 * without a transition), and the On / Off / Toggle commands: the fade settings, so a 0 goes out
 * only while an earlier time could still apply to this change.
 * The write queue keeps one frame in flight and sends a lost one again before the next, so the
 * time always reaches the MCU before its change; a time lost for good leaves that change at the
 * fade settings. */
#define DIMMER_TRANSITION_VALID_MS    1500 /* the MCU's window is 1 s from its receipt */

static void dimmer_send_transition(zigbee_dimmer_cluster *cluster, const uint8_t *tt) {
    if (!dimmer_od_from(cluster, OPEN_DIMMER_TRANSITION_BUILD))
        return;

    uint16_t t   = (uint16_t)(tt[0] | (tt[1] << 8));
    uint32_t now = hal_millis();

    if (t == 0xFFFF)
        t = 0;
    if (t == 0 && (cluster->transition_at == 0 ||
                   now - cluster->transition_at >= DIMMER_TRANSITION_VALID_MS))
        return;

    uint32_t ms = (uint32_t)t * 100u;
    dimmer_write_tuya_value(cluster->own_transition_dpid, ms > 0xFFFFu ? 0xFFFFu : ms);
    cluster->transition_at = t ? (now | 1u) : 0;
}

/* ---- Open Dimmer settings (from OPEN_DIMMER_FEATURES_BUILD) -----------------
 *
 * Its DPs per channel (zdms16-py32 src/dimmer.h): DP 6 / 12 the countdown, and from 162 on,
 * 162 + 2 * k + channel: k 0 wall switch mode, 1 level after a power cut, 2 identify, 3 start
 * boost; all values. Written only to such an MCU: to any other one a write of their
 * attributes sends nothing and sets them back to "none". */
#define DIMMER_OD_INPUT          0
#define DIMMER_OD_POWER_LEVEL    1
#define DIMMER_OD_IDENTIFY       2
#define DIMMER_OD_BOOST          3
#define DIMMER_COUNTDOWN_MAX     86400u /* s */
#define DIMMER_INPUT_MODE_MAX    2      /* 0 normal, 1 detached, 2 off */
#define DIMMER_IDENTIFY_MAX      255    /* s */
#define DIMMER_BOOST_MAX         750    /* ms */

#ifdef SECONDARY_MCU_IMAGE
static uint8_t dimmer_od_dp(const zigbee_dimmer_cluster *cluster, uint8_t k) {
    return (uint8_t)(cluster->own_transition_dpid + 2 + 2 * k);
}

#endif

static uint8_t dimmer_countdown_dp(const zigbee_dimmer_cluster *cluster) {
    return (uint8_t)(6 * (cluster->dimmer_idx + 1));
}

static uint8_t dimmer_od_features(const zigbee_dimmer_cluster *cluster) {
    return dimmer_od_from(cluster, OPEN_DIMMER_FEATURES_BUILD);
}

/* StartUpCurrentLevel from the MCU's value: 0 the last level (0xFF, previous); one below the
 * channel's minimum (by more than its steps of 4) is what a "minimum" wrote; else the level. */
static uint8_t dimmer_startup_level(const zigbee_dimmer_cluster *cluster) {
    uint16_t v = cluster->startup_value;

    if (v == 0)
        return ZCL_START_UP_CURRENT_LEVEL_PREVIOUS;

    if ((uint32_t)v + 4 <= cluster->mcu_min)
        return ZCL_START_UP_CURRENT_LEVEL_MINIMUM;

    return dimmer_value_to_level(cluster, v);
}

static void dimmer_update_startup_level(zigbee_dimmer_cluster *cluster) {
    uint8_t level = dimmer_startup_level(cluster);

    if (level != cluster->startup_level) {
        cluster->startup_level = level;
        hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_LEVEL_CONTROL,
                                            ZCL_ATTR_LEVEL_START_UP_CURRENT_LEVEL);
    }
}

#ifdef SECONDARY_MCU_IMAGE
/* IdentifyTime counts down by the second; the MCU blinks as long by itself. */
static void dimmer_identify_tick(void *arg) {
    zigbee_dimmer_cluster *cluster = (zigbee_dimmer_cluster *)arg;

    if (cluster->identify_time == 0)
        return;

    cluster->identify_time--;
    if (cluster->identify_time)
        hal_tasks_schedule(&cluster->identify_task, 1000);
}

/* ZCL Identify or an IdentifyTime write: the channel blinks that long (at most 255 s, the
 * MCU's limit), 0 stops it. Nothing to any other MCU. */
static void dimmer_identify(zigbee_dimmer_cluster *cluster, uint16_t s) {
    hal_tasks_unschedule(&cluster->identify_task);
    if (!dimmer_od_features(cluster)) {
        cluster->identify_time = 0;
        return;
    }
    cluster->identify_time = s > DIMMER_IDENTIFY_MAX ? DIMMER_IDENTIFY_MAX : s;
    dimmer_write_tuya_value(dimmer_od_dp(cluster, DIMMER_OD_IDENTIFY), cluster->identify_time);
    if (cluster->identify_time)
        hal_tasks_schedule(&cluster->identify_task, 1000);
}

#endif

/* On before level: the MCU acknowledges a level written while the channel is
 * off but does not keep it, and "on" restores the level it remembers.
 * So "on at level X" is on first, then X. */
static void dimmer_turn_on(zigbee_dimmer_cluster *cluster) {
    dimmer_write_onoff(cluster, 1);
    dimmer_set_on(cluster, 1);
}

static void dimmer_turn_on_at(zigbee_dimmer_cluster *cluster, uint8_t level) {
    dimmer_turn_on(cluster);
    dimmer_set_current_level(cluster, level);
    dimmer_write_level(cluster, level);
}

static uint8_t dimmer_level_set(uint8_t level) {
    return level >= ZCL_LEVEL_MIN && level <= ZCL_LEVEL_MAX;
}

static uint8_t dimmer_on_level_set(const zigbee_dimmer_cluster *cluster) {
    return dimmer_level_set(cluster->on_level);
}

/* ---- OnLevel in NV --------------------------------------------------------
 *
 * OnLevel is the Zigbee side's own setting (the MCU knows nothing of it), so
 * it is kept in NV: a power cut, watchdog reset or OTA must not lose it while
 * the coordinator still shows the old value. */

#define DIMMER_ON_LEVEL_UNDEFINED    0xFF

/* One byte the Zigbee side keeps for a dimmer (its OnLevel, its DefaultMoveRate); 0xFF when
 * there is none. */
static uint8_t dimmer_nv_load(const zigbee_dimmer_cluster *cluster, uint8_t item) {
    uint8_t value = 0xFF;

    if (cluster->dimmer_idx >= NV_LAYOUT_DIMMERS ||
        hal_nvm_read(item, sizeof(value), &value) != HAL_NVM_SUCCESS) {
        value = 0xFF;
    }
    return value;
}

static void dimmer_nv_store(const zigbee_dimmer_cluster *cluster, uint8_t item, uint8_t *value,
                            uint8_t *saved) {
    if (*value != *saved && cluster->dimmer_idx < NV_LAYOUT_DIMMERS &&
        hal_nvm_write(item, sizeof(*value), value) == HAL_NVM_SUCCESS) {
        *saved = *value;
    }
}

static void dimmer_load_on_level(zigbee_dimmer_cluster *cluster) {
    uint8_t value = dimmer_nv_load(cluster, NV_ITEM_DIMMER_CLUSTER_DATA(cluster->dimmer_idx));

    if (value < ZCL_LEVEL_MIN || value > ZCL_LEVEL_MAX)
        value = DIMMER_ON_LEVEL_UNDEFINED;
    cluster->on_level          = value;
    cluster->on_level_saved    = value;
    cluster->default_move_rate =
        dimmer_nv_load(cluster, NV_ITEM_DIMMER_MOVE_RATE(cluster->dimmer_idx));
    cluster->default_move_rate_saved = cluster->default_move_rate;

    value = dimmer_nv_load(cluster, NV_ITEM_DIMMER_ON_LEVEL_LOCAL(cluster->dimmer_idx));
    if (!dimmer_level_set(value))
        value = DIMMER_ON_LEVEL_UNDEFINED;
    cluster->on_level_local       = value;
    cluster->on_level_local_saved = value;

    value = dimmer_nv_load(cluster, NV_ITEM_DIMMER_LEVEL_OPTIONS(cluster->dimmer_idx));
    cluster->level_options       = value == 0xFF ? 0 : value;   /* none stored: 0 */
    cluster->level_options_saved = value;
}

static void dimmer_store_on_level(zigbee_dimmer_cluster *cluster) {
    dimmer_nv_store(cluster, NV_ITEM_DIMMER_CLUSTER_DATA(cluster->dimmer_idx),
                    &cluster->on_level, &cluster->on_level_saved);
}

/* ZCL On: to OnLevel when one is set, else wherever the MCU left it (a level
 * set while off by ExecuteIfOff for a stock MCU: that one). A channel that is
 * already on keeps its level (ZCL: On does nothing to a lit light); "on" still
 * goes to the MCU, in case it is off after all. (With a transition
 * Zigbee2MQTT sends MoveToLevelWithOnOff(OnLevel) instead of On, and that goes
 * to OnLevel whether lit or not.) */
static void dimmer_turn_on_default(zigbee_dimmer_cluster *cluster) {
    if (!cluster->on && dimmer_on_level_set(cluster))
        dimmer_turn_on_at(cluster, cluster->on_level);
    else if (!cluster->on && cluster->pending_level)
        dimmer_turn_on_at(cluster, cluster->current_level);
    else
        dimmer_turn_on(cluster);
}

/* The level a wall switch on goes to (0: where the MCU goes by itself): the
 * wall switch's OnLevel, else OnLevel, else a level set while off for a stock
 * MCU (pending: as it was before the on). */
static uint8_t dimmer_local_on_level(const zigbee_dimmer_cluster *cluster, uint8_t pending) {
    if (dimmer_level_set(cluster->on_level_local))
        return cluster->on_level_local;

    if (dimmer_on_level_set(cluster))
        return cluster->on_level;

    return pending ? cluster->current_level : 0;
}

static void dimmer_turn_off(zigbee_dimmer_cluster *cluster) {
    dimmer_stop_move(cluster);
    dimmer_write_onoff(cluster, 0);
    dimmer_set_on(cluster, 0);
}

/* ZCL ExecuteIfOff of a level command without OnOff: Options bit 0, unless its
 * OptionsMask / OptionsOverride (at payload[at], when there) set it otherwise. */
static uint8_t dimmer_execute_if_off(const zigbee_dimmer_cluster *cluster, const uint8_t *payload,
                                     uint16_t len, uint16_t at) {
    uint8_t options = cluster->level_options;

    if (len >= at + 2)
        options = (uint8_t)((options & ~payload[at]) | (payload[at + 1] & payload[at]));
    return options & ZCL_LEVEL_OPTIONS_EXECUTE_IF_OFF;
}

/* While off (ExecuteIfOff): the level the light comes on at, the light stays
 * off. Open Dimmer keeps a level written while off; a stock MCU would forget
 * it, so it is kept here for the next on. */
static void dimmer_set_off_level(zigbee_dimmer_cluster *cluster, uint8_t level) {
    dimmer_set_current_level(cluster, level);
    if (dimmer_own_mcu(cluster)) {
        dimmer_write_level(cluster, level);
    } else {
        cluster->level_value   = dimmer_level_to_value(cluster, level);
        cluster->pending_level = 1;
    }
}

/* Level change without OnOff: while off only with ExecuteIfOff (ZCL; the
 * Telink stack drops the others for an off light before they reach us). */
static void dimmer_change_level(zigbee_dimmer_cluster *cluster, uint8_t level,
                                uint8_t execute_if_off) {
    if (!cluster->on) {
        if (execute_if_off)
            dimmer_set_off_level(cluster, level);
        return;
    }
    dimmer_set_current_level(cluster, level);
    dimmer_write_level(cluster, level);
}

/* ---- ramp (Move / MoveWithOnOff) -----------------------------------------
 *
 * A step every DIMMER_RAMP_PERIOD_MS, as many levels as the command's rate (levels/s; 0xFF:
 * DefaultMoveRate, else DIMMER_RAMP_RATE) makes in that time, so any MCU gets the frames it
 * always got at the pace it always got them, only the step size follows the rate (a slow one
 * skips a step's write now and then). Open Dimmer from OPEN_DIMMER_TRANSITION_BUILD takes each
 * step in the step period (a transition time before it): one even movement instead of a fade
 * per step. */

#define DIMMER_RAMP_PERIOD_MS    300
#define DIMMER_RAMP_RATE         40  /* levels/s, the wall switch's speed: 12 per step */

/* The step period as a ZCL transition time (1/10 s, little endian). */
static const uint8_t dimmer_ramp_time[2] = { DIMMER_RAMP_PERIOD_MS / 100, 0 };

/* 0 is no ZCL rate, but a Move with it always ramped at the default here: it still does. */
static uint8_t dimmer_move_rate(const zigbee_dimmer_cluster *cluster, uint8_t rate) {
    if (rate == 0xFF)
        rate = cluster->default_move_rate;
    return (rate == 0 || rate == 0xFF) ? DIMMER_RAMP_RATE : rate;
}

static void dimmer_cluster_ramp_step(void *arg) {
    zigbee_dimmer_cluster *cluster = (zigbee_dimmer_cluster *)arg;

    if (cluster == NULL || !cluster->move_active)
        return;

    uint32_t acc  = cluster->move_acc + (uint32_t)cluster->move_rate * DIMMER_RAMP_PERIOD_MS;
    int      step = (int)(acc / 1000u);

    cluster->move_acc = (uint16_t)(acc % 1000u);

    int     next     = cluster->current_level + (cluster->move_direction ? step : -step);
    uint8_t level    = dimmer_clamp_level(next);
    uint8_t at_limit = cluster->move_direction ? level >= ZCL_LEVEL_MAX
                                               : level <= ZCL_LEVEL_MIN;

    if (level != cluster->current_level) {
        if (cluster->on)
            dimmer_send_transition(cluster, dimmer_ramp_time);
        dimmer_change_level(cluster, level, 1); /* off: a Move with ExecuteIfOff */
    }

    if (at_limit) {
        cluster->move_active = 0;
        /* ZCL: MoveWithOnOff down to the minimum switches the light off. */
        if (!cluster->move_direction && cluster->move_with_onoff)
            dimmer_turn_off(cluster);
        return;
    }
    hal_tasks_unschedule(&cluster->ramp_task);
    hal_tasks_schedule(&cluster->ramp_task, DIMMER_RAMP_PERIOD_MS);
}

/* ---- ZCL command handlers ------------------------------------------------ */

static hal_zigbee_cmd_result_t dimmer_cluster_callback(zigbee_dimmer_cluster *cluster,
                                                       uint8_t command_id,
                                                       void *cmd_payload,
                                                       uint16_t cmd_payload_len) {
    if (cluster == NULL)
        return HAL_ZIGBEE_CMD_SKIPPED;

    static const uint8_t no_time[2] = { 0, 0 };
    const uint8_t *      payload    = (const uint8_t *)cmd_payload;

    switch (command_id) {
    case ZCL_CMD_ON_WITH_TIMED_OFF:
        /* Payload: on/off control (bit 0: accept only when on), on time (u16, 1/10 s), off
         * wait time (u16, not used: nothing guards the off). */
        if (payload == NULL || cmd_payload_len < 5)
            return HAL_ZIGBEE_MALFORMED_COMMAND;

        if ((payload[0] & 0x01) && !cluster->on)
            return HAL_ZIGBEE_CMD_PROCESSED;

    /* fall through */
    case ZCL_CMD_ONOFF_ON:
    case ZCL_CMD_ON_WITH_RECALL_GLOBAL_SCENE:
    case ZCL_CMD_ONOFF_OFF:
    case ZCL_CMD_OFF_WITH_EFFECT:
    case ZCL_CMD_ONOFF_TOGGLE:
        /* No transition here: an earlier time that could still apply is taken back. */
        dimmer_send_transition(cluster, no_time);
        break;
    default:
        return HAL_ZIGBEE_CMD_SKIPPED;
    }

    switch (command_id) {
    case ZCL_CMD_ON_WITH_TIMED_OFF: {
        /* An On; Open Dimmer also goes off by itself after the on time, written once it is
         * on (whole seconds, rounded up). An on time of 0 (or the invalid 0xFFFF) leaves a
         * running countdown as it is (ZCL: the longer one wins, and 0 is no time). */
        uint16_t on_time = (uint16_t)(payload[1] | (payload[2] << 8));

        dimmer_turn_on_default(cluster);
        if (on_time != 0 && on_time != 0xFFFF && dimmer_od_features(cluster))
            dimmer_write_tuya_value(dimmer_countdown_dp(cluster), (on_time + 9u) / 10u);
        break;
    }
    case ZCL_CMD_ONOFF_ON:
    case ZCL_CMD_ON_WITH_RECALL_GLOBAL_SCENE:
        dimmer_turn_on_default(cluster);
        break;
    case ZCL_CMD_ONOFF_OFF:
    case ZCL_CMD_OFF_WITH_EFFECT:
        dimmer_turn_off(cluster);
        break;
    case ZCL_CMD_ONOFF_TOGGLE:
        if (cluster->on)
            dimmer_turn_off(cluster);
        else
            dimmer_turn_on_default(cluster);
        break;
    default:
        break;
    }
    return HAL_ZIGBEE_CMD_PROCESSED;
}

static hal_zigbee_cmd_result_t dimmer_cluster_level_callback(zigbee_dimmer_cluster *cluster,
                                                             uint8_t command_id,
                                                             void *cmd_payload,
                                                             uint16_t cmd_payload_len) {
    const uint8_t *payload = (const uint8_t *)cmd_payload;

    if (cluster == NULL)
        return HAL_ZIGBEE_CMD_SKIPPED;

    switch (command_id) {
    case ZCL_CMD_LEVEL_MOVE_TO_LEVEL:
    case ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF: {
        /* Payload: level, transition time (u16), [options mask, override] */
        if (payload == NULL || cmd_payload_len < 1)
            return HAL_ZIGBEE_MALFORMED_COMMAND;

        uint8_t with_onoff = command_id == ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF;
        dimmer_stop_move(cluster);
        if (cmd_payload_len >= 3 && (with_onoff || cluster->on))
            dimmer_send_transition(cluster, payload + 1);

        if (!with_onoff) {
            dimmer_change_level(cluster, dimmer_clamp_level(payload[0]),
                                dimmer_execute_if_off(cluster, payload, cmd_payload_len, 3));
            break;
        }
        if (payload[0] == 0) {
            /* Level 0 with OnOff = off; keep the level for the next "on". */
            dimmer_turn_off(cluster);
            break;
        }

        /* Home Assistant turns dimmers on with this command only, and the
         * MCU does not switch on from a level: on, then the level. */
        dimmer_turn_on_at(cluster, dimmer_clamp_level(payload[0]));
        break;
    }

    case ZCL_CMD_LEVEL_MOVE:
    case ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF: {
        /* Payload: move mode, rate (levels/s; 0xFF and 0: the default, as the ramp always ran
         * for a 0), [options mask, override]. */
        if (payload == NULL || cmd_payload_len < 2)
            return HAL_ZIGBEE_MALFORMED_COMMAND;

        uint8_t with_onoff = command_id == ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF;
        uint8_t up         = payload[0] == ZCL_LEVEL_MOVE_UP;

        /* Off without OnOff and with ExecuteIfOff: the level moves, the light stays off. */
        if (!cluster->on &&
            (with_onoff || !dimmer_execute_if_off(cluster, payload, cmd_payload_len, 2))) {
            if (!(with_onoff && up))
                break; /* nothing to move while off */
            /* ZCL: start from the minimum level when switching on by moving. */
            dimmer_turn_on_at(cluster, ZCL_LEVEL_MIN);
        }
        cluster->move_direction  = up;
        cluster->move_with_onoff = with_onoff;
        cluster->move_rate       = dimmer_move_rate(cluster, payload[1]);
        cluster->move_acc        = 0;
        cluster->move_active     = 1;
        hal_tasks_unschedule(&cluster->ramp_task);
        hal_tasks_schedule(&cluster->ramp_task, DIMMER_RAMP_PERIOD_MS);
        break;
    }

    case ZCL_CMD_LEVEL_STEP:
    case ZCL_CMD_LEVEL_STEP_WITH_ON_OFF: {
        /* Payload: step mode, step size, transition time (u16), [options] */
        if (payload == NULL || cmd_payload_len < 2)
            return HAL_ZIGBEE_MALFORMED_COMMAND;

        uint8_t with_onoff = command_id == ZCL_CMD_LEVEL_STEP_WITH_ON_OFF;
        uint8_t up         = payload[0] == ZCL_LEVEL_MOVE_UP;
        int     base       = cluster->current_level;

        dimmer_stop_move(cluster);
        if (!cluster->on && !with_onoff) {
            if (dimmer_execute_if_off(cluster, payload, cmd_payload_len, 4))
                dimmer_set_off_level(cluster, dimmer_clamp_level(up ? base + payload[1]
                                                                    : base - payload[1]));
            break;
        }
        if (!cluster->on) {
            if (!up)
                break;
            base = 0; /* stepping up from off starts at the bottom */
        }
        if (cmd_payload_len >= 4)
            dimmer_send_transition(cluster, payload + 2);

        int next = up ? base + payload[1] : base - payload[1];
        if (!up && next < ZCL_LEVEL_MIN && with_onoff) {
            dimmer_turn_off(cluster);
            break;
        }
        uint8_t level = dimmer_clamp_level(next);
        if (!cluster->on) {
            dimmer_turn_on_at(cluster, level);
        } else {
            dimmer_set_current_level(cluster, level);
            dimmer_write_level(cluster, level);
        }
        break;
    }

    case ZCL_CMD_LEVEL_STOP:
    case ZCL_CMD_LEVEL_STOP_WITH_ON_OFF:
        dimmer_stop_move(cluster);
        break;

    default:
        return HAL_ZIGBEE_CMD_SKIPPED;
    }
    return HAL_ZIGBEE_CMD_PROCESSED;
}

/* ---- MCU reports --------------------------------------------------------- */

/* The MCU's min or max changed: the same light output is another level now. */
static void dimmer_range_changed(zigbee_dimmer_cluster *cluster) {
    if (cluster->level_value != 0)
        dimmer_set_current_level(cluster, dimmer_value_to_level(cluster, cluster->level_value));
    dimmer_update_startup_level(cluster);
}

#ifdef SECONDARY_MCU_IMAGE
/* A value DP of Open Dimmer: this channel's countdown or one of its settings from 162 on. */
static void dimmer_on_od_value(zigbee_dimmer_cluster *cluster, uint8_t dpid, uint32_t number) {
    uint16_t v = (uint16_t)(number > 0xFFFF ? 0xFFFF : number);

    if (dpid == dimmer_countdown_dp(cluster)) {
        cluster->countdown = number;
        hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_ON_OFF,
                                            ZCL_ATTR_ONOFF_COUNTDOWN);
        return;
    }

    uint8_t k = (uint8_t)(dpid - dimmer_od_dp(cluster, 0));
    if (dpid < dimmer_od_dp(cluster, 0) || (k & 1) || k > 2 * DIMMER_OD_BOOST)
        return; /* the other channel's, or none of these */

    switch (k >> 1) {
    case DIMMER_OD_INPUT:
        if (number <= DIMMER_INPUT_MODE_MAX) {
            cluster->input_mode     = (uint8_t)number;
            cluster->input_mode_mcu = (uint8_t)number;
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
                                                ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_INPUT_MODE);
        }
        break;
    case DIMMER_OD_POWER_LEVEL:
        cluster->startup_value = v;
        dimmer_update_startup_level(cluster);
        break;
    case DIMMER_OD_BOOST:
        cluster->start_boost = v;
        hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG,
                                            ZCL_ATTR_BALLAST_START_BOOST);
        break;
    default: /* identify: its end is counted here */
        break;
    }
}

#endif

/* Our own PY32 firmware with its per-channel DPs on this dimmer. */
static uint8_t dimmer_own_mcu(const zigbee_dimmer_cluster *cluster) {
#ifdef SECONDARY_MCU_IMAGE
    return cluster->own_power_on_dpid && tuya_mcu_app_mcu_is_own();
#else
    (void)cluster;
    return 0;
#endif
}

static void dimmer_cluster_on_dp_report(uint8_t dpid, uint8_t dp_type,
                                        const uint8_t *value, uint16_t value_len) {
    if (value_len < 1)
        return;

    /* A newer write of ours sets this on/off or enum DP to another value:
     * the report (e.g. the late answer to an earlier write, off before our
     * on) is outdated already. Our write's own answer settles it; if none
     * comes, the stock MCU's report is handed on again (passive) when our
     * write times out. */
    if (tuya_secondary_mcu_report_is_superseded())
        return;

    // A single DP report can apply to any registered dimmer endpoint, since
    // DPIDs are assigned per-dimmer, not globally.
    for (int i = 0; i < DIMMER_MAX_ENDPOINTS; i++) {
        zigbee_dimmer_cluster *cluster = dimmer_cluster_by_endpoint[i];
        if (cluster == NULL)
            continue;

        uint32_t number = dimmer_decode_tuya_value(value, value_len);

        if (dp_type == TUYA_DP_TYPE_BOOL && dpid == cluster->onoff_dpid) {
            /* Our own "on" has already set cluster->on, so only the MCU
             * switching on by itself (the wall switch) gets here while off.
             * Not an answer to our query or to our write (the stock MCU
             * answers writes with 0x06 too), not the first report after
             * boot. */
            uint8_t switched_on = value[0] && cluster->state_known && !cluster->on &&
                                  !tuya_secondary_mcu_report_is_passive() &&
                                  !tuya_secondary_mcu_report_acks_write();
            uint8_t pending = cluster->pending_level;

            if (!value[0])
                dimmer_stop_move(cluster);
            dimmer_set_on(cluster, value[0]);
            cluster->state_known = 1;

            /* Go to the wall switch's OnLevel (else OnLevel) at once, with
            * no round trip through the network: the MCU is fading in to
            * the level it remembers and turns towards this one instead. */
            uint8_t on_at = switched_on ? dimmer_local_on_level(cluster, pending) : 0;
            if (on_at) {
                dimmer_set_current_level(cluster, on_at);
                dimmer_write_level(cluster, on_at);
            }
        } else if (dp_type == TUYA_DP_TYPE_VALUE && dpid == cluster->level_dpid) {
            if (cluster->move_active || cluster->level_holdoff) {
                /* Our own ramp or fade in progress: the MCU reports where
                 * its smoothing is, not where the light is going. */
                cluster->held_report_value = number;
                cluster->has_held_report   = 1;
            } else {
                cluster->level_value = number;
                dimmer_set_current_level(cluster, dimmer_value_to_level(cluster, number));
            }
        } else if (dp_type == TUYA_DP_TYPE_VALUE && cluster->min_level_dpid &&
                   dpid == cluster->min_level_dpid) {
            cluster->mcu_min   = (uint16_t)(number > 0xFFFF ? 0xFFFF : number);
            cluster->min_level = dimmer_tuya_value_to_zcl_level(number);
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG,
                                                ZCL_ATTR_BALLAST_MIN_LEVEL);
            dimmer_range_changed(cluster);
        } else if (dp_type == TUYA_DP_TYPE_VALUE && cluster->max_level_dpid &&
                   dpid == cluster->max_level_dpid) {
            cluster->mcu_max   = (uint16_t)(number > 0xFFFF ? 0xFFFF : number);
            cluster->max_level = dimmer_tuya_value_to_zcl_level(number);
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG,
                                                ZCL_ATTR_BALLAST_MAX_LEVEL);
            dimmer_range_changed(cluster);
        } else if (dp_type == TUYA_DP_TYPE_ENUM && cluster->switch_type_dpid &&
                   dpid == cluster->switch_type_dpid) {
            cluster->switch_type = value[0];
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
                                                ZCL_ATTR_ONOFF_CONFIGURATION_SWITCH_TYPE);
        } else if (dp_type == TUYA_DP_TYPE_ENUM && cluster->power_on_behavior_dpid &&
                   dpid == cluster->power_on_behavior_dpid && !dimmer_own_mcu(cluster)) {
            /* Shared DP 14: every channel. Our own MCU has one per channel. */
            cluster->startup_mode = tuya_mcu_startup_from_power_on(value[0]);
            hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_ON_OFF,
                                                ZCL_ATTR_START_UP_ONOFF);
        }
#ifdef SECONDARY_MCU_IMAGE
        else if (dp_type == TUYA_DP_TYPE_ENUM && dimmer_own_mcu(cluster) &&
                 dpid == cluster->own_power_on_dpid) {
            cluster->startup_mode = tuya_mcu_startup_from_power_on(value[0]);
            hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_ON_OFF,
                                                ZCL_ATTR_START_UP_ONOFF);
        } else if (dp_type == TUYA_DP_TYPE_ENUM && cluster->action_dpid &&
                   tuya_mcu_app_mcu_is_own() && dpid == cluster->action_dpid &&
                   value[0] >= 1 && value[0] <= 5) {
            cluster->action_seq   = (uint8_t)((cluster->action_seq + 1) & 0x0F);
            cluster->action_state = (uint16_t)((cluster->action_seq << 4) | value[0]);
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_MULTISTATE_INPUT_BASIC,
                                                ZCL_ATTR_MULTISTATE_INPUT_PRESENT_VALUE);
        } else if (dp_type == TUYA_DP_TYPE_VALUE && dimmer_own_mcu(cluster) &&
                   cluster->dimmer_idx < OPEN_DIMMER_CHANNELS) {
            dimmer_on_od_value(cluster, dpid, number);
        }
#endif
    }
}

/* ---- trampolines + registration ------------------------------------------ */

static hal_zigbee_cmd_result_t dimmer_cluster_callback_trampoline(
    uint8_t endpoint, uint16_t cluster_id, uint8_t command_id,
    void *cmd_payload, uint16_t cmd_payload_len) {
    return dimmer_cluster_callback(dimmer_by_endpoint(endpoint),
                                   command_id, cmd_payload, cmd_payload_len);
}

static hal_zigbee_cmd_result_t dimmer_cluster_level_callback_trampoline(
    uint8_t endpoint, uint16_t cluster_id, uint8_t command_id,
    void *cmd_payload, uint16_t cmd_payload_len) {
    return dimmer_cluster_level_callback(dimmer_by_endpoint(endpoint),
                                         command_id, cmd_payload,
                                         cmd_payload_len);
}

#ifdef SECONDARY_MCU_IMAGE
static hal_zigbee_cmd_result_t dimmer_cluster_identify_callback_trampoline(
    uint8_t endpoint, uint16_t cluster_id, uint8_t command_id,
    void *cmd_payload, uint16_t cmd_payload_len) {
    zigbee_dimmer_cluster *cluster = dimmer_by_endpoint(endpoint);
    const uint8_t *        payload = (const uint8_t *)cmd_payload;

    if (cluster == NULL || command_id != ZCL_CMD_IDENTIFY)
        return HAL_ZIGBEE_CMD_SKIPPED; /* Identify Query: the stack answers it */

    if (payload == NULL || cmd_payload_len < 2)
        return HAL_ZIGBEE_MALFORMED_COMMAND;

    dimmer_identify(cluster, (uint16_t)(payload[0] | (payload[1] << 8)));
    return HAL_ZIGBEE_CMD_PROCESSED;
}

#define DIMMER_OD_ATTRS       1 /* each Open Dimmer setting is one more attribute of its cluster */
#else
#define DIMMER_OD_ATTRS       0
#endif
#define DIMMER_ONOFF_ATTRS    (2 + DIMMER_OD_ATTRS)
#define DIMMER_LEVEL_ATTRS    (5 + DIMMER_OD_ATTRS)

static void dimmer_add_cluster(hal_zigbee_endpoint *endpoint, uint16_t cluster_id,
                               uint8_t attribute_count, hal_zigbee_attribute *attributes,
                               hal_zigbee_cmd_callback_t cmd_callback) {
    hal_zigbee_cluster *c = &endpoint->clusters[endpoint->cluster_count++];

    c->cluster_id      = cluster_id;
    c->attribute_count = attribute_count;
    c->attributes      = attributes;
    c->is_server       = 1;
    c->cmd_callback    = cmd_callback;
}

void dimmer_cluster_add_to_endpoint(zigbee_dimmer_cluster *cluster,
                                    hal_zigbee_endpoint *endpoint) {
    cluster->endpoint = endpoint->endpoint;
    if (endpoint->endpoint < DIMMER_MAX_ENDPOINTS)
        dimmer_cluster_by_endpoint[endpoint->endpoint] = cluster;

    cluster->ramp_task.handler = dimmer_cluster_ramp_step;
    cluster->ramp_task.arg     = cluster;
    hal_tasks_init(&cluster->ramp_task);
    cluster->move_active = 0;

    cluster->holdoff_task.handler = dimmer_holdoff_done;
    cluster->holdoff_task.arg     = cluster;
    hal_tasks_init(&cluster->holdoff_task);
    cluster->level_holdoff   = 0;
    cluster->has_held_report = 0;
    cluster->mcu_min         = 0;
    cluster->mcu_max         = 0;
    cluster->level_value     = 0;
    cluster->state_known     = 0;
    cluster->pending_level   = 0;
    cluster->countdown       = 0;
    cluster->startup_value   = 0;
    cluster->startup_level   = ZCL_START_UP_CURRENT_LEVEL_PREVIOUS;
    cluster->identify_time   = 0;
    cluster->start_boost     = 0;
    cluster->input_mode      = 0;
    cluster->input_mode_mcu  = 0;
#ifdef SECONDARY_MCU_IMAGE
    cluster->identify_task.handler = dimmer_identify_tick;
    cluster->identify_task.arg     = cluster;
    hal_tasks_init(&cluster->identify_task);
#endif
    dimmer_load_on_level(cluster);

    hal_zigbee_attribute *level_attrs = &cluster->attr_infos[DIMMER_ONOFF_ATTRS];

    SETUP_ATTR(0, ZCL_ATTR_ONOFF, ZCL_DATA_TYPE_BOOLEAN, ATTR_READONLY, cluster->on);
    SETUP_ATTR(1, ZCL_ATTR_START_UP_ONOFF, ZCL_DATA_TYPE_ENUM8, ATTR_WRITABLE,
               cluster->startup_mode);
    SETUP_ATTR_FOR_TABLE(level_attrs, 0, ZCL_ATTR_LEVEL_CURRENT_LEVEL, ZCL_DATA_TYPE_UINT8,
                         ATTR_READONLY, cluster->current_level);
    SETUP_ATTR_FOR_TABLE(level_attrs, 1, ZCL_ATTR_LEVEL_ON_LEVEL, ZCL_DATA_TYPE_UINT8,
                         ATTR_WRITABLE, cluster->on_level);
    SETUP_ATTR_FOR_TABLE(level_attrs, 2, ZCL_ATTR_LEVEL_DEFAULT_MOVE_RATE, ZCL_DATA_TYPE_UINT8,
                         ATTR_WRITABLE, cluster->default_move_rate);
    SETUP_ATTR_FOR_TABLE(level_attrs, 3, ZCL_ATTR_LEVEL_OPTIONS, ZCL_DATA_TYPE_BITMAP8,
                         ATTR_WRITABLE, cluster->level_options);
    SETUP_ATTR_FOR_TABLE(level_attrs, 4, ZCL_ATTR_LEVEL_ON_LEVEL_LOCAL, ZCL_DATA_TYPE_UINT8,
                         ATTR_WRITABLE, cluster->on_level_local);

    // Min/max brightness on the standard lightingBallastCfg cluster (0x0301),
    // so Zigbee2MQTT can render sliders bound to these without a custom cluster.
    SETUP_ATTR_FOR_TABLE(cluster->ballast_attr_infos, 0, ZCL_ATTR_BALLAST_MIN_LEVEL,
                         ZCL_DATA_TYPE_UINT8, ATTR_WRITABLE, cluster->min_level);
    SETUP_ATTR_FOR_TABLE(cluster->ballast_attr_infos, 1, ZCL_ATTR_BALLAST_MAX_LEVEL,
                         ZCL_DATA_TYPE_UINT8, ATTR_WRITABLE, cluster->max_level);

    // Switch type on the standard genOnOffSwitchCfg cluster (0x0007).
    SETUP_ATTR_FOR_TABLE(cluster->switch_type_attr_infos, 0,
                         ZCL_ATTR_ONOFF_CONFIGURATION_SWITCH_TYPE,
                         ZCL_DATA_TYPE_ENUM8, ATTR_WRITABLE, cluster->switch_type);

#ifdef SECONDARY_MCU_IMAGE
    /* Open Dimmer's settings (from OPEN_DIMMER_FEATURES_BUILD), next to the standard ones. */
    SETUP_ATTR(2, ZCL_ATTR_ONOFF_COUNTDOWN, ZCL_DATA_TYPE_UINT32, ATTR_WRITABLE,
               cluster->countdown);
    SETUP_ATTR_FOR_TABLE(level_attrs, 5, ZCL_ATTR_LEVEL_START_UP_CURRENT_LEVEL,
                         ZCL_DATA_TYPE_UINT8, ATTR_WRITABLE, cluster->startup_level);
    SETUP_ATTR_FOR_TABLE(cluster->ballast_attr_infos, 2, ZCL_ATTR_BALLAST_START_BOOST,
                         ZCL_DATA_TYPE_UINT16, ATTR_WRITABLE, cluster->start_boost);
    SETUP_ATTR_FOR_TABLE(cluster->switch_type_attr_infos, 1,
                         ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_INPUT_MODE,
                         ZCL_DATA_TYPE_ENUM8, ATTR_WRITABLE, cluster->input_mode);
    SETUP_ATTR_FOR_TABLE(cluster->identify_attr_infos, 0, ZCL_ATTR_IDENTIFY_TIME,
                         ZCL_DATA_TYPE_UINT16, ATTR_WRITABLE, cluster->identify_time);
#endif

    dimmer_add_cluster(endpoint, ZCL_CLUSTER_ON_OFF, DIMMER_ONOFF_ATTRS, cluster->attr_infos,
                       dimmer_cluster_callback_trampoline);
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_LEVEL_CONTROL, DIMMER_LEVEL_ATTRS, level_attrs,
                       dimmer_cluster_level_callback_trampoline);
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG, 2 + DIMMER_OD_ATTRS,
                       cluster->ballast_attr_infos, NULL);
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG, 1 + DIMMER_OD_ATTRS,
                       cluster->switch_type_attr_infos, NULL);

#ifdef SECONDARY_MCU_IMAGE
    /* Identify: the channel blinks (Open Dimmer from OPEN_DIMMER_FEATURES_BUILD). */
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_IDENTIFY, 1, cluster->identify_attr_infos,
                       dimmer_cluster_identify_callback_trampoline);

    /* Wall switch gestures of the own PY32 firmware (silent with another MCU). */
    cluster->action_seq   = 0;
    cluster->action_state = 0;
    SETUP_ATTR_FOR_TABLE(cluster->multistate_attr_infos, 0,
                         ZCL_ATTR_MULTISTATE_INPUT_NUMBER_OF_STATES,
                         ZCL_DATA_TYPE_UINT16, ATTR_READONLY, dimmer_gesture_states);
    SETUP_ATTR_FOR_TABLE(cluster->multistate_attr_infos, 1,
                         ZCL_ATTR_MULTISTATE_INPUT_OUT_OF_SERVICE,
                         ZCL_DATA_TYPE_BOOLEAN, ATTR_READONLY, dimmer_gesture_out_of_service);
    SETUP_ATTR_FOR_TABLE(cluster->multistate_attr_infos, 2,
                         ZCL_ATTR_MULTISTATE_INPUT_PRESENT_VALUE,
                         ZCL_DATA_TYPE_UINT16, ATTR_READONLY, cluster->action_state);
    SETUP_ATTR_FOR_TABLE(cluster->multistate_attr_infos, 3,
                         ZCL_ATTR_MULTISTATE_INPUT_STATUS_FLAGS,
                         ZCL_DATA_TYPE_BITMAP8, ATTR_READONLY, dimmer_gesture_flags);
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_MULTISTATE_INPUT_BASIC, 4,
                       cluster->multistate_attr_infos, NULL);
#endif

    // Receive DP state reports from the secondary MCU (e.g. physical button
    // presses) for every registered dimmer, not just this endpoint.
    tuya_secondary_mcu_add_dp_listener(dimmer_cluster_on_dp_report);

    /* Nothing is pushed to the MCU here. It stores min/max/switch type and
     * power-on behaviour itself, and those are read back from its answer to
     * query-all at first contact. (A push from here never reached the MCU
     * anyway: the UART link is enabled only after parse_config().) */
}

/* Attribute writes, by cluster: attribute ids repeat across clusters
 * (Level Control OnLevel = ballast max = 0x0011, OnOff = switch type =
 * 0x0000). Only attributes whose write succeeded get here (the HAL skips the
 * rest). */
void dimmer_cluster_on_write_attr(zigbee_dimmer_cluster *cluster,
                                  uint16_t cluster_id, uint16_t attribute_id) {
    if (cluster == NULL)
        return;

    switch (cluster_id) {
    case ZCL_CLUSTER_ON_OFF:
        if (attribute_id == ZCL_ATTR_START_UP_ONOFF &&
            (cluster->power_on_behavior_dpid || dimmer_own_mcu(cluster))) {
            uint8_t value = tuya_mcu_power_on_from_startup(cluster->startup_mode);

            /* Own MCU: this channel only. Another MCU: the shared DP 14 sets
             * every channel (each endpoint follows its report). */
            tuya_secondary_mcu_write_dp(dimmer_own_mcu(cluster) ? cluster->own_power_on_dpid
                                                                : cluster->power_on_behavior_dpid,
                                        TUYA_DP_TYPE_ENUM, &value, sizeof(value));
        }
#ifdef SECONDARY_MCU_IMAGE
        else if (attribute_id == ZCL_ATTR_ONOFF_COUNTDOWN) {
            /* Counts only while on (the MCU keeps 0 when off). */
            if (!cluster->on || !dimmer_od_features(cluster)) {
                cluster->countdown = 0;
            } else {
                if (cluster->countdown > DIMMER_COUNTDOWN_MAX)
                    cluster->countdown = DIMMER_COUNTDOWN_MAX;
                dimmer_write_tuya_value(dimmer_countdown_dp(cluster), cluster->countdown);
            }
        }
#endif
        break;

    case ZCL_CLUSTER_LEVEL_CONTROL:
        if (attribute_id == ZCL_ATTR_LEVEL_ON_LEVEL) {
            dimmer_store_on_level(cluster);
        } else if (attribute_id == ZCL_ATTR_LEVEL_DEFAULT_MOVE_RATE) {
            dimmer_nv_store(cluster, NV_ITEM_DIMMER_MOVE_RATE(cluster->dimmer_idx),
                            &cluster->default_move_rate, &cluster->default_move_rate_saved);
        } else if (attribute_id == ZCL_ATTR_LEVEL_OPTIONS) {
            dimmer_nv_store(cluster, NV_ITEM_DIMMER_LEVEL_OPTIONS(cluster->dimmer_idx),
                            &cluster->level_options, &cluster->level_options_saved);
        } else if (attribute_id == ZCL_ATTR_LEVEL_ON_LEVEL_LOCAL) {
            dimmer_nv_store(cluster, NV_ITEM_DIMMER_ON_LEVEL_LOCAL(cluster->dimmer_idx),
                            &cluster->on_level_local, &cluster->on_level_local_saved);
        }
#ifdef SECONDARY_MCU_IMAGE
        else if (attribute_id == ZCL_ATTR_LEVEL_START_UP_CURRENT_LEVEL) {
            /* 0xFF previous: 0, the last level; 0x00 minimum: the lowest value, which the MCU
             * takes as the channel's min at every start (also after a later change of it). */
            uint8_t l = cluster->startup_level;
            if (!dimmer_od_features(cluster)) {
                cluster->startup_level = dimmer_startup_level(cluster);
                break;
            }
            dimmer_write_tuya_value(dimmer_od_dp(cluster, DIMMER_OD_POWER_LEVEL),
                                    l == ZCL_START_UP_CURRENT_LEVEL_PREVIOUS ? 0
                                    : l == ZCL_START_UP_CURRENT_LEVEL_MINIMUM ? TUYA_BRIGHTNESS_MIN
                                    : dimmer_level_to_value(cluster, l));
        }
#endif
        break;

    case ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG:
        if (attribute_id == ZCL_ATTR_BALLAST_MIN_LEVEL && cluster->min_level_dpid) {
            dimmer_write_tuya_value(cluster->min_level_dpid,
                                    dimmer_zcl_level_to_tuya_value(cluster->min_level));
        } else if (attribute_id == ZCL_ATTR_BALLAST_MAX_LEVEL && cluster->max_level_dpid) {
            dimmer_write_tuya_value(cluster->max_level_dpid,
                                    dimmer_zcl_level_to_tuya_value(cluster->max_level));
        }
#ifdef SECONDARY_MCU_IMAGE
        else if (attribute_id == ZCL_ATTR_BALLAST_START_BOOST) {
            if (!dimmer_od_features(cluster)) {
                cluster->start_boost = 0;
                break;
            }
            if (cluster->start_boost > DIMMER_BOOST_MAX)
                cluster->start_boost = DIMMER_BOOST_MAX;
            /* The MCU keeps steps of 50 ms; its answer tells the one it took. */
            dimmer_write_tuya_value(dimmer_od_dp(cluster, DIMMER_OD_BOOST), cluster->start_boost);
        }
#endif
        break;

    case ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG:
        if (attribute_id == ZCL_ATTR_ONOFF_CONFIGURATION_SWITCH_TYPE && cluster->switch_type_dpid) {
            tuya_secondary_mcu_write_dp(cluster->switch_type_dpid, TUYA_DP_TYPE_ENUM,
                                        &cluster->switch_type, sizeof(cluster->switch_type));
        }
#ifdef SECONDARY_MCU_IMAGE
        else if (attribute_id == ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_INPUT_MODE) {
            if (!dimmer_od_features(cluster) || cluster->input_mode > DIMMER_INPUT_MODE_MAX) {
                cluster->input_mode = cluster->input_mode_mcu;
                break;
            }
            dimmer_write_tuya_value(dimmer_od_dp(cluster, DIMMER_OD_INPUT), cluster->input_mode);
        }
#endif
        break;

#ifdef SECONDARY_MCU_IMAGE
    case ZCL_CLUSTER_IDENTIFY:
        if (attribute_id == ZCL_ATTR_IDENTIFY_TIME)
            dimmer_identify(cluster, cluster->identify_time);
        break;
#endif

    default:
        break;
    }
}

void dimmer_cluster_callback_attr_write_trampoline(uint8_t endpoint,
                                                   uint16_t cluster_id,
                                                   uint16_t attribute_id) {
    dimmer_cluster_on_write_attr(dimmer_by_endpoint(endpoint), cluster_id, attribute_id);
}
