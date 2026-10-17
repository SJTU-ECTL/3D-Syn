# TaiWei Comparison Baseline

The public `run_baseline.sh` launcher runs the comparison flow used to evaluate
3D-Syn. It preserves the TaiWei Yosys result, optimizes that mapped netlist
with Genus, and continues through the TaiWei physical-design flow.

## Flow

```text
RTL
  -> TaiWei Yosys synthesis
  -> preserved Yosys netlist
  -> Genus optimization
  -> TaiWei physical design and final reporting
```

| File | Responsibility |
| --- | --- |
| `run_baseline_synth.sh` | Run the complete comparison baseline |
| `run_genus_on_netlist.tcl` | Optimize the mapped Yosys netlist with the selected Genus effort |

The baseline uses the selected design configuration, constraints, optimization
effort, and CPU-core setting without modifying the TaiWei checkout.

## Usage

```bash
./run_baseline.sh
```

Edit the user-configuration section in the repository-root launcher before
running it. The lower-level driver accepts explicit paths and settings; run
`flow/baseline/run_baseline_synth.sh --help` for its complete interface.

## Outputs

Results are isolated under
`RUN_DIR/{logs,objects,reports,results}/PLATFORM/DESIGN/FLOW_VARIANT`.
`1_synth_yosys.v` remains the comparison point, while
`1_synth_netlist.v/.sdc` are the explicit Genus outputs. The driver publishes
the Genus netlist as canonical `1_synth.v` and the selected constraints as
canonical `1_synth.sdc`. Remaining artifacts use TaiWei's normal names.
