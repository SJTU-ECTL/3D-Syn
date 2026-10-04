# Backend Handoff

This stage converts the per-tier synthesis outputs into the netlist,
floorplan, and partition views required by the selected TaiWei backend.

## Execution order

1. `ord/run_backend_handoff.sh` or `cds/run_backend_handoff.sh` selects the
   backend-specific implementation of the shared handoff contract.
2. `prepare_backend_netlist.py` maps the per-tier output to the
   tier-qualified netlist consumed by the backend.
3. The selected OpenROAD or Innovus scripts perform backend-specific
   floorplanning and pre-placement.
4. `02_prepare_backend_partition.py` reconciles allowed pre-place netlist
   changes with the partition contract. `backend_partition_rules.py` defines
   platform mappings and library rules, and `backend_partition_analysis.py`
   evaluates connectivity and selects tiers for added instances.
5. `03_generate_backend_views.py` writes the canonical 3D netlist and DEF.
6. The driver publishes `backend_handoff_manifest.json`, which records the
   selected backend and the canonical files supplied to physical design.

The ORD path uses `01_backend_floorplan.tcl` followed by
`01_backend_io_place.tcl`. The CDS path uses
`01_backend_preplace.tcl`. Backend-independent Python logic is stored once in
this directory rather than duplicated under `ord/` and `cds/`.

Before physical design begins, `flow/run_backend.sh` invokes
`validate_backend_handoff.py` to check the manifest, canonical netlist and DEF,
partition, design configuration, platform, backend, and flow variant. A stale
or incomplete handoff therefore fails before an EDA backend stage is launched.
