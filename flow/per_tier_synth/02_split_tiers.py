"""Split the source netlist into validated upper- and bottom-tier modules.

The numbered entry coordinates partition analysis, module construction, and
the split and clock-map contracts consumed by later stages.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import OrderedDict, defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FLOW_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, FLOW_DIR)

from flow_config import (
    DESIGN_SDC_FILE,
    DESIGN,
    LIBRARY,
    YOSYS,
    NETLIST_V,
    PARTITION_TXT,
    PIN_DIR_JSON,
    COARSE_PIN_DIR_JSON,
    TMP_DIR,
    TOP_MODULE_NAME,
    SOURCE_JSON,
    SOURCE_V,
    BOTTOM_COARSE_JSON,
    BOTTOM_COARSE_V,
    UPPER_COARSE_JSON,
    UPPER_COARSE_V,
    SPLIT_PLAN_JSON,
    OUTPUT_VERILOG,
    PARTITION_BOTTOM_VALUE,
    PARTITION_TOP_VALUE,
    CLOCK_PORT_MAP_JSON,
    BOTTOM_COARSE_MODULE_NAME,
    UPPER_COARSE_MODULE_NAME,
    COARSE_SYNTH_BOT,
    assert_required_source_inputs,
)
from yosys_json_toolkit import (
    Design,
    Module,
    add_cell,
    add_netname,
    add_port,
    collect_bit_references,
    dump_design,
    load_design,
    netname_bit_labels,
    port_bit_labels,
    replace_bit_references,
    validate_design,
)

TIER_BOTTOM = "bottom"
TIER_UPPER = "upper"
SUPPORTED_PORT_DIRECTIONS = {"input", "output"}
SUPPORTED_CELL_PIN_DIRECTIONS = {"input", "output"}
SUPPORTED_CONST_PORT_BITS = {"0", "1"}
SUPPORTED_CONST_BITS = {"0", "1", "x", "z"}
BIT_KIND_PI = "PI"
BIT_KIND_PO = "PO"
BIT_KIND_INTERNAL = "internal"
CLOCK_PIN_NAMES_BY_LIBRARY = {
    "asap7_3D": frozenset(["CLK", "clk"]),
    "nangate45_3D": frozenset(["CK", "CLK", "clk"]),
    "asap7_nangate45_3D": frozenset(["CK", "CLK", "clk"]),
}
COARSE_2D_SYNTH_DIR = COARSE_SYNTH_BOT


from modules.tier_split_analysis import (
    build_primary_port_entries,
    build_source_json,
    canonicalize_undriven_internal_bits,
    classify_bits,
    emit_json_to_verilog,
    load_partition_entries,
    load_pin_direction_tables,
    resolve_cell_port_directions,
)
from modules.tier_split_builder import (
    assert_clock_port_map_contract,
    assert_split_plan_contract,
    build_clock_port_map,
    build_split_design,
    build_split_plan,
    copy_cells_to_split_modules,
    make_split_module,
    populate_ports,
    print_summary,
    write_clock_port_map,
    write_split_plan,
)

def main():
    assert_required_source_inputs()
    assert os.path.exists(NETLIST_V), "Netlist not found: {}".format(NETLIST_V)
    assert os.path.exists(PARTITION_TXT), "partition.txt not found: {}".format(PARTITION_TXT)
    assert os.path.exists(PIN_DIR_JSON), "pin_dir.json not found: {}".format(PIN_DIR_JSON)
    assert os.path.exists(COARSE_PIN_DIR_JSON), "coarse_pin_dir.json not found: {}".format(COARSE_PIN_DIR_JSON)
    os.makedirs(TMP_DIR, exist_ok=True)

    print("[1] Building source json with Yosys ...")
    build_source_json()
    if OUTPUT_VERILOG:
        emit_json_to_verilog(SOURCE_JSON, TOP_MODULE_NAME, SOURCE_V)
        print("[02] source_verilog -> {}".format(SOURCE_V))

    print("[2] Loading source json through toolkit ...")
    src_design = load_design(SOURCE_JSON)
    validate_design(src_design)
    assert TOP_MODULE_NAME in src_design.modules, "Top module {} not found in {}".format(TOP_MODULE_NAME, SOURCE_JSON)
    src_module = src_design.get_module(TOP_MODULE_NAME)

    print("[3] Resolving cell pin directions ...")
    pin_direction_table = load_pin_direction_tables()
    resolve_cell_port_directions(src_module, pin_direction_table)

    print("[4] Expanding primary port bits and partition ownership ...")
    primary_entries, label_to_entry, _primary_int_bits = build_primary_port_entries(src_module)
    cell_tiers, primary_owner_tiers = load_partition_entries(label_to_entry, src_module)

    print("[5] Canonicalizing undriven internal inputs ...")
    undriven_fix_report = canonicalize_undriven_internal_bits(src_module, cell_tiers)
    with open(os.path.join(TMP_DIR, "02_undriven_actual_coarse_fix_report.json"), "w") as fp:
        json.dump(undriven_fix_report, fp, indent=2, sort_keys=True)
        fp.write("\n")
    dump_design(src_design, SOURCE_JSON)
    if OUTPUT_VERILOG:
        emit_json_to_verilog(SOURCE_JSON, TOP_MODULE_NAME, SOURCE_V)

    print("[6] Classifying bit-level split cases ...")
    primary_cases, internal_cases = classify_bits(src_module, primary_entries, primary_owner_tiers, cell_tiers)

    print("[7] Constructing split modules in memory ...")
    upper_module = make_split_module(src_module, UPPER_COARSE_MODULE_NAME)
    bottom_module = make_split_module(src_module, BOTTOM_COARSE_MODULE_NAME)
    upper_state, bottom_state = populate_ports(upper_module, bottom_module, primary_cases, internal_cases)
    cell_counts = copy_cells_to_split_modules(src_module, upper_module, bottom_module, cell_tiers)

    print("[8] Validating and writing split json ...")
    upper_design = build_split_design(src_design, upper_module)
    bottom_design = build_split_design(src_design, bottom_module)
    validate_design(upper_design)
    validate_design(bottom_design)
    dump_design(upper_design, UPPER_COARSE_JSON)
    dump_design(bottom_design, BOTTOM_COARSE_JSON)
    if OUTPUT_VERILOG:
        emit_json_to_verilog(UPPER_COARSE_JSON, upper_module.name, UPPER_COARSE_V)
        emit_json_to_verilog(BOTTOM_COARSE_JSON, bottom_module.name, BOTTOM_COARSE_V)
        print("[02] upper_verilog  -> {}".format(UPPER_COARSE_V))
        print("[02] bottom_verilog -> {}".format(BOTTOM_COARSE_V))

    plan = build_split_plan(src_module, primary_cases, internal_cases, upper_state, bottom_state, cell_counts)
    assert_split_plan_contract(
        plan,
        src_module,
        primary_entries,
        internal_cases,
        upper_module,
        bottom_module,
        cell_tiers,
    )
    write_split_plan(plan)
    clock_port_map = build_clock_port_map(src_module, primary_cases, internal_cases, upper_state, bottom_state)
    assert_clock_port_map_contract(clock_port_map, src_module, primary_cases, internal_cases)
    write_clock_port_map(clock_port_map)
    print_summary(plan)

    print("[9] Split JSON handoff is ready")

    print("[done]")
    print("  source_json   -> {}".format(SOURCE_JSON))
    print("  upper_json    -> {}".format(UPPER_COARSE_JSON))
    print("  bottom_json   -> {}".format(BOTTOM_COARSE_JSON))
    print("  split_plan    -> {}".format(SPLIT_PLAN_JSON))
    print("  clock_port_map-> {}".format(CLOCK_PORT_MAP_JSON))


if __name__ == "__main__":
    main()
