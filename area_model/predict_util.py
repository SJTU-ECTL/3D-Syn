#!/usr/bin/env python3
"""Predict coarse-unit utilization from a mapped standard-cell netlist.

The entry point validates its platform resources and checkpoint manifest before
building the graph features expected by the released GraphSAGE model.
"""

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import networkx as nx
import torch

if __package__:
    from .config import (
        BW_VEC_DIM, CELL_TYPE_COL, FEATURE_SCHEMA_VERSION, GRAPH_FEAT_DIM,
        GRAPH_FEAT_KEYS, MAX_BITWIDTH, MODEL_FORMAT_VERSION, NODE_FEAT_DIM,
        NODE_FEAT_KEYS, PRETRAINED_DIR, RESOURCES_DIR, TECHNOLOGIES, VECTOR_LEN,
    )
    from .model import UtilizationGNN
else:
    from config import (
        BW_VEC_DIM, CELL_TYPE_COL, FEATURE_SCHEMA_VERSION, GRAPH_FEAT_DIM,
        GRAPH_FEAT_KEYS, MAX_BITWIDTH, MODEL_FORMAT_VERSION, NODE_FEAT_DIM,
        NODE_FEAT_KEYS, PRETRAINED_DIR, RESOURCES_DIR, TECHNOLOGIES, VECTOR_LEN,
    )
    from model import UtilizationGNN


class PredictionError(RuntimeError):
    """An inference input or runtime artifact violates the released contract."""


def require_file(path: Path, label: str) -> Path:
    if not path.is_file() or path.stat().st_size == 0:
        raise PredictionError(f"{label} is missing or empty: {path}")
    return path


def load_json(path: Path, label: str) -> dict:
    require_file(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PredictionError(f"cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise PredictionError(f"{label} must contain a JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for block in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_hash(path: Path, metadata: dict, label: str) -> None:
    if not isinstance(metadata, dict) or "sha256" not in metadata:
        raise PredictionError(f"manifest is missing the SHA-256 value for {label}")
    actual = sha256_file(path)
    if actual != metadata["sha256"]:
        raise PredictionError(
            f"{label} checksum mismatch: expected {metadata['sha256']}, got {actual}"
        )
    expected_size = metadata.get("size_bytes")
    if expected_size is not None and path.stat().st_size != expected_size:
        raise PredictionError(f"{label} size does not match its manifest")


def validate_manifest(checkpoint_dir: Path, resources_dir: Path,
                      technology: str) -> dict:
    manifest = load_json(checkpoint_dir / "manifest.json", "checkpoint manifest")
    expected_values = {
        "manifest_version": 1,
        "model_format_version": MODEL_FORMAT_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "platform": TECHNOLOGIES[technology]["platform"],
    }
    for key, expected in expected_values.items():
        if manifest.get(key) != expected:
            raise PredictionError(
                f"manifest {key} must be {expected!r}, got {manifest.get(key)!r}"
            )
    manifest_technology = manifest.get("technology")
    expected_process = TECHNOLOGIES[technology]["platform"]
    if manifest_technology not in (technology, expected_process):
        raise PredictionError(
            f"manifest technology must be {technology!r} or {expected_process!r}, "
            f"got {manifest_technology!r}"
        )
    if manifest_technology == expected_process \
            and manifest.get("inference_technology") != technology:
        raise PredictionError(
            f"manifest inference_technology must be {technology!r}"
        )
    if manifest.get("model_class") != "UtilizationGNN":
        raise PredictionError("manifest model_class must be 'UtilizationGNN'")
    minimum_torch = tuple(
        int(component) for component in manifest.get("pytorch_min_version", "").split(".")
        if component.isdigit()
    )
    current_torch = tuple(
        int(component) for component in torch.__version__.split("+")[0].split(".")[:2]
    )
    if len(minimum_torch) < 2 or current_torch < minimum_torch[:2]:
        raise PredictionError(
            f"checkpoint requires PyTorch {manifest.get('pytorch_min_version')} or newer"
        )

    features = manifest.get("features", {})
    expected_features = {
        "node_feat_dim": NODE_FEAT_DIM,
        "graph_feat_dim": GRAPH_FEAT_DIM,
        "bitwidth_vec_dim": BW_VEC_DIM,
        "max_bitwidth": MAX_BITWIDTH,
        "cell_type_column": CELL_TYPE_COL,
        "node_feature_keys": list(NODE_FEAT_KEYS),
        "graph_feature_keys": list(GRAPH_FEAT_KEYS),
    }
    for key, expected in expected_features.items():
        if features.get(key) != expected:
            raise PredictionError(f"manifest feature contract mismatch for {key}")

    checkpoint_files = manifest.get("checkpoint_files", {})
    for filename in ("best_model.pt", "norm_stats.pt", "config.json"):
        path = require_file(checkpoint_dir / filename, "checkpoint file")
        validate_hash(path, checkpoint_files.get(filename), filename)

    resource_files = manifest.get("resources", {})
    for filename in ("cell_type_vocab.json", "port_schema.json"):
        path = require_file(resources_dir / filename, "area-model resource")
        validate_hash(path, resource_files.get(filename), filename)
    return manifest


def validate_resources(schema: dict, vocabulary: dict) -> None:
    if not schema:
        raise PredictionError("port schema is empty")
    if not vocabulary:
        raise PredictionError("cell-type vocabulary is empty")
    identifiers = list(vocabulary.values())
    if any(not isinstance(value, int) or value < 0 for value in identifiers):
        raise PredictionError("cell-type identifiers must be non-negative integers")
    if len(set(identifiers)) != len(identifiers):
        raise PredictionError("cell-type vocabulary contains duplicate identifiers")


def parse_lib_areas(lib_paths: list) -> dict:
    areas        = {}
    current_cell = None
    depth        = 0
    for lib_path in lib_paths:
        with open(lib_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if current_cell is None:
                    m = re.match(r'^cell\s*\(\s*(\S+?)\s*\)\s*\{', line)
                    if m:
                        current_cell = m.group(1)
                        depth = 1
                    continue
                depth += line.count("{") - line.count("}")
                m = re.match(r'^area\s*:\s*([0-9.eE+\-]+)\s*;', line)
                if m:
                    areas[current_cell] = float(m.group(1))
                if depth <= 0:
                    current_cell = None
                    depth = 0
    if not areas:
        raise PredictionError("no cell areas were found in the selected Liberty files")
    return areas


# Mapped-netlist parsing
def parse_netlist(v_path: Path, cell_areas: dict,
                  wrapper_name: str | None = None) -> dict:
    require_file(v_path, "mapped Verilog netlist")
    src = v_path.read_text(encoding="utf-8", errors="replace")
    src = re.sub(r'\(\*.*?\*\)', '', src, flags=re.DOTALL)
    src = re.sub(r'//[^\n]*', '', src)

    modules = {}
    for m in re.finditer(
        r'module\s+([\w\\$=\']+)\s*\((.*?)\)\s*;(.*?)endmodule', src, re.DOTALL
    ):
        mod_name = m.group(1)
        body     = m.group(3)

        ports = {}
        for direction in ("input", "output"):
            for pm in re.finditer(
                rf'\b{direction}\b\s+(?:\[(\d+):(\d+)\]\s+)?(\w+)\s*;', body
            ):
                hi = int(pm.group(1)) if pm.group(1) is not None else 0
                lo = int(pm.group(2)) if pm.group(2) is not None else 0
                ports[pm.group(3)] = {"dir": direction, "width": abs(hi - lo) + 1}

        port_nets = set(ports.keys())
        for pname, pinfo in ports.items():
            if pinfo["width"] > 1:
                for i in range(pinfo["width"]):
                    port_nets.add(f"{pname}[{i}]")

        instances = []
        for im in re.finditer(
            r'([\w\\$=\']+)\s+(\w+)\s*\((.*?)\)\s*;', body, re.DOTALL
        ):
            cell_type = im.group(1)
            if cell_type in ("module", "input", "output", "wire",
                             "assign", "reg", "parameter"):
                continue
            if cell_type == mod_name:
                continue
            pins = {
                pm.group(1): pm.group(2)
                for pm in re.finditer(
                    r'\.(\w+)\s*\(\s*([\w\[\]]+)\s*\)', im.group(3)
                )
            }
            instances.append({"cell": cell_type, "inst": im.group(2), "pins": pins})

        modules[mod_name.lstrip("\\")] = {
            "ports": ports, "port_nets": port_nets, "instances": instances,
        }

    requested_name = wrapper_name.lstrip("\\") if wrapper_name else None
    if requested_name and requested_name in modules:
        candidates = [modules[requested_name]]
    else:
        candidates = [
            module for module in modules.values()
            if any(inst["cell"] in cell_areas for inst in module["instances"])
        ]
    if len(candidates) != 1:
        raise PredictionError(
            f"expected one standard-cell module in {v_path}, found {len(candidates)}"
        )
    if not candidates[0]["instances"]:
        raise PredictionError("the selected netlist module contains no standard cells")
    return candidates[0]


# Cell graph construction
def build_cell_graph(module: dict):
    instances    = module["instances"]
    G            = nx.Graph()
    G.add_nodes_from(range(len(instances)))
    net_to_insts = {}

    for idx, inst in enumerate(instances):
        for net in inst["pins"].values():
            net_to_insts.setdefault(net, []).append(idx)

    for inst_list in net_to_insts.values():
        for i in range(len(inst_list)):
            for j in range(i + 1, len(inst_list)):
                G.add_edge(inst_list[i], inst_list[j])

    src, dst = [], []
    for u, v in G.edges():
        src += [u, v]
        dst += [v, u]

    return G, net_to_insts, [src, dst]


# Model feature extraction
def extract_graph_features(module: dict, G: nx.Graph,
                            net_to_insts: dict, cell_areas: dict) -> dict:
    instances   = module["instances"]
    port_nets   = module["port_nets"]
    n_cells     = len(instances)
    net_degrees = []
    n_internal = n_border = fanout_5_10 = fanout_gt10 = 0

    for net, inst_list in net_to_insts.items():
        degree = len(inst_list)
        net_degrees.append(degree)
        if net in port_nets:
            n_border += 1
        else:
            n_internal += 1
        if 5 <= degree <= 10:
            fanout_5_10 += 1
        elif degree > 10:
            fanout_gt10 += 1

    if not instances:
        raise PredictionError("cannot extract features from an empty cell graph")
    total_cell_area = sum(cell_areas.get(inst["cell"], 0.0) for inst in instances)
    n_pins          = sum(len(inst["pins"]) for inst in instances)
    avg_cell_degree = sum(d for _, d in G.degree()) / n_cells if n_cells else 0.0
    avg_net_degree  = sum(net_degrees) / len(net_degrees) if net_degrees else 0.0
    avg_global_eff  = nx.global_efficiency(G)
    avg_clust_coeff = nx.average_clustering(G)
    density         = nx.density(G)
    coloring        = nx.coloring.greedy_color(G, strategy="largest_first")
    n_colors        = max(coloring.values()) + 1 if coloring else 0

    ref_G = G if nx.is_connected(G) else \
            G.subgraph(max(nx.connected_components(G), key=len)).copy()

    return {
        "n_cells":               n_cells,
        "n_nets":                len(net_to_insts),
        "n_pins":                n_pins,
        "n_nets_fanout_5_10":    fanout_5_10,
        "n_nets_fanout_gt10":    fanout_gt10,
        "n_internal_nets":       n_internal,
        "n_border_nets":         n_border,
        "total_cell_area":       round(total_cell_area, 6),
        "avg_cell_degree":       round(avg_cell_degree, 6),
        "avg_net_degree":        round(avg_net_degree, 6),
        "avg_clustering_coeff":  round(avg_clust_coeff, 6),
        "density":               round(density, 6),
        "diameter":              nx.diameter(ref_G),
        "radius":                nx.radius(ref_G),
        "edge_connectivity":     nx.edge_connectivity(ref_G),
        "n_colors_greedy":       n_colors,
        "avg_global_efficiency": round(avg_global_eff, 6),
    }


def extract_node_features(module: dict, G: nx.Graph, cell_areas: dict) -> list:
    instances = module["instances"]
    if not instances:
        raise PredictionError("cannot extract features from an empty cell graph")
    n_cells   = len(instances)
    ref_G     = G if nx.is_connected(G) else \
                G.subgraph(max(nx.connected_components(G), key=len)).copy()
    ecc         = nx.eccentricity(ref_G)
    closeness   = nx.closeness_centrality(G)
    betweenness = nx.betweenness_centrality(G)
    clustering  = nx.clustering(G)
    avg_nbr_deg = nx.average_neighbor_degree(G)

    nodes = []
    for idx, inst in enumerate(instances):
        degree = G.degree(idx)
        nodes.append({
            "cell_type":              inst["cell"],
            "cell_area":              round(cell_areas.get(inst["cell"], 0.0), 6),
            "cell_degree":            degree,
            "avg_neighbor_degree":    round(avg_nbr_deg.get(idx, 0.0), 6),
            "betweenness_centrality": round(betweenness.get(idx, 0.0), 6),
            "closeness_centrality":   round(closeness.get(idx, 0.0), 6),
            "degree_centrality":      round(degree / (n_cells - 1), 6) if n_cells > 1 else 0.0,
            "clustering_coeff":       round(clustering.get(idx, 0.0), 6),
            "eccentricity":           ecc.get(idx, -1),
        })
    return nodes


def canonical_type(wrapper_name: str) -> str:
    """Extract the canonical type token from an RTLIL wrapper name."""
    parts = [p for p in wrapper_name.lstrip("\\_").split("_") if p]
    if not parts:
        raise PredictionError(
            f"cannot extract a canonical type from wrapper name {wrapper_name!r}"
        )
    return parts[0].upper()


def build_bitwidth_vec(ctype: str, netlist_ports: dict, schema: dict) -> list:
    """Build the schema-ordered, zero-padded wrapper bit-width vector."""
    if ctype not in schema:
        raise PredictionError(f"canonical type {ctype!r} is absent from the port schema")
    entry = schema[ctype]

    param_ports = sorted(entry["parameterized_ports"], key=lambda p: p["slot_idx"])
    fixed_names = {p["name"] for p in entry.get("fixed_ports", [])}

    schema_port_names  = {p["name"] for p in param_ports} | fixed_names
    netlist_port_names = set(netlist_ports.keys())

    if schema_port_names != netlist_port_names:
        raise PredictionError(
            f"port schema mismatch for {ctype}: expected "
            f"{sorted(schema_port_names)}, got {sorted(netlist_port_names)}"
        )

    vec = [0] * VECTOR_LEN
    for p in param_ports:
        idx  = p["slot_idx"]
        name = p["name"]
        if not 0 <= idx < VECTOR_LEN:
            raise PredictionError(
                f"port-schema slot {idx} is invalid for {ctype}.{name}"
            )
        vec[idx] = netlist_ports[name]["width"]

    return vec


# Normalization shared with dataset.py
def validate_norm_stats(norm_stats: dict) -> None:
    expected_shapes = {
        "x_mean": (NODE_FEAT_DIM,),
        "x_std": (NODE_FEAT_DIM,),
        "gf_mean": (GRAPH_FEAT_DIM,),
        "gf_std": (GRAPH_FEAT_DIM,),
    }
    if not isinstance(norm_stats, dict):
        raise PredictionError("normalization statistics must be a dictionary")
    for key, expected_shape in expected_shapes.items():
        tensor = norm_stats.get(key)
        if not isinstance(tensor, torch.Tensor) or tuple(tensor.shape) != expected_shape:
            raise PredictionError(
                f"normalization tensor {key} must have shape {expected_shape}"
            )
        if not torch.isfinite(tensor).all():
            raise PredictionError(f"normalization tensor {key} contains NaN or Inf")


def apply_norm(x: torch.Tensor, graph_feat: torch.Tensor,
               bitwidth_vec: torch.Tensor, norm_stats: dict):
    validate_norm_stats(norm_stats)
    x_mean = norm_stats["x_mean"]
    x_std  = norm_stats["x_std"]
    x = x.clone()
    for col in range(NODE_FEAT_DIM):
        if col == CELL_TYPE_COL:
            continue
        std = x_std[col].item()
        if std < 1e-8:
            x[:, col] = x[:, col] - x_mean[col]
        else:
            x[:, col] = (x[:, col] - x_mean[col]) / std

    gf_mean = norm_stats["gf_mean"]
    gf_std  = norm_stats["gf_std"]
    graph_feat = graph_feat.clone()
    for i in range(GRAPH_FEAT_DIM):
        std = gf_std[i].item()
        if std < 1e-8:
            graph_feat[i] = graph_feat[i] - gf_mean[i]
        else:
            graph_feat[i] = (graph_feat[i] - gf_mean[i]) / std

    bitwidth_vec = bitwidth_vec / MAX_BITWIDTH
    return x, graph_feat, bitwidth_vec


def build_input_tensors(module: dict, wrapper_name: str, cell_areas: dict,
                        schema: dict, vocabulary: dict):
    unknown_cells = sorted({
        instance["cell"] for instance in module["instances"]
        if instance["cell"] not in vocabulary
    })
    if unknown_cells:
        raise PredictionError(
            "mapped netlist contains cells absent from the released "
            f"library/vocabulary: {', '.join(unknown_cells)}"
        )

    ctype = canonical_type(wrapper_name)
    bw_vec_list = build_bitwidth_vec(ctype, module["ports"], schema)
    graph, net_to_insts, edge_index = build_cell_graph(module)
    graph_feat_dict = extract_graph_features(
        module, graph, net_to_insts, cell_areas
    )
    node_list = extract_node_features(module, graph, cell_areas)

    node_matrix = []
    for node in node_list:
        row = [float(node[key]) for key in NODE_FEAT_KEYS]
        row.append(float(vocabulary[node["cell_type"]]))
        node_matrix.append(row)

    x = torch.tensor(node_matrix, dtype=torch.float)
    edge_tensor = torch.tensor(edge_index, dtype=torch.long)
    graph_features = torch.tensor(
        [graph_feat_dict[key] for key in GRAPH_FEAT_KEYS], dtype=torch.float
    )
    bitwidth_vector = torch.tensor(bw_vec_list, dtype=torch.float)
    if x.ndim != 2 or x.shape[1] != NODE_FEAT_DIM:
        raise PredictionError("node feature tensor has an unexpected shape")
    if edge_tensor.ndim != 2 or edge_tensor.shape[0] != 2:
        raise PredictionError("edge tensor has an unexpected shape")
    return x, edge_tensor, graph_features, bitwidth_vector


def load_runtime(checkpoint_dir: Path, resources_dir: Path, technology: str):
    validate_manifest(checkpoint_dir, resources_dir, technology)
    schema = load_json(resources_dir / "port_schema.json", "port schema")
    vocabulary = load_json(
        resources_dir / "cell_type_vocab.json", "cell vocabulary"
    )
    validate_resources(schema, vocabulary)

    config = load_json(checkpoint_dir / "config.json", "checkpoint configuration")
    expected_config = {
        "model_format_version": MODEL_FORMAT_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "node_feat_dim": NODE_FEAT_DIM,
        "graph_feat_dim": GRAPH_FEAT_DIM,
        "bw_vec_dim": BW_VEC_DIM,
    }
    for key, expected in expected_config.items():
        if config.get(key) != expected:
            raise PredictionError(f"checkpoint configuration mismatch for {key}")

    try:
        norm_stats = torch.load(
            checkpoint_dir / "norm_stats.pt", weights_only=True, map_location="cpu"
        )
        state_dict = torch.load(
            checkpoint_dir / "best_model.pt", weights_only=True, map_location="cpu"
        )
    except Exception as error:
        raise PredictionError(f"cannot load checkpoint tensors: {error}") from error
    validate_norm_stats(norm_stats)

    model = UtilizationGNN(
        node_feat_dim=config["node_feat_dim"],
        graph_feat_dim=config["graph_feat_dim"],
        bw_vec_dim=config["bw_vec_dim"],
        hidden_dim=config["hidden_dim"],
        n_layers=config["n_layers"],
        mlp_dims=tuple(config["mlp_dims"]),
        dropout=config["dropout"],
    )
    try:
        model.load_state_dict(state_dict, strict=True)
    except RuntimeError as error:
        raise PredictionError(
            f"checkpoint does not match the model architecture: {error}"
        ) from error
    model.eval()
    return schema, vocabulary, norm_stats, model


def parse_args():
    parser = argparse.ArgumentParser(
        description="Predict post-route utilization for a single mapped netlist.")
    parser.add_argument("v_path",       type=Path,
                        help="Path to mapped Verilog netlist (.v)")
    parser.add_argument("wrapper_name", type=str,
                        help="RTLIL module name, e.g. _dffe_CLK1_D128_EN1_Q128")
    parser.add_argument("tech",         choices=sorted(TECHNOLOGIES),
                        help="bottom = NanGate45, upper = ASAP7")
    parser.add_argument(
        "--platform-dir", "--common-platform-dir", dest="platform_dir",
        required=True, type=Path,
        help="Path to TaiWei platforms/asap7_nangate45_3D",
    )
    parser.add_argument(
        "--checkpoint-root", type=Path, default=PRETRAINED_DIR,
        help="Directory containing asap7/ and nangate45/ checkpoints",
    )
    parser.add_argument(
        "--resources-dir", type=Path, default=RESOURCES_DIR,
        help="Directory containing the released vocabulary and port schema",
    )
    return parser.parse_args()


def predict(args) -> float:
    technology = TECHNOLOGIES[args.tech]
    platform_dir = args.platform_dir.resolve()
    checkpoint_dir = args.checkpoint_root.resolve() / technology["checkpoint"]
    resources_dir = args.resources_dir.resolve()
    lib_paths = [platform_dir / relative for relative in technology["liberties"]]
    for lib_path in lib_paths:
        require_file(lib_path, "platform Liberty file")

    schema, vocabulary, norm_stats, model = load_runtime(
        checkpoint_dir, resources_dir, args.tech
    )
    cell_areas = parse_lib_areas(lib_paths)
    module = parse_netlist(args.v_path.resolve(), cell_areas, args.wrapper_name)
    x, edge_index, graph_features, bitwidth_vector = build_input_tensors(
        module, args.wrapper_name, cell_areas, schema, vocabulary
    )
    x, graph_features, bitwidth_vector = apply_norm(
        x, graph_features, bitwidth_vector, norm_stats
    )
    if not all(torch.isfinite(tensor).all() for tensor in
               (x, graph_features, bitwidth_vector)):
        raise PredictionError("normalized model input contains NaN or Inf")

    batch_index = torch.zeros(x.shape[0], dtype=torch.long)
    with torch.no_grad():
        prediction = model(
            x, edge_index, graph_features.unsqueeze(0),
            bitwidth_vector.unsqueeze(0), batch_index,
        )["util_pred"].item()
    if not math.isfinite(prediction) or not 0.0 < prediction <= 1.0:
        raise PredictionError(f"model returned an invalid utilization: {prediction}")
    return prediction


def main():
    print(f"{predict(parse_args()):.6f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[ERROR] predict_util: {error}", file=sys.stderr)
        sys.exit(1)
