#include "scene_cluster.h"

#ifdef DIMMER_SCENES
/* -O2 like the rest: a per-function size level miscompiles a switch (tests/test_no_optimize_pragma.py). */

#include "consts.h"
#include "device_config/nvm_items.h"
#include "dimmer_cluster.h"
#include "hal/nvm.h"
#include <string.h>

/* One entry (8 bytes, so the table is 128 bytes in RAM and in NV). transition: the scene's time
 * in 1/10 s; 0 (a Store's, an Add's without a time) takes the channel's fade settings, as a
 * command without a time does. */
typedef struct {
    uint8_t  endpoint; /* 0: free */
    uint8_t  scene_id;
    uint16_t group_id;
    uint16_t transition;
    uint8_t  flags;    /* SCENE_HAS_ONOFF, SCENE_ON, SCENE_HAS_LEVEL */
    uint8_t  level;
} scene_entry_t;

typedef char scene_entry_is_8_bytes[sizeof(scene_entry_t) == 8 ? 1 : -1];

#define SCENE_MAX_ENDPOINTS    8

static scene_entry_t         scenes[SCENE_TABLE_SIZE];
static uint8_t               scene_count; /* SceneCount: the entries in use, all endpoints */
static uint8_t               scenes_loaded;
static zigbee_scene_cluster *scene_by_endpoint[SCENE_MAX_ENDPOINTS];
static const uint8_t         scene_name_support = 0;

static uint16_t get16(const uint8_t *p) {
    return (uint16_t)(p[0] | (p[1] << 8));
}

static uint8_t put16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    return 2;
}

static void scene_recount(void) {
    scene_count = 0;
    for (int i = 0; i < SCENE_TABLE_SIZE; i++)
        if (scenes[i].endpoint)
            scene_count++;
}

static void scene_save(void) {
    scene_recount();
    hal_nvm_write(NV_ITEM_SCENE_TABLE, sizeof(scenes), (uint8_t *)scenes);
}

static scene_entry_t *scene_find(uint8_t ep, uint16_t group, uint8_t scene) {
    for (int i = 0; i < SCENE_TABLE_SIZE; i++)
        if (scenes[i].endpoint == ep && scenes[i].group_id == group && scenes[i].scene_id == scene)
            return &scenes[i];

    return NULL;
}

static scene_entry_t *scene_find_or_free(uint8_t ep, uint16_t group, uint8_t scene) {
    scene_entry_t *e = scene_find(ep, group, scene);

    if (e)
        return e;

    for (int i = 0; i < SCENE_TABLE_SIZE; i++)
        if (!scenes[i].endpoint)
            return &scenes[i];

    return NULL;
}

/* Group 0 is always valid; another one only while the endpoint is in it. */
static uint8_t scene_group_ok(uint16_t group, uint8_t ep) {
    return group == 0 || hal_zigbee_group_has_endpoint(group, ep);
}

/* Drop the endpoint's entries of the group; SCENE_DROP_ALL: of every group but 0;
 * SCENE_DROP_STALE: of a group the endpoint is no longer in (also those gone with a network
 * leave). */
#define SCENE_DROP_ALL      0x10000u
#define SCENE_DROP_STALE    0x20000u

static void scene_drop(uint8_t ep, uint32_t group) {
    uint8_t dropped = 0;

    for (scene_entry_t *e = scenes; e < scenes + SCENE_TABLE_SIZE; e++) {
        if (e->endpoint == ep &&
            (group == SCENE_DROP_STALE ? !scene_group_ok(e->group_id, ep)
             : group == SCENE_DROP_ALL ? e->group_id != 0 : e->group_id == group)) {
            memset(e, 0, sizeof(*e));
            dropped = 1;
        }
    }
    if (dropped)
        scene_save();
}

static void scene_on_group_removed(uint8_t endpoint, uint16_t group_id, bool all) {
    scene_drop(endpoint, all ? SCENE_DROP_ALL : group_id);
}

/* Left the network: its scenes go (group 0's too, which no group removal ever drops), else a
 * module paired again would recall what the old network stored. */
static void scene_on_network_left(void) {
    memset(scenes, 0, sizeof(scenes));
    scene_save();
    for (uint8_t i = 0; i < SCENE_MAX_ENDPOINTS; i++) {
        zigbee_scene_cluster *c = scene_by_endpoint[i];
        if (c) {
            c->current_scene = 0;
            c->current_group = 0;
            c->scene_valid   = 0;
        }
    }
}

static uint8_t scene_set(scene_entry_t *e, const scene_entry_t *v) {
    if (memcmp(e, v, sizeof(*e)) == 0)
        return 0; /* unchanged: no NV write */

    *e = *v;
    scene_save();
    return 1;
}

static uint8_t scene_clamp_level(uint8_t level) {
    return level < 1 ? 1 : level > 254 ? 254 : level;
}

/* Add / Enhanced Add: group, scene, transition (s, enhanced 1/10 s), name (skipped), extension
 * field sets (On/Off and Level Control kept, the rest ignored). */
static uint8_t scene_add(uint8_t ep, const uint8_t *p, uint16_t len, uint8_t enhanced,
                         uint8_t *status) {
    scene_entry_t v = { 0 };
    uint16_t      t, at;

    if (len < 6)
        return 0;

    t  = get16(p + 3);
    at = (uint16_t)(6 + (p[5] == 0xFF ? 0 : p[5]));
    if (at > len)
        return 0;

    v.endpoint   = ep;
    v.group_id   = get16(p);
    v.scene_id   = p[2];
    v.transition = enhanced ? t : t >= 6554 ? 0xFFFE : (uint16_t)(t * 10);
    while (at + 3 <= len) {
        uint16_t cluster = get16(p + at);
        uint8_t  n       = p[at + 2];
        at += 3;
        if (at + n > len)
            break;
        if (n >= 1 && cluster == ZCL_CLUSTER_ON_OFF)
            v.flags |= (uint8_t)(SCENE_HAS_ONOFF | (p[at] ? SCENE_ON : 0));
        if (n >= 1 && cluster == ZCL_CLUSTER_LEVEL_CONTROL) {
            v.flags |= SCENE_HAS_LEVEL;
            v.level  = scene_clamp_level(p[at]);
        }
        at += n;
    }
    if (*status == ZCL_STATUS_SUCCESS) {
        scene_entry_t *e = scene_find_or_free(ep, v.group_id, v.scene_id);
        if (e)
            scene_set(e, &v);
        else
            *status = ZCL_STATUS_INSUFFICIENT_SPACE;
    }
    return 1;
}

static hal_zigbee_cmd_result_t scene_cmd(uint8_t ep, uint16_t cluster_id, uint8_t cmd,
                                         void *payload, uint16_t len) {
    zigbee_scene_cluster *c = ep < SCENE_MAX_ENDPOINTS ? scene_by_endpoint[ep] : NULL;
    const uint8_t *       p = (const uint8_t *)payload;
    uint8_t enhanced        = cmd == ZCL_CMD_SCENE_ENHANCED_ADD ||
                              cmd == ZCL_CMD_SCENE_ENHANCED_VIEW;
    uint8_t base = enhanced ? (uint8_t)(cmd - ZCL_CMD_SCENE_ENHANCED_ADD) : cmd;
    uint8_t rsp[5 + SCENE_TABLE_SIZE];
    uint8_t n;

    if (c == NULL || (base > ZCL_CMD_SCENE_GET_MEMBERSHIP && !enhanced))
        return HAL_ZIGBEE_CMD_SKIPPED; /* Copy Scene and anything else: not supported */

    uint8_t with_scene = base != ZCL_CMD_SCENE_REMOVE_ALL && base != ZCL_CMD_SCENE_GET_MEMBERSHIP;
    if (p == NULL || len < (with_scene ? 3 : 2))
        return HAL_ZIGBEE_MALFORMED_COMMAND;

    uint16_t group  = get16(p);
    uint8_t  status = scene_group_ok(group, ep) ? ZCL_STATUS_SUCCESS
                                                      : ZCL_STATUS_INVALID_FIELD;
    scene_entry_t *e;

    scene_drop(ep, SCENE_DROP_STALE);
    e = with_scene ? scene_find(ep, group, p[2]) : NULL;

    /* Every response but Get Scene Membership's starts with status, group, scene. */
    rsp[0] = status;
    n      = (uint8_t)(1 + put16(rsp + 1, group));
    if (with_scene)
        rsp[n++] = p[2];

    switch (base) {
    case ZCL_CMD_SCENE_ADD:
        if (!scene_add(ep, p, len, enhanced, &rsp[0]))
            return HAL_ZIGBEE_MALFORMED_COMMAND;

        break;

    case ZCL_CMD_SCENE_VIEW:
        if (status == ZCL_STATUS_SUCCESS && e == NULL)
            rsp[0] = ZCL_STATUS_NOT_FOUND;
        if (rsp[0] == ZCL_STATUS_SUCCESS) {
            n       += put16(rsp + n, enhanced ? e->transition : (uint16_t)(e->transition / 10));
            rsp[n++] = 0; /* empty name */
            if (e->flags & SCENE_HAS_ONOFF) {
                n       += put16(rsp + n, ZCL_CLUSTER_ON_OFF);
                rsp[n++] = 1;
                rsp[n++] = (e->flags & SCENE_ON) ? 1 : 0;
            }
            if (e->flags & SCENE_HAS_LEVEL) {
                n       += put16(rsp + n, ZCL_CLUSTER_LEVEL_CONTROL);
                rsp[n++] = 1;
                rsp[n++] = e->level;
            }
        }
        break;

    case ZCL_CMD_SCENE_REMOVE:
        if (status == ZCL_STATUS_SUCCESS && e == NULL)
            rsp[0] = ZCL_STATUS_NOT_FOUND;
        if (rsp[0] == ZCL_STATUS_SUCCESS) {
            memset(e, 0, sizeof(*e));
            scene_save();
        }
        break;

    case ZCL_CMD_SCENE_REMOVE_ALL:
        if (status == ZCL_STATUS_SUCCESS)
            scene_drop(ep, group);
        break;

    case ZCL_CMD_SCENE_STORE:
        if (status == ZCL_STATUS_SUCCESS) {
            scene_entry_t v = { ep, p[2], group, 0, 0, 0 };
            dimmer_cluster_scene_state(ep, &v.flags, &v.level);
            if (e)
                v.transition = e->transition; /* a Store keeps the scene's time */
            else
                e = scene_find_or_free(ep, group, p[2]);
            if (e == NULL) {
                rsp[0] = ZCL_STATUS_INSUFFICIENT_SPACE;
            } else {
                scene_set(e, &v);
                c->current_scene = p[2];
                c->current_group = group;
                c->scene_valid   = 1;
            }
        }
        break;

    case ZCL_CMD_SCENE_RECALL:
        /* No response; the default response tells the status. A time in the command (ZCL 7,
        * not 0xFFFF) wins over the scene's: 0 there is "at once", as in a level command. */
        if (status != ZCL_STATUS_SUCCESS)
            return HAL_ZIGBEE_INVALID_FIELD;

        if (e == NULL)
            return HAL_ZIGBEE_NOT_FOUND;

        dimmer_cluster_scene_recall(ep, e->flags, e->level,
                                    len >= 5 && get16(p + 3) != 0xFFFF ? get16(p + 3)
                                    : e->transition ? e->transition : 0xFFFF);
        c->current_scene = p[2];
        c->current_group = group;
        c->scene_valid   = 1;
        return HAL_ZIGBEE_CMD_PROCESSED;

    case ZCL_CMD_SCENE_GET_MEMBERSHIP: {
        /* status, capacity (free entries), group, [count, scene ids] */
        uint8_t free = 0, count = 0;
        for (int i = 0; i < SCENE_TABLE_SIZE; i++) {
            if (!scenes[i].endpoint)
                free++;
            else if (scenes[i].endpoint == ep && scenes[i].group_id == group)
                rsp[5 + count++] = scenes[i].scene_id;
        }
        rsp[1] = free;
        put16(rsp + 2, group);
        rsp[4] = count;
        n      = status == ZCL_STATUS_SUCCESS ? (uint8_t)(5 + count) : 4;
        break;
    }
    }
    hal_zigbee_send_response(cmd, rsp, n);
    return HAL_ZIGBEE_CMD_PROCESSED;
}

void scene_cluster_light_changed(uint8_t endpoint) {
    if (endpoint < SCENE_MAX_ENDPOINTS && scene_by_endpoint[endpoint])
        scene_by_endpoint[endpoint]->scene_valid = 0;
}

void scene_cluster_add_to_endpoint(zigbee_scene_cluster *cluster, hal_zigbee_endpoint *endpoint) {
    if (!scenes_loaded) {
        scenes_loaded = 1;
        if (hal_nvm_read(NV_ITEM_SCENE_TABLE, sizeof(scenes), (uint8_t *)scenes) != HAL_NVM_SUCCESS)
            memset(scenes, 0, sizeof(scenes)); /* none yet, or another size: an empty table */
        scene_recount();
        hal_zigbee_register_on_group_removed_callback(scene_on_group_removed);
        hal_zigbee_register_on_network_left_callback(scene_on_network_left);
    }
    if (endpoint->endpoint < SCENE_MAX_ENDPOINTS)
        scene_by_endpoint[endpoint->endpoint] = cluster;
    cluster->current_scene = 0;
    cluster->current_group = 0;
    cluster->scene_valid   = 0;

    /* SceneCount, CurrentScene, CurrentGroup, SceneValid, NameSupport (ids 0..4), read-only. */
    static const uint8_t types[5] = { ZCL_DATA_TYPE_UINT8,   ZCL_DATA_TYPE_UINT8,
                                      ZCL_DATA_TYPE_UINT16,
                                      ZCL_DATA_TYPE_BOOLEAN, ZCL_DATA_TYPE_BITMAP8 };
    uint8_t *            values[5] = { &scene_count,           &cluster->current_scene,
                                       (uint8_t *)&cluster->current_group,
                                       &cluster->scene_valid,
                                       (uint8_t *)&scene_name_support };
    for (uint8_t i = 0; i < 5; i++) {
        hal_zigbee_attribute *a = &cluster->attr_infos[i];
        a->attribute_id = i;
        a->data_type_id = types[i];
        a->flag         = ATTR_READONLY;
        a->size         = types[i] == ZCL_DATA_TYPE_UINT16 ? 2 : 1;
        a->value        = values[i];
    }

    hal_zigbee_cluster *c = &endpoint->clusters[endpoint->cluster_count++];
    c->cluster_id      = ZCL_CLUSTER_SCENES;
    c->attribute_count = 5;
    c->attributes      = cluster->attr_infos;
    c->is_server       = 1;
    c->cmd_callback    = scene_cmd;
}

#endif
