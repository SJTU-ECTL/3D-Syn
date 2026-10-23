# Public release interface for 3D-Syn.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

SYN3D_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ifneq ($(strip $(SYN3D_TAIWEI_HOME)),)
TAIWEI_HOME := $(abspath $(SYN3D_TAIWEI_HOME))
endif
include $(SYN3D_ROOT)/taiwei/3dsyn.mk

START_STAGE ?= coarse-synth
STOP_STAGE ?= backend-handoff
BACKEND_START_STAGE ?= 3d-floorplan
BACKEND_STOP_STAGE ?= final
OUTER_ITERATIONS ?= 1
BASELINE_STOP_STAGE ?= final

export START_STAGE STOP_STAGE BACKEND_START_STAGE BACKEND_STOP_STAGE
export OUTER_ITERATIONS
export BASELINE_STOP_STAGE

.PHONY: help plugin run baseline
.PHONY: _run-frontend _run-backend _run-all

help:
	@printf '%s\n' \
	  '3D-Syn release targets:' \
	  '  plugin     Build the 3D-Syn Yosys plugin.' \
	  '  run        Run the complete 3D-Syn frontend and backend flow.' \
	  '  baseline   Run the TaiWei comparison baseline.' \
	  '  help       Show this summary.' \
	  '' \
	  'Recommended entry points:' \
	  '  ./run_3d_syn.sh' \
	  '  ./run_baseline.sh' \
	  '' \
	  'Advanced users may pass PLATFORM, DESIGN, BACKEND, FLOW_VARIANT,' \
	  'RUN_DIR, NUM_CORES, AREA_MODE, and tool paths directly to make.'

plugin:
	@$(MAKE) --no-print-directory -C "$(SYN3D_PLUGIN_DIR)" \
	  TAIWEI_HOME="$(TAIWEI_HOME)" \
	  YOSYS_CONFIG="$(YOSYS_CONFIG)" \
	  YOSYS_EXE="$(YOSYS_EXE)" plugin

run: _run-all

baseline:
	@bash "$(SYN3D_BASELINE_DRIVER)" \
	  --taiwei-home "$(TAIWEI_HOME)" \
	  --platform "$(PLATFORM)" \
	  --design "$(DESIGN)" \
	  --backend "$(BACKEND)" \
	  --run-dir "$(RUN_DIR)" \
	  --yosys-bin "$(YOSYS_EXE)" \
	  --openroad-bin "$(OPENROAD_EXE)" \
	  --genus-bin "$(GENUS_EXE)" \
	  --innovus-bin "$(INNOVUS_EXE)" \
	  --python-bin "$(PYTHON_EXE)" \
	  --design-config "$(DESIGN_CONFIG_2D)" \
	  --design-config-3d "$(DESIGN_CONFIG_3D)" \
	  --design-sdc "$(DESIGN_SDC)" \
	  --fixed-core-area "$(FIXED_CORE_AREA)" \
	  --flow-variant "$(FLOW_VARIANT)" \
	  --num-cores "$(NUM_CORES)" \
	  --synth-opt-mode "$(SYNTH_OPT_MODE)" \
	  --tritonpart-timing-aware "$(TRITONPART_TIMING_AWARE)" \
	  --outer-iterations "$(OUTER_ITERATIONS)" \
	  --stop-stage "$(BASELINE_STOP_STAGE)"

_run-frontend:
	@bash "$(SYN3D_FLOW_DIR)/run_frontend.sh" --stage all

_run-backend:
	@bash "$(SYN3D_FLOW_DIR)/run_backend.sh"

_run-all:
	@bash "$(SYN3D_FLOW_DIR)/run_frontend.sh" --stage all
	@bash "$(SYN3D_FLOW_DIR)/run_backend.sh"
