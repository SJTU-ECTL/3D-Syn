#!/usr/bin/env python3
"""Merge extracted features and successful P&R labels into training samples.

For each group, the command writes a tabular ``dataset.csv`` and one PyTorch
graph file per accepted sample.  It rejects missing/failed P&R results, unknown
cell types, empty graphs, invalid feature dimensions and NaN/Inf values.

Utilization filtering follows the original policy:

* values below ``--util-min`` are discarded;
* values through 1.0 are retained;
* values from 1.0 through ``--util-clip-max`` are clipped to 1.0; and
* larger values are treated as anomalies and discarded.

After each invocation, ``splits.json`` is rebuilt across every available group
for the selected process.  Splitting uses a fixed seed and wrapper identity, so
samples derived from the same source wrapper cannot leak across train,
validation and test sets.

Example:
    python 05_build_dataset.py arith_same --technology asap7 \
      --data-root path/to/work --resources-dir path/to/work/00_resources \
      --seed 42
"""

import argparse
import csv
import json
import math
import random
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import (
    BW_VEC_DIM,
    FEATURE_SCHEMA_VERSION,
    GRAPH_FEAT_KEYS,
    NODE_FEAT_KEYS,
    TRAINING_TECHNOLOGIES,
)


GROUPS = ("ff_fa", "arith_same", "arith_rand")


def load_json(path: Path, label: str) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return value


def finite_tensor(value, label: str) -> torch.Tensor:
    tensor = torch.tensor(value, dtype=torch.float)
    if not torch.isfinite(tensor).all():
        raise ValueError(f"{label} contains NaN or Inf")
    return tensor


def write_split_manifest(dataset_root: Path, technology: str, seed: int,
                         train_frac: float, val_frac: float) -> Path:
    """Create a deterministic, source-grouped split over all built graphs."""
    graph_paths = sorted(dataset_root.glob("*/graphs/*.pt"))
    origins = {}
    for path in graph_paths:
        origins.setdefault(path.stem, []).append(path)
    origin_names = sorted(origins)
    random.Random(seed).shuffle(origin_names)
    n_origins = len(origin_names)
    n_train = int(n_origins * train_frac)
    n_val = int(n_origins * val_frac)
    if n_origins >= 3:
        n_train = min(max(n_train, 1), n_origins - 2)
        n_val = min(max(n_val, 1), n_origins - n_train - 1)
    split_origins = {
        "train": origin_names[:n_train],
        "validation": origin_names[n_train:n_train + n_val],
        "test": origin_names[n_train + n_val:],
    }
    splits = {
        name: [str(path.relative_to(dataset_root))
               for origin in names for path in origins[origin]]
        for name, names in split_origins.items()
    }
    manifest = {
        "format_version": 1,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "technology": technology,
        "seed": seed,
        "train_fraction": train_frac,
        "validation_fraction": val_frac,
        "source_key": "wrapper_name",
        "splits": splits,
        "origins": split_origins,
    }
    output = dataset_root / "splits.json"
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build PyTorch graph samples from features and P&R labels."
    )
    parser.add_argument("group", choices=GROUPS, help="sample group to build")
    parser.add_argument("--technology", choices=sorted(TRAINING_TECHNOLOGIES),
                        required=True, help="sample process")
    parser.add_argument("--data-root", type=Path, required=True,
                        help="external generation root")
    parser.add_argument("--resources-dir", type=Path, required=True,
                        help="directory containing cell_type_vocab.json")
    parser.add_argument("--util-min", type=float, default=0.3,
                        help="minimum accepted utilization (default: 0.3)")
    parser.add_argument("--util-clip-max", type=float, default=1.05,
                        help="largest utilization clipped to 1.0 (default: 1.05)")
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--val-frac", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true",
                        help="replace existing graph files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 <= args.util_min <= 1 or args.util_clip_max < 1:
        raise ValueError("util-min must be in [0,1] and util-clip-max must be >= 1")
    if not 0 < args.train_frac < 1 or args.val_frac < 0 \
            or args.train_frac + args.val_frac >= 1:
        raise ValueError("train-frac and val-frac must be non-negative and sum below 1")
    data_root = args.data_root.expanduser().resolve()
    resources_dir = args.resources_dir.expanduser().resolve()
    vocabulary = load_json(resources_dir / "cell_type_vocab.json", "cell vocabulary")
    identifiers = list(vocabulary.values())
    if len(set(identifiers)) != len(identifiers) or any(
            not isinstance(value, int) or value < 0 for value in identifiers):
        raise ValueError("cell vocabulary identifiers must be unique non-negative integers")

    feature_root = data_root / "03_features" / args.technology / args.group
    feature_files = sorted(feature_root.rglob("*.json")) if feature_root.is_dir() else []
    if not feature_files:
        raise FileNotFoundError(f"no feature files found under {feature_root}")
    pnr_root = data_root / "04_pnr" / args.technology / args.group
    pnr_status_root = data_root / "status/04_pnr" / args.technology / args.group
    dataset_root = data_root / "05_dataset" / args.technology
    output_dir = dataset_root / args.group
    graph_dir = output_dir / "graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)

    width_columns = [f"bw_{index}" for index in range(BW_VEC_DIM)]
    node_columns = [f"{key}_{stat}" for key in NODE_FEAT_KEYS
                    for stat in ("mean", "max", "min")]
    csv_header = (["module_name", "wrapper_name"] + width_columns
                  + list(GRAPH_FEAT_KEYS) + node_columns + ["utilization"])
    csv_rows = []
    counts = {
        "ok": 0, "skipped": 0, "failed": 0,
        "filtered_low": 0, "filtered_high": 0, "clipped": 0,
    }

    for feature_path in feature_files:
        module_name = feature_path.parent.name
        wrapper_name = feature_path.stem
        relative = feature_path.relative_to(feature_root)
        label_path = pnr_root / relative.parent / wrapper_name / "label.json"
        status_path = pnr_status_root / relative
        try:
            status = load_json(status_path, "PnR status")
            if status.get("status") not in ("ok", "skipped"):
                raise ValueError(f"PnR status is {status.get('status')!r}")
            feature = load_json(feature_path, "feature file")
            label = load_json(label_path, "P&R label")
            utilization = float(label["utilization"])
            if not math.isfinite(utilization) or utilization <= 0:
                raise ValueError(f"invalid utilization: {utilization}")
            if utilization < args.util_min:
                counts["filtered_low"] += 1
                continue
            if utilization > args.util_clip_max:
                counts["filtered_high"] += 1
                continue
            if utilization > 1.0:
                utilization = 1.0
                counts["clipped"] += 1

            graph_features = feature["graph_features"]
            nodes = feature["nodes"]
            edge_index = feature["edge_index"]
            bitwidth_vector = list(feature["bitwidth_vec"])
            if len(bitwidth_vector) != BW_VEC_DIM:
                raise ValueError(f"bit-width vector must have {BW_VEC_DIM} entries")
            if not nodes:
                raise ValueError("sample has no graph nodes")
            node_matrix = []
            for node in nodes:
                cell_type = node["cell_type"]
                if cell_type not in vocabulary:
                    raise ValueError(f"unknown cell type: {cell_type}")
                node_matrix.append(
                    [float(node[key]) for key in NODE_FEAT_KEYS]
                    + [float(vocabulary[cell_type])]
                )
            x = finite_tensor(node_matrix, "node features")
            graph_tensor = finite_tensor(
                [graph_features[key] for key in GRAPH_FEAT_KEYS],
                "graph features",
            )
            bitwidth_tensor = finite_tensor(bitwidth_vector, "bit-width vector")
            edge_tensor = torch.tensor(edge_index, dtype=torch.long)
            if tuple(edge_tensor.shape[:1]) != (2,):
                raise ValueError("edge_index must have shape [2, number_of_edges]")
            graph_output = graph_dir / f"{wrapper_name}.pt"
            if graph_output.exists() and not args.force:
                counts["skipped"] += 1
            else:
                torch.save({
                    "x": x,
                    "edge_index": edge_tensor,
                    "graph_feat": graph_tensor,
                    "bitwidth_vec": bitwidth_tensor,
                    "y": torch.tensor(round(utilization, 6), dtype=torch.float),
                    "name": wrapper_name,
                    "source_id": wrapper_name,
                    "technology": args.technology,
                    "feature_schema_version": FEATURE_SCHEMA_VERSION,
                }, graph_output)
                counts["ok"] += 1

            aggregates = {}
            for key in NODE_FEAT_KEYS:
                values = [float(node[key]) for node in nodes]
                aggregates[f"{key}_mean"] = sum(values) / len(values)
                aggregates[f"{key}_max"] = max(values)
                aggregates[f"{key}_min"] = min(values)
            csv_rows.append(
                [module_name, wrapper_name] + bitwidth_vector
                + [graph_features[key] for key in GRAPH_FEAT_KEYS]
                + [round(aggregates[key], 6) for key in node_columns]
                + [round(utilization, 6)]
            )
        except Exception as error:
            counts["failed"] += 1
            print(f"[failed] {wrapper_name}: {error}", file=sys.stderr)

    csv_path = output_dir / "dataset.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(csv_header)
        writer.writerows(csv_rows)
    split_path = write_split_manifest(
        dataset_root, args.technology, args.seed, args.train_frac, args.val_frac
    )
    summary = {
        "stage": "dataset",
        "technology": args.technology,
        "group": args.group,
        "feature_files": len(feature_files),
        "csv_rows": len(csv_rows),
        **counts,
    }
    summary_path = data_root / "status/05_dataset" / args.technology \
        / f"{args.group}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"CSV: {csv_path}")
    print(f"Graphs: {graph_dir}")
    print(f"Splits: {split_path}")
    print("Done. " + " ".join(f"{key}={value}" for key, value in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
