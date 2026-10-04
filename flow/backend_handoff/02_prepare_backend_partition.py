#!/usr/bin/env python3
"""Reconcile backend pre-placement changes with the per-tier partition.

The entry converts both netlists through Yosys, classifies added instances, and
updates a workspace copy of the backend partition.
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

FLOW_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FLOW_DIR))

from yosys_json_toolkit import load_design, validate_design

from backend_partition_analysis import (
    analyze_added_instance,
    build_bit_connections,
    build_bit_netnames,
    build_cell_tiers,
    choose_tier,
    edge_cost,
    format_conns,
    format_counter,
    net_cost,
)
from backend_partition_rules import (
    BUFFER_LIKE_ALLOWLIST,
    DIE_TO_TIER,
    TIER_TO_DIE,
    base_master_for_mode,
    fail,
    load_map_json,
    load_pin_directions,
    norm_name,
    strip_tier_suffix,
    target_macro_for_mode,
)


def find_yosys(repo_root, requested):
    candidates = []
    if requested:
        candidates.append(Path(requested))
    for env_key in ("YOSYS_EXE", "YOSYS"):
        if os.environ.get(env_key):
            candidates.append(Path(os.environ[env_key]))
    which = shutil.which("yosys")
    if which:
        candidates.append(Path(which))
    candidates.append(repo_root.parent / "ORFS-Research" / "tools" / "install" / "yosys" / "bin" / "yosys")

    for candidate in candidates:
        path = candidate if candidate.is_absolute() else repo_root / candidate
        if path.is_file() and os.access(str(path), os.X_OK):
            return path.resolve()
    fail("Could not find yosys. Tried: {}".format([str(c) for c in candidates]))


def run_yosys_to_json(yosys, verilog_path, json_path, log_path):
    json_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    script_path = json_path.with_suffix(".ys")
    script_path.write_text(
        "read_verilog {}\n"
        "hierarchy -auto-top\n"
        "write_json {}\n".format(verilog_path, json_path),
        encoding="utf-8",
    )
    with log_path.open("w", encoding="utf-8") as log_fp:
        result = subprocess.run([str(yosys), "-q", "-s", str(script_path)], stdout=log_fp, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        fail("Yosys failed for {}. See {}".format(verilog_path, log_path))


def load_single_top(json_path):
    design = load_design(json_path)
    validate_design(design)
    if len(design.modules) != 1:
        fail("Expected exactly one module in {}, found {}".format(json_path, sorted(design.modules)))
    return next(iter(design.modules.values()))


def parse_partition(path):
    mapping = {}
    order = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        toks = line.split()
        if len(toks) < 2:
            fail("Malformed partition line {} in {}: {!r}".format(lineno, path, raw))
        die_s = toks[-1]
        if die_s not in ("0", "1"):
            fail("Unsupported partition die on line {} in {}: {!r}".format(lineno, path, raw))
        name = norm_name(toks[0])
        die = int(die_s)
        if name in mapping:
            fail("Duplicate partition key {!r} in {}".format(name, path))
        mapping[name] = die
        order.append(name)
    return mapping, order

def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def append_partition_entries(partition_path, entries):
    backup = partition_path.with_name("partition.before_preplace_update.txt")
    if not backup.exists():
        shutil.copy2(partition_path, backup)
    if entries:
        with partition_path.open("a", encoding="utf-8") as fp:
            if not partition_path.read_text(encoding="utf-8", errors="ignore").endswith("\n"):
                fp.write("\n")
            for inst, die in entries:
                fp.write("{} {}\n".format(inst, die))
    return backup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", required=True, type=Path)
    ap.add_argument("--enablement", required=True, choices=sorted(BUFFER_LIKE_ALLOWLIST))
    ap.add_argument("--design-nickname", required=True)
    ap.add_argument("--flow-root", required=True, type=Path)
    ap.add_argument("--objects-dir", required=True, type=Path)
    ap.add_argument("--log-dir", required=True, type=Path)
    ap.add_argument("--yosys", default="")
    ap.add_argument("--expect-unchanged", action="store_true")
    ap.add_argument("--fail-on-removed-non-buffer", action="store_true", default=True)
    args = ap.parse_args()

    repo_root = args.flow_root.resolve()
    results_dir = args.results_dir.resolve()
    objects_dir = args.objects_dir.resolve() / "partition_update_after_preplace"
    log_dir = args.log_dir.resolve() / "partition_update_after_preplace"
    objects_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    base_v = results_dir / "base.v"
    pre_v = results_dir / "2_2_floorplan_io.v"
    partition_txt = results_dir / "partition.txt"
    for path, label in ((base_v, "base.v"), (pre_v, "2_2_floorplan_io.v"), (partition_txt, "partition.txt")):
        if not path.is_file():
            fail("Required {} not found: {}".format(label, path))

    yosys = find_yosys(repo_root, args.yosys)
    base_json = objects_dir / "base.json"
    pre_json = objects_dir / "preplace.json"
    run_yosys_to_json(yosys, base_v, base_json, log_dir / "base_yosys.log")
    run_yosys_to_json(yosys, pre_v, pre_json, log_dir / "preplace_yosys.log")

    base_module = load_single_top(base_json)
    pre_module = load_single_top(pre_json)
    partition, _ = parse_partition(partition_txt)
    pin_dirs = load_pin_directions(repo_root, args.enablement)
    buffer_allow = BUFFER_LIKE_ALLOWLIST[args.enablement]

    base_to_entry = {}
    macro_to_base = {}
    map_json = None
    if args.enablement == "asap7_nangate45_3D":
        map_json = repo_root / "platforms" / args.enablement / "map.json"
        if not map_json.is_file():
            fail("map.json not found: {}".format(map_json))
        base_to_entry, macro_to_base = load_map_json(map_json)

    base_cells = set(base_module.cells)
    pre_cells = set(pre_module.cells)
    added = sorted(pre_cells - base_cells)
    removed = sorted(base_cells - pre_cells)
    changed_masters = sorted(
        (inst, base_module.cells[inst].type, pre_module.cells[inst].type)
        for inst in base_cells & pre_cells
        if base_module.cells[inst].type != pre_module.cells[inst].type
    )
    if args.expect_unchanged and (added or removed or changed_masters):
        fail(
            "Netlist-preserving preplace changed the instance/master set: "
            "added={} removed={} changed_masters={}".format(
                added[:20], removed[:20], changed_masters[:20]
            )
        )
    removed_non_buffer = []
    for inst in removed:
        master = strip_tier_suffix(base_module.cells[inst].type)
        if master not in buffer_allow:
            removed_non_buffer.append((inst, base_module.cells[inst].type))
    if removed_non_buffer and args.fail_on_removed_non_buffer:
        fail("Removed non-buffer-like instances: {}".format(removed_non_buffer[:20]))

    cell_tiers = build_cell_tiers(pre_module, partition)
    bit_to_names = build_bit_netnames(pre_module)
    bit_to_conns = build_bit_connections(pre_module, cell_tiers, pin_dirs)

    assigned_new_count = Counter()
    detail_rows = []
    append_entries = []
    already_present = []

    for inst_name in added:
        cell = pre_module.cells[inst_name]
        info = analyze_added_instance(inst_name, cell, bit_to_conns, bit_to_names, pin_dirs, set(added))
        target_tier, decision_reason = choose_tier(
            info["driver_counter"],
            info["input_side_counter"],
            info["output_load_counter"],
            args.enablement,
            assigned_new_count,
        )
        target_die = TIER_TO_DIE[target_tier]
        base_master = base_master_for_mode(cell.type, args.enablement, macro_to_base)
        target_macro = target_macro_for_mode(base_master, target_tier, args.enablement, base_to_entry)

        if inst_name in partition:
            if partition[inst_name] != target_die:
                fail("Added instance {} already in partition with conflicting die {} vs {}".format(inst_name, partition[inst_name], target_die))
            already_present.append(inst_name)
        else:
            append_entries.append((inst_name, target_die))
            assigned_new_count[target_tier] += 1

        input_existing_counter = info["driver_counter"] + info["input_side_counter"]
        row = {
            "inst": inst_name,
            "master": cell.type,
            "base_master": base_master,
            "target_tier": target_tier,
            "target_die": target_die,
            "target_macro": target_macro,
            "decision_reason": decision_reason,
            "decision_basis": info["decision_basis"],
            "edge_cost_if_upper": edge_cost(info["driver_counter"], info["output_load_counter"], "upper"),
            "edge_cost_if_bottom": edge_cost(info["driver_counter"], info["output_load_counter"], "bottom"),
            "net_cost_if_upper": net_cost(input_existing_counter, info["output_load_counter"], "upper"),
            "net_cost_if_bottom": net_cost(input_existing_counter, info["output_load_counter"], "bottom"),
            "driver_count": len(info["driver_conns"]),
            "driver_tiers": format_counter(info["driver_counter"]),
            "input_side_load_count": len(info["input_side_load_conns"]),
            "input_side_load_tiers": format_counter(info["input_side_counter"]),
            "output_load_count": len(info["output_load_conns"]),
            "output_load_tiers": format_counter(info["output_load_counter"]),
            "skipped_added_neighbor_count": len(info["skipped_added"]),
            "skipped_added_neighbor_driver_count": sum(1 for c in info["skipped_added"] if c["direction"] == "output"),
            "skipped_added_neighbor_load_count": sum(1 for c in info["skipped_added"] if c["direction"] != "output"),
            "input_net_names": ";".join(info["input_net_names"]),
            "output_net_names": ";".join(info["output_net_names"]),
            "driver_details": format_conns(info["driver_conns"]),
            "input_side_load_details": format_conns(info["input_side_load_conns"]),
            "output_load_details": format_conns(info["output_load_conns"]),
            "skipped_added_neighbor_details": format_conns(info["skipped_added"]),
        }
        detail_rows.append(row)

    backup = append_partition_entries(partition_txt, append_entries)
    updated_partition, _ = parse_partition(partition_txt)
    for inst, die in append_entries:
        if updated_partition.get(inst) != die:
            fail("Post-check failed: appended {} {} not found in partition".format(inst, die))

    appended_fields = ["inst", "die", "tier"]
    write_csv(
        log_dir / "appended_partition_entries.csv",
        [{"inst": inst, "die": die, "tier": DIE_TO_TIER[die]} for inst, die in append_entries],
        appended_fields,
    )
    detail_fields = [
        "inst",
        "master",
        "base_master",
        "target_tier",
        "target_die",
        "target_macro",
        "decision_reason",
        "decision_basis",
        "edge_cost_if_upper",
        "edge_cost_if_bottom",
        "net_cost_if_upper",
        "net_cost_if_bottom",
        "driver_count",
        "driver_tiers",
        "input_side_load_count",
        "input_side_load_tiers",
        "output_load_count",
        "output_load_tiers",
        "skipped_added_neighbor_count",
        "skipped_added_neighbor_driver_count",
        "skipped_added_neighbor_load_count",
        "input_net_names",
        "output_net_names",
        "driver_details",
        "input_side_load_details",
        "output_load_details",
        "skipped_added_neighbor_details",
    ]
    write_csv(log_dir / "added_instance_detail.csv", detail_rows, detail_fields)

    summary = {
        "design": args.design_nickname,
        "enablement": args.enablement,
        "inputs": {
            "base_v": str(base_v),
            "preplace_v": str(pre_v),
            "partition_txt": str(partition_txt),
            "map_json": str(map_json) if map_json else "",
            "yosys": str(yosys),
        },
        "json_outputs": {"base": str(base_json), "preplace": str(pre_json)},
        "partition_backup": str(backup),
        "orientation_source": "default_backend_contract",
        "added_instance_count": len(added),
        "removed_instance_count": len(removed),
        "changed_master_count": len(changed_masters),
        "changed_master_examples": changed_masters[:20],
        "removed_non_buffer_like_count": len(removed_non_buffer),
        "removed_non_buffer_like_examples": removed_non_buffer[:20],
        "already_present_same_tier_count": len(already_present),
        "appended_partition_entry_count": len(append_entries),
        "target_tier_counts": dict(Counter(row["target_tier"] for row in detail_rows)),
        "decision_reason_counts": dict(Counter(row["decision_reason"] for row in detail_rows)),
        "decision_basis_counts": dict(Counter(row["decision_basis"] for row in detail_rows)),
        "skipped_added_neighbor_total": sum(int(row["skipped_added_neighbor_count"]) for row in detail_rows),
        "buffer_like_allowlist_count": len(buffer_allow),
    }
    (log_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    md = [
        "# update_partition_after_preplace summary",
        "",
        "- design: {}".format(args.design_nickname),
        "- enablement: {}".format(args.enablement),
        "- added instances: {}".format(len(added)),
        "- appended partition entries: {}".format(len(append_entries)),
        "- already present same tier: {}".format(len(already_present)),
        "- removed instances: {}".format(len(removed)),
        "- removed non-buffer-like: {}".format(len(removed_non_buffer)),
        "- target tier counts: {}".format(dict(Counter(row["target_tier"] for row in detail_rows))),
        "- decision reason counts: {}".format(dict(Counter(row["decision_reason"] for row in detail_rows))),
        "- decision basis counts: {}".format(dict(Counter(row["decision_basis"] for row in detail_rows))),
        "- skipped added neighbors: {}".format(summary["skipped_added_neighbor_total"]),
        "",
    ]
    (log_dir / "summary.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
