# Build the TaiWei OpenROAD 3D floorplan with an optional fixed core area.

source $::env(OPENROAD_SCRIPTS_DIR)/load.tcl
source $::env(OPENROAD_SCRIPTS_DIR)/handoff_manager.tcl

set LOG_DIR       [_get LOG_DIR]
set RESULTS_DIR   [_get RESULTS_DIR]
set REPORTS_DIR   [_get REPORTS_DIR]
set OBJECTS_DIR   [_get OBJECTS_DIR]

set stage_name "floorplan-3d"
set stage_paths [handoff_stage_paths $stage_name $RESULTS_DIR $OBJECTS_DIR $LOG_DIR]
handoff_bind_stage_io $stage_paths

handoff_log_paths $stage_paths
load_design $V_IN $SDC_IN "Starting 3D floorplan"
source $::env(OPENROAD_SCRIPTS_DIR)/placement_utils.tcl
source $::env(OPENROAD_SCRIPTS_DIR)/floorplan_utils.tcl

set core_util_pct 60
if {[info exists ::env(CORE_UTILIZATION)] && $::env(CORE_UTILIZATION) ne ""} {
  set core_util_pct $::env(CORE_UTILIZATION)
}
set U_target [expr {double($core_util_pct) / 100.0}]
if {$U_target <= 0.0 || $U_target > 1.0} {
  utl::error FP 100 "CORE_UTILIZATION must be in (0,100]. got $core_util_pct"
}

set aspect_ratio 1.0
if {[info exists ::env(CORE_ASPECT_RATIO)] && $::env(CORE_ASPECT_RATIO) ne ""} {
  set aspect_ratio [expr {double($::env(CORE_ASPECT_RATIO))}]
} elseif {[info exists ::env(ASPECT_RATIO)] && $::env(ASPECT_RATIO) ne ""} {
  set aspect_ratio [expr {double($::env(ASPECT_RATIO))}]
}
if {$aspect_ratio <= 0.0} {
  utl::error FP 101 "CORE_ASPECT_RATIO must be > 0. got $aspect_ratio"
}

set fixed_core_area ""
if {[info exists ::env(FIXED_CORE_AREA)] && $::env(FIXED_CORE_AREA) ne ""} {
  if {![regexp {^[+]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$} $::env(FIXED_CORE_AREA)]} {
    utl::error FP 103 "FIXED_CORE_AREA must be a positive number. got '$::env(FIXED_CORE_AREA)'"
  }
  set fixed_core_area [expr {double($::env(FIXED_CORE_AREA))}]
  if {$fixed_core_area <= 0.0 || $fixed_core_area >= Inf} {
    utl::error FP 103 "FIXED_CORE_AREA must be a positive number. got '$::env(FIXED_CORE_AREA)'"
  }
}

set mL 0.0
set mR 0.0
set mT 0.0
set mB 0.0
if {[info exists ::env(CORE_MARGIN)] && $::env(CORE_MARGIN) ne ""} {
  set margin_tokens [split $::env(CORE_MARGIN)]
  if {[llength $margin_tokens] == 1} {
    set mL [expr {double([lindex $margin_tokens 0])}]
    set mB $mL
    set mR $mL
    set mT $mL
  } elseif {[llength $margin_tokens] == 4} {
    set mL [expr {double([lindex $margin_tokens 0])}]
    set mB [expr {double([lindex $margin_tokens 1])}]
    set mR [expr {double([lindex $margin_tokens 2])}]
    set mT [expr {double([lindex $margin_tokens 3])}]
  } else {
    utl::error FP 102 "CORE_MARGIN must be 'x' or 'L B R T'. got: $::env(CORE_MARGIN)"
  }
}

set block [ord::get_db_block]
set track_grids [::odb::dbBlock_getTrackGrids $block]
puts "TrackGrids = [llength $track_grids]"
foreach track_grid $track_grids {
  ::odb::dbTrackGrid_destroy $track_grid
}

lassign [get_tier_areas_um2] A_up A_bot C_up C_bot method
set A_max [expr {($A_up > $A_bot) ? $A_up : $A_bot}]

puts "INFO: Tier areas method=$method"
puts [format "INFO: upper:  inst=%d area=%.6f um^2" $C_up $A_up]
puts [format "INFO: bottom: inst=%d area=%.6f um^2" $C_bot $A_bot]
puts [format "INFO: max-tier area=%.6f um^2, target_util=%.3f, aspect_ratio(H/W)=%.3f" \
  $A_max $U_target $aspect_ratio]

set use_explicit_box 0
if {$fixed_core_area ne ""} {
  set core_area_um2 $fixed_core_area
  set sizing_mode fixed
  set use_explicit_box 1
} elseif {$A_max > 0.0} {
  set core_area_um2 [expr {$A_max / $U_target}]
  set sizing_mode automatic
  set use_explicit_box 1
} else {
  set sizing_mode utilization_fallback
}

if {$use_explicit_box} {
  set CORE_W [expr {sqrt($core_area_um2 / double($aspect_ratio))}]
  set CORE_H [expr {$CORE_W * double($aspect_ratio)}]
  set requested_core_w $CORE_W
  set requested_core_h $CORE_H
  set requested_core_area [expr {$CORE_W * $CORE_H}]

  puts [format "INFO: 3D floorplan sizing mode=%s requested_area=%.6f um^2" \
    $sizing_mode $core_area_um2]
  puts [format "INFO: Requested core W/H/area = %.6f / %.6f / %.6f" \
    $requested_core_w $requested_core_h $requested_core_area]
  puts [format "INFO: Margins L/B/R/T = %.6f %.6f %.6f %.6f" $mL $mB $mR $mT]

  set die_lx 0.0
  set die_ly 0.0
  set die_ux [expr {$CORE_W + $mL + $mR}]
  set die_uy [expr {$CORE_H + $mB + $mT}]
  set core_lx $mL
  set core_ly $mB
  set core_ux [expr {$mL + $CORE_W}]
  set core_uy [expr {$mB + $CORE_H}]

  puts [format "INFO: DIE_AREA  = {%.6f %.6f %.6f %.6f}" $die_lx $die_ly $die_ux $die_uy]
  puts [format "INFO: CORE_AREA = {%.6f %.6f %.6f %.6f}" $core_lx $core_ly $core_ux $core_uy]

  set site_opt {}
  if {[info exists ::env(PLACE_SITE)] && $::env(PLACE_SITE) ne ""} {
    set site_opt [list -site $::env(PLACE_SITE)]
  }
  initialize_floorplan \
    -die_area [list $die_lx $die_ly $die_ux $die_uy] \
    -core_area [list $core_lx $core_ly $core_ux $core_uy] \
    {*}$site_opt
} else {
  puts "WARN: A_max <= 0 and FIXED_CORE_AREA is unset; using utilization-based initialization."
  set site_opt {}
  if {[info exists ::env(PLACE_SITE)] && $::env(PLACE_SITE) ne ""} {
    set site_opt [list -site $::env(PLACE_SITE)]
  }
  initialize_floorplan -utilization $core_util_pct \
                       -aspect_ratio $aspect_ratio \
                       -core_space [list $mB $mT $mL $mR] \
                       {*}$site_opt
  puts "INFO: 3D floorplan sizing mode=$sizing_mode"
  set requested_core_w ""
  set requested_core_h ""
  set requested_core_area ""
}

# Report the grid-snapped core box rather than treating the requested box as
# final. OpenROAD may adjust both the origin and dimensions to legal rows/sites.
set core_rect [$block getCoreArea]
set dbu [_get_dbu]
if {$core_rect eq "NULL" || $dbu <= 0} {
  utl::error FP 104 "Unable to query the finalized OpenROAD core box."
}
set final_core_w [expr {double([$core_rect xMax] - [$core_rect xMin]) / double($dbu)}]
set final_core_h [expr {double([$core_rect yMax] - [$core_rect yMin]) / double($dbu)}]
set final_core_area [expr {$final_core_w * $final_core_h}]
set final_aspect_ratio [expr {$final_core_h / $final_core_w}]
puts [format "INFO: Final snapped core W/H/area/aspect = %.6f / %.6f / %.6f / %.6f" \
  $final_core_w $final_core_h $final_core_area $final_aspect_ratio]

dict set stage_paths floorplan_sizing_mode $sizing_mode
dict set stage_paths requested_core_width $requested_core_w
dict set stage_paths requested_core_height $requested_core_h
dict set stage_paths requested_core_area $requested_core_area
dict set stage_paths final_core_width $final_core_w
dict set stage_paths final_core_height $final_core_h
dict set stage_paths final_core_area $final_core_area
dict set stage_paths final_core_aspect_ratio $final_aspect_ratio

if {[info exists ::env(MAKE_TRACKS)] && $::env(MAKE_TRACKS) ne ""} {
  source $::env(MAKE_TRACKS)
  set track_grids [::odb::dbBlock_getTrackGrids $block]
  puts "New TrackGrids = [llength $track_grids]"
} else {
  make_tracks
}

set floorplan_cross_tier_report [file join $LOG_DIR "2_3_floorplan_3d.nets"]
set floorplan_cross_tier_stats [report_cross_tier_snapshot $floorplan_cross_tier_report -label "floorplan_3d"]
puts "INFO(OR): floorplan cross-tier estimate=[dict get $floorplan_cross_tier_stats cross_tier_all]"

handoff_write_stage_outputs $stage_paths \
  -copy_sdc 1 \
  -write_image 1 \
  -write_manifest 1
exit
