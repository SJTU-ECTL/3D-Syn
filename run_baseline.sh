#!/usr/bin/env bash
# Public single-case launcher for the released comparison baseline.

set -euo pipefail

# ==============================================================================
# User configuration
# ==============================================================================

# Technology: asap7_3D, nangate45_3D, asap7_nangate45_3D
TECH="nangate45_3D"

# Design: gcd, aes, ibex, jpeg, ariane133, bp_quad, swerv_wrapper
DESIGN="gcd"

# Backend:
#   ord  OpenROAD
#   cds  Cadence Innovus
BACKEND="cds"

# Run identifier used for the output directory.
RUN_TAG="example_baseline"

# Genus optimization effort: normal, low_effort
SYNTH_OPT_MODE="low_effort"

# CPU cores.
NUM_CORES=1

# ==============================================================================
# End user configuration
# ==============================================================================

fail() {
  printf '[3D-Syn][baseline] ERROR: %s\n' "$*" >&2
  exit 2
}

if (($#)); then
  if [[ $# -eq 1 && ( "$1" == -h || "$1" == --help ) ]]; then
    printf 'Usage: ./run_baseline.sh\n'
    exit 0
  fi
  fail 'This launcher takes no arguments; edit the configuration section.'
fi

# Hidden compatibility control for advanced runs.
TECH="${SYN3D_TECH:-${TECH}}"
DESIGN="${SYN3D_DESIGN:-${DESIGN}}"
BACKEND="${SYN3D_BACKEND:-${BACKEND}}"
RUN_TAG="${SYN3D_RUN_TAG:-${RUN_TAG}}"
NUM_CORES="${SYN3D_NUM_CORES:-${NUM_CORES}}"

case "${TECH}" in asap7_3D|nangate45_3D|asap7_nangate45_3D) ;; *) fail "Unsupported technology: ${TECH}" ;; esac
case "${DESIGN}" in gcd|aes|ibex|jpeg|ariane133|bp_quad|swerv_wrapper) ;; *) fail "Unsupported design: ${DESIGN}" ;; esac
case "${BACKEND}" in ord|cds) ;; *) fail "BACKEND must be ord or cds: ${BACKEND}" ;; esac
[[ "${RUN_TAG}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Invalid RUN_TAG: ${RUN_TAG}"
[[ "${NUM_CORES}" =~ ^[1-9][0-9]*$ ]] || fail "NUM_CORES must be positive: ${NUM_CORES}"
case "${SYNTH_OPT_MODE}" in normal|low_effort) ;; *) fail "Invalid SYNTH_OPT_MODE: ${SYNTH_OPT_MODE}" ;; esac

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TAIWEI_HOME="${SYN3D_TAIWEI_HOME:-$(realpath -m "${SCRIPT_DIR}/../TaiWei")}"
RUN_DIR="${SYN3D_RUN_DIR:-${SCRIPT_DIR}/runs/${RUN_TAG}}"
FLOW_VARIANT="${RUN_TAG}_${TECH}_${DESIGN}_baseline_${BACKEND}"
BASELINE_STOP_STAGE="${SYN3D_BASELINE_STOP_STAGE:-final}"

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

printf '[3D-Syn][baseline] tech=%s design=%s backend=%s run=%s\n' \
  "${TECH}" "${DESIGN}" "${BACKEND}" "${RUN_TAG}"

exec make --no-print-directory -C "${SCRIPT_DIR}" baseline \
  "TAIWEI_HOME=${TAIWEI_HOME}" \
  "PLATFORM=${TECH}" \
  "DESIGN=${DESIGN}" \
  "BACKEND=${BACKEND}" \
  "FLOW_VARIANT=${FLOW_VARIANT}" \
  "RUN_DIR=${RUN_DIR}" \
  "BASELINE_STOP_STAGE=${BASELINE_STOP_STAGE}" \
  "NUM_CORES=${NUM_CORES}" \
  "SYNTH_OPT_MODE=${SYNTH_OPT_MODE}" \
  "YOSYS_EXE=${YOSYS_EXE}" \
  "YOSYS_CONFIG=${YOSYS_CONFIG}" \
  "OPENROAD_EXE=${OPENROAD_EXE}" \
  "GENUS_EXE=${GENUS_EXE}" \
  "INNOVUS_EXE=${INNOVUS_EXE}" \
  "PYTHON_EXE=${PYTHON_EXE}"
