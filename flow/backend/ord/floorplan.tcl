# Apply the TaiWei OpenROAD floorplan stage with configurable pre-place optimization.

utl::set_metrics_stage "floorplan__{}"
source $::env(OPENROAD_SCRIPTS_DIR)/load.tcl
load_design 1_synth.v 1_synth.sdc "Starting floorplan"

set enable_opt 0
if {[info exists ::env(PREPLACE_OPT)] && $::env(PREPLACE_OPT) ne ""} {
  set enable_opt $::env(PREPLACE_OPT)
}
if {$enable_opt ne "0" && $enable_opt ne "1"} {
  error "PREPLACE_OPT must be 0 or 1: $enable_opt"
}
puts "INFO: PREPLACE_OPT = $enable_opt"

puts "\n=========================================================================="
puts "Floorplan check_setup"
puts "--------------------------------------------------------------------------"
check_setup

set num_instances [llength [get_cells -hier *]]
puts "number instances in verilog is $num_instances"

if {[info exists ::env(FLOORPLAN_DEF)]} {
  puts "Read in Floorplan DEF to initialize floorplan: $::env(FLOORPLAN_DEF)"
  read_def -floorplan_initialize $::env(FLOORPLAN_DEF)
} elseif {[info exists ::env(CORE_UTILIZATION)] && $::env(CORE_UTILIZATION) ne ""} {
  set aspect_ratio 1.0
  if {[info exists ::env(CORE_ASPECT_RATIO)] && $::env(CORE_ASPECT_RATIO) ne ""} {
    set aspect_ratio $::env(CORE_ASPECT_RATIO)
  }
  set core_margin 1.0
  if {[info exists ::env(CORE_MARGIN)] && $::env(CORE_MARGIN) ne ""} {
    set core_margin $::env(CORE_MARGIN)
  }
  initialize_floorplan -utilization $::env(CORE_UTILIZATION) \
                       -aspect_ratio $aspect_ratio \
                       -core_space $core_margin \
                       -site $::env(PLACE_SITE)
} else {
  initialize_floorplan -die_area $::env(DIE_AREA) \
                       -core_area $::env(CORE_AREA) \
                       -site $::env(PLACE_SITE)
}

if {[info exists ::env(MAKE_TRACKS)]} {
  source $::env(MAKE_TRACKS)
} elseif {[file exists $::env(PLATFORM_DIR)/make_tracks.tcl]} {
  source $::env(PLATFORM_DIR)/make_tracks.tcl
} else {
  make_tracks
}

# These operations can change the instance/master set and are therefore
# enabled only when partition reconciliation has been requested explicitly.
if {$enable_opt eq "1"} {
  remove_buffers
}

if {$enable_opt eq "1" && [info exists ::env(RESYNTH_TIMING_RECOVER)] && $::env(RESYNTH_TIMING_RECOVER) == 1} {
  repair_design
  repair_timing
  puts "Post synth-opt area"
  report_design_area
  report_worst_slack -min -digits 3
  puts "Post synth-opt wns"
  report_worst_slack -max -digits 3
  puts "Post synth-opt tns"
  report_tns -digits 3

  if {![info exists save_checkpoint] || $save_checkpoint} {
    write_verilog $::env(RESULTS_DIR)/2_pre_abc_timing.v
  }

  restructure -target timing -liberty_file $::env(DONT_USE_SC_LIB) \
              -work_dir $::env(RESULTS_DIR)

  if {![info exists save_checkpoint] || $save_checkpoint} {
    write_verilog $::env(RESULTS_DIR)/2_post_abc_timing.v
  }

  remove_buffers
  repair_design
  repair_timing
  puts "Post restructure-opt wns"
  report_worst_slack -max -digits 3
  puts "Post restructure-opt tns"
  report_tns -digits 3
  remove_buffers
}

puts "Default units for flow"
report_units
report_units_metric

if {$enable_opt eq "1" && [info exists ::env(RESYNTH_AREA_RECOVER)] && $::env(RESYNTH_AREA_RECOVER) == 1} {
  utl::push_metrics_stage "floorplan__{}__pre_restruct"
  set num_instances [llength [get_cells -hier *]]
  puts "number instances before restructure is $num_instances"
  puts "Design Area before restructure"
  report_design_area
  report_design_area_metrics
  utl::pop_metrics_stage

  if {![info exists save_checkpoint] || $save_checkpoint} {
    write_verilog $::env(RESULTS_DIR)/2_pre_abc.v
  }

  set tielo_cell_name [lindex $::env(TIELO_CELL_AND_PORT) 0]
  set tielo_lib_name [get_name [get_property [lindex [get_lib_cell $tielo_cell_name] 0] library]]
  set tielo_port $tielo_lib_name/$tielo_cell_name/[lindex $::env(TIELO_CELL_AND_PORT) 1]

  set tiehi_cell_name [lindex $::env(TIEHI_CELL_AND_PORT) 0]
  set tiehi_lib_name [get_name [get_property [lindex [get_lib_cell $tiehi_cell_name] 0] library]]
  set tiehi_port $tiehi_lib_name/$tiehi_cell_name/[lindex $::env(TIEHI_CELL_AND_PORT) 1]

  restructure -liberty_file $::env(DONT_USE_SC_LIB) -target area \
              -tiehi_port $tiehi_port \
              -tielo_port $tielo_port \
              -work_dir $::env(RESULTS_DIR)
  remove_buffers

  if {![info exists save_checkpoint] || $save_checkpoint} {
    write_verilog $::env(RESULTS_DIR)/2_post_abc.v
  }
  utl::push_metrics_stage "floorplan__{}__post_restruct"
  set num_instances [llength [get_cells -hier *]]
  puts "number instances after restructure is $num_instances"
  puts "Design Area after restructure"
  report_design_area
  report_design_area_metrics
  utl::pop_metrics_stage
}

if {$enable_opt eq "1" && [info exists ::env(POST_FLOORPLAN_TCL)]} {
  source $::env(POST_FLOORPLAN_TCL)
}

write_def $::env(RESULTS_DIR)/2_1_floorplan.def
write_db $::env(RESULTS_DIR)/2_1_floorplan.odb
write_sdc $::env(RESULTS_DIR)/2_floorplan.sdc

exit
