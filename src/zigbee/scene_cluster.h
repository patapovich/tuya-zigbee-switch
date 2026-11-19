#ifndef _SCENE_CLUSTER_H_
#define _SCENE_CLUSTER_H_

/* Scenes server of the dimmer endpoints (DIMMER_SCENES: the ZDMS16-2 builds and the stub).
 * A scene holds a channel's On/Off state and level (Zigbee2MQTT scene_store / scene_add /
 * scene_recall); names are not kept (NameSupport 0: Zigbee2MQTT keeps them itself). One table
 * of SCENE_TABLE_SIZE entries for every endpoint, kept in NV (NV_ITEM_SCENE_TABLE). */

#include "hal/zigbee.h"
#include <stdint.h>

#define SCENE_TABLE_SIZE    16

/* What a scene holds (scene_entry_t.flags). */
#define SCENE_HAS_ONOFF     0x01 /* the On/Off state, SCENE_ON: on */
#define SCENE_ON            0x02
#define SCENE_HAS_LEVEL     0x04 /* the level (ZCL 1..254) */

typedef struct {
    uint8_t              current_scene;
    uint16_t             current_group;
    uint8_t              scene_valid;
    hal_zigbee_attribute attr_infos[5];
} zigbee_scene_cluster;

#ifdef DIMMER_SCENES
void scene_cluster_add_to_endpoint(zigbee_scene_cluster *cluster, hal_zigbee_endpoint *endpoint);

/** The light of the endpoint changed other than by a recall (a command, the wall switch):
 *  SceneValid goes 0. */
void scene_cluster_light_changed(uint8_t endpoint);
#endif

#endif
