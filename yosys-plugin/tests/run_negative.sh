#!/usr/bin/env bash
set -euo pipefail

plugin=${1:?usage: run_negative.sh PLUGIN OUTPUT_DIR}
output_dir=${2:?usage: run_negative.sh PLUGIN OUTPUT_DIR}
yosys=${YOSYS_EXE:?YOSYS_EXE must name the TaiWei Yosys executable}
mkdir -p "$output_dir"

expect_failure() {
  local name=$1
  local command=$2
  if "$yosys" -Q -p "plugin -i $plugin; $command" >"$output_dir/$name.log" 2>&1; then
    printf 'Expected command to fail: %s\n' "$command" >&2
    return 1
  fi
}

expect_failure missing_output_dir "syn3d_mark_coarse_units"
expect_failure invalid_keep "syn3d_mark_coarse_units -keep invalid -output-dir $output_dir"

if env -u SYN3D_HANDOFF_DIR -u RESULTS_DIR \
  "$yosys" -Q -p "plugin -i $plugin; syn3d_write_coarse_unit_libs" \
  >"$output_dir/missing_handoff_env.log" 2>&1; then
  printf 'Expected syn3d_write_coarse_unit_libs to reject missing environment\n' >&2
  exit 1
fi

grep -q 'Option -output-dir is required' "$output_dir/missing_output_dir.log"
grep -q "Unsupported -keep mode 'invalid'" "$output_dir/invalid_keep.log"
grep -q 'SYN3D_HANDOFF_DIR is not set' "$output_dir/missing_handoff_env.log"
