#!/usr/bin/env bash
# Internal driver for the TaiWei physical backend from a 3D-Syn handoff.
#
# BACKEND_STOP_STAGE=3d-floorplan stops immediately after the selected ORD/CDS
# 3D floorplan. The default value `final` runs the remaining backend stages.
# BACKEND_START_STAGE can resume at `3d-floorplan` or an exact stage name
# printed by this driver while reusing the same RUN_DIR and FLOW_VARIANT.

set -euo pipefail

fail() {
  printf '[3D-Syn] ERROR: %s\n' "$*" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage: run_backend.sh [--validate-handoff-only]

The release Makefile exports the required environment. Controls:
  BACKEND=ord|cds
  BACKEND_START_STAGE=3d-floorplan|<backend target>
  BACKEND_STOP_STAGE=3d-floorplan|final|<backend target>
  OUTER_ITERATIONS=<positive integer>
  --validate-handoff-only validates the published handoff without running EDA.
EOF
}

validate_handoff_only=0
if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
  usage
  exit 0
elif [[ "${1:-}" == --validate-handoff-only && $# -eq 1 ]]; then
  validate_handoff_only=1
elif (($#)); then
  fail "Unsupported argument; expected --validate-handoff-only or no arguments."
fi

required_variables=(
  SYN3D_ROOT TAIWEI_FLOW_HOME PLATFORM DESIGN BACKEND FLOW_VARIANT RUN_DIR
  DESIGN_CONFIG_3D DESIGN_SDC OPENROAD_EXE INNOVUS_EXE PYTHON_EXE NUM_CORES
)
for name in "${required_variables[@]}"; do
  [[ -n "${!name:-}" ]] || fail "Required variable is not set: ${name}"
done
case "${BACKEND}" in ord|cds) ;; *) fail "BACKEND must be ord or cds: ${BACKEND}" ;; esac

use_flow="cadence"
if [[ "${BACKEND}" == ord ]]; then
  use_flow="openroad"
fi

case_dir="${PLATFORM}/${DESIGN}/${FLOW_VARIANT}"
log_dir="$(realpath -m "${RUN_DIR}/logs/${case_dir}")"
objects_dir="$(realpath -m "${RUN_DIR}/objects/${case_dir}")"
reports_dir="$(realpath -m "${RUN_DIR}/reports/${case_dir}")"
results_dir="$(realpath -m "${RUN_DIR}/results/${case_dir}")"
status_dir="${objects_dir}/3dsyn/backend_status"

handoff_validator="${SYN3D_ROOT}/flow/backend_handoff/validate_backend_handoff.py"
[[ -f "${handoff_validator}" ]] || fail "Backend handoff validator is unavailable: ${handoff_validator}"
"${PYTHON_EXE}" "${handoff_validator}" \
  --manifest "${results_dir}/backend_handoff_manifest.json" \
  --results-dir "${results_dir}" \
  --design-config "${DESIGN_CONFIG_3D}" \
  --platform "${PLATFORM}" \
  --flow-variant "${FLOW_VARIANT}" \
  --backend "${BACKEND}"
if ((validate_handoff_only)); then
  printf '[3D-Syn] Backend handoff validation-only completed: %s\n' "${results_dir}"
  exit 0
fi
mkdir -p "${log_dir}" "${objects_dir}" "${reports_dir}" "${results_dir}" "${status_dir}"

if [[ "${BACKEND}" == ord ]]; then
  stages=(
    3dsyn-ord-3d-floorplan ord-3d-io ord-3d-split-net
    ord-place-macro-upper ord-place-macro-bottom ord-3d-pdn-only
    ord-place-init ord-place-init-upper ord-place-init-bottom staged-prects
    ord-gp2lg ord-legalize-upper ord-legalize-bottom ord-cts ord-cts-post
    ord-route ord-final
  )
else
  stages=(
    3dsyn-cds-3d-floorplan cds-3d-io cds-3d-split-net
    cds-place-macro-upper cds-place-macro-bottom cds-3d-pdn-only
    cds-place-init cds-place-init-upper cds-place-init-bottom staged-prects
    cds-gp2lg cds-legalize-upper cds-legalize-bottom cds-cts cds-route cds-restore
  )
fi

stage_index() {
  local wanted="$1"
  local i
  if [[ "${wanted}" == 3d-floorplan ]]; then
    printf '%s\n' 0
    return 0
  fi
  if [[ "${wanted}" == final ]]; then
    printf '%s\n' "$((${#stages[@]} - 1))"
    return 0
  fi
  for ((i = 0; i < ${#stages[@]}; i++)); do
    if [[ "${stages[i]}" == "${wanted}" ]]; then
      printf '%s\n' "${i}"
      return 0
    fi
  done
  return 1
}

start_stage="${BACKEND_START_STAGE:-3d-floorplan}"
stop_stage="${BACKEND_STOP_STAGE:-final}"
start_index="$(stage_index "${start_stage}")" || fail "Unsupported BACKEND_START_STAGE: ${start_stage}"
stop_index="$(stage_index "${stop_stage}")" || fail "Unsupported BACKEND_STOP_STAGE: ${stop_stage}"
((start_index <= stop_index)) || fail "BACKEND_START_STAGE must not follow BACKEND_STOP_STAGE"
[[ "${OUTER_ITERATIONS:-1}" =~ ^[1-9][0-9]*$ ]] || \
  fail "OUTER_ITERATIONS must be a positive integer: ${OUTER_ITERATIONS:-}"

make_args=(
  --no-print-directory
  -C "${TAIWEI_FLOW_HOME}"
  -f "${TAIWEI_FLOW_HOME}/Makefile"
  -f "${SYN3D_ROOT}/taiwei/3dsyn.mk"
  "DESIGN_CONFIG=${DESIGN_CONFIG_3D}"
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
  "USE_FLOW=${use_flow}"
  "NUM_CORES=${NUM_CORES}"
  "OPENROAD_EXE=${OPENROAD_EXE}"
  "INNOVUS_EXE=${INNOVUS_EXE}"
  "PYTHON_EXE=${PYTHON_EXE}"
)

# Preserve the values loaded from config.mk unless the caller supplied a
# non-empty override. In particular, an empty FIXED_CORE_AREA means that ORD
# and CDS keep their existing CORE_UTILIZATION-based automatic sizing.
if [[ -n "${FIXED_CORE_AREA:-}" ]]; then
  make_args+=("FIXED_CORE_AREA=${FIXED_CORE_AREA}")
fi
if [[ -n "${CORE_ASPECT_RATIO:-}" ]]; then
  make_args+=("CORE_ASPECT_RATIO=${CORE_ASPECT_RATIO}")
fi

write_state() {
  local stage="$1" state="$2" rc="$3"
  local safe_stage="${stage//\//_}"
  local state_path="${status_dir}/${safe_stage}.env"
  local tmp_path="${state_path}.$$.tmp"
  {
    printf 'stage=%s\nstate=%s\nrc=%s\n' "${stage}" "${state}" "${rc}"
    printf 'platform=%s\ndesign=%s\nbackend=%s\nflow_variant=%s\n' \
      "${PLATFORM}" "${DESIGN}" "${BACKEND}" "${FLOW_VARIANT}"
    printf 'updated_at=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
  } > "${tmp_path}"
  mv -f -- "${tmp_path}" "${state_path}"
}

run_make_target() {
  local target="$1"
  local rc
  local -a target_args=()
  if [[ -n "${TIER_ALLOW_NET:-}" ]]; then
    target_args+=("TIER_ALLOW_NET=${TIER_ALLOW_NET}")
  fi
  write_state "${target}" running 0
  set +e
  make "${make_args[@]}" \
    "${target_args[@]+"${target_args[@]}"}" \
    "${target}"
  rc=$?
  set -e
  if ((rc != 0)); then
    write_state "${target}" failed "${rc}"
    return "${rc}"
  fi
  write_state "${target}" ok 0
}

run_stage() {
  local stage="$1"
  local iteration
  if [[ "${stage}" == staged-prects ]]; then
    for ((iteration = 1; iteration <= ${OUTER_ITERATIONS:-1}; iteration++)); do
      printf '[3D-Syn] staged preCTS iteration %d/%d\n' \
        "${iteration}" "${OUTER_ITERATIONS:-1}"
      TIER_ALLOW_NET=upper-only run_make_target "${BACKEND}-place-upper"
      TIER_ALLOW_NET=bottom-only run_make_target "${BACKEND}-place-bottom"
    done
  else
    printf '[3D-Syn] Running backend stage %s\n' "${stage}"
    run_make_target "${stage}"
  fi
}

printf '[3D-Syn] backend=%s stage range: %s -> %s\n' \
  "${BACKEND}" "${start_stage}" "${stop_stage}"
for ((i = start_index; i <= stop_index; i++)); do
  run_stage "${stages[i]}"
done

if ((stop_index >= 0)); then
  test -s "${results_dir}/2_3_floorplan_3d.v"
  test -s "${results_dir}/handoffs/floorplan-3d.tcl"
fi
printf '[3D-Syn] Backend completed through %s: %s\n' "${stop_stage}" "${results_dir}"
