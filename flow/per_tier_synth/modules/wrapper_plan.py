"""Plan wrapper ports, D2D channels, aliases, and tier-interface actions."""

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



def other_tier(tier):
    assert tier in {TIER_BOTTOM, TIER_UPPER}, "Unexpected tier: {}".format(tier)
    return TIER_UPPER if tier == TIER_BOTTOM else TIER_BOTTOM


def canonical_name(kind, port_name, bit_index):
    if bit_index is None:
        return "__{}_{}".format(kind, port_name)
    return "__{}_{}_{}".format(kind, port_name, bit_index)


def safe_label(label):
    value = re.sub(r"[^A-Za-z0-9_$]+", "_", label).strip("_")
    assert value, "Cannot build safe label from {!r}".format(label)
    if value[0].isdigit():
        value = "b_" + value
    return value


def wrapper_primary_port_name(kind, bit_label):
    assert kind in {"PI", "PO"}, "Unexpected primary kind: {}".format(kind)
    prefix = "__syn3d_pi_" if kind == "PI" else "__syn3d_po_"
    return prefix + safe_label(bit_label)

def build_source_port_plan(source_top):
    allocator = BitAllocator(start=1)
    source_bit_to_wrapper_bit = {}
    label_to_wrapper_bit = OrderedDict()
    source_ports = []

    for port_name, port in source_top.ports.items():
        assert port.direction in SUPPORTED_SOURCE_PORT_DIRECTIONS, (
            "Unsupported source port direction {} on {}".format(port.direction, port_name)
        )
        labels = port_bit_labels(source_top, port_name)
        assert len(labels) == len(port.bits), "Source port label/bit length mismatch for {}".format(port_name)
        wrapper_bits = []
        bit_labels = []
        for index, bit in enumerate(port.bits):
            bit_label = labels[index][1]
            assert bit_label not in label_to_wrapper_bit, "Duplicate source bit label: {}".format(bit_label)
            if isinstance(bit, int):
                if bit not in source_bit_to_wrapper_bit:
                    source_bit_to_wrapper_bit[bit] = allocator.allocate_one()
                wrapper_bit = source_bit_to_wrapper_bit[bit]
            else:
                assert bit in {"0", "1"}, "Source top port constant must be 0/1: {} -> {!r}".format(bit_label, bit)
                wrapper_bit = bit
            wrapper_bits.append(wrapper_bit)
            bit_labels.append(bit_label)
            label_to_wrapper_bit[bit_label] = wrapper_bit
        source_ports.append(
            OrderedDict(
                [
                    ("name", port_name),
                    ("direction", port.direction),
                    ("width", len(port.bits)),
                    ("bit_labels", bit_labels),
                    ("wrapper_bits", wrapper_bits),
                    ("offset", port.offset),
                    ("upto", port.upto),
                    ("signed", port.signed),
                    ("single_bit_vector", port_shape(source_top, port_name)["single_bit_vector"]),
                ]
            )
        )

    return {
        "source_ports": source_ports,
        "source_bit_to_wrapper_bit": source_bit_to_wrapper_bit,
        "label_to_wrapper_bit": label_to_wrapper_bit,
        "allocator": allocator,
    }


def index_primary_bits(split_plan):
    by_label = OrderedDict()
    for item in split_plan["primary_bits"]:
        label = item["bit_label"]
        assert label not in by_label, "Duplicate primary bit label in split plan: {}".format(label)
        assert item["kind"] in {"PI", "PO"}, "Unexpected primary bit kind: {}".format(item)
        by_label[label] = item
    return by_label


def analyze_primary_output_aliases(primary_by_label):
    by_source_bit = defaultdict(list)
    for item in primary_by_label.values():
        if isinstance(item["bit"], int):
            by_source_bit[item["bit"]].append(item)

    alias_to_canonical = {}
    alias_groups = []
    for source_bit, items in by_source_bit.items():
        if len(items) == 1:
            continue

        kinds = {item["kind"] for item in items}
        assert kinds == {"PO"}, (
            "Unsupported aliased primary bit {} with kinds {}: {}".format(
                source_bit, sorted(kinds), [item["bit_label"] for item in items]
            )
        )
        signatures = {
            (
                item["case"],
                item["owner_tier"],
                item.get("driver_tier"),
                tuple(item.get("user_tiers", [])),
            )
            for item in items
        }
        assert len(signatures) == 1, (
            "Aliased primary outputs for source bit {} have incompatible split semantics: {}".format(
                source_bit,
                [
                    {
                        "bit_label": item["bit_label"],
                        "case": item["case"],
                        "owner_tier": item["owner_tier"],
                        "driver_tier": item.get("driver_tier"),
                        "user_tiers": item.get("user_tiers", []),
                    }
                    for item in items
                ],
            )
        )

        signature = next(iter(signatures))
        canonical = items[0]["bit_label"]
        aliases = [item["bit_label"] for item in items[1:]]
        for alias in aliases:
            assert alias not in alias_to_canonical, "Duplicate primary output alias: {}".format(alias)
            alias_to_canonical[alias] = canonical
        alias_groups.append(
            OrderedDict(
                [
                    ("source_bit", source_bit),
                    ("canonical_bit_label", canonical),
                    ("alias_bit_labels", aliases),
                    (
                        "split_signature",
                        OrderedDict(
                            [
                                ("case", signature[0]),
                                ("owner_tier", signature[1]),
                                ("driver_tier", signature[2]),
                                ("user_tiers", list(signature[3])),
                            ]
                        ),
                    ),
                ]
            )
        )

    return alias_to_canonical, alias_groups


def index_internal_d2d(split_plan):
    by_name = OrderedDict()
    for item in split_plan["internal_bits"]:
        if item["case"] in {"NET-1", "NET-5"}:
            assert "d2d_name" not in item, "Non-cross-tier internal bit has d2d_name: {}".format(item)
            continue
        assert item["case"] in {"NET-2", "NET-3"}, "Unsupported internal split case: {}".format(item)
        d2d_name = item.get("d2d_name")
        assert d2d_name, "Cross-tier internal bit missing d2d_name: {}".format(item)
        assert d2d_name not in by_name, "Duplicate d2d_name in split plan: {}".format(d2d_name)
        by_name[d2d_name] = item
    return by_name


class TierActionPlan(object):
    def __init__(self, tier, module):
        self.tier = tier
        self.module_name = module.name
        self.original_ports = set(module.ports.keys())
        self.actions = OrderedDict()

    def _check_common(self, module, port_name, expected_direction, expected_bit):
        assert port_name in module.ports, "Missing tier port {}.{}".format(module.name, port_name)
        port = module.ports[port_name]
        assert port.direction == expected_direction, (
            "Tier port {}.{} direction mismatch: {} != {}".format(
                module.name, port_name, port.direction, expected_direction
            )
        )
        assert len(port.bits) == 1, "Tier port {}.{} must be scalar".format(module.name, port_name)
        assert port.bits[0] == expected_bit, (
            "Tier port {}.{} bit mismatch: {} != {}".format(module.name, port_name, port.bits[0], expected_bit)
        )

    def keep(self, module, port_name, new_name, expected_direction, expected_bit, wrapper_bit, kind, reason, source):
        self._check_common(module, port_name, expected_direction, expected_bit)
        assert port_name not in self.actions, (
            "Tier port {}.{} already has an action".format(module.name, port_name)
        )
        self.actions[port_name] = OrderedDict(
            [
                ("action", "rename" if port_name != new_name else "keep"),
                ("old_name", port_name),
                ("new_name", new_name),
                ("direction", expected_direction),
                ("bit", expected_bit),
                ("wrapper_bit", wrapper_bit),
                ("kind", kind),
                ("reason", reason),
                ("source", source),
            ]
        )

    def remove(self, module, port_name, expected_direction, expected_bit, reason, source):
        self._check_common(module, port_name, expected_direction, expected_bit)
        assert port_name not in self.actions, (
            "Tier port {}.{} already has an action".format(module.name, port_name)
        )
        self.actions[port_name] = OrderedDict(
            [
                ("action", "remove"),
                ("old_name", port_name),
                ("new_name", None),
                ("direction", expected_direction),
                ("bit", expected_bit),
                ("wrapper_bit", None),
                ("kind", "removed_legacy_carrier"),
                ("reason", reason),
                ("source", source),
            ]
        )

    def assert_complete(self):
        missing = sorted(self.original_ports - set(self.actions.keys()))
        assert not missing, (
            "Tier {} has ports without wrapper action: {}".format(self.module_name, missing[:20])
        )


def build_tier_action_plans(tier_modules, split_plan, source_port_plan, clock_port_map):
    primary_by_label = index_primary_bits(split_plan)
    primary_alias_to_canonical, primary_output_alias_groups = analyze_primary_output_aliases(primary_by_label)
    internal_by_name = index_internal_d2d(split_plan)
    generated_clock_channels = set(clock_port_map.get("generated_clock_channels", []))
    assert generated_clock_channels <= set(internal_by_name.keys()), (
        "Generated clock channels missing from internal d2d metadata: {}".format(
            sorted(generated_clock_channels - set(internal_by_name.keys()))
        )
    )

    plans = {
        TIER_UPPER: TierActionPlan(TIER_UPPER, tier_modules[TIER_UPPER]),
        TIER_BOTTOM: TierActionPlan(TIER_BOTTOM, tier_modules[TIER_BOTTOM]),
    }
    primary_connections = []
    tier_interface_rewrite = []
    data_d2d_channels = []
    clock_d2d_channels = []
    d2d_name_to_wrapper_bit = OrderedDict()

    def record_action(tier, action):
        if action["action"] == "remove":
            new_direction = None
        else:
            new_direction = action["direction"]
        tier_interface_rewrite.append(
            OrderedDict(
                [
                    ("tier", tier),
                    ("original_port_name", action["old_name"]),
                    ("new_wrapper_facing_port_name", action["new_name"]),
                    ("original_direction", action["direction"]),
                    ("new_direction", new_direction),
                    ("source_split_case", action["source"].get("case")),
                    ("reason", action["reason"]),
                ]
            )
        )

    def keep_primary(tier, old_name, direction, item, wrapper_bit, reason):
        new_name = wrapper_primary_port_name(item["kind"], item["bit_label"])
        plans[tier].keep(
            tier_modules[tier],
            old_name,
            new_name,
            direction,
            item["bit"],
            wrapper_bit,
            "primary_{}".format(item["kind"].lower()),
            reason,
            item,
        )
        return new_name

    def remove_primary(tier, old_name, direction, item, reason):
        plans[tier].remove(tier_modules[tier], old_name, direction, item["bit"], reason, item)

    for bit_label, item in primary_by_label.items():
        wrapper_bit = source_port_plan["label_to_wrapper_bit"][bit_label]
        owner = item["owner_tier"]
        other = other_tier(owner)
        connected_tier_ports = []
        removed_legacy_ports = []
        assert owner in {TIER_BOTTOM, TIER_UPPER}, "Unexpected primary owner tier: {}".format(item)

        if item["kind"] == "PI":
            owner_name = canonical_name("pi", item["port_name"], item["bit_index"])
            cross_name = canonical_name("d2d_pi", item["port_name"], item["bit_index"])
            if item["case"] == "PI-1":
                new_name = keep_primary(owner, owner_name, "input", item, wrapper_bit, "primary input direct")
                connected_tier_ports.append({"tier": owner, "port": new_name, "direction": "input"})
            elif item["case"] in {"PI-2", "PI-3"}:
                if owner in item["user_tiers"]:
                    new_name = keep_primary(owner, owner_name, "input", item, wrapper_bit, "primary input direct")
                    connected_tier_ports.append({"tier": owner, "port": new_name, "direction": "input"})
                else:
                    remove_primary(owner, owner_name, "input", item, "unused owner-side primary input carrier")
                    removed_legacy_ports.append({"tier": owner, "port": owner_name})
                assert other in item["user_tiers"], "PI cross case missing opposite-tier user: {}".format(item)
                new_name = keep_primary(other, cross_name, "input", item, wrapper_bit, "primary input direct cross-tier consumer")
                connected_tier_ports.append({"tier": other, "port": new_name, "direction": "input"})
                remove_primary(owner, cross_name, "output", item, "remove source-clock/data carrier output")
                removed_legacy_ports.append({"tier": owner, "port": cross_name})
            elif item["case"] == "PI-4":
                assert not item["user_tiers"], "PI-4 must have no user tiers: {}".format(item)
            else:
                raise AssertionError("Unsupported PI case: {}".format(item))
        elif item["kind"] == "PO":
            owner_name = canonical_name("po", item["port_name"], item["bit_index"])
            cross_name = canonical_name("d2d_po", item["port_name"], item["bit_index"])
            alias_of = primary_alias_to_canonical.get(bit_label)
            if alias_of is not None:
                if item["case"] == "PO-1":
                    remove_primary(owner, owner_name, "output", item, "redundant aliased primary output carrier")
                    removed_legacy_ports.append({"tier": owner, "port": owner_name})
                elif item["case"] == "PO-2":
                    remove_primary(owner, owner_name, "output", item, "redundant aliased primary output carrier")
                    remove_primary(other, cross_name, "input", item, "redundant aliased primary output consumer")
                    remove_primary(owner, cross_name, "output", item, "redundant aliased primary output carrier")
                    removed_legacy_ports.extend(
                        [
                            {"tier": owner, "port": owner_name},
                            {"tier": other, "port": cross_name},
                            {"tier": owner, "port": cross_name},
                        ]
                    )
                elif item["case"] == "PO-3":
                    driver = item["driver_tier"]
                    remove_primary(driver, cross_name, "output", item, "redundant aliased primary output driver")
                    removed_legacy_ports.append({"tier": driver, "port": cross_name})
                    if owner in item["user_tiers"]:
                        remove_primary(owner, cross_name, "input", item, "redundant aliased primary output consumer")
                        removed_legacy_ports.append({"tier": owner, "port": cross_name})
                    remove_primary(owner, owner_name, "output", item, "redundant aliased primary output pass-through")
                    removed_legacy_ports.append({"tier": owner, "port": owner_name})
                else:
                    raise AssertionError(
                        "Unsupported aliased primary output case {} for {}".format(item["case"], bit_label)
                    )
            elif item["case"] == "PO-1":
                new_name = keep_primary(owner, owner_name, "output", item, wrapper_bit, "primary output driver")
                connected_tier_ports.append({"tier": owner, "port": new_name, "direction": "output"})
            elif item["case"] == "PO-2":
                new_name = keep_primary(owner, owner_name, "output", item, wrapper_bit, "primary output driver")
                connected_tier_ports.append({"tier": owner, "port": new_name, "direction": "output"})
                new_name = keep_primary(other, cross_name, "input", item, wrapper_bit, "primary output direct consumer")
                connected_tier_ports.append({"tier": other, "port": new_name, "direction": "input"})
                remove_primary(owner, cross_name, "output", item, "remove duplicate primary output carrier")
                removed_legacy_ports.append({"tier": owner, "port": cross_name})
            elif item["case"] == "PO-3":
                driver = item["driver_tier"]
                assert driver in {TIER_BOTTOM, TIER_UPPER}, "PO-3 missing driver tier: {}".format(item)
                assert driver != owner, "PO-3 driver must differ from owner: {}".format(item)
                new_name = keep_primary(driver, cross_name, "output", item, wrapper_bit, "primary output real driver")
                connected_tier_ports.append({"tier": driver, "port": new_name, "direction": "output"})
                if owner in item["user_tiers"]:
                    new_name = keep_primary(owner, cross_name, "input", item, wrapper_bit, "primary output direct consumer")
                    connected_tier_ports.append({"tier": owner, "port": new_name, "direction": "input"})
                else:
                    remove_primary(owner, cross_name, "input", item, "unused owner-side primary output carrier input")
                    removed_legacy_ports.append({"tier": owner, "port": cross_name})
                remove_primary(owner, owner_name, "output", item, "remove owner-side primary output pass-through")
                removed_legacy_ports.append({"tier": owner, "port": owner_name})
            elif item["case"] == "PO-4":
                assert item["bit"] in {"0", "1"}, "PO-4 must be a constant output: {}".format(item)
                remove_primary(owner, owner_name, "output", item, "constant primary output does not need tier driver")
                removed_legacy_ports.append({"tier": owner, "port": owner_name})
            else:
                raise AssertionError("Unsupported PO case: {}".format(item))
        else:
            raise AssertionError("Unsupported primary kind: {}".format(item))

        primary_connections.append(
            OrderedDict(
                [
                    ("source_bit_label", bit_label),
                    ("kind", item["kind"]),
                    ("case", item["case"]),
                    ("owner_tier", owner),
                    ("driver_tier", item.get("driver_tier")),
                    ("user_tiers", item.get("user_tiers", [])),
                    ("wrapper_port", item["port_name"]),
                    ("wrapper_bit", wrapper_bit),
                    ("physical_driver_alias_of", primary_alias_to_canonical.get(bit_label)),
                    ("connected_tier_ports", connected_tier_ports),
                    ("removed_legacy_carrier_ports", removed_legacy_ports),
                ]
            )
        )

    allocator = source_port_plan["allocator"]
    for d2d_name, item in internal_by_name.items():
        producer = item["driver_tier"]
        consumer = other_tier(producer)
        assert consumer in item["user_tiers"], (
            "Cross-tier d2d {} missing opposite-tier consumer in {}".format(d2d_name, item)
        )
        wrapper_bit = allocator.allocate_one()
        assert d2d_name not in d2d_name_to_wrapper_bit, "Duplicate wrapper d2d bit for {}".format(d2d_name)
        d2d_name_to_wrapper_bit[d2d_name] = wrapper_bit
        kind = "clock_d2d" if d2d_name in generated_clock_channels else "data_d2d"
        plans[producer].keep(
            tier_modules[producer],
            d2d_name,
            d2d_name,
            "output",
            item["bit"],
            wrapper_bit,
            kind,
            "internal cross-tier {}".format(kind),
            item,
        )
        plans[consumer].keep(
            tier_modules[consumer],
            d2d_name,
            d2d_name,
            "input",
            item["bit"],
            wrapper_bit,
            kind,
            "internal cross-tier {}".format(kind),
            item,
        )
        channel = OrderedDict(
            [
                ("d2d_name", d2d_name),
                ("source_debug_name", item["debug_name"]),
                ("case", item["case"]),
                ("producer_tier", producer),
                ("consumer_tier", consumer),
                ("producer_port", d2d_name),
                ("consumer_port", d2d_name),
                ("wrapper_net_bit", wrapper_bit),
            ]
        )
        if d2d_name in generated_clock_channels:
            clock_d2d_channels.append(channel)
        else:
            data_d2d_channels.append(channel)

    for tier, plan in plans.items():
        plan.assert_complete()
        new_names = defaultdict(list)
        for action in plan.actions.values():
            if action["action"] == "remove":
                continue
            new_names[action["new_name"]].append(action["old_name"])
        duplicates = {name: olds for name, olds in new_names.items() if len(olds) > 1}
        assert not duplicates, "Tier {} has duplicate transformed port names: {}".format(tier, duplicates)
        for action in plan.actions.values():
            record_action(tier, action)

    return {
        "plans": plans,
        "primary_connections": primary_connections,
        "primary_output_alias_groups": primary_output_alias_groups,
        "tier_interface_rewrite": tier_interface_rewrite,
        "data_d2d_channels": data_d2d_channels,
        "clock_d2d_channels": clock_d2d_channels,
        "d2d_name_to_wrapper_bit": d2d_name_to_wrapper_bit,
    }

def combine_contract_plan(action_plan, source_port_plan):
    combined = dict(action_plan)
    combined["source_ports"] = source_port_plan["source_ports"]
    return combined


def build_wrapper_plan(source_port_plan, action_plan, clock_port_map, merge_log, wrapper_design):
    wrapper_top = wrapper_design.get_module(WRAPPER_TOP_MODULE_NAME)
    removed_count = sum(
        1
        for tier_plan in action_plan["plans"].values()
        for action in tier_plan.actions.values()
        if action["action"] == "remove"
    )
    return OrderedDict(
        [
            (
                "identity",
                OrderedDict(
                    [
                        ("design", DESIGN),
                        ("library", LIBRARY),
                        ("mapping_mode", CELL_NAME_MAPPING_MODE),
                        ("flow_variant", FLOW_VARIANT),
                    ]
                ),
            ),
            (
                "modules",
                OrderedDict(
                    [
                        ("wrapper_top", WRAPPER_TOP_MODULE_NAME),
                        ("upper_module", UPPER_COARSE_MODULE_NAME),
                        ("bottom_module", BOTTOM_COARSE_MODULE_NAME),
                        ("upper_instance", WRAPPER_UPPER_INSTANCE_NAME),
                        ("bottom_instance", WRAPPER_BOTTOM_INSTANCE_NAME),
                    ]
                ),
            ),
            ("source_ports", source_port_plan["source_ports"]),
            ("tier_interface_rewrite", action_plan["tier_interface_rewrite"]),
            ("primary_connections", action_plan["primary_connections"]),
            ("primary_output_alias_groups", action_plan["primary_output_alias_groups"]),
            ("data_d2d_channels", action_plan["data_d2d_channels"]),
            (
                "clock",
                OrderedDict(
                    [
                        ("source_clock_port", clock_port_map["source_clock_port_name"]),
                        ("source_clock_bit_label", clock_port_map["source_clock_bit_label"]),
                        ("clock_case", clock_port_map["clock_case"]),
                        ("direct_fanout_tier_ports", collect_clock_direct_fanout_ports(action_plan, clock_port_map)),
                        ("clock_d2d_channels", action_plan["clock_d2d_channels"]),
                    ]
                ),
            ),
            ("module_merge_log", merge_log),
            (
                "checks",
                OrderedDict(
                    [
                        ("top_cell_count", len(wrapper_top.cells)),
                        ("data_d2d_count", len(action_plan["data_d2d_channels"])),
                        ("clock_d2d_count", len(action_plan["clock_d2d_channels"])),
                        ("removed_legacy_carrier_count", removed_count),
                        ("primary_output_alias_group_count", len(action_plan["primary_output_alias_groups"])),
                        ("unsupported_count", 0),
                    ]
                ),
            ),
        ]
    )


def collect_clock_direct_fanout_ports(action_plan, clock_port_map):
    if clock_port_map.get("generated_clock_channels"):
        return []
    source_label = clock_port_map["source_clock_bit_label"]
    ports = []
    for item in action_plan["primary_connections"]:
        if item["source_bit_label"] != source_label:
            continue
        for port in item["connected_tier_ports"]:
            ports.append(port)
    return ports


def assert_wrapper_plan_matches_design(wrapper_plan, wrapper_design):
    wrapper_top = wrapper_design.get_module(WRAPPER_TOP_MODULE_NAME)
    assert wrapper_plan["checks"]["top_cell_count"] == len(wrapper_top.cells)
    assert wrapper_plan["checks"]["data_d2d_count"] == len(wrapper_plan["data_d2d_channels"])
    assert wrapper_plan["checks"]["clock_d2d_count"] == len(wrapper_plan["clock"]["clock_d2d_channels"])
    assert wrapper_plan["checks"]["unsupported_count"] == 0
    assert wrapper_plan["modules"]["wrapper_top"] == WRAPPER_TOP_MODULE_NAME
    assert wrapper_plan["modules"]["upper_instance"] in wrapper_top.cells
    assert wrapper_plan["modules"]["bottom_instance"] in wrapper_top.cells

    assert len(wrapper_plan["source_ports"]) == len(wrapper_top.ports)
    for plan_entry, port_name in zip(wrapper_plan["source_ports"], wrapper_top.ports):
        shape = port_shape(wrapper_top, port_name)
        assert plan_entry["name"] == port_name
        assert plan_entry["direction"] == shape["direction"]
        assert plan_entry["width"] == shape["width"]
        assert plan_entry["bit_labels"] == shape["bit_labels"]
        assert plan_entry["single_bit_vector"] == shape["single_bit_vector"]

    d2d_netnames = {
        name
        for name, netname in wrapper_top.netnames.items()
        if netname.attributes.get("syn3d_channel_kind") in {"data_d2d", "clock_d2d"}
    }
    plan_d2d = {item["d2d_name"] for item in wrapper_plan["data_d2d_channels"]}
    plan_d2d.update(item["d2d_name"] for item in wrapper_plan["clock"]["clock_d2d_channels"])
    assert d2d_netnames == plan_d2d, (
        "Wrapper plan d2d netnames mismatch: json={} plan={}".format(sorted(d2d_netnames), sorted(plan_d2d))
    )

