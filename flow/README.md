# 3D-Syn Flow Implementation

This directory contains the stage implementations used by the public
launchers in the repository root. Normal users should run `run_3d_syn.sh` or
`run_baseline.sh`; `run_frontend.sh` and `run_backend.sh` are lower-level
drivers called by the release Makefile.

## 3D-Syn stages

| Stage | Directory | Responsibility |
| --- | --- | --- |
| 1. Coarse-Grained Netlist Construction | `coarse_synth` | Construct and characterize the coarse-grained netlist |
| 2. Tier Partitioning | `coarse_partition` | Partition coarse-grained units through the selected ORD or CDS path |
| 3. Per-Tier Synthesis | `per_tier_synth` | Expand, map, and synthesize each tier with cross-tier interconnect modeling |
| 4. Backend Handoff | `backend_handoff` | Prepare and validate the netlist, partition, DEF, and handoff manifest |
| 5. Physical Design | `backend` | Run the staged OpenROAD or Innovus 3D physical-design flow |

## Supporting components

| Path | Responsibility |
| --- | --- |
| `baseline` | TaiWei Yosys-to-Genus comparison frontend |
| `yosys_json_toolkit` | Shared operations on Yosys JSON netlists |
| `env_isolation` | Isolate Python libraries from EDA subprocess runtimes |
| `run_frontend.sh` | Frontend stage-range driver through backend handoff |
| `run_backend.sh` | ORD/CDS backend stage-range driver |

All generated files are written below the launcher's `RUN_DIR`. Stage-private
working data, logs, reports, and final results do not modify this source tree
or the TaiWei checkout.

The hidden range variables `START_STAGE`, `STOP_STAGE`,
`BACKEND_START_STAGE`, and `BACKEND_STOP_STAGE` support targeted recovery while
keeping the normal launcher configuration concise.
