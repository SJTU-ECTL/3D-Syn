"""Build the hierarchical wrapper consumed by per-tier Genus synthesis.

The numbered entry validates upstream contracts, applies the wrapper plan, and
writes the wrapper design without changing the stage interface.
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
    BOTTOM_COARSE_MODULE_NAME,
    BOTTOM_REMAPPED_JSON,
    CELL_MAPPING_PLAN_JSON,
    CELL_NAME_MAPPING_MODE,
    CLOCK_PORT_MAP_JSON,
    DESIGN,
    FLOW_VARIANT,
    LIBRARY,
    OUTPUT_VERILOG,
    SOURCE_JSON,
    SPLIT_PLAN_JSON,
    TOP_MODULE_NAME,
    TMP_DIR,
    UPPER_COARSE_MODULE_NAME,
    UPPER_REMAPPED_JSON,
    WRAPPER_BOTTOM_INSTANCE_NAME,
    WRAPPER_JSON,
    WRAPPER_PLAN_JSON,
    WRAPPER_TOP_MODULE_NAME,
    WRAPPER_UPPER_INSTANCE_NAME,
    WRAPPER_V,
    YOSYS,
)
from yosys_json_toolkit import (
    BitAllocator,
    Design,
    Module,
    add_cell,
    add_module,
    add_netname,
    add_port_with_netname,
    copy_port_shape_metadata,
    dump_design,
    load_design,
    module_port_shape_signature,
    port_bit_labels,
    port_shape,
    remove_port_and_optional_netname,
    rename_port_and_netname,
    validate_design,
)


TIER_BOTTOM = "bottom"
TIER_UPPER = "upper"
TIER_MODULES = {
    TIER_BOTTOM: BOTTOM_COARSE_MODULE_NAME,
    TIER_UPPER: UPPER_COARSE_MODULE_NAME,
}
TIER_INSTANCES = {
    TIER_BOTTOM: WRAPPER_BOTTOM_INSTANCE_NAME,
    TIER_UPPER: WRAPPER_UPPER_INSTANCE_NAME,
}
SUPPORTED_SOURCE_PORT_DIRECTIONS = {"input", "output"}
SUPPORTED_CELL_PIN_DIRECTIONS = {"input", "output"}



def run_yosys_script(body_tcl, label):
    assert os.path.exists(YOSYS), "Yosys not found: {}".format(YOSYS)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ys", delete=False) as tcl_fp:
        tcl_fp.write(body_tcl)
        tcl_path = tcl_fp.name
    try:
        result = subprocess.run([YOSYS, "-s", tcl_path], check=False)
    finally:
        os.unlink(tcl_path)
    assert result.returncode == 0, "Yosys failed during {} with exit code {}".format(label, result.returncode)


def emit_json_to_verilog(json_path, module_name, out_v):
    tcl = """
read_json {json_path}
hierarchy -top {module_name}
write_verilog -norename -noexpr -noattr {out_v}
""".format(
        json_path=json_path,
        module_name=module_name,
        out_v=out_v,
    )
    run_yosys_script(tcl, "json -> verilog ({})".format(module_name))
    assert os.path.exists(out_v), "Wrapper Verilog was not created: {}".format(out_v)


def assert_verilog_readback(v_path, module_name):
    tcl = """
read_verilog {v_path}
hierarchy -top {module_name}
""".format(
        v_path=v_path,
        module_name=module_name,
    )
    run_yosys_script(tcl, "wrapper verilog readback ({})".format(module_name))


def load_json_file(path):
    assert os.path.isfile(path), "Required JSON file not found: {}".format(path)
    with open(path) as fp:
        data = json.load(fp)
    assert isinstance(data, dict), "JSON file must contain a dictionary: {}".format(path)
    return data


def write_json_file(path, data):
    with open(path, "w") as fp:
        json.dump(data, fp, indent=2)
        fp.write("\n")
    loaded = load_json_file(path)
    assert loaded == data, "JSON readback differs from written file: {}".format(path)

def assert_design_valid(design, label):
    validate_design(design)
    assert design.modules, "{} has no modules".format(label)


def load_inputs():
    for path in [
        SOURCE_JSON,
        SPLIT_PLAN_JSON,
        CLOCK_PORT_MAP_JSON,
        CELL_MAPPING_PLAN_JSON,
        BOTTOM_REMAPPED_JSON,
        UPPER_REMAPPED_JSON,
    ]:
        assert os.path.isfile(path), "Required 05 input missing: {}".format(path)

    source_design = load_design(SOURCE_JSON)
    bottom_design = load_design(BOTTOM_REMAPPED_JSON)
    upper_design = load_design(UPPER_REMAPPED_JSON)
    assert_design_valid(source_design, "SOURCE_JSON")
    assert_design_valid(bottom_design, "BOTTOM_REMAPPED_JSON")
    assert_design_valid(upper_design, "UPPER_REMAPPED_JSON")

    split_plan = load_json_file(SPLIT_PLAN_JSON)
    clock_port_map = load_json_file(CLOCK_PORT_MAP_JSON)
    cell_mapping_plan = load_json_file(CELL_MAPPING_PLAN_JSON)
    return source_design, bottom_design, upper_design, split_plan, clock_port_map, cell_mapping_plan


def assert_input_contract(source_design, tier_designs, split_plan, clock_port_map, cell_mapping_plan):
    assert TOP_MODULE_NAME in source_design.modules, "Source top module {} not found".format(TOP_MODULE_NAME)
    source_top = source_design.get_module(TOP_MODULE_NAME)
    for port in source_top.ports.values():
        assert port.direction in SUPPORTED_SOURCE_PORT_DIRECTIONS, (
            "05 does not support source top port direction {} on {}".format(port.direction, port.name)
        )

    for tier, module_name in TIER_MODULES.items():
        assert module_name in tier_designs[tier].modules, (
            "{} remapped design missing top module {}".format(tier, module_name)
        )
        assert split_plan["modules"][tier]["name"] == module_name, (
            "Split plan {} module mismatch: {} != {}".format(
                tier, split_plan["modules"][tier]["name"], module_name
            )
        )
        top_module = tier_designs[tier].get_module(module_name)
        for port in top_module.ports.values():
            assert len(port.bits) == 1, (
                "05 first implementation expects scalar tier top ports, got {}.{} -> {}".format(
                    module_name, port.name, port.bits
                )
            )

    assert cell_mapping_plan["mapping_mode"] == CELL_NAME_MAPPING_MODE, (
        "CELL_MAPPING_PLAN_JSON mapping_mode mismatch: {} != {}".format(
            cell_mapping_plan["mapping_mode"], CELL_NAME_MAPPING_MODE
        )
    )
    assert WRAPPER_UPPER_INSTANCE_NAME == "inst_upper", (
        "WRAPPER_UPPER_INSTANCE_NAME must stay fixed for downstream scripts"
    )
    assert WRAPPER_BOTTOM_INSTANCE_NAME == "inst_bottom", (
        "WRAPPER_BOTTOM_INSTANCE_NAME must stay fixed for downstream scripts"
    )
    assert clock_port_map["source_clock_port_name"] in source_top.ports, (
        "Clock source port missing from source top: {}".format(clock_port_map["source_clock_port_name"])
    )
    clk_port = source_top.ports[clock_port_map["source_clock_port_name"]]
    assert clk_port.direction == "input", "Clock source port must be a primary input"
    assert len(clk_port.bits) == 1, "05 first implementation supports one scalar source clock"
    assert clock_port_map["clock_case"] in {"PI-1", "PI-2", "PI-3"}, (
        "Unsupported clock primary case: {}".format(clock_port_map["clock_case"])
    )
    generated = set(clock_port_map.get("generated_clock_channels", []))
    for tier, module_name in TIER_MODULES.items():
        assert tier in clock_port_map["tiers"], "CLOCK_PORT_MAP_JSON missing tier entry: {}".format(tier)
        entry = clock_port_map["tiers"][tier]
        assert entry["module_name"] == module_name, (
            "CLOCK_PORT_MAP_JSON {} module mismatch: {} != {}".format(tier, entry["module_name"], module_name)
        )
        top_module = tier_designs[tier].get_module(module_name)
        source_clock_ports = entry.get("source_clock_ports", [])
        generated_clock_ports = entry.get("generated_clock_ports", [])
        direct_source_clock_ports = entry.get("direct_source_clock_ports", [])
        generated_clock_input_ports = entry.get("generated_clock_input_ports", [])
        expected_related = sorted(
            [item["port_name"] for item in source_clock_ports] + [item["port_name"] for item in generated_clock_ports]
        )
        assert entry["clock_related_ports"] == expected_related, (
            "CLOCK_PORT_MAP_JSON {} related port mismatch: expected {} got {}".format(
                tier, expected_related, entry["clock_related_ports"]
            )
        )
        expected_direct = sorted(item["port_name"] for item in source_clock_ports if item["direction"] == "input")
        assert direct_source_clock_ports == expected_direct, (
            "CLOCK_PORT_MAP_JSON {} direct source clock port mismatch: expected {} got {}".format(
                tier, expected_direct, direct_source_clock_ports
            )
        )
        expected_generated_inputs = sorted(
            item["port_name"] for item in generated_clock_ports if item["direction"] == "input"
        )
        assert generated_clock_input_ports == expected_generated_inputs, (
            "CLOCK_PORT_MAP_JSON {} generated clock input mismatch: expected {} got {}".format(
                tier, expected_generated_inputs, generated_clock_input_ports
            )
        )
        for item in source_clock_ports:
            assert item["port_name"] in top_module.ports, (
                "CLOCK_PORT_MAP_JSON {} source clock port missing from tier module: {}".format(tier, item)
            )
            assert top_module.ports[item["port_name"]].direction == item["direction"], (
                "CLOCK_PORT_MAP_JSON {} source clock port direction mismatch: {}".format(tier, item)
            )
        for item in generated_clock_ports:
            assert item["d2d_name"] in generated, (
                "CLOCK_PORT_MAP_JSON {} generated port references unknown d2d channel: {}".format(tier, item)
            )
            assert item["port_name"] in top_module.ports, (
                "CLOCK_PORT_MAP_JSON {} generated clock port missing from tier module: {}".format(tier, item)
            )
            assert top_module.ports[item["port_name"]].direction == item["direction"], (
                "CLOCK_PORT_MAP_JSON {} generated clock port direction mismatch: {}".format(tier, item)
            )
from modules.wrapper_builder import (
    assert_wrapper_top_contract,
    build_wrapper_design,
    build_wrapper_top,
    transform_tier_module_interface,
)
from modules.wrapper_plan import (
    assert_wrapper_plan_matches_design,
    build_source_port_plan,
    build_tier_action_plans,
    build_wrapper_plan,
    combine_contract_plan,
)

def main():
    os.makedirs(TMP_DIR, exist_ok=True)
    source_design, bottom_design, upper_design, split_plan, clock_port_map, cell_mapping_plan = load_inputs()
    tier_designs = {TIER_BOTTOM: bottom_design, TIER_UPPER: upper_design}
    assert_input_contract(source_design, tier_designs, split_plan, clock_port_map, cell_mapping_plan)

    source_top = source_design.get_module(TOP_MODULE_NAME)
    tier_modules = {
        TIER_BOTTOM: bottom_design.get_module(BOTTOM_COARSE_MODULE_NAME),
        TIER_UPPER: upper_design.get_module(UPPER_COARSE_MODULE_NAME),
    }

    print("[05] analyzing source primary port shape ...")
    source_port_plan = build_source_port_plan(source_top)

    print("[05] building tier interface rewrite plan ...")
    action_plan = build_tier_action_plans(tier_modules, split_plan, source_port_plan, clock_port_map)

    print("[05] transforming tier module interfaces ...")
    transformed_tiers = {
        TIER_UPPER: transform_tier_module_interface(
            TIER_UPPER, tier_modules[TIER_UPPER], action_plan["plans"][TIER_UPPER]
        ),
        TIER_BOTTOM: transform_tier_module_interface(
            TIER_BOTTOM, tier_modules[TIER_BOTTOM], action_plan["plans"][TIER_BOTTOM]
        ),
    }

    print("[05] building wrapper top ...")
    wrapper_top = build_wrapper_top(source_top, transformed_tiers, action_plan, source_port_plan)
    wrapper_design, merge_log = build_wrapper_design(source_design, tier_designs, transformed_tiers, wrapper_top)
    assert_wrapper_top_contract(
        wrapper_design,
        source_top,
        split_plan,
        clock_port_map,
        combine_contract_plan(action_plan, source_port_plan),
    )

    wrapper_plan = build_wrapper_plan(source_port_plan, action_plan, clock_port_map, merge_log, wrapper_design)
    assert_wrapper_plan_matches_design(wrapper_plan, wrapper_design)

    print("[05] writing wrapper json and plan ...")
    dump_design(wrapper_design, WRAPPER_JSON)
    reloaded = load_design(WRAPPER_JSON)
    validate_design(reloaded)
    assert_wrapper_plan_matches_design(wrapper_plan, reloaded)
    write_json_file(WRAPPER_PLAN_JSON, wrapper_plan)
    assert_wrapper_plan_matches_design(load_json_file(WRAPPER_PLAN_JSON), reloaded)

    if OUTPUT_VERILOG:
        print("[05] emitting wrapper verilog ...")
        emit_json_to_verilog(WRAPPER_JSON, WRAPPER_TOP_MODULE_NAME, WRAPPER_V)
        assert_verilog_readback(WRAPPER_V, WRAPPER_TOP_MODULE_NAME)

    print("[05] done")
    print("       wrapper_json -> {}".format(WRAPPER_JSON))
    print("       wrapper_plan -> {}".format(WRAPPER_PLAN_JSON))
    if OUTPUT_VERILOG:
        print("       wrapper_v    -> {}".format(WRAPPER_V))


if __name__ == "__main__":
    main()
