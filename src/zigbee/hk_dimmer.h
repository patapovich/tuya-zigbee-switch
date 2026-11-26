#ifndef _HK_DIMMER_H_
#define _HK_DIMMER_H_

#include <stdint.h>

/*
 * The 2-gang dimmer modules of the ZH-Z06 board family (Tuya TS110E _TZ3210_3mpwqzuu and its
 * Wi-Fi siblings): the dimming MCU (HK32F030M) runs its factory firmware, which only listens on a
 * one-way UART (9600 8N1) for 12-byte level frames and a range command, and never answers. The
 * wall switches, the module button and the LED are on the Zigbee module.
 *
 * This module is a virtual Tuya secondary MCU between the two: the Tuya link
 * (tuya_secondary_mcu.c) and the dimmer endpoints talk to it as they talk to the factory ZDMS16-2
 * MCU (DPs 1..5 / 7..11, power-on DP 14, levels 10..1000, answers as passive reports) and to Open
 * Dimmer 2.2.0 (its countdown DP 6 / 12, per-channel power-on 103 / 104, gestures 105 / 106, and
 * its settings 162..171: wall switch mode, level after a power cut, identify, start boost, fast
 * press) with 2.2.8's lamp compensation (172 / 173) and switch options (180), and it drives the
 * dimming MCU with Open Dimmer's fader (hk_fader.c), reads the wall switches (their changes go to
 * the module as active reports, as a factory MCU reports a switch, their gestures as Open
 * Dimmer's; a momentary switch acts as Open Dimmer's; enough presses of one ask for a network
 * reset) and the button (both channels; held 3 s and let go: the reset request 0x03), and shows
 * the network state on the LED. The module takes it as an Open Dimmer of a fixed build
 * (dimmer_cluster.c HK_OD_BUILD, dimmer_own_mcu()) that does not identify as one (version
 * 1.0.0). See docs/ts110e_hk_protocol.md.
 *
 * Config token: HK<s1><s2><button><led>, e.g. HKB4C0C3C2 (pins as in other tokens; the switches
 * and the button are active low with the pull-up, the LED is active high). With DM2, WB1B7, Y9600.
 */

#ifdef HK_DIMMER

#include "hal/zigbee.h"

/* Basic 0xff3a / 0xff3b (endpoint 1): the custom dimming curve of L1 / L2, 17 little-endian u16
 * 0..10000 (0 first, 10000 last, never falling), as Open Dimmer's lightingBallastCfg 0xff12.
 * Basic 0xff3c / 0xff3d: fade_off_slow_zone of L1 / L2, 0xff3e / 0xff3f: hold_slow_zone (uint32, %
 * of min..max, 0..100), as Open Dimmer's DPs 152 / 157 and 154 / 159. */
#define HK_ATTR_CUSTOM_CURVE    0xff3a
#define HK_ATTR_SLOW_ZONE       0xff3c
#define HK_BASIC_ATTRS          6

/* DP 0x7A / 0x7B (value, ms): the transition time of the channel's changes written within the
 * next second (dimmer_cluster sends it before them, as Open Dimmer's DP 160 / 161): 0 its fade
 * settings again, 1 at once, else that long (at most HK_TIME_MAX_MS). */
#define HK_DIMMER_CHANNELS      2
#define HK_DP_TIME_BASE         0x7A
#define HK_TIME_MAX_MS          600000u

/** The custom curves' and the slow zones' attributes into the Basic cluster's table; how many
 * (0: not in use). */
uint8_t hk_dimmer_register_attrs(hal_zigbee_attribute *table, uint8_t start);

/** A Basic attribute was written (the custom curves, the slow zones). */
void hk_dimmer_on_attr_write(uint16_t attr);

/** Parse the HK token; the virtual MCU is in use once it is parsed. */
void hk_dimmer_parse_token(const char *entry);

/** 1 when the config has the HK token. */
uint8_t hk_dimmer_active(void);

/** Start: NV settings, power-on behaviour, inputs. Call once the UART is up. */
void hk_dimmer_start(void);

/** A whole frame the module sends to its MCU (55 AA 02 ...): handled here, never on the wire. */
void hk_dimmer_from_module(const uint8_t *frame, uint16_t len);

/** Bytes the virtual MCU sends to the module (its answers and reports). */
uint16_t hk_dimmer_to_module(uint8_t *buf, uint16_t max);

/** Main loop: paces the frames to the dimming MCU. */
void hk_dimmer_poll(void);

/** A planned restart of the module (an update, a config write): the state is saved now. */
void hk_dimmer_before_reboot(void);

/** 1 while a wall switch hold runs on channel ch (0, 1): the finger has its level. */
uint8_t hk_dimmer_holding(uint8_t ch);

/** 1 while channel ch (0, 1) fades lit, whatever started the fade (a timed move, the fade
 * settings, from dark, the wall switch; not a hold, not an off on its way to dark): *value is
 * where its light is now (10..1000, within the channel's min..max). */
uint8_t hk_dimmer_fading(uint8_t ch, uint16_t *value);

#else

static inline uint8_t hk_dimmer_active(void) {
    return 0;
}

#endif

#endif
