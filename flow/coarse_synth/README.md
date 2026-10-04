# Coarse-Grained Netlist Construction

This stage identifies supported arithmetic units and multi-bit registers,
preserves them as coarse-grained units, and publishes the netlist and views
consumed by tier partitioning. For each unit, the area model predicts
post-route utilization from its mapped implementation; the flow combines that
prediction with implementation area to derive its physical footprint.
`run_coarse_synth.sh` is the stage entry point.

## Execution order

1. `yosys_generate_coarse_netlist.tcl` marks coarse units and writes their
   canonical RTLIL sources and the coarse top-level netlist.
2. `characterize_coarse_units.py` maps each unit, calls
   `area_model/predict_util.py` or uses a fixed utilization, and records the
   resulting implementation area and footprint. `CHECKPOINT_ROOT` may select
   an alternate validated checkpoint root; otherwise the bundled checkpoints
   are used.
3. `prepare_hierarchy_input.py` assembles the characterized units into the
   hierarchy input netlist.
4. `genus_run_hierarchy_synthesis.tcl` synthesizes the complete hierarchy.
   `genus_refine_coarse_unit.tcl` is used earlier by the characterization
   step when refining an individual unit.
5. `finalize_hierarchy_output.py` coordinates final hierarchy normalization
   and publishes the coarse-synthesis handoff. `hierarchy_variants.py`
   analyzes and remaps retained variants, while `coarse_unit_views.py`
   generates their metrics, LEF, Liberty, and netlist views.

All generated files are written below the configured run directory. The
scripts in this directory do not contain a private copy of the area model.
