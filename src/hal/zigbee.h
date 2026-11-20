#ifndef _HAL_ZIGBEE_H_
#define _HAL_ZIGBEE_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/** Zigbee attribute access permissions */
typedef enum {
    ATTR_READONLY,
    ATTR_WRITABLE,
} hal_attr_flags_t;

/** Command handler result (processed or delegate to default handler) */
typedef enum {
    HAL_ZIGBEE_CMD_PROCESSED,
    HAL_ZIGBEE_CMD_SKIPPED,
    HAL_ZIGBEE_INVALID_VALUE,
    HAL_ZIGBEE_MALFORMED_COMMAND,
    HAL_ZIGBEE_ACTION_DENIED,
#ifdef DIMMER_SCENES          /* (only there: other boards keep their builds byte for byte) */
    HAL_ZIGBEE_NOT_FOUND,     /* ZCL NOT_FOUND (Scenes: no such scene) */
    HAL_ZIGBEE_INVALID_FIELD, /* ZCL INVALID_FIELD (Scenes: the endpoint is not in the group) */
#endif
} hal_zigbee_cmd_result_t;

/** Zigbee network connection state */
typedef enum {
    HAL_ZIGBEE_NETWORK_NOT_JOINED = 0,
    HAL_ZIGBEE_NETWORK_JOINED     = 1,
    HAL_ZIGBEE_NETWORK_JOINING    = 2,
} hal_zigbee_network_status_t;

/** Zigbee cluster attribute definition (readable/writable device properties) */
typedef struct {
    uint16_t         attribute_id;
    uint8_t          data_type_id;
    hal_attr_flags_t flag;
    uint8_t          size;
    uint8_t *        value;
} hal_zigbee_attribute;

/** Function called when cluster receives a command */
typedef hal_zigbee_cmd_result_t (*hal_zigbee_cmd_callback_t)(
    uint8_t endpoint, uint16_t cluster_id, uint8_t command_id,
    void *cmd_payload, uint16_t cmd_payload_len);

/** Zigbee cluster (group of related attributes and commands) */
typedef struct {
    uint16_t                  cluster_id;
    uint8_t                   is_server;
    uint8_t                   attribute_count;
    hal_zigbee_attribute *    attributes;
    hal_zigbee_cmd_callback_t cmd_callback;
} hal_zigbee_cluster;

/** Zigbee endpoint (logical device with multiple clusters) */
typedef struct {
    uint8_t             endpoint;
    uint16_t            profile_id;
    uint16_t            device_id;
    uint8_t             device_version;

    uint8_t             cluster_count;
    hal_zigbee_cluster *clusters;
} hal_zigbee_endpoint;

/**
 * Initialize Zigbee stack with device endpoints and clusters
 * @param endpoints Array of endpoint definitions
 * @param endpoints_cnt Number of endpoints
 */
void hal_zigbee_init(hal_zigbee_endpoint *endpoints, uint8_t endpoints_cnt);

// Network membership control

/**
 * Get current Zigbee network connection status
 * @return Network status (joined/joining/not joined)
 */
hal_zigbee_network_status_t hal_zigbee_get_network_status(void);

/** Function called when network status changes */
typedef void (*hal_network_status_change_callback_t)(
    hal_zigbee_network_status_t new_status);

/**
 * Register callback for network status changes
 * @param callback Function to call on status change
 */
void hal_register_on_network_status_change_callback(
    hal_network_status_change_callback_t callback);

/** Leave current Zigbee network */
void hal_zigbee_leave_network(void);

/** Start searching for and joining Zigbee networks (pairing mode) */
void hal_zigbee_start_network_steering(void);

/** Set Zigbee OTA image type */
void hal_zigbee_set_image_type(uint16_t image_type);

// Attributes

/**
 * Notify SDK that attribute value changed (for example, trigger reporting)
 * @param endpoint Endpoint number
 * @param cluster_id Cluster ID
 * @param attribute_id Attribute ID that changed
 */
void hal_zigbee_notify_attribute_changed(uint8_t endpoint, uint16_t cluster_id,
                                         uint16_t attribute_id);

/** Function called when attribute is written via Zigbee */
typedef void (*hal_attribute_change_callback_t)(uint8_t endpoint,
                                                uint16_t cluster_id,
                                                uint16_t attribute_id);

/**
 * Register callback for attribute changes from network
 * @param callback Function to call when attributes are written
 */
void hal_zigbee_register_on_attribute_change_callback(
    hal_attribute_change_callback_t callback);

/** Zigbee command direction (client sends commands, server responds) */
typedef enum {
    HAL_ZIGBEE_DIR_CLIENT_TO_SERVER = 0,
    HAL_ZIGBEE_DIR_SERVER_TO_CLIENT = 1,
} hal_zigbee_direction_t;

typedef struct {
    uint8_t                endpoint;            // source endpoint
    uint16_t               profile_id;          // e.g., 0x0104 (HA)
    uint16_t               cluster_id;          // e.g., 0x0006 (On/Off)
    uint8_t                command_id;          // ZCL command id
    uint8_t                cluster_specific;    // 1 = cluster-specific, 0 = global
    hal_zigbee_direction_t direction;
    uint8_t                disable_default_rsp; // 1 = set “disable default response”
    uint16_t               manufacturer_code;   // 0 if not manufacturer-specific
    const uint8_t *        payload;             // raw payload (little-endian encoded)
    uint8_t                payload_len;
#ifdef DIMMER_BINDINGS
    uint8_t                aps_ack;             // 1 = APS-acknowledged (the stack drops it for groups)
#endif
} hal_zigbee_cmd;

/** Zigbee operation result codes (success or failure reasons) */
typedef enum {
    HAL_ZIGBEE_OK = 0,
    HAL_ZIGBEE_ERR_NOT_JOINED,
    HAL_ZIGBEE_ERR_BAD_ARG,
    HAL_ZIGBEE_ERR_SEND_FAILED,
} hal_zigbee_status_t;

/**
 * Send command to bound devices (switches controlling lights)
 * @param cmd Command structure to send
 * @return HAL_ZIGBEE_OK on success, error code otherwise
 */
hal_zigbee_status_t hal_zigbee_send_cmd_to_bindings(const hal_zigbee_cmd *cmd);

/**
 * Send attribute report to bound devices (notify of state changes)
 * @param endpoint Source endpoint
 * @param cluster_id Cluster containing the attribute
 * @param attr_id Attribute ID to report
 * @param zcl_type_id ZCL data type of the value
 * @param value Pointer to attribute value
 * @param value_len Size of value in bytes
 * @return HAL_ZIGBEE_OK on success, error code otherwise
 */
hal_zigbee_status_t
hal_zigbee_send_report_attr(uint8_t endpoint, uint16_t cluster_id,
                            uint16_t attr_id, uint8_t zcl_type_id,
                            const void *value, uint8_t value_len);

/**
 * Hold the stack's attribute reporting (configured reporting) at startup, while a state is not
 * known yet: a secondary MCU tells the light's state only after the boot, and the stack would
 * otherwise report the defaults (off, level 0) at once, which a hub takes as the light going off.
 * hold = 0 releases at once; a hold ends by itself after timeout_ms anyway.
 */
void hal_zigbee_hold_reports(uint8_t hold, uint32_t timeout_ms);

/** Send Zigbee "announce" command to notify other devices of our presence
 * @return HAL_ZIGBEE_OK on success, error code otherwise
 */
hal_zigbee_status_t hal_zigbee_send_announce(void);


/** Function called when any ZCL command is received, both generic and cluster-specific */
typedef void (*hal_zcl_activity_callback_t)();

/**
 * Register callback for ZCL activity from network
 * @param callback Function to call when ZCL commands are received
 *
 * Idea is to use this for battery device to control polling rate after incoming messages.
 */
void hal_zigbee_register_on_zcl_activity_callback(
    hal_zcl_activity_callback_t callback);


/** Set end device poll rate in milliseconds
 *
 * No-op for routers.
 */
void hal_zigbee_set_poll_rate_ms(uint32_t poll_rate_ms);

/** Get current end device poll rate in milliseconds
 *
 * Returns 0 for routers.
 */
uint32_t hal_zigbee_get_poll_rate_ms(void);

#ifdef DIMMER_SCENES
/* ---- for the Scenes server (src/zigbee/scene_cluster.c) ---- */

/**
 * Answer the ZCL command being handled (call it from its cmd_callback): a cluster-specific
 * command of the same cluster, server to client, to its sender, with its sequence number.
 * Nothing goes out for a command sent to a group or as a broadcast (ZCL: no response then),
 * and the stack sends no default response for an answered command.
 */
void hal_zigbee_send_response(uint8_t command_id, const uint8_t *payload, uint8_t payload_len);

/** Whether the endpoint is in the group (the stack's group table). */
bool hal_zigbee_group_has_endpoint(uint16_t group_id, uint8_t endpoint);

/** Called before the stack takes a Remove Group (group_id) or Remove All Groups (all = true)
 *  command of the Groups cluster on an endpoint. */
typedef void (*hal_group_removed_callback_t)(uint8_t endpoint, uint16_t group_id, bool all);
void hal_zigbee_register_on_group_removed_callback(hal_group_removed_callback_t callback);

#endif

#ifdef DIMMER_SCENES

/** Called when the device leaves its network (also a reset to factory new): what belonged to
 *  that network is to be forgotten (the scenes; the stack drops its groups and bindings). */
typedef void (*hal_network_left_callback_t)(void);
void hal_zigbee_register_on_network_left_callback(hal_network_left_callback_t callback);

#endif

#ifdef DIMMER_BINDINGS
/** The group a binding of the endpoint's cluster goes to (the first such), 0 if none. */
uint16_t hal_zigbee_bound_group(uint8_t endpoint, uint16_t cluster_id);

#endif

/** Find cluster definition by endpoint and cluster ID */
static inline hal_zigbee_cluster *
hal_zigbee_find_cluster(hal_zigbee_endpoint *endpoints, uint8_t endpoints_count,
                        uint8_t endpoint, uint16_t cluster_id) {
    if (!endpoints) {
        return NULL;
    }

    for (int i = 0; i < endpoints_count; i++) {
        if (endpoints[i].endpoint == endpoint) {
            for (int j = 0; j < endpoints[i].cluster_count; j++) {
                if (endpoints[i].clusters[j].cluster_id == cluster_id) {
                    return &endpoints[i].clusters[j];
                }
            }
        }
    }
    return NULL;
}

/** Find attribute definition within a specific cluster */
static inline hal_zigbee_attribute *
hal_zigbee_find_attribute(hal_zigbee_endpoint *endpoints,
                          uint8_t endpoints_count, uint8_t endpoint,
                          uint16_t cluster_id, uint16_t attribute_id) {
    hal_zigbee_cluster *cluster =
        hal_zigbee_find_cluster(endpoints, endpoints_count, endpoint, cluster_id);

    if (!cluster) {
        return NULL;
    }

    for (int i = 0; i < cluster->attribute_count; i++) {
        if (cluster->attributes[i].attribute_id == attribute_id) {
            return &cluster->attributes[i];
        }
    }
    return NULL;
}

#endif
