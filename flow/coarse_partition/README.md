# Tier Partitioning

This stage pre-places the coarse netlist and assigns its instances to the
bottom and upper tiers. Both backend paths consume the same coarse-synthesis
inputs and must publish the same partition contract.

## Entry points

1. `run_ord_coarse_partition.sh` invokes the OpenROAD-based TaiWei partition
   path.
2. `run_cds_coarse_partition.sh` invokes the Cadence-based TaiWei partition
   path.

The root Makefile selects one entry point from `BACKEND`. The scripts remain
side by side because each is a single backend driver; backend-specific Tcl
collections use `ord/` and `cds/` directories elsewhere in the flow.
