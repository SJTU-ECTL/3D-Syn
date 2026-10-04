#!/usr/bin/env bash
# Run coarse-unit discovery, characterization, hierarchy synthesis, and handoff publication.

set -euo pipefail

export NUM_CORES="${NUM_CORES:-1}"
if [[ ! "${NUM_CORES}" =~ ^[1-9][0-9]*$ ]]; then
  echo "[3D-Syn] NUM_CORES must be a positive integer: ${NUM_CORES}" >&2
  exit 1
fi

required_variables=(
  FLOW_HOME
  YOSYS_EXE
  PYTHON_EXE
  GENUS_EXE
  SYN3D_GENUS_OPT_MODE
  SYN3D_COARSE_SYNTH_DIR
  SYN3D_PLATFORM
  DESIGN_NAME
  LIB_FILES
  SDC_FILE
  OBJECTS_DIR
  RESULTS_DIR
  LOG_DIR
)

for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "[3D-Syn] Missing required environment variable: ${variable_name}" >&2
    exit 1
  fi
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/../.." && pwd)"
generate_script="${script_dir}/yosys_generate_coarse_netlist.tcl"
characterize_script="${script_dir}/characterize_coarse_units.py"
prepare_script="${script_dir}/prepare_hierarchy_input.py"
hierarchy_script="${script_dir}/genus_run_hierarchy_synthesis.tcl"
finalize_script="${script_dir}/finalize_hierarchy_output.py"
unit_genus_script="${script_dir}/genus_refine_coarse_unit.tcl"
predict_util_script="${repo_root}/area_model/predict_util.py"

if [[ ! -x "${YOSYS_EXE}" ]]; then
  echo "[3D-Syn] Yosys executable is unavailable: ${YOSYS_EXE}" >&2
  exit 1
fi
for required_file in "${generate_script}" "${characterize_script}" "${prepare_script}" \
  "${hierarchy_script}" "${finalize_script}" "${unit_genus_script}" \
  "${predict_util_script}" "${SDC_FILE}"; do
  if [[ ! -f "${required_file}" ]]; then
    echo "[3D-Syn] Required input is unavailable: ${required_file}" >&2
    exit 1
  fi
done

if [[ ! -x "${GENUS_EXE}" ]]; then
  echo "[3D-Syn] Genus executable is unavailable: ${GENUS_EXE}" >&2
  exit 1
fi

case "${SYN3D_PLATFORM}" in
  asap7_3D|nangate45_3D|asap7_nangate45_3D) ;;
  *)
    echo "[3D-Syn] Unsupported SYN3D_PLATFORM: ${SYN3D_PLATFORM}" >&2
    exit 1
    ;;
esac

case "${SYN3D_GENUS_OPT_MODE}" in
  normal|low_effort) ;;
  *)
    echo "[3D-Syn] Unsupported SYN3D_GENUS_OPT_MODE: ${SYN3D_GENUS_OPT_MODE}" >&2
    exit 1
    ;;
esac

stage_dir="${SYN3D_COARSE_SYNTH_DIR}"
yosys_dir="${stage_dir}/yosys"
characterization_dir="${stage_dir}/characterization"
hierarchy_dir="${stage_dir}/hierarchy"
handoff_dir="${stage_dir}/handoff"
mkdir -p "${stage_dir}" "${RESULTS_DIR}" "${LOG_DIR}"

read -r -a yosys_flags <<< "${YOSYS_FLAGS:-}"
"${YOSYS_EXE}" "${yosys_flags[@]}" -c "${generate_script}"

rm -rf -- "${characterization_dir}" "${hierarchy_dir}" "${handoff_dir}"
mkdir -p "${characterization_dir}" "${hierarchy_dir}"

characterize_args=(
  --flow-home "${FLOW_HOME}"
  --platform "${SYN3D_PLATFORM}"
  --manifest "${yosys_dir}/coarse_units.list"
  --source-dir "${yosys_dir}/coarse_units"
  --output-dir "${characterization_dir}"
  --yosys "${YOSYS_EXE}"
  --genus "${GENUS_EXE}"
  --genus-script "${unit_genus_script}"
  --predict-util "${predict_util_script}"
  --python "${PYTHON_EXE}"
)
if [[ -n "${SYN3D_FIXED_UTIL:-}" ]]; then
  characterize_args+=(--fixed-util "${SYN3D_FIXED_UTIL}")
fi
if [[ -n "${CHECKPOINT_ROOT:-}" ]]; then
  characterize_args+=(--checkpoint-root "${CHECKPOINT_ROOT}")
fi
"${PYTHON_EXE}" "${characterize_script}" "${characterize_args[@]}"

"${PYTHON_EXE}" "${prepare_script}" \
  --top-netlist "${yosys_dir}/coarse_netlist.v" \
  --coarse-unit-source-dir "${characterization_dir}/implementation" \
  --output-netlist "${hierarchy_dir}/input.v" \
  --coarse-unit-list-out "${hierarchy_dir}/coarse_units.list" \
  --manifest-out "${hierarchy_dir}/input_manifest.json" \
  --platform "${SYN3D_PLATFORM}" \
  --yosys "${YOSYS_EXE}" \
  --work-dir "${hierarchy_dir}/prepare_work"

env \
  GENUS_INPUT_NETLIST="${hierarchy_dir}/input.v" \
  GENUS_OUTPUT_NETLIST="${hierarchy_dir}/output.raw.v" \
  GENUS_OUTPUT_SDC="${OBJECTS_DIR}/1_synth.genus.sdc" \
  GENUS_LIB_FILES="${LIB_FILES}" \
  SYN3D_COARSE_UNIT_LIST_FILE="${hierarchy_dir}/coarse_units.list" \
  "${GENUS_EXE}" -overwrite -log "${LOG_DIR}/cadence_1_3dsyn_hierarchy.log" \
  -f "${hierarchy_script}"

"${PYTHON_EXE}" "${finalize_script}" \
  --design-name "${DESIGN_NAME}" \
  --platform "${SYN3D_PLATFORM}" \
  --yosys "${YOSYS_EXE}" \
  --input-verilog "${hierarchy_dir}/output.raw.v" \
  --output-verilog "${RESULTS_DIR}/1_synth.v" \
  --coarse-unit-source-dir "${characterization_dir}/implementation" \
  --coarse-unit-list "${hierarchy_dir}/coarse_units.list" \
  --family-metrics-dir "${characterization_dir}/metrics" \
  --handoff-dir "${handoff_dir}" \
  --work-dir "${hierarchy_dir}/finalize_work"

cp -f -- "${SDC_FILE}" "${RESULTS_DIR}/1_synth.sdc"

test -s "${RESULTS_DIR}/1_synth.v"
test -s "${RESULTS_DIR}/1_synth.sdc"
echo "[3D-Syn] Coarse synthesis handoff: ${handoff_dir}"
