#!/usr/bin/env bash
# Public single-case launcher for the complete released 3D-Syn flow.

set -euo pipefail

# ==============================================================================
# User configuration
# ==============================================================================

# Technology: asap7_3D, nangate45_3D, asap7_nangate45_3D
TECH="nangate45_3D"

# Design: gcd, aes, ibex, jpeg, bp_quad, swerv_wrapper
DESIGN="gcd"

# Backend:
#   ord  OpenROAD
#   cds  Cadence Innovus
BACKEND="cds"

# Run identifier used for the output directory.
RUN_TAG="example_3dsyn"

# Coarse-grained unit utilization mode:
#   gnn    Predict utilization with the pretrained area model.
#   fixed  Use FIXED_UTIL below.
AREA_MODE="gnn"

# Fixed coarse-grained unit utilization in (0, 1].
# Used only when AREA_MODE="fixed".
FIXED_UTIL=1.0

# Genus optimization effort: normal, low_effort
SYNTH_OPT_MODE="low_effort"

# Backend pre-placement optimization: 0 (off), 1 (on)
PREPLACE_OPT=0

# TritonPart timing-aware partitioning: 0 (off), 1 (on)
TRITONPART_TIMING_AWARE=0

# Cross-tier interconnect resistance in ohms.
HBT_R_OHM=1.0

# Cross-tier interconnect capacitance in fF.
HBT_C_FF=0.5

# CPU cores.
NUM_CORES=1

# ==============================================================================
# End user configuration
# ==============================================================================

fail() {
  printf '[3D-Syn] ERROR: %s\n' "$*" >&2
  exit 2
}

usage() {
  printf 'Usage: ./run_3d_syn.sh\n'
}

if (($#)); then
  if [[ $# -eq 1 && ( "$1" == -h || "$1" == --help ) ]]; then
    usage
    exit 0
  fi
  fail 'This launcher takes no arguments; edit the configuration section.'
fi

# Hidden compatibility controls for advanced runs.
TECH="${SYN3D_TECH:-${TECH}}"

case "${TECH}" in asap7_3D|nangate45_3D|asap7_nangate45_3D) ;; *) fail "Unsupported technology: ${TECH}" ;; esac
case "${DESIGN}" in gcd|aes|ibex|jpeg|bp_quad|swerv_wrapper) ;; *) fail "Unsupported design: ${DESIGN}" ;; esac
case "${BACKEND}" in ord|cds) ;; *) fail "BACKEND must be ord or cds: ${BACKEND}" ;; esac
[[ "${RUN_TAG}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Invalid RUN_TAG: ${RUN_TAG}"
[[ "${NUM_CORES}" =~ ^[1-9][0-9]*$ ]] || fail "NUM_CORES must be positive: ${NUM_CORES}"
case "${AREA_MODE}" in gnn|fixed) ;; *) fail "AREA_MODE must be gnn or fixed: ${AREA_MODE}" ;; esac
case "${SYNTH_OPT_MODE}" in normal|low_effort) ;; *) fail "Invalid SYNTH_OPT_MODE: ${SYNTH_OPT_MODE}" ;; esac
case "${PREPLACE_OPT}" in 0|1) ;; *) fail "PREPLACE_OPT must be 0 or 1: ${PREPLACE_OPT}" ;; esac
case "${TRITONPART_TIMING_AWARE}" in 0|1) ;; *) fail "TRITONPART_TIMING_AWARE must be 0 or 1: ${TRITONPART_TIMING_AWARE}" ;; esac
hbt_model_mode="${SYN3D_HBT_MODEL_MODE:-direct}"
case "${hbt_model_mode}" in direct|scale) ;; *) fail "Invalid SYN3D_HBT_MODEL_MODE: ${hbt_model_mode}" ;; esac
if [[ "${hbt_model_mode}" == direct ]]; then
  hbt_r_ohm="${SYN3D_HBT_R_OHM:-${HBT_R_OHM}}"
  hbt_c_ff="${SYN3D_HBT_C_FF:-${HBT_C_FF}}"
  [[ "${hbt_r_ohm}" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] ||
    fail "HBT_R_OHM must be non-negative: ${hbt_r_ohm}"
  [[ "${hbt_c_ff}" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] ||
    fail "HBT_C_FF must be non-negative: ${hbt_c_ff}"
else
  hbt_r_ohm=""
  hbt_c_ff=""
fi
if [[ "${AREA_MODE}" == fixed ]]; then
  [[ "${FIXED_UTIL}" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] &&
    awk -v value="${FIXED_UTIL}" 'BEGIN { exit !(value > 0.0 && value <= 1.0) }' ||
    fail "FIXED_UTIL must be in (0, 1]: ${FIXED_UTIL}"
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TAIWEI_HOME="${SYN3D_TAIWEI_HOME:-$(realpath -m "${SCRIPT_DIR}/../TaiWei")}"
RUN_DIR="${SCRIPT_DIR}/runs/${RUN_TAG}"
FLOW_VARIANT="${RUN_TAG}_${TECH}_${DESIGN}_${BACKEND}"

[[ -f "${SCRIPT_DIR}/configs/${TECH}/${DESIGN}/config2d.mk" ]] ||
  fail "Missing release case: ${TECH}/${DESIGN}"
[[ -f "${TAIWEI_HOME}/TaiWei-Pin-3D/Makefile" ]] ||
  fail "Compatible TaiWei checkout not found: ${TAIWEI_HOME}"

YOSYS_EXE="${SYN3D_YOSYS_EXE:-${TAIWEI_HOME}/ORFS-Research/tools/install/yosys/bin/yosys}"
YOSYS_CONFIG="${SYN3D_YOSYS_CONFIG:-${TAIWEI_HOME}/ORFS-Research/tools/install/yosys/bin/yosys-config}"
OPENROAD_EXE="${SYN3D_OPENROAD_EXE:-${TAIWEI_HOME}/ORFS-Research/tools/install/OpenROAD/bin/openroad}"
GENUS_EXE="${SYN3D_GENUS_EXE:-genus}"
INNOVUS_EXE="${SYN3D_INNOVUS_EXE:-innovus}"
PYTHON_EXE="${SYN3D_PYTHON_EXE:-${SCRIPT_DIR}/flow/env_isolation/python_launcher.sh}"

make_args=(
  --no-print-directory
  -C "${SCRIPT_DIR}"
  run
  "TAIWEI_HOME=${TAIWEI_HOME}"
  "PLATFORM=${TECH}"
  "DESIGN=${DESIGN}"
  "BACKEND=${BACKEND}"
  "FLOW_VARIANT=${FLOW_VARIANT}"
  "RUN_DIR=${RUN_DIR}"
  "NUM_CORES=${NUM_CORES}"
  "AREA_MODE=${AREA_MODE}"
  "SYNTH_OPT_MODE=${SYNTH_OPT_MODE}"
  "PREPLACE_OPT=${PREPLACE_OPT}"
  "TRITONPART_TIMING_AWARE=${TRITONPART_TIMING_AWARE}"
  "SYN3D_HBT_MODEL_MODE=${hbt_model_mode}"
  "SYN3D_HBT_R_OHM=${hbt_r_ohm}"
  "SYN3D_HBT_C_FF=${hbt_c_ff}"
  "YOSYS_EXE=${YOSYS_EXE}"
  "YOSYS_CONFIG=${YOSYS_CONFIG}"
  "OPENROAD_EXE=${OPENROAD_EXE}"
  "GENUS_EXE=${GENUS_EXE}"
  "INNOVUS_EXE=${INNOVUS_EXE}"
  "PYTHON_EXE=${PYTHON_EXE}"
)
if [[ "${AREA_MODE}" == fixed ]]; then
  make_args+=("FIXED_UTIL=${FIXED_UTIL}")
fi

printf '[3D-Syn] tech=%s design=%s backend=%s run=%s\n' \
  "${TECH}" "${DESIGN}" "${BACKEND}" "${RUN_TAG}"
exec make "${make_args[@]}"
