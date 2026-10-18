set timing_aware_value $::env(TRITONPART_TIMING_AWARE)
if {$timing_aware_value ne "0" && $timing_aware_value ne "1"} {
  utl::error SYN3D 101 "TRITONPART_TIMING_AWARE must be 0 or 1: $timing_aware_value"
}
set ::SYN3D_TRITONPART_TIMING_AWARE [expr {$timing_aware_value eq "1"}]
set ::SYN3D_TRITONPART_TIMING_AWARE_TEXT \
  [expr {$::SYN3D_TRITONPART_TIMING_AWARE ? "true" : "false"}]

set taiwei_scripts_dir $::env(OPENROAD_SCRIPTS_DIR)
source [file join $taiwei_scripts_dir load.tcl]
source [file join $taiwei_scripts_dir util.tcl]
source [file join $taiwei_scripts_dir tier_partition_helpers.tcl]

proc append_plan_line {var_name line} {
  upvar 1 $var_name plan_text
  set line [string map [list \
    "timing_aware=true" \
    "timing_aware=$::SYN3D_TRITONPART_TIMING_AWARE_TEXT"] $line]
  append plan_text $line "\n"
}

rename write_kv_file taiwei_write_kv_file
proc write_kv_file {outfile kv_dict} {
  if {[dict exists $kv_dict timing_aware]} {
    dict set kv_dict timing_aware $::SYN3D_TRITONPART_TIMING_AWARE_TEXT
  }
  taiwei_write_kv_file $outfile $kv_dict
}

proc run_triton_part {solution_file ub seed base_balance} {
  set timing_aware $::SYN3D_TRITONPART_TIMING_AWARE_TEXT
  puts [format {INFO %s: triton_part_design ub=%.6f seed=%d timing_aware=%s base_balance=%s pin_constraints=disabled -> %s} \
    [_ts] $ub $seed $timing_aware $base_balance $solution_file]
  flush stdout

  triton_part_design \
    -num_parts 2 \
    -balance_constraint $ub \
    -base_balance $base_balance \
    -seed $seed \
    -solution_file $solution_file \
    -timing_aware_flag $timing_aware
}

source [file join $taiwei_scripts_dir tier_partition.tcl]
