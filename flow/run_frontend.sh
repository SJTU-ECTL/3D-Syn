#!/usr/bin/env bash
# Internal driver for the 3D-Syn frontend through backend handoff.
#
# The release Makefile calls this file after validating the public launcher
# configuration. It is not a user-facing entry point.
#
# Usage:
#   run_frontend.sh [--stage all|coarse-synth|coarse-partition|per-tier-synth|backend-handoff]
#
# For an all-stage run, START_STAGE and STOP_STAGE may select an inclusive
# range. Reuse the same RUN_DIR and FLOW_VARIANT to resume after a failure.

set -euo pipefail

fail() {
  printf '[3D-Syn] ERROR: %s\n' "$*" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage: run_frontend.sh [--stage STAGE]

Stages:
  all               Run the inclusive START_STAGE..STOP_STAGE range.
  coarse-synth      Run coarse-unit synthesis and characterization.
  coarse-partition  Run the selected ORD/CDS coarse partition.
  per-tier-synth    Run HBT-aware per-tier synthesis.
  backend-handoff   Publish the selected backend handoff.

Required environment is normally exported by the repository root Makefile.
Important controls: PLATFORM, DESIGN, BACKEND, FLOW_VARIANT, RUN_DIR,
START_STAGE, STOP_STAGE, NUM_CORES, AREA_MODE, FIXED_UTIL, and tool paths.
EOF
}

selected_stage="all"
while (($#)); do
  case "$1" in
    --stage) selected_stage="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

required_variables=(
  SYN3D_ROOT TAIWEI_FLOW_HOME PLATFORM DESIGN BACKEND FLOW_VARIANT RUN_DIR
  DESIGN_CONFIG_2D DESIGN_SDC YOSYS_EXE YOSYS_PLUGIN OPENROAD_EXE GENUS_EXE
  INNOVUS_EXE PYTHON_EXE NUM_CORES SYNTH_OPT_MODE PREPLACE_OPT
)
for name in "${required_variables[@]}"; do
  [[ -n "${!name:-}" ]] || fail "Required variable is not set: ${name}"
done

case "${BACKEND}" in ord|cds) ;; *) fail "BACKEND must be ord or cds: ${BACKEND}" ;; esac
case "${PLATFORM}" in
  asap7_3D|nangate45_3D|asap7_nangate45_3D) ;;
  *) fail "Unsupported platform: ${PLATFORM}" ;;
esac
[[ "${DESIGN}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Invalid design: ${DESIGN}"
[[ "${FLOW_VARIANT}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Invalid FLOW_VARIANT: ${FLOW_VARIANT}"

# TaiWei's frontend Makefile is intentionally configured with the 2D design
# view.  Coarse synthesis and partition therefore operate on unsuffixed base
# cells; the 3D-Syn platform remains explicit in SYN3D_PLATFORM for the
# per-tier and backend handoff stages.
coarse_platform="${PLATFORM%_3D}"

stage_names=(coarse-synth coarse-partition per-tier-synth backend-handoff)
stage_target() {
  case "$1" in
    coarse-synth) printf '%s\n' 3dsyn-coarse-synth ;;
    coarse-partition) printf '3dsyn-%s-coarse-partition\n' "${BACKEND}" ;;
    per-tier-synth) printf '%s\n' 3dsyn-per-tier-synth ;;
    backend-handoff) printf '3dsyn-%s-backend-handoff\n' "${BACKEND}" ;;
    *) return 1 ;;
  esac
}

stage_index() {
  local wanted="$1"
  local i
  for ((i = 0; i < ${#stage_names[@]}; i++)); do
    if [[ "${stage_names[i]}" == "${wanted}" ]]; then
      printf '%s\n' "${i}"
      return 0
    fi
  done
  return 1
}

if [[ "${selected_stage}" == all ]]; then
  start_stage="${START_STAGE:-coarse-synth}"
  stop_stage="${STOP_STAGE:-backend-handoff}"
else
  stage_index "${selected_stage}" >/dev/null || fail "Unsupported stage: ${selected_stage}"
  start_stage="${selected_stage}"
  stop_stage="${selected_stage}"
fi
start_index="$(stage_index "${start_stage}")" || fail "Unsupported START_STAGE: ${start_stage}"
stop_index="$(stage_index "${stop_stage}")" || fail "Unsupported STOP_STAGE: ${stop_stage}"
((start_index <= stop_index)) || fail "START_STAGE must not follow STOP_STAGE"

case_dir="${PLATFORM}/${DESIGN}/${FLOW_VARIANT}"
log_dir="$(realpath -m "${RUN_DIR}/logs/${case_dir}")"
objects_dir="$(realpath -m "${RUN_DIR}/objects/${case_dir}")"
reports_dir="$(realpath -m "${RUN_DIR}/reports/${case_dir}")"
results_dir="$(realpath -m "${RUN_DIR}/results/${case_dir}")"
status_dir="${objects_dir}/3dsyn/stage_status"
mkdir -p "${log_dir}" "${objects_dir}" "${reports_dir}" "${results_dir}" "${status_dir}"

use_flow="cadence"
if [[ "${BACKEND}" == ord ]]; then
  use_flow="openroad"
fi

is_nonnegative_number() {
  local value="$1"
  [[ "${value}" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]]
}

hbt_model_mode="${SYN3D_HBT_MODEL_MODE:-direct}"
hbt_r_scale="${SYN3D_HBT_R_SCALE:-0}"
hbt_c_scale="${SYN3D_HBT_C_SCALE:-0.25}"
case "${hbt_model_mode}" in
  direct)
    hbt_r_ohm="${SYN3D_HBT_R_OHM:-1}"
    hbt_c_ff="${SYN3D_HBT_C_FF:-0.5}"
    is_nonnegative_number "${hbt_r_ohm}" || fail "SYN3D_HBT_R_OHM must be non-negative: ${hbt_r_ohm}"
    is_nonnegative_number "${hbt_c_ff}" || fail "SYN3D_HBT_C_FF must be non-negative: ${hbt_c_ff}"
    ;;
  scale)
    hbt_r_ohm=""
    hbt_c_ff=""
    is_nonnegative_number "${hbt_r_scale}" || fail "SYN3D_HBT_R_SCALE must be non-negative: ${hbt_r_scale}"
    is_nonnegative_number "${hbt_c_scale}" || fail "SYN3D_HBT_C_SCALE must be non-negative: ${hbt_c_scale}"
    ;;
  *) fail "SYN3D_HBT_MODEL_MODE must be direct or scale: ${hbt_model_mode}" ;;
esac

make_args=(
  --no-print-directory
  -C "${TAIWEI_FLOW_HOME}"
  -f "${TAIWEI_FLOW_HOME}/Makefile"
  -f "${SYN3D_ROOT}/taiwei/3dsyn.mk"
  "DESIGN_CONFIG=${DESIGN_CONFIG_2D}"
  "PLATFORM=${coarse_platform}"
  "DESIGN_SDC=${DESIGN_SDC}"
  "SDC_FILE=${DESIGN_SDC}"
  "DESIGN_NICKNAME=${DESIGN}"
  "FLOW_VARIANT=${FLOW_VARIANT}"
  "WORK_HOME=${RUN_DIR}"
  "LOG_DIR=${log_dir}"
  "OBJECTS_DIR=${objects_dir}"
  "REPORTS_DIR=${reports_dir}"
  "RESULTS_DIR=${results_dir}"
  "SYN3D_PLATFORM=${PLATFORM}"
  "SYN3D_PLATFORM_DIR=${TAIWEI_FLOW_HOME}/platforms/${PLATFORM}"
  "SYN3D_COARSE_SYNTH_DIR=${objects_dir}/3dsyn/coarse_synth"
  "SYN3D_PER_TIER_SYNTH_DIR=${objects_dir}/3dsyn/per_tier_synth"
  "SYN3D_HANDOFF_DIR=${objects_dir}/3dsyn/handoff"
  "SYN3D_COARSE_UNIT_KEEP_MODE=${SYN3D_COARSE_UNIT_KEEP_MODE:-all}"
  "SYN3D_GENUS_OPT_MODE=${SYNTH_OPT_MODE}"
  "SYN3D_HBT_MODEL_MODE=${hbt_model_mode}"
  "SYN3D_HBT_R_SCALE=${hbt_r_scale}"
  "SYN3D_HBT_C_SCALE=${hbt_c_scale}"
  "SYN3D_HBT_R_OHM=${hbt_r_ohm}"
  "SYN3D_HBT_C_FF=${hbt_c_ff}"
  "SYN3D_BACKEND_PREPLACE_OPT=${PREPLACE_OPT}"
  "CHECKPOINT_ROOT=${CHECKPOINT_ROOT:-}"
  "NUM_CORES=${NUM_CORES}"
  "USE_FLOW=${use_flow}"
  "YOSYS_EXE=${YOSYS_EXE}"
  "YOSYS_FLAGS=-m ${YOSYS_PLUGIN} -v 3"
  "OPENROAD_EXE=${OPENROAD_EXE}"
  "GENUS_EXE=${GENUS_EXE}"
  "INNOVUS_EXE=${INNOVUS_EXE}"
  "PYTHON_EXE=${PYTHON_EXE}"
  "PREPLACE_OPT=${PREPLACE_OPT}"
)

# A non-empty public override wins. Otherwise the inner TaiWei make loads
# FIXED_UTIL from the selected case's config2d.mk and 3dsyn.mk maps it to
# SYN3D_FIXED_UTIL. Never pass an empty command-line assignment: GNU Make
# command-line variables would mask the case configuration.
if [[ "${AREA_MODE:-gnn}" == fixed && -n "${FIXED_UTIL:-}" ]]; then
  make_args+=("SYN3D_FIXED_UTIL=${FIXED_UTIL}")
fi

write_state() {
  local stage="$1"
  local state="$2"
  local rc="$3"
  local state_path="${status_dir}/${stage}.env"
  local tmp_path="${state_path}.$$.tmp"
  {
    printf 'stage=%s\n' "${stage}"
    printf 'state=%s\n' "${state}"
    printf 'rc=%s\n' "${rc}"
    printf 'platform=%s\n' "${PLATFORM}"
    printf 'design=%s\n' "${DESIGN}"
    printf 'backend=%s\n' "${BACKEND}"
    printf 'flow_variant=%s\n' "${FLOW_VARIANT}"
    printf 'updated_at=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
  } > "${tmp_path}"
  mv -f -- "${tmp_path}" "${state_path}"
}

run_stage() {
  local stage="$1"
  local target
  local rc
  local -a stage_make_args
  target="$(stage_target "${stage}")"
  printf '[3D-Syn] Running %s (%s)\n' "${stage}" "${target}"
  write_state "${stage}" running 0
  stage_make_args=("${make_args[@]}")
  if [[ "${stage}" == backend-handoff ]]; then
    # Backend scripts must load the tier-qualified 3D LEF/Lib view.  The
    # frontend uses the 2D view for coarse synthesis, so override only this
    # stage's inherited Make variables and keep the original source tree
    # untouched.
    stage_make_args+=(
      "PLATFORM=${PLATFORM}"
      "DESIGN_CONFIG=${SYN3D_ROOT}/configs/${PLATFORM}/${DESIGN}/config.mk"
    )
  fi
  set +e
  make "${stage_make_args[@]}" "${target}"
  rc=$?
  set -e
  if ((rc != 0)); then
    write_state "${stage}" failed "${rc}"
    return "${rc}"
  fi
  write_state "${stage}" ok 0
}

printf '[3D-Syn] platform=%s design=%s backend=%s variant=%s\n' \
  "${PLATFORM}" "${DESIGN}" "${BACKEND}" "${FLOW_VARIANT}"
printf '[3D-Syn] frontend stage range: %s -> %s\n' "${start_stage}" "${stop_stage}"
for ((i = start_index; i <= stop_index; i++)); do
  run_stage "${stage_names[i]}"
done

printf '[3D-Syn] Frontend completed through %s: %s\n' "${stop_stage}" "${results_dir}"
