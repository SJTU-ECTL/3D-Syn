# Physical Design Backend

After validating the backend handoff, `flow/run_backend.sh` runs the selected
TaiWei physical-design flow stage by stage. `BACKEND=ord` selects OpenROAD and
`BACKEND=cds` selects Innovus. The scripts in this directory provide the
3D-Syn-specific pre-placement and 3D-floorplan behavior used by those flows.

## Backend sequence

The ORD path performs 3D floorplanning, IO placement, split-net preparation,
per-tier macro placement, PDN generation, placement, staged pre-CTS
optimization, legalization, CTS, routing, and final reporting.

The CDS path follows the same handoff and tier-placement structure through
Innovus, then performs legalization, CTS, routing, and final design restore.
The exact make target for every stage is listed by `flow/run_backend.sh` and is
recorded in the run's `backend_status` directory.

`BACKEND_START_STAGE` and `BACKEND_STOP_STAGE` can resume or stop at an exact
backend target. The alias `3d-floorplan` selects the first backend stage and
`final` selects the last stage. `OUTER_ITERATIONS` controls the number of
upper-tier/bottom-tier staged pre-CTS placement iterations.

## Layout

```text
backend/
├── ord/
│   ├── floorplan.tcl
│   └── floorplan_3d.tcl
└── cds/
    ├── innovus_preplace.tcl
    └── innovus_3d_floorplan.tcl
```

| File | Responsibility |
| --- | --- |
| `ord/floorplan.tcl` | OpenROAD 2D floorplan and optional topology-changing pre-place optimization |
| `ord/floorplan_3d.tcl` | OpenROAD 3D core sizing and floorplan initialization |
| `cds/innovus_preplace.tcl` | Innovus 2D pre-place with a topology-preserving mode |
| `cds/innovus_3d_floorplan.tcl` | Innovus 3D core sizing, HBT-capacity adjustment, and floorplan output |

The TaiWei make overlay exposes the internal adapter targets
`3dsyn-ord-preplace`, `3dsyn-cds-preplace`,
`3dsyn-ord-3d-floorplan`, and `3dsyn-cds-3d-floorplan`. The backend driver
selects these targets; users normally do not invoke them directly.

## Pre-place optimization

`PREPLACE_OPT=0` preserves the synthesized instance/master structure wherever
the selected tool permits it. `PREPLACE_OPT=1` enables the native optimization
steps and requires the downstream partition contract to account for allowed
changes. ORD and CDS adapters implement the same public meaning even though
their tool commands differ.

## Fixed 3D core area

When `FIXED_CORE_AREA` is unset, the backend uses TaiWei's automatic tier-area
sizing. When set, it is a positive area in µm² used with the configured aspect
ratio `height / width` to derive the same requested dimensions for ORD and CDS:

```text
width  = sqrt(area / aspect_ratio)
height = width * aspect_ratio
```

Both adapters report the sizing mode, requested area, and final dimensions.
They also write the requested/planned and tool-snapped dimensions to the
`floorplan-3d` handoff manifest. Small ORD/CDS differences after snapping are
expected because each tool aligns the requested box to its own placement grid.
The CDS adapter also records any expansion required by an explicitly enabled
HBT-capacity rule.

## Constraints

Both backend paths consume the explicit `DESIGN_SDC` selected by the root
interface. Backend scripts may create stage-local SDC derivatives below
`RUN_DIR`, but must not read an unrelated SDC implicitly from TaiWei's design
tree.

These files are selected by the make overlay and remain outside the TaiWei
checkout. The Cadence scripts retain the original attribution and permission
notice from their TaiWei sources.
