#include "zigbee/dimmer_cluster.h"
#include <string.h>
#include "device_config/nvm_items.h"
#include "device_config/device_params_nv.h"
#include "hal/nvm.h"
#include "zigbee/cluster_common.h"
#include "zigbee/consts.h"
#include "hal/printf_selector.h"
#include "hal/timer.h"
#include "zigbee/tuya_mcu_app.h"
#include "zigbee/tuya_mcu_ota.h"
#include "zigbee/tuya_secondary_mcu.h"
#ifdef DIMMER_SCENES
#include "zigbee/scene_cluster.h"
/* SceneValid goes 0 when the light changes other than by a recall. */
#define DIMMER_SCENE_CHANGED(endpoint)    scene_cluster_light_changed(endpoint)
#else
#define DIMMER_SCENE_CHANGED(endpoint)    ((void)0)
#endif

/* Gestures of Open Dimmer (DP 105 / 106): 1 single, 2 double, 3 triple, 4 hold, 5 release,
 * 6 press (from 2.2.0: the fast press, in the detached mode at once, no multi-taps). */
#define DIMMER_GESTURE_MAX     6
#define DIMMER_GESTURE_HOLD    4

#ifdef SECONDARY_MCU_IMAGE
static const uint16_t dimmer_gesture_states         = DIMMER_GESTURE_MAX + 1; /* and none */
static const uint8_t  dimmer_gesture_out_of_service = 0;
static const uint8_t  dimmer_gesture_flags          = 0;
#endif

#define DIMMER_MAX_ENDPOINTS    10
static zigbee_dimmer_cluster *dimmer_cluster_by_endpoint[DIMMER_MAX_ENDPOINTS];
static void dimmer_reports_known(void);

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

/* The light as a whole for the MCU updates (tuya_mcu_ota_set_light): lit while any channel is
 * on; told once every channel's state is known, and on every change after. */
static void dimmer_light_tell(void) {
    uint8_t lit = 0;

    for (int i = 0; i < DIMMER_MAX_ENDPOINTS; i++) {
        zigbee_dimmer_cluster *c = dimmer_cluster_by_endpoint[i];
        if (!c)
            continue;
        if (!c->state_known)
            return;

        lit |= c->on;
    }
    tuya_mcu_ota_set_light(lit);
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
    if (on) {
        cluster->pending_level = 0; /* applied by whoever switched it on */
    } else {
        cluster->fade_ms = 0;
        cluster->hold    = 0;       /* an off ends a wall switch hold */
    }
    dimmer_notify_onoff(cluster);
    /* Open Dimmer: any off (ours, the wall switch, the countdown's own end) ends it. */
    if (!on && cluster->countdown) {
        cluster->countdown = 0;
        hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_ON_OFF,
                                            ZCL_ATTR_ONOFF_COUNTDOWN);
    }
    dimmer_light_tell();
}

/* ---- a wall switch hold (Open Dimmer) --------------------------------------
 *
 * From its HOLD gesture (normal wall switch mode) the finger has the level: the MCU reports it
 * every 0.5 s (from 2.2.4) and ignores our level writes for the light. Until the RELEASE, an
 * off, or DIMMER_HOLD_MS without a level report (a lost release, a hold at the end of the range,
 * an older MCU that reports only at the end), no ramp of ours runs and the level reports are
 * taken as they come, none held back for a fade of ours. */
#define DIMMER_HOLD_MS    1200

#ifdef SECONDARY_MCU_IMAGE
/* 1 while a hold is in progress; a level report (report) keeps it going. */
static uint8_t dimmer_holding(zigbee_dimmer_cluster *cluster, uint8_t report) {
    uint32_t now = hal_millis();

    if (cluster->hold && now - cluster->hold_at >= DIMMER_HOLD_MS)
        cluster->hold = 0;
    if (cluster->hold && report)
        cluster->hold_at = now;
    return cluster->hold;
}

#else
#define dimmer_holding(cluster, report)    0
#endif

/* ---- MCU writes ---------------------------------------------------------- */

/* How long reports of the level DP are treated as the MCU's own fade towards
 * a level we wrote. The MCU always fades (both the WiFi and Zigbee DMS16
 * variants); a fade takes about 1 s, 3 s leaves margin. */
#define DIMMER_FADE_HOLDOFF_MS    3000

static void dimmer_start_holdoff(zigbee_dimmer_cluster *cluster) {
    if (dimmer_holding(cluster, 0))
        return; /* a wall switch hold: the MCU does not fade to what we wrote */

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
    if (cluster->fade_pending)
        cluster->fade_pending = 0;  /* the level of the timed move just sent */
    else
        cluster->fade_ms = 0;       /* a move the module does not time: no estimate */
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
 * settings, as before, and gets nothing more.
 *  - 0xFFFF (none given: ZCL's "use the default"; the converter sends it when Zigbee2MQTT has
 *    no transition), the On / Off / Toggle commands and a command without a time: the fade
 *    settings. DP 0 goes out only while an earlier time could still apply to this change.
 *  - 0: at once. DP 1 ms (0 there means the fade settings): the MCU's timed move lands in one
 *    half cycle, from dark (its floor) to the level as well; an off goes to the floor at once
 *    and is dark after its usual moment there (off_hold_ms, 80 ms: open-dimmer dim_core.c
 *    dim_fader_set_timed() takes it from a time only when the time is longer).
 *  - 1..0xFFFE: that many tenths (at most 65.535 s, the DP's range).
 * The write queue keeps one frame in flight and sends a lost one again before the next, so the
 * time always reaches the MCU before its change; a time lost for good leaves that change at the
 * fade settings. */
#define DIMMER_TRANSITION_VALID_MS    1500        /* the MCU's window is 1 s from its receipt */

#define DIMMER_TIME_NONE              0xFFFFFFFFu /* no time given: the fade settings */

/* The time of the change written next, in ms (at most the DP's 65535), or DIMMER_TIME_NONE. */
static void dimmer_send_time(zigbee_dimmer_cluster *cluster, uint32_t ms) {
    if (!dimmer_od_from(cluster, OPEN_DIMMER_TRANSITION_BUILD))
        return;

    uint32_t now = hal_millis();

    if (ms == DIMMER_TIME_NONE) {
        if (cluster->transition_at == 0 ||
            now - cluster->transition_at >= DIMMER_TRANSITION_VALID_MS)
            return;

        ms = 0;                        /* take an earlier time back */
    }
    dimmer_write_tuya_value(cluster->own_transition_dpid, ms > 0xFFFFu ? 0xFFFFu : ms);
    cluster->transition_at = ms ? (now | 1u) : 0;
}

/* A ZCL time (1/10 s, little endian at tt): 0xFFFF none, 0 at once (1 ms), else tenths. */
static uint32_t dimmer_zcl_time_ms(const uint8_t *tt) {
    uint16_t t = (uint16_t)(tt[0] | (tt[1] << 8));

    return t == 0xFFFF ? DIMMER_TIME_NONE : t == 0 ? 1u : (uint32_t)t * 100u;
}

static void dimmer_send_transition(zigbee_dimmer_cluster *cluster, const uint8_t *tt) {
    dimmer_send_time(cluster, dimmer_zcl_time_ms(tt));
}

/* Where the light is (MCU value): during the last timed lit-to-lit move sent, the even point of
 * it for the time gone (the MCU moves evenly from where it was); else the value the module
 * knows (the last written or reported). No overflow: d <= 1000, t < 65536. */
static uint32_t dimmer_fade_position(const zigbee_dimmer_cluster *cluster, uint32_t now) {
    if (cluster->fade_ms) {
        uint32_t t = now - cluster->fade_at;
        if (t < cluster->fade_ms) {
            uint32_t a = cluster->fade_from, b = cluster->fade_to;
            uint32_t m = (b > a ? b - a : a - b) * t / cluster->fade_ms;
            return b > a ? a + m : a - m;
        }
    }
    return cluster->level_value ? cluster->level_value
                                : dimmer_level_to_value(cluster, cluster->current_level);
}

/* During a timed move (see above; Open Dimmer only) CurrentLevel is the move's end from its
 * start. A Stop, Move or Step in it acts on where the light is: CurrentLevel becomes that
 * level. Returns 1 while such a move runs. */
static uint8_t dimmer_fade_settle(zigbee_dimmer_cluster *cluster) {
    uint32_t now = hal_millis();

    if (!cluster->fade_ms || now - cluster->fade_at >= cluster->fade_ms)
        return 0;

    dimmer_set_current_level(cluster,
                             dimmer_value_to_level(cluster, dimmer_fade_position(cluster, now)));
    return 1;
}

/* A lit-to-lit level change without a time with a brightness fade time set: that time for the
 * whole range, so a part of it for this change (ms = time * |to - from| / range in MCU values,
 * from where the light is, dimmer_fade_position(); the range is the channel's min..max as the
 * levels map to it, 10..1000 without scaling); at least 1 ms for any change, none for no
 * change. No overflow: the time is at most 6553.4 s, and (time / range) * d + (time % range) *
 * d / range stays below 2^32 with d <= range <= 990. */
static uint32_t dimmer_fade_time_ms(const zigbee_dimmer_cluster *cluster, uint32_t from,
                                    uint32_t to) {
    uint32_t full = (uint32_t)cluster->brightness_fade_time * 100u;
    uint32_t span = dimmer_scaling_active(cluster) ? (uint32_t)(cluster->mcu_max - cluster->mcu_min)
                                                   : TUYA_BRIGHTNESS_MAX - TUYA_BRIGHTNESS_MIN;
    uint32_t d = to > from ? to - from : from - to;

    if (d == 0)
        return DIMMER_TIME_NONE;

    if (d > span)
        d = span;
    uint32_t ms = full / span * d + full % span * d / span;
    return ms ? ms : 1u;
}

/* A level command's time (tt: its ZCL time, NULL: none given) for a change to level (0: off).
 * None while the channel is lit and stays lit takes the channel's brightness fade time when one
 * is set (the MCU's own rate otherwise); from dark (on with a level) and to off the fade
 * settings; a given time wins. */
static void dimmer_send_level_transition(zigbee_dimmer_cluster *cluster, const uint8_t *tt,
                                         uint8_t level) {
    uint32_t ms   = tt ? dimmer_zcl_time_ms(tt) : DIMMER_TIME_NONE;
    uint8_t  lit  = cluster->on && level;
    uint32_t now  = hal_millis();
    uint32_t from = dimmer_fade_position(cluster, now);
    uint32_t to   = level ? dimmer_level_to_value(cluster, level) : 0;

    if (ms == DIMMER_TIME_NONE && lit && cluster->brightness_fade_time != 0xFFFF)
        ms = dimmer_fade_time_ms(cluster, from, to);
    dimmer_send_time(cluster, ms);

    /* Keep the move for the next change's "from" (a timed one to Open Dimmer only). */
    cluster->fade_pending = 0;
    if (lit && ms != DIMMER_TIME_NONE && from != to &&
        dimmer_od_from(cluster, OPEN_DIMMER_TRANSITION_BUILD)) {
        cluster->fade_from    = (uint16_t)from;
        cluster->fade_to      = (uint16_t)to;
        cluster->fade_ms      = (uint16_t)(ms > 0xFFFFu ? 0xFFFFu : ms);
        cluster->fade_at      = now;
        cluster->fade_pending = 1;
    }
}

/* ---- Open Dimmer settings (from OPEN_DIMMER_FEATURES_BUILD) -----------------
 *
 * Its DPs per channel (open-dimmer src/dimmer.h): DP 6 / 12 the countdown, and from 162 on,
 * 162 + 2 * k + channel: k 0 wall switch mode, 1 level after a power cut, 2 identify, 3 start
 * boost, 4 fast press; all values. Written only to such an MCU: to any other one a write of their
 * attributes sends nothing and sets them back to "none". */
#define DIMMER_OD_INPUT          0
#define DIMMER_OD_POWER_LEVEL    1
#define DIMMER_OD_IDENTIFY       2
#define DIMMER_OD_BOOST          3
#define DIMMER_OD_FAST_PRESS     4
#define DIMMER_OD_LAMP_COMP      5      /* 2.2.8: known from its dump (lamp_comp_mcu) */
#define DIMMER_LAMP_COMP_MAX     63     /* levels (the MCU: up to 252 of 1000, steps of 4) */
#define DIMMER_LAMP_COMP_NONE    0xFF
#define DIMMER_CUSTOM_POINTS     17
#define DIMMER_CUSTOM_LEN        (2 * DIMMER_CUSTOM_POINTS)
#define DIMMER_CUSTOM_DP_BASE    174    /* + 3 * channel + part (6 big-endian u16 each) */

/* Both channels' switch options (2.2.8), bits set = off: 0 / 1 the long press from off's rise,
 * 2 / 3 the double tap to full; written as mask << 8 | bits. */
#define DIMMER_SWITCH_OPTS_DP    180
#define DIMMER_DARK_HOLD_NONE    0xFF
typedef char dimmer_custom_curve_takes_any_octet_string[
        (sizeof(((zigbee_dimmer_cluster *) 0)->custom_curve) == 256) ? 1 : -1];
#define DIMMER_COUNTDOWN_MAX     86400u    /* s */
#define DIMMER_INPUT_MODE_MAX    2         /* 0 normal, 1 detached, 2 off */
#define DIMMER_INPUT_NORMAL      0
#define DIMMER_IDENTIFY_MAX      255       /* s */
#define DIMMER_BOOST_MAX         750       /* ms */

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

/* The countdown the module last wrote or the MCU last reported: s from now (0: none). */
static void dimmer_countdown_set(zigbee_dimmer_cluster *cluster, uint32_t s) {
    cluster->countdown     = s;
    cluster->countdown_end = hal_millis() + s * 1000u;
    hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_ON_OFF,
                                        ZCL_ATTR_ONOFF_COUNTDOWN);
}

/* Its whole seconds left, rounded up. */
static uint32_t dimmer_countdown_left(const zigbee_dimmer_cluster *cluster) {
    int32_t ms = (int32_t)(cluster->countdown_end - hal_millis());

    return cluster->countdown && ms > 0 ? ((uint32_t)ms + 999u) / 1000u : 0;
}

static void dimmer_write_countdown(zigbee_dimmer_cluster *cluster, uint32_t s) {
    dimmer_write_tuya_value(dimmer_countdown_dp(cluster), s);
    dimmer_countdown_set(cluster, s);
}

/* StartUpCurrentLevel from the MCU's value: 0 the last level (0xFF, previous); one below the
 * channel's minimum (by more than its steps of 4) is what a "minimum" wrote; else the level. */
static uint8_t dimmer_startup_level(const zigbee_dimmer_cluster *cluster) {
    uint16_t v = cluster->startup_value;

    if (cluster->startup_expect != 0xFFFF && v == cluster->startup_expect)
        return cluster->startup_written; /* what was written, not a guess */

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

/* One byte the Zigbee side keeps for a dimmer; 1 when one is stored. */
static uint8_t dimmer_nv_read(const zigbee_dimmer_cluster *cluster, uint8_t item, uint8_t *value) {
    return cluster->dimmer_idx < NV_LAYOUT_DIMMERS &&
           hal_nvm_read(item, sizeof(*value), value) == HAL_NVM_SUCCESS;
}

/* Its OnLevel, its DefaultMoveRate: 0xFF ("none" in ZCL) when there is none. */
static uint8_t dimmer_nv_load(const zigbee_dimmer_cluster *cluster, uint8_t item) {
    uint8_t value = 0xFF;

    if (!dimmer_nv_read(cluster, item, &value))
        value = 0xFF;
    return value;
}

static void dimmer_nv_store(const zigbee_dimmer_cluster *cluster, uint8_t item, uint8_t *value,
                            uint8_t *saved) {
    if (*value != *saved && cluster->dimmer_idx < NV_LAYOUT_DIMMERS &&
        hal_nvm_write(item, sizeof(*value), value) == HAL_NVM_SUCCESS) {
        *saved = *value;
    }
}

/* Two bytes kept for a dimmer: 0xFFFF when there are none. */
static uint16_t dimmer_nv_load16(const zigbee_dimmer_cluster *cluster, uint8_t item) {
    uint16_t value = 0xFFFF;

    if (cluster->dimmer_idx >= NV_LAYOUT_DIMMERS ||
        hal_nvm_read(item, sizeof(value), (uint8_t *)&value) != HAL_NVM_SUCCESS) {
        value = 0xFFFF;
    }
    return value;
}

static void dimmer_nv_store16(const zigbee_dimmer_cluster *cluster, uint8_t item, uint16_t *value,
                              uint16_t *saved) {
    if (*value != *saved && cluster->dimmer_idx < NV_LAYOUT_DIMMERS &&
        hal_nvm_write(item, sizeof(*value), (uint8_t *)value) == HAL_NVM_SUCCESS) {
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

    cluster->brightness_fade_time =
        dimmer_nv_load16(cluster, NV_ITEM_DIMMER_BRIGHTNESS_FADE_TIME(cluster->dimmer_idx));
    cluster->brightness_fade_time_saved = cluster->brightness_fade_time;

    /* Options: any value is one (0xFF too); none stored is 0, so a 0 needs no write. */
    if (!dimmer_nv_read(cluster, NV_ITEM_DIMMER_LEVEL_OPTIONS(cluster->dimmer_idx), &value))
        value = 0;
    cluster->level_options       = value;
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
 * wall switch's OnLevel (Open Dimmer from OPEN_DIMMER_FEATURES_BUILD only: any
 * other MCU gets exactly what it got before), else OnLevel, else a level set
 * while off for a stock MCU (pending: as it was before the on). */
static uint8_t dimmer_local_on_level(const zigbee_dimmer_cluster *cluster, uint8_t pending) {
    if (dimmer_od_features(cluster) && dimmer_level_set(cluster->on_level_local))
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
 * OptionsMask / OptionsOverride (at payload[at], when there) set it otherwise.
 * Limit on Telink: the SDK's level handler (zcl_level.c, its options check
 * before the app callback) applies the same rule first and is stricter for
 * some mask shapes: it executes only when (mask & override) is exactly 0x01,
 * so a mask that also has bit 1 (CoupleColorTempToLevel) set, or bit 0
 * cleared by a mask with bit 1 while Options has ExecuteIfOff, is dropped
 * there before it reaches us. No dimmer controller sends bit 1. */
static uint8_t dimmer_execute_if_off(const zigbee_dimmer_cluster *cluster, const uint8_t *payload,
                                     uint16_t len, uint16_t at) {
    uint8_t options = cluster->level_options;

    if (len >= at + 2)
        options = (uint8_t)((options & ~payload[at]) | (payload[at + 1] & payload[at]));
    return options & ZCL_LEVEL_OPTIONS_EXECUTE_IF_OFF;
}

/* Every Open Dimmer app keeps a level written while off for its next on: it
 * did from its first Tuya protocol code (open-dimmer 26e5126), in every tag
 * from v2.1.9 (no build, 0) to v2.1.14 (build 400). */
#define OPEN_DIMMER_KEEPS_OFF_LEVEL_BUILD    0

/* While off (ExecuteIfOff): the level the light comes on at, the light stays
 * off. Open Dimmer keeps a level written while off; a stock MCU would forget
 * it, so it is kept here for the next on. */
static void dimmer_set_off_level(zigbee_dimmer_cluster *cluster, uint8_t level) {
    dimmer_set_current_level(cluster, level);
    if (dimmer_od_from(cluster, OPEN_DIMMER_KEEPS_OFF_LEVEL_BUILD)) {
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
 * per step. (2.1.14, build 400, already gives a level change within 1 s of a DP 160 / 161 that
 * time, open-dimmer v2.1.14 apply(): a 300 ms time every 300 ms step is continuous.) */

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

    if (dimmer_holding(cluster, 0)) {
        cluster->move_active = 0; /* a wall switch hold has the level */
        return;
    }

    uint32_t acc  = cluster->move_acc + (uint32_t)cluster->move_rate * DIMMER_RAMP_PERIOD_MS;
    int      step = (int)(acc / 1000u);

    cluster->move_acc = (uint16_t)(acc % 1000u);
    dimmer_fade_settle(cluster); /* a Move during a timed move sets out from where the light is */

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

    static const uint8_t no_time[2] = { 0xFF, 0xFF }; /* the fade settings */
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
         * on (whole seconds, rounded up). ZCL: while on, the longer of the time left and
         * the new one wins, so a shorter one writes nothing; an on time of 0 (or the
         * invalid 0xFFFF) is no time. The off wait time is not used: nothing keeps an On
         * from coming right after the countdown's off. */
        uint16_t on_time = (uint16_t)(payload[1] | (payload[2] << 8));
        uint32_t left    = cluster->on ? dimmer_countdown_left(cluster) : 0;
        uint32_t s       = (on_time + 9u) / 10u;

        dimmer_turn_on_default(cluster);
        if (on_time != 0 && on_time != 0xFFFF && dimmer_od_features(cluster) && s > left)
            dimmer_write_countdown(cluster, s);
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
        if (with_onoff || cluster->on)
            dimmer_send_level_transition(cluster, cmd_payload_len >= 3 ? payload + 1 : NULL,
                                         with_onoff && payload[0] == 0 ? 0
                                         : dimmer_clamp_level(payload[0]));

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

            /* ZCL: start from the minimum level when switching on by moving (in no earlier
             * command's time). */
            dimmer_send_time(cluster, DIMMER_TIME_NONE);
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

        dimmer_fade_settle(cluster); /* during a timed move: from where the light is */
        int base = cluster->current_level;

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
        int     next  = up ? base + payload[1] : base - payload[1];
        uint8_t off   = !up && next < ZCL_LEVEL_MIN && with_onoff;
        uint8_t level = dimmer_clamp_level(next);

        dimmer_send_level_transition(cluster, cmd_payload_len >= 4 ? payload + 2 : NULL,
                                     off ? 0 : level);
        if (off) {
            dimmer_turn_off(cluster);
            break;
        }
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
        /* A timed move of the MCU's stops too: the level the light is at, at once. */
        if (dimmer_fade_settle(cluster)) {
            dimmer_send_time(cluster, 1);
            dimmer_write_level(cluster, cluster->current_level);
        }
        break;

    default:
        return HAL_ZIGBEE_CMD_SKIPPED;
    }
    cluster->fade_pending = 0; /* a timed move's level is written within its command */
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
static uint16_t dimmer_custom_point(const zigbee_dimmer_cluster *cluster, uint8_t i) {
    return (uint16_t)(cluster->custom_curve.data[2 * i] | cluster->custom_curve.data[2 * i + 1] <<
                      8);
}

/* A whole table: 0 first, 10000 last, never falling. */
static uint8_t dimmer_custom_valid(const zigbee_dimmer_cluster *cluster) {
    if (cluster->custom_curve.len != DIMMER_CUSTOM_LEN || dimmer_custom_point(cluster, 0) != 0 ||
        dimmer_custom_point(cluster, DIMMER_CUSTOM_POINTS - 1) != 10000)
        return 0;

    for (uint8_t i = 1; i < DIMMER_CUSTOM_POINTS; i++) {
        if (dimmer_custom_point(cluster, i) < dimmer_custom_point(cluster, i - 1))
            return 0;
    }
    return 1;
}

/* To the MCU in three parts of 6 little-endian points as the attribute has them (the write
 * queue takes 16 bytes; the last part's sixth: 0); none (length 0): a last part of zeros, which
 * leaves its table invalid (it uses led_soft). */
static void dimmer_custom_send(const zigbee_dimmer_cluster *cluster) {
    uint8_t has = cluster->custom_curve.len == DIMMER_CUSTOM_LEN;

    for (uint8_t part = has ? 0 : 2; part < 3; part++) {
        uint8_t b[12] = { 0 };
        if (has)
            memcpy(b, cluster->custom_curve.data + 12 * part,
                   part < 2 ? 12 : DIMMER_CUSTOM_LEN - 24);
        tuya_secondary_mcu_write_dp((uint8_t)(DIMMER_CUSTOM_DP_BASE + 3 * cluster->dimmer_idx +
                                              part),
                                    TUYA_DP_TYPE_RAW, b, sizeof(b));
    }
}

/* The attribute as the MCU has it (all three parts told), or none. */
static void dimmer_custom_show(zigbee_dimmer_cluster *cluster) {
    memcpy(cluster->custom_curve.data, cluster->custom_mcu, DIMMER_CUSTOM_LEN);
    cluster->custom_curve.len = cluster->custom_parts == 7 ? DIMMER_CUSTOM_LEN : 0;
    if (cluster->custom_curve.len && !dimmer_custom_valid(cluster))
        cluster->custom_curve.len = 0;
}

/* A part of the MCU's table (its dump, or its answer to our write). */
static void dimmer_on_custom_part(zigbee_dimmer_cluster *cluster, uint8_t dpid,
                                  const uint8_t *value,
                                  uint16_t len) {
    uint8_t part = (uint8_t)(dpid - DIMMER_CUSTOM_DP_BASE - 3 * cluster->dimmer_idx);

    if (dpid < DIMMER_CUSTOM_DP_BASE + 3 * cluster->dimmer_idx || part > 2 || len != 12)
        return;

    memcpy(cluster->custom_mcu + 12 * part, value, 12);
    cluster->custom_parts |= (uint8_t)(1u << part);
    if (cluster->custom_parts == 7) {
        dimmer_custom_show(cluster);
        hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG,
                                            ZCL_ATTR_BALLAST_CUSTOM_CURVE);
    }
}

/* Its network reset by wall switch presses (2.2.8; DP 181, not saved: 8 at its every start, in its
 * dump): the module's multi-press reset count, written whenever its dump or ack says another. */
#define DIMMER_RESET_PRESSES_DP    181
static uint8_t g_od_reset_presses;   /* the MCU has it (seen in its dump since its start) */

static void dimmer_on_od_value(zigbee_dimmer_cluster *cluster, uint8_t dpid, uint32_t number) {
    uint16_t v = (uint16_t)(number > 0xFFFF ? 0xFFFF : number);

    if (dpid == DIMMER_RESET_PRESSES_DP) {
        g_od_reset_presses = 1;
        if (cluster->dimmer_idx == 0 && number != g_multi_press_reset_count)
            dimmer_write_tuya_value(DIMMER_RESET_PRESSES_DP, g_multi_press_reset_count);
        return;
    }

    if (dpid == DIMMER_SWITCH_OPTS_DP) {
        if (number <= 0x0F) {
            cluster->dark_hold       = (uint8_t) !(number >> cluster->dimmer_idx & 1u);
            cluster->dark_hold_mcu   = cluster->dark_hold;
            cluster->double_full     = (uint8_t) !(number >> (2 + cluster->dimmer_idx) & 1u);
            cluster->double_full_mcu = cluster->double_full;
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
                                                ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_DARK_HOLD);
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
                                                ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_DOUBLE_FULL);
        }
        return;
    }

    if (dpid == dimmer_countdown_dp(cluster)) {
        uint8_t lost = cluster->countdown_lost;

        cluster->countdown_lost = 0;
        if (lost && number == 0 && cluster->on && cluster->countdown) {
            /* The MCU restarted, its light is on again and its countdown gone: what is left
             * of it again, or off when its time has passed meanwhile. */
            uint32_t left = dimmer_countdown_left(cluster);

            if (left)
                dimmer_write_countdown(cluster, left);
            else
                dimmer_turn_off(cluster);
            return;
        }
        /* 0 also when it ran out; a bogus huge one is the MCU's longest. */
        dimmer_countdown_set(cluster,
                             number > DIMMER_COUNTDOWN_MAX ? DIMMER_COUNTDOWN_MAX : number);
        return;
    }

    uint8_t k = (uint8_t)(dpid - dimmer_od_dp(cluster, 0));
    if (dpid < dimmer_od_dp(cluster, 0) || (k & 1) || k > 2 * DIMMER_OD_LAMP_COMP)
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
        if (v != cluster->startup_expect)
            cluster->startup_expect = 0xFFFF; /* changed otherwise: the value tells */
        cluster->startup_value = v;
        dimmer_update_startup_level(cluster);
        break;
    case DIMMER_OD_FAST_PRESS:
        if (number <= 1) {
            cluster->fast_press     = (uint8_t)number;
            cluster->fast_press_mcu = (uint8_t)number;
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
                                                ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_FAST_PRESS);
        }
        break;
    case DIMMER_OD_BOOST:
        cluster->start_boost = v;
        hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG,
                                            ZCL_ATTR_BALLAST_START_BOOST);
        break;
    case DIMMER_OD_LAMP_COMP: {
        /* value of 1000 -> levels of 254 (the MCU keeps steps of 4, about one level) */
        uint32_t lv = (number * ZCL_LEVEL_MAX + TUYA_BRIGHTNESS_MAX / 2) / TUYA_BRIGHTNESS_MAX;
        cluster->lamp_comp     = (uint8_t)(lv > DIMMER_LAMP_COMP_MAX ? DIMMER_LAMP_COMP_MAX : lv);
        cluster->lamp_comp_mcu = cluster->lamp_comp;
        hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG,
                                            ZCL_ATTR_BALLAST_LAMP_COMP);
        break;
    }
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

#ifdef DIMMER_BINDINGS

/* ---- the wall switch's bindings (its own endpoint, 3 / 4 on the ZDMS16-2) -----------------
 *
 * Lights bound to it follow the wall switch. Normal mode (DP 162 / 163 0, also any MCU that does
 * not have it): the channel's on and off by the wall switch go out as On / Off (never Toggle:
 * right also when the channel itself is in a bound group); its hold, which the MCU reports on
 * the way (the factory MCU and Open Dimmer from 2.2.4 every 0.5 s, older Open Dimmer only at
 * the release), as one Move: its direction from the first report (from the second right after
 * the wall switch's on: the level before is the one before the off), its rate measured
 * between the first two (until then the last hold's), again if the direction turns; at its
 * end (Open Dimmer's release, else no report for DIMMER_BIND_END_MS) StopWithOnOff and
 * MoveToLevelWithOnOff(its level, 0.4 s). Never a stream of MoveToLevel: each frame to a group
 * takes a broadcast table entry in every router for seconds. Only the changes taken for the
 * wall switch's (not our commands, their answers, a query's answer, the first report after a
 * boot, the countdown's end), so two units bound to each other cannot loop. Detached mode (Open Dimmer): its gestures
 * drive them: single (and the fast press) Toggle; hold MoveWithOnOff up and Move down by turns
 * (the first up, a single starts again at up); its release StopWithOnOff; double
 * MoveToLevelWithOnOff(254, 0.4 s), full; triple nothing (an action in Zigbee2MQTT only). Off
 * mode: nothing. Without a binding nothing goes out. On / Off / Toggle and MoveToLevel ask for
 * an APS ack (retried if a unicast target misses it; the stack asks none of a group), Move and
 * Stop not (a late retry would move again).
 *
 * Configurable (genOnOffSwitchCfg 0xff12..0xff14, kept in NV): the bind mode, auto (the above,
 * by the wall switch mode), off (nothing), mirror (only following the wall switch, any mode) or
 * remote (only the gestures, also in the normal mode where the channel itself acts too); what a
 * double and a triple press send in remote: nothing, full (the default of a double), off, or a
 * Recall Scene of scene 1 / 2 of the group the Scenes binding goes to (0 if it goes to a
 * device). */
#define DIMMER_GESTURE_SINGLE     1
#define DIMMER_GESTURE_DOUBLE     2
#define DIMMER_GESTURE_TRIPLE     3
#define DIMMER_GESTURE_RELEASE    5
#define DIMMER_GESTURE_PRESS      6
#define DIMMER_INPUT_DETACHED     1
#define DIMMER_BIND_RATE          50   /* levels/s of a hold without a DefaultMoveRate */
#define DIMMER_BIND_FADE          4    /* 1/10 s, of a MoveToLevelWithOnOff */
#define DIMMER_BIND_END_MS        800  /* no level report for this long: the hold has ended */
#define DIMMER_BIND_ON_MS         2000 /* a level this soon after the wall switch's on: its start */
#define DIMMER_BIND_AUTO          0
#define DIMMER_BIND_OFF           1
#define DIMMER_BIND_MIRROR        2
#define DIMMER_BIND_REMOTE        3
#define DIMMER_ACT_NONE           0
#define DIMMER_ACT_FULL           1
#define DIMMER_ACT_OFF            2
#define DIMMER_ACT_SCENE_1        3
#define DIMMER_ACT_SCENE_2        4
static const uint8_t dimmer_bind_default[3] = { DIMMER_BIND_AUTO, DIMMER_ACT_FULL,
                                                DIMMER_ACT_NONE };
static const uint8_t dimmer_bind_max[3] = { DIMMER_BIND_REMOTE, DIMMER_ACT_SCENE_2,
                                            DIMMER_ACT_SCENE_2 };

static void dimmer_add_cluster(hal_zigbee_endpoint *endpoint, uint16_t cluster_id,
                               uint8_t attribute_count, hal_zigbee_attribute *attributes,
                               hal_zigbee_cmd_callback_t cmd_callback);

static void dimmer_bind_send(const zigbee_dimmer_cluster *cluster, uint16_t cluster_id,
                             uint8_t command_id, const uint8_t *payload, uint8_t len) {
    hal_zigbee_cmd cmd = {
        .endpoint            = cluster->bind_endpoint,
        .profile_id          = ZCL_HA_PROFILE,
        .cluster_id          = cluster_id,
        .command_id          = command_id,
        .cluster_specific    =                               1,
        .direction           = HAL_ZIGBEE_DIR_CLIENT_TO_SERVER,
        .disable_default_rsp =                               1,
        .payload             = payload,
        .payload_len         = len,
        .aps_ack             = cluster_id != ZCL_CLUSTER_LEVEL_CONTROL ||
                               command_id == ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    };

    if (cluster->bind_endpoint)
        hal_zigbee_send_cmd_to_bindings(&cmd);
}

static void dimmer_bind_level(zigbee_dimmer_cluster *cluster, uint8_t level) {
    uint8_t p[3] = { level, DIMMER_BIND_FADE, 0 };

    dimmer_bind_send(cluster, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
                     p, 3);
}

/* The bindings follow the wall switch / its gestures drive them (bind mode, wall switch mode). */
static uint8_t dimmer_bind_mirrors(const zigbee_dimmer_cluster *cluster) {
    uint8_t m = cluster->bind_cfg[0];

    return m == DIMMER_BIND_MIRROR ||
           (m == DIMMER_BIND_AUTO && cluster->input_mode_mcu == DIMMER_INPUT_NORMAL);
}

static uint8_t dimmer_bind_remote(const zigbee_dimmer_cluster *cluster) {
    uint8_t m = cluster->bind_cfg[0];

    return m == DIMMER_BIND_REMOTE ||
           (m == DIMMER_BIND_AUTO && cluster->input_mode_mcu == DIMMER_INPUT_DETACHED);
}

/* One out of range is its default again. */
static void dimmer_bind_cfg_check(zigbee_dimmer_cluster *cluster) {
    for (uint8_t i = 0; i < 3; i++) {
        if (cluster->bind_cfg[i] > dimmer_bind_max[i])
            cluster->bind_cfg[i] = dimmer_bind_default[i];
    }
}

/* Written (one at a time, by hand): stored. */
static void dimmer_bind_cfg_store(zigbee_dimmer_cluster *cluster) {
    dimmer_bind_cfg_check(cluster);
    if (cluster->dimmer_idx < NV_LAYOUT_DIMMERS)
        hal_nvm_write(NV_ITEM_DIMMER_BIND(cluster->dimmer_idx), 3, cluster->bind_cfg);
}

/* The settings from NV (else the defaults), as attributes base.. of genOnOffSwitchCfg. */
__attribute__((noinline)) static void dimmer_bind_setup(zigbee_dimmer_cluster *cluster,
                                                        uint8_t base) {
    if (cluster->dimmer_idx >= NV_LAYOUT_DIMMERS ||
        hal_nvm_read(NV_ITEM_DIMMER_BIND(cluster->dimmer_idx), 3, cluster->bind_cfg) !=
        HAL_NVM_SUCCESS) {
        for (uint8_t i = 0; i < 3; i++)
            cluster->bind_cfg[i] = dimmer_bind_default[i];
    }
    dimmer_bind_cfg_check(cluster);
    for (uint8_t i = 0; i < 3; i++) {
        SETUP_ATTR_FOR_TABLE(cluster->switch_type_attr_infos, base + i,
                             ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_BIND_MODE + i,
                             ZCL_DATA_TYPE_ENUM8, ATTR_WRITABLE, cluster->bind_cfg[i]);
    }
}

static uint8_t dimmer_bind_default_rate(const zigbee_dimmer_cluster *cluster) {
    uint8_t rate = cluster->default_move_rate;

    return rate && rate != 0xFF ? rate : DIMMER_BIND_RATE;
}

/* dir 1: MoveWithOnOff up, 2: Move down (not off at the bottom). */
static void dimmer_bind_move(zigbee_dimmer_cluster *cluster, uint8_t dir, uint8_t rate) {
    uint8_t p[2] = { dir == 1 ? ZCL_LEVEL_MOVE_UP : ZCL_LEVEL_MOVE_DOWN, rate };

    dimmer_bind_send(cluster, ZCL_CLUSTER_LEVEL_CONTROL,
                     dir == 1 ? ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF : ZCL_CMD_LEVEL_MOVE, p, 2);
}

/* The wall switch's hold has ended: the bindings stop and take its level. */
static void dimmer_bind_follow_end(void *arg) {
    zigbee_dimmer_cluster *cluster = (zigbee_dimmer_cluster *)arg;

    if (cluster->bind_dir)
        dimmer_bind_send(cluster, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_STOP_WITH_ON_OFF, NULL,
                         0);
    if (cluster->bind_reports && cluster->on)
        dimmer_bind_level(cluster, cluster->current_level);
    cluster->bind_dir     = 0;
    cluster->bind_reports = 0;
}

/* A level of the wall switch's (normal mode): a hold's on the way. */
__attribute__((noinline)) static void dimmer_bind_follow(zigbee_dimmer_cluster *cluster,
                                                         uint8_t was_level) {
    uint32_t now   = hal_millis();
    uint32_t dt    = now - cluster->bind_time;
    uint8_t  level = cluster->current_level;
    uint8_t  dir   = level > was_level ? 1 : 2;
    uint8_t  cur   = cluster->bind_rate ? cluster->bind_rate : dimmer_bind_default_rate(cluster);
    uint8_t  rate  = cur;

    if (cluster->bind_reports == 1 && dt) {
        uint32_t r = (uint32_t)(dir == 1 ? level - was_level : was_level - level) * 1000u / dt;
        rate = r < 1 ? 1 : r > 254 ? 254 : (uint8_t)r;
    }
    if (cluster->bind_reports++ == 0 && cluster->bind_after_on && dt < DIMMER_BIND_ON_MS) {
        /* the hold's start only */
    } else if (dir != cluster->bind_dir || (rate > cur ? rate - cur : cur - rate) > cur / 4) {
        dimmer_bind_move(cluster, dir, rate);
        cluster->bind_dir = dir;
    }
    if (cluster->bind_reports > 2)
        cluster->bind_reports = 2;
    cluster->bind_rate     = rate;
    cluster->bind_after_on = 0;
    cluster->bind_time     = now;
    hal_tasks_unschedule(&cluster->bind_task);
    hal_tasks_schedule(&cluster->bind_task, DIMMER_BIND_END_MS);
}

/* A double / triple press's action (DIMMER_ACT_*). */
static void dimmer_bind_action(zigbee_dimmer_cluster *cluster, uint8_t act) {
    if (act == DIMMER_ACT_FULL) {
        dimmer_bind_level(cluster, ZCL_LEVEL_MAX);
    } else if (act == DIMMER_ACT_OFF) {
        dimmer_bind_send(cluster, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_OFF, NULL, 0);
    } else if (act >= DIMMER_ACT_SCENE_1) {
        uint16_t group = hal_zigbee_bound_group(cluster->bind_endpoint, ZCL_CLUSTER_SCENES);
        uint8_t  p[3]  = { (uint8_t)group, (uint8_t)(group >> 8),
                           (uint8_t)(act - DIMMER_ACT_SCENE_1 + 1) };
        dimmer_bind_send(cluster, ZCL_CLUSTER_SCENES, ZCL_CMD_SCENE_RECALL, p, 3);
    }
}

__attribute__((noinline)) static void dimmer_bind_gesture(zigbee_dimmer_cluster *cluster,
                                                          uint8_t gesture) {
    if (!dimmer_bind_remote(cluster)) {
        if (gesture == DIMMER_GESTURE_RELEASE && cluster->bind_reports) {
            hal_tasks_unschedule(&cluster->bind_task);   /* Open Dimmer: the hold's end */
            dimmer_bind_follow_end(cluster);
        }
        return;
    }

    switch (gesture) {
    case DIMMER_GESTURE_SINGLE:
    case DIMMER_GESTURE_PRESS:
        cluster->bind_hold_down = 0;
        dimmer_bind_send(cluster, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_TOGGLE, NULL, 0);
        break;
    case DIMMER_GESTURE_DOUBLE:
    case DIMMER_GESTURE_TRIPLE:
        dimmer_bind_action(cluster, cluster->bind_cfg[gesture - 1]);
        break;
    case DIMMER_GESTURE_HOLD:
        dimmer_bind_move(cluster, cluster->bind_hold_down ? 2 : 1,
                         dimmer_bind_default_rate(cluster));
        cluster->bind_hold_down ^= 1;
        break;
    case DIMMER_GESTURE_RELEASE:
        dimmer_bind_send(cluster, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_CMD_LEVEL_STOP_WITH_ON_OFF, NULL,
                         0);
        break;
    }
}

void dimmer_cluster_add_switch_endpoint(zigbee_dimmer_cluster *cluster,
                                        hal_zigbee_endpoint *endpoint) {
    endpoint->device_id        = ZCL_HA_DEVICE_ID_DIMMER_SWITCH;
    cluster->bind_endpoint     = endpoint->endpoint;
    cluster->bind_hold_down    = 0;
    cluster->bind_dir          = 0;
    cluster->bind_reports      = 0;
    cluster->bind_rate         = 0;
    cluster->bind_after_on     = 0;
    cluster->bind_task.handler = dimmer_bind_follow_end;
    cluster->bind_task.arg     = cluster;
    hal_tasks_init(&cluster->bind_task);
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_ON_OFF, 0, NULL, NULL);
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_LEVEL_CONTROL, 0, NULL, NULL);
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_SCENES, 0, NULL, NULL);
    for (uint8_t i = 0; i < 3; i++)
        endpoint->clusters[i].is_server = 0; /* clients: commands out */
}

#endif

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

        uint32_t number    = dimmer_decode_tuya_value(value, value_len);
        uint8_t  was_on    = cluster->on;
        uint8_t  was_level = cluster->current_level;

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
#ifdef DIMMER_BINDINGS
            /* The same for an off, not at the end of a countdown (Open Dimmer's own off). */
            uint8_t switched_off = !value[0] && cluster->state_known && cluster->on &&
                                   !(cluster->countdown && dimmer_countdown_left(cluster) == 0) &&
                                   !tuya_secondary_mcu_report_is_passive() &&
                                   !tuya_secondary_mcu_report_acks_write();
            if ((switched_on || switched_off) && dimmer_bind_mirrors(cluster))
                dimmer_bind_send(cluster, ZCL_CLUSTER_ON_OFF,
                                 switched_on ? ZCL_CMD_ONOFF_ON : ZCL_CMD_ONOFF_OFF, NULL, 0);
            if (!value[0])
                cluster->bind_dir = cluster->bind_reports = 0;  /* off ends a hold: no Stop */
            if (switched_on)
                cluster->bind_time = hal_millis();
            cluster->bind_after_on = switched_on;
#endif

            if (!value[0])
                dimmer_stop_move(cluster);
            dimmer_set_on(cluster, value[0]);
            if (!cluster->state_known) {
                cluster->state_known = 1;
                dimmer_reports_known();
                dimmer_light_tell();
            }

            /* Go to the wall switch's OnLevel (else OnLevel) at once, with
            * no round trip through the network: the MCU is fading in to
            * the level it remembers and turns towards this one instead. */
            uint8_t on_at = switched_on ? dimmer_local_on_level(cluster, pending) : 0;
            if (on_at) {
                /* Not in the time of a command just before: Open Dimmer would take it for
                 * this level (up to 2.2.6 for the wall switch's own on as well). */
                dimmer_send_time(cluster, DIMMER_TIME_NONE);
                dimmer_set_current_level(cluster, on_at);
                dimmer_write_level(cluster, on_at);
            }
        } else if (dp_type == TUYA_DP_TYPE_VALUE && dpid == cluster->level_dpid) {
            if (!dimmer_holding(cluster, 1) && (cluster->move_active || cluster->level_holdoff)) {
                /* Our own ramp or fade in progress: the MCU reports where
                 * its smoothing is, not where the light is going. (Not under
                 * a wall switch hold: its level is the finger's.) */
                cluster->held_report_value = number;
                cluster->has_held_report   = 1;
            } else {
                cluster->level_value = number;
                cluster->fade_ms     = 0; /* the MCU's word now */
                dimmer_set_current_level(cluster, dimmer_value_to_level(cluster, number));
#ifdef DIMMER_BINDINGS
                /* The wall switch dimming (the same tests as its on above). */
                if (cluster->current_level != was_level && cluster->on && cluster->state_known &&
                    cluster->bind_endpoint && dimmer_bind_mirrors(cluster) &&
                    !tuya_secondary_mcu_report_is_passive() &&
                    !tuya_secondary_mcu_report_acks_write())
                    dimmer_bind_follow(cluster, was_level);
#endif
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
                   value[0] >= 1 && value[0] <= DIMMER_GESTURE_MAX) {
            /* A hold (normal mode): the finger is in control until its release
             * (dimmer_holding()). A ramp of ours stops, and a holdoff of ours (e.g. the wall
             * switch's OnLevel write before a hold from dark) ends now: the hold's level
             * reports go out as they come. Its release ends a holdoff too: the light is where
             * the finger left it (reported just before the release), whatever we wrote under
             * the hold, also after dimmer_holding() ran out with the finger at rest at the
             * end of the range (no reports there). Any other gesture: the release was lost. */
            uint8_t hold = value[0] == DIMMER_GESTURE_HOLD;

            cluster->hold = 0;
            if ((hold || value[0] == DIMMER_GESTURE_RELEASE) &&
                cluster->input_mode_mcu == DIMMER_INPUT_NORMAL) {
                if (hold) {
                    cluster->hold    = 1;
                    cluster->hold_at = hal_millis();
                    dimmer_stop_move(cluster);
                }
                if (cluster->level_holdoff) {
                    hal_tasks_unschedule(&cluster->holdoff_task);
                    dimmer_holdoff_done(cluster);
                }
            }
            cluster->action_seq   = (uint8_t)((cluster->action_seq + 1) & 0x0F);
            cluster->action_state = (uint16_t)((cluster->action_seq << 4) | value[0]);
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_MULTISTATE_INPUT_BASIC,
                                                ZCL_ATTR_MULTISTATE_INPUT_PRESENT_VALUE);
#ifdef DIMMER_BINDINGS
            dimmer_bind_gesture(cluster, value[0]);
#endif
        } else if (dp_type == TUYA_DP_TYPE_VALUE && dimmer_own_mcu(cluster) &&
                   cluster->dimmer_idx < OPEN_DIMMER_CHANNELS) {
            dimmer_on_od_value(cluster, dpid, number);
        } else if (dp_type == TUYA_DP_TYPE_RAW && dimmer_own_mcu(cluster) &&
                   cluster->dimmer_idx < OPEN_DIMMER_CHANNELS) {
            dimmer_on_custom_part(cluster, dpid, value, value_len);
        }
#endif
        if (cluster->on != was_on || cluster->current_level != was_level)
            DIMMER_SCENE_CHANGED(cluster->endpoint);
    }
}

/* ---- trampolines + registration ------------------------------------------ */

static hal_zigbee_cmd_result_t dimmer_cluster_callback_trampoline(
    uint8_t endpoint, uint16_t cluster_id, uint8_t command_id,
    void *cmd_payload, uint16_t cmd_payload_len) {
    DIMMER_SCENE_CHANGED(endpoint);
    return dimmer_cluster_callback(dimmer_by_endpoint(endpoint),
                                   command_id, cmd_payload, cmd_payload_len);
}

static hal_zigbee_cmd_result_t dimmer_cluster_level_callback_trampoline(
    uint8_t endpoint, uint16_t cluster_id, uint8_t command_id,
    void *cmd_payload, uint16_t cmd_payload_len) {
    DIMMER_SCENE_CHANGED(endpoint);
    return dimmer_cluster_level_callback(dimmer_by_endpoint(endpoint),
                                         command_id, cmd_payload,
                                         cmd_payload_len);
}

#ifdef DIMMER_SCENES
void dimmer_cluster_scene_state(uint8_t endpoint, uint8_t *flags, uint8_t *level) {
    zigbee_dimmer_cluster *cluster = dimmer_by_endpoint(endpoint);

    *flags = 0;
    *level = 0;
    if (cluster == NULL)
        return;

    *flags = (uint8_t)(SCENE_HAS_ONOFF | (cluster->on ? SCENE_ON : 0));
    if (cluster->current_level) {               /* 0: not known yet */
        *flags |= SCENE_HAS_LEVEL;
        *level  = cluster->current_level;
    }
}

/* The writes of the commands that set that state (nothing new for any MCU): an off scene and
 * one on at a level as MoveToLevelWithOnOff (0 / the level, the time), on alone as On, a level
 * alone as MoveToLevel (only a lit channel moves, or one with ExecuteIfOff). */
void dimmer_cluster_scene_recall(uint8_t endpoint, uint8_t flags, uint8_t level,
                                 uint16_t transition) {
    zigbee_dimmer_cluster *cluster = dimmer_by_endpoint(endpoint);
    uint8_t cmd[3] = { 0, (uint8_t)transition, (uint8_t)(transition >> 8) };

    if (cluster == NULL)
        return;

    if ((flags & SCENE_HAS_ONOFF) && !(flags & SCENE_ON)) {
        dimmer_cluster_level_callback(cluster, ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF, cmd, 3);
    } else if (flags & SCENE_HAS_LEVEL) {
        cmd[0] = level;
        dimmer_cluster_level_callback(cluster, (flags & SCENE_HAS_ONOFF)
                                      ? ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF
                                      : ZCL_CMD_LEVEL_MOVE_TO_LEVEL, cmd, 3);
    } else if (flags & SCENE_HAS_ONOFF) {
        dimmer_cluster_callback(cluster, ZCL_CMD_ONOFF_ON, NULL, 0);
    }
}

#endif

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
#define DIMMER_LEVEL_ATTRS    (6 + DIMMER_OD_ATTRS)

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

/* After a restart the stack would report the defaults (off, level 0) at once, before the MCU
 * has told the light's state: a hub takes that as the light going off (and e.g. Adaptive
 * Lighting drops a level set by hand). The reports are held from the start until every dimmer
 * knows its state, plus DIMMER_REPORTS_SETTLE_MS for the rest of the MCU's dump (the level after
 * the on/off), or DIMMER_REPORTS_HOLD_MS at most (an MCU that does not answer). */
#define DIMMER_REPORTS_HOLD_MS      10000
#define DIMMER_REPORTS_SETTLE_MS    300
static hal_task_t dimmer_reports_task;

static void dimmer_reports_release(void *arg) {
    (void)arg;
    hal_zigbee_hold_reports(0, 0);
}

static void dimmer_reports_known(void) {
    for (int i = 0; i < DIMMER_MAX_ENDPOINTS; i++)
        if (dimmer_cluster_by_endpoint[i] && !dimmer_cluster_by_endpoint[i]->state_known)
            return;

    hal_tasks_unschedule(&dimmer_reports_task);
    hal_tasks_schedule(&dimmer_reports_task, DIMMER_REPORTS_SETTLE_MS);
}

void dimmer_cluster_add_to_endpoint(zigbee_dimmer_cluster *cluster,
                                    hal_zigbee_endpoint *endpoint) {
    if (!dimmer_reports_task.handler) {
        dimmer_reports_task.handler = dimmer_reports_release;
        dimmer_reports_task.arg     = NULL;
        hal_tasks_init(&dimmer_reports_task);
        hal_zigbee_hold_reports(1, DIMMER_REPORTS_HOLD_MS);
        tuya_mcu_ota_set_light(TUYA_MCU_OTA_LIGHT_UNKNOWN);  /* no MCU update until known */
    }
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
    cluster->startup_expect  = 0xFFFF;
    cluster->identify_time   = 0;
    cluster->start_boost     = 0;
    cluster->lamp_comp       = DIMMER_LAMP_COMP_NONE;
    cluster->lamp_comp_mcu   = DIMMER_LAMP_COMP_NONE;
    cluster->input_mode      = 0;
    cluster->input_mode_mcu  = 0;
    cluster->fast_press      = 0;
    cluster->fast_press_mcu  = 0;
    cluster->dark_hold       = DIMMER_DARK_HOLD_NONE;
    cluster->dark_hold_mcu   = DIMMER_DARK_HOLD_NONE;
    cluster->double_full     = DIMMER_DARK_HOLD_NONE;
    cluster->double_full_mcu = DIMMER_DARK_HOLD_NONE;
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
    SETUP_ATTR_FOR_TABLE(level_attrs, 5, ZCL_ATTR_LEVEL_BRIGHTNESS_FADE_TIME, ZCL_DATA_TYPE_UINT16,
                         ATTR_WRITABLE, cluster->brightness_fade_time);

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
    SETUP_ATTR_FOR_TABLE(level_attrs, 6, ZCL_ATTR_LEVEL_START_UP_CURRENT_LEVEL,
                         ZCL_DATA_TYPE_UINT8, ATTR_WRITABLE, cluster->startup_level);
    SETUP_ATTR_FOR_TABLE(cluster->ballast_attr_infos, 2, ZCL_ATTR_BALLAST_START_BOOST,
                         ZCL_DATA_TYPE_UINT16, ATTR_WRITABLE, cluster->start_boost);
    SETUP_ATTR_FOR_TABLE(cluster->ballast_attr_infos, 3, ZCL_ATTR_BALLAST_LAMP_COMP,
                         ZCL_DATA_TYPE_UINT8, ATTR_WRITABLE, cluster->lamp_comp);
    SETUP_ATTR_FOR_TABLE(cluster->ballast_attr_infos, 4, ZCL_ATTR_BALLAST_CUSTOM_CURVE,
                         ZCL_DATA_TYPE_OCTET_STR, ATTR_WRITABLE, cluster->custom_curve);
    cluster->ballast_attr_infos[4].size = 0xFFu;   /* the field's most (uint8_t); the buffer has 256 */
    SETUP_ATTR_FOR_TABLE(cluster->switch_type_attr_infos, 1,
                         ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_INPUT_MODE,
                         ZCL_DATA_TYPE_ENUM8, ATTR_WRITABLE, cluster->input_mode);
    SETUP_ATTR_FOR_TABLE(cluster->switch_type_attr_infos, 2,
                         ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_FAST_PRESS,
                         ZCL_DATA_TYPE_BOOLEAN, ATTR_WRITABLE, cluster->fast_press);
    SETUP_ATTR_FOR_TABLE(cluster->switch_type_attr_infos, 3,
                         ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_DARK_HOLD,
                         ZCL_DATA_TYPE_BOOLEAN, ATTR_WRITABLE, cluster->dark_hold);
    SETUP_ATTR_FOR_TABLE(cluster->switch_type_attr_infos, 4,
                         ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_DOUBLE_FULL,
                         ZCL_DATA_TYPE_BOOLEAN, ATTR_WRITABLE, cluster->double_full);
    SETUP_ATTR_FOR_TABLE(cluster->identify_attr_infos, 0, ZCL_ATTR_IDENTIFY_TIME,
                         ZCL_DATA_TYPE_UINT16, ATTR_WRITABLE, cluster->identify_time);
#endif

    dimmer_add_cluster(endpoint, ZCL_CLUSTER_ON_OFF, DIMMER_ONOFF_ATTRS, cluster->attr_infos,
                       dimmer_cluster_callback_trampoline);
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_LEVEL_CONTROL, DIMMER_LEVEL_ATTRS, level_attrs,
                       dimmer_cluster_level_callback_trampoline);
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG, 2 + 3 * DIMMER_OD_ATTRS,
                       cluster->ballast_attr_infos, NULL);
#ifdef DIMMER_BINDINGS
    dimmer_bind_setup(cluster, 1 + 4 * DIMMER_OD_ATTRS);
#define DIMMER_BIND_ATTRS    3
#else
#define DIMMER_BIND_ATTRS    0
#endif
    dimmer_add_cluster(endpoint, ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
                       1 + 4 * DIMMER_OD_ATTRS + DIMMER_BIND_ATTRS,
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
    if (tuya_secondary_mcu_add_dp_listener(dimmer_cluster_on_dp_report) != 0) {
        printf("dimmer_cluster_on_dp_report: not registered, its DPs are not taken\r\n");
    }

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
                dimmer_write_countdown(cluster, cluster->countdown > DIMMER_COUNTDOWN_MAX
                                                ? DIMMER_COUNTDOWN_MAX : cluster->countdown);
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
        } else if (attribute_id == ZCL_ATTR_LEVEL_BRIGHTNESS_FADE_TIME) {
            dimmer_nv_store16(cluster, NV_ITEM_DIMMER_BRIGHTNESS_FADE_TIME(cluster->dimmer_idx),
                              &cluster->brightness_fade_time,
                              &cluster->brightness_fade_time_saved);
            hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_LEVEL_CONTROL,
                                                ZCL_ATTR_LEVEL_BRIGHTNESS_FADE_TIME);
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
            uint32_t v = l == ZCL_START_UP_CURRENT_LEVEL_PREVIOUS ? 0
                         : l == ZCL_START_UP_CURRENT_LEVEL_MINIMUM ? TUYA_BRIGHTNESS_MIN
                         : dimmer_level_to_value(cluster, l);
            /* The MCU keeps it in steps of 4 within 10..1000 (its answer says so). */
            cluster->startup_written = l;
            cluster->startup_expect  = (uint16_t)(v ? ((v >
                                                        TUYA_BRIGHTNESS_MAX ? TUYA_BRIGHTNESS_MAX
                                                             : v) + 2) / 4 * 4 : 0);
            dimmer_write_tuya_value(dimmer_od_dp(cluster, DIMMER_OD_POWER_LEVEL), v);
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
        } else if (attribute_id == ZCL_ATTR_BALLAST_LAMP_COMP) {
            if (!dimmer_od_features(cluster) || cluster->lamp_comp_mcu == DIMMER_LAMP_COMP_NONE) {
                cluster->lamp_comp = DIMMER_LAMP_COMP_NONE;   /* an MCU without it: none */
                break;
            }
            if (cluster->lamp_comp > DIMMER_LAMP_COMP_MAX)
                cluster->lamp_comp = DIMMER_LAMP_COMP_MAX;
            dimmer_write_tuya_value(dimmer_od_dp(cluster, DIMMER_OD_LAMP_COMP),
                                    ((uint32_t)cluster->lamp_comp * TUYA_BRIGHTNESS_MAX +
                                     ZCL_LEVEL_MAX / 2) / ZCL_LEVEL_MAX);
        } else if (attribute_id == ZCL_ATTR_BALLAST_CUSTOM_CURVE) {
            /* a whole valid table, or length 0 (none), to an MCU that has it (2.2.8: it told its
             * lamp compensation); anything else: shown again as the MCU has it */
            if ((cluster->custom_curve.len && !dimmer_custom_valid(cluster)) ||
                !dimmer_od_features(cluster) || cluster->lamp_comp_mcu == DIMMER_LAMP_COMP_NONE) {
                dimmer_custom_show(cluster);
                break;
            }
            dimmer_custom_send(cluster);   /* its answers show it */
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
        } else if (attribute_id == ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_FAST_PRESS) {
            if (!dimmer_od_features(cluster)) {
                cluster->fast_press = cluster->fast_press_mcu;
                break;
            }
            dimmer_write_tuya_value(dimmer_od_dp(cluster, DIMMER_OD_FAST_PRESS),
                                    cluster->fast_press ? 1 : 0);
        } else if (attribute_id == ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_DARK_HOLD ||
                   attribute_id == ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_DOUBLE_FULL) {
            /* only to an MCU that has said it (its dump): an older one would not take it. Its
             * bit of DP 180 alone (the mask), set for "off". */
            uint8_t  dbl = attribute_id == ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_DOUBLE_FULL;
            uint8_t *val = dbl ? &cluster->double_full : &cluster->dark_hold;
            uint8_t  mcu = dbl ? cluster->double_full_mcu : cluster->dark_hold_mcu;
            uint8_t  bit = (uint8_t)(1u << (cluster->dimmer_idx + (dbl ? 2u : 0u)));
            if (!dimmer_od_features(cluster) || mcu == DIMMER_DARK_HOLD_NONE || *val > 1) {
                *val = mcu;
                break;
            }
            dimmer_write_tuya_value(DIMMER_SWITCH_OPTS_DP, (uint32_t)bit << 8 | (*val ? 0u : bit));
        }
#endif
#ifdef DIMMER_BINDINGS
        else if (attribute_id >= ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_BIND_MODE &&
                 attribute_id <= ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_TRIPLE_ACTION) {
            dimmer_bind_cfg_store(cluster);
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

void dimmer_cluster_reset_presses_changed(void) {
#ifdef SECONDARY_MCU_IMAGE
    if (g_od_reset_presses)
        dimmer_write_tuya_value(DIMMER_RESET_PRESSES_DP, g_multi_press_reset_count);
#endif
}

void dimmer_cluster_mcu_restarted(void) {
#ifdef SECONDARY_MCU_IMAGE
    g_od_reset_presses = 0;
#endif
    for (int i = 0; i < DIMMER_MAX_ENDPOINTS; i++) {
        zigbee_dimmer_cluster *cluster = dimmer_cluster_by_endpoint[i];
        if (cluster == NULL)
            continue;

        /* The MCU lost a running countdown: dimmer_on_od_value() writes its rest again. */
        if (cluster->countdown)
            cluster->countdown_lost = 1;
#ifdef SECONDARY_MCU_IMAGE
        /* Its dump tells it again; an older MCU (a downgrade) does not have it. */
        cluster->custom_parts = 0;     /* its dump tells it again */
        if (cluster->custom_curve.len) {
            cluster->custom_curve.len = 0;
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG,
                                                ZCL_ATTR_BALLAST_CUSTOM_CURVE);
        }
        if (cluster->dark_hold != DIMMER_DARK_HOLD_NONE) {
            cluster->dark_hold   = cluster->dark_hold_mcu = DIMMER_DARK_HOLD_NONE;
            cluster->double_full = cluster->double_full_mcu = DIMMER_DARK_HOLD_NONE;
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
                                                ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_DOUBLE_FULL);
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
                                                ZCL_ATTR_ONOFF_CONFIGURATION_DIMMER_DARK_HOLD);
        }
        if (cluster->lamp_comp != DIMMER_LAMP_COMP_NONE) {
            cluster->lamp_comp = cluster->lamp_comp_mcu = DIMMER_LAMP_COMP_NONE;
            hal_zigbee_notify_attribute_changed(cluster->endpoint,
                                                ZCL_CLUSTER_LIGHTING_BALLAST_CONFIG,
                                                ZCL_ATTR_BALLAST_LAMP_COMP);
        }
        hal_tasks_unschedule(&cluster->identify_task);
        if (cluster->identify_time) {
            cluster->identify_time = 0;
            hal_zigbee_notify_attribute_changed(cluster->endpoint, ZCL_CLUSTER_IDENTIFY,
                                                ZCL_ATTR_IDENTIFY_TIME);
        }
#endif
    }
}

void dimmer_cluster_callback_attr_write_trampoline(uint8_t endpoint,
                                                   uint16_t cluster_id,
                                                   uint16_t attribute_id) {
    dimmer_cluster_on_write_attr(dimmer_by_endpoint(endpoint), cluster_id, attribute_id);
}
