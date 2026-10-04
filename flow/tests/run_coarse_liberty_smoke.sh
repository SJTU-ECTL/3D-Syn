#!/usr/bin/env bash
# Validate each supported coarse-library contract with Yosys dfflibmap and ABC.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/../.." && pwd)"
taiwei_home="${TAIWEI_HOME:-${repo_root}/../TaiWei}"
yosys="${YOSYS_EXE:-${taiwei_home}/ORFS-Research/tools/install/yosys/bin/yosys}"
overlay="${repo_root}/taiwei/3dsyn.mk"
fixture="${script_dir}/coarse_liberty_smoke.v"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/3dsyn-coarse-liberty-smoke.XXXXXX")"
trap 'rm -rf -- "${work_dir}"' EXIT

[[ -x "${yosys}" ]] || { echo "Yosys is unavailable: ${yosys}" >&2; exit 1; }
[[ -f "${overlay}" ]] || { echo "3D-Syn overlay is unavailable: ${overlay}" >&2; exit 1; }
[[ -f "${fixture}" ]] || { echo "Smoke fixture is unavailable: ${fixture}" >&2; exit 1; }

resolve_coarse_variables() {
  local platform="$1"
  make --no-print-directory -s -f "${overlay}" \
    "SYN3D_ROOT=${repo_root}" \
    "TAIWEI_HOME=${taiwei_home}" \
    "SYN3D_PLATFORM=${platform}" \
    --eval='.PHONY: print-coarse-smoke-variables' \
    --eval='print-coarse-smoke-variables: ; @printf "%s\n" "$(SYN3D_COARSE_LIB_FILES)" "$(SYN3D_COARSE_DFF_LIB_FILE)" "$(SYN3D_COARSE_DONT_USE_CELLS)" "$(SYN3D_COARSE_ABC_DRIVER_CELL)" "$(SYN3D_COARSE_ABC_LOAD_IN_FF)" "$(SYN3D_COARSE_MIN_BUF_CELL_AND_PORTS)"' \
    print-coarse-smoke-variables
}

for platform in asap7_3D nangate45_3D asap7_nangate45_3D; do
  mapfile -t resolved < <(resolve_coarse_variables "${platform}")
  ((${#resolved[@]} == 6)) || {
    echo "Unexpected coarse-variable output for ${platform}" >&2
    exit 1
  }

  read -r -a liberty_files <<< "${resolved[0]}"
  dff_liberty="${resolved[1]}"
  read -r -a dont_use_cells <<< "${resolved[2]}"
  abc_driver_cell="${resolved[3]}"
  abc_load_in_ff="${resolved[4]}"
  read -r -a min_buf_cell_and_ports <<< "${resolved[5]}"
  ((${#liberty_files[@]} > 0)) || {
    echo "No coarse Liberty resolved for ${platform}" >&2
    exit 1
  }

  lib_args=""
  read_liberty_commands=""
  for liberty_file in "${liberty_files[@]}"; do
    [[ -s "${liberty_file}" ]] || {
      echo "Missing or empty coarse Liberty for ${platform}: ${liberty_file}" >&2
      exit 1
    }
    lib_args+=" -liberty ${liberty_file}"
    read_liberty_commands+=" read_liberty -lib ${liberty_file};"
  done

  dff_args="${lib_args}"
  if [[ -n "${dff_liberty}" ]]; then
    [[ -s "${dff_liberty}" ]] || {
      echo "Missing or empty DFF Liberty for ${platform}: ${dff_liberty}" >&2
      exit 1
    }
    dff_args=" -liberty ${dff_liberty}"
  fi

  dont_use_args=""
  for cell in "${dont_use_cells[@]}"; do
    dont_use_args+=" -dont_use ${cell}"
  done

  ((${#min_buf_cell_and_ports[@]} == 3)) || {
    echo "Invalid minimum-buffer contract for ${platform}: ${resolved[5]}" >&2
    exit 1
  }
  abc_constr="${work_dir}/${platform}.abc.constr"
  printf 'set_driving_cell %s\nset_load %s\n' \
    "${abc_driver_cell}" "${abc_load_in_ff}" > "${abc_constr}"

  "${yosys}" -Q -q -p "
    ${read_liberty_commands}
    read_verilog ${fixture};
    hierarchy -check -top coarse_dff_smoke;
    proc;
    opt;
    techmap;
    opt;
    dfflibmap${dff_args}${dont_use_args};
    select -assert-none t:\$_DFF_*;
    check -assert
  "

  "${yosys}" -Q -q -p "
    ${read_liberty_commands}
    read_verilog ${fixture};
    hierarchy -check -top coarse_liberty_smoke;
    synth -top coarse_liberty_smoke -run :fine;
    synth -top coarse_liberty_smoke -run fine:;
    dfflibmap${dff_args}${dont_use_args};
    opt;
    setundef -zero;
    abc${lib_args}${dont_use_args} -constr ${abc_constr};
    splitnets;
    opt_clean -purge;
    insbuf -buf ${min_buf_cell_and_ports[*]};
    check -assert -mapped;
    stat${lib_args}
  "
  printf '[coarse-liberty-smoke] PASS platform=%s libs=%d dff=%s\n' \
    "${platform}" "${#liberty_files[@]}" "${dff_liberty:-all}"
done
