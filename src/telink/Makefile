# Project configuration

PROJECT_ROOT := ../..
BUILD_DIR := $(PROJECT_ROOT)/build/telink
BIN_DIR := $(BUILD_DIR)/bin
TELINK_TOOLS_DIR := $(PROJECT_ROOT)/telink_tools
SRC_DIR := $(PROJECT_ROOT)/src

# Version and board configuration
VERSION_STR ?= "0.0.0"
FILE_VERSION ?= 0
NVM_MIGRATIONS_VERSION ?= $(shell cat ../../NVM_MIGRATIONS_VERSION 2>/dev/null || echo "1")

FIRMWARE_BASENAME ?= tlc_switch
DEVICE_TYPE ?= router
DEBUG ?= 0
# The SWD master's dump job (reads an unprotected dimmer MCU over Zigbee): debug builds only
MCU_DUMP ?= 0
CONFIG_STR ?= ja5osu5g;TS004F-Loginovo;IC4i;SC2u;BTB5;M;
# CONFIG_STR ?= jl7qyupf;TS0012-custom;BA0f;LD7;SC2f;RC0;SC3f;RB4;
MANUFACTURER_ID ?= 4417
IMAGE_TYPE ?= 43521
OTA_VERSION ?= 
OTA_MANUFACTURER_ID ?= ${MANUFACTURER_ID}
OTA_IMAGE_TYPE ?= ${IMAGE_TYPE}

# Toolchain paths
SDK_PATH := $(TELINK_TOOLS_DIR)/sdk
TOOLCHAIN_PATH := $(TELINK_TOOLS_DIR)/toolchain/tc32
TC32_PATH := $(TOOLCHAIN_PATH)/bin

# Compiler and linker
CC := $(TC32_PATH)/tc32-elf-gcc
LD := $(TC32_PATH)/tc32-elf-ld
OBJCOPY := $(TC32_PATH)/tc32-elf-objcopy
OBJDUMP := $(TC32_PATH)/tc32-elf-objdump
SIZE := $(TC32_PATH)/tc32-elf-size
NM := $(TC32_PATH)/tc32-elf-nm

# Flash tool configuration
TLSRPGM_TTY ?= /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
TLSRPGM_TOOL := $(TELINK_TOOLS_DIR)/tlsrpgm/TlsrPgm.py

# Chip and library configuration with Zigbee libraries
ifeq ($(DEVICE_TYPE), router)
	TEL_CHIP := -DMCU_CORE_8258=1 -DROUTER=1 -DMCU_STARTUP_8258=1
	LIBS := -ldrivers_8258 -lzb_router
endif

ifeq ($(DEVICE_TYPE), end_device)
	TEL_CHIP := -DMCU_CORE_8258=1 -DEND_DEVICE=1 -DMCU_STARTUP_8258=1 -DPM_ENABLE
	LIBS := -ldrivers_8258 -lzb_ed
endif


# Compiler and linker flags
GCC_FLAGS := \
	-O2 \
	-ffunction-sections \
	-fdata-sections \
	-Wall \
	-fshort-enums \
	-finline-small-functions \
	-std=gnu99 \
	-funsigned-char \
	-fshort-wchar \
	-fms-extensions \
	-nostartfiles \
	-nostdlib \
	-MMD \
	-MP

LNK_FLAGS := --gc-sections -nostartfiles 

ASM_FLAGS := \
	-fomit-frame-pointer \
	-fshort-enums \
	-Wall \
	-Wpacked \
	-Wcast-align \
	-fdata-sections \
	-ffunction-sections \
	-fno-use-cxa-atexit \
	-fno-rtti \
	-fno-threadsafe-statics

# Device definitions
DEVICE_DEFS := \
    -DVERSION_STR="$(VERSION_STR)" \
	-DFILE_VERSION=$(FILE_VERSION) \
	-DNVM_MIGRATIONS_VERSION=$(NVM_MIGRATIONS_VERSION) \
	-DDEFAULT_CONFIG="$(CONFIG_STR)" \
	-DDEFAULT_DP_CONFIG="$(DP_CONFIG_STR)" \
	$(if $(SECONDARY_MCU_IMAGE),-DSECONDARY_MCU_IMAGE) \
	$(if $(DIMMER_ONLY),-DDIMMER_ONLY -DDIMMER_SCENES -DDIMMER_BINDINGS -DTUYA_MCU_SLOW_VALUE_ANSWERS) \
	-DIMAGE_TYPE=$(IMAGE_TYPE) \
	-DHAL_TELINK \
	$(TEL_CHIP) 

ifeq ($(DEBUG), 1)
	DEVICE_DEFS := $(DEVICE_DEFS) -DUART_PRINTF_MODE=1
endif
ifeq ($(MCU_DUMP), 1)
	DEVICE_DEFS := $(DEVICE_DEFS) -DMCU_FLASHER_DUMP
endif

# Include paths (SDK paths first to avoid conflicts)
INCLUDE_PATHS := \
	-I. \
	-Iconfigs \
	-Ilibc_polyfills \
	-Icustom_zcl \
	-I$(SRC_DIR) \
	-I$(SDK_PATH)/platform \
	-I$(SDK_PATH)/platform/chip_8258 \
	-I$(SDK_PATH)/proj \
	-I$(SDK_PATH)/proj/common \
	-I$(SDK_PATH)/zigbee \
	-I$(SDK_PATH)/zigbee/af \
	-I$(SDK_PATH)/zigbee/include \
	-I$(SDK_PATH)/zigbee/bdb/includes \
	-I$(SDK_PATH)/zigbee/common/includes \
	-I$(SDK_PATH)/zigbee/ota \
	-I$(SDK_PATH)/zigbee/zbapi \
	-I$(SDK_PATH)/zigbee/zbhci \
	-I$(SDK_PATH)/zigbee/zcl \
	-I$(SDK_PATH)/zigbee/zdo

# Library paths
LIBRARY_PATHS := -L$(SDK_PATH)/zigbee/lib/tc32 -L$(SDK_PATH)/platform/lib

# Source files
TELINK_SOURCES := \
	main.c \
	ota_reformating/ensure_ota_scheme.c \
	ota_reformating/ram_code_flash.c \
	hal/system.c \
	hal/timer.c \
	hal/tasks.c \
	hal/gpio.c \
	hal/gpio_interrupts.c \
	hal/nvm.c \
	hal/uart.c \
	hal/zigbee.c \
	hal/zigbee_network.c \
	hal/zigbee_zcl.c \
	hal/zigbee_ota.c \
	hal/adc.c \
	libc_polyfills/atoi.c \
	custom_zcl/zcl_multistate_input.c \
	custom_zcl/zcl_onoff_configuration.c \
	custom_zcl/zcl_cover_switch_config.c \
	custom_zcl/zcl_window_covering_custom.c \
	custom_zcl/zcl_ballast_config.c \
	custom_zcl/zcl_scenes_custom.c \
	patch_sdk/drv_nv.c

ifneq ($(DIMMER_ONLY),)
TELINK_SOURCES += patch_sdk/gp_stub.c patch_sdk/ota_client_only.c patch_sdk/zcl_ota_client_only.c
endif

# The SWD master of the secondary MCU (src/zigbee/mcu_flasher.c): only boards
# that carry an MCU image; dormant there without the SW config token.
ifneq ($(SECONDARY_MCU_IMAGE),)
TELINK_SOURCES += hal/swd_io.c
endif

# Common source files (shared with Silicon Labs build)
COMMON_SOURCES := \
	$(SRC_DIR)/app.c \
	$(SRC_DIR)/zigbee/poll_control_cluster.c \
	$(SRC_DIR)/base_components/button.c \
	$(SRC_DIR)/base_components/led.c \
	$(SRC_DIR)/base_components/network_indicator.c \
	$(SRC_DIR)/base_components/relay.c \
	$(SRC_DIR)/base_components/battery.c \
	$(SRC_DIR)/device_config/config_nv.c \
	$(SRC_DIR)/device_config/device_params_nv.c \
	$(SRC_DIR)/device_config/reset.c \
	$(SRC_DIR)/device_config/config_parser.c \
	$(SRC_DIR)/device_config/nvm_migrations.c \
	$(SRC_DIR)/zigbee/basic_cluster.c \
	$(SRC_DIR)/zigbee/tuya_secondary_mcu.c \
	$(SRC_DIR)/zigbee/tuya_mcu_diag.c \
	$(SRC_DIR)/zigbee/tuya_mcu_app.c \
	$(SRC_DIR)/zigbee/tuya_mcu_ota.c \
	$(SRC_DIR)/zigbee/crc32_nibble.c \
	$(SRC_DIR)/zigbee/stock_nv.c \
	$(SRC_DIR)/zigbee/factory_identity.c \
	$(SRC_DIR)/zigbee/secondary_mcu_image.c \
	$(SRC_DIR)/zigbee/dimmer_cluster.c \
	$(SRC_DIR)/zigbee/scene_cluster.c \
	$(SRC_DIR)/zigbee/general_commands.c \
	$(SRC_DIR)/zigbee/group_cluster.c \
	$(SRC_DIR)/zigbee/tuya_dp_relay.c \
	$(SRC_DIR)/zigbee/dp_attr.c \
	$(SRC_DIR)/zigbee/relay_cluster.c \
	$(SRC_DIR)/zigbee/switch_cluster.c \
	$(SRC_DIR)/zigbee/cover_switch_cluster.c \
	$(SRC_DIR)/zigbee/cover_cluster.c \
	$(SRC_DIR)/zigbee/battery_cluster.c 

ifneq ($(SECONDARY_MCU_IMAGE),)
COMMON_SOURCES += \
	$(SRC_DIR)/base_components/swd.c \
	$(SRC_DIR)/zigbee/mcu_flasher.c \
	$(SRC_DIR)/zigbee/secondary_mcu_boot.c
endif

# All application sources
ALL_TELINK_SOURCES := $(TELINK_SOURCES) $(COMMON_SOURCES)

# Include SDK build rules
include sdk.mk

# Application object files  
TELINK_OBJS := $(TELINK_SOURCES:%.c=$(BUILD_DIR)/telink/%.o)
COMMON_OBJS := $(COMMON_SOURCES:$(SRC_DIR)/%.c=$(BUILD_DIR)/common/%.o)
ALL_APP_OBJS := $(TELINK_OBJS) $(COMMON_OBJS)

# Dependency files for application sources
TELINK_DEPS := $(TELINK_SOURCES:%.c=$(BUILD_DIR)/telink/%.d)
COMMON_DEPS := $(COMMON_SOURCES:$(SRC_DIR)/%.c=$(BUILD_DIR)/common/%.d)
ALL_APP_DEPS := $(TELINK_DEPS) $(COMMON_DEPS)

# All object files (application + common + SDK)
ALL_OBJS := $(ALL_APP_OBJS) $(SDK_OBJS)

# Linker script
LINKER_SCRIPT := $(SDK_PATH)/platform/boot/8258/boot_8258.link

# Output files
ELF_FILE := $(BUILD_DIR)/$(FIRMWARE_BASENAME).elf
LST_FILE := $(BUILD_DIR)/$(FIRMWARE_BASENAME).lst
BIN_FILE := $(BIN_DIR)/$(FIRMWARE_BASENAME).bin
OTA_FILE := $(BIN_DIR)/$(FIRMWARE_BASENAME).ota
SIZEDUMMY := $(BUILD_DIR)/sizedummy

# Default target
all: $(BIN_FILE) $(LST_FILE)
	@echo "Build complete: $(BIN_FILE)"

# Build main ELF file
$(ELF_FILE): $(ALL_APP_OBJS) sdk-build
	@echo "Linking $(ELF_FILE)"
	@$(LD) $(LNK_FLAGS) -T$(LINKER_SCRIPT) $(LIBRARY_PATHS) -o $@ $(ALL_APP_OBJS) $(SDK_OBJS) $(LIBS)


$(LST_FILE): $(ELF_FILE)
	@echo 'Invoking: TC32 Create Extended Listing'
	@$(OBJDUMP) -x -D -l -S  $(ELF_FILE)  > $(LST_FILE)

# Create binary file
$(BIN_FILE): $(ELF_FILE)
	@echo "Creating binary $(BIN_FILE)"
	@mkdir -p $(dir $@)
	@$(OBJCOPY) -O binary $< $@
	@$(SIZE) $<

# The SDK files a dimmer_only board compiles through patch_sdk/ (ota_client_only.c,
# zcl_ota_client_only.c) get the SDK's flags (sdk.mk: -fpack-struct), so their
# structures have the layout the SDK's other objects and libraries expect.
$(BUILD_DIR)/telink/patch_sdk/ota_client_only.o $(BUILD_DIR)/telink/patch_sdk/zcl_ota_client_only.o: GCC_FLAGS += -fpack-struct

# Dimmer-only boards (the ZDMS16-2, whose OTA image carries the PY32 images and is near the SDK's
# limit): the project's own objects for size, ~5 KB less; the SDK's objects stay -O2.
# -fno-jump-tables is required: tc32-gcc lays out a switch's jump table as for -Os (bytes or
# halfwords in .text) but dispatches it as for -O2 (32-bit loads), so every switch would jump to
# garbage. check-jump-tables refuses such an ELF (helper_scripts/check_jump_tables.py).
ifneq ($(DIMMER_ONLY),)
$(ALL_APP_OBJS): GCC_FLAGS += -Os -fno-jump-tables
endif

# Compile Telink-specific C files
$(BUILD_DIR)/telink/%.o: %.c
	@echo "Compiling $<"
	@mkdir -p $(dir $@)
	@$(CC) $(GCC_FLAGS) $(DEVICE_DEFS) $(INCLUDE_PATHS) -c -o $@ $<

# Compile common C files
$(BUILD_DIR)/common/%.o: $(SRC_DIR)/%.c
	@echo "Compiling $<"
	@mkdir -p $(dir $@)
	@$(CC) $(GCC_FLAGS) $(DEVICE_DEFS) $(INCLUDE_PATHS) -c -o $@ $<

# SDK compilation rules are in sdk.mk


# Open Dimmer support (the MCU identity question, treated restarts, the
# carried image) only in a board whose device_db entry has secondary_mcu_image:
# the factory MCUs of every other board never see it (tuya_mcu_ota.h,
# contract items 4 and 6). Read from the linked ELF, every board build.
check-open-dimmer: $(ELF_FILE)
	@python3 $(PROJECT_ROOT)/helper_scripts/check_open_dimmer_symbols.py --nm $(NM) \
	  $(if $(SECONDARY_MCU_IMAGE),--with-image) $(ELF_FILE)

# The switch, relay, cover and battery endpoints left out exactly in a board
# whose device_db entry has dimmer_only (src/device_config/config_parser.c).
check-dimmer-only: $(ELF_FILE)
	@python3 $(PROJECT_ROOT)/helper_scripts/check_dimmer_only_symbols.py --nm $(NM) \
	  $(if $(DIMMER_ONLY),--dimmer-only) $(ELF_FILE)

# No switch jump table inside .text: tc32-gcc's -Os layout with an -O2 dispatch (see the
# -fno-jump-tables above; also a per-function optimize level does it). Every board build.
check-jump-tables: $(ELF_FILE)
	@python3 $(PROJECT_ROOT)/helper_scripts/check_jump_tables.py --objdump $(OBJDUMP) $(ELF_FILE)

# Build with tools check
build: all check-open-dimmer check-dimmer-only check-jump-tables

ifdef OTA_VERSION
OTA_VERSION_ARG := --file-version $(OTA_VERSION)
endif

# Size limit of every OTA file (normal, forced and from_tuya). This build uses
# the SDK's 512 KB flash map without a bootloader (proj/drivers/drv_nv.h):
# image slots at 0x0 and 0x40000, NV_1 at 0x34000, MAC address at 0x76000,
# factory calibration at 0x77000, pre-install code at 0x78000, NV_2 at 0x7A000.
# The SDK OTA client answers INSUFFICIENT_SPACE to a QueryNextImageResponse
# whose image size (the whole .zigbee file) is over FLASH_OTA_IMAGE_MAX_SIZE,
# 0x34000 = 208 KB, and ota_newImageValid() refuses a size field over it; a
# bigger image in slot 0 would also run into NV_1. The from_tuya conversion
# (ota_reformating/ensure_ota_scheme.c) copies the image from 0x8000 to
# 0x40000 and only breaks above 0x36000 (MAC sector) or 0x38000 (its own
# unread source), so this one limit covers it too. The limit is read from the
# SDK with this build's flags; make_ota.py refuses a bigger file (padding and
# CRC included) and warns when less than 4 KB is left.
OTA_MAX_SIZE_EXPR = printf '\#include "tl_common.h"\nFLASH_OTA_IMAGE_MAX_SIZE\n' | \
	$(CC) $(DEVICE_DEFS) $(INCLUDE_PATHS) -E -P -x c - | tail -n 1

# Make an ota file. It links the ELF again (sdk-build is phony), so the
# symbol checks run on that link too: an OTA file made without the board's
# DIMMER_ONLY / SECONDARY_MCU_IMAGE fails here (board.mk OTA_LINK_VARS).
ota: $(BIN_FILE) check-open-dimmer check-dimmer-only check-jump-tables
	@if [ -z "$(OTA_MANUFACTURER_ID)" ] || [ "$(OTA_MANUFACTURER_ID)" = "null" ] || \
      [ -z "$(OTA_IMAGE_TYPE)" ]      || [ "$(OTA_IMAGE_TYPE)" = "null" ]; then \
        echo "Skipping OTA file generation: missing arguments"; \
        echo " "; \
    else \
	    max_size=$$($(OTA_MAX_SIZE_EXPR)); \
	    max_size=$$(($${max_size:-0})); \
	    if [ "$$max_size" -le 0 ]; then \
	        echo "ERROR: cannot read FLASH_OTA_IMAGE_MAX_SIZE from the SDK"; \
	        exit 1; \
	    fi; \
	    echo 'Creating OTA file $(OTA_FILE)'; \
			echo ' '; \
	    mkdir -p $(dir $(OTA_FILE)); \
	    python3 $(SRC_DIR)/telink/make_ota.py create-ota \
	      --manufacturer-id $(OTA_MANUFACTURER_ID) \
	      --image-type $(OTA_IMAGE_TYPE) \
	      --max-size $$max_size \
	      $(OTA_VERSION_ARG) \
	      $(BIN_FILE) $(OTA_FILE) || exit 1; \
	    echo ''; \
	fi

# Flash binary to device
flash: $(BIN_FILE)
	@echo "Flashing $(BIN_FILE) to device via $(TLSRPGM_TTY)"
	@python3 $(TLSRPGM_TOOL) -p$(TLSRPGM_TTY) -t25 -a 20 --mrst we 0 $(BIN_FILE)

# Restart device
restart:
	@echo "Restarting device via $(TLSRPGM_TTY)"
	@python3 $(TLSRPGM_TOOL) -p$(TLSRPGM_TTY)  -t25 -a 20 --mrst

# Wipe device
wipe: 
	@echo "Wiping device via $(TLSRPGM_TTY)"
	@python3 $(TLSRPGM_TOOL) -p$(TLSRPGM_TTY) -t25 -a 20 ea

flasher:
	@python3 $(TLSRPGM_TOOL) -p$(TLSRPGM_TTY) $(ARGS)

# Include dependency files (if they exist)
-include $(ALL_APP_DEPS)

# Clean build artifacts
clean: sdk-clean
	@echo "Cleaning Telink build artifacts..."
	@rm -rf $(BUILD_DIR)/telink
	@rm -rf $(BUILD_DIR)/common
	@rm -rf $(BIN_DIR)


# Delegate tools targets to tools.mk
tools/%:
	$(MAKE) -f tools.mk $*

# Help target
help:
	@echo "Telink Build System"
	@echo "=================="
	@echo ""
	@echo "Tools Setup Targets:"
	@echo "  tools/all          - Download and install all Telink tools"
	@echo "  tools/status       - Show installation status and verify tools"
	@echo "  tools/verify       - Verify installed tools"
	@echo "  tools/help         - Show detailed tools help"
	@echo ""
	@echo "Quick Start:"
	@echo "  make build          - Check tools and build firmware"
	@echo "  make all            - Build firmware (assumes tools installed)"
	@echo ""
	@echo "Build Targets:"
	@echo "  build               - Build with tools verification"
	@echo "  all                 - Build firmware (ELF + BIN + LST files)"
	@echo "  ota                 - Create OTA update file from binary"
	@echo "  clean               - Clean build artifacts"
	@echo "  sdk-build           - Build SDK sources only"
	@echo "  sdk-clean           - Clean SDK build artifacts"
	@echo "  sdk-help            - Show SDK build help"
	@echo ""
	@echo "Hardware/Device Targets:"
	@echo "  flash               - Flash binary to device via programmer"
	@echo "  restart             - Restart device via programmer"
	@echo "  wipe                - Wipe device flash via programmer"
	@echo "  flasher             - Run programmer tool with custom ARGS"
	@echo ""
	@echo "Configuration Variables:"
	@echo "  APP_RELEASE         - Application release version (default: $(APP_RELEASE))"
	@echo "  APP_BUILD           - Application build version (default: $(APP_BUILD))"
	@echo "  STACK_RELEASE       - Stack release version (default: $(STACK_RELEASE))"
	@echo "  STACK_BUILD         - Stack build version (default: $(STACK_BUILD))"
	@echo "  DEVICE_TYPE         - router or end_device (default: $(DEVICE_TYPE))"
	@echo "  CONFIG_STR          - Device pin configuration string"
	@echo "  DEBUG               - Enable debug output (0/1, default: $(DEBUG))"
	@echo "  MCU_DUMP            - The SWD master's dump job, debug builds (0/1, default: $(MCU_DUMP))"
	@echo "  TLSRPGM_TTY         - Programmer serial port (default: $(TLSRPGM_TTY))"
	@echo ""
	@echo "Help Targets:"
	@echo "  help                - Show this help"
	@echo ""
	@echo "Output Files:"
	@echo "  ELF: $(ELF_FILE)"
	@echo "  BIN: $(BIN_FILE)"
	@echo "  LST: $(LST_FILE)"

.PHONY: check-open-dimmer check-dimmer-only all build clean ota flash restart wipe flasher help
