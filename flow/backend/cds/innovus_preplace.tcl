# This script was written and developed by Zhiyu Zheng at Fudan University; however, the underlying
# commands and reports are copyrighted by Cadence. We thank Cadence for
# granting permission to share our research to help promote and foster the next
# generation of innovators.
#
# Run the TaiWei Innovus pre-place stage with configurable structural optimization.

source $::env(CADENCE_SCRIPTS_DIR)/utils.tcl
source $::env(CADENCE_SCRIPTS_DIR)/lib_setup.tcl
source $::env(CADENCE_SCRIPTS_DIR)/design_setup.tcl
source $::env(CADENCE_SCRIPTS_DIR)/handoff_manager.tcl

set LOG_DIR       [_get LOG_DIR]
set RESULTS_DIR   [_get RESULTS_DIR]
set REPORTS_DIR   [_get REPORTS_DIR]
set OBJECTS_DIR   [_get OBJECTS_DIR]

set stage_name "preplace"
set stage_paths [handoff_stage_paths $stage_name $RESULTS_DIR $OBJECTS_DIR $LOG_DIR]
handoff_bind_stage_io $stage_paths
set sdc $SDC_IN

source $::env(CADENCE_SCRIPTS_DIR)/mmmc_setup.tcl
handoff_log_paths $stage_paths

set init_verilog $V_IN
set init_design_netlisttype "Verilog"
set init_design_settop 1
set init_top_cell $DESIGN
set init_lef_file $lefs

setGenerateViaMode -auto true
init_design -setup {WC_VIEW} -hold {BC_VIEW}
set_power_analysis_mode -leakage_power_view WC_VIEW -dynamic_power_view WC_VIEW
set_interactive_constraint_modes {CON}
setAnalysisMode -reset
setAnalysisMode -analysisType onChipVariation -cppr both
setOptMode -powerEffort low -leakageToDynamicRatio 0.5
setGenerateViaMode -auto true
generateVias
createBasicPathGroups -expanded

set CORE_UTIL    [_get CORE_UTILIZATION 60]
set ASPECT_RATIO [_get CORE_ASPECT_RATIO 1.0]
set CORE_MARGIN  [_get CORE_MARGIN 0]
set util [expr {double($CORE_UTIL) / 100.0}]
set mL $CORE_MARGIN
set mR $CORE_MARGIN
set mT $CORE_MARGIN
set mB $CORE_MARGIN
floorPlan -r $ASPECT_RATIO $util $mL $mB $mR $mT
generateTracks
source $::env(CADENCE_SCRIPTS_DIR)/place_pin.tcl

set enable_opt [_get PREPLACE_OPT 0]
if {$enable_opt eq "0"} {
  puts "INFO: PREPLACE_OPT=0; using place_design -noPrePlaceOpt"
  place_design -noPrePlaceOpt
} elseif {$enable_opt eq "1"} {
  puts "INFO: PREPLACE_OPT=1; using the native TaiWei global-place step"
  source $::env(CADENCE_SCRIPTS_DIR)/place_common.tcl
  pc::setup_basic
  pc::run_global_place_step preplace
} else {
  error "PREPLACE_OPT must be 0 or 1: $enable_opt"
}

handoff_write_stage_outputs $stage_paths \
  -def_args {-floorplan} \
  -copy_sdc 0 \
  -save_design 1 \
  -write_png 1 \
  -write_manifest 1 \
  -extra_manifest [list preplace_optimization $enable_opt]

puts "INFO: PrePlace pin placement finished."
puts "INFO:   pins DEF      : [handoff_get $stage_paths def_out]"
puts "INFO:   pins Verilog  : [handoff_get $stage_paths v_out]"
exit
