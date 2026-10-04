#!/usr/bin/env bash
# Run the selected Python while keeping its PyTorch libraries isolated from EDA
# subprocesses. The interpreter may come from the system, conda, or a venv.

set -euo pipefail

python_exe="${SYN3D_PYTHON_INTERPRETER:-python3}"

if [[ "${python_exe}" == */* ]]; then
  if [[ ! -x "${python_exe}" ]]; then
    printf '[3D-Syn Python] interpreter is unavailable: %s\n' "${python_exe}" >&2
    exit 2
  fi
else
  resolved_python="$(command -v -- "${python_exe}" 2>/dev/null || true)"
  if [[ -z "${resolved_python}" ]]; then
    printf '[3D-Syn Python] interpreter is unavailable: %s\n' "${python_exe}" >&2
    exit 2
  fi
  python_exe="${resolved_python}"
fi

torch_lib="$(env -u PYTHONHOME -u PYTHONPATH "${python_exe}" - <<'PY'
import importlib.util
from pathlib import Path

spec = importlib.util.find_spec("torch")
if spec and spec.submodule_search_locations:
    candidate = Path(next(iter(spec.submodule_search_locations))) / "lib"
    if candidate.is_dir():
        print(candidate.resolve())
PY
)"

if [[ -n "${torch_lib}" ]]; then
  export SYN3D_PRIVATE_PYTHON_LIB="${torch_lib}"
  if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then
    export LD_LIBRARY_PATH="${torch_lib}:${LD_LIBRARY_PATH}"
  else
    export LD_LIBRARY_PATH="${torch_lib}"
  fi
fi

unset PYTHONHOME PYTHONPATH
exec "${python_exe}" "$@"

