#!/usr/bin/env bash
set -euo pipefail

plugin=${1:?usage: run_smoke.sh PLUGIN OUTPUT_DIR}
output_dir=${2:?usage: run_smoke.sh PLUGIN OUTPUT_DIR}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
yosys=${YOSYS_EXE:?YOSYS_EXE must name the TaiWei Yosys executable}

mkdir -p "$output_dir/coarse_units"
"$yosys" -Q -p "
  plugin -i $plugin;
  read_verilog $script_dir/smoke.v;
  hierarchy -top top;
  proc;
  opt;
  syn3d_mark_coarse_units -keep arithmetic -output-dir $output_dir/coarse_units;
  syn3d_normalize_instance_names;
  write_rtlil $output_dir/design.rtlil
" >"$output_dir/yosys.log" 2>&1

test -s "$output_dir/coarse_units/coarse_units.list"
test -s "$output_dir/design.rtlil"
grep -q '_add_' "$output_dir/coarse_units/coarse_units.list"
grep -q 'cell \\child \\odd_name' "$output_dir/design.rtlil"
