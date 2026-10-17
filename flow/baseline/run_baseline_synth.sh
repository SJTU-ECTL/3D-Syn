#!/usr/bin/env bash
# Run the TaiWei comparison baseline.
#
# The driver preserves the Yosys netlist, runs Genus on that mapped netlist,
# publishes the Genus result, and continues through physical design.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
syn3d_root="$(cd -- "${script_dir}/../.." && pwd)"
view_generation_set_rc="${script_dir}/platform_overrides/asap7_nangate45_3D/setRC_6M10M.tcl"

usage() {
  cat <<'EOF'
Usage:
  run_baseline_synth.sh \
    --taiwei-home PATH \
    --platform PLATFORM_3D \
    --design DESIGN \
    --backend ord|cds \
    --run-dir PATH \
    --yosys-bin PATH \
    --openroad-bin PATH \
    --genus-bin PATH \
    --python-bin PATH \
    [options]

Required arguments:
  --taiwei-home PATH       TaiWei checkout containing TaiWei-Pin-3D.
  --platform NAME          Public 3D platform name, such as asap7_3D.
  --design NAME            Design directory and output namespace.
  --backend NAME           Physical backend: ord or cds.
  --run-dir PATH           Root for logs, objects, reports, and results.
  --yosys-bin PATH         Yosys executable used for synthesis.
  --openroad-bin PATH      OpenROAD executable used by the flow.
  --genus-bin PATH         Genus executable used on the Yosys netlist.
  --python-bin PATH        Python launcher used by TaiWei utility stages.

Optional arguments:
  --design-config PATH     2D config.mk; defaults to the repository case.
  --design-config-3d PATH  3D config.mk; defaults to the repository case.
  --design-sdc PATH        Constraint file; defaults to the repository case.
  --flow-variant NAME      Output namespace (default: baseline).
  --num-cores N            Tool thread count (default: 1).
  --synth-opt-mode MODE    normal or low_effort (default: low_effort).
  --innovus-bin PATH       Innovus executable when required by the selected backend.
  --outer-iterations N     Staged preCTS iterations (default: 1).
  --stop-stage STAGE       final, 3d-floorplan, or an exact TaiWei 3D target.
  -h, --help               Show this help text.

Outputs:
  RUN_DIR/results/PLATFORM/DESIGN/FLOW_VARIANT/
    1_synth_yosys.v        Preserved TaiWei Yosys result.
    1_synth_netlist.v      Genus result generated from the Yosys netlist.
    1_synth_netlist.sdc    Constraints written by Genus.
    1_synth.v              TaiWei physical-flow input, copied from Genus output.
    1_synth.sdc            Physical-flow copy of --design-sdc.
    2_3_floorplan_3d.v     3D floorplan netlist.
    2_3_floorplan_3d.def   3D floorplan DEF.
EOF
}

fail() {
  printf '[3D-Syn][baseline] %s\n' "$*" >&2
  exit 2
}

require_file() {
  local label="$1"
  local path="$2"
  [[ -s "${path}" ]] || fail "Missing or empty ${label}: ${path}"
}

require_executable() {
  local label="$1"
  local path="$2"
  [[ -x "${path}" ]] || fail "${label} is not executable: ${path}"
}

resolve_tool_path() {
  local path="$1"
  if [[ "${path}" = /* ]]; then
    printf '%s\n' "${path}"
  elif [[ "${path}" == */* ]]; then
    printf '%s/%s\n' "$(cd -- "$(dirname -- "${path}")" && pwd)" "$(basename -- "${path}")"
  else
    command -v -- "${path}" || fail "Tool is not available in PATH: ${path}"
  fi
}

taiwei_home=""
platform=""
design=""
backend=""
run_dir=""
yosys_bin=""
openroad_bin=""
genus_bin=""
innovus_bin=""
python_bin=""
design_config=""
design_config_3d=""
design_sdc=""
flow_variant="baseline"
num_cores="1"
synth_opt_mode="low_effort"
outer_iterations="1"
stop_stage="final"

while (($#)); do
  case "$1" in
    --taiwei-home) taiwei_home="${2:-}"; shift 2 ;;
    --platform) platform="${2:-}"; shift 2 ;;
    --design) design="${2:-}"; shift 2 ;;
    --backend) backend="${2:-}"; shift 2 ;;
    --run-dir) run_dir="${2:-}"; shift 2 ;;
    --yosys-bin) yosys_bin="${2:-}"; shift 2 ;;
    --openroad-bin) openroad_bin="${2:-}"; shift 2 ;;
    --genus-bin) genus_bin="${2:-}"; shift 2 ;;
    --innovus-bin) innovus_bin="${2:-}"; shift 2 ;;
    --python-bin) python_bin="${2:-}"; shift 2 ;;
    --design-config) design_config="${2:-}"; shift 2 ;;
    --design-config-3d) design_config_3d="${2:-}"; shift 2 ;;
    --design-sdc) design_sdc="${2:-}"; shift 2 ;;
    --flow-variant) flow_variant="${2:-}"; shift 2 ;;
    --num-cores) num_cores="${2:-}"; shift 2 ;;
    --synth-opt-mode) synth_opt_mode="${2:-}"; shift 2 ;;
    --outer-iterations) outer_iterations="${2:-}"; shift 2 ;;
    --stop-stage) stop_stage="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

[[ -n "${taiwei_home}" ]] || fail '--taiwei-home is required.'
[[ -n "${platform}" ]] || fail '--platform is required.'
[[ -n "${design}" ]] || fail '--design is required.'
[[ -n "${backend}" ]] || fail '--backend is required.'
[[ -n "${run_dir}" ]] || fail '--run-dir is required.'
[[ -n "${yosys_bin}" ]] || fail '--yosys-bin is required.'
[[ -n "${openroad_bin}" ]] || fail '--openroad-bin is required.'
[[ -n "${genus_bin}" ]] || fail '--genus-bin is required.'
[[ -n "${python_bin}" ]] || fail '--python-bin is required.'

[[ "${platform}" =~ ^[A-Za-z0-9_.-]+_3D$ ]] || fail "Invalid platform: ${platform}"
[[ "${design}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Invalid design: ${design}"
case "${backend}" in ord|cds) ;; *) fail "--backend must be ord or cds: ${backend}" ;; esac
[[ "${flow_variant}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Invalid flow variant: ${flow_variant}"
[[ "${num_cores}" =~ ^[1-9][0-9]*$ ]] || fail "--num-cores must be a positive integer: ${num_cores}"
[[ "${outer_iterations}" =~ ^[1-9][0-9]*$ ]] || fail "--outer-iterations must be a positive integer: ${outer_iterations}"
[[ "${stop_stage}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Invalid stop stage: ${stop_stage}"
case "${synth_opt_mode}" in
  normal|low_effort) ;;
  *) fail "--synth-opt-mode must be normal or low_effort: ${synth_opt_mode}" ;;
esac

synth_platform="${platform%_3D}"

taiwei_home="$(realpath -m -- "${taiwei_home}")"
taiwei_flow_home="${taiwei_home}/TaiWei-Pin-3D"
run_dir="$(realpath -m -- "${run_dir}")"
# Preserve tool-entry symlinks because some wrappers locate runtime files next
# to their invoked path.
yosys_bin="$(resolve_tool_path "${yosys_bin}")"
openroad_bin="$(resolve_tool_path "${openroad_bin}")"
genus_bin="$(resolve_tool_path "${genus_bin}")"
python_bin="$(resolve_tool_path "${python_bin}")"
if [[ "${backend}" == cds ]]; then
  [[ -n "${innovus_bin}" ]] || fail '--innovus-bin is required for backend cds.'
  innovus_bin="$(resolve_tool_path "${innovus_bin}")"
fi

if [[ -z "${design_config}" ]]; then
  design_config="${syn3d_root}/configs/${platform}/${design}/config2d.mk"
fi
if [[ -z "${design_config_3d}" ]]; then
  design_config_3d="${syn3d_root}/configs/${platform}/${design}/config.mk"
fi
if [[ -z "${design_sdc}" ]]; then
  design_sdc="${syn3d_root}/configs/${platform}/${design}/constraint.sdc"
fi
design_config="$(realpath -m -- "${design_config}")"
design_config_3d="$(realpath -m -- "${design_config_3d}")"
design_sdc="$(realpath -m -- "${design_sdc}")"

require_file 'TaiWei flow Makefile' "${taiwei_flow_home}/Makefile"
require_file '3D-Syn make overlay' "${syn3d_root}/taiwei/3dsyn.mk"
require_file '2D design configuration' "${design_config}"
require_file '3D design configuration' "${design_config_3d}"
require_file 'design constraints' "${design_sdc}"
require_file 'Genus-on-netlist script' "${script_dir}/run_genus_on_netlist.tcl"
if [[ "${backend}" == cds && "${platform}" == asap7_nangate45_3D ]]; then
  require_file '3D view RC setup' "${view_generation_set_rc}"
fi
require_executable 'Yosys' "${yosys_bin}"
require_executable 'OpenROAD' "${openroad_bin}"
require_executable 'Genus' "${genus_bin}"
require_executable 'Python launcher' "${python_bin}"
if [[ "${backend}" == cds ]]; then
  require_executable 'Innovus' "${innovus_bin}"
fi

resolved_syn3d_root="$(realpath -m -- "${syn3d_root}")"
resolved_taiwei_home="$(realpath -m -- "${taiwei_home}")"
if [[ "${run_dir}" == "${resolved_syn3d_root}" || "${run_dir}" == "${resolved_taiwei_home}" ]]; then
  fail "--run-dir must not be a source repository root: ${run_dir}"
fi

case_dir="${platform}/${design}/${flow_variant}"
log_dir="${run_dir}/logs/${case_dir}"
objects_dir="${run_dir}/objects/${case_dir}"
reports_dir="${run_dir}/reports/${case_dir}"
results_dir="${run_dir}/results/${case_dir}"

common_make_args=(
  --no-print-directory
  -C "${taiwei_flow_home}"
  -f "${taiwei_flow_home}/Makefile"
  "DESIGN_NICKNAME=${design}"
  "DESIGN_DIMENSION=3D"
  "FLOW_VARIANT=${flow_variant}"
  "WORK_HOME=${run_dir}"
  "LOG_DIR=${log_dir}"
  "OBJECTS_DIR=${objects_dir}"
  "REPORTS_DIR=${reports_dir}"
  "RESULTS_DIR=${results_dir}"
  "SDC_FILE=${design_sdc}"
  "DESIGN_SDC=${design_sdc}"
  "NUM_CORES=${num_cores}"
  "OUTER_ITERATIONS=${outer_iterations}"
  "YOSYS_EXE=${yosys_bin}"
  "OPENROAD_EXE=${openroad_bin}"
  "GENUS_EXE=${genus_bin}"
  "INNOVUS_EXE=${innovus_bin}"
  "PYTHON_EXE=${python_bin}"
  "SYNTH_OPT_MODE=${synth_opt_mode}"
)

make_2d_args=(
  "${common_make_args[@]}"
  "PLATFORM=${synth_platform}"
  "DESIGN_CONFIG=${design_config}"
)

use_flow="cadence"
if [[ "${backend}" == ord ]]; then
  use_flow="openroad"
fi
make_3d_args=(
  "${common_make_args[@]}"
  "PLATFORM=${platform}"
  "DESIGN_CONFIG=${design_config_3d}"
  "USE_FLOW=${use_flow}"
)

if [[ "${backend}" == ord ]]; then
  physical_stages=(
    ord-pre ord-3d-floorplan ord-3d-io ord-3d-split-net
    ord-place-macro-upper ord-place-macro-bottom ord-3d-pdn-only
    ord-place-init ord-place-init-upper ord-place-init-bottom staged-prects
    ord-gp2lg ord-legalize-upper ord-legalize-bottom ord-cts ord-cts-post
    ord-route ord-final
  )
  final_stage="ord-final"
else
  physical_stages=(
    cds-pre cds-3d-floorplan cds-3d-io cds-3d-split-net
    cds-place-macro-upper cds-place-macro-bottom cds-3d-pdn-only
    cds-place-init cds-place-init-upper cds-place-init-bottom staged-prects
    cds-gp2lg cds-legalize-upper cds-legalize-bottom cds-cts cds-route
    cds-restore
  )
  final_stage="cds-restore"
fi

case "${stop_stage}" in
  final) resolved_stop_stage="${final_stage}" ;;
  3d-floorplan) resolved_stop_stage="${backend}-3d-floorplan" ;;
  *) resolved_stop_stage="${stop_stage}" ;;
esac

stop_index=""
for ((i = 0; i < ${#physical_stages[@]}; i++)); do
  if [[ "${physical_stages[i]}" == "${resolved_stop_stage}" ]]; then
    stop_index="${i}"
    break
  fi
done
[[ -n "${stop_index}" ]] || fail "Unsupported stop stage for ${backend}: ${stop_stage}"

printf '[3D-Syn][baseline] Platform          : %s\n' "${platform}"
printf '[3D-Syn][baseline] Synth platform    : %s\n' "${synth_platform}"
printf '[3D-Syn][baseline] Design            : %s\n' "${design}"
printf '[3D-Syn][baseline] Backend           : %s\n' "${backend}"
printf '[3D-Syn][baseline] Constraints       : %s\n' "${design_sdc}"
printf '[3D-Syn][baseline] Synthesis effort  : %s\n' "${synth_opt_mode}"
printf '[3D-Syn][baseline] Stop stage        : %s\n' "${resolved_stop_stage}"
printf '[3D-Syn][baseline] Result directory  : %s\n' "${results_dir}"

printf '[3D-Syn][baseline] Cleaning selected run directory\n'
make "${make_3d_args[@]}" clean_all
mkdir -p -- "${log_dir}" "${objects_dir}" "${reports_dir}" "${results_dir}"

printf '[3D-Syn][baseline] Stage 1/2: TaiWei Yosys synthesis\n'
make "${make_2d_args[@]}" ord-synth
require_file 'TaiWei Yosys netlist' "${results_dir}/1_synth.v"
cp -f -- "${results_dir}/1_synth.v" "${results_dir}/1_synth_yosys.v"

printf '[3D-Syn][baseline] Stage 2/2: Genus optimization of Yosys netlist\n'
make "${make_2d_args[@]}" \
  -f "${syn3d_root}/taiwei/3dsyn.mk" \
  3dsyn-baseline-genus

require_file 'preserved Yosys netlist' "${results_dir}/1_synth_yosys.v"
require_file 'Genus netlist' "${results_dir}/1_synth_netlist.v"
require_file 'Genus constraints' "${results_dir}/1_synth_netlist.sdc"

# TaiWei physical stages consume these canonical synthesis filenames. Preserve
# the Genus-exported SDC as an audit artifact, but publish the explicitly
# selected design SDC for physical design. Genus may add library-qualified
# set_dont_use commands that are valid for the 2D library and invalid after
# cells are mapped to tier-qualified 3D libraries.
cp -f -- "${results_dir}/1_synth_netlist.v" "${results_dir}/1_synth.v"
cp -f -- "${design_sdc}" "${results_dir}/1_synth.sdc"

printf '[3D-Syn][baseline] Yosys netlist     : %s\n' "${results_dir}/1_synth_yosys.v"
printf '[3D-Syn][baseline] Genus netlist     : %s\n' "${results_dir}/1_synth_netlist.v"
printf '[3D-Syn][baseline] Genus constraints : %s\n' "${results_dir}/1_synth_netlist.sdc"
printf '[3D-Syn][baseline] Physical constraints: %s\n' "${design_sdc}"

printf '[3D-Syn][baseline] TaiWei 2D preplace\n'
make "${make_2d_args[@]}" "${backend}-preplace"
require_file '2D preplace netlist' "${results_dir}/2_2_floorplan_io.v"
require_file '2D preplace DEF' "${results_dir}/2_2_floorplan_io.def"

printf '[3D-Syn][baseline] TaiWei tier partition\n'
make "${make_2d_args[@]}" "${backend}-tier-partition"
require_file 'TaiWei tier partition' "${results_dir}/partition.txt"

run_physical_stage() {
  local stage="$1"
  local iteration
  if [[ "${stage}" == staged-prects ]]; then
    for ((iteration = 1; iteration <= outer_iterations; iteration++)); do
      printf '[3D-Syn][baseline] TaiWei staged preCTS iteration %d/%d\n' \
        "${iteration}" "${outer_iterations}"
      make "${make_3d_args[@]}" "TIER_ALLOW_NET=upper-only" "${backend}-place-upper"
      make "${make_3d_args[@]}" "TIER_ALLOW_NET=bottom-only" "${backend}-place-bottom"
    done
  else
    printf '[3D-Syn][baseline] TaiWei stage %s\n' "${stage}"
    if [[ "${backend}" == cds && "${platform}" == asap7_nangate45_3D && "${stage}" == cds-pre ]]; then
      make "${make_3d_args[@]}" \
        "SET_RC_TCL=${view_generation_set_rc}" \
        "${stage}"
    else
      make "${make_3d_args[@]}" "${stage}"
    fi
  fi
}

for ((i = 0; i <= stop_index; i++)); do
  run_physical_stage "${physical_stages[i]}"
done

if ((stop_index >= 1)); then
  require_file '3D floorplan netlist' "${results_dir}/2_3_floorplan_3d.v"
  require_file '3D floorplan DEF' "${results_dir}/2_3_floorplan_3d.def"
fi
printf '[3D-Syn][baseline] Completed through %s: %s\n' \
  "${resolved_stop_stage}" "${results_dir}"
