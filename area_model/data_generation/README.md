# Area-Model Data Generation

This optional offline workflow creates the graph samples and routed utilization
labels used to train the area model. It is independent of the root 3D-Syn
Makefile and does not change TaiWei platform files.

## Inputs and outputs

All generated content is placed below an explicit `--data-root` outside the
source tree:

```text
<data-root>/
├── 00_resources/                 generated vocabulary, schema, and patched LEF
├── 01_rtl/<group>/<module>/      parameterized RTL wrappers
├── 02_netlist/<technology>/...   Yosys mapped netlists and logs
├── 03_features/<technology>/...  graph-feature JSON
├── 04_pnr/<technology>/...       OpenROAD logs, DEFs, images, and label.json
├── 05_dataset/<technology>/
│   ├── <group>/dataset.csv
│   ├── <group>/graphs/*.pt
│   └── splits.json
└── status/<step>/<technology>/... per-sample or per-group status records
```

`<technology>` is always the concrete process name: `asap7` or `nangate45`.
Both use the corresponding views from TaiWei's hybrid
`asap7_nangate45_3D` platform. Tool executables and platform locations are
command-line arguments; they are not stored in project configuration.

Set local shell variables for concise commands:

```bash
export SYN3D_TAIWEI_HOME=/path/to/TaiWei
export PLATFORM_DIR="$SYN3D_TAIWEI_HOME/TaiWei-Pin-3D/platforms/asap7_nangate45_3D"
export YOSYS_BIN="$SYN3D_TAIWEI_HOME/ORFS-Research/tools/install/yosys/bin/yosys"
export OPENROAD_BIN="$SYN3D_TAIWEI_HOME/ORFS-Research/tools/install/OpenROAD/bin/openroad"
export DATA_ROOT=/path/to/area-model-work
export GENERATED_RESOURCES="$DATA_ROOT/00_resources"
```

## 00: prepare resources

Build a deterministic vocabulary from both process libraries:

```bash
python3 area_model/data_generation/00_build_cell_vocab.py \
  --platform-dir "$PLATFORM_DIR" \
  --output "$GENERATED_RESOURCES/cell_type_vocab.json"
```

Extract the wrapper-port schema from the published simulation library:

```bash
python3 area_model/data_generation/00_extract_port_schema.py \
  --simlib area_model/resources/simlib.v \
  --output "$GENERATED_RESOURCES/port_schema.json"
```

Create the run-local ASAP7 LEF required for detailed-routing pin access:

```bash
python3 area_model/data_generation/00_patch_cell_lef.py \
  "$PLATFORM_DIR/lef_upper/asap7sc7p5t_28_R_1x_220121a.upper.processed.lef" \
  "$GENERATED_RESOURCES/asap7_patched_cell.lef"
```

The patch command never modifies its input. Nangate45 does not require this
derived LEF.

## 01: generate RTL

The groups are `ff_fa`, `arith_same`, and `arith_rand`. Batch defaults preserve
the original 1-to-32 width range, 64 random points per arithmetic module, and
seed 42.

The published dataset contains 17 RTLIL unit types and 992 candidates for each
technology:

| Unit group | Types | Configurations per type | Candidates |
| --- | ---: | --- | ---: |
| Multi-bit registers | 9 | 32 shared-width configurations | 288 |
| Full adder | 1 | 32 shared-width configurations | 32 |
| Other arithmetic units | 7 | 32 same-width and 64 different-width configurations | 672 |
| Total | 17 | | 992 |

The multi-bit registers are `DFF`, `DFFE`, `DFFSR`, `DFFSRE`, `ADFF`, `ADFFE`,
`SDFF`, `SDFFE`, and `SDFFCE`. The full adder is `FA`. The other arithmetic
units are `ADD`, `SUB`, `MUL`, `DIV`, `MOD`, `ALU`, and `MACC`.

```bash
python3 area_model/data_generation/01_generate_rtl.py arith_same \
  --simlib area_model/resources/simlib.v \
  --port-schema "$GENERATED_RESOURCES/port_schema.json" \
  --data-root "$DATA_ROOT"
```

Use `--target MODULE W` for an `ff_fa` smoke case or
`--target MODULE A B Y` for one arithmetic case:

```bash
python3 area_model/data_generation/01_generate_rtl.py arith_same \
  --target ADD 2 2 2 \
  --simlib area_model/resources/simlib.v \
  --port-schema "$GENERATED_RESOURCES/port_schema.json" \
  --data-root "$DATA_ROOT"
```

## 02: synthesize with Yosys

Run each group once per process. The executable is supplied only at invocation:

```bash
python3 area_model/data_generation/02_run_synth.py arith_same \
  --technology asap7 \
  --platform-dir "$PLATFORM_DIR" \
  --yosys-bin "$YOSYS_BIN" \
  --data-root "$DATA_ROOT" \
  --workers 16
```

For a single case, append `--target ADD 2 2 2`.

## 03: extract features

Feature extraction reuses the same graph construction and feature definitions
as online inference:

```bash
python3 area_model/data_generation/03_extract_features.py arith_same \
  --technology asap7 \
  --platform-dir "$PLATFORM_DIR" \
  --data-root "$DATA_ROOT" \
  --workers 16
```

## 04: generate routed labels

Pass OpenROAD explicitly. For ASAP7, also pass the patched run-local cell LEF:

```bash
python3 area_model/data_generation/04_run_pnr.py arith_same \
  --technology asap7 \
  --platform-dir "$PLATFORM_DIR" \
  --cell-lef "$GENERATED_RESOURCES/asap7_patched_cell.lef" \
  --openroad-bin "$OPENROAD_BIN" \
  --data-root "$DATA_ROOT" \
  --threads 4 --workers 4
```

Nangate45 uses the platform cell LEF directly, so omit `--cell-lef`:

```bash
python3 area_model/data_generation/04_run_pnr.py arith_same \
  --technology nangate45 \
  --platform-dir "$PLATFORM_DIR" \
  --openroad-bin "$OPENROAD_BIN" \
  --data-root "$DATA_ROOT" \
  --threads 4 --workers 4
```

Append `--target ADD 2 2 2` to either command for a single case.

> **Note:** P&R uses `--placement-density 2.0` by default. If global routing
> fails because of congestion, rerun step 04 with `--placement-density 1.0`.

## 05: build datasets and splits

Run this command for every generated group and process:

```bash
python3 area_model/data_generation/05_build_dataset.py arith_same \
  --technology asap7 \
  --data-root "$DATA_ROOT" \
  --resources-dir "$GENERATED_RESOURCES" \
  --seed 42
```

The command excludes missing or failed P&R samples and rejects unknown cells,
empty graphs, incompatible feature dimensions, and non-finite values. It then
rebuilds `splits.json` across all available groups. Wrapper identity is the
source key, preventing one generated design from appearing in multiple splits.

## Resuming and replacing outputs

Steps 01 through 04 skip valid existing outputs and refresh their status files.
Use `--force` on a specific command to replace that command's outputs. Failed
samples have an explicit `failed` status and are never silently admitted into
the dataset. The scripts do not recursively remove a data root or platform
directory.
