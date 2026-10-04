#!/usr/bin/env python3
"""Finalize hierarchy synthesis and publish the coarse-synthesis handoff.

The entry point coordinates hierarchy analysis, zero-area variant removal, and
generation of the retained coarse-unit physical and timing views.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

FLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLOW_DIR))

from yosys_json_toolkit import dump_design, load_design, validate_design

from coarse_unit_views import (
    publish_retained_actual_outputs,
    validate_published_variant_sets,
)
from hierarchy_variants import (
    build_active_design,
    build_top_only_design,
    collect_top_referenced_actual_modules,
    emit_json_to_verilog,
    flatten_eliminated_actual_instances,
    infer_top_module_name,
    load_canonical_contracts,
    prepare_actual_variant_data,
    read_module_list,
    resolve_yosys_exe,
    should_eliminate_actual_unit,
    validate_top_level_contract,
    verilog_to_json,
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-name", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--yosys", required=True)
    parser.add_argument("--input-verilog", required=True)
    parser.add_argument("--output-verilog", required=True)
    parser.add_argument("--coarse-unit-source-dir", required=True)
    parser.add_argument("--coarse-unit-list", required=True)
    parser.add_argument("--family-metrics-dir", required=True)
    parser.add_argument("--handoff-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    args.yosys = resolve_yosys_exe(args.yosys)
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    module_names = read_module_list(args.coarse_unit_list)
    if not module_names:
        shutil.copyfile(args.input_verilog, args.output_verilog)
        report = {
            "design_name": args.design_name,
            "platform": args.platform,
            "module_count": 0,
            "status": "no coarse units; copied raw Genus output",
        }
        with open(work_dir / "normalization_report.json", "w", encoding="utf-8") as fp:
            json.dump(report, fp, indent=2, sort_keys=True)
            fp.write("\n")
        print(report["status"])
        return

    raw_json = work_dir / "1_synth_netlist.raw.json"
    pruned_raw_json = None
    active_json = work_dir / "1_synth_netlist.actual_modules.json"
    top_only_json = work_dir / "1_synth_netlist.top_only.json"

    top_module_name = args.design_name
    try:
        verilog_to_json(args.yosys, args.input_verilog, top_module_name, raw_json)
    except RuntimeError as exc:
        if "verilog -> json" not in str(exc):
            raise
        top_module_name = infer_top_module_name(args.yosys, args.input_verilog, work_dir)
        verilog_to_json(args.yosys, args.input_verilog, top_module_name, raw_json)
    raw_design = load_design(raw_json)
    validate_design(raw_design)
    if top_module_name not in raw_design.modules:
        raise RuntimeError("top module {} missing from Genus JSON".format(top_module_name))

    canonical_contracts = load_canonical_contracts(
        args.yosys,
        args.coarse_unit_source_dir,
        module_names,
        work_dir,
    )
    pre_actual_modules = collect_top_referenced_actual_modules(
        raw_design, top_module_name, canonical_contracts)
    prepared = prepare_actual_variant_data(args, pre_actual_modules)
    eliminated_actual_modules = prepared["eliminated_actual_modules"]

    (
        pruned_raw_design,
        pruned_raw_json,
        flattened_instance_count,
    ) = flatten_eliminated_actual_instances(
        args.yosys,
        raw_design,
        top_module_name,
        eliminated_actual_modules.keys(),
        work_dir,
    )

    if eliminated_actual_modules:
        actual_modules = collect_top_referenced_actual_modules(
            pruned_raw_design, top_module_name, canonical_contracts)
    else:
        actual_modules = pre_actual_modules

    expected_retained = set(pre_actual_modules) - set(eliminated_actual_modules)
    actual_retained = set(actual_modules)
    if actual_retained != expected_retained:
        raise RuntimeError(
            "retained actual-unit set mismatch after zero-area flatten: "
            "expected={} observed={}".format(
                sorted(expected_retained), sorted(actual_retained)
            )
        )

    active_design = build_active_design(pruned_raw_design, top_module_name, actual_modules)
    dump_design(active_design, active_json)
    validate_design(load_design(active_json))

    top_contract = validate_top_level_contract(active_design, top_module_name, set(actual_modules.keys()))
    top_only_design = build_top_only_design(active_design, top_module_name)
    dump_design(top_only_design, top_only_json)
    validate_design(load_design(top_only_json))
    emit_json_to_verilog(args.yosys, top_only_json, top_module_name, args.output_verilog)

    collateral_report = publish_retained_actual_outputs(
        args,
        actual_modules,
        prepared["variant_data"],
    )

    for actual_name, eliminated_info in eliminated_actual_modules.items():
        print("[3dsyn] eliminated zero-area actual unit:")
        print("  module={}".format(actual_name))
        print("  family={}".format(eliminated_info["family_name"]))
        print("  bottom_area={}".format(eliminated_info["bottom_cell_area"]))
        print("  upper_area={}".format(eliminated_info["upper_cell_area"]))
        print("  bottom_cell_count={}".format(eliminated_info["bottom_cell_count"]))
        print("  upper_cell_count={}".format(eliminated_info["upper_cell_count"]))
        print("  top_instance_count={}".format(eliminated_info["top_instance_count"]))

    print("[3dsyn] pre_actual_count={}".format(len(pre_actual_modules)))
    print("[3dsyn] eliminated_actual_count={}".format(len(eliminated_actual_modules)))
    print("[3dsyn] retained_actual_count={}".format(len(actual_modules)))

    report = {
        "design_name": args.design_name,
        "top_module_name": top_module_name,
        "platform": args.platform,
        "module_count": len(module_names),
        "pre_actual_coarse_unit_type_count": len(pre_actual_modules),
        "eliminated_actual_coarse_unit_type_count": len(eliminated_actual_modules),
        "eliminated_actual_coarse_unit_types": eliminated_actual_modules,
        "flattened_zero_area_instance_count": flattened_instance_count,
        "actual_coarse_unit_type_count": len(actual_modules),
        "actual_coarse_unit_types": collateral_report["actual_variants"],
        "top_level_contract": top_contract,
        "input_verilog": args.input_verilog,
        "output_verilog": args.output_verilog,
        "active_json": str(active_json),
        "top_only_json": str(top_only_json),
        "zero_area_flattened_json": str(pruned_raw_json) if pruned_raw_json else None,
        "handoff_dir": args.handoff_dir,
    }
    with open(work_dir / "normalization_report.json", "w", encoding="utf-8") as fp:
        json.dump(report, fp, indent=2, sort_keys=False)
        fp.write("\n")
    print("Normalized Genus output written to {}".format(args.output_verilog))
    print("Published actual coarse-unit handoff under {}".format(args.handoff_dir))


if __name__ == "__main__":
    main()

