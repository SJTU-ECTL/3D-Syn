# 3D-Syn

3D-Syn is a 3D-aware synthesis flow for face-to-face (F2F) 3D ICs. It
constructs a coarse-grained netlist for tier partitioning and uses a graph
neural network (GNN) to estimate each unit's post-route area for the target
technology. After partitioning, it synthesizes each tier with its corresponding
library while accounting for cross-tier interconnect delay.

3D-Syn is implemented based on [TaiWei](https://github.com/CODA-Team/TaiWei), an open-source 3D IC design flow.

## Repository Layout

```text
3D-Syn/
├── run_3d_syn.sh             Complete 3D-Syn flow launcher
├── run_baseline.sh           Comparison baseline launcher
├── Makefile                  Flow execution interface
├── configs/                  Technology and design configurations
│   ├── asap7_3D/
│   ├── nangate45_3D/
│   └── asap7_nangate45_3D/
├── flow/                     3D-Syn flow implementation
│   ├── coarse_synth/         Coarse-grained netlist construction
│   ├── coarse_partition/     Coarse-grained tier partitioning
│   ├── per_tier_synth/       Per-tier synthesis
│   ├── backend_handoff/      Backend netlist, partition, and DEF generation
│   ├── backend/              OpenROAD and Innovus physical-design backend
│   ├── baseline/             Comparison baseline implementation
│   ├── yosys_json_toolkit/   Yosys JSON netlist processing
│   ├── env_isolation/        Python and EDA environment isolation
│   ├── run_frontend.sh       Frontend flow
│   └── run_backend.sh        Backend flow
├── area_model/               Routability-aware area estimation model
│   ├── data_generation/      Offline training-data generation pipeline
│   ├── pretrained/           Released model checkpoints
│   ├── config.py             Model and feature configuration
│   ├── dataset.py            Graph dataset representation
│   ├── model.py              Model architecture
│   ├── predict_util.py       Model inference
│   └── train.py              Model training
├── yosys-plugin/             Custom Yosys passes used by 3D-Syn
└── taiwei/
    └── 3dsyn.mk              Non-invasive TaiWei flow integration
```

## 3D-Syn Flow

| Stage | Purpose | Documentation |
| --- | --- | --- |
| 1. Coarse-Grained Netlist Construction | Construct a coarse-grained netlist that preserves arithmetic units and multi-bit registers for tier partitioning. | [Coarse-Grained Netlist Construction](flow/coarse_synth/README.md) |
| 2. Tier Partitioning | Perform tier partitioning on the coarse-grained netlist using technology-dependent area estimates. | [Tier Partitioning](flow/coarse_partition/README.md) |
| 3. Per-Tier Synthesis | Synthesize each tier with its target technology while accounting for cross-tier interconnect cost. | [Per-Tier Synthesis](flow/per_tier_synth/README.md) |
| 4. Backend Handoff | Convert the synthesis and partitioning results into the netlist and physical-design inputs required by the backend. | [Backend Handoff](flow/backend_handoff/README.md) |
| 5. Physical Design | Run the TaiWei OpenROAD or Innovus backend for 3D physical implementation and evaluation. | [Physical Design Backend](flow/backend/README.md) |

## Requirements

- Python >= 3.10 (tested with 3.12)
- TaiWei (ab56e8590946f2f335fd1ac315f69114f66a12ba)
- Cadence Genus (tested with v21.39)
- Cadence Innovus (tested with v21.39)

## Installation

### Clone and Build

```bash
git clone https://github.com/Hywel17/3D-Syn.git
git clone --recurse-submodules https://github.com/CODA-Team/TaiWei.git
git -C TaiWei checkout ab56e8590946f2f335fd1ac315f69114f66a12ba
git -C TaiWei submodule update --init --recursive

# Build TaiWei
cd TaiWei/ORFS-Research
./build_openroad.sh --local

# Build 3D-Syn Yosys plugin
cd 3D-Syn
make plugin
```

### Install Python dependencies

```bash
pip install networkx==3.4.2 numpy==1.26.4 torch==2.5.1
```

### Configure tool paths

```bash
export SYN3D_TAIWEI_HOME=/path/to/TaiWei
export SYN3D_GENUS_EXE=/path/to/genus
export SYN3D_INNOVUS_EXE=/path/to/innovus
```

## Run 3D-Syn & baseline

```bash
./run_3d_syn.sh
./run_baseline.sh
```

Outputs are written to:

```text
3D-Syn/runs/<RUN_TAG>/
├── logs/
├── objects/
├── reports/
└── results/
```
