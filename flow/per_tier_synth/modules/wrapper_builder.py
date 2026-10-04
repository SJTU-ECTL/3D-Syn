"""Apply the wrapper plan and construct the hierarchical synthesis design."""

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import OrderedDict, defaultdict

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
STAGE_DIR = os.path.dirname(MODULE_DIR)
FLOW_DIR = os.path.dirname(STAGE_DIR)
sys.path.insert(0, STAGE_DIR)
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



def transform_tier_module_interface(tier, module, plan):
    transformed = module.copy()
    before_cells = {name: cell.to_dict() for name, cell in transformed.cells.items()}
    before_memories = {name: memory.to_dict() for name, memory in transformed.memories.items()}

    for action in plan.actions.values():
        if action["action"] == "remove":
            remove_port_and_optional_netname(transformed, action["old_name"], expected_bits=[action["bit"]])
    for action in plan.actions.values():
        if action["action"] == "rename":
            rename_port_and_netname(transformed, action["old_name"], action["new_name"])

    assert before_cells == {name: cell.to_dict() for name, cell in transformed.cells.items()}, (
        "05 {} interface transform unexpectedly changed cells".format(tier)
    )
    assert before_memories == {name: memory.to_dict() for name, memory in transformed.memories.items()}, (
        "05 {} interface transform unexpectedly changed memories".format(tier)
    )
    assert_live_module_boundary(transformed, "transformed {}".format(tier))
    return transformed


def assert_live_module_boundary(module, context):
    cell_output_driver_bits = set()
    cell_input_bits = set()
    module_input_bits = set()
    module_output_bits = set()

    for port in module.ports.values():
        for bit in port.bits:
            if not isinstance(bit, int):
                continue
            if port.direction == "input":
                module_input_bits.add(bit)
            elif port.direction == "output":
                module_output_bits.add(bit)
            else:
                raise AssertionError("{} has unsupported module port direction {}".format(context, port.direction))

    for cell in module.cells.values():
        for pin_name, bits in cell.connections.items():
            direction = cell.port_directions.get(pin_name)
            assert direction in SUPPORTED_CELL_PIN_DIRECTIONS, (
                "{} missing supported port direction for {}.{}".format(context, cell.name, pin_name)
            )
            for bit in bits:
                if not isinstance(bit, int):
                    continue
                if direction == "output":
                    cell_output_driver_bits.add(bit)
                else:
                    cell_input_bits.add(bit)

    missing_input_drivers = sorted(cell_input_bits - cell_output_driver_bits - module_input_bits)
    assert not missing_input_drivers, (
        "{} has live cell input bits without cell driver or module input: {}".format(
            context, missing_input_drivers[:20]
        )
    )
    missing_output_drivers = sorted(module_output_bits - cell_output_driver_bits - module_input_bits)
    assert not missing_output_drivers, (
        "{} has module output bits without cell driver or module input: {}".format(
            context, missing_output_drivers[:20]
        )
    )


def add_wrapper_primary_ports(wrapper_top, source_top, source_port_plan):
    bit_iter = iter(source_port_plan["source_ports"])
    for port_name, source_port in source_top.ports.items():
        plan_entry = next(bit_iter)
        assert plan_entry["name"] == port_name, "Source port order mismatch while building wrapper"
        add_port_with_netname(
            wrapper_top,
            port_name,
            source_port.direction,
            plan_entry["wrapper_bits"],
            offset=source_port.offset,
            upto=source_port.upto,
            signed=source_port.signed,
        )
        copy_port_shape_metadata(source_top, wrapper_top, port_name)


def add_d2d_netnames(wrapper_top, data_d2d_channels, clock_d2d_channels):
    seen = set()
    for kind, channels in [("data_d2d", data_d2d_channels), ("clock_d2d", clock_d2d_channels)]:
        for channel in channels:
            name = channel["d2d_name"]
            assert name not in seen, "Duplicate wrapper d2d netname: {}".format(name)
            seen.add(name)
            add_netname(
                wrapper_top,
                name,
                [channel["wrapper_net_bit"]],
                hide_name=0,
                attributes={"syn3d_channel_kind": kind},
            )


def build_instance_connections(module, plan):
    connections = OrderedDict()
    port_directions = OrderedDict()
    for port_name, port in module.ports.items():
        action = None
        for candidate in plan.actions.values():
            if candidate["action"] == "remove":
                continue
            if candidate["new_name"] == port_name:
                action = candidate
                break
        assert action is not None, "No wrapper connection action for port {}.{}".format(module.name, port_name)
        assert port.direction == action["direction"], (
            "Transformed port direction mismatch for {}.{}".format(module.name, port_name)
        )
        connections[port_name] = [action["wrapper_bit"]]
        port_directions[port_name] = port.direction
    assert set(connections.keys()) == set(module.ports.keys()), (
        "Instance connection set does not match transformed module ports for {}".format(module.name)
    )
    return connections, port_directions


def build_wrapper_top(source_top, transformed_tiers, action_plan, source_port_plan):
    wrapper_top = Module(
        name=WRAPPER_TOP_MODULE_NAME,
        attributes=OrderedDict(),
        parameter_default_values=OrderedDict(),
        ports=OrderedDict(),
        cells=OrderedDict(),
        memories=OrderedDict(),
        netnames=OrderedDict(),
    )
    add_wrapper_primary_ports(wrapper_top, source_top, source_port_plan)
    add_d2d_netnames(wrapper_top, action_plan["data_d2d_channels"], action_plan["clock_d2d_channels"])

    for tier in [TIER_UPPER, TIER_BOTTOM]:
        connections, port_directions = build_instance_connections(transformed_tiers[tier], action_plan["plans"][tier])
        add_cell(
            wrapper_top,
            TIER_INSTANCES[tier],
            TIER_MODULES[tier],
            connections,
            hide_name=0,
            parameters={},
            attributes={"syn3d_tier": tier},
            port_directions=port_directions,
        )

    return wrapper_top


def tier_namespaced_module_name(tier, module_name):
    assert tier in {TIER_BOTTOM, TIER_UPPER}, "Unexpected tier namespace: {}".format(tier)
    assert module_name, "Cannot namespace an empty module name"
    return "_syn3d_{}__{}".format(tier, module_name)


def build_tier_internal_module_map(tier_designs):
    maps = {}
    all_new_names = set()
    for tier in [TIER_UPPER, TIER_BOTTOM]:
        module_map = OrderedDict()
        for module_name in tier_designs[tier].modules:
            if module_name == TIER_MODULES[tier]:
                continue
            new_name = tier_namespaced_module_name(tier, module_name)
            assert new_name not in tier_designs[tier].modules, (
                "{} namespaced module name collides with existing module: {}".format(tier, new_name)
            )
            assert new_name not in all_new_names, "Duplicate namespaced module name: {}".format(new_name)
            all_new_names.add(new_name)
            module_map[module_name] = new_name
        maps[tier] = module_map
    return maps


def clone_module_with_type_map(module, new_name, type_map):
    cloned = module.copy()
    cloned.name = new_name
    remapped_cells = []
    for cell in cloned.cells.values():
        if cell.type in type_map:
            old_type = cell.type
            cell.type = type_map[old_type]
            remapped_cells.append((cell.name, old_type, cell.type))
    return cloned, remapped_cells


def build_wrapper_design(source_design, tier_designs, transformed_tiers, wrapper_top):
    wrapper_design = Design(
        creator=source_design.creator,
        modules=OrderedDict(),
        models=json.loads(json.dumps(source_design.models)),
        extras=json.loads(json.dumps(source_design.extras)),
    )
    tier_module_maps = build_tier_internal_module_map(tier_designs)

    add_module(wrapper_design, wrapper_top, clone=True)
    for tier in [TIER_UPPER, TIER_BOTTOM]:
        transformed_top, remapped_cells = clone_module_with_type_map(
            transformed_tiers[tier], TIER_MODULES[tier], tier_module_maps[tier]
        )
        add_module(wrapper_design, transformed_top, clone=False)
        assert not any(new_type in {TIER_MODULES[TIER_UPPER], TIER_MODULES[TIER_BOTTOM]} for _, _, new_type in remapped_cells), (
            "{} tier top unexpectedly references a tier top module after namespace rewrite".format(tier)
        )

    merge_log = []
    for tier in [TIER_UPPER, TIER_BOTTOM]:
        type_map = tier_module_maps[tier]
        for module_name, module in tier_designs[tier].modules.items():
            if module_name == TIER_MODULES[tier]:
                continue
            namespaced_name = type_map[module_name]
            namespaced_module, remapped_cells = clone_module_with_type_map(module, namespaced_name, type_map)
            assert namespaced_name not in wrapper_design.modules, (
                "Namespaced module collision while merging {}: {}".format(tier, namespaced_name)
            )
            add_module(wrapper_design, namespaced_module, clone=False)
            merge_log.append(
                OrderedDict(
                    [
                        ("tier", tier),
                        ("original_module", module_name),
                        ("namespaced_module", namespaced_name),
                        ("status", "tier_namespaced"),
                        ("remapped_internal_instance_count", len(remapped_cells)),
                    ]
                )
            )

    validate_design(wrapper_design)
    return wrapper_design, merge_log


def assert_wrapper_top_contract(wrapper_design, source_top, split_plan, clock_port_map, plan):
    assert WRAPPER_TOP_MODULE_NAME in wrapper_design.modules, "Wrapper top module missing"
    wrapper_top = wrapper_design.get_module(WRAPPER_TOP_MODULE_NAME)
    assert set(wrapper_top.cells.keys()) == {WRAPPER_UPPER_INSTANCE_NAME, WRAPPER_BOTTOM_INSTANCE_NAME}, (
        "Wrapper top must contain only inst_upper/inst_bottom, got {}".format(sorted(wrapper_top.cells.keys()))
    )
    assert wrapper_top.cells[WRAPPER_UPPER_INSTANCE_NAME].type == UPPER_COARSE_MODULE_NAME
    assert wrapper_top.cells[WRAPPER_BOTTOM_INSTANCE_NAME].type == BOTTOM_COARSE_MODULE_NAME

    assert module_port_shape_signature(wrapper_top) == module_port_shape_signature(source_top), (
        "Wrapper top primary port shape differs from source top"
    )

    legacy_top_ports = [
        name for name in wrapper_top.ports if name.startswith("__d2d_pi_") or name.startswith("__d2d_po_")
    ]
    assert not legacy_top_ports, "Wrapper top must not expose legacy carrier ports: {}".format(legacy_top_ports)

    expected_data = [
        item
        for item in split_plan["internal_bits"]
        if item.get("d2d_name") and item["d2d_name"] not in set(clock_port_map.get("generated_clock_channels", []))
    ]
    expected_clock = list(clock_port_map.get("generated_clock_channels", []))
    assert len(plan["data_d2d_channels"]) == len(expected_data), (
        "Data d2d count mismatch: {} != {}".format(len(plan["data_d2d_channels"]), len(expected_data))
    )
    assert len(plan["clock_d2d_channels"]) == len(expected_clock), (
        "Clock d2d count mismatch: {} != {}".format(len(plan["clock_d2d_channels"]), len(expected_clock))
    )
    data_names = {item["d2d_name"] for item in plan["data_d2d_channels"]}
    clock_names = {item["d2d_name"] for item in plan["clock_d2d_channels"]}
    assert not (data_names & clock_names), "Data d2d and clock d2d names overlap"

    for tier in [TIER_UPPER, TIER_BOTTOM]:
        cell = wrapper_top.cells[TIER_INSTANCES[tier]]
        module = wrapper_design.get_module(TIER_MODULES[tier])
        assert set(cell.connections.keys()) == set(module.ports.keys()), (
            "Wrapper instance {} connections do not match module ports".format(TIER_INSTANCES[tier])
        )
        assert cell.port_directions == {name: port.direction for name, port in module.ports.items()}, (
            "Wrapper instance {} port_directions do not match module ports".format(TIER_INSTANCES[tier])
        )

    assert_no_multiple_wrapper_drivers(wrapper_top)
    assert_clock_direct_fanout_not_backdriven(wrapper_top, clock_port_map, plan)


def assert_no_multiple_wrapper_drivers(wrapper_top):
    drivers = defaultdict(list)
    for port in wrapper_top.ports.values():
        if port.direction != "input":
            continue
        for bit in port.bits:
            if isinstance(bit, int):
                drivers[bit].append("wrapper_input:{}".format(port.name))
    for cell in wrapper_top.cells.values():
        for pin_name, bits in cell.connections.items():
            direction = cell.port_directions.get(pin_name)
            assert direction in SUPPORTED_CELL_PIN_DIRECTIONS, (
                "Wrapper cell {} missing direction for port {}".format(cell.name, pin_name)
            )
            if direction != "output":
                continue
            for bit in bits:
                if isinstance(bit, int):
                    drivers[bit].append("{}.{}".format(cell.name, pin_name))
    multiple = {bit: names for bit, names in drivers.items() if len(names) > 1}
    assert not multiple, "Wrapper top has multiple drivers per bit: {}".format(multiple)


def assert_clock_direct_fanout_not_backdriven(wrapper_top, clock_port_map, plan):
    generated = set(clock_port_map.get("generated_clock_channels", []))
    if generated:
        return
    source_clock_label = clock_port_map["source_clock_bit_label"]
    clock_bit = None
    for entry in plan["source_ports"]:
        for index, bit_label in enumerate(entry["bit_labels"]):
            if bit_label == source_clock_label:
                clock_bit = entry["wrapper_bits"][index]
    assert clock_bit is not None, "Source clock bit not found in wrapper source port plan"
    output_drivers = []
    for cell in wrapper_top.cells.values():
        for pin_name, bits in cell.connections.items():
            if bits != [clock_bit]:
                continue
            if cell.port_directions.get(pin_name) == "output":
                output_drivers.append("{}.{}".format(cell.name, pin_name))
    assert not output_drivers, (
        "Source clock direct fanout is back-driven by tier output ports: {}".format(output_drivers)
    )

