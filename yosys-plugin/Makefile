# Builds all 3D-Syn Yosys passes as one shared library using the selected yosys-config.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

PLUGIN_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
SRC_DIR := $(PLUGIN_ROOT)/src
BUILD_DIR ?= $(PLUGIN_ROOT)/build
PLUGIN := $(BUILD_DIR)/3dsyn.so

TAIWEI_HOME ?= $(abspath $(PLUGIN_ROOT)/../../TaiWei)
YOSYS_CONFIG ?= $(TAIWEI_HOME)/ORFS-Research/tools/install/yosys/bin/yosys-config
YOSYS_EXE ?= $(TAIWEI_HOME)/ORFS-Research/tools/install/yosys/bin/yosys

SOURCES := \
  $(SRC_DIR)/syn3d_mark_coarse_units.cc \
  $(SRC_DIR)/syn3d_normalize_instance_names.cc \
  $(SRC_DIR)/yosys_json_toolkit.cc \
  $(SRC_DIR)/syn3d_coarse_unit_timing.cc \
  $(SRC_DIR)/syn3d_coarse_unit_liberty.cc \
  $(SRC_DIR)/syn3d_write_coarse_unit_libs.cc
HEADERS := \
  $(SRC_DIR)/yosys_json_toolkit.h \
  $(SRC_DIR)/syn3d_coarse_unit_libs.h

.PHONY: all plugin load-check clean

all: plugin

plugin: $(PLUGIN)

$(PLUGIN): $(SOURCES) $(HEADERS)
	@command -v -- "$(YOSYS_CONFIG)" >/dev/null 2>&1 || { \
	  printf 'Missing executable yosys-config: %s\n' "$(YOSYS_CONFIG)" >&2; \
	  exit 2; \
	}
	@mkdir -p "$(BUILD_DIR)"
	"$(YOSYS_CONFIG)" --build "$@" -I"$(SRC_DIR)" $(SOURCES)

load-check: $(PLUGIN)
	@command -v -- "$(YOSYS_EXE)" >/dev/null 2>&1 || { \
	  printf 'Missing executable Yosys: %s\n' "$(YOSYS_EXE)" >&2; \
	  exit 2; \
	}
	"$(YOSYS_EXE)" -Q -p 'plugin -i "$(PLUGIN)"; help syn3d_mark_coarse_units; help syn3d_normalize_instance_names; help syn3d_write_coarse_unit_libs'

clean:
	@rm -f "$(PLUGIN)" "$(BUILD_DIR)/3dsyn.d"
