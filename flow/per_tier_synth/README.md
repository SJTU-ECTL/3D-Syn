# Per-Tier Synthesis

This stage expands the partitioned coarse units, maps each tier to its target
technology, builds the cross-tier wrapper, and publishes the final base
netlist and partition. `run_per_tier_synth.sh` validates the environment and
launches `run_flow.py`, which executes the numbered transformations below.

## Execution order

| Step | Script | Responsibility |
| --- | --- | --- |
| 01 | `01_build_pin_directions.py` | Derive top-level and coarse-unit pin directions |
| 02 | `02_split_tiers.py` | Split the design according to the coarse partition |
| 03 | `03_expand_coarse_units.py` | Restore coarse-unit implementations in each tier |
| 04 | `04_map_tier_cells.py` | Map cells to the selected tier technologies |
| 05 | `05_build_wrapper.py` | Build explicit cross-tier interfaces and the wrapper |
| 06 | `06_synthesize_tiers.py` | Run homogeneous or heterogeneous tier synthesis |
| 07 | `07_flatten_synthesis_output.py` | Flatten the synthesized tier hierarchy |
| 08 | `08_restore_base_netlist.py` | Reconstruct the backend-facing base netlist |
| 09 | `09_write_partition.py` | Publish the final instance partition |

The numbered entry scripts remain the only executable stages. Their internal
implementations are grouped under `modules/` so that the stage directory keeps
only the public sequence and its shared configuration files:

| Step | Supporting modules | Responsibility |
| --- | --- | --- |
| 02 | `modules/tier_split_analysis.py`, `modules/tier_split_builder.py` | Analyze bit ownership, construct both tier modules, and validate split contracts |
| 04 | `modules/cell_map.py` | Normalize the heterogeneous cell-map schema; steps 01, 03, 04, and 08 read this mapping |
| 05 | `modules/wrapper_plan.py`, `modules/wrapper_builder.py` | Plan tier-interface actions and construct the wrapper design |
| 06 | `modules/genus_common.py`, `modules/genus_homogeneous.py`, `modules/genus_heterogeneous.py`, `modules/hbt_model.py` | Share Genus contracts and implement the two synthesis strategies and HBT model |

During step 06, `hbt_model.py` derives the resistance and capacitance applied
to every explicit data or clock connection that crosses tiers. The generated
Genus constraints attach this interconnect cost to the corresponding D2D nets,
so both homogeneous and heterogeneous tier synthesis account for cross-tier
delay. The selected parameters are forwarded through the shared TaiWei overlay
and recorded in the per-tier synthesis artifacts. The released default is
1 ohm of resistance and 0.5 fF of capacitance per cross-tier connection.

`flow_config.py` resolves validated paths and platform settings for all steps.
`partition_contract.py` defines the coarse-partition contract used before the
numbered sequence begins. The numbered scripts import only the modules required
by their own stage.
