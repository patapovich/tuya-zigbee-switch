# Telink SDK Sources Makefile
# This makefile handles compilation of all Telink SDK sources

# SDK paths
SDK_PATH := $(TELINK_TOOLS_DIR)/sdk

PLATFORM_SOURCES := \
	$(SDK_PATH)/platform/boot/8258/cstartup_8258.S \
    $(SDK_PATH)/platform/boot/link_cfg.S \
	$(SDK_PATH)/platform/services/b85m/irq_handler.c \
	$(SDK_PATH)/platform/tc32/div_mod.S \
	$(SDK_PATH)/platform/chip_8258/flash.c \
	$(SDK_PATH)/platform/chip_8258/flash/flash_common.c \
	$(SDK_PATH)/platform/chip_8258/flash/flash_mid1060c8.c \
	$(SDK_PATH)/platform/chip_8258/flash/flash_mid1360c8.c \
	$(SDK_PATH)/platform/chip_8258/flash/flash_mid011460c8.c \
	$(SDK_PATH)/platform/chip_8258/flash/flash_mid134051.c \
	$(SDK_PATH)/platform/chip_8258/flash/flash_mid136085.c \
	$(SDK_PATH)/platform/chip_8258/flash/flash_mid1360eb.c \
	$(SDK_PATH)/platform/chip_8258/flash/flash_mid14325e.c \
	$(SDK_PATH)/platform/chip_8258/flash/flash_mid1460c8.c \
	$(SDK_PATH)/platform/chip_8258/flash/flash_mid13325e.c \
	$(SDK_PATH)/platform/chip_8258/adc.c

PROJ_SOURCES := \
	$(SDK_PATH)/proj/common/list.c \
	$(SDK_PATH)/proj/common/mempool.c \
	$(SDK_PATH)/proj/common/tlPrintf.c \
	$(SDK_PATH)/proj/common/string.c \
	$(SDK_PATH)/proj/common/utility.c \
	$(SDK_PATH)/proj/drivers/drv_gpio.c \
	$(SDK_PATH)/proj/drivers/drv_adc.c \
	$(SDK_PATH)/proj/drivers/drv_nv.c \
	$(SDK_PATH)/proj/drivers/drv_pm.c \
	$(SDK_PATH)/proj/drivers/drv_putchar.c \
	$(SDK_PATH)/proj/drivers/drv_timer.c \
	$(SDK_PATH)/proj/drivers/drv_uart.c \
	$(SDK_PATH)/proj/drivers/drv_calibration.c \
	$(SDK_PATH)/proj/drivers/drv_flash.c \
	$(SDK_PATH)/proj/drivers/drv_hw.c \
	$(SDK_PATH)/proj/drivers/drv_security.c \
	$(SDK_PATH)/proj/os/ev.c \
	$(SDK_PATH)/proj/os/ev_buffer.c \
	$(SDK_PATH)/proj/os/ev_poll.c \
	$(SDK_PATH)/proj/os/ev_queue.c \
	$(SDK_PATH)/proj/os/ev_timer.c \
	$(SDK_PATH)/proj/os/ev_rtc.c

# Green Power (proxy basic). This firmware never calls gp_init(), so no GP
# cluster is registered and nothing is ever sent; what is linked is the
# stack's receive path for GP frames (cGp_mcpsDataInd, ~6 KB with the library
# members it pulls). A dimmer_only board leaves all of it out:
# patch_sdk/gp_stub.c drops a received GP frame instead (gp_stub.c says why
# that is safe).
# A dimmer_only board compiles zigbee/ota/ota.c and zigbee/zcl/ota_upgrading/zcl_ota.c
# through patch_sdk/ota_client_only.c and patch_sdk/zcl_ota_client_only.c (Makefile),
# which leave the OTA server's side out (~3.5 KB): the module is an OTA client only.
GP_SOURCES := \
	$(SDK_PATH)/zigbee/gp/gp.c \
	$(SDK_PATH)/zigbee/gp/gpEpCfg.c \
	$(SDK_PATH)/zigbee/gp/gp_proxy.c \
	$(SDK_PATH)/zigbee/gp/gp_proxyTab.c

ZIGBEE_SOURCES := \
	$(SDK_PATH)/zigbee/bdb/bdb.c \
	$(SDK_PATH)/zigbee/aps/aps_group.c \
	$(SDK_PATH)/zigbee/mac/mac_phy.c \
	$(SDK_PATH)/zigbee/mac/mac_pib.c \
	$(SDK_PATH)/zigbee/zdo/zdp.c \
	$(SDK_PATH)/zigbee/zcl/zcl.c \
	$(SDK_PATH)/zigbee/zcl/zcl_nv.c \
	$(SDK_PATH)/zigbee/zcl/zcl_reporting.c \
	$(SDK_PATH)/zigbee/zcl/hvac/zcl_thermostat.c \
	$(SDK_PATH)/zigbee/zcl/smart_energy/zcl_metering.c \
	$(SDK_PATH)/zigbee/zcl/smart_energy/zcl_metering_attr.c \
	$(SDK_PATH)/zigbee/zcl/zll_commissioning/zcl_toucklink_security.c \
	$(SDK_PATH)/zigbee/zcl/zll_commissioning/zcl_zllTouchLinkDiscovery.c \
	$(SDK_PATH)/zigbee/zcl/zll_commissioning/zcl_zllTouchLinkJoinOrStart.c \
	$(SDK_PATH)/zigbee/zcl/zll_commissioning/zcl_zll_commissioning.c \
	$(SDK_PATH)/zigbee/zcl/commissioning/zcl_commissioning.c \
	$(SDK_PATH)/zigbee/zcl/commissioning/zcl_commissioning_attr.c \
	$(SDK_PATH)/zigbee/zcl/hvac/zcl_thermostat.c \
	$(SDK_PATH)/zigbee/zcl/measument_sensing/zcl_electrical_measurement.c \
	$(SDK_PATH)/zigbee/zcl/measument_sensing/zcl_electrical_measurement_attr.c \
	$(SDK_PATH)/zigbee/zcl/measument_sensing/zcl_illuminance_measurement.c \
	$(SDK_PATH)/zigbee/zcl/measument_sensing/zcl_illuminance_measurement_attr.c \
	$(SDK_PATH)/zigbee/zcl/measument_sensing/zcl_occupancy_sensing.c \
	$(SDK_PATH)/zigbee/zcl/measument_sensing/zcl_occupancy_sensing_attr.c \
	$(SDK_PATH)/zigbee/zcl/measument_sensing/zcl_temperature_measurement.c \
	$(SDK_PATH)/zigbee/zcl/measument_sensing/zcl_temperature_measurement_attr.c \
	$(SDK_PATH)/zigbee/zcl/light_color_control/zcl_light_colorCtrl.c \
	$(SDK_PATH)/zigbee/zcl/light_color_control/zcl_light_colorCtrl_attr.c \
	$(SDK_PATH)/zigbee/zcl/closures/zcl_door_lock.c \
	$(SDK_PATH)/zigbee/zcl/closures/zcl_door_lock_attr.c \
	$(SDK_PATH)/zigbee/zcl/closures/zcl_window_covering.c \
	$(SDK_PATH)/zigbee/zcl/closures/zcl_window_covering_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_alarm.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_alarm_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_basic.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_basic_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_binary_input.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_binary_input_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_binary_output.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_binary_output_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_devTemperatureCfg.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_devTemperatureCfg_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_diagnostics.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_diagnostics_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_greenPower.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_greenPower_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_group.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_group_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_identify.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_identify_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_level.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_level_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_multistate_input.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_multistate_input_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_multistate_output.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_multistate_output_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_onoff.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_onoff_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_pollCtrl.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_pollCtrl_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_powerCfg.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_powerCfg_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_scene.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_scene_attr.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_time.c \
	$(SDK_PATH)/zigbee/zcl/general/zcl_time_attr.c \
	$(SDK_PATH)/zigbee/zcl/security_safety/zcl_ias_ace.c \
	$(SDK_PATH)/zigbee/zcl/security_safety/zcl_ias_wd.c \
	$(SDK_PATH)/zigbee/zcl/security_safety/zcl_ias_wd_attr.c \
	$(SDK_PATH)/zigbee/zcl/security_safety/zcl_ias_zone.c \
	$(SDK_PATH)/zigbee/zcl/security_safety/zcl_ias_zone_attr.c \
	$(SDK_PATH)/zigbee/zcl/zcl_wwah/zcl_wwah.c \
	$(SDK_PATH)/zigbee/zcl/zcl_wwah/zcl_wwah_attr.c \
	$(if $(DIMMER_ONLY),,$(SDK_PATH)/zigbee/zcl/ota_upgrading/zcl_ota.c) \
	$(SDK_PATH)/zigbee/zcl/ota_upgrading/zcl_ota_attr.c \
	$(SDK_PATH)/zigbee/common/zb_config.c \
	$(SDK_PATH)/zigbee/af/zb_af.c \
	$(SDK_PATH)/zigbee/wwah/wwah.c \
	$(SDK_PATH)/zigbee/wwah/wwahEpCfg.c \
	$(if $(DIMMER_ONLY),,$(GP_SOURCES)) \
	$(SDK_PATH)/zigbee/ss/ss_nv.c \
	$(if $(DIMMER_ONLY),,$(SDK_PATH)/zigbee/ota/ota.c) \
	$(SDK_PATH)/zigbee/ota/otaEpCfg.c
ALL_SDK_SOURCES := $(PLATFORM_SOURCES) $(PROJ_SOURCES) $(ZIGBEE_SOURCES)

# Object files
PLATFORM_OBJS := $(PLATFORM_SOURCES:$(SDK_PATH)/%.c=$(BUILD_DIR)/sdk/%.o)
PLATFORM_OBJS := $(PLATFORM_OBJS:$(SDK_PATH)/%.S=$(BUILD_DIR)/sdk/%.o)
PROJ_OBJS := $(PROJ_SOURCES:$(SDK_PATH)/%.c=$(BUILD_DIR)/sdk/%.o)
ZIGBEE_OBJS := $(ZIGBEE_SOURCES:$(SDK_PATH)/%.c=$(BUILD_DIR)/sdk/%.o)

# All SDK object files
SDK_OBJS := $(ALL_SDK_SOURCES:$(SDK_PATH)/%.c=$(BUILD_DIR)/sdk/%.o)
SDK_OBJS := $(SDK_OBJS:$(SDK_PATH)/%.S=$(BUILD_DIR)/sdk/%.o)

# SDK dependency files (only for C files, not assembly)
SDK_C_SOURCES := $(filter %.c,$(ALL_SDK_SOURCES))
SDK_DEPS := $(SDK_C_SOURCES:$(SDK_PATH)/%.c=$(BUILD_DIR)/sdk/%.d)

# Build all SDK objects
sdk-build: $(SDK_OBJS)
	@echo "SDK compilation complete: $(words $(SDK_OBJS)) object files"

# Clean SDK build artifacts
sdk-clean:
	@echo "Cleaning SDK build artifacts..."
	@rm -rf $(BUILD_DIR)/sdk

# Include SDK dependency files (if they exist)
-include $(SDK_DEPS)

# SDK files taken with fixes backported from later SDK releases, or with fixes of our own: the
# SDK's file (its CRs dropped) with patch_sdk/<its path>.patch applied, written to
# $(BUILD_DIR)/sdk_patched/ and compiled to the object the SDK's file would give, so every flag
# above (and the Makefile's) stays as for the SDK's file. The SDK file's own directory comes first
# on the include path, for its relative includes. Each patch says what it changes and from which
# release (zcl_reporting.c: reports with an APS ACK, ours).
SDK_PATCHED := zigbee/mac/mac_phy.c proj/drivers/drv_nv.c zigbee/bdb/bdb.c zigbee/zdo/zdp.c \
               zigbee/zcl/zcl_reporting.c
SDK_PATCHED_OBJS := $(SDK_PATCHED:%.c=$(BUILD_DIR)/sdk/%.o)
.SECONDARY: $(SDK_PATCHED:%.c=$(BUILD_DIR)/sdk_patched/%.c)

$(BUILD_DIR)/sdk_patched/%.c: $(SDK_PATH)/%.c patch_sdk/%.c.patch
	@mkdir -p $(dir $@)
	@tr -d '\r' < $< > $@.orig
	@patch -s -o - $@.orig < patch_sdk/$*.c.patch > $@.tmp && mv $@.tmp $@
	@rm -f $@.orig

$(SDK_PATCHED_OBJS): $(BUILD_DIR)/sdk/%.o: $(BUILD_DIR)/sdk_patched/%.c
	@echo "Compiling SDK $(SDK_PATH)/$*.c + patch_sdk/$*.c.patch"
	@mkdir -p $(dir $@)
	@$(CC) $(GCC_FLAGS) $(DEVICE_DEFS) -I$(dir $(SDK_PATH)/$*.c) $(INCLUDE_PATHS) -c -o $@ $<

# Special compiler flags for specific SDK files
$(BUILD_DIR)/sdk/proj/drivers/drv_nv.o: GCC_FLAGS += -Dnv_resetToFactoryNew=nv_resetToFactoryNew__sdk
# The SDK's xcrc32 and its 1 KB table renamed away (unreferenced: dropped by --gc-sections): its
# NV and OTA code call src/zigbee/crc32_nibble.c's, the same CRC in 64 bytes.
$(BUILD_DIR)/sdk/proj/common/utility.o: GCC_FLAGS += -Dxcrc32=xcrc32__sdk

$(SDK_OBJS): GCC_FLAGS += -fpack-struct
# Dimmer-only boards (near the OTA limit): the SDK's logic for size, as the project's own objects
# (src/telink/Makefile: -Os with -fno-jump-tables, which tc32-gcc's -Os needs; check-jump-tables
# checks the ELF). The radio, flash, clock and timer drivers (zigbee/mac, platform/, proj/drivers)
# keep -O2 for their timing; the NV driver (proj/drivers/drv_nv.c, no timing of its own: the flash
# driver below it keeps -O2) is -Os too, about 730 bytes less (ZDMS16-2). ~2.8 KB.
ifneq ($(DIMMER_ONLY),)
SDK_OS_OBJS := $(filter $(addprefix $(BUILD_DIR)/sdk/,zigbee/zcl/% zigbee/aps/% zigbee/af/% \
                 zigbee/bdb/% zigbee/zdo/% zigbee/common/% proj/os/% proj/common/% \
                 proj/drivers/drv_nv.o),$(SDK_OBJS))
$(SDK_OS_OBJS): GCC_FLAGS += -Os -fno-jump-tables
endif

# Compile SDK C files
$(BUILD_DIR)/sdk/%.o: $(SDK_PATH)/%.c
	@echo "Compiling SDK $<"
	@mkdir -p $(dir $@)
	@$(CC) $(GCC_FLAGS) $(DEVICE_DEFS) $(INCLUDE_PATHS) -c -o $@ $<

# Compile SDK assembly files
$(BUILD_DIR)/sdk/%.o: $(SDK_PATH)/%.S
	@echo "Assembling SDK $<"
	@mkdir -p $(dir $@)
	@$(CC) $(ASM_FLAGS) $(DEVICE_DEFS) $(INCLUDE_PATHS) -c -o $@ $<

# Help for SDK makefile
sdk-help:
	@echo "Telink SDK Build System"
	@echo "======================="
	@echo ""
	@echo "Targets:"
	@echo "  sdk-build           - Build all SDK object files"
	@echo "  sdk-clean           - Clean SDK build artifacts"
	@echo "  sdk-directories     - Create SDK build directories"
	@echo "  sdk-help            - Show this help"
	@echo ""
	@echo "Variables:"
	@echo "  SDK_PATH            - Path to Telink SDK ($(SDK_PATH))"
	@echo "  BUILD_DIR           - Build output directory ($(BUILD_DIR))"
	@echo ""
	@echo "Source files:"
	@echo "  Platform sources:   $(words $(PLATFORM_SOURCES)) files"
	@echo "  Project sources:    $(words $(PROJ_SOURCES)) files"  
	@echo "  Zigbee sources:     $(words $(ZIGBEE_SOURCES)) files"
	@echo "  Total SDK sources:  $(words $(ALL_SDK_SOURCES)) files"

.PHONY: sdk-build sdk-clean sdk-directories sdk-help