# TaiWei Comparison Baseline

The public `run_baseline.sh` launcher runs the synthesis baseline used for a
controlled comparison with 3D-Syn. It does not run tier partitioning, backend
handoff, or the ORD/CDS physical-design backend.

## Synthesis flow

```text
RTL
  -> TaiWei Yosys synthesis
  -> preserved Yosys netlist
  -> Genus optimization
  -> baseline synthesis netlist and SDC
```

| File | Responsibility |
| --- | --- |
| `run_baseline_synth.sh` | Run TaiWei Yosys, preserve its netlist, and invoke Genus on that netlist |
| `run_genus_on_netlist.tcl` | Optimize the mapped Yosys netlist with the selected Genus effort |

The baseline uses the same design configuration, constraints, optimization
effort, and CPU-core setting selected by the public launchers. It evaluates the
3D-Syn make overlay together with the original TaiWei Makefile without
modifying the TaiWei checkout.

## Standalone driver

The repository-root launcher is the normal interface. The lower-level driver
can also be invoked directly with explicit paths:

```bash
flow/baseline/run_baseline_synth.sh \
  --taiwei-home "$SYN3D_TAIWEI_HOME" \
  --platform asap7_3D \
  --design gcd \
  --run-dir runs/example_baseline \
  --yosys-bin "$SYN3D_TAIWEI_HOME/ORFS-Research/tools/install/yosys/bin/yosys" \
  --genus-bin "$SYN3D_GENUS_EXE" \
  --synth-opt-mode low_effort
```

Use `--design-config` and `--design-sdc` to select non-default case files.
Run the command with `--help` for the complete interface.

## Outputs

Results are isolated under
`RUN_DIR/{logs,objects,reports,results}/PLATFORM/DESIGN/FLOW_VARIANT`.
`1_synth_yosys.v` remains the comparison point, while
`1_synth_netlist.v/.sdc` are the explicit Genus outputs. The driver publishes
the Genus netlist as canonical `1_synth.v`, but copies the explicitly selected
`--design-sdc` to canonical `1_synth.sdc`.
