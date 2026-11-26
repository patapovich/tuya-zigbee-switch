#ifdef HK_DIMMER

#include "hk_dimmer.h"

#include <stddef.h>
#include <string.h>

#include "base_components/button.h"
#include "device_config/device_params_nv.h"
#include "device_config/nvm_items.h"
#include "hal/gpio.h"
#include "hal/printf_selector.h"
#include "hal/nvm.h"
#include "hal/system.h"
#include "hal/tasks.h"
#include "hal/timer.h"
#include "hal/uart.h"
#include "hal/zigbee.h"
#include "zigbee/cluster_common.h"
#include "zigbee/consts.h"
#include "zigbee/hk_fader.h"
#include "zigbee/tuya_mcu_diag.h"
#include "zigbee/tuya_secondary_mcu.h"

/* Debug builds only (MCU_FLASHER_DUMP: make MCU_DUMP=1, and the stub): what went to the dimming
 * MCU and why, in the link's event log (Basic 0xff41..0xff45, helper_scripts/decode_mcu_diag.py),
 * so a bench unit can show what its wire had when a lamp did what the frames do not explain. The
 * frames (HK_DIAG_FRAME: id = the channel, 1 or 2, | 0x80 when the UART did not take it; value =
 * level << 16 | ramp; not a hold's own steps: they would push everything else out of the log),
 * and the events (HK_DIAG_EVENT, id = what << 4 | the channel's index, 0 L1, 1 L2): HK_EV_RAMP_LEFT
 * a change at once because the MCU's own ramp runs (value: its ms left), HK_EV_AT_ONCE_ON / _OFF a
 * change at once by the fade settings (the level, 0), HK_EV_FADER the change to the fader (the
 * network's time, HK_TIME_NONE: none), HK_EV_FADER_AT where the fader is then (current << 16 |
 * target), HK_EV_HOLD_BEGIN (the way, as a byte, << 16 | from where), HK_EV_HOLD_END (the level
 * it took). A release build compiles none of it, its arguments included. */
#ifdef MCU_FLASHER_DUMP
#define HK_DIAG_FRAME    14 /* the log's kinds (tuya_mcu_diag_kind_t: the next two free) */
#define HK_DIAG_EVENT    15
#define HK_TRACE(what, ch, value) \
        tuya_mcu_diag_log(HK_DIAG_EVENT, (uint8_t)((what) << 4 | (ch)), (uint32_t)(value))
#else
#define HK_TRACE(what, ch, value)    ((void)0)
#endif
#define HK_EV_RAMP_LEFT      1
#define HK_EV_AT_ONCE_ON     2
#define HK_EV_AT_ONCE_OFF    3
#define HK_EV_FADER          4
#define HK_EV_FADER_AT       5
#define HK_EV_HOLD_BEGIN     6
#define HK_EV_HOLD_END       7

/* ---- the virtual MCU's DPs: the factory ZDMS16-2 layout the dimmer endpoints expect ---------- */

#define HK_CHANNELS          2
#define HK_DP_BASE(ch)    (1 + 6 * (ch))
#define HK_DP_ONOFF          0
#define HK_DP_LEVEL          1
#define HK_DP_MIN            2
#define HK_DP_SWITCH_TYPE    3
#define HK_DP_MAX            4
#define HK_DP_COUNTDOWN      5                 /* s, auto-off (Open Dimmer's DP 6 / 12) */
#define HK_DP_POWER_ON       0x0E              /* both channels (written: both; reported: channel 1's) */
/* This firmware's own settings DPs, per channel (device_db dp_attributes). */
#define HK_DP_RAMP(ch)           (0x70 + (ch)) /* the dimming MCU's own ramp (instant changes) */
#define HK_DP_FADE_ON(ch)        (0x72 + (ch)) /* ms, dark to full */
#define HK_DP_FADE_OFF(ch)       (0x74 + (ch)) /* ms, full to dark */
#define HK_DP_HOLD(ch)           (0x76 + (ch)) /* ms, a wall switch hold over the channel's range */
#define HK_DP_CURVE(ch)          (0x78 + (ch)) /* enum: the dimming curve, as Open Dimmer's */

/* Open Dimmer's DPs it takes (dimmer_cluster: an Open Dimmer of build HK_OD_BUILD, 2.2.0, with
 * 2.2.8's lamp compensation and switch options; their meaning as open-dimmer src/dimmer.h has
 * it). */
#define HK_DP_POWER_ON_CH(ch)    (0x67 + (ch)) /* enum: the channel's power-on (103 / 104) */
#define HK_DP_ACTION(ch)         (0x69 + (ch)) /* enum, reported only: a wall switch gesture */
#define HK_DP_OD_BASE        162               /* + 2 * k + channel, values: */
#define HK_OD_INPUT          0                 /*   the wall switch mode (INPUT_*) */
#define HK_OD_POWER_LEVEL    1                 /*   the level after a power cut (0: the last one) */
#define HK_OD_IDENTIFY       2                 /*   identify: s the channel blinks (not saved) */
#define HK_OD_BOOST          3                 /*   start boost, ms (steps of 50) */
#define HK_OD_FAST_PRESS     4                 /*   fast press (detached momentary: gesture 6) */
#define HK_OD_COMP           5                 /*   lamp compensation (of 1000, steps of 4; 2.2.8) */
#define HK_OD_KEPT           6                 /* (from 174 on: its custom curve's, none here) */
#define HK_DP_OD(k, ch)    (HK_DP_OD_BASE + 2 * (k) + (ch))

/* Both channels' wall switch options (2.2.8), bits set = off: 0 / 1 a long press from off rises
 * from the minimum, 2 / 3 a double tap goes to full; written as mask << 8 | bits. */
#define HK_DP_SWITCH_OPTS    180
#define HK_OPT_NO_RISE(ch)      (1u << (ch))
#define HK_OPT_NO_DOUBLE(ch)    (4u << (ch))

#define VALUE_MIN            10             /* levels 10..1000, as the factory MCU */
#define VALUE_MAX            1000
#define VALUE_SPAN           (VALUE_MAX - VALUE_MIN)

#define SWITCH_TOGGLE        0    /* every change of a latching switch toggles */
#define SWITCH_STATE         1    /* closed on, open off */
#define SWITCH_MOMENTARY     2    /* a press toggles, a hold dims */

#define POWER_ON_OFF         0
#define POWER_ON_ON          1
#define POWER_ON_PREVIOUS    2

#define INPUT_NORMAL         0    /* the wall switch drives the light */
#define INPUT_DETACHED       1    /* it only reports its gestures (the light is the network's) */
#define INPUT_OFF            2    /* ignored */

/* The version the virtual MCU tells (1.0.0): 1.x keeps the module on its factory-MCU path. */
#define HK_VERSION           0x40

/* ---- Tuya link framing (module <-> MCU) ---------------------------------------------------- */

#define TUYA_HEADER_LEN       8   /* 55 AA 02 seq seq cmd len len */
#define TUYA_VERSION          0x02
#define CMD_NETWORK_STATUS    0x02
#define CMD_RESET_NETWORK     0x03
#define CMD_WRITE             0x04
#define CMD_REPORT_PASSIVE    0x05
#define CMD_REPORT            0x06
#define CMD_MCU_VERSION       0x0B
#define CMD_QUERY_ALL_DP      0x28
#define DP_TYPE_BOOL          0x01
#define DP_TYPE_VALUE         0x02
#define DP_TYPE_ENUM          0x04

/* ---- the dimming MCU (docs/ts110e_hk_protocol.md) ------------------------------------------ */

#define HK_RAMP_SMOOTH           0x05DC /* the factory firmware's ramp for every change (1500) */
#define HK_BOOT_DELAY_MS         1500   /* the dimming MCU starts with the module: let it come up */
#define HK_RESEND_MS             4000   /* and send everything once more after this */
#define HK_FRAME_GAP_MS          20     /* a 12-byte frame takes 12.5 ms at 9600 baud */
#define HK_RANGE_MIN             3      /* the factory range: 1 % .. 100 % of 255, rounded */
#define HK_RANGE_MAX             255

/* The dimming MCU forgets its levels at a power loss of its own and never says so: a channel
 * whose level went out this long ago (and whose ramp has run out) gets it again, at ramp 0. */
#define HK_KEEPALIVE_MS          60000u

/* A change the module writes in parts (a time, the on, then its level) goes out whole: no frame
 * while its writes still wait for the virtual MCU (a pass or two), for this long at most. */
#define HK_WRITES_WAIT_MS        40u

/* A frame the UART did not take (wedged; a failed write spends its whole timeout, about 29 ms)
 * goes again HK_TX_RETRY_MS later; every HK_TX_REINIT_FAILURES in a row the UART is set up
 * again, and from the first one on the tries are HK_TX_STUCK_WAIT_MS apart (one quick try
 * after it), so a dead UART never starves the main loop. */
#define HK_TX_RETRY_MS           200u
#define HK_TX_REINIT_FAILURES    3
#define HK_TX_STUCK_WAIT_MS      5000u
#define HK_TX_FAILURES_MAX       (80 * HK_TX_REINIT_FAILURES)

/* ---- fades (this module's own: frames with ramp 0, a frame gap apart) ---------------------- */

#define HK_FADE_ON_MS       1000    /* defaults: dark to full */
#define HK_FADE_OFF_MS      1500    /* full to dark (about the factory ramp) */
#define HK_HOLD_MS          4000    /* a wall switch hold over the channel's range */
#define HK_FADE_MAX_MS      60000
#define HK_TIME_VALID_MS    1000    /* a transition time from the network: its changes of 1 s */
#define HK_TIME_NONE        0xFFFFFFFFu
#define HK_BOOST_STEP_MS    50      /* the start boost in steps of this (Open Dimmer's) ... */
#define HK_BOOST_MAX_MS     750     /* ... up to this */
#define HK_SOFT_END_PCT     15      /* the slow zones' defaults, % of min..max (Open Dimmer's */
#define HK_HOLD_END_PCT     25      /* DIMMER_SOFT_END_PCT / DIMMER_HOLD_END_PCT) */
#define HK_COMP_MAX         252     /* the lamp compensation's DP, of 1000 (kept / 4) */

/* ---- Open Dimmer 2.2.0's settings (open-dimmer src/dimmer.h, dimmer.c; keep in sync) ------- */

#define HK_COUNTDOWN_MAX          86400u /* s */
#define HK_IDENTIFY_MAX_S         255
#define HK_IDENTIFY_SHIFT         9      /* a blink: dark / full by bit 9 of the ms (0.5 s each) */
#define HK_POWER_LEVEL4_MAX       250    /* the level after a power cut is kept / 4 */
#define HK_RESET_WINDOW_MS        10000u /* the presses of a network reset: within this of the first */
#define HK_RESET_PRESSES_LEAST    8      /* as few as the stock MCU (1..7 act as 8), 0: never */
#define HK_RESET_BLINK_S          3      /* ... then the switch's channel blinks this long first */

/* The way a lit hold goes (Open Dimmer's dimmer.c dimmer_hold_begin / learn_verdict, keep in
 * sync): a hold that starts within HOLD_SEQ_MS of the last one's end goes the other way (a
 * correction); any other one takes the way learnt for the band of min..max it starts in
 * (HOLD_BANDS), at first the far end. A learnt hold let go within HOLD_WRONG_MS and turned round
 * at once went the wrong way: the band learns the other one; a band the last verdict confirmed
 * (RAM only) needs two such misses. The learnt bits (ch * HOLD_BANDS + band: that band goes
 * against the far end) are saved (g_nv.lrn_ways). */
#define HOLD_BANDS                4u
#define HOLD_SEQ_MS               2000u
#define HOLD_WRONG_MS             1200u

/* ---- inputs --------------------------------------------------------------------------------- */

#define HK_LONG_PRESS_MS     400    /* a momentary switch held this long dims (Open Dimmer's) */
#define HK_HOLD_REPORT_MS    500u   /* a hold's level goes to the module this often (as Open
                                     * Dimmer's and the factory MCU's) */
#define HK_HOLD_CAP_MS       2000u  /* + 2 x hold_time from the press: a switch stuck closed */
#define HK_RESET_PRESS_MS    3000   /* the module button held this long and let go: a re-pair */
#define HK_LED_BLINK_MS      500
#define HK_NV_DELAY_MS       5000   /* settings and the state are kept this long after a change */
#define HK_NV_LIT_MS         60000u /* a level written while lit: this long after the first one */
#define HK_NV_BUSY_MS        500u   /* not while a fade or a hold runs: a flash write stalls it */
#define HK_NV_RETRY_MS       1000u  /* a write that failed: again, HK_NV_TRIES in all */
#define HK_NV_TRIES          3
#define HK_NV_LAYOUT         5      /* 2: + ramp, 3: + fade and hold times, 4: + curve, 5: + Open
                                     * Dimmer's settings; older taken over */

/* The Telink NV read takes the newest item whose size is a multiple of the length asked for
 * and copies all of it: a later layout of up to this size read by this build stays in bounds. */
#define HK_NV_READ_MAX       128

/* A byte kept through a reset of the module alone, not a power cut (hal_system_kept_*): this
 * marker and the channels' on bits (bits 0, 1). Found at the start: a warm start, the light
 * comes back as it was (the dimming MCU kept driving it), not by the power-on setting. */
#define HK_KEPT_MAGIC        0xA8
#define HK_KEPT_MARK         0xFC

/* ---- dimming curves: Open Dimmer's (open-dimmer src/curves.h, dim_core.c dim_curve /
 * dim_output), the output a conduction time 0..10000 of the half cycle, here the dimming
 * MCU's 0..255 (taken as linear in the conduction time) ------------------------------------- */

#define CURVE_LINEAR          0
#define CURVE_LED             1
#define CURVE_LED_SOFT        2
#define CURVE_INCANDESCENT    3
#define CURVE_LED_DEEP        4
#define CURVE_LED_DEEPER      5
#define CURVE_CUSTOM          6
#define CURVE_POINTS          33
#define CURVE_OUT_MAX         10000
#define CUSTOM_POINTS         17
#define CUSTOM_LEN            (2 * CUSTOM_POINTS)   /* little-endian u16, as Open Dimmer's attribute */

/* Index curve - 1: linear has no table. */
static const uint16_t k_curves[CURVE_CUSTOM - 1][CURVE_POINTS] = {
    /* led */
    { 0,  35,   69,  105,  148,  203,  269,  348,  442,  550,  676,
      819, 981, 1163, 1367, 1592, 1842, 2116, 2416, 2744, 3099, 3484,
      3900, 4347, 4828, 5342, 5892, 6479, 7103, 7766, 8469, 9213, 10000 },
    /* led_soft */
    { 0,  39,  118,  227,  359,  513,  687,  879, 1088, 1314, 1555,
      1811, 2082, 2366, 2664, 2975, 3299, 3635, 3983, 4343, 4714, 5097,
      5491, 5896, 6311, 6737, 7173, 7620, 8076, 8543, 9019, 9505, 10000 },
    /* incandescent */
    { 0, 811, 1024, 1178, 1326, 1476, 1627, 1779, 1933, 2089, 2247,
      2408, 2571, 2737, 2907, 3080, 3257, 3439, 3626, 3819, 4019, 4227,
      4444, 4673, 4914, 5171, 5449, 5753, 6094, 6486, 6966, 7627, 10000 },
    /* led_deep */
    { 0,   2,    6,   11,   18,   29,   44,   65,   93,  129,  176,
      234, 307, 397, 505, 635, 790, 973, 1188, 1437, 1725, 2057,
      2435, 2866, 3354, 3905, 4523, 5215, 5986, 6844, 7794, 8844, 10000 },
    /* led_deeper */
    { 0,   0,    0,    1,    2,    4,    7,   12,   19,   30,   46,
      67, 96, 135, 187, 254, 339, 448, 584, 753, 960, 1214,
      1521, 1890, 2331, 2854, 3472, 4198, 5045, 6031, 7173, 8489, 10000 },
};

/* The custom table of a channel as its ZCL attribute holds it (any length can be written:
 * only a whole valid table is taken). */
typedef struct {
    uint8_t len;
    uint8_t data[255];
} hk_custom_attr_t;

typedef struct {
    uint8_t len[2];
    uint8_t data[2][CUSTOM_LEN];
} hk_custom_nv_t;

typedef struct {
    uint8_t  on;
    uint8_t  switch_type;
    uint16_t value;
    uint16_t min;
    uint16_t max;
} hk_channel_nv_t;

typedef struct {
    uint8_t         layout;
    uint8_t         power_on;           /* channel 1's (layouts 1..4: both channels') */
    hk_channel_nv_t ch[HK_CHANNELS];
    /* layout 2 */
    uint16_t        ramp[HK_CHANNELS];
    /* layout 3 */
    uint16_t        fade_on[HK_CHANNELS];
    uint16_t        fade_off[HK_CHANNELS];
    uint16_t        hold[HK_CHANNELS];
    /* layout 4 */
    uint8_t         curve[HK_CHANNELS];
    /* layout 5: Open Dimmer's settings (as its record keeps them) */
    uint8_t         power_on_2;                /* channel 2's power-on */
    uint8_t         input_mode[HK_CHANNELS];
    uint8_t         power_level4[HK_CHANNELS]; /* the level after a power cut / 4 (0: the last) */
    uint8_t         boost50[HK_CHANNELS];      /* the start boost / 50 ms (0: none) */
    uint8_t         fast_press[HK_CHANNELS];
    uint8_t         soft_end[HK_CHANNELS];     /* % of min..max: the off fade's slow end */
    uint8_t         hold_end[HK_CHANNELS];     /* % of min..max: a hold's slow zone */
    uint8_t         comp4[HK_CHANNELS];        /* the lamp compensation / 4 (0: none) */
    uint8_t         switch_opts;               /* DP 180's bits (HK_OPT_*: set = off) */
    uint8_t         lrn_ways;                  /* the hold ways learnt (HOLD_BANDS) */
    uint8_t         spare;                     /* 0: no padding, every byte compared (nv_store) */
} hk_nv_t;

/* The item layout 4 wrote (the deployed units'): its fields, and the tail padding of the target
 * (tc32 aligns a struct to 4: 40 bytes there, the stub's 36), which nv_load takes too. */
typedef struct {
    uint8_t         layout;
    uint8_t         power_on;
    hk_channel_nv_t ch[HK_CHANNELS];
    uint16_t        ramp[HK_CHANNELS];
    uint16_t        fade_on[HK_CHANNELS];
    uint16_t        fade_off[HK_CHANNELS];
    uint16_t        hold[HK_CHANNELS];
    uint8_t         curve[HK_CHANNELS];
} hk_nv_v4_t;

/* The items of the earlier layouts: everything before what came later (layouts 1..3 ended on a
 * word: no padding). */
#define HK_NV_V1_SIZE    offsetof(hk_nv_t, ramp)
#define HK_NV_V2_SIZE    offsetof(hk_nv_t, fade_on)
#define HK_NV_V3_SIZE    offsetof(hk_nv_t, curve)
#define HK_NV_V4_SIZE    sizeof(hk_nv_v4_t)
typedef char   hk_nv_fits_the_read_buffer[sizeof(hk_nv_t) <= HK_NV_READ_MAX ? 1 : -1];
typedef char   hk_nv_v4_is_the_start_of_layout_5[
        offsetof(hk_nv_v4_t, curve) == offsetof(hk_nv_t, curve) ? 1 : -1];

/* Read by the size of each layout, the largest first (nv_load): the newest item is found by its
 * own size (the Telink read takes an item whose size is a multiple of the length asked for, so a
 * larger length never takes a smaller item). Layout 5 is no multiple of layout 3 or 4 (an older
 * build does not take it for its own) and has no padding: every byte is compared (nv_store). */
typedef char   hk_nv_sizes_tell_the_layouts[
        (sizeof(hk_nv_t) > HK_NV_V4_SIZE && HK_NV_V4_SIZE > HK_NV_V3_SIZE &&
         HK_NV_V3_SIZE > HK_NV_V2_SIZE && HK_NV_V2_SIZE > HK_NV_V1_SIZE &&
         sizeof(hk_nv_t) % HK_NV_V4_SIZE != 0 && sizeof(hk_nv_t) % HK_NV_V3_SIZE != 0 &&
         sizeof(hk_nv_t) == offsetof(hk_nv_t, spare) + 1) ? 1 : -1];

/* ---- gestures: Open Dimmer's (open-dimmer src/gesture_core.h / gesture_core.c, keep in sync)
 * of a momentary wall switch, fed with its events as Open Dimmer's input_core.h has them
 * (button_t's press, release, long press and the release after it). The light does not wait
 * for them: it acts on each press as before, and the gesture goes to the module on top of it,
 * after what the light did (DP HK_DP_ACTION, active, as Open Dimmer's DP 105 / 106; never in
 * the dump): the wall switch's bindings take it (bind_mode remote: a single toggles them, a
 * double / triple sends its action, a hold moves them, its release stops them; mirror: the
 * release ends the follow of a hold at once).
 *   single / double / triple: that many short presses, each started within GESTURE_GAP_MS of
 *     the previous release; reported GESTURE_GAP_MS after the last release (more than three:
 *     nothing, it was not meant).
 *   hold: the press became a hold (any short presses before it are dropped).
 *   release: the end of a hold. */
#define GESTURE_GAP_MS                 400
#define GESTURE_NONE                   0
#define GESTURE_SINGLE                 1
#define GESTURE_TRIPLE                 3
#define GESTURE_HOLD                   4
#define GESTURE_RELEASE                5
#define GESTURE_PRESS                  6    /* the fast press (detached momentary): at once */
#define INPUT_EV_PRESS                 1
#define INPUT_EV_RELEASE               2    /* after a short press (no hold) */
#define INPUT_EV_HOLD                  3
#define INPUT_EV_RELEASE_AFTER_HOLD    4

typedef struct {
    uint8_t  count;       /* short presses in this sequence */
    uint8_t  down;        /* pressed now */
    uint8_t  pending;     /* a sequence waits for its gap to end */
    uint32_t release_ms;  /* time of the last release */
} gesture_t;

static void gesture_init(gesture_t *g) {
    g->count      = 0;
    g->down       = 0;
    g->pending    = 0;
    g->release_ms = 0;
}

/* An input event (INPUT_EV_*); a gesture that ends now (hold, release). */
static uint8_t gesture_event(gesture_t *g, uint8_t input_ev, uint32_t now) {
    switch (input_ev) {
    case INPUT_EV_PRESS:
        if (!g->pending) {
            g->count = 0;
        }
        if (g->count < 0xFF) {
            g->count++;
        }
        g->down    = 1;
        g->pending = 1;
        return GESTURE_NONE;

    case INPUT_EV_RELEASE:
        g->down       = 0;
        g->release_ms = now;
        return GESTURE_NONE;

    case INPUT_EV_HOLD:
        g->pending = 0;
        g->count   = 0;
        return GESTURE_HOLD;

    case INPUT_EV_RELEASE_AFTER_HOLD:
        g->down    = 0;
        g->pending = 0;
        return GESTURE_RELEASE;

    default:
        return GESTURE_NONE;
    }
}

/* Single / double / triple once the gap after the last release has passed. */
static uint8_t gesture_poll(gesture_t *g, uint32_t now) {
    if (!g->pending || g->down || now - g->release_ms < GESTURE_GAP_MS) {
        return GESTURE_NONE;
    }
    g->pending = 0;
    return g->count <= GESTURE_TRIPLE ? g->count : GESTURE_NONE;
}

typedef struct {
    hal_gpio_pin_t pin;
    button_t       btn;

    /* A momentary wall switch (Open Dimmer's controls.c and dimmer.c, keep in sync): a hold runs
     * (holding: the finger has the level; from a press from dark at the press, else from the
     * hold time); this press reached the hold time (its release is one after a hold), switched
     * the light on (from dark: on at once, the hold's rise waiting at the minimum; a tap then
     * goes on to the level), began within GESTURE_GAP_MS of a tap's release (a double tap:
     * full); the last tap's release (| 1, 0: none); closed at the start (its release is no
     * press's); apply leaves its change at once to a hold from dark that follows (rise_next). */
    uint8_t        holding;
    uint8_t        hold_press;
    uint8_t        turned_on;
    uint8_t        dbl;
    uint8_t        boot_held;
    uint8_t        rise_next;
    uint32_t       tap_at;

    /* Its holds' way: the last one's, the band of a learnt one awaiting its verdict (1 + band,
     * 0: none), the last one's start and end (0: none since the light came on). */
    int8_t         last_dir;
    uint8_t        lrn_band;
    uint32_t       lrn_t0;
    uint32_t       lrn_t1;
    uint16_t       rep_val;       /* a hold's level last reported, and when the next is due (0: */
    uint32_t       rep_at;        /* none: a hold from dark before its hold time) */

    /* The output: the fader (hk_fader.c), ticked every DIM_TICK_MS from tick_at on; the lamp
     * compensation's peak follower (Open Dimmer's phase.c channel_compare: the highest duty
     * since the light came on x 16, the last duty, moved down since). */
    dim_fader_t    f;
    uint32_t       tick_at;
    uint32_t       peak16;
    uint16_t       last_d;
    uint8_t        down;
    uint8_t        sent;          /* the level last sent to the dimming MCU (0 = dark) */
    uint16_t       sent_v;        /* the fader's value it was sent for */
    uint16_t       sent_ramp;     /* with that ramp, and when (the keep-alive) */
    uint32_t       sent_at;
    uint8_t        resend;        /* send it again (start-up) */
    uint8_t        kick;          /* an instant change from dark: the minimum first, at once */
    uint8_t        start;         /* from dark: the first frame (the floor, a boost's full) is due;
                                   * the fader waits for it */
    uint32_t       time_ms;       /* a transition time from the network (DP HK_DP_TIME): */
    uint32_t       time_at;       /* for the changes written within HK_TIME_VALID_MS of it */
    uint8_t        factory_ramp;  /* an instant change: the next frame with the MCU's own ramp */
    gesture_t      gesture;       /* a momentary wall switch's (zero: gesture_init) */

    /* Open Dimmer 2.2.0's (RAM only): the countdown as written (s, 0: none) and when it ends;
     * identify (the s asked for, when it ends | 1, 0: none); the wall switch mode and type the
     * switch was last handled in, and a momentary press begun in another mode (its hold and
     * release: nothing). */
    uint32_t       countdown;
    uint32_t       cd_end;
    uint8_t        ident_s;
    uint32_t       ident_end;
    uint8_t        mode_seen;
    uint8_t        type_seen;
    uint8_t        skip_release;
} hk_channel_t;

static uint8_t        g_active;
static hal_gpio_pin_t g_button_pin = HAL_INVALID_PIN;
static hal_gpio_pin_t g_led_pin    = HAL_INVALID_PIN;
static hk_nv_t        g_nv;
static hk_nv_t        g_nv_saved;
static hk_channel_t   g_ch[HK_CHANNELS];
static uint8_t        g_ch_index[HK_CHANNELS] = { 0, 1 };
static button_t       g_button;
static uint8_t        g_button_seen;  /* pressed since the start (not one held through it) */
static uint8_t        g_button_long;

static uint8_t  g_out[512];
static uint16_t g_out_len;
static uint16_t g_seq;

static uint8_t  g_started;
static uint8_t  g_warm;          /* this start was a reset of the module alone */
static uint8_t  g_range_due;
static uint32_t g_last_frame_ms;
static uint8_t  g_next_ch;
static uint8_t  g_led_lit;
static uint8_t  g_tx_failures;   /* writes the UART did not take, in a row */
static uint32_t g_tx_wait_from;  /* no frame before g_tx_wait_ms from then */
static uint32_t g_tx_wait_ms;
static uint8_t  g_nv_tries;
static uint8_t  g_nv_due;        /* a save is scheduled (g_nv_task) */
static uint8_t  g_writes_wait;   /* frames held for the module's writes since g_writes_from */
static uint32_t g_writes_from;

/* A network reset by wall switch presses (Open Dimmer's DP 181, the module's
 * multi_press_reset_count): closures of one switch (g_reset_input), the first opens the window;
 * at the count the switch's channel blinks, then the request goes (g_reset_at | 1: then). */
static uint8_t  g_reset_input;
static uint8_t  g_reset_count;
static uint32_t g_reset_start;
static uint32_t g_reset_at;

static hal_task_t g_boot_task;
static hal_task_t g_resend_task;
static hal_task_t g_nv_task;
static hal_task_t g_led_task;

static hk_custom_attr_t g_custom[HK_CHANNELS];
static hk_custom_nv_t   g_custom_nv;

static uint8_t g_lrn_sure;     /* the hold bands the last verdict confirmed (RAM only) */

/* Basic 0xff3c..0xff3f: the slow zones as their attributes show them (fade_off_slow_zone L1, L2,
 * hold_slow_zone L1, L2; %, as Open Dimmer's DPs 152 / 157 and 154 / 159). */
static uint32_t g_zone_attr[2 * HK_CHANNELS];

/* ---- helpers -------------------------------------------------------------------------------- */

static uint16_t clamp_value(const hk_channel_nv_t *c, uint32_t v) {
    if (v < c->min) {
        return c->min;
    }
    if (v > c->max) {
        return c->max;
    }
    return (uint16_t)v;
}

static uint16_t custom_point(uint8_t ch, uint8_t i) {
    return (uint16_t)(g_custom[ch].data[2 * i] | g_custom[ch].data[2 * i + 1] << 8);
}

/* A whole table: 0 first, 10000 last, never falling (as Open Dimmer's dim_custom_check). */
static uint8_t custom_valid(uint8_t ch) {
    if (g_custom[ch].len != CUSTOM_LEN || custom_point(ch, 0) != 0 ||
        custom_point(ch, CUSTOM_POINTS - 1) != CURVE_OUT_MAX) {
        return 0;
    }
    for (uint8_t i = 1; i < CUSTOM_POINTS; i++) {
        if (custom_point(ch, i) < custom_point(ch, i - 1)) {
            return 0;
        }
    }
    return 1;
}

/* Open Dimmer's dim_curve: x 0..1000 -> 0..10000. */
static uint16_t dim_curve(uint8_t ch, uint8_t curve, uint16_t x) {
    uint16_t        pts[CUSTOM_POINTS];
    const uint16_t *k = NULL;
    uint32_t        n = CURVE_POINTS;

    if (x == 0) {
        return 0;
    }
    if (x >= VALUE_MAX) {
        return CURVE_OUT_MAX;
    }
    if (curve == CURVE_LINEAR || curve > CURVE_CUSTOM) {
        return (uint16_t)(x * (CURVE_OUT_MAX / VALUE_MAX));
    }
    if (curve == CURVE_CUSTOM) {
        if (custom_valid(ch)) {
            for (uint8_t i = 0; i < CUSTOM_POINTS; i++) {
                pts[i] = custom_point(ch, i);
            }
            k = pts;
            n = CUSTOM_POINTS;
        } else {
            curve = CURVE_LED_SOFT;     /* none (valid): the default */
        }
    }
    if (k == NULL) {
        k = k_curves[curve - 1];
    }
    uint32_t pos  = (uint32_t)x * (n - 1);
    uint32_t seg  = pos / VALUE_MAX;
    uint32_t frac = pos % VALUE_MAX;
    uint32_t a = k[seg], b = k[seg + 1];
    uint32_t v = a + ((b - a) * frac + VALUE_MAX / 2) / VALUE_MAX;

    return (uint16_t)(v < 1 ? 1 : v);   /* on is never fully dark */
}

/* Open Dimmer's dim_output: the level (10..1000) through the channel's curve, stretched over
 * its min..max (min stays min, max stays max), as a conduction time 0..10000 (0: dark). */
static uint16_t hk_duty(uint8_t ch, uint16_t value) {
    const hk_channel_nv_t *c = &g_nv.ch[ch];
    uint8_t  curve           = g_nv.curve[ch];
    uint16_t min = c->min, max = c->max;
    uint32_t duty;

    if (value == 0) {
        return 0;
    }
    if (min > max) {
        min = max;
    }
    if (value < min) {
        value = min;
    }
    if (value > max) {
        value = max;
    }
    if (curve == CURVE_LINEAR || curve > CURVE_CUSTOM || max == min) {
        duty = (uint32_t)value * (CURVE_OUT_MAX / VALUE_MAX);
    } else {
        uint32_t span  = (uint32_t)(max - min);
        uint32_t pos   = ((uint32_t)(value - min) * VALUE_MAX + span / 2) / span;
        uint32_t shape = pos ? dim_curve(ch, curve, (uint16_t)pos) : 0;

        duty = (uint32_t)min * (CURVE_OUT_MAX / VALUE_MAX) + (shape * span + VALUE_MAX / 2) /
               VALUE_MAX;
    }
    return (uint16_t)(duty < 1 ? 1 : duty);
}

/* A conduction time as the dimming MCU's level, 1..255 (taken as linear in it; 0: dark). */
static uint8_t duty_level(uint32_t duty) {
    uint32_t l = (duty * 255u + CURVE_OUT_MAX / 2) / CURVE_OUT_MAX;

    if (duty == 0) {
        return 0;
    }
    return l < 1 ? 1 : (l > 255 ? 255 : (uint8_t)l);
}

static uint8_t hk_out(uint8_t ch, uint16_t value) {
    return duty_level(hk_duty(ch, value));
}

/* The lamp compensation's peak follower, every tick (Open Dimmer 2.2.8's phase.c
 * channel_compare, keep in sync): the highest duty since the light came on (x 16) follows a rise
 * in about 1.3 s (128 ticks), from 0 when it comes on; moved down (or a hold down began) since it
 * came on, it counts as at least DIM_COMP_FALL above the minimum's duty (min x 10 whatever the
 * curve), so a dim-down from a low level reaches the same lowest light as one from above; dark
 * resets it. Kept at 0 while the channel has none. */
static void comp_follow(uint8_t ch) {
    hk_channel_t *r  = &g_ch[ch];
    uint16_t      v  = r->f.current;
    uint32_t      lo = g_nv.ch[ch].min * 10u;

    if (!g_nv.comp4[ch] || !v) {
        r->peak16 = 0;
        r->down   = 0;
        r->last_d = 0;
        return;
    }
    uint16_t d = hk_duty(ch, v);
    uint32_t w = d;

    if (d < r->last_d) {
        r->down = 1;
    }
    r->last_d = d;
    if (r->down && w < lo + DIM_COMP_FALL) {
        w = lo + DIM_COMP_FALL;
    }
    if (w * 16u > r->peak16) {
        r->peak16 += (w * 16u - r->peak16 + 127u) / 128u;
    }
}

/* The level a fader value goes out as: through the curve, less the lamp compensation (Open Dimmer
 * 2.2.8's: its comp4 x 40 of the duty at most, about one level of the dimming MCU each). A level
 * it lowered is not sent below the factory range's minimum HK_RANGE_MIN: what the dimming MCU
 * makes of a level under it is not known (its linearity has not been measured either: the
 * compensation's levels are Open Dimmer's duty, taken as linear in the MCU's level). */
static uint8_t out_level(uint8_t ch, uint16_t v) {
    uint16_t d = hk_duty(ch, v);
    uint16_t c = d && g_nv.comp4[ch]
               ? dim_comp(d, (uint16_t)(g_ch[ch].peak16 >> 4), (uint16_t)(g_nv.comp4[ch] * 40u),
                          (uint16_t)(g_nv.ch[ch].min * 10u)) : 0;

    if (!c) {
        return duty_level(d);
    }
    uint8_t l = duty_level(d > c ? (uint32_t)(d - c) : 1u);

    return l < HK_RANGE_MIN ? HK_RANGE_MIN : l;
}

static void nv_schedule(uint32_t ms) {
    g_nv_due = 1;
    hal_tasks_unschedule(&g_nv_task);
    hal_tasks_schedule(&g_nv_task, ms);
}

static void nv_changed(void) {
    g_nv_tries = 0;
    nv_schedule(HK_NV_DELAY_MS);
}

/* A level written while lit: saved HK_NV_LIT_MS after the first one, not put off by the next
 * ones (an automation's steps write at most once a minute), or with a save due earlier; the
 * off saves it too. Open Dimmer saves none (a RAM mailbox keeps it through a reset of its MCU
 * alone); here nothing like it does, so a reset of the module alone could leave the light at
 * a level hours old. */
static void nv_changed_lit(void) {
    if (!g_nv_due) {
        g_nv_tries = 0;
        nv_schedule(HK_NV_LIT_MS);
    }
}

/* The channel's power-on setting (POWER_ON_*; Open Dimmer's DP 103 / 104). */
static uint8_t *power_on_of(uint8_t ch) {
    return ch ? &g_nv.power_on_2 : &g_nv.power_on;
}

/* What NV keeps: a channel's on / off state only for the power-on setting that uses it
 * (previous; as Open Dimmer's on_mask), so a plain on or off under off / on writes no flash. */
static void nv_image(hk_nv_t *img) {
    *img = g_nv;
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        if (*power_on_of(ch) != POWER_ON_PREVIOUS) {
            img->ch[ch].on = 0;
        }
    }
}

/* A fade or a hold runs on a channel: this module sends its frames, which a flash write (IRQs
 * off, tens of ms for an erase) would hold up. A first frame waiting for a UART that refuses
 * frames holds nothing up (none goes, and the fader waits for it): no save waits for it, as long
 * as the link stays dead. */
static uint8_t nv_busy(void) {
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        const hk_channel_t *r = &g_ch[ch];
        const dim_fader_t * f = &r->f;

        if (r->holding) {
            return 1;
        }
        if (r->start || r->kick) {
            if (g_tx_failures == 0) {
                return 1;
            }
            continue;
        }
        if (f->boost_left || f->timed_left || f->current != f->target) {
            return 1;
        }
    }
    return 0;
}

/* force: now, also while a fade runs (before a planned restart). */
static void nv_store(uint8_t force) {
    hk_nv_t img;

    nv_image(&img);
    if (memcmp(&img, &g_nv_saved, sizeof(img)) == 0) {
        return;
    }
    if (!force && nv_busy()) {
        nv_schedule(HK_NV_BUSY_MS);
        return;
    }
    if (hal_nvm_write(NV_ITEM_HK_DIMMER, sizeof(img), (uint8_t *)&img) == HAL_NVM_SUCCESS) {
        g_nv_saved = img;
        g_nv_tries = 0;
    } else if (!force && ++g_nv_tries < HK_NV_TRIES) {
        nv_schedule(HK_NV_RETRY_MS);
    } else {
        g_nv_tries = 0;     /* given up until the next change */
    }
}

static void nv_save(void *arg) {
    (void)arg;
    g_nv_due = 0;
    nv_store(0);
}

static void nv_defaults_from(uint8_t layout) {
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        if (layout < 2) {
            g_nv.ramp[ch] = HK_RAMP_SMOOTH;
        }
        if (layout < 3) {
            g_nv.fade_on[ch]  = HK_FADE_ON_MS;
            g_nv.fade_off[ch] = HK_FADE_OFF_MS;
            g_nv.hold[ch]     = HK_HOLD_MS;
        }
        if (layout < 4) {
            g_nv.curve[ch] = CURVE_LED_SOFT;    /* Open Dimmer's default */
        }
    }
    if (layout < 5) {
        /* (a layout 4 item's tail padding came along: 2 bytes on the Telink build) */
        memset(&g_nv.power_on_2, 0, sizeof(g_nv) - offsetof(hk_nv_t, power_on_2));
        g_nv.power_on_2 = g_nv.power_on;        /* one setting for both before (DP 14) */
        for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
            g_nv.soft_end[ch] = HK_SOFT_END_PCT;
            g_nv.hold_end[ch] = HK_HOLD_END_PCT;
        }
    }
    g_nv.layout = HK_NV_LAYOUT;
}

static void nv_load(void) {
    static const uint16_t sizes[] = { sizeof(hk_nv_t), HK_NV_V4_SIZE, HK_NV_V3_SIZE,
                                      HK_NV_V2_SIZE,   HK_NV_V1_SIZE };
    static const uint8_t  layouts[] = { HK_NV_LAYOUT, 4, 3, 2, 1 };
    static uint8_t        buf[HK_NV_READ_MAX];
    uint8_t ok = 0;

    /* The item of this layout, or of an earlier one (its settings kept, the rest defaults). */
    for (uint8_t i = 0; i < sizeof(sizes) / sizeof(sizes[0]) && !ok; i++) {
        memset(&g_nv, 0, sizeof(g_nv));
        if (hal_nvm_read(NV_ITEM_HK_DIMMER, sizes[i], buf) == HAL_NVM_SUCCESS &&
            buf[0] == layouts[i]) {
            memcpy(&g_nv, buf, sizes[i]);
            nv_defaults_from(layouts[i]);
            ok = 1;
        }
    }
    if (!ok) {
        memset(&g_nv, 0, sizeof(g_nv));
        g_nv.power_on = POWER_ON_OFF;
        for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
            g_nv.ch[ch].switch_type = SWITCH_TOGGLE;
            g_nv.ch[ch].value       = VALUE_MAX;
            g_nv.ch[ch].min         = VALUE_MIN;
            g_nv.ch[ch].max         = VALUE_MAX;
        }
        nv_defaults_from(0);
    }
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        hk_channel_nv_t *c = &g_nv.ch[ch];

        /* Limits within 10..1000, the minimum not above the maximum (Open Dimmer's load). */
        c->max = c->max < VALUE_MIN ? VALUE_MIN : c->max > VALUE_MAX ? VALUE_MAX : c->max;
        c->min = c->min < VALUE_MIN ? VALUE_MIN : c->min > c->max ? c->max : c->min;
        /* Open Dimmer 2.2.0's settings within their ranges (else as none). */
        if (*power_on_of(ch) > POWER_ON_PREVIOUS) {
            *power_on_of(ch) = POWER_ON_OFF;
        }
        if (g_nv.input_mode[ch] > INPUT_OFF) {
            g_nv.input_mode[ch] = INPUT_NORMAL;
        }
        if (g_nv.power_level4[ch] > HK_POWER_LEVEL4_MAX) {
            g_nv.power_level4[ch] = 0;
        }
        if (g_nv.boost50[ch] > HK_BOOST_MAX_MS / HK_BOOST_STEP_MS) {
            g_nv.boost50[ch] = 0;
        }
        g_nv.fast_press[ch] = g_nv.fast_press[ch] ? 1 : 0;
        if (g_nv.soft_end[ch] > 100) {
            g_nv.soft_end[ch] = HK_SOFT_END_PCT;
        }
        if (g_nv.hold_end[ch] > 100) {
            g_nv.hold_end[ch] = HK_HOLD_END_PCT;
        }
        if (g_nv.comp4[ch] > HK_COMP_MAX / 4) {
            g_nv.comp4[ch] = 0;
        }
    }
    g_nv.switch_opts &= HK_OPT_NO_RISE(0) | HK_OPT_NO_RISE(1) | HK_OPT_NO_DOUBLE(0) |
                        HK_OPT_NO_DOUBLE(1);
    g_nv.spare = 0;
    nv_image(&g_nv_saved);

    memset(&g_custom_nv, 0, sizeof(g_custom_nv));
    hal_nvm_read(NV_ITEM_HK_CUSTOM, sizeof(g_custom_nv), (uint8_t *)&g_custom_nv);
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        g_custom[ch].len = g_custom_nv.len[ch] == CUSTOM_LEN ? CUSTOM_LEN : 0;
        memcpy(g_custom[ch].data, g_custom_nv.data[ch], CUSTOM_LEN);
        if (!custom_valid(ch)) {
            g_custom[ch].len = 0;
        }
    }
}

/* ---- the output: the fader (hk_fader.c, Open Dimmer's), ticked here ------------------------ */

/* A hold from dark waits at the minimum past the hold time, so it can be let go there (Open
 * Dimmer's controls.c check). */
typedef char hk_dark_wait_past_the_hold_time[
        DIM_HOLD_DARK_WAIT_MS >= HK_LONG_PRESS_MS + 200 ? 1 : -1];

/* The fader's profile from the channel's settings (Open Dimmer's dimmer.c apply: the slow zones
 * are % of its min..max, from 2.2.8), its start boost and its floor (the minimum). This module's
 * own: hold_time is the hold's full rate over min..max (as in its builds before the fader; Open
 * Dimmer's is over the whole 0..1000), so a narrowed range holds at the same pace; the slow zone
 * still slows it to DIM_HOLD_BOTTOM_Q at the minimum. */
static void profile_set(uint8_t ch) {
    const hk_channel_nv_t *c = &g_nv.ch[ch];
    dim_fader_t *          f = &g_ch[ch].f;
    uint32_t range           = c->max > c->min ? (uint32_t)(c->max - c->min) : 0u;
    uint32_t hold            = g_nv.hold[ch] < 100u ? 100u : g_nv.hold[ch];
    uint32_t hold_q          = (range * DIM_Q_PER_STEP * DIM_TICK_MS + hold / 2u) / hold;

    dim_profile_set(&f->prof, g_nv.fade_on[ch], g_nv.fade_off[ch], g_nv.soft_end[ch],
                    g_nv.hold[ch], g_nv.hold_end[ch]);
    f->prof.hold_q    = (uint16_t)(hold_q ? hold_q : 1u);
    f->prof.soft_span = (uint16_t)(f->prof.soft_span * range / 1000u);
    f->prof.hold_span = (uint16_t)(f->prof.hold_span * range / 1000u);
    f->prof.boost_ms  = (uint16_t)(g_nv.boost50[ch] * HK_BOOST_STEP_MS);
    f->floor          = c->min;
}

/* Where the channel's light is now on its way (a value, 0: dark): a timed move's even position
 * (also during a start boost: its clock runs), a boost's target, a fade from dark waiting for its
 * first frame at the minimum, else the fader's. */
static uint16_t fade_at(uint8_t ch) {
    const hk_channel_t *r = &g_ch[ch];
    const dim_fader_t * f = &r->f;

    if (f->timed_left) {
        uint16_t a = f->timed_from, b = f->timed_to;
        uint16_t d = a > b ? (uint16_t)(a - b) : (uint16_t)(b - a);
        uint16_t m = (uint16_t)((uint32_t)d * (f->timed_total - f->timed_left) / f->timed_total);

        return b > a ? (uint16_t)(a + m) : (uint16_t)(a - m);
    }
    if (f->boost_left) {
        return f->target;
    }
    return r->start && !f->current ? g_nv.ch[ch].min : f->current;
}

/* 1 while the fader moves the light (not a hold): a fade, a timed move, a boost, a fade from dark
 * waiting for its first frame. */
static uint8_t fader_moving(uint8_t ch) {
    const hk_channel_t *r = &g_ch[ch];
    const dim_fader_t * f = &r->f;

    return !f->hold_dir && (r->start || f->timed_left || f->boost_left || f->current != f->target);
}

/* The network's transition time for a change now, or HK_TIME_NONE. */
static uint32_t network_time(uint8_t ch, uint32_t now) {
    const hk_channel_t *r = &g_ch[ch];

    return r->time_ms && now - r->time_at < HK_TIME_VALID_MS ? r->time_ms : HK_TIME_NONE;
}

/* The ms left of the channel's last frame's ramp on the dimming MCU (taken as ms, as the
 * keep-alive takes it; 0: it has run out, or the frame had none). */
static uint32_t last_ramp_left(const hk_channel_t *r, uint32_t now) {
    uint32_t gone = now - r->sent_at;

    return gone < r->sent_ramp ? r->sent_ramp - gone : 0;
}

/* The ms the dimming MCU still runs its own ramp (mcu_ramp) on the channel: the whole ramp while
 * the frame of a change at once still waits, else what is left of the last frame's (0: none).
 * Only the MCU knows where the lamp is on that ramp. */
static uint32_t mcu_ramp_left(uint8_t ch, uint32_t now) {
    const hk_channel_t *r = &g_ch[ch];

    if (r->factory_ramp && g_nv.ramp[ch]) {
        return g_nv.ramp[ch];
    }
    return last_ramp_left(r, now);
}

/* A change the settings make at once (fade_on_time / fade_off_time under a frame gap; this
 * module's, not Open Dimmer's): the light there now, in one frame with the dimming MCU's own ramp
 * (mcu_ramp); from dark to above the minimum the minimum at once first, as an LED lamp stays dark
 * through the part of that ramp below its threshold (not while the last frame's ramp runs: the
 * lamp is lit on its way, the minimum dropped it). */
static void instant(uint8_t ch, uint16_t v) {
    hk_channel_t *r = &g_ch[ch];
    dim_fader_t * f = &r->f;

    uint8_t above = hk_out(ch, v) > hk_out(ch, g_nv.ch[ch].min) &&
                    !last_ramp_left(r, hal_millis());

    r->kick         = v && (r->kick || (!f->current && above));
    f->current      = v;
    f->target       = v;
    f->timed_left   = 0;
    f->boost_left   = 0;
    f->floor_hold   = 0;
    f->from_dark    = 0;
    f->acc          = 0;
    r->factory_ramp = 1;
    r->start        = 0;
}

/* The channel's on / off, level or a setting changed: the fader takes it, as Open Dimmer's
 * dimmer.c apply has it (the profile, then the target in the time the network sent for the
 * changes of the second after it; under a hold the finger has the light, an off ends it). This
 * module's own: a change the settings make at once goes as instant() has it (an on from dark, a
 * level written while its frame waits, an on after a tap from dark, an off; never with a start
 * boost, nor before a hold from dark: rise_next); a fade from dark starts with its first frame
 * (start: the fader waits for it); a network time under a tick from dark is there at once.
 *
 * While the dimming MCU runs its own ramp (mcu_ramp_left: a change at once went, or its frame
 * waits), only the MCU knows where the lamp is on it: a frame at ramp 0 from the fader jumped
 * the lamp from there (an off 40 ms after an on at once: near full first, then down). So a
 * change then goes at once too, in one frame with that ramp (the MCU turns round from where it
 * is), whatever its fade settings or network time (a network time of 0 still at once, ramp 0);
 * a press from dark's first frame (the minimum) too (apply's start, send_due), and a hold
 * waits for that ramp's end (hold_begin). */
static void apply(uint8_t ch) {
    hk_channel_t *         r = &g_ch[ch];
    const hk_channel_nv_t *c = &g_nv.ch[ch];
    dim_fader_t *          f = &r->f;
    uint32_t now             = hal_millis();
    uint32_t t   = network_time(ch, now);               /* HK_TIME_NONE: above any time */
    uint8_t  mcu = mcu_ramp_left(ch, now) != 0;

    /* An instant change whose frame still waits (its kick may have gone) is not where the light
     * is: the change now starts from the last frame's (dark, the kick's minimum, the level
     * before). Else an off right after an on at once (fade_on_time 0) faded down from a level
     * the lamp never showed: up to it first. */
    if (r->factory_ramp) {
        f->current = r->sent ? r->sent_v : 0;
        r->kick    = 0;
    }
    uint8_t dark = f->current == 0;

    profile_set(ch);
    if (mcu && t >= DIM_TICK_MS && !f->hold_dir && !r->rise_next && !f->boost_left) {
        HK_TRACE(HK_EV_RAMP_LEFT, ch, mcu_ramp_left(ch, now));
        instant(ch, c->on ? c->value : 0);
        return;
    }
    if (t == HK_TIME_NONE && !f->hold_dir && !r->rise_next && !f->boost_left) {
        if (c->on && g_nv.fade_on[ch] < HK_FRAME_GAP_MS &&
            (r->factory_ramp || f->from_dark || (dark && !f->prof.boost_ms))) {
            HK_TRACE(HK_EV_AT_ONCE_ON, ch, c->value);
            instant(ch, c->value);
            return;
        }
        if (!c->on && g_nv.fade_off[ch] < HK_FRAME_GAP_MS && (!dark || r->factory_ramp)) {
            HK_TRACE(HK_EV_AT_ONCE_OFF, ch, 0);
            instant(ch, 0);
            return;
        }
    }
    r->factory_ramp = 0;
    HK_TRACE(HK_EV_FADER, ch, t);
    dim_fader_set_timed(f, c->on, c->value, t == HK_TIME_NONE ? 0 : t);
    HK_TRACE(HK_EV_FADER_AT, ch, (uint32_t)f->current << 16 | f->target);
    if (r->start && !f->target && !f->hold_dir) {
        /* Off before its first frame: nothing was lit, nothing to send. */
        f->current    = 0;
        f->boost_left = 0;
        f->timed_left = 0;
        r->start      = 0;
    } else if (f->target && t < DIM_TICK_MS && !f->boost_left && !f->hold_dir) {
        /* The network's at once (lit or from dark; not an off: its floor): there now, not at
         * the fader's next tick, so the next frame (after the module's level write that follows
         * its on) has it, not the level the on went to. */
        f->current    = f->target;
        f->timed_left = 0;
    } else if (dark && (f->target || f->hold_dir)) {
        r->start        = 1;
        r->factory_ramp = mcu;  /* its first frame with the MCU's ramp, from where the lamp is */
    }
}

/* Identify runs on the channel (not under a hold: the finger has the light). */
static uint8_t identifying(const hk_channel_t *r) {
    return r->ident_end && !r->holding;
}

static uint8_t want_level(uint8_t ch, uint32_t now) {
    hk_channel_t *r = &g_ch[ch];

    if (identifying(r)) {
        return (now >> HK_IDENTIFY_SHIFT) & 1u ? hk_out(ch, VALUE_MAX) : 0;
    }
    return out_level(ch, r->f.current);
}

/* The finger has the channel's level: a hold past its hold time (a press from dark before it:
 * the light waits at the minimum, a tap goes on to the level, which may still be written). */
static uint8_t finger(const hk_channel_t *r) {
    return r->holding && r->hold_press;
}

/* The level the channel shows: under the finger where the hold is (it is taken at its end). */
static uint16_t shown_value(uint8_t ch) {
    const hk_channel_t *r = &g_ch[ch];

    return finger(r) ? clamp_value(&g_nv.ch[ch], r->f.current) : g_nv.ch[ch].value;
}

/* Both channels' on bits into the kept byte, for a reset of the module alone. */
static void warm_keep(void) {
    uint8_t v = HK_KEPT_MAGIC;

    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        if (g_nv.ch[ch].on) {
            v |= (uint8_t)(1u << ch);
        }
    }
    hal_system_kept_write(v);
}

/* A hold ends without taking its level (Open Dimmer's end_hold: the network, the button or a
 * setting changed the channel under the finger; its release changes nothing). */
static void end_hold(uint8_t ch) {
    if (g_ch[ch].holding) {
        g_ch[ch].holding = 0;
        dim_fader_hold_stop(&g_ch[ch].f);
    }
}

/* The channel was switched on or off: a hold ends without its level, an off ends the countdown
 * and a hold's correction sequence (Open Dimmer's), and the output follows. */
static void on_changed(uint8_t ch) {
    end_hold(ch);
    if (!g_nv.ch[ch].on) {
        g_ch[ch].countdown = 0;     /* any off ends the auto-off countdown (Open Dimmer's) */
        g_ch[ch].lrn_t1    = 0;     /* no correction across an off */
    }
    apply(ch);
    warm_keep();
}

/* Identify (Open Dimmer's dimmer_identify, DP 166 / 167): the channel blinks dark / full (its
 * max) every 0.5 s for s seconds, then is as it was: it goes to its state by its fade from where
 * the blink left it (a fade or a transition that ran when it began is not resumed); 0 stops it.
 * Not saved. A hold during it has the light; the blink goes on after it. */
static void identify_done(uint8_t ch) {
    hk_channel_t *r = &g_ch[ch];
    dim_fader_t * f = &r->f;

    if (r->holding) {
        return;     /* the hold's fade is the light's */
    }
    f->current      = r->sent ? g_nv.ch[ch].max : 0;
    f->target       = f->current;
    f->timed_left   = 0;
    f->boost_left   = 0;
    f->floor_hold   = 0;
    f->from_dark    = 0;
    r->start        = 0;
    r->kick         = 0;
    r->factory_ramp = 0;
    apply(ch);
}

static void identify(uint8_t ch, uint8_t s) {
    hk_channel_t *r   = &g_ch[ch];
    uint8_t       was = r->ident_end != 0;

    r->ident_s   = s;
    r->ident_end = s ? (hal_millis() + s * 1000u) | 1u : 0;
    if (was && !s) {
        identify_done(ch);
    }
}

#define HK_FRAME_LEN    12

/* One frame to the dimming MCU; 1 when the UART took it (see HK_TX_RETRY_MS). */
static uint8_t hk_write(const uint8_t *frame) {
    if (hal_uart_write(frame, HK_FRAME_LEN, NULL) == HAL_UART_OK) {
        g_tx_failures = 0;
        g_tx_wait_ms  = 0;
        return 1;
    }
    tuya_mcu_diag.tx_fail++;
    if (++g_tx_failures > HK_TX_FAILURES_MAX) {
        g_tx_failures = HK_TX_REINIT_FAILURES + 1;
    }
    g_tx_wait_from = hal_millis();
    g_tx_wait_ms   = g_tx_failures > HK_TX_REINIT_FAILURES ? HK_TX_STUCK_WAIT_MS : HK_TX_RETRY_MS;
    if (g_tx_failures % HK_TX_REINIT_FAILURES == 0) {
        tuya_secondary_mcu_uart_reinit();
    }
    return 0;
}

/* Both levels go in every frame: the Wi-Fi siblings' MCU takes the one byte 4 names, and the
 * other one is its current level anyway, should this MCU take both. 1 when written. */
static uint8_t hk_send_level(uint8_t ch, uint8_t level, uint16_t ramp) {
    hk_channel_t *r = &g_ch[ch];
    uint8_t       frame[HK_FRAME_LEN] = { 0x00,
                                          0x00,
                                          0xFF,
                                          0x55,
                                          (uint8_t)(ch + 1),
                                          0x00,
                                          0x00,
                                          (uint8_t)(ramp >> 8),
                                          (uint8_t)ramp,
                                          0x0A,
                                          0x00, 0x00 };

    for (uint8_t i = 0; i < HK_CHANNELS; i++) {
        frame[5 + i] = g_ch[i].sent;
    }
    frame[5 + ch] = level;
    if (!hk_write(frame)) {
#ifdef MCU_FLASHER_DUMP
        tuya_mcu_diag_log(HK_DIAG_FRAME, (uint8_t)(0x80u | (ch + 1u)),
                          (uint32_t)level << 16 | ramp);
#endif
        return 0;
    }
#ifdef MCU_FLASHER_DUMP
    if (!r->holding) {
        tuya_mcu_diag_log(HK_DIAG_FRAME, (uint8_t)(ch + 1u), (uint32_t)level << 16 | ramp);
    }
#endif
    r->sent      = level;
    r->sent_ramp = ramp;
    r->sent_at   = hal_millis();
    return 1;
}

static uint8_t hk_send_range(void) {
    uint8_t frame[HK_FRAME_LEN] = { 0x55,
                                    0xAA,
                                    0x00,
                                    0xFE,
                                    0x00,
                                    0x05,
                                    0x03,
                                    HK_RANGE_MIN,HK_RANGE_MAX,
                                    HK_RANGE_MIN,
                                    HK_RANGE_MAX, 0x00 };
    uint8_t sum = 0;

    for (uint8_t i = 0; i < sizeof(frame) - 1; i++) {
        sum += frame[i];
    }
    frame[sizeof(frame) - 1] = sum;
    return hk_write(frame);
}

/* One frame of a channel that needs one, if any (the channels in turn). A frame the UART did
 * not take leaves everything as it was, so it goes again after hk_write's wait. */
static uint8_t send_due(uint8_t ch, uint32_t now) {
    hk_channel_t *r = &g_ch[ch];

    if (identifying(r)) {
        /* Dark / full by turns, at once (Open Dimmer's dimmer_poll). */
        uint8_t level = want_level(ch, now);

        if (level == r->sent) {
            return 0;
        }
        hk_send_level(ch, level, 0);
        return 1;
    }
    if (r->kick) {
        r->kick = !hk_send_level(ch, hk_out(ch, g_nv.ch[ch].min), 0);
        if (!r->kick) {
            r->sent_v = g_nv.ch[ch].min;
        }
        return 1;
    }
    if (r->start) {
        /* From dark: the fader's first tick goes out at once (at the minimum, a start boost at
         * the channel's full, a hold from dark waiting at the minimum); its clock runs from this
         * frame on. A frame the UART did not take: that tick again, not another one. While the
         * MCU's own ramp runs (apply: factory_ramp), the minimum goes with that ramp; a start
         * boost's full at once. */
        if (!r->f.current) {
            dim_fader_tick(&r->f);
            comp_follow(ch);
        }
        uint16_t ramp = r->factory_ramp && !r->f.boost_left ? g_nv.ramp[ch] : 0;

        if (hk_send_level(ch, want_level(ch, now), ramp)) {
            r->start        = 0;
            r->tick_at      = now;
            r->resend       = 0;
            r->sent_v       = r->f.current;
            r->factory_ramp = 0;
        }
        return 1;
    }
    uint16_t v     = r->f.current;
    uint8_t  level = want_level(ch, now);

    /* The same level again (the keep-alive: quiet for HK_KEEPALIVE_MS; a resend) goes at ramp
     * 0, so only once the MCU's own ramp of the last frame (taken as ms) has run out: never
     * cutting that ramp short; nor does the lamp compensation's drift of a light at rest (only a
     * new value of the fader does). */
    uint8_t ramping = now - r->sent_at < r->sent_ramp;
    uint8_t quiet   = now - r->sent_at >= HK_KEEPALIVE_MS + r->sent_ramp;

    if ((ramping && v == r->sent_v) || (level == r->sent && (ramping || (!r->resend && !quiet)))) {
        /* A change at once to where the MCU already goes has no frame: its ramp is not kept for
         * a later change's frame (apply took that for one at once). */
        r->factory_ramp = 0;
        return 0;
    }
    /* The MCU's own ramp for a change made at once only; the same level again goes at 0. */
    if (hk_send_level(ch, level, r->factory_ramp && level != r->sent ? g_nv.ramp[ch] : 0)) {
        r->sent_v       = v;
        r->resend       = 0;
        r->factory_ramp = 0;
    }
    return 1;
}

/* The faders, a tick every DIM_TICK_MS of the clock (none before the start-up's first sending:
 * as Open Dimmer's, which fades only while its output runs; nor while a channel's first frame
 * from dark waits, which starts its clock), each with the lamp compensation's follower. A long
 * stall is caught up (at most HK_TICKS_MAX ticks at once). */
#define HK_TICKS_MAX    1000u

static void fader_ticks(uint32_t now) {
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        hk_channel_t *r = &g_ch[ch];

        if (!g_started || r->start) {
            r->tick_at = now;
            continue;
        }
        for (uint16_t n = 0; (int32_t)(now - r->tick_at) >= DIM_TICK_MS; n++) {
            if (n >= HK_TICKS_MAX) {
                r->tick_at = now;
                break;
            }
            dim_fader_tick(&r->f);
            comp_follow(ch);
            r->tick_at += DIM_TICK_MS;
        }
    }
}

static void hold_tick(uint8_t ch, uint32_t now);
static void report_gesture(uint8_t ch, uint8_t gesture);
static void mode_check(uint8_t ch);
static void set_on(uint8_t ch, uint8_t on);
static void send_reset_request(void);

void hk_dimmer_poll(void) {
    if (!g_active) {
        return;
    }
    uint32_t now = hal_millis();

    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        hk_channel_t *r = &g_ch[ch];

        mode_check(ch);
        hold_tick(ch, now);
        report_gesture(ch, gesture_poll(&r->gesture, now));
        if (r->countdown && (int32_t)(now - r->cd_end) >= 0) {
            set_on(ch, 0);  /* auto-off: as a switch off (reported, the countdown 0) */
        }
        if (r->ident_end && (int32_t)(now - r->ident_end) >= 0) {
            r->ident_end = 0;
            r->ident_s   = 0;
            identify_done(ch);
        }
    }
    if (g_reset_at && (int32_t)(now - g_reset_at) >= 0) {
        g_reset_at = 0;
        send_reset_request();
    }

    /* The time again (the events above took a while), the faders up to it, then a frame. */
    now = hal_millis();
    fader_ticks(now);

    /* Not while the module's writes of a change wait for the virtual MCU (HK_WRITES_WAIT_MS at
     * most): the network's on at once from dark is its own write, the level its next one; a
     * frame between them showed the light at the level it had before, for a frame. */
    if (tuya_secondary_mcu_tx_pending()) {
        if (!g_writes_wait) {
            g_writes_wait = 1;
            g_writes_from = now;
        }
        if (now - g_writes_from < HK_WRITES_WAIT_MS) {
            return;
        }
    } else {
        g_writes_wait = 0;
    }
    if (!g_started || now - g_last_frame_ms < HK_FRAME_GAP_MS ||
        now - g_tx_wait_from < g_tx_wait_ms) {
        return;
    }
    if (g_range_due) {
        g_last_frame_ms = now;
        g_range_due     = !hk_send_range();
        return;
    }
    for (uint8_t i = 0; i < HK_CHANNELS; i++) {
        uint8_t ch = (uint8_t)((g_next_ch + i) % HK_CHANNELS);

        if (send_due(ch, now)) {
            g_last_frame_ms = now;
            g_next_ch       = (uint8_t)((ch + 1) % HK_CHANNELS);
            return;
        }
    }
}

/* arg: the first sending (lit channels fade in from dark); the second one, a little later,
 * sends the output as it is. After a warm start both send it as it is, without the range
 * (the dimming MCU kept driving the light: hk_dimmer_start). */
static void boot_send(void *arg) {
    uint8_t first = arg != NULL;

    g_started   = 1;
    g_range_due = !g_warm;
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        if (first && g_nv.ch[ch].on && !g_warm) {
            apply(ch);
        }
        g_ch[ch].resend = 1;
    }
}

/* ---- to the module -------------------------------------------------------------------------- */

static uint8_t *out_frame_begin(uint8_t cmd, uint16_t seq) {
    if (g_out_len + TUYA_HEADER_LEN + 1 > sizeof(g_out)) {
        return NULL;
    }
    uint8_t *f = &g_out[g_out_len];

    f[0] = 0x55;
    f[1] = 0xAA;
    f[2] = TUYA_VERSION;
    f[3] = (uint8_t)(seq >> 8);
    f[4] = (uint8_t)seq;
    f[5] = cmd;
    f[6] = 0;
    f[7] = 0;
    return f;
}

static void out_frame_add(uint8_t *f, const uint8_t *data, uint16_t len) {
    uint16_t dlen = (uint16_t)((f[6] << 8) | f[7]);

    if (f + TUYA_HEADER_LEN + dlen + len + 1 > g_out + sizeof(g_out)) {
        return;
    }
    memcpy(f + TUYA_HEADER_LEN + dlen, data, len);
    dlen = (uint16_t)(dlen + len);
    f[6] = (uint8_t)(dlen >> 8);
    f[7] = (uint8_t)dlen;
}

static void out_frame_end(uint8_t *f) {
    uint16_t dlen = (uint16_t)((f[6] << 8) | f[7]);
    uint16_t n    = (uint16_t)(TUYA_HEADER_LEN + dlen);
    uint8_t  sum  = 0;

    for (uint16_t i = 0; i < n; i++) {
        sum += f[i];
    }
    f[n]      = sum;
    g_out_len = (uint16_t)(g_out_len + n + 1);
}

static void add_dp(uint8_t *f, uint8_t dpid, uint8_t type, uint32_t value) {
    uint8_t unit[8] = { dpid, type, 0, 0 };
    uint8_t n;

    if (type == DP_TYPE_VALUE) {
        unit[3] = 4;
        unit[4] = (uint8_t)(value >> 24);
        unit[5] = (uint8_t)(value >> 16);
        unit[6] = (uint8_t)(value >> 8);
        unit[7] = (uint8_t)value;
        n       = 8;
    } else {
        unit[3] = 1;
        unit[4] = (uint8_t)value;
        n       = 5;
    }
    out_frame_add(f, unit, n);
}

/* Open Dimmer's setting k (HK_OD_*) of a channel, as its DP has it. */
static uint32_t od_value(uint8_t ch, uint8_t k) {
    switch (k) {
    case HK_OD_INPUT:
        return g_nv.input_mode[ch];

    case HK_OD_POWER_LEVEL:
        return g_nv.power_level4[ch] * 4u;

    case HK_OD_IDENTIFY:
        return g_ch[ch].ident_s;

    case HK_OD_BOOST:
        return g_nv.boost50[ch] * (uint32_t)HK_BOOST_STEP_MS;

    case HK_OD_COMP:
        return g_nv.comp4[ch] * 4u;

    default:
        return g_nv.fast_press[ch];
    }
}

static void add_od_dp(uint8_t *f, uint8_t ch, uint8_t k) {
    add_dp(f, (uint8_t)HK_DP_OD(k, ch), DP_TYPE_VALUE, od_value(ch, k));
}

static void add_channel_dp(uint8_t *f, uint8_t ch, uint8_t which) {
    const hk_channel_nv_t *c = &g_nv.ch[ch];
    uint8_t dpid             = (uint8_t)(HK_DP_BASE(ch) + which);

    switch (which) {
    case HK_DP_COUNTDOWN:
        add_dp(f, dpid, DP_TYPE_VALUE, g_ch[ch].countdown);
        break;
    case HK_DP_ONOFF:
        add_dp(f, dpid, DP_TYPE_BOOL, c->on);
        break;
    case HK_DP_LEVEL:
        add_dp(f, dpid, DP_TYPE_VALUE, shown_value(ch));
        break;
    case HK_DP_MIN:
        add_dp(f, dpid, DP_TYPE_VALUE, c->min);
        break;
    case HK_DP_SWITCH_TYPE:
        add_dp(f, dpid, DP_TYPE_ENUM, c->switch_type);
        break;
    case HK_DP_MAX:
        add_dp(f, dpid, DP_TYPE_VALUE, c->max);
        break;
    }
}

#define REPORT_ON           1
#define REPORT_LEVEL        2
#define REPORT_COUNTDOWN    4   /* an off ended it: 0 (Open Dimmer's report_local) */

/* A wall switch or button change (what: REPORT_*): an active report, as a factory MCU reports
 * a key. */
static void report_change(uint8_t ch, uint8_t what) {
    uint8_t *f = out_frame_begin(CMD_REPORT, g_seq++);

    if (f == NULL) {
        return;
    }
    if (what & REPORT_ON) {
        add_channel_dp(f, ch, HK_DP_ONOFF);
    }
    if (what & REPORT_LEVEL) {
        add_channel_dp(f, ch, HK_DP_LEVEL);
    }
    if (what & REPORT_COUNTDOWN) {
        add_channel_dp(f, ch, HK_DP_COUNTDOWN);
    }
    out_frame_end(f);
}

/* A gesture (GESTURE_*, none: nothing) of the channel's wall switch: an active report. */
static void report_gesture(uint8_t ch, uint8_t gesture) {
    uint8_t *f = gesture ? out_frame_begin(CMD_REPORT, g_seq++) : NULL;

    if (f == NULL) {
        return;
    }
    add_dp(f, (uint8_t)HK_DP_ACTION(ch), DP_TYPE_ENUM, gesture);
    out_frame_end(f);
}

/* An event of the channel's momentary wall switch, after the light acted on it. */
static void gesture_feed(uint8_t ch, uint8_t input_ev) {
    report_gesture(ch, gesture_event(&g_ch[ch].gesture, input_ev, hal_millis()));
}

uint16_t hk_dimmer_to_module(uint8_t *buf, uint16_t max) {
    uint16_t n = g_out_len < max ? g_out_len : max;

    if (n == 0) {
        return 0;
    }
    memcpy(buf, g_out, n);
    memmove(g_out, g_out + n, (size_t)(g_out_len - n));
    g_out_len = (uint16_t)(g_out_len - n);
    return n;
}

/* ---- the module's frames -------------------------------------------------------------------- */

static uint32_t unit_value(const uint8_t *v, uint16_t len) {
    uint32_t x = 0;

    for (uint16_t i = 0; i < len && i < 4; i++) {
        x = (x << 8) | v[i];
    }
    return x;
}

/* The settings DPs of this firmware: the field of DP dpid, or NULL. max: its largest value. */
static uint16_t *setting_dp(uint8_t dpid, uint32_t *max) {
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        if (dpid == HK_DP_RAMP(ch)) {
            *max = 0xFFFF;
            return &g_nv.ramp[ch];
        }
        if (dpid == HK_DP_FADE_ON(ch)) {
            *max = HK_FADE_MAX_MS;
            return &g_nv.fade_on[ch];
        }
        if (dpid == HK_DP_FADE_OFF(ch)) {
            *max = HK_FADE_MAX_MS;
            return &g_nv.fade_off[ch];
        }
        if (dpid == HK_DP_HOLD(ch)) {
            *max = HK_FADE_MAX_MS;
            return &g_nv.hold[ch];
        }
    }
    return NULL;
}

/* One of Open Dimmer's settings DPs (162..173: HK_DP_OD) written, as its dimmer_write_dp takes
 * them; a value out of range is refused (the answer: the one kept).
 * A new wall switch mode (Open Dimmer's controls_poll, keep in sync): no gesture of the old mode
 * is finished, a momentary press under way is not acted on when let go (set here, before its
 * release can come), any other press is this mode's; a hold under way and a state switch's
 * contact: mode_check (from hk_dimmer_poll: they report, and the answer is being built here). */
static void write_od(uint8_t *f, uint8_t dpid, uint32_t x) {
    uint8_t       ch = (uint8_t)((dpid - HK_DP_OD_BASE) & 1u);
    uint8_t       k  = (uint8_t)((dpid - HK_DP_OD_BASE) >> 1);
    hk_channel_t *r  = &g_ch[ch];

    switch (k) {
    case HK_OD_INPUT:
        if (x <= INPUT_OFF && x != g_nv.input_mode[ch]) {
            g_nv.input_mode[ch] = (uint8_t)x;
            gesture_init(&r->gesture);
            r->skip_release = r->btn.pressed && g_nv.ch[ch].switch_type == SWITCH_MOMENTARY;
            r->hold_press   = 0;
            nv_changed();
        }
        break;
    case HK_OD_POWER_LEVEL:
        /* in steps of 4 within 10..1000; 0 the last level */
        g_nv.power_level4[ch] =
            x ? (uint8_t)(((x < VALUE_MIN ? VALUE_MIN : x > VALUE_MAX ? VALUE_MAX : x) + 2u) >> 2)
              : 0;
        nv_changed();
        break;
    case HK_OD_IDENTIFY:
        identify(ch, (uint8_t)(x > HK_IDENTIFY_MAX_S ? HK_IDENTIFY_MAX_S : x));
        break;
    case HK_OD_BOOST:
        if (x <= HK_BOOST_MAX_MS) {
            g_nv.boost50[ch] = (uint8_t)(x / HK_BOOST_STEP_MS);
            nv_changed();
        }
        break;
    case HK_OD_COMP:
        /* of 1000, kept in steps of 4 (the output takes it at the next frame) */
        if (x <= HK_COMP_MAX) {
            g_nv.comp4[ch] = (uint8_t)((x + 2u) / 4u);
            nv_changed();
        }
        break;
    default:
        if (x <= 1) {
            g_nv.fast_press[ch] = (uint8_t)x;
            nv_changed();
        }
        break;
    }
    add_od_dp(f, ch, k);
}

/* One DP written by the module; added to the answer f with the value it now has. */
static void write_dp(uint8_t *f, uint8_t dpid, const uint8_t *v, uint16_t len) {
    uint32_t  max;
    uint16_t *setting = setting_dp(dpid, &max);

    if (setting != NULL) {
        uint32_t x = unit_value(v, len);

        *setting = (uint16_t)(x > max ? max : x);
        if (dpid >= HK_DP_HOLD(0) && *setting < 100) {
            *setting = 100;     /* a hold needs some time */
        }
        for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
            profile_set(ch);    /* the fader takes it now (Open Dimmer's apply at the write) */
        }
        nv_changed();
        add_dp(f, dpid, DP_TYPE_VALUE, *setting);
        return;
    }
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        if (dpid == HK_DP_TIME_BASE + ch) {
            /* The time of this channel's changes of the next second (0: the settings again). */
            uint32_t x = unit_value(v, len);

            g_ch[ch].time_ms = x > HK_TIME_MAX_MS ? HK_TIME_MAX_MS : x;
            g_ch[ch].time_at = hal_millis();
            add_dp(f, dpid, DP_TYPE_VALUE, g_ch[ch].time_ms);
            return;
        }
        if (dpid == HK_DP_CURVE(ch)) {
            uint32_t x = unit_value(v, len);

            if (x <= CURVE_CUSTOM) {
                g_nv.curve[ch] = (uint8_t)x;    /* the output follows at the next frame */
                nv_changed();
            }
            add_dp(f, dpid, DP_TYPE_ENUM, g_nv.curve[ch]);
            return;
        }
    }
    if (dpid == HK_DP_POWER_ON || dpid == HK_DP_POWER_ON_CH(0) || dpid == HK_DP_POWER_ON_CH(1)) {
        /* DP 14 (the factory MCU's): both channels, answered with channel 1's and both of
         * Open Dimmer's 103 / 104 (its dimmer_sibling_dp); 103 / 104: that channel. */
        uint32_t x = unit_value(v, len);

        for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
            if (x <= POWER_ON_PREVIOUS &&
                (dpid == HK_DP_POWER_ON || dpid == HK_DP_POWER_ON_CH(ch))) {
                *power_on_of(ch) = (uint8_t)x;
                nv_changed();
            }
        }
        if (dpid == HK_DP_POWER_ON) {
            add_dp(f, HK_DP_POWER_ON, DP_TYPE_ENUM, g_nv.power_on);
        }
        for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
            if (dpid == HK_DP_POWER_ON || dpid == HK_DP_POWER_ON_CH(ch)) {
                add_dp(f, (uint8_t)HK_DP_POWER_ON_CH(ch), DP_TYPE_ENUM, *power_on_of(ch));
            }
        }
        return;
    }
    if (dpid >= HK_DP_OD_BASE && dpid < HK_DP_OD(HK_OD_KEPT, 0)) {
        write_od(f, dpid, unit_value(v, len));
        return;
    }
    if (dpid == HK_DP_SWITCH_OPTS) {
        /* Open Dimmer 2.2.8's: only the bits in the mask change (mask << 8 | bits). */
        uint32_t x = unit_value(v, len);

        if (x <= 0x0F0Fu) {
            g_nv.switch_opts = (uint8_t)((g_nv.switch_opts & ~(x >> 8)) | (x & (x >> 8)));
            nv_changed();
        }
        add_dp(f, HK_DP_SWITCH_OPTS, DP_TYPE_VALUE, g_nv.switch_opts);
        return;
    }
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        if (dpid < HK_DP_BASE(ch) || dpid > HK_DP_BASE(ch) + HK_DP_COUNTDOWN) {
            continue;
        }
        hk_channel_nv_t *c       = &g_nv.ch[ch];
        uint8_t          which   = (uint8_t)(dpid - HK_DP_BASE(ch));
        uint32_t         x       = unit_value(v, len);
        uint8_t          holding = finger(&g_ch[ch]);

        uint8_t save = 1;

        switch (which) {
        case HK_DP_ONOFF:
            /* The module writes on before every level change with on: only a change is saved. */
            save = c->on != (x != 0);
            if (save) {
                c->on = x != 0;
                on_changed(ch);
            }
            break;
        case HK_DP_LEVEL:
            /* While lit saved a minute later at the latest (nv_changed_lit): an automation's
             * steps wear little flash; the off saves the level, so does a hold's end. Under the
             * finger refused: it has the level, the answer is the hold's. A press from dark
             * before its hold time takes it (the module's OnLevel after the on: a tap goes
             * there; the fader, holding, waits at the minimum meanwhile). */
            save = !c->on;
            if (finger(&g_ch[ch])) {
                break;
            }
            x = clamp_value(c, x);
            if (x != c->value) {
                c->value = (uint16_t)x;
                if (c->on) {
                    apply(ch);
                    nv_changed_lit();
                }
            }
            break;
        case HK_DP_MIN:
        case HK_DP_MAX:
            /* As Open Dimmer's DP_MIN / DP_MAX: taken (10..1000), the other limit pushed along
             * when they cross (equal: a fixed level), the level kept within them; the answer
             * has the other limit and the level too (its dimmer_sibling_dp). */
            if (x < VALUE_MIN) {
                x = VALUE_MIN;
            }
            if (x > VALUE_MAX) {
                x = VALUE_MAX;
            }
            if (which == HK_DP_MIN) {
                c->min = (uint16_t)x;
                if (c->max < c->min) {
                    c->max = c->min;
                }
            } else {
                c->max = (uint16_t)x;
                if (c->min > c->max) {
                    c->min = c->max;
                }
            }
            profile_set(ch);    /* the slow zones and the hold's rate follow the range */
            if (clamp_value(c, c->value) != c->value) {
                c->value = clamp_value(c, c->value);
                if (c->on && !g_ch[ch].holding) {
                    apply(ch);
                }
            }
            break;
        case HK_DP_SWITCH_TYPE:
            if (x <= SWITCH_MOMENTARY) {
                c->switch_type = (uint8_t)x;
            }
            gesture_init(&g_ch[ch].gesture);   /* none of the old type's is finished */
            if (g_ch[ch].holding) {
                /* A hold ends without its level: the light as it was (Open Dimmer). The rest
                 * (a state switch's contact): mode_check, from the poll. */
                end_hold(ch);
                apply(ch);
            }
            break;
        case HK_DP_COUNTDOWN:
            /* Auto-off, counted only while on (written while off it stays 0, as the answer
             * says); RAM only (Open Dimmer's): a restart of the module ends it. */
            g_ch[ch].countdown = c->on ? (x > HK_COUNTDOWN_MAX ? HK_COUNTDOWN_MAX : x) : 0;
            g_ch[ch].cd_end    = hal_millis() + g_ch[ch].countdown * 1000u;
            save = 0;
            break;
        default:
            return;     /* (none: which is 0..HK_DP_COUNTDOWN) */
        }
        if (save) {
            nv_changed();
        }
        add_channel_dp(f, ch, which);
        if (which == HK_DP_MIN || which == HK_DP_MAX) {
            add_channel_dp(f, ch, (uint8_t)(HK_DP_MIN + HK_DP_MAX - which));   /* the other */
            add_channel_dp(f, ch, HK_DP_LEVEL);
        } else if (holding && !finger(&g_ch[ch]) && which != HK_DP_LEVEL) {
            add_channel_dp(f, ch, HK_DP_LEVEL);     /* a hold ended: the level kept again */
        }
        return;
    }
}

static void handle_frame(uint16_t seq, uint8_t cmd, const uint8_t *data, uint16_t dlen) {
    uint8_t *f;

    switch (cmd) {
    case CMD_WRITE:
        f = out_frame_begin(CMD_REPORT_PASSIVE, seq);
        if (f == NULL) {
            return;
        }
        for (uint16_t i = 0; i + 4 <= dlen;) {
            uint16_t len = (uint16_t)((data[i + 2] << 8) | data[i + 3]);

            if (i + 4 + len > dlen) {
                break;
            }
            write_dp(f, data[i], &data[i + 4], len);
            i = (uint16_t)(i + 4 + len);
        }
        if (f[6] | f[7]) {
            out_frame_end(f);   /* a DP not kept here gets no answer (as Open Dimmer) */
        }
        return;

    case CMD_QUERY_ALL_DP:
        /* Per channel its DPs 1..6 / 7..12 and Open Dimmer's (as its dimmer_report_all:
         * power-on, the settings 162.., the lamp compensation; not identify, the gestures or the
         * transition time); DP 14, the switch options (180) and this module's settings after
         * them. */
        for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
            f = out_frame_begin(CMD_REPORT_PASSIVE, seq);
            if (f == NULL) {
                return;
            }
            for (uint8_t which = HK_DP_ONOFF; which <= HK_DP_COUNTDOWN; which++) {
                add_channel_dp(f, ch, which);
            }
            add_dp(f, (uint8_t)HK_DP_POWER_ON_CH(ch), DP_TYPE_ENUM, *power_on_of(ch));
            for (uint8_t k = HK_OD_INPUT; k < HK_OD_KEPT; k++) {
                if (k != HK_OD_IDENTIFY) {
                    add_od_dp(f, ch, k);
                }
            }
            out_frame_end(f);
        }
        f = out_frame_begin(CMD_REPORT_PASSIVE, seq);
        if (f != NULL) {
            add_dp(f, HK_DP_POWER_ON, DP_TYPE_ENUM, g_nv.power_on);
            add_dp(f, HK_DP_SWITCH_OPTS, DP_TYPE_VALUE, g_nv.switch_opts);
            for (uint8_t dp = HK_DP_RAMP(0); dp <= HK_DP_HOLD(HK_CHANNELS - 1); dp++) {
                uint32_t        max;
                const uint16_t *setting = setting_dp(dp, &max);

                if (setting != NULL) {
                    add_dp(f, dp, DP_TYPE_VALUE, *setting);
                }
            }
            for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
                add_dp(f, HK_DP_CURVE(ch), DP_TYPE_ENUM, g_nv.curve[ch]);
            }
            out_frame_end(f);
        }
        return;

    case CMD_MCU_VERSION: {
        uint8_t version = HK_VERSION;

        f = out_frame_begin(CMD_MCU_VERSION, seq);
        if (f != NULL) {
            out_frame_add(f, &version, 1);
            out_frame_end(f);
        }
        return;
    }

    case CMD_NETWORK_STATUS:
        /* Only acknowledged: the LED follows the network itself (led_update). */
        f = out_frame_begin(CMD_NETWORK_STATUS, seq);
        if (f != NULL) {
            out_frame_end(f);
        }
        return;

    default:
        return;
    }
}

void hk_dimmer_from_module(const uint8_t *frame, uint16_t len) {
    while (len >= TUYA_HEADER_LEN + 1) {
        if (frame[0] != 0x55 || frame[1] != 0xAA) {
            frame++;
            len--;
            continue;
        }
        uint16_t dlen = (uint16_t)((frame[6] << 8) | frame[7]);
        uint32_t n    = TUYA_HEADER_LEN + (uint32_t)dlen + 1u;

        if (n > len) {
            return;
        }
        handle_frame((uint16_t)((frame[3] << 8) | frame[4]), frame[5], &frame[TUYA_HEADER_LEN],
                     dlen);
        frame += n;
        len    = (uint16_t)(len - n);
    }
}

/* ---- wall switches -------------------------------------------------------------------------- */

/* A change made at the module (wall switch, button): at the fade settings, never in a time the
 * network sent for its own changes (Open Dimmer's apply_local), nor what the module writes on
 * top of it (its on level after a wall switch on). */
static void set_on(uint8_t ch, uint8_t on) {
    hk_channel_nv_t *c = &g_nv.ch[ch];

    uint8_t what = finger(&g_ch[ch]) ? REPORT_ON | REPORT_LEVEL : REPORT_ON;  /* a hold ends */

    if (c->on == on) {
        return;
    }
    if (!on && g_ch[ch].countdown) {
        what |= REPORT_COUNTDOWN;   /* the off ends it */
    }
    c->on            = on;
    g_ch[ch].time_ms = 0;
    on_changed(ch);
    nv_changed();
    report_change(ch, what);
}

/* A network reset by wall switch presses (Open Dimmer's controls.c reset_press, DP 181 as it
 * acts; keep in sync): a closure of a wall switch (any type, normal or detached; never one set
 * off), the multi_press_reset_count closures of one switch, each within HK_RESET_WINDOW_MS of the
 * first, ask for the leave and pairing (as the module button's hold); 0: never, 1..7 act as 8
 * (quick taps and toggling are no reset). The switch's channel blinks HK_RESET_BLINK_S first (the
 * request restarts this module, which blinks the light). */
static void reset_press(uint8_t ch) {
    uint8_t  need = g_multi_press_reset_count;
    uint32_t now  = hal_millis();

    if (need && need < HK_RESET_PRESSES_LEAST) {
        need = HK_RESET_PRESSES_LEAST;
    }
    if (!need || g_nv.input_mode[ch] == INPUT_OFF) {
        g_reset_count = 0;
        return;
    }
    if (!g_reset_count || ch != g_reset_input || now - g_reset_start >= HK_RESET_WINDOW_MS) {
        g_reset_count = 0;
        g_reset_input = ch;
        g_reset_start = now;
    }
    if (++g_reset_count >= need) {
        g_reset_count = 0;
        identify(ch, HK_RESET_BLINK_S);
        g_reset_at = (now + HK_RESET_BLINK_S * 1000u) | 1u;
    }
}

/* The reset request (0x03, rejoin): the module leaves its network and pairs again. */
static void send_reset_request(void) {
    uint8_t  rejoin = 0x01;
    uint8_t *f      = out_frame_begin(CMD_RESET_NETWORK, g_seq++);

    if (f != NULL) {
        out_frame_add(f, &rejoin, 1);
        out_frame_end(f);
    }
}

/* An event (INPUT_EV_*) of a wall switch whose mode is not normal (Open Dimmer's controls.c
 * wall_switch; keep in sync). Detached: the light is the network's, the switch only reports: a
 * momentary its gestures (with the fast press: the press at once as 6, a hold and its release,
 * no multi-taps), a toggle or state switch each change as a single. Off: ignored. */
static void detached_event(uint8_t ch, uint8_t ev) {
    uint8_t g;

    if (g_nv.input_mode[ch] != INPUT_DETACHED) {
        return;
    }
    if (g_nv.ch[ch].switch_type != SWITCH_MOMENTARY) {
        g = ev != INPUT_EV_HOLD ? GESTURE_SINGLE : GESTURE_NONE;
    } else if (g_nv.fast_press[ch]) {
        g = ev == INPUT_EV_PRESS ? GESTURE_PRESS : ev == INPUT_EV_HOLD ? GESTURE_HOLD
          : ev == INPUT_EV_RELEASE_AFTER_HOLD ? GESTURE_RELEASE : GESTURE_NONE;
    } else {
        g = gesture_event(&g_ch[ch].gesture, ev, hal_millis());
    }
    report_gesture(ch, g);
}

/* A momentary press that began in another wall switch mode: its hold and release are not this
 * mode's (OD's g_skip_release); 1: this event is skipped. */
static uint8_t skipped(uint8_t ch, uint8_t ev) {
    hk_channel_t *r = &g_ch[ch];

    if (!r->skip_release) {
        return 0;
    }
    if (ev != INPUT_EV_HOLD || g_nv.ch[ch].switch_type != SWITCH_MOMENTARY) {
        r->skip_release = 0;    /* a release ends it; a switch type no longer momentary too */
    }
    return g_nv.ch[ch].switch_type == SWITCH_MOMENTARY;
}

/* ---- a momentary wall switch: Open Dimmer's (controls.c wall_switch, dimmer.c dimmer_hold_*,
 * learn_verdict, dimmer_local_full; keep in sync) ----------------------------------------- */

/* The hold's level reports, from now on every HK_HOLD_REPORT_MS (Open Dimmer's
 * dimmer_hold_reports): a lit hold from its start, one from dark from its hold time. */
static void hold_reports(uint8_t ch) {
    g_ch[ch].rep_at  = (hal_millis() + HK_HOLD_REPORT_MS) | 1u;
    g_ch[ch].rep_val = g_nv.ch[ch].value;   /* what the module has */
}

/* The verdict on a learnt hold, at the next hold or at its end at the end of the range: turned
 * round within HOLD_SEQ_MS after being let go within HOLD_WRONG_MS, it went the wrong way. */
static void learn_verdict(uint8_t ch, uint8_t turned_round) {
    hk_channel_t *r    = &g_ch[ch];
    uint8_t       band = r->lrn_band;

    if (!band) {
        return;
    }
    r->lrn_band = 0;
    uint8_t bit = (uint8_t)(1u << (ch * HOLD_BANDS + band - 1u));

    if (!turned_round || r->lrn_t1 - r->lrn_t0 >= HOLD_WRONG_MS) {
        g_lrn_sure |= bit;            /* kept, or a long hold corrected: the right way */
    } else if (g_lrn_sure & bit) {
        g_lrn_sure &= (uint8_t) ~bit; /* confirmed before: one miss is not enough */
    } else {
        g_nv.lrn_ways ^= bit;         /* the band learns the other way */
        nv_changed();
    }
}

/* Hold dimming (Open Dimmer's dimmer_hold_begin): dir 0 picks the way (up from dark or near the
 * minimum, down near the maximum, within HOLD_SEQ_MS of the last hold's end the other way, else
 * the far end or the way learnt for the band of where the light is); from_dark 1: the rise from
 * the minimum (none if long_press_from_off is off: the light comes on at its level), 2: a long
 * press from off without the rise (its way picked by the level it comes on to). Going down, or
 * rising from dark, it slows down in the hold's slow zone (not a lit hold with the custom curve:
 * that one is even); a hold down counts as a fall for the lamp compensation (phase.c
 * phase_hold_start). Its level reports from its start (from dark: from its hold time). */
static void hold_begin(uint8_t ch, int8_t dir, uint8_t from_dark) {
    hk_channel_t *         r = &g_ch[ch];
    const hk_channel_nv_t *c = &g_nv.ch[ch];
    uint32_t now             = hal_millis();

    if (!c->on || (from_dark == 1 && (g_nv.switch_opts & HK_OPT_NO_RISE(ch)))) {
        return;
    }
    profile_set(ch);    /* a lit hold: the settings as they are now (no apply since a write) */
    uint16_t at  = from_dark == 2 ? c->value : r->f.current;
    uint8_t  seq = r->lrn_t1 && now - r->lrn_t1 <= HOLD_SEQ_MS;

    from_dark = from_dark == 1;
    learn_verdict(ch, seq);
    if (dir == 0) {
        if (from_dark || at <= c->min + 2 * DIM_HOLD_RATE) {
            dir = +1;
        } else if (at + 2 * DIM_HOLD_RATE >= c->max) {
            dir = -1;
        } else if (seq && r->last_dir) {
            dir = (int8_t)-r->last_dir;          /* a correction: the other way */
        } else {                                 /* towards the far end, or the way learnt here */
            uint8_t band = (uint8_t)((unsigned)(at - c->min) * HOLD_BANDS /
                                     (unsigned)(c->max - c->min + 1u));

            dir = band >= HOLD_BANDS / 2u ? -1 : +1;
            if (g_nv.lrn_ways & (1u << (ch * HOLD_BANDS + band))) {
                dir = (int8_t)-dir;
            }
            r->lrn_band = (uint8_t)(band + 1u);
        }
    }

    /* The dimming MCU's own ramp runs (apply): the hold stays where the lamp is going until that
     * ramp's end (its frames at ramp 0 jumped the lamp there from where it was on the way); a
     * frame of it that still waits goes with that ramp. */
    uint32_t wait = mcu_ramp_left(ch, now);

    r->lrn_t0   = now;
    r->last_dir = dir;
    r->holding  = 1;
    r->time_ms  = 0;
    r->kick     = 0;
    if (from_dark) {
        dim_fader_hold_start_from_dark(&r->f, c->min, c->max);
    } else {
        dim_fader_hold_start(&r->f, dir, c->min, c->max);
        if (dir < 0) {
            r->down = 1;
        }
        if (g_nv.curve[ch] == CURVE_CUSTOM) {
            r->f.hold_fine = 0;
        }
    }
    if (wait > r->f.hold_wait && !r->f.boost_left) {
        r->f.hold_wait = (uint16_t)wait;    /* mcu_ramp is 16 bits; a start boost goes at once */
    }
    HK_TRACE(HK_EV_HOLD_BEGIN, ch, (uint32_t)(uint8_t)dir << 16 | at);
    r->rep_at = 0;
    if (!from_dark) {
        hold_reports(ch);
    }
}

/* The end of a hold (Open Dimmer's dimmer_hold_end): the level the light has is kept and
 * reported (switched off during it: it stays off); taken to the end of the range, its way was
 * right. Nothing if no hold runs (an off, the cap, a switch type change ended it). */
static void hold_end(uint8_t ch) {
    hk_channel_t *   r   = &g_ch[ch];
    hk_channel_nv_t *c   = &g_nv.ch[ch];
    uint32_t         now = hal_millis();

    if (!r->holding) {
        return;
    }
    r->holding = 0;
    r->lrn_t1  = now ? now : 1u;
    uint16_t v = dim_fader_hold_stop(&r->f);

    HK_TRACE(HK_EV_HOLD_END, ch, v);
    if (v <= c->min + 2 * DIM_HOLD_RATE || v + 2 * DIM_HOLD_RATE >= c->max) {
        learn_verdict(ch, 0);
    }
    if (!c->on) {
        apply(ch);
        return;
    }
    c->value   = clamp_value(c, v);
    r->time_ms = 0;
    apply(ch);
    nv_changed();       /* a level picked by hand is kept */
    report_change(ch, REPORT_ON | REPORT_LEVEL);
}

/* A press from dark that was a tap (Open Dimmer's dimmer_hold_cancel): on to the level, quickly
 * (the on rate, from the minimum), nothing new to report. */
static void hold_cancel(uint8_t ch) {
    hk_channel_t *r = &g_ch[ch];

    if (!r->holding) {
        return;
    }
    r->holding = 0;
    dim_fader_hold_cancel(&r->f);
    r->time_ms = 0;
    apply(ch);
}

/* A double tap (Open Dimmer's dimmer_local_full): the channel on at its max, a press from dark
 * ended; the level reported too (the module would keep the one before). */
static void local_full(uint8_t ch) {
    hk_channel_t *   r = &g_ch[ch];
    hk_channel_nv_t *c = &g_nv.ch[ch];

    if (r->holding) {
        r->holding = 0;
        dim_fader_hold_cancel(&r->f);
    }
    c->on      = 1;
    c->value   = c->max;
    r->time_ms = 0;
    apply(ch);
    warm_keep();
    nv_changed();
    report_change(ch, REPORT_ON | REPORT_LEVEL);
}

/* A press (Open Dimmer's controls.c wall_switch). A momentary switch from dark: on at once at
 * the minimum, the hold's rise waiting there DIM_HOLD_DARK_WAIT_MS (with long_press_from_off off:
 * on at its level); its release as a tap goes on quickly to the level; within GESTURE_GAP_MS of a
 * tap's release (and double_tap_full on) a press that is a tap too takes the light to full at its
 * release, from either state (a tap and a hold still dims). */
static void on_switch_press(void *arg) {
    uint8_t          ch  = *(uint8_t *)arg;
    hk_channel_t *   r   = &g_ch[ch];
    hk_channel_nv_t *c   = &g_nv.ch[ch];
    uint32_t         now = hal_millis();

    r->skip_release = 0;      /* a new press is this mode's */
    r->hold_press   = 0;
    reset_press(ch);
    if (g_nv.input_mode[ch] != INPUT_NORMAL) {
        detached_event(ch, INPUT_EV_PRESS);
        return;
    }
    switch (c->switch_type) {
    case SWITCH_TOGGLE:
        set_on(ch, !c->on);
        break;
    case SWITCH_STATE:
        set_on(ch, 1);
        break;
    default:
        r->dbl = !(g_nv.switch_opts & HK_OPT_NO_DOUBLE(ch)) && r->tap_at &&
                 now - r->tap_at <= GESTURE_GAP_MS;
        r->tap_at    = 0;
        r->turned_on = !c->on;
        if (!c->on) {
            r->rise_next = !(g_nv.switch_opts & HK_OPT_NO_RISE(ch));
            set_on(ch, 1);
            r->rise_next = 0;
            hold_begin(ch, +1, 1);
        }
        gesture_feed(ch, INPUT_EV_PRESS);
        break;
    }
}

/* The hold time: a momentary switch's press became a hold. From a lit light a hold begins (its
 * way picked by where the light is); from dark the rise runs since the press (its reports from
 * now), or, with long_press_from_off off, a hold begins from the level the light comes on to. */
static void on_switch_long(void *arg) {
    uint8_t       ch = *(uint8_t *)arg;
    hk_channel_t *r  = &g_ch[ch];

    if (skipped(ch, INPUT_EV_HOLD)) {
        return;
    }
    if (g_nv.input_mode[ch] != INPUT_NORMAL) {
        r->hold_press = 1;      /* its release is one after a hold */
        detached_event(ch, INPUT_EV_HOLD);
        return;
    }
    if (g_nv.ch[ch].switch_type != SWITCH_MOMENTARY) {
        return;
    }
    r->hold_press = 1;
    r->time_ms    = 0;
    if (!r->turned_on) {
        hold_begin(ch, 0, 0);
    } else if (!r->holding) {
        hold_begin(ch, 0, 2);
    } else {
        hold_reports(ch);
    }
    gesture_feed(ch, INPUT_EV_HOLD);
}

/* A hold on the way: its level to the module every HK_HOLD_REPORT_MS when it moved (Home
 * Assistant and the lights bound to the wall switch follow it, as with Open Dimmer); and its end
 * at the cap, 2 x hold_time + HK_HOLD_CAP_MS from the press (a switch stuck closed; Open
 * Dimmer's): the level is taken there and the release changes nothing. */
static void hold_tick(uint8_t ch, uint32_t now) {
    hk_channel_t *r = &g_ch[ch];

    if (!r->holding) {
        return;
    }
    if (now - r->btn.pressed_at_ms > 2u * g_nv.hold[ch] + HK_HOLD_CAP_MS) {
        hold_end(ch);
        return;
    }
    if (r->rep_at && (int32_t)(now - r->rep_at) >= 0) {
        uint16_t v = shown_value(ch);

        r->rep_at = (now + HK_HOLD_REPORT_MS) | 1u;
        if (v != r->rep_val) {
            r->rep_val = v;
            report_change(ch, REPORT_LEVEL);
        }
    }
}

static void on_switch_release(void *arg) {
    uint8_t          ch = *(uint8_t *)arg;
    hk_channel_t *   r  = &g_ch[ch];
    hk_channel_nv_t *c  = &g_nv.ch[ch];

    if (r->boot_held) {
        /* The release of a press that began before the start (Open Dimmer's g_boot_held): a
         * toggle or state switch's real change; a momentary switch's nothing. */
        r->boot_held = 0;
        if (c->switch_type == SWITCH_MOMENTARY) {
            return;
        }
    }
    if (skipped(ch, INPUT_EV_RELEASE)) {
        r->hold_press = 0;
        return;
    }
    if (g_nv.input_mode[ch] != INPUT_NORMAL) {
        detached_event(ch, r->hold_press ? INPUT_EV_RELEASE_AFTER_HOLD : INPUT_EV_RELEASE);
        r->hold_press = 0;
        return;
    }
    if (r->hold_press) {
        /* A press that became a hold: its level where it is now (nothing if it ended); its
         * release (the gesture) after that level. */
        r->hold_press = 0;
        hold_end(ch);
        if (c->switch_type == SWITCH_MOMENTARY) {
            gesture_feed(ch, INPUT_EV_RELEASE_AFTER_HOLD);
        }
        return;
    }
    switch (c->switch_type) {
    case SWITCH_STATE:
        set_on(ch, 0);
        break;
    case SWITCH_MOMENTARY:
        if (r->dbl) {
            r->dbl = 0;
            local_full(ch);             /* a third tap is a plain one again */
        } else {
            r->tap_at = hal_millis() | 1u;
            if (r->turned_on) {
                hold_cancel(ch);
            } else {
                set_on(ch, 0);
            }
        }
        gesture_feed(ch, INPUT_EV_RELEASE);
        break;
    default:
        set_on(ch, !c->on);   /* a toggle switch's change */
        break;
    }
}

/* A new switch type (DP 4 / 10) or wall switch mode (DP 162 / 163, write_od; Open Dimmer's
 * controls_poll and end_hold, keep in sync), here, not in the DP write, whose answer is being
 * built: no press state of the old one is kept; a hold under way at a mode change ends where the
 * light is (its level kept and reported); and a state switch in the normal mode says what the
 * light is (a type change to state, back in normal). */
static void mode_check(uint8_t ch) {
    hk_channel_t *r     = &g_ch[ch];
    uint8_t       mode  = g_nv.input_mode[ch];
    uint8_t       type  = g_nv.ch[ch].switch_type;
    uint8_t       again = 0;

    if (type != r->type_seen) {
        r->type_seen = type;
        gesture_init(&r->gesture);
        r->turned_on = 0;
        again        = 1;
    }
    if (mode != r->mode_seen) {
        r->mode_seen = mode;
        r->turned_on = 0;
        hold_end(ch);
        again = 1;
    }
    if (again && type == SWITCH_STATE && mode == INPUT_NORMAL && r->pin != HAL_INVALID_PIN) {
        set_on(ch, r->btn.pressed);
    }
}

/* ---- module button and LED ------------------------------------------------------------------ */

/* The LED (active high: measured on a TS110E) blinks while the module is not on a network and
 * is dark on one; lit while the module button has been held long enough for the re-pair (let
 * go now). The network is read here, not from the module's status frames: it tells a Tuya MCU
 * only of a join, and a status query would make the module refuse the button's reset request
 * for 3 s. */
static void led_set(uint8_t lit) {
    g_led_lit = lit;
    hal_gpio_write(g_led_pin, lit ? 1 : 0);
}

static void led_update(void *arg) {
    (void)arg;
    if (g_button_long) {
        led_set(1);
    } else if (hal_zigbee_get_network_status() == HAL_ZIGBEE_NETWORK_JOINED) {
        led_set(0);
    } else {
        led_set(!g_led_lit);
    }
    hal_tasks_schedule(&g_led_task, HK_LED_BLINK_MS);
}

/* A press since the start: the release of one held through it does nothing (as Open Dimmer). */
static void on_button_press(void *arg) {
    (void)arg;
    g_button_seen = 1;
}

static void on_button_long(void *arg) {
    (void)arg;
    g_button_long = g_button_seen;
    if (g_button_long && g_led_pin != HAL_INVALID_PIN) {
        led_set(1);
    }
}

/* The module button (as Open Dimmer's): a press switches both channels (any on: all off, else
 * all on); held HK_RESET_PRESS_MS and let go, the reset request (the module leaves its network
 * and pairs again). */
static void on_button_release(void *arg) {
    (void)arg;
    uint8_t seen = g_button_seen;
    uint8_t held = g_button_long;
    uint8_t any  = 0;

    g_button_seen = 0;
    g_button_long = 0;
    if (!seen) {
        return;
    }
    if (held) {
        send_reset_request();
        return;
    }
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        any |= g_nv.ch[ch].on;
    }
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        set_on(ch, !any);
    }
}

/* ---- the module's own attributes: the custom curves (Basic 0xff3a / 0xff3b), the slow zones
 * (0xff3c..0xff3f) --------------------------------------------------------------------------- */

/* The slow zones' attributes as NV has them (fade_off_slow_zone L1, L2, hold_slow_zone L1, L2). */
static void zones_show(void) {
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        g_zone_attr[ch] = g_nv.soft_end[ch];
        g_zone_attr[HK_CHANNELS + ch] = g_nv.hold_end[ch];
    }
}

uint8_t hk_dimmer_register_attrs(hal_zigbee_attribute *table, uint8_t start) {
    if (!g_active) {
        return 0;
    }
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        SETUP_ATTR_FOR_TABLE(table, start + ch, HK_ATTR_CUSTOM_CURVE + ch, ZCL_DATA_TYPE_OCTET_STR,
                             ATTR_WRITABLE, g_custom[ch]);
        table[start + ch].size = 0xFFu;     /* the field's most (uint8_t); the buffer has 256 */
    }
    for (uint8_t i = 0; i < 2 * HK_CHANNELS; i++) {
        SETUP_ATTR_FOR_TABLE(table, start + HK_CHANNELS + i, HK_ATTR_SLOW_ZONE + i,
                             ZCL_DATA_TYPE_UINT32, ATTR_WRITABLE, g_zone_attr[i]);
    }
    return HK_BASIC_ATTRS;
}

/* A slow zone written (% of min..max, Open Dimmer's DP 152 / 157 and 154 / 159; up to 100): kept,
 * and the fader's profile from now on (a fade on its way too). */
static void zone_written(uint8_t i) {
    uint8_t  ch = (uint8_t)(i % HK_CHANNELS);
    uint8_t *z  = i < HK_CHANNELS ? &g_nv.soft_end[ch] : &g_nv.hold_end[ch];

    if (g_zone_attr[i] > 100) {
        g_zone_attr[i] = 100;
    }
    if (*z != g_zone_attr[i]) {
        *z = (uint8_t)g_zone_attr[i];
        nv_changed();
    }
    profile_set(ch);
}

/* A custom curve: a whole valid table or none (length 0) is taken and kept; anything else: the one
 * kept is shown again. The output follows at the next frame. */
void hk_dimmer_on_attr_write(uint16_t attr) {
    if (!g_active) {
        return;
    }
    if (attr >= HK_ATTR_SLOW_ZONE && attr < HK_ATTR_SLOW_ZONE + 2 * HK_CHANNELS) {
        zone_written((uint8_t)(attr - HK_ATTR_SLOW_ZONE));
        return;
    }
    if (attr < HK_ATTR_CUSTOM_CURVE || attr >= HK_ATTR_CUSTOM_CURVE + HK_CHANNELS) {
        return;
    }
    uint8_t ch = (uint8_t)(attr - HK_ATTR_CUSTOM_CURVE);

    if (g_custom[ch].len != 0 && !custom_valid(ch)) {
        g_custom[ch].len = g_custom_nv.len[ch];
        memcpy(g_custom[ch].data, g_custom_nv.data[ch], CUSTOM_LEN);
        return;
    }
    if (g_custom_nv.len[ch] == g_custom[ch].len &&
        (g_custom[ch].len == 0 || memcmp(g_custom_nv.data[ch], g_custom[ch].data,
                                         CUSTOM_LEN) == 0)) {
        return;     /* as kept: no flash write */
    }
    g_custom_nv.len[ch] = g_custom[ch].len;
    memcpy(g_custom_nv.data[ch], g_custom[ch].data, CUSTOM_LEN);
    hal_nvm_write(NV_ITEM_HK_CUSTOM, sizeof(g_custom_nv), (uint8_t *)&g_custom_nv);
}

/* ---- setup ---------------------------------------------------------------------------------- */

void hk_dimmer_parse_token(const char *entry) {
    /* HK<s1><s2><button><led>: two characters per pin. */
    if (strlen(entry) < 10) {
        printf("HK token needs four pins: %s\r\n", entry);
        return;
    }
    g_ch[0].pin  = hal_gpio_parse_pin(entry + 2);
    g_ch[1].pin  = hal_gpio_parse_pin(entry + 4);
    g_button_pin = hal_gpio_parse_pin(entry + 6);
    g_led_pin    = hal_gpio_parse_pin(entry + 8);
    g_active     = 1;
}

uint8_t hk_dimmer_active(void) {
    return g_active;
}

static void init_input(button_t *b, hal_gpio_pin_t pin, uint16_t long_ms, void *param) {
    hal_gpio_init(pin, 1, HAL_GPIO_PULL_UP);
    b->pin = pin;
    b->pressed_when_high       = 0;
    b->long_press_duration_ms  = long_ms;
    b->multi_press_duration_ms = 0;
    b->debounce_delay_ms       = DEBOUNCE_DELAY_MS;
    b->callback_param          = param;
    btn_init(b);
}

void hk_dimmer_start(void) {
    if (!g_active) {
        return;
    }
    g_nv_task.handler = nv_save;
    hal_tasks_init(&g_nv_task);
    g_boot_task.handler = boot_send;
    g_boot_task.arg     = &g_boot_task;   /* the first sending */
    hal_tasks_init(&g_boot_task);
    g_resend_task.handler = boot_send;
    hal_tasks_init(&g_resend_task);
    g_led_task.handler = led_update;
    hal_tasks_init(&g_led_task);

    /* A reset of the module alone (an update, a config write, the watchdog; the kept byte is
     * cleared only by a power cut): the dimming MCU kept driving the light, which stays as it
     * was, on / off as the kept byte has it, at the level of NV (saved before a planned
     * restart; after a watchdog reset one of the last minute: nv_changed_lit), sent as it is
     * (no fade from dark). Else the power-on setting. */
    uint8_t kept = hal_system_kept_read();

    g_warm = (kept & HK_KEPT_MARK) == HK_KEPT_MAGIC;
    nv_load();
    zones_show();
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        hk_channel_nv_t *c = &g_nv.ch[ch];
        hk_channel_t *   r = &g_ch[ch];

        dim_fader_init(&r->f);
        profile_set(ch);
        r->mode_seen = g_nv.input_mode[ch];
        r->type_seen = c->switch_type;
        if (g_warm) {
            c->on = (kept >> ch) & 1u;
        } else {
            /* A power cut: the channel's power-on setting, and the level after a power cut
             * when one is set (Open Dimmer's dimmer_init: on or off by the power-on). */
            if (*power_on_of(ch) == POWER_ON_OFF) {
                c->on = 0;
            } else if (*power_on_of(ch) == POWER_ON_ON) {
                c->on = 1;
            }
            if (g_nv.power_level4[ch]) {
                c->value = (uint16_t)(g_nv.power_level4[ch] * 4u);
            }
        }
        c->value = clamp_value(c, c->value);
    }
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        hk_channel_nv_t *c = &g_nv.ch[ch];
        hk_channel_t *   r = &g_ch[ch];

        if (r->pin == HAL_INVALID_PIN) {
            continue;
        }
        r->btn.on_press      = on_switch_press;
        r->btn.on_long_press = on_switch_long;
        r->btn.on_release    = on_switch_release;
        init_input(&r->btn, r->pin, HK_LONG_PRESS_MS, &g_ch_index[ch]);

        /* A switch closed at the start (Open Dimmer's controls_init): its release is no
         * momentary press's; at a power-up a state switch in the normal mode says what the light
         * is, over the power-on setting (dimmer_boot_set: no report, the module's query has it).
         * Not at a warm start: the dimming MCU kept the light as it was, as a restart of the
         * module alone leaves Open Dimmer's (its controls_init does not run then); the
         * switch's next change acts as usual. */
        r->boot_held = r->btn.pressed;
        if (!g_warm && c->switch_type == SWITCH_STATE && g_nv.input_mode[ch] == INPUT_NORMAL) {
            c->on = r->btn.pressed;
        }
    }
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        hk_channel_nv_t *c = &g_nv.ch[ch];
        hk_channel_t *   r = &g_ch[ch];

        if (g_warm && c->on) {
            r->f.current = c->value;             /* lit through the restart: as it is */
            r->f.target  = c->value;
            r->sent      = hk_out(ch, c->value); /* what the MCU has: the other byte of a frame */
            r->sent_v    = c->value;
        }
    }
    warm_keep();

    /* Saved if NV holds another state (nv_store writes nothing if they agree): after a warm
     * start under power-on previous, an on / off whose save was not due yet at the reset would
     * else come back the old way after a later power cut (Open Dimmer's dimmer_init). */
    nv_changed();

    if (g_button_pin != HAL_INVALID_PIN) {
        g_button.on_press      = on_button_press;
        g_button.on_long_press = on_button_long;
        g_button.on_release    = on_button_release;
        init_input(&g_button, g_button_pin, HK_RESET_PRESS_MS, NULL);
    }
    if (g_led_pin != HAL_INVALID_PIN) {
        hal_gpio_init(g_led_pin, 0, HAL_GPIO_PULL_NONE);
        hal_gpio_write(g_led_pin, 0);
        hal_tasks_schedule(&g_led_task, HK_LED_BLINK_MS);
    }

    hal_tasks_schedule(&g_boot_task, HK_BOOT_DELAY_MS);
    hal_tasks_schedule(&g_resend_task, HK_RESEND_MS);
}

/* Before a planned restart: where a hold or a fade is now becomes the level kept (as Open
 * Dimmer's dimmer_prepare_restart takes a hold's), so the warm start sends the level the light
 * has, not the one from before the hold or the fade's target; then everything is saved at
 * once. An off on its way needs nothing (off is kept already). */
void hk_dimmer_before_reboot(void) {
    if (!g_active) {
        return;
    }
    for (uint8_t ch = 0; ch < HK_CHANNELS; ch++) {
        hk_channel_t *   r = &g_ch[ch];
        hk_channel_nv_t *c = &g_nv.ch[ch];

        if (r->holding) {
            /* (in a hold from dark's start boost: its minimum, as its release would take) */
            c->value   = clamp_value(c, r->f.boost_left ? r->f.hold_lo : r->f.current);
            r->holding = 0;
        } else if (c->on && fader_moving(ch)) {
            c->value = clamp_value(c, fade_at(ch));
        }
    }
    nv_store(1);
}

uint8_t hk_dimmer_holding(uint8_t ch) {
    return g_active && ch < HK_CHANNELS && finger(&g_ch[ch]);
}

/* The module asks this (dimmer_cluster.c dimmer_fade_position()) instead of estimating the fade:
 * it is exact for every fade, those the module did not time among them. A fade from dark waiting
 * for its first frame is at its start; an off on its way to dark is none, nor is a hold. */
uint8_t hk_dimmer_fading(uint8_t ch, uint16_t *value) {
    if (!g_active || ch >= HK_CHANNELS || g_ch[ch].holding || !g_ch[ch].f.target ||
        !fader_moving(ch)) {
        return 0;
    }
    *value = clamp_value(&g_nv.ch[ch], fade_at(ch));
    return 1;
}

#endif /* HK_DIMMER */
