# 3D-Syn Yosys Plugin

This component builds the custom Yosys passes used by the 3D-Syn flow as a
loadable shared library. It is intentionally separate from the TaiWei and
Yosys source trees so that users do not need to rebuild or replace Yosys.

## Passes

| Pass | Responsibility |
| --- | --- |
| `syn3d_mark_coarse_units` | Identify and wrap supported coarse units |
| `syn3d_normalize_instance_names` | Normalize instance names for handoff contracts |
| `syn3d_write_coarse_unit_libs` | Generate coarse-unit timing and library handoff data |

`src/yosys_json_toolkit.cc` and `.h` provide C++ helpers shared by these
passes. They are distinct from the flow's Python Yosys JSON toolkit.

The coarse-unit Liberty pass is divided into three implementation files:

| File | Responsibility |
| --- | --- |
| `src/syn3d_write_coarse_unit_libs.cc` | Pass registration, input discovery, and stage orchestration |
| `src/syn3d_coarse_unit_timing.cc` | Technology timing tables and synthesized-netlist path analysis |
| `src/syn3d_coarse_unit_liberty.cc` | Liberty templates, pins, timing arcs, cells, and file serialization |

`src/syn3d_coarse_unit_libs.h` is the narrow internal contract shared by
these three files.

## Build contract

The plugin Makefile obtains compiler, include, linker, and ABI settings
from the Yosys installation selected by the compatible TaiWei checkout. No
path into an Integration build tree may be embedded in the library.

The expected output is:

```text
yosys-plugin/build/3dsyn.so
```

The flow will load this file explicitly before calling a 3D-Syn pass. A build
or load must fail with a clear version diagnostic when the selected Yosys does
not provide a compatible plugin interface.

Build and verify the plugin from the repository root:

```bash
export SYN3D_TAIWEI_HOME=/path/to/TaiWei
make plugin
make -C yosys-plugin load-check TAIWEI_HOME="$SYN3D_TAIWEI_HOME"
```

If Yosys is installed outside that TaiWei checkout, also pass `YOSYS_EXE` and
`YOSYS_CONFIG` to `load-check`.
