#include "hal/zigbee.h"
#include "hal/zigbee_limits.h"
#include "stub/machine_io.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void bytes_to_hexstr(const uint8_t *bytes, size_t len, char *out) {
    static const char HEX_DIGITS[] = "0123456789ABCDEF";

    for (size_t i = 0; i < len; ++i) {
        uint8_t b = bytes[i];
        out[2 * i]     = HEX_DIGITS[b >> 4];
        out[2 * i + 1] = HEX_DIGITS[b & 0x0F];
    }
    out[2 * len] = '\0';
}

#define MAX_ENDPOINTS    16
#define MAX_BINDINGS     32

typedef struct {
    uint16_t short_addr;
    uint8_t  endpoint;
    uint16_t cluster_id;
} stub_binding_t;

static hal_zigbee_endpoint *endpoints             = NULL;
static uint8_t endpoints_count                    = 0;
static hal_zigbee_network_status_t network_status =
    HAL_ZIGBEE_NETWORK_NOT_JOINED;
static hal_attribute_change_callback_t attr_change_callback  = NULL;
static hal_zcl_activity_callback_t     zcl_activity_callback = NULL;
static uint32_t poll_rate_ms = 0;

static stub_binding_t bindings[MAX_BINDINGS];
static int            binding_count = 0;

/* The command being handled (hal_zigbee_send_response answers it): its endpoint and cluster,
 * and whether it came to a group (no response then). */
static uint8_t  cmd_active;
static uint8_t  cmd_ep;
static uint16_t cmd_cluster;
static uint8_t  cmd_to_group;

/* The group table, as APS_GROUP_TABLE_NUM on Telink (8 entries of group + endpoint). */
#define STUB_GROUPS    8
static struct {
    uint16_t group_id;
    uint8_t  endpoint; /* 0: free */
} groups[STUB_GROUPS];
static hal_group_removed_callback_t group_removed_callback = NULL;

void hal_zigbee_init(hal_zigbee_endpoint *ep_list, uint8_t ep_count) {
    if (!ep_list && ep_count > 0) {
        io_log("ZIGBEE", "Error: NULL endpoint list with non-zero count %d",
               ep_count);
        exit(1);
    }

    if (ep_count > MAX_ENDPOINTS) {
        io_log("ZIGBEE", "Error: Endpoint count %d exceeds maximum %d", ep_count,
               MAX_ENDPOINTS);
        exit(1);
    }

    endpoints       = ep_list;
    endpoints_count = ep_count;

    /* What the Telink HAL and stack would have to register (hal/zigbee_limits.h), logged for
     * tests/test_zcl_tables.py, which checks every Telink board's config against it. Over
     * the limits the stub goes on (the tests run synthetic configs larger than any board),
     * saying so and counting it as the Telink HAL would drop it. */
    int in = 0, out = 0, infos = 0, attrs = 0;
    for (int i = 0; i < ep_count; i++) {
        for (int j = 0; j < ep_list[i].cluster_count; j++) {
            const hal_zigbee_cluster *c = &ep_list[i].clusters[j];
            if (c->is_server)
                in++;
            else
                out++;

            /* OTA: the SDK's own. Every other cluster is counted for the SDK's ZCL list: an
             * upper bound (the Telink HAL counts only those with a register function, once
             * per endpoint and id; tests/test_zcl_tables.py checks the register functions). */
            if (c->cluster_id != 0x0019) {
                infos++;
                attrs += c->attribute_count;
            }
        }
    }
    io_log("ZIGBEE", "ZCL tables: endpoints %d/%d in_clusters %d/%d out_clusters %d/%d "
           "zcl_clusters %d/%d attributes %d/%d", ep_count, HAL_ZIGBEE_MAX_ENDPOINTS, in,
           HAL_ZIGBEE_MAX_IN_CLUSTERS, out, HAL_ZIGBEE_MAX_OUT_CLUSTERS,
           infos + HAL_ZIGBEE_SDK_ZCL_CLUSTERS, HAL_ZIGBEE_MAX_ZCL_CLUSTERS, attrs,
           HAL_ZIGBEE_MAX_ATTRS);
    if (ep_count > HAL_ZIGBEE_MAX_ENDPOINTS || in > HAL_ZIGBEE_MAX_IN_CLUSTERS ||
        out > HAL_ZIGBEE_MAX_OUT_CLUSTERS || infos > HAL_ZIGBEE_MAX_CLUSTER_INFOS ||
        infos + HAL_ZIGBEE_SDK_ZCL_CLUSTERS > HAL_ZIGBEE_MAX_ZCL_CLUSTERS ||
        attrs > HAL_ZIGBEE_MAX_ATTRS) {
        io_log("ZIGBEE", "Error: the ZCL tables of the Telink HAL would overflow");
        hal_zigbee_tables_dropped++;
    }

    io_log("ZIGBEE", "Initialized Zigbee with %d endpoints", ep_count);

    for (int i = 0; i < ep_count; i++) {
        io_log("ZIGBEE", "Endpoint %d: profile=0x%04x, device=0x%04x, clusters=%d",
               ep_list[i].endpoint, ep_list[i].profile_id, ep_list[i].device_id,
               ep_list[i].cluster_count);

        if (ep_list[i].cluster_count > 0 && !ep_list[i].clusters) {
            io_log(
                "ZIGBEE",
                "Error: Endpoint %d has cluster count %d but NULL clusters pointer",
                ep_list[i].endpoint, ep_list[i].cluster_count);
            exit(1);
        }
        for (int j = 0; j < ep_list[i].cluster_count; j++) {
            io_log("ZIGBEE", "  Cluster %d: id=0x%04x, attrs=%d, is_server=%d", j,
                   ep_list[i].clusters[j].cluster_id,
                   ep_list[i].clusters[j].attribute_count,
                   ep_list[i].clusters[j].is_server);
            if (ep_list[i].clusters[j].attribute_count > 0 &&
                !ep_list[i].clusters[j].attributes) {
                io_log("ZIGBEE",
                       "Error: Endpoint %d Cluster %d has attribute count %d but NULL "
                       "attributes pointer",
                       ep_list[i].endpoint, j, ep_list[i].clusters[j].attribute_count);
                exit(1);
            }
            for (int k = 0; k < ep_list[i].clusters[j].attribute_count; k++) {
                io_log("ZIGBEE",
                       "    Attr %d: id=0x%04x, type=0x%02x, size=%d, flags=%d", k,
                       ep_list[i].clusters[j].attributes[k].attribute_id,
                       ep_list[i].clusters[j].attributes[k].data_type_id,
                       ep_list[i].clusters[j].attributes[k].size,
                       ep_list[i].clusters[j].attributes[k].flag);
            }
        }
    }
}

hal_zigbee_network_status_t hal_zigbee_get_network_status(void) {
    return network_status;
}

static hal_network_status_change_callback_t network_status_change_callback =
    NULL;

void hal_register_on_network_status_change_callback(
    hal_network_status_change_callback_t callback) {
    network_status_change_callback = callback;
}

void hal_zigbee_leave_network(void) {
    if (network_status == HAL_ZIGBEE_NETWORK_NOT_JOINED) {
        io_log("ZIGBEE", "Cannot leave network - not joined");
        return;
    }
    io_evt("zcl_leave_network");
    io_log("ZIGBEE", "Leaving network");
    network_status = HAL_ZIGBEE_NETWORK_NOT_JOINED;
    if (network_status_change_callback != NULL) {
        network_status_change_callback(hal_zigbee_get_network_status());
    }
}

void hal_zigbee_start_network_steering() {
    io_log("ZIGBEE", "Starting network steering (joining)");
    io_evt("zcl_start_network_steering");
    network_status = HAL_ZIGBEE_NETWORK_JOINING;
    if (network_status_change_callback != NULL) {
        network_status_change_callback(hal_zigbee_get_network_status());
    }
}

void hal_zigbee_register_on_attribute_change_callback(
    hal_attribute_change_callback_t callback) {
    attr_change_callback = callback;
    io_log("ZIGBEE", "Registered attribute change callback");
}

void hal_zigbee_register_on_zcl_activity_callback(
    hal_zcl_activity_callback_t callback) {
    zcl_activity_callback = callback;
    io_log("ZIGBEE", "Registered ZCL activity callback");
}

void hal_zigbee_set_poll_rate_ms(uint32_t new_poll_rate_ms) {
    poll_rate_ms = new_poll_rate_ms;
    io_log("ZIGBEE", "Set poll rate to %u ms", (unsigned)new_poll_rate_ms);
}

uint32_t hal_zigbee_get_poll_rate_ms(void) {
    return poll_rate_ms;
}

void hal_zigbee_notify_attribute_changed(uint8_t endpoint, uint16_t cluster_id,
                                         uint16_t attribute_id) {
    io_log("ZIGBEE", "Attribute changed: ep=%d, cluster=0x%04x, attr=0x%04x",
           endpoint, cluster_id, attribute_id);
    // In stub, do NOT call attr_change_callback here.
    // That callback models "attribute written via Zigbee" (incoming write).
    // Calling it from notify_attribute_changed() causes recursion when the
    // application updates attributes in response to a write.
    // Instead, emit an event so Python tests can observe the change.
    io_evt("zcl_attr_change ep=%u cluster=0x%04X attr=0x%04X", endpoint,
           cluster_id, attribute_id);
}

hal_zigbee_status_t hal_zigbee_send_cmd_to_bindings(const hal_zigbee_cmd *cmd) {
    if (!cmd)
        return HAL_ZIGBEE_ERR_BAD_ARG;

    if (network_status != HAL_ZIGBEE_NETWORK_JOINED) {
        io_log("ZIGBEE", "Cannot send command - not joined to network");
        return HAL_ZIGBEE_ERR_NOT_JOINED;
    }

    io_log("ZIGBEE", "Sending command: ep=%d, cluster=0x%04x, cmd=0x%02x, len=%d",
           cmd->endpoint, cmd->cluster_id, cmd->command_id, cmd->payload_len);

    char buffer[cmd->payload_len * 2 + 1];
    bytes_to_hexstr(cmd->payload, cmd->payload_len, buffer);

    io_evt("zcl_cmd_send ep=%u cluster=0x%04X cmd=0x%02X len=%u data_hex=%s",
           cmd->endpoint, cmd->cluster_id, cmd->command_id, cmd->payload_len,
           buffer);

    // Simulate sending to all relevant bindings
    int sent_count = 0;
    for (int i = 0; i < binding_count; i++) {
        if (bindings[i].endpoint == cmd->endpoint &&
            bindings[i].cluster_id == cmd->cluster_id) {
            io_log("ZIGBEE", "Sent to binding %d (addr=0x%04x)", i,
                   bindings[i].short_addr);
            sent_count++;
        }
    }

    if (sent_count == 0) {
        io_log("ZIGBEE", "No matching bindings found");
    }

    return HAL_ZIGBEE_OK;
}

hal_zigbee_status_t
hal_zigbee_send_report_attr(uint8_t endpoint, uint16_t cluster_id,
                            uint16_t attr_id, uint8_t zcl_type_id,
                            const void *value, uint8_t value_len) {
    if (!value)
        return HAL_ZIGBEE_ERR_BAD_ARG;

    if (network_status != HAL_ZIGBEE_NETWORK_JOINED) {
        io_log("ZIGBEE", "Cannot send report - not joined to network");
        return HAL_ZIGBEE_ERR_NOT_JOINED;
    }

    io_log("ZIGBEE",
           "Sending attribute report: ep=%d, cluster=0x%04x, attr=0x%04x, "
           "type=0x%02x, len=%d",
           endpoint, cluster_id, attr_id, zcl_type_id, value_len);
    io_evt("zcl_report_send ep=%u cluster=0x%04X attr=0x%04X", endpoint, cluster_id, attr_id);

    return HAL_ZIGBEE_OK;
}

hal_zigbee_status_t hal_zigbee_send_announce(void) {
    io_log("ZIGBEE", "Sending Zigbee announce");
    io_evt("zdo_announce");
    return HAL_ZIGBEE_OK;
}

void stub_zigbee_set_network_status(hal_zigbee_network_status_t status) {
    network_status = status;
    io_log("ZIGBEE", "Network status set to %d", network_status);
    if (network_status_change_callback != NULL) {
        network_status_change_callback(hal_zigbee_get_network_status());
    }
}

void stub_zigbee_add_binding(uint16_t short_addr, uint8_t endpoint,
                             uint16_t cluster_id) {
    if (binding_count >= MAX_BINDINGS) {
        io_log("ZIGBEE", "Cannot add binding - table full");
        return;
    }

    bindings[binding_count].short_addr = short_addr;
    bindings[binding_count].endpoint   = endpoint;
    bindings[binding_count].cluster_id = cluster_id;
    binding_count++;

    io_log("ZIGBEE", "Added binding: addr=0x%04x, ep=%d, cluster=0x%04x",
           short_addr, endpoint, cluster_id);
}

void stub_zigbee_clear_bindings(void) {
    binding_count = 0;
    io_log("ZIGBEE", "Cleared all bindings");
}

hal_zigbee_endpoint *stub_zigbee_get_endpoints(uint8_t *count) {
    if (count)
        *count = endpoints_count;
    return endpoints;
}

static hal_zigbee_cmd_result_t
stub_zigbee_simulate_command_internal(uint8_t endpoint, uint16_t cluster_id,
                                      uint8_t command_id, void *payload,
                                      uint16_t payload_len,
                                      bool trigger_activity) {
    io_log("ZIGBEE", "Simulating command: ep=%d, cluster=0x%04x, cmd=0x%02x",
           endpoint, cluster_id, command_id);

    if (trigger_activity && zcl_activity_callback != NULL) {
        zcl_activity_callback();
    }

    // Find the endpoint and cluster
    for (int i = 0; i < endpoints_count; i++) {
        if (endpoints[i].endpoint == endpoint) {
            for (int j = 0; j < endpoints[i].cluster_count; j++) {
                hal_zigbee_cluster *cluster = &endpoints[i].clusters[j];
                if (cluster->cluster_id == cluster_id && cluster->cmd_callback) {
                    cmd_active  = 1;
                    cmd_ep      = endpoint;
                    cmd_cluster = cluster_id;
                    hal_zigbee_cmd_result_t r = cluster->cmd_callback(endpoint, cluster_id,
                                                                      command_id, payload,
                                                                      payload_len);
                    cmd_active = 0;
                    return r;
                }
            }
        }
    }

    return HAL_ZIGBEE_CMD_SKIPPED;
}

void hal_zigbee_send_response(uint8_t command_id, const uint8_t *payload, uint8_t payload_len) {
    if (!cmd_active) {
        io_log("ZIGBEE", "Error: response outside a command");
        return;
    }
    if (cmd_to_group) {
        io_log("ZIGBEE", "No response to a group command (cluster 0x%04x cmd 0x%02x)",
               cmd_cluster, command_id);
        return;
    }
    char buffer[payload_len * 2 + 1];
    bytes_to_hexstr(payload, payload_len, buffer);
    io_evt("zcl_cmd_resp ep=%u cluster=0x%04X cmd=0x%02X len=%u data_hex=%s", cmd_ep,
           cmd_cluster, command_id, payload_len, buffer);
}

bool hal_zigbee_group_has_endpoint(uint16_t group_id, uint8_t endpoint) {
    for (int i = 0; i < STUB_GROUPS; i++)
        if (groups[i].endpoint == endpoint && groups[i].group_id == group_id)
            return true;

    return false;
}

void hal_zigbee_register_on_group_removed_callback(hal_group_removed_callback_t callback) {
    group_removed_callback = callback;
}

int stub_zigbee_group_add(uint8_t endpoint, uint16_t group_id) {
    if (hal_zigbee_group_has_endpoint(group_id, endpoint))
        return 0;

    for (int i = 0; i < STUB_GROUPS; i++) {
        if (!groups[i].endpoint) {
            groups[i].endpoint = endpoint;
            groups[i].group_id = group_id;
            return 0;
        }
    }
    return -1;
}

void stub_zigbee_group_remove(uint8_t endpoint, uint16_t group_id, bool all) {
    if (group_removed_callback)
        group_removed_callback(endpoint, group_id, all);
    for (int i = 0; i < STUB_GROUPS; i++)
        if (groups[i].endpoint == endpoint && (all || groups[i].group_id == group_id))
            groups[i].endpoint = 0;
}

/* To every endpoint in the group, as the stack delivers a groupcast; the last result. */
hal_zigbee_cmd_result_t stub_zigbee_simulate_group_command(uint16_t group_id, uint16_t cluster_id,
                                                           uint8_t command_id, void *payload,
                                                           uint16_t payload_len) {
    hal_zigbee_cmd_result_t r = HAL_ZIGBEE_CMD_SKIPPED;

    cmd_to_group = 1;
    for (int i = 0; i < STUB_GROUPS; i++)
        if (groups[i].endpoint && groups[i].group_id == group_id)
            r = stub_zigbee_simulate_command_internal(groups[i].endpoint, cluster_id, command_id,
                                                      payload, payload_len, true);
    cmd_to_group = 0;
    return r;
}

// Simulate receiving a ZCL command
hal_zigbee_cmd_result_t stub_zigbee_simulate_command(uint8_t endpoint,
                                                     uint16_t cluster_id,
                                                     uint8_t command_id,
                                                     void *payload,
                                                     uint16_t payload_len) {
    return stub_zigbee_simulate_command_internal(endpoint, cluster_id,
                                                 command_id, payload,
                                                 payload_len, true);
}

hal_zigbee_cmd_result_t
stub_zigbee_simulate_command_without_activity(uint8_t endpoint,
                                              uint16_t cluster_id,
                                              uint8_t command_id,
                                              void *payload,
                                              uint16_t payload_len) {
    return stub_zigbee_simulate_command_internal(endpoint, cluster_id,
                                                 command_id, payload,
                                                 payload_len, false);
}

void stub_simulate_zigbee_attribute_write(uint8_t endpoint, uint16_t cluster_id,
                                          uint16_t attribute_id) {
    if (zcl_activity_callback != NULL) {
        zcl_activity_callback();
    }

    if (attr_change_callback) {
        attr_change_callback(endpoint, cluster_id, attribute_id);
    }
}
