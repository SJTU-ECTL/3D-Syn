"""Shared paths and feature definitions for area-model inference and training.

All paths are derived from this source tree; external tools and TaiWei platform
files are supplied by each command-line entry point.
"""

from pathlib import Path


AREA_MODEL_ROOT = Path(__file__).resolve().parent
RESOURCES_DIR = AREA_MODEL_ROOT / "resources"
PRETRAINED_DIR = AREA_MODEL_ROOT / "pretrained"
DATA_DIR = AREA_MODEL_ROOT / "data"

SIMLIB_PATH = RESOURCES_DIR / "simlib.v"
PORT_SCHEMA_PATH = RESOURCES_DIR / "port_schema.json"
VOCAB_PATH = RESOURCES_DIR / "cell_type_vocab.json"
NANGATE45_SDC = RESOURCES_DIR / "virtual_clk_nangate45.sdc"
ASAP7_SDC = RESOURCES_DIR / "virtual_clk_asap7.sdc"

MODEL_FORMAT_VERSION = 1
FEATURE_SCHEMA_VERSION = 1
MAX_BITWIDTH = 32.0
VECTOR_LEN = 5
CELL_TYPE_COL = 8
NODE_FEAT_DIM = 9
GRAPH_FEAT_DIM = 17
BW_VEC_DIM = 5

NODE_FEAT_KEYS = (
    "cell_area",
    "cell_degree",
    "avg_neighbor_degree",
    "betweenness_centrality",
    "closeness_centrality",
    "degree_centrality",
    "clustering_coeff",
    "eccentricity",
)

GRAPH_FEAT_KEYS = (
    "n_cells",
    "n_nets",
    "n_pins",
    "n_nets_fanout_5_10",
    "n_nets_fanout_gt10",
    "n_internal_nets",
    "n_border_nets",
    "total_cell_area",
    "avg_cell_degree",
    "avg_net_degree",
    "avg_clustering_coeff",
    "density",
    "diameter",
    "radius",
    "edge_connectivity",
    "n_colors_greedy",
    "avg_global_efficiency",
)

ASAP7_LIBRARIES = (
    "lib_upper/NLDM/asap7sc7p5t_AO_RVT_FF_nldm_211120.upper.processed.lib",
    "lib_upper/NLDM/asap7sc7p5t_INVBUF_RVT_FF_nldm_220122.upper.processed.lib",
    "lib_upper/NLDM/asap7sc7p5t_OA_RVT_FF_nldm_211120.upper.processed.lib",
    "lib_upper/NLDM/asap7sc7p5t_SEQ_RVT_FF_nldm_220123.upper.processed.lib",
    "lib_upper/NLDM/asap7sc7p5t_SIMPLE_RVT_FF_nldm_211120.upper.processed.lib",
)

TECHNOLOGIES = {
    "bottom": {
        "platform": "nangate45",
        "checkpoint": "nangate45",
        "liberties": (
            "lib_bottom/NangateOpenCellLibrary_typical.bottom.processed.lib",
        ),
    },
    "upper": {
        "platform": "asap7",
        "checkpoint": "asap7",
        "liberties": ASAP7_LIBRARIES,
    },
}

# Offline data generation uses concrete process names.  The corresponding
# physical views live in the bottom and upper tiers of TaiWei's hybrid
# ASAP7/Nangate45 platform, while online inference keeps its historical
# bottom/upper interface for flow compatibility.
TRAINING_TECHNOLOGIES = {
    "nangate45": {
        "tier": "bottom",
        "liberties": TECHNOLOGIES["bottom"]["liberties"],
        "tech_lef": "lef/asap7_nangate45_2A6M10M.lef",
        "cell_lef": (
            "lef_bottom/"
            "NangateOpenCellLibrary.macro.mod.bottom.processed.lef"
        ),
        "sdc": NANGATE45_SDC,
        "abc_liberty": TECHNOLOGIES["bottom"]["liberties"][0],
        "dff_liberty": TECHNOLOGIES["bottom"]["liberties"][0],
        "abc_fast": False,
    },
    "asap7": {
        "tier": "upper",
        "liberties": TECHNOLOGIES["upper"]["liberties"],
        "tech_lef": "lef/asap7_nangate45_2A6M10M.lef",
        "cell_lef": (
            "lef_upper/"
            "asap7sc7p5t_28_R_1x_220121a.upper.processed.lef"
        ),
        "sdc": ASAP7_SDC,
        "abc_liberty": ASAP7_LIBRARIES[-1],
        "dff_liberty": ASAP7_LIBRARIES[3],
        "abc_fast": True,
    },
}


def resolve_training_platform(platform_dir: Path, technology: str) -> dict:
    """Resolve the TaiWei Liberty and LEF views used by offline generation."""
    if technology not in TRAINING_TECHNOLOGIES:
        choices = ", ".join(sorted(TRAINING_TECHNOLOGIES))
        raise ValueError(f"technology must be one of: {choices}")

    root = Path(platform_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"platform directory not found: {root}")
    definition = TRAINING_TECHNOLOGIES[technology]
    paths = {
        "platform_dir": root,
        "technology": technology,
        "tier": definition["tier"],
        "liberties": tuple(root / path for path in definition["liberties"]),
        "tech_lef": root / definition["tech_lef"],
        "cell_lef": root / definition["cell_lef"],
        "sdc": definition["sdc"],
        "abc_liberty": root / definition["abc_liberty"],
        "dff_liberty": root / definition["dff_liberty"],
        "abc_fast": definition["abc_fast"],
    }
    required_files = list(paths["liberties"]) + [
        paths["tech_lef"], paths["cell_lef"], paths["sdc"],
        paths["abc_liberty"], paths["dff_liberty"],
    ]
    missing = [path for path in required_files if not path.is_file()]
    if missing:
        formatted = "\n  ".join(str(path) for path in missing)
        raise FileNotFoundError(f"missing platform files:\n  {formatted}")
    return paths
