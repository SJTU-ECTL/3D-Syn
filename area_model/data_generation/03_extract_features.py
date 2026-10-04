#!/usr/bin/env python3
"""Extract graph features from synthesized area-model netlists.

The output JSON preserves the feature ordering and graph construction consumed
by ``area_model/dataset.py`` and ``area_model/predict_util.py``.  Filenames
encode the five-slot bit-width vector:

    DFF_8_8_0_0_0.v    -> [8, 8, 0, 0, 0]
    FA_4_4_4_4_4.v     -> [4, 4, 4, 4, 4]
    ADD_8_16_32_0_0.v  -> [8, 16, 32, 0, 0]

Batch mode processes one complete synthesis group.  ``--target`` selects one
existing netlist, using ``MODULE W`` for ``ff_fa`` and ``MODULE A B Y`` for an
arithmetic group.  Valid JSON is skipped unless ``--force`` is supplied.

Example:
    python 03_extract_features.py arith_same --target ADD 2 2 2 \
      --technology asap7 --platform-dir path/to/platform \
      --data-root path/to/work
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import TRAINING_TECHNOLOGIES, VECTOR_LEN, resolve_training_platform
import predict_util


GROUPS = ("ff_fa", "arith_same", "arith_rand")


def parse_bitwidth_vec(stem: str) -> list[int]:
    """Read the fixed-length width suffix from a wrapper name."""
    tokens = stem.split("_")
    values = []
    for token in reversed(tokens):
        if not token.isdigit():
            break
        values.append(int(token))
    values.reverse()
    if len(values) != VECTOR_LEN:
        raise ValueError(
            f"wrapper {stem!r} must end in {VECTOR_LEN} numeric width slots"
        )
    return values


def locate_target(netlist_root: Path, group: str, values: list[str]) -> Path:
    expected = 1 if group == "ff_fa" else 3
    if len(values) != expected + 1:
        syntax = "MODULE W" if expected == 1 else "MODULE A B Y"
        raise ValueError(f"--target for {group} expects {syntax}")
    module = values[0].upper()
    widths = [int(value) for value in values[1:]]
    prefix = f"{module}_" + "_".join(str(value) for value in widths)
    directory = netlist_root / group / module
    matches = sorted(directory.glob(f"{prefix}_*.v"))
    exact = directory / f"{prefix}.v"
    if exact.is_file():
        matches.append(exact)
    matches = sorted(set(matches))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected one netlist matching {directory / (prefix + '*.v')}, "
            f"found {len(matches)}"
        )
    return matches[0]


def write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def process_one(task: tuple) -> tuple[str, str, str]:
    (netlist, netlist_root, feature_root, status_root,
     cell_areas, technology, force) = task
    relative = netlist.relative_to(netlist_root)
    output = feature_root / relative.with_suffix(".json")
    status_file = status_root / relative.with_suffix(".json")
    wrapper = netlist.stem
    base_status = {
        "stage": "feature_extraction",
        "technology": technology,
        "sample": wrapper,
        "input": str(netlist),
        "output": str(output),
    }
    if output.is_file() and not force:
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
            if existing.get("nodes") and existing.get("graph_features"):
                write_status(status_file, {**base_status, "status": "skipped"})
                return wrapper, "skipped", str(output)
        except (OSError, json.JSONDecodeError):
            pass
    try:
        # Yosys preserves the thin generated wrapper and emits the mapped
        # parameterized implementation as a second module.  Select the unique
        # module containing standard cells, matching the original extractor.
        module = predict_util.parse_netlist(netlist, cell_areas)
        graph, net_to_instances, edge_index = predict_util.build_cell_graph(module)
        payload = {
            "feature_schema_version": 1,
            "technology": technology,
            "bitwidth_vec": parse_bitwidth_vec(wrapper),
            "graph_features": predict_util.extract_graph_features(
                module, graph, net_to_instances, cell_areas
            ),
            "nodes": predict_util.extract_node_features(module, graph, cell_areas),
            "edge_index": edge_index,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        write_status(status_file, {**base_status, "status": "ok"})
        return wrapper, "ok", str(output)
    except Exception as error:
        write_status(status_file, {**base_status, "status": "failed",
                                   "error": str(error)})
        return wrapper, "failed", str(error)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract graph features from mapped netlists."
    )
    parser.add_argument("group", choices=GROUPS, help="netlist group to process")
    parser.add_argument("--target", nargs="+", metavar=("MODULE", "WIDTH"),
                        help="one sample: MODULE W or MODULE A B Y")
    parser.add_argument("--technology", choices=sorted(TRAINING_TECHNOLOGIES),
                        required=True, help="mapped-netlist process")
    parser.add_argument("--platform-dir", type=Path, required=True,
                        help="TaiWei hybrid platform directory")
    parser.add_argument("--data-root", type=Path, required=True,
                        help="external generation root")
    parser.add_argument("--workers", type=int, default=1,
                        help="parallel feature workers (default: 1)")
    parser.add_argument("--force", action="store_true",
                        help="replace existing feature JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.workers <= 0:
        raise ValueError("workers must be positive")
    platform = resolve_training_platform(args.platform_dir, args.technology)
    cell_areas = predict_util.parse_lib_areas(list(platform["liberties"]))
    data_root = args.data_root.expanduser().resolve()
    netlist_root = data_root / "02_netlist" / args.technology
    if args.target:
        netlists = [locate_target(netlist_root, args.group, args.target)]
    else:
        group_root = netlist_root / args.group
        netlists = sorted(group_root.rglob("*.v")) if group_root.is_dir() else []
    if not netlists:
        raise FileNotFoundError(f"no netlists found for group {args.group}")

    feature_root = data_root / "03_features" / args.technology
    status_root = data_root / "status/03_features" / args.technology
    tasks = [
        (path, netlist_root, feature_root, status_root,
         cell_areas, args.technology, args.force)
        for path in netlists
    ]
    counts = {"ok": 0, "skipped": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for wrapper, state, detail in executor.map(process_one, tasks):
            counts[state] += 1
            print(f"[{state}] {wrapper}: {detail}")
    print("Done. " + " ".join(f"{key}={value}" for key, value in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
