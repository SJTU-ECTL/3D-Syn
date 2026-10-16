# Prepare a complete DEF whose ROW site matches the next active tier before a
# fresh Innovus placement or optimization process imports the handoff.

source $::env(CADENCE_SCRIPTS_DIR)/utils.tcl
source $::env(CADENCE_SCRIPTS_DIR)/lib_setup.tcl
source $::env(CADENCE_SCRIPTS_DIR)/design_setup.tcl
source $::env(CADENCE_SCRIPTS_DIR)/handoff_manager.tcl

set requested_tier [_get SYN3D_NORMALIZE_TIER]
set normalize_def_in [_get SYN3D_NORMALIZE_DEF_IN]
set normalize_v_in [_get SYN3D_NORMALIZE_V_IN]
set normalize_sdc_in [_get SYN3D_NORMALIZE_SDC_IN]
set normalize_def_out [_get SYN3D_NORMALIZE_DEF_OUT]

proc syn3d_resolve_active_tier {requested_tier} {
  switch -- $requested_tier {
    upper - bottom {
      return $requested_tier
    }
    owner {
      if {[info exists ::env(CTS_LAYER)] && $::env(CTS_LAYER) ne ""} {
        set tier [string tolower $::env(CTS_LAYER)]
      } else {
        set tier bottom
      }
    }
    receive {
      if {[info exists ::env(CTS_LAYER)] &&
          [string tolower $::env(CTS_LAYER)] eq "upper"} {
        set tier bottom
      } else {
        set tier upper
      }
    }
    default {
      error "Unsupported SYN3D_NORMALIZE_TIER '$requested_tier'."
    }
  }
  if {$tier ni {upper bottom}} {
    error "Resolved active tier must be upper or bottom: $tier"
  }
  return $tier
}

set active_tier [syn3d_resolve_active_tier $requested_tier]
if {$active_tier eq "upper"} {
  set site_variable UPPER_SITE
} else {
  set site_variable BOTTOM_SITE
}
if {![info exists ::env($site_variable)] || $::env($site_variable) eq ""} {
  error "$site_variable is required for $active_tier-tier ROW normalization."
}
set target_site $::env($site_variable)

set stage_paths [dict create \
  stage "3dsyn-normalize-$requested_tier" \
  def_in $normalize_def_in \
  v_in $normalize_v_in \
  sdc_in $normalize_sdc_in]
set sdc $normalize_sdc_in

source $::env(CADENCE_SCRIPTS_DIR)/mmmc_setup.tcl
source $::env(CADENCE_SCRIPTS_DIR)/place_macro_util.tcl
source $::env(CADENCE_SCRIPTS_DIR)/tier_cell_policy.tcl

handoff_init_design_from_paths $stage_paths

set die_before [get_db current_design .bbox]
set instances_before [llength [get_db insts]]
set nets_before [llength [get_db nets]]

rebuild_rows_for_site $target_site $active_tier

set die_after [get_db current_design .bbox]
set instances_after [llength [get_db insts]]
set nets_after [llength [get_db nets]]
if {$die_before ne $die_after} {
  error "Die bbox changed during ROW normalization: before=$die_before after=$die_after"
}
if {$instances_before != $instances_after} {
  error "Instance count changed during ROW normalization: before=$instances_before after=$instances_after"
}
if {$nets_before != $nets_after} {
  error "Net count changed during ROW normalization: before=$nets_before after=$nets_after"
}

set row_sites {}
foreach site [dbGet top.fPlan.rows.site.name -u] {
  if {$site ne "" && $site ne "0x0"} {
    lappend row_sites $site
  }
}
set row_sites [lsort -unique $row_sites]
if {[llength $row_sites] != 1 || [lindex $row_sites 0] ne $target_site} {
  error "ROW site normalization failed: expected=$target_site actual=$row_sites"
}

defOut -netlist -floorplan -routing $normalize_def_out
if {![file exists $normalize_def_out] || [file size $normalize_def_out] == 0} {
  error "ROW normalization did not produce a non-empty DEF: $normalize_def_out"
}

puts "SYN3D_ROW_NORMALIZE requested=$requested_tier active=$active_tier site=$target_site"
puts "SYN3D_ROW_NORMALIZE die_bbox=$die_after instances=$instances_after nets=$nets_after"
puts "SYN3D_ROW_NORMALIZE input=$normalize_def_in output=$normalize_def_out"
exit
