#ifndef _DIMMER_CLUSTER_H_
#define _DIMMER_CLUSTER_H_

#include "hal/zigbee.h"
#include "hal/tasks.h"

typedef struct {
    uint8_t              dimmer_idx;
    uint8_t              endpoint;
    uint8_t              startup_mode;
    uint8_t              onoff_dpid;
    uint8_t              level_dpid;
    uint8_t              switch_type_dpid;
    uint8_t              min_level_dpid;
    uint8_t              max_level_dpid;
    uint8_t              power_on_behavior_dpid;

    /* Own PY32 firmware only (the MCU answered the identity query): its
     * per-channel power-on DP, used instead of the shared DP 14, and its
     * wall switch gesture DP (1 single, 2 double, 3 triple, 4 hold,
     * 5 release), reported as genMultistateInput presentValue
     * (sequence << 4 | gesture, so every gesture is a change). */
    uint8_t              own_power_on_dpid;
    uint8_t              action_dpid;

    /* Its transition time DP (160 / 161, from OPEN_DIMMER_TRANSITION_BUILD): the ms of the
     * change written right after it; transition_at: when a time other than 0 went out (0: none
     * that could still apply). */
    uint8_t              own_transition_dpid;
    uint32_t             transition_at;
    uint8_t              action_seq;
    uint16_t             action_state;

    /* genOnOff: onoff, startup, [countdown]; genLevelCtrl after them: current level, on
     * level, [start-up level] ([]: boards with an image only). */
    hal_zigbee_attribute attr_infos[6];
    hal_zigbee_attribute multistate_attr_infos[4];  // wall switch gestures (own MCU)
    hal_zigbee_attribute ballast_attr_infos[3];     // min/max level, [start boost] (lightingBallastCfg)
    hal_zigbee_attribute switch_type_attr_infos[2]; // switch type, [wall switch mode] (genOnOffSwitchCfg)
    hal_zigbee_attribute identify_attr_infos[1];    // [IdentifyTime] (genIdentify)

    /* Open Dimmer from OPEN_DIMMER_FEATURES_BUILD (2.2.0), as its dump and the answers to our
     * writes tell (any other MCU: never written, they keep the values of "none"): the auto-off
     * countdown in s (DP 6 / 12; 0 again when the channel goes off), the wall switch mode
     * (162 / 163; input_mode_mcu: as the MCU said), the level after a power cut
     * (StartUpCurrentLevel, 164 / 165; startup_value: the MCU's value, 0 = the last level),
     * IdentifyTime (166 / 167, counted down here: the MCU does not report the end) and the
     * start boost in ms (168 / 169). */
    uint32_t             countdown;
    uint16_t             startup_value;
    uint16_t             identify_time;
    uint16_t             start_boost;
    uint8_t              input_mode;
    uint8_t              input_mode_mcu;
    uint8_t              startup_level;
    hal_task_t           identify_task;
    uint8_t              current_level;
    uint8_t              min_level;
    uint8_t              max_level;
    uint8_t              switch_type;
    uint8_t              on;

    /* ZCL OnLevel: the level the light comes on at, from the wall switch or
     * an On command. 0xFF (default) = wherever the MCU left it. Kept in NV
     * (NV_ITEM_DIMMER_CLUSTER_DATA); on_level_saved is the value stored
     * there, so only a real change is written. */
    uint8_t              on_level;

    /* Set once the MCU has told us its on/off state, so its first report
     * after boot is not taken for a switch press. */
    uint8_t              state_known;
    uint8_t              on_level_saved;

    /* What the MCU uses: its own min/max brightness (Tuya 10..1000, 0 =
     * not known yet) and the last level value written to or reported by it. */
    uint16_t             mcu_min;
    uint16_t             mcu_max;
    uint32_t             level_value;

    /* Spread ZCL levels 1..254 over mcu_min..mcu_max (default). Cleared by
     * the DR config token for the stock behaviour (1..254 -> 10..1000, the
     * MCU clamps, so the ends of the slider do nothing). */
    uint8_t              scale_levels;

    /* Level ramp (ZCL Move / MoveWithOnOff). */
    hal_task_t           ramp_task;
    uint8_t              move_direction; /* 0 = down, 1 = up */
    uint8_t              move_active;
    uint8_t              move_with_onoff;

    /* The MCU fades towards a written level and reports where it is on the
     * way. While a fade we started is running, those reports are kept aside
     * instead of overwriting current_level. */
    hal_task_t           holdoff_task;
    uint8_t              level_holdoff;
    uint8_t              has_held_report;
    uint32_t             held_report_value;
    uint32_t             last_written_value;
} zigbee_dimmer_cluster;

void dimmer_cluster_add_to_endpoint(zigbee_dimmer_cluster *cluster,
                                    hal_zigbee_endpoint *endpoint);


// Returns HAL_ZIGBEE_CMD_SKIPPED-equivalent behavior: no-op if this endpoint
// has no registered dimmer cluster (e.g. the write targeted a relay endpoint
// sharing the same ZCL_CLUSTER_ON_OFF cluster id). Attribute ids repeat across
// clusters (OnLevel and the ballast max are both 0x0011, OnOff and the switch
// type both 0x0000), so the cluster id picks the attribute.
void dimmer_cluster_callback_attr_write_trampoline(uint8_t endpoint,
                                                   uint16_t cluster_id,
                                                   uint16_t attribute_id);

#endif
