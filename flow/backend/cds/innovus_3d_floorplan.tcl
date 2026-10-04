# This script was written and developed by Zhiyu Zheng at Fudan University; however, the underlying
# commands and reports are copyrighted by Cadence. We thank Cadence for
# granting permission to share our research to help promote and foster the next
# generation of innovators.
#
# Build the TaiWei Innovus 3D floorplan with an optional fixed core area.

source $::env(CADENCE_SCRIPTS_DIR)/utils.tcl
source $::env(CADENCE_SCRIPTS_DIR)/lib_setup.tcl
source $::env(CADENCE_SCRIPTS_DIR)/design_setup.tcl
source $::env(CADENCE_SCRIPTS_DIR)/handoff_manager.tcl

set LOG_DIR       [_get LOG_DIR]
set RESULTS_DIR   [_get RESULTS_DIR]
set REPORTS_DIR   [_get REPORTS_DIR]
set OBJECTS_DIR   [_get OBJECTS_DIR]

set stage_name "floorplan-3d"
set stage_paths [handoff_stage_paths $stage_name $RESULTS_DIR $OBJECTS_DIR $LOG_DIR]
handoff_bind_stage_io $stage_paths
set sdc $SDC_IN

source $::env(CADENCE_SCRIPTS_DIR)/mmmc_setup.tcl
handoff_log_paths $stage_paths
handoff_init_design_from_paths $stage_paths -require_def 0

set CORE_UTIL       [_get CORE_UTILIZATION 60]
set ASPECT_RATIO    [_get CORE_ASPECT_RATIO 1.0]
set CORE_MARGIN     [_get CORE_MARGIN 0.2]
set PLACE_SITE      [_get PLACE_SITE ""]
set CREATE_OBS_STAGE [_get CREATE_OBS_STAGE ""]
set FIXED_CORE_AREA [_get FIXED_CORE_AREA ""]

if {[expr {double($ASPECT_RATIO)}] <= 0.0} {
  error "CORE_ASPECT_RATIO must be > 0. got '$ASPECT_RATIO'"
}

set U_target [expr {double($CORE_UTIL) / 100.0}]
set mL $CORE_MARGIN
set mR $CORE_MARGIN
set mT $CORE_MARGIN
set mB $CORE_MARGIN

source $::env(CADENCE_SCRIPTS_DIR)/extract_report.tcl
set cross_tier_net_estimate [extract_cross_tier_nets [file join $LOG_DIR "2_3_floorplan_3d.nets"]]
puts "INFO: Cross-tier net estimate at floorplan stage = $cross_tier_net_estimate"

source $::env(CADENCE_SCRIPTS_DIR)/floorplan_utils.tcl
lassign [tier::core_wh_for_max_tier_util $U_target $ASPECT_RATIO] AUTO_CORE_W AUTO_CORE_H A_up A_bot A_max

puts "INFO: Tier areas: upper=$A_up bottom=$A_bot max=$A_max"
puts "INFO: Auto core size: W=$AUTO_CORE_W H=$AUTO_CORE_H"

if {$FIXED_CORE_AREA ne ""} {
  set requested_core_area [expr {double($FIXED_CORE_AREA)}]
  if {$requested_core_area <= 0.0} {
    error "FIXED_CORE_AREA must be > 0. got '$FIXED_CORE_AREA'"
  }
  set CORE_W [expr {sqrt($requested_core_area / double($ASPECT_RATIO))}]
  set CORE_H [expr {$CORE_W * double($ASPECT_RATIO)}]
  set base_core_area $requested_core_area
  set sizing_mode fixed
  puts "INFO: Fixed core area request: area=$requested_core_area aspect_ratio=$ASPECT_RATIO W=$CORE_W H=$CORE_H"
} else {
  set CORE_W $AUTO_CORE_W
  set CORE_H $AUTO_CORE_H
  set base_core_area [expr {$CORE_W * $CORE_H}]
  set sizing_mode automatic
  puts "INFO: Automatic core area: area=$base_core_area"
}

source $::env(CADENCE_SCRIPTS_DIR)/innovus_hb_layer_obs.tcl
if {$CREATE_OBS_STAGE == "FLOORPLAN"} {
  lassign [hb_required_core_wh \
    -estimated_hbt_count $cross_tier_net_estimate \
    -aspect_ratio $ASPECT_RATIO \
    -origin_x $mL \
    -origin_y $mB] hbt_required_core_w hbt_required_core_h hbt_required_core_area
  puts "INFO: HBT-required core area = $hbt_required_core_area"
  if {$hbt_required_core_area > $base_core_area} {
    set CORE_W $hbt_required_core_w
    set CORE_H $hbt_required_core_h
    puts "INFO: HBT capacity expands core area from $base_core_area to $hbt_required_core_area"
  }
}

set planned_core_width $CORE_W
set planned_core_height $CORE_H
set planned_core_area [expr {$CORE_W * $CORE_H}]
puts "INFO: Planned floorplan sizing mode=$sizing_mode W=$planned_core_width H=$planned_core_height area=$planned_core_area"
floorPlan -s [list $CORE_W $CORE_H $mL $mB $mR $mT] -siteOnly $PLACE_SITE -adjustToSite

# Innovus snaps the requested box to the placement grid. Query and record the
# realized core dimensions after floorPlan so the manifest describes the
# physical result rather than only the request.
lassign [_mt_box4 1] final_core_lx final_core_ly final_core_ux final_core_uy
set final_core_width [expr {double($final_core_ux) - double($final_core_lx)}]
set final_core_height [expr {double($final_core_uy) - double($final_core_ly)}]
set final_core_area [expr {$final_core_width * $final_core_height}]
set final_core_aspect_ratio [expr {$final_core_height / $final_core_width}]
puts "INFO: Final snapped core W/H/area/aspect = $final_core_width $final_core_height $final_core_area $final_core_aspect_ratio"

deleteTrack
generateTracks

if {$CREATE_OBS_STAGE == "FLOORPLAN"} {
  puts "INFO: Create HBT allow window in stage FLOORPLAN"
  create_hb_layer_obs -estimated_hbt_count $cross_tier_net_estimate
}
fit
handoff_write_stage_outputs $stage_paths \
  -def_args {-floorplan} \
  -copy_sdc 0 \
  -save_design 0 \
  -write_png 1 \
  -write_manifest 1 \
  -extra_manifest [list \
    cross_tier_net_estimate $cross_tier_net_estimate \
    create_obs_stage $CREATE_OBS_STAGE \
    floorplan_sizing_mode $sizing_mode \
    requested_core_area $FIXED_CORE_AREA \
    planned_core_width $planned_core_width \
    planned_core_height $planned_core_height \
    planned_core_area $planned_core_area \
    final_core_width $final_core_width \
    final_core_height $final_core_height \
    final_core_area $final_core_area \
    final_core_aspect_ratio $final_core_aspect_ratio]

puts "INFO: 3D floorplan done."
exit
