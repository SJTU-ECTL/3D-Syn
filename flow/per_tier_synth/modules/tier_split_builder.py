"""Build and validate the two tier modules from classified partition data."""

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


from modules.tier_split_analysis import canonical_name, other_tier, read_clk_port_name

def make_split_module(src_module, new_name):
    return Module(
        name=new_name,
        attributes=json.loads(json.dumps(src_module.attributes)),
        parameter_default_values=json.loads(json.dumps(src_module.parameter_default_values)),
        ports=OrderedDict(),
        cells=OrderedDict(),
        memories=OrderedDict(),
        netnames=OrderedDict(),
        extras=json.loads(json.dumps(src_module.extras)),
    )


def add_canonical_port(module, module_state, port_name, direction, bit):
    assert port_name not in module_state["port_names"], "Duplicate canonical port {} in module {}".format(port_name, module.name)
    add_port(module, port_name, direction, [bit])
    add_netname(module, port_name, [bit], hide_name=0, attributes={})
    module_state["port_names"].add(port_name)
    module_state["ports"].append({
        "name": port_name,
        "direction": direction,
        "bit": bit,
    })


def populate_ports(upper_module, bottom_module, primary_cases, internal_cases):
    upper_state = {"port_names": set(), "ports": []}
    bottom_state = {"port_names": set(), "ports": []}

    def module_for_tier(tier):
        return upper_module if tier == TIER_UPPER else bottom_module

    def state_for_tier(tier):
        return upper_state if tier == TIER_UPPER else bottom_state

    ordered_primary = sorted(primary_cases, key=lambda item: (item["kind"], item["port_name"], -1 if item["bit_index"] is None else item["bit_index"]))
    for item in ordered_primary:
        port_name = item["port_name"]
        bit_index = item["bit_index"]
        bit = item["bit"]
        owner_tier = item["owner_tier"]
        owner_module = module_for_tier(owner_tier)
        owner_state = state_for_tier(owner_tier)
        non_owner_tier = other_tier(owner_tier)
        non_owner_module = module_for_tier(non_owner_tier)
        non_owner_state = state_for_tier(non_owner_tier)

        if item["kind"] == BIT_KIND_PI:
            owner_name = canonical_name("pi", port_name, bit_index)
            cross_name = canonical_name("d2d_pi", port_name, bit_index)
            if item["case"] == "PI-1":
                add_canonical_port(owner_module, owner_state, owner_name, "input", bit)
            elif item["case"] == "PI-2":
                add_canonical_port(owner_module, owner_state, owner_name, "input", bit)
                add_canonical_port(owner_module, owner_state, cross_name, "output", bit)
                add_canonical_port(non_owner_module, non_owner_state, cross_name, "input", bit)
            elif item["case"] == "PI-3":
                add_canonical_port(owner_module, owner_state, owner_name, "input", bit)
                add_canonical_port(owner_module, owner_state, cross_name, "output", bit)
                add_canonical_port(non_owner_module, non_owner_state, cross_name, "input", bit)
            elif item["case"] == "PI-4":
                pass
            else:
                raise AssertionError("Unsupported PI case: {}".format(item["case"]))
            continue

        owner_name = canonical_name("po", port_name, bit_index)
        cross_name = canonical_name("d2d_po", port_name, bit_index)
        if item["case"] == "PO-1":
            add_canonical_port(owner_module, owner_state, owner_name, "output", bit)
        elif item["case"] == "PO-2":
            add_canonical_port(owner_module, owner_state, owner_name, "output", bit)
            add_canonical_port(owner_module, owner_state, cross_name, "output", bit)
            add_canonical_port(non_owner_module, non_owner_state, cross_name, "input", bit)
        elif item["case"] == "PO-3":
            driver_tier = item["driver_tier"]
            driver_module = module_for_tier(driver_tier)
            driver_state = state_for_tier(driver_tier)
            add_canonical_port(driver_module, driver_state, cross_name, "output", bit)
            add_canonical_port(owner_module, owner_state, cross_name, "input", bit)
            add_canonical_port(owner_module, owner_state, owner_name, "output", bit)
        elif item["case"] == "PO-4":
            add_canonical_port(owner_module, owner_state, owner_name, "output", bit)
        else:
            raise AssertionError("Unsupported PO case: {}".format(item["case"]))

    ordered_internal = sorted(internal_cases, key=lambda item: item["debug_name"])
    for item in ordered_internal:
        if item["case"] in ("NET-1", "NET-5"):
            continue
        assert item["case"] in ("NET-2", "NET-3"), "Unsupported internal case: {}".format(item["case"])
        d2d_name = "__d2d_{}".format(item["debug_name"].replace("[", "_").replace("]", "").replace(":", "_").replace(".", "_"))
        driver_tier = item["driver_tier"]
        receiver_tier = other_tier(driver_tier)
        add_canonical_port(module_for_tier(driver_tier), state_for_tier(driver_tier), d2d_name, "output", item["bit"])
        add_canonical_port(module_for_tier(receiver_tier), state_for_tier(receiver_tier), d2d_name, "input", item["bit"])
        item["d2d_name"] = d2d_name

    return upper_state, bottom_state


def copy_cells_to_split_modules(src_module, upper_module, bottom_module, cell_tiers):
    counts = {TIER_UPPER: 0, TIER_BOTTOM: 0}
    for cell_name, cell in src_module.cells.items():
        tier = cell_tiers[cell_name]
        dst_module = upper_module if tier == TIER_UPPER else bottom_module
        add_cell(
            dst_module,
            cell.name,
            cell.type,
            cell.connections,
            hide_name=cell.hide_name,
            parameters=cell.parameters,
            attributes=cell.attributes,
            port_directions=cell.port_directions,
            extras=cell.extras,
        )
        counts[tier] += 1
    return counts


def build_split_design(src_design, new_module):
    return Design(
        creator=src_design.creator,
        modules=OrderedDict([(new_module.name, new_module)]),
        models=json.loads(json.dumps(src_design.models)),
        extras=json.loads(json.dumps(src_design.extras)),
    )


def build_split_plan(src_module, primary_cases, internal_cases, upper_state, bottom_state, cell_counts):
    case_counts = OrderedDict()
    for item in primary_cases + internal_cases:
        case_counts[item["case"]] = case_counts.get(item["case"], 0) + 1

    return OrderedDict([
        ("source", OrderedDict([
            ("netlist_v", NETLIST_V),
            ("partition_txt", PARTITION_TXT),
            ("source_json", SOURCE_JSON),
            ("top_module", src_module.name),
        ])),
        ("outputs", OrderedDict([
            ("bottom_json", BOTTOM_COARSE_JSON),
            ("upper_json", UPPER_COARSE_JSON),
        ])),
        ("summary", OrderedDict([
            ("source_cells", len(src_module.cells)),
            ("source_ports", len(src_module.ports)),
            ("upper_cells", cell_counts[TIER_UPPER]),
            ("bottom_cells", cell_counts[TIER_BOTTOM]),
            ("upper_ports", len(upper_state["ports"])),
            ("bottom_ports", len(bottom_state["ports"])),
            ("case_counts", case_counts),
        ])),
        ("modules", OrderedDict([
            ("upper", OrderedDict([
                ("name", UPPER_COARSE_MODULE_NAME),
                ("ports", upper_state["ports"]),
            ])),
            ("bottom", OrderedDict([
                ("name", BOTTOM_COARSE_MODULE_NAME),
                ("ports", bottom_state["ports"]),
            ])),
        ])),
        ("primary_bits", primary_cases),
        ("internal_bits", internal_cases),
    ])


def write_split_plan(plan):
    with open(SPLIT_PLAN_JSON, "w") as fp:
        json.dump(plan, fp, indent=2)
        fp.write("\n")
    with open(SPLIT_PLAN_JSON) as fp:
        loaded = json.load(fp)
    assert loaded == plan, "SPLIT_PLAN_JSON readback differs from written plan"


def find_clock_primary_case(primary_cases, clk_port_name):
    matches = []
    for item in primary_cases:
        if item["kind"] != BIT_KIND_PI:
            continue
        if item["port_name"] != clk_port_name:
            continue
        matches.append(item)
    assert matches, "Clock source port {} not found in primary split cases".format(clk_port_name)
    assert len(matches) == 1, "Clock source port {} maps to multiple primary split cases".format(clk_port_name)
    clock_primary = matches[0]
    assert clock_primary["case"] in ("PI-1", "PI-2", "PI-3"), (
        "Clock primary bit must map to a live PI case, got {}".format(clock_primary["case"])
    )
    assert clock_primary["bit_index"] in (None, 0), (
        "Clock primary input must resolve to a unique scalar bit, got bit_index={}".format(clock_primary["bit_index"])
    )
    return clock_primary


def clock_pin_names_for_library(library):
    assert library in CLOCK_PIN_NAMES_BY_LIBRARY, "Unsupported library for clock pin names: {}".format(library)
    return CLOCK_PIN_NAMES_BY_LIBRARY[library]


def base_pin_name(pin_name):
    return pin_name.split("[", 1)[0]


def collect_clock_set(src_module, clock_pin_names):
    clock_bits = set()
    for cell in src_module.cells.values():
        for pin_name, bits in cell.connections.items():
            if cell.port_directions.get(pin_name) != "input":
                continue
            if base_pin_name(pin_name) not in clock_pin_names:
                continue
            assert len(bits) == 1, "Clock-like pin must remain scalar: {}.{} -> {}".format(cell.name, pin_name, bits)
            bit = bits[0]
            assert isinstance(bit, int), "Clock-like pin must connect to an integer bit: {}.{} -> {!r}".format(
                cell.name,
                pin_name,
                bit,
            )
            clock_bits.add(bit)
    return clock_bits


def collect_carrier_clock_bits(internal_cases, clock_set):
    carrier_bits = set()
    for item in internal_cases:
        if item["case"] not in ("NET-2", "NET-3"):
            continue
        if item["bit"] in clock_set:
            carrier_bits.add(item["bit"])
    return carrier_bits


def build_tier_clock_map_entry(module_name, module_state, source_clock_bit, carrier_bit_to_name):
    source_clock_ports = []
    direct_source_clock_ports = []
    generated_clock_ports = []
    generated_clock_input_ports = []
    related_ports = []
    all_input_ports = []

    for item in module_state["ports"]:
        port_name = item["name"]
        direction = item["direction"]
        bit = item["bit"]
        if bit == source_clock_bit:
            source_clock_ports.append(
                OrderedDict([
                    ("port_name", port_name),
                    ("direction", direction),
                ])
            )
            related_ports.append(port_name)
            if direction == "input":
                direct_source_clock_ports.append(port_name)
                all_input_ports.append(port_name)
        elif bit in carrier_bit_to_name:
            generated_clock_ports.append(
                OrderedDict([
                    ("port_name", port_name),
                    ("direction", direction),
                    ("d2d_name", carrier_bit_to_name[bit]),
                ])
            )
            related_ports.append(port_name)
            if direction == "input":
                generated_clock_input_ports.append(port_name)
                all_input_ports.append(port_name)

    source_clock_ports = sorted(source_clock_ports, key=lambda item: (item["port_name"], item["direction"]))
    direct_source_clock_ports = sorted(direct_source_clock_ports)
    generated_clock_ports = sorted(
        generated_clock_ports,
        key=lambda item: (item["d2d_name"], item["port_name"], item["direction"]),
    )
    generated_clock_input_ports = sorted(generated_clock_input_ports)
    related_ports = sorted(related_ports)
    all_input_ports = sorted(all_input_ports)
    legacy_clock_input_port = all_input_ports[0] if len(all_input_ports) == 1 else None

    return OrderedDict([
        ("module_name", module_name),
        ("clock_input_port", legacy_clock_input_port),
        ("clock_related_ports", related_ports),
        ("source_clock_ports", source_clock_ports),
        ("direct_source_clock_ports", direct_source_clock_ports),
        ("generated_clock_ports", generated_clock_ports),
        ("generated_clock_input_ports", generated_clock_input_ports),
    ])


def build_clock_port_map(src_module, primary_cases, internal_cases, upper_state, bottom_state):
    clk_port_name = read_clk_port_name(DESIGN_SDC_FILE)
    clock_primary = find_clock_primary_case(primary_cases, clk_port_name)
    source_clock_bit = clock_primary["bit"]
    clock_set = collect_clock_set(src_module, clock_pin_names_for_library(LIBRARY))
    assert source_clock_bit in clock_set, "Source clock bit {} was not identified in the source-level clock set".format(
        source_clock_bit
    )
    carrier_clock_bits = collect_carrier_clock_bits(internal_cases, clock_set)
    carrier_bit_to_name = OrderedDict()
    for item in sorted(internal_cases, key=lambda entry: (entry["bit"], entry.get("d2d_name") or "")):
        if item["bit"] not in carrier_clock_bits:
            continue
        assert item.get("d2d_name"), "Clock carrier bit missing d2d_name: {}".format(item)
        assert item["bit"] not in carrier_bit_to_name, (
            "Clock carrier bit mapped to multiple d2d names: bit={} existing={} new={}".format(
                item["bit"], carrier_bit_to_name.get(item["bit"]), item["d2d_name"]
            )
        )
        carrier_bit_to_name[item["bit"]] = item["d2d_name"]
    return OrderedDict([
        ("source_clock_port_name", clk_port_name),
        ("source_clock_bit_label", clock_primary["bit_label"]),
        ("source_clock_bit_index", clock_primary["bit_index"]),
        ("source_clock_bit", source_clock_bit),
        ("clock_case", clock_primary["case"]),
        ("owner_tier", clock_primary["owner_tier"]),
        ("tiers", OrderedDict([
            (
                TIER_BOTTOM,
                build_tier_clock_map_entry(BOTTOM_COARSE_MODULE_NAME, bottom_state, source_clock_bit, carrier_bit_to_name),
            ),
            (
                TIER_UPPER,
                build_tier_clock_map_entry(UPPER_COARSE_MODULE_NAME, upper_state, source_clock_bit, carrier_bit_to_name),
            ),
        ])),
        ("generated_clock_channels", list(carrier_bit_to_name.values())),
    ])


def write_clock_port_map(clock_port_map):
    with open(CLOCK_PORT_MAP_JSON, "w") as fp:
        json.dump(clock_port_map, fp, indent=2)
        fp.write("\n")
    with open(CLOCK_PORT_MAP_JSON) as fp:
        loaded = json.load(fp)
    assert loaded == clock_port_map, "CLOCK_PORT_MAP_JSON readback differs from written map"


def assert_split_plan_contract(plan, src_module, primary_entries, internal_cases, upper_module, bottom_module, cell_tiers):
    assert plan["summary"]["source_cells"] == len(src_module.cells)
    assert plan["summary"]["source_ports"] == len(src_module.ports)
    assert plan["summary"]["upper_cells"] == sum(1 for tier in cell_tiers.values() if tier == TIER_UPPER)
    assert plan["summary"]["bottom_cells"] == sum(1 for tier in cell_tiers.values() if tier == TIER_BOTTOM)
    assert len(upper_module.cells) == plan["summary"]["upper_cells"]
    assert len(bottom_module.cells) == plan["summary"]["bottom_cells"]

    source_primary_labels = {entry["bit_label"] for entry in primary_entries}
    plan_primary_labels = {entry["bit_label"] for entry in plan["primary_bits"]}
    assert plan_primary_labels == source_primary_labels, (
        "SPLIT_PLAN_JSON primary bit set mismatch: missing={} extra={}".format(
            sorted(source_primary_labels - plan_primary_labels)[:20],
            sorted(plan_primary_labels - source_primary_labels)[:20],
        )
    )
    assert len(plan["primary_bits"]) == len(primary_entries)

    for module_name, module in [(UPPER_COARSE_MODULE_NAME, upper_module), (BOTTOM_COARSE_MODULE_NAME, bottom_module)]:
        for cell_name in module.cells:
            assert cell_tiers[cell_name] == (TIER_UPPER if module_name == UPPER_COARSE_MODULE_NAME else TIER_BOTTOM), (
                "Tier module {} contains cell {} assigned to {}".format(module_name, cell_name, cell_tiers[cell_name])
            )

    d2d_by_name = {}
    clock_d2d = set()
    data_d2d = set()
    for item in internal_cases:
        if item["case"] in ("NET-1", "NET-5"):
            assert "d2d_name" not in item, "Non-cross-tier internal bit has d2d_name: {}".format(item)
            continue
        assert item["case"] in ("NET-2", "NET-3")
        assert item.get("d2d_name"), "Cross-tier internal bit missing d2d_name: {}".format(item)
        assert item["d2d_name"] not in d2d_by_name, "Duplicate d2d_name: {}".format(item["d2d_name"])
        d2d_by_name[item["d2d_name"]] = item["bit"]
        assert item["driver_tier"] in item["user_tiers"] or item["user_tiers"], (
            "Cross-tier internal bit has no consumer tiers: {}".format(item)
        )

    for module_state in plan["modules"].values():
        seen_ports = set()
        for port in module_state["ports"]:
            assert port["name"] not in seen_ports, "Duplicate tier port in split plan: {}".format(port)
            seen_ports.add(port["name"])
            if port["name"].startswith("__d2d_") and port["name"] in d2d_by_name:
                data_d2d.add(port["name"])

    assert len(data_d2d) == len(d2d_by_name), (
        "Not all internal d2d names appear in tier ports: missing={}".format(
            sorted(set(d2d_by_name) - data_d2d)[:20]
        )
    )
    assert not (data_d2d & clock_d2d), "Data d2d and clock d2d sets overlap"


def assert_clock_port_map_contract(clock_port_map, src_module, primary_cases, internal_cases):
    assert clock_port_map["source_clock_port_name"] in src_module.ports
    clk_port = src_module.ports[clock_port_map["source_clock_port_name"]]
    assert clk_port.direction == "input", "Source clock port must be primary input"
    assert len(clk_port.bits) == 1, "The current clock model supports only a scalar source clock"
    assert clock_port_map["source_clock_bit"] == clk_port.bits[0]
    assert clock_port_map["clock_case"] in {"PI-1", "PI-2", "PI-3"}

    generated = set(clock_port_map.get("generated_clock_channels", []))
    internal_d2d = {item.get("d2d_name") for item in internal_cases if item.get("d2d_name")}
    assert generated <= internal_d2d, (
        "Generated clock channels not found in internal d2d metadata: {}".format(sorted(generated - internal_d2d))
    )
    for tier, entry in clock_port_map["tiers"].items():
        assert tier in {TIER_BOTTOM, TIER_UPPER}
        related = entry["clock_related_ports"]
        source_clock_ports = entry.get("source_clock_ports", [])
        direct_source_clock_ports = entry.get("direct_source_clock_ports", [])
        generated_clock_ports = entry.get("generated_clock_ports", [])
        generated_clock_input_ports = entry.get("generated_clock_input_ports", [])

        source_names = []
        for item in source_clock_ports:
            assert item["direction"] in {"input", "output"}, (
                "Unexpected source clock port direction in tier {}: {}".format(tier, item)
            )
            source_names.append(item["port_name"])
        generated_names = []
        for item in generated_clock_ports:
            assert item["direction"] in {"input", "output"}, (
                "Unexpected generated clock port direction in tier {}: {}".format(tier, item)
            )
            assert item["d2d_name"] in generated, (
                "Tier {} generated clock port references unknown d2d channel: {}".format(tier, item)
            )
            generated_names.append(item["port_name"])

        expected_related = sorted(source_names + generated_names)
        assert related == expected_related, (
            "Tier {} clock_related_ports mismatch: expected {} got {}".format(tier, expected_related, related)
        )

        expected_direct = sorted(item["port_name"] for item in source_clock_ports if item["direction"] == "input")
        assert direct_source_clock_ports == expected_direct, (
            "Tier {} direct_source_clock_ports mismatch: expected {} got {}".format(
                tier, expected_direct, direct_source_clock_ports
            )
        )
        assert len(direct_source_clock_ports) <= 1, (
            "Tier {} must not expose multiple direct source clock inputs: {}".format(tier, direct_source_clock_ports)
        )

        expected_generated_inputs = sorted(
            item["port_name"] for item in generated_clock_ports if item["direction"] == "input"
        )
        assert generated_clock_input_ports == expected_generated_inputs, (
            "Tier {} generated_clock_input_ports mismatch: expected {} got {}".format(
                tier, expected_generated_inputs, generated_clock_input_ports
            )
        )

        all_input_ports = sorted(direct_source_clock_ports + generated_clock_input_ports)
        if len(all_input_ports) == 1:
            assert entry["clock_input_port"] == all_input_ports[0], (
                "Tier {} legacy clock_input_port mismatch: expected {} got {}".format(
                    tier, all_input_ports[0], entry["clock_input_port"]
                )
            )
        else:
            assert entry["clock_input_port"] is None, (
                "Tier {} legacy clock_input_port must be None when multiple/no clock inputs exist, got {}".format(
                    tier, entry["clock_input_port"]
                )
            )


def print_summary(plan):
    summary = plan["summary"]
    print("[split] source_cells={} source_ports={}".format(summary["source_cells"], summary["source_ports"]))
    print("[split] upper_cells={} upper_ports={}".format(summary["upper_cells"], summary["upper_ports"]))
    print("[split] bottom_cells={} bottom_ports={}".format(summary["bottom_cells"], summary["bottom_ports"]))
    print("[split] case_counts={}".format(dict(summary["case_counts"])))

