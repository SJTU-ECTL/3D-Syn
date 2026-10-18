# Shared configuration contract for the non-invasive TaiWei overlay.
# Tool installation paths are supplied by the caller, never stored here.

SYN3D_ROOT ?= $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
TAIWEI_HOME ?= $(abspath $(SYN3D_ROOT)/../TaiWei)
TAIWEI_FLOW_HOME ?= $(TAIWEI_HOME)/TaiWei-Pin-3D

SYN3D_FLOW_DIR ?= $(SYN3D_ROOT)/flow
SYN3D_PLUGIN_DIR ?= $(SYN3D_ROOT)/yosys-plugin
AREA_MODEL_HOME ?= $(SYN3D_ROOT)/area_model
SYN3D_CONFIG_DIR ?= $(SYN3D_ROOT)/configs
SYN3D_BASELINE_DIR ?= $(SYN3D_FLOW_DIR)/baseline
SYN3D_BACKEND_DIR ?= $(SYN3D_FLOW_DIR)/backend
SYN3D_BACKEND_HANDOFF_DIR ?= $(SYN3D_FLOW_DIR)/backend_handoff
SYN3D_JSON_TOOLKIT_DIR ?= $(SYN3D_FLOW_DIR)/yosys_json_toolkit

PLATFORM ?=
DESIGN ?=
FLOW_VARIANT ?= 3dsyn
BACKEND ?= cds
RUN_DIR ?= $(SYN3D_ROOT)/runs
NUM_CORES ?= 1
AREA_MODE ?= gnn
FIXED_UTIL ?=
SYNTH_OPT_MODE ?= low_effort
PREPLACE_OPT ?= 0
TRITONPART_TIMING_AWARE ?= 0
FIXED_CORE_AREA ?=
CORE_ASPECT_RATIO ?=
PYTHON_EXE ?= $(SYN3D_ROOT)/flow/env_isolation/python_launcher.sh
OPENROAD_EXE ?= $(TAIWEI_HOME)/ORFS-Research/tools/install/OpenROAD/bin/openroad

DESIGN_CONFIG_2D ?= $(SYN3D_CONFIG_DIR)/$(PLATFORM)/$(DESIGN)/config2d.mk
DESIGN_CONFIG_3D ?= $(SYN3D_CONFIG_DIR)/$(PLATFORM)/$(DESIGN)/config.mk
DESIGN_CONFIG ?= $(DESIGN_CONFIG_2D)
DESIGN_SDC ?= $(SYN3D_CONFIG_DIR)/$(PLATFORM)/$(DESIGN)/constraint.sdc
CHECKPOINT_ROOT ?= $(AREA_MODEL_HOME)/pretrained
YOSYS_PLUGIN ?= $(SYN3D_PLUGIN_DIR)/build/3dsyn.so
YOSYS_EXE ?= $(TAIWEI_HOME)/ORFS-Research/tools/install/yosys/bin/yosys
YOSYS_CONFIG ?= $(TAIWEI_HOME)/ORFS-Research/tools/install/yosys/bin/yosys-config
GENUS_EXE ?= genus
INNOVUS_EXE ?= innovus

SYN3D_PLATFORM ?= $(PLATFORM)
SYN3D_PLATFORM_DIR ?= $(TAIWEI_FLOW_HOME)/platforms/$(SYN3D_PLATFORM)
SYN3D_COARSE_UNIT_KEEP_MODE ?= all
# Coarse synthesis maps the logical 2D base netlist before per-tier mapping.
# The hybrid 3D platform otherwise exposes six tier-specific Liberty files to
# one ABC invocation, which is not a valid mapping contract. Keep this as an
# overlay variable so callers can replace it without editing design configs.
ifeq ($(SYN3D_PLATFORM),asap7_nangate45_3D)
SYN3D_COARSE_LIB_FILES := $(TAIWEI_FLOW_HOME)/platforms/asap7_nangate45/lib/NangateOpenCellLibrary_typical.processed.lib $(ADDITIONAL_LIBS)
SYN3D_COARSE_DFF_LIB_FILE ?= $(TAIWEI_FLOW_HOME)/platforms/asap7_nangate45/lib/NangateOpenCellLibrary_typical.processed.lib
SYN3D_COARSE_ABC_DRIVER_CELL ?= BUF_X1
SYN3D_COARSE_ABC_LOAD_IN_FF ?= 3.898
SYN3D_COARSE_MIN_BUF_CELL_AND_PORTS ?= BUF_X1 A Z
SYN3D_COARSE_DONT_USE_CELLS ?= TAPCELL_X1 FILLCELL_X1 AOI211_X1 OAI211_X1
SYN3D_COARSE_TIEHI_CELL_AND_PORT ?= LOGIC1_X1 Z
SYN3D_COARSE_TIELO_CELL_AND_PORT ?= LOGIC0_X1 Z
else ifeq ($(SYN3D_PLATFORM),asap7_3D)
# The outer 3D-Syn Makefile is parsed before TaiWei's platform config, so its
# exported compatibility variables can otherwise be empty in the inner 2D
# frontend make.  Resolve the canonical 2D ASAP7 view explicitly here.
SYN3D_COARSE_LIB_FILES := $(TAIWEI_FLOW_HOME)/platforms/asap7/lib/NLDM/asap7sc7p5t_AO_RVT_FF_nldm_211120.lib $(TAIWEI_FLOW_HOME)/platforms/asap7/lib/NLDM/asap7sc7p5t_INVBUF_RVT_FF_nldm_220122.lib $(TAIWEI_FLOW_HOME)/platforms/asap7/lib/NLDM/asap7sc7p5t_OA_RVT_FF_nldm_211120.lib $(TAIWEI_FLOW_HOME)/platforms/asap7/lib/NLDM/asap7sc7p5t_SIMPLE_RVT_FF_nldm_211120.lib $(TAIWEI_FLOW_HOME)/platforms/asap7/lib/NLDM/asap7sc7p5t_SEQ_RVT_FF_nldm_220123.lib $(ADDITIONAL_LIBS)
SYN3D_COARSE_ABC_DRIVER_CELL := BUFx2_ASAP7_75t_R
SYN3D_COARSE_ABC_LOAD_IN_FF := 3.898
SYN3D_COARSE_MIN_BUF_CELL_AND_PORTS := BUFx2_ASAP7_75t_R A Y
SYN3D_COARSE_DONT_USE_CELLS := *x1p*_ASAP7* *xp*_ASAP7* SDF* ICG*
SYN3D_COARSE_TIEHI_CELL_AND_PORT := TIEHIx1_ASAP7_75t_R H
SYN3D_COARSE_TIELO_CELL_AND_PORT := TIELOx1_ASAP7_75t_R L
else ifeq ($(SYN3D_PLATFORM),nangate45_3D)
# Same compatibility rule for the homogeneous Nangate45 2D frontend view.
SYN3D_COARSE_LIB_FILES := $(TAIWEI_FLOW_HOME)/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib $(ADDITIONAL_LIBS)
SYN3D_COARSE_ABC_DRIVER_CELL := BUF_X1
SYN3D_COARSE_ABC_LOAD_IN_FF := 3.898
SYN3D_COARSE_MIN_BUF_CELL_AND_PORTS := BUF_X1 A Z
SYN3D_COARSE_DONT_USE_CELLS := TAPCELL_X1 FILLCELL_X1 AOI211_X1 OAI211_X1
SYN3D_COARSE_TIEHI_CELL_AND_PORT := LOGIC1_X1 Z
SYN3D_COARSE_TIELO_CELL_AND_PORT := LOGIC0_X1 Z
else
SYN3D_COARSE_LIB_FILES ?= $(LIB_FILES)
SYN3D_COARSE_DFF_LIB_FILE ?=
SYN3D_COARSE_ABC_DRIVER_CELL ?= $(ABC_DRIVER_CELL)
SYN3D_COARSE_ABC_LOAD_IN_FF ?= $(ABC_LOAD_IN_FF)
SYN3D_COARSE_MIN_BUF_CELL_AND_PORTS ?= $(MIN_BUF_CELL_AND_PORTS)
SYN3D_COARSE_DONT_USE_CELLS ?= $(DONT_USE_CELLS)
SYN3D_COARSE_TIEHI_CELL_AND_PORT ?= $(BOTTOM_TIEHI_CELL_AND_PORT)
SYN3D_COARSE_TIELO_CELL_AND_PORT ?= $(BOTTOM_TIELO_CELL_AND_PORT)
endif
SYN3D_COARSE_SYNTH_DIR ?= $(OBJECTS_DIR)/3dsyn/coarse_synth
SYN3D_PER_TIER_SYNTH_DIR ?= $(OBJECTS_DIR)/3dsyn/per_tier_synth
SYN3D_HANDOFF_DIR ?= $(OBJECTS_DIR)/3dsyn/handoff
# The case's 2D design config owns FIXED_UTIL. It is consumed only in fixed
# mode; a populated case value must not silently disable GNN inference.
ifeq ($(AREA_MODE),fixed)
SYN3D_FIXED_UTIL ?= $(FIXED_UTIL)
else
SYN3D_FIXED_UTIL ?=
endif
SYN3D_HBT_MODEL_MODE ?= direct
SYN3D_HBT_R_SCALE ?= 0
SYN3D_HBT_C_SCALE ?= 0.25
ifeq ($(SYN3D_HBT_MODEL_MODE),direct)
SYN3D_HBT_R_OHM ?= 1
SYN3D_HBT_C_FF ?= 0.5
else
SYN3D_HBT_R_OHM ?=
SYN3D_HBT_C_FF ?=
endif

SYN3D_BASELINE_DRIVER ?= $(SYN3D_BASELINE_DIR)/run_baseline_synth.sh
SYN3D_BASELINE_GENUS_TCL ?= $(SYN3D_BASELINE_DIR)/run_genus_on_netlist.tcl
SYN3D_ORD_PREPLACE_TCL ?= $(SYN3D_BACKEND_DIR)/ord/floorplan.tcl
SYN3D_ORD_FLOORPLAN_3D_TCL ?= $(SYN3D_BACKEND_DIR)/ord/floorplan_3d.tcl
SYN3D_CDS_PREPLACE_TCL ?= $(SYN3D_BACKEND_DIR)/cds/innovus_preplace.tcl
SYN3D_CDS_FLOORPLAN_3D_TCL ?= $(SYN3D_BACKEND_DIR)/cds/innovus_3d_floorplan.tcl
SYN3D_CDS_NORMALIZE_TIER_ROWS_TCL ?= $(SYN3D_BACKEND_DIR)/cds/normalize_tier_rows.tcl
SYN3D_TIER_PARTITION_TCL ?= $(SYN3D_FLOW_DIR)/coarse_partition/tier_partition_overlay.tcl

# Compatibility names consumed by the current 3D-Syn reference scripts.
SYN3D_GENUS_OPT_MODE ?= $(SYNTH_OPT_MODE)
SYN3D_BACKEND_PREPLACE_OPT ?= $(PREPLACE_OPT)

export SYN3D_ROOT TAIWEI_HOME TAIWEI_FLOW_HOME
export SYN3D_FLOW_DIR SYN3D_PLUGIN_DIR AREA_MODEL_HOME SYN3D_CONFIG_DIR
export SYN3D_BASELINE_DIR SYN3D_BACKEND_DIR
export SYN3D_BACKEND_HANDOFF_DIR SYN3D_JSON_TOOLKIT_DIR
export PLATFORM DESIGN FLOW_VARIANT BACKEND RUN_DIR NUM_CORES
export AREA_MODE FIXED_UTIL CHECKPOINT_ROOT YOSYS_PLUGIN YOSYS_EXE YOSYS_CONFIG
export PYTHON_EXE OPENROAD_EXE GENUS_EXE INNOVUS_EXE
export DESIGN_CONFIG DESIGN_CONFIG_2D DESIGN_CONFIG_3D DESIGN_SDC
export SYNTH_OPT_MODE PREPLACE_OPT TRITONPART_TIMING_AWARE
export FIXED_CORE_AREA CORE_ASPECT_RATIO
export SYN3D_GENUS_OPT_MODE SYN3D_BACKEND_PREPLACE_OPT
export SYN3D_CDS_NORMALIZE_TIER_ROWS_TCL
export SYN3D_TIER_PARTITION_TCL
export SYN3D_PLATFORM SYN3D_PLATFORM_DIR SYN3D_COARSE_UNIT_KEEP_MODE
export SYN3D_COARSE_LIB_FILES SYN3D_COARSE_DFF_LIB_FILE
export SYN3D_COARSE_ABC_DRIVER_CELL SYN3D_COARSE_ABC_LOAD_IN_FF
export SYN3D_COARSE_MIN_BUF_CELL_AND_PORTS
export SYN3D_COARSE_DONT_USE_CELLS
export SYN3D_COARSE_TIEHI_CELL_AND_PORT SYN3D_COARSE_TIELO_CELL_AND_PORT
export SYN3D_COARSE_SYNTH_DIR SYN3D_PER_TIER_SYNTH_DIR SYN3D_HANDOFF_DIR
export SYN3D_FIXED_UTIL SYN3D_HBT_MODEL_MODE SYN3D_HBT_R_SCALE SYN3D_HBT_C_SCALE
export SYN3D_HBT_R_OHM SYN3D_HBT_C_FF

# The four frontend targets reuse the original TaiWei platform and library
# setup while running only repository-local 3D-Syn stage implementations.
.PHONY: 3dsyn-coarse-synth 3dsyn-ord-coarse-partition
.PHONY: 3dsyn-cds-coarse-partition 3dsyn-per-tier-synth
.PHONY: 3dsyn-ord-tier-partition 3dsyn-cds-tier-partition
.PHONY: 3dsyn-ord-backend-handoff 3dsyn-cds-backend-handoff

3dsyn-coarse-synth: prep-libs
	@printf '[3D-Syn] Coarse synthesis\n'
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/1_3dsyn_coarse_synth.log,cd "$(OBJECTS_DIR)" && /usr/bin/env \
	  DONT_USE_LIBS="$(DONT_USE_LIBS)" \
	  DONT_USE_SC_LIB="$(DONT_USE_SC_LIB)" \
	  DONT_USE_CELLS="$(SYN3D_COARSE_DONT_USE_CELLS)" \
	  LIB_SYNTH="$(DONT_USE_LIBS)" \
	  LIB_FILES="$(SYN3D_COARSE_LIB_FILES)" \
	  DFF_LIB_FILE="$(SYN3D_COARSE_DFF_LIB_FILE)" \
	  LIB_DIR="$(LIB_DIR)" \
	  LEF_DIR="$(LEF_DIR)" \
	  TECH_LEF="$(TECH_LEF)" \
	  SC_LEF="$(SC_LEF)" \
	  ADDITIONAL_LEFS="$(ADDITIONAL_LEFS)" \
	  VERILOG_FILES="$(VERILOG_FILES)" \
	  SDC_FILE="$(DESIGN_SDC)" \
	  DESIGN_NAME="$(DESIGN_NAME)" \
	  SYNTH_ARGS="$(SYNTH_ARGS)" \
	  SYNTH_HIERARCHICAL="$(SYNTH_HIERARCHICAL)" \
	  ABC_AREA="$(ABC_AREA)" \
	  ABC_DRIVER_CELL="$(SYN3D_COARSE_ABC_DRIVER_CELL)" \
	  ABC_LOAD_IN_FF="$(SYN3D_COARSE_ABC_LOAD_IN_FF)" \
	  MIN_BUF_CELL_AND_PORTS="$(SYN3D_COARSE_MIN_BUF_CELL_AND_PORTS)" \
	  TIEHI_CELL_AND_PORT="$(SYN3D_COARSE_TIEHI_CELL_AND_PORT)" \
	  TIELO_CELL_AND_PORT="$(SYN3D_COARSE_TIELO_CELL_AND_PORT)" \
	  ADDER_MAP_FILE="$(ADDER_MAP_FILE)" \
	  LATCH_MAP_FILE="$(LATCH_MAP_FILE)" \
	  CLKGATE_MAP_FILE="$(CLKGATE_MAP_FILE)" \
	  MAX_UNGROUP_SIZE="$(MAX_UNGROUP_SIZE)" \
	  $(TIME_CMD) bash "$(SYN3D_BASELINE_DIR)/../coarse_synth/run_coarse_synth.sh")

3dsyn-ord-coarse-partition:
	@printf '[3D-Syn][ORD] Coarse partition\n'
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/2_3dsyn_coarse_partition.log,/usr/bin/env \
	  DESIGN_CONFIG="$(DESIGN_CONFIG)" \
	  ADDITIONAL_LEFS="$(ADDITIONAL_LEFS)" \
	  ADDITIONAL_LIBS="$(ADDITIONAL_LIBS)" \
	  $(TIME_CMD) bash "$(SYN3D_BASELINE_DIR)/../coarse_partition/run_ord_coarse_partition.sh")

3dsyn-cds-coarse-partition:
	@printf '[3D-Syn][CDS] Coarse partition\n'
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/2_3dsyn_coarse_partition.log,/usr/bin/env \
	  DESIGN_CONFIG="$(DESIGN_CONFIG)" \
	  ADDITIONAL_LEFS="$(ADDITIONAL_LEFS)" \
	  ADDITIONAL_LIBS="$(ADDITIONAL_LIBS)" \
	  $(TIME_CMD) bash "$(SYN3D_BASELINE_DIR)/../coarse_partition/run_cds_coarse_partition.sh")

3dsyn-ord-tier-partition:
	@echo "[3D-Syn][ORD] Tier partition"
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/2_tritonpart.log,$(OPENROAD_CMD) $(SYN3D_TIER_PARTITION_TCL))
	@echo "[3D-Syn][ORD] Copy 2D artifacts to $(3D_PLATFORM)"
	@mkdir -p $(WORK_HOME)/results/$(3D_PLATFORM)/$(DESIGN_NICKNAME)/$(FLOW_VARIANT)
	@cp -rf $(RESULTS_DIR)/* $(WORK_HOME)/results/$(3D_PLATFORM)/$(DESIGN_NICKNAME)/$(FLOW_VARIANT)/ || true

3dsyn-cds-tier-partition:
	@echo "[3D-Syn][CDS] Build OpenDB handoff for TritonPart"
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/2_2_cds_partition_handoff.log,$(TIME_CMD) $(OPENROAD_CMD) $(OPENROAD_SCRIPTS_DIR)/cds_partition_handoff.tcl)
	@$(MAKE) --no-print-directory -C "$(FLOW_HOME)" \
	  -f "$(FLOW_HOME)/Makefile" -f "$(SYN3D_ROOT)/taiwei/3dsyn.mk" \
	  DESIGN_CONFIG="$(DESIGN_CONFIG)" 3dsyn-ord-tier-partition

3dsyn-per-tier-synth:
	@printf '[3D-Syn] Per-tier synthesis\n'
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/3_3dsyn_per_tier_synth.log,cd "$(OBJECTS_DIR)" && /usr/bin/env \
	  DESIGN_NAME="$(DESIGN_NAME)" \
	  SDC_FILE="$(DESIGN_SDC)" \
	  $(TIME_CMD) bash "$(SYN3D_BASELINE_DIR)/../per_tier_synth/run_per_tier_synth.sh")

3dsyn-cds-backend-handoff:
	@printf '[3D-Syn][CDS] Backend handoff\n'
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/4_3dsyn_handoff.log,cd "$(OBJECTS_DIR)" && /usr/bin/env \
	  DESIGN_CONFIG="$(DESIGN_CONFIG)" \
	  DESIGN_NAME="$(DESIGN_NAME)" \
	  SDC_FILE="$(DESIGN_SDC)" \
	  SYN3D_BACKEND_PREPLACE_OPT="$(PREPLACE_OPT)" \
	  $(TIME_CMD) bash "$(SYN3D_BACKEND_HANDOFF_DIR)/cds/run_backend_handoff.sh")

3dsyn-ord-backend-handoff: prep-libs
	@printf '[3D-Syn][ORD] Backend handoff\n'
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/4_3dsyn_handoff.log,cd "$(OBJECTS_DIR)" && /usr/bin/env \
	  DESIGN_NAME="$(DESIGN_NAME)" \
	  SDC_FILE="$(DESIGN_SDC)" \
	  SYN3D_BACKEND_PREPLACE_OPT="$(PREPLACE_OPT)" \
	  $(TIME_CMD) bash "$(SYN3D_BACKEND_HANDOFF_DIR)/ord/run_backend_handoff.sh")

# This target is evaluated together with the original TaiWei Makefile. It
# reuses TaiWei's library setup and log wrapper while selecting repository-local
# Genus logic that reads the preserved Yosys netlist rather than RTL.
.PHONY: 3dsyn-baseline-genus
3dsyn-baseline-genus:
	@$(call _mkstdirs)
	@if [[ ! -s "$(RESULTS_DIR)/1_synth_yosys.v" ]]; then \
	  printf '[3D-Syn][baseline] Missing Yosys netlist: %s\n' \
	    "$(RESULTS_DIR)/1_synth_yosys.v" >&2; \
	  exit 2; \
	fi
	$(call _run_with_tmp_log,$(LOG_DIR)/1_genus_yosys.log,cd "$(OBJECTS_DIR)" && /usr/bin/env \
	  GENUS_INPUT_NETLIST="$(RESULTS_DIR)/1_synth_yosys.v" \
	  GENUS_OUTPUT_NETLIST="$(RESULTS_DIR)/1_synth_netlist.v" \
	  GENUS_OUTPUT_SDC="$(RESULTS_DIR)/1_synth_netlist.sdc" \
	  GENUS_LIB_FILES="$(strip $(LIB_FILES))" \
	  SYNTH_OPT_MODE="$(SYNTH_OPT_MODE)" \
	  $(TIME_CMD) $(GENUS_EXE) -overwrite \
	    -log $(LOG_DIR)/cadence_1_genus_yosys.log \
	    -f $(SYN3D_BASELINE_GENUS_TCL))

# Shared backend overrides are loaded after the TaiWei Makefile and therefore
# reuse its tool commands, platform variables, log wrapper, and stage outputs.
.PHONY: 3dsyn-ord-preplace 3dsyn-cds-preplace
.PHONY: 3dsyn-ord-3d-floorplan 3dsyn-cds-3d-floorplan
.PHONY: 3dsyn-cds-normalize-tier-handoff

3dsyn-cds-normalize-tier-handoff:
	@$(call _mkstdirs)
	@test -n "$(SYN3D_NORMALIZE_TIER)" || { \
	  printf '[3D-Syn][CDS] Missing SYN3D_NORMALIZE_TIER\n' >&2; exit 2; }
	@test -s "$(SYN3D_NORMALIZE_DEF_IN)" || { \
	  printf '[3D-Syn][CDS] Missing normalization DEF: %s\n' "$(SYN3D_NORMALIZE_DEF_IN)" >&2; exit 2; }
	@test -s "$(SYN3D_NORMALIZE_V_IN)" || { \
	  printf '[3D-Syn][CDS] Missing normalization netlist: %s\n' "$(SYN3D_NORMALIZE_V_IN)" >&2; exit 2; }
	@test -s "$(SYN3D_NORMALIZE_SDC_IN)" || { \
	  printf '[3D-Syn][CDS] Missing normalization SDC: %s\n' "$(SYN3D_NORMALIZE_SDC_IN)" >&2; exit 2; }
	@test -n "$(SYN3D_NORMALIZE_DEF_OUT)" || { \
	  printf '[3D-Syn][CDS] Missing SYN3D_NORMALIZE_DEF_OUT\n' >&2; exit 2; }
	@test -n "$(SYN3D_NORMALIZE_AUDIT_DEF)" || { \
	  printf '[3D-Syn][CDS] Missing SYN3D_NORMALIZE_AUDIT_DEF\n' >&2; exit 2; }
ifeq ($(strip $(UPPER_SITE)),$(strip $(BOTTOM_SITE)))
	@printf '[3D-Syn][CDS] ROW normalization skipped: homogeneous site %s\n' \
	  "$(UPPER_SITE)"
else
	@test "$$(realpath -m -- "$(SYN3D_NORMALIZE_DEF_IN)")" != \
	  "$$(realpath -m -- "$(SYN3D_NORMALIZE_DEF_OUT)")" || { \
	  printf '[3D-Syn][CDS] Normalization output must differ from input: %s\n' \
	    "$(SYN3D_NORMALIZE_DEF_IN)" >&2; exit 2; }
	@mkdir -p -- "$(dir $(SYN3D_NORMALIZE_AUDIT_DEF))"
	@cp -p -- "$(SYN3D_NORMALIZE_DEF_IN)" "$(SYN3D_NORMALIZE_AUDIT_DEF)"
	@rm -f -- "$(SYN3D_NORMALIZE_DEF_OUT)"
	$(call _run_with_tmp_log,$(LOG_DIR)/$(SYN3D_NORMALIZE_LOG_NAME).log,/usr/bin/env \
	  SYN3D_NORMALIZE_TIER="$(SYN3D_NORMALIZE_TIER)" \
	  SYN3D_NORMALIZE_DEF_IN="$(SYN3D_NORMALIZE_DEF_IN)" \
	  SYN3D_NORMALIZE_V_IN="$(SYN3D_NORMALIZE_V_IN)" \
	  SYN3D_NORMALIZE_SDC_IN="$(SYN3D_NORMALIZE_SDC_IN)" \
	  SYN3D_NORMALIZE_DEF_OUT="$(SYN3D_NORMALIZE_DEF_OUT)" \
	  LEF_FILES="$(if $(filter upper,$(SYN3D_NORMALIZE_TIER)),$(LEF_FILES_BOTTOM_COVER),$(if $(filter bottom,$(SYN3D_NORMALIZE_TIER)),$(LEF_FILES_UPPER_COVER),$(if $(filter owner,$(SYN3D_NORMALIZE_TIER)),$(LEF_FILES_CTS_OWNER),$(LEF_FILES_CTS_RECEIVE))))" \
	  $(TIME_CMD) $(INNOVUS_CMD) -overwrite \
	    -log $(LOG_DIR)/cadence_$(SYN3D_NORMALIZE_LOG_NAME).log \
	    -files $(SYN3D_CDS_NORMALIZE_TIER_ROWS_TCL))
	@test -s "$(SYN3D_NORMALIZE_DEF_OUT)" || { \
	  printf '[3D-Syn][CDS] Normalizer produced no DEF: %s\n' "$(SYN3D_NORMALIZE_DEF_OUT)" >&2; exit 2; }
	@mv -f -- "$(SYN3D_NORMALIZE_DEF_OUT)" "$(SYN3D_NORMALIZE_DEF_IN)"
	@printf '[3D-Syn][CDS] Published normalized ROW handoff: tier=%s def=%s\n' \
	  "$(SYN3D_NORMALIZE_TIER)" "$(SYN3D_NORMALIZE_DEF_IN)"
endif

3dsyn-ord-preplace:
	@printf '[3D-Syn][ORD] Pre-place with PREPLACE_OPT=%s\n' "$(PREPLACE_OPT)"
	@$(call _mkstdirs)
	$(call _or,$(SYN3D_ORD_PREPLACE_TCL),$(LOG_DIR)/2_1_floorplan.log)
	$(call _or,$(OPENROAD_SCRIPTS_DIR)/io_placement_random.tcl,$(LOG_DIR)/2_2_floorplan_io.log)

3dsyn-cds-preplace:
	@printf '[3D-Syn][CDS] Pre-place with PREPLACE_OPT=%s\n' "$(PREPLACE_OPT)"
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/2_innovus_preplace.log, \
	  $(TIME_CMD) $(INNOVUS_CMD) -overwrite \
	    -log $(LOG_DIR)/cadence_2_innovus_preplace.log \
	    -files $(SYN3D_CDS_PREPLACE_TCL))

3dsyn-ord-3d-floorplan:
	@printf '[3D-Syn][ORD] 3D floorplan with FIXED_CORE_AREA=%s\n' "$(FIXED_CORE_AREA)"
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/2_3_floorplan_3d.log, \
	  LEF_FILES="$(LEF_FILES_SPLIT)" $(TIME_CMD) $(OPENROAD_CMD) \
	    $(SYN3D_ORD_FLOORPLAN_3D_TCL))

3dsyn-cds-3d-floorplan:
	@printf '[3D-Syn][CDS] 3D floorplan with FIXED_CORE_AREA=%s\n' "$(FIXED_CORE_AREA)"
	@$(call _mkstdirs)
	$(call _run_with_tmp_log,$(LOG_DIR)/2_3_floorplan_3d.log, \
	  $(TIME_CMD) $(INNOVUS_CMD) -overwrite \
	    -log $(LOG_DIR)/cadence_2_3_floorplan_3d.log \
	    -files $(SYN3D_CDS_FLOORPLAN_3D_TCL))
