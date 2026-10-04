"""Environment helper for EDA subprocesses launched by the Python flow.

The Python launcher prepends the selected environment's PyTorch library
directory so ``import torch`` remains usable beside the Cadence modules. EDA
executables must not inherit that directory because it contains libraries with
generic names that can override the tool's own runtime libraries.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


PRIVATE_LIBRARY_ENV = "SYN3D_PRIVATE_PYTHON_LIB"


def eda_subprocess_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment suitable for an external EDA executable.

    The caller's environment is copied, never mutated. Only the PyTorch
    directory advertised by ``python_launcher.sh`` is removed from
    ``LD_LIBRARY_PATH``; Cadence/OpenROAD module paths and all other caller
    settings remain unchanged.
    """

    child_env = dict(os.environ if env is None else env)
    private_library = child_env.pop(PRIVATE_LIBRARY_ENV, "").strip()
    if not private_library:
        return child_env

    private_path = Path(private_library).resolve()
    library_path = child_env.get("LD_LIBRARY_PATH", "")
    if not library_path:
        return child_env

    filtered = []
    for entry in library_path.split(":"):
        if not entry:
            filtered.append(entry)
            continue
        try:
            same_private_path = Path(entry).resolve() == private_path
        except OSError:
            same_private_path = entry == private_library
        if not same_private_path:
            filtered.append(entry)

    if filtered:
        child_env["LD_LIBRARY_PATH"] = ":".join(filtered)
    else:
        child_env.pop("LD_LIBRARY_PATH", None)
    return child_env

