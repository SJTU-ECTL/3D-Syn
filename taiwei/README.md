# TaiWei Integration

This directory defines the non-invasive interface between 3D-Syn and a
compatible TaiWei checkout.

## Design rules

- TaiWei source files, Makefiles, and platform files are never patched.
- 3D-Syn targets live in a separate make overlay.
- All generated files are written below `RUN_DIR`, outside the TaiWei source
  tree.
- Existing TaiWei targets keep their original names and behavior.
- The overlay consumes public variables rather than machine-specific paths.

## Files

- `3dsyn.mk` defines the shared path and run-configuration contract, the
  Yosys-to-Genus baseline target, and the ORD/CDS backend override targets.
- The repository root `Makefile` provides the user-facing flow targets and
  forwards the selected configuration to this integration layer.

Backend and baseline targets evaluate the overlay together with the original
TaiWei Makefile. This reuses TaiWei design, platform, library, and logging
variables without copying their definitions into this repository.

## Tool executables

The public launchers derive Yosys and OpenROAD from `SYN3D_TAIWEI_HOME` and use
the Cadence executables available in the caller's environment. Every executable
can be overridden explicitly:

```bash
export SYN3D_TAIWEI_HOME=/path/to/TaiWei
export SYN3D_YOSYS_EXE=/path/to/yosys
export SYN3D_YOSYS_CONFIG=/path/to/yosys-config
export SYN3D_OPENROAD_EXE=/path/to/openroad
export SYN3D_GENUS_EXE=/path/to/genus
export SYN3D_INNOVUS_EXE=/path/to/innovus
export SYN3D_PYTHON_EXE=/path/to/python-launcher-or-interpreter
```

The launchers translate these public environment variables into the internal
`TAIWEI_HOME`, `YOSYS_EXE`, `OPENROAD_EXE`, `GENUS_EXE`, `INNOVUS_EXE`, and
`PYTHON_EXE` make variables. The standalone baseline driver exposes its tool
paths through `--yosys-bin` and `--genus-bin`.

## Configuration mapping

| Public variable | TaiWei or 3D-Syn role |
| --- | --- |
| `TAIWEI_FLOW_HOME` | Location of the original TaiWei flow Makefile |
| `DESIGN_CONFIG_2D` | Repository-owned `config2d.mk` used by the frontend |
| `DESIGN_CONFIG_3D` | Repository-owned `config.mk` used by the 3D flow |
| `DESIGN_SDC` | Constraint file owned and selected by 3D-Syn |
| `RUN_DIR` | TaiWei `WORK_HOME`; contains logs/objects/reports/results |
| `PLATFORM`, `DESIGN` | Case selection |
| `FLOW_VARIANT` | Output namespace for one run |
| `BACKEND` | Selects ORD or CDS partition and backend-handoff adapters |
| `AREA_MODE` | Selects GNN inference or fixed utilization |
| `FIXED_UTIL` | Coarse-unit utilization used when `AREA_MODE=fixed` |
| `SYNTH_OPT_MODE` | Shared Genus optimization effort for 3D-Syn and baseline |
| `PREPLACE_OPT` | Shared switch for topology-changing ORD/CDS pre-place steps |
| `FIXED_CORE_AREA` | Optional shared 3D floorplan core-area override |
| `CORE_ASPECT_RATIO` | Height-to-width ratio used with fixed core area |
| `NUM_CORES` | CPU-core count forwarded to supported EDA stages |
| `CHECKPOINT_ROOT` | Root containing compatible area-model checkpoints |
| `SYN3D_HBT_*` | Cross-tier interconnect model parameters used by per-tier synthesis |
| `YOSYS_PLUGIN` | Loadable library containing the 3D-Syn passes |

The overlay will compose original TaiWei targets with scripts stored in this
repository. It will not replace `run_genus.tcl`, pre-place scripts, 3D
floorplan scripts, design SDC files, or the TaiWei Makefile in place.

Repository-owned case files may retain their upstream `SDC_FILE` assignment
for traceability. Every overlay invocation passes `DESIGN_SDC` as a command-
line `SDC_FILE` override, so neither the 3D-Syn flow nor the baseline can
silently use a constraint file from the TaiWei design tree.
