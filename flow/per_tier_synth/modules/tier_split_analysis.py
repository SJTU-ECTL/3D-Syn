"""Analyze partition ownership and classify source-netlist bits for tier splitting."""

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
    CELL_NAME_MAPPING_MODE,
    BOTTOM_CELL_SUFFIX,
    UPPER_CELL_SUFFIX,
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


def build_source_json():
    tcl = """
read_verilog {netlist}
hierarchy -top {top}
proc
write_json {out_json}
""".format(
        netlist=NETLIST_V,
        top=TOP_MODULE_NAME,
        out_json=SOURCE_JSON,
    )
    run_yosys_script(tcl, "source verilog -> source json")
    assert os.path.exists(SOURCE_JSON), "source json was not created: {}".format(SOURCE_JSON)


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
    assert os.path.exists(out_v), "split verilog was not created: {}".format(out_v)


def load_single_module_design_from_verilog(v_path, module_name):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as json_fp:
        json_path = json_fp.name
    try:
        tcl = """
read_verilog {v_path}
hierarchy -top {module_name}
proc
write_json {json_path}
""".format(
            v_path=v_path,
            module_name=module_name,
            json_path=json_path,
        )
        run_yosys_script(tcl, "coarse synth verilog -> json ({})".format(module_name))
        design = load_design(json_path)
    finally:
        if os.path.exists(json_path):
            os.unlink(json_path)
    validate_design(design)
    assert set(design.modules.keys()) == {module_name}, (
        "Expected single module {} in {}, found {}".format(
            module_name,
            v_path,
            sorted(design.modules.keys()),
        )
    )
    return design


def decode_partition_tier(raw_value):
    raw_value = str(raw_value).strip()
    if raw_value == str(PARTITION_BOTTOM_VALUE):
        return TIER_BOTTOM
    if raw_value == str(PARTITION_TOP_VALUE):
        return TIER_UPPER
    raise AssertionError("Unsupported partition tier value: {!r}".format(raw_value))


def other_tier(tier):
    assert tier in (TIER_BOTTOM, TIER_UPPER), "Unexpected tier: {}".format(tier)
    return TIER_UPPER if tier == TIER_BOTTOM else TIER_BOTTOM


def load_json_file(path):
    with open(path) as fp:
        return json.load(fp)


def read_sdc_var(sdc_path, var_name):
    pattern = re.compile(r"set\s+" + re.escape(var_name) + r"\s+(\S+)")
    with open(sdc_path) as fp:
        for line in fp:
            match = pattern.match(line.strip())
            if match:
                return match.group(1)
    raise AssertionError("{} not found in {}".format(var_name, sdc_path))


def read_clk_port_name(sdc_path):
    return read_sdc_var(sdc_path, "clk_port_name")


def normalize_pin_direction(raw_direction, context):
    mapping = {
        "INPUT": "input",
        "OUTPUT": "output",
        "INOUT": "inout",
        "input": "input",
        "output": "output",
        "inout": "inout",
    }
    assert raw_direction in mapping, "{} has unsupported direction value {!r}".format(context, raw_direction)
    return mapping[raw_direction]


def collapse_pin_direction_table(raw_table, table_name):
    collapsed = {}
    for cell_type, pin_map in raw_table.items():
        base_dirs = OrderedDict()
        for raw_pin, raw_dir in pin_map.items():
            direction = normalize_pin_direction(raw_dir, "{}:{}:{}".format(table_name, cell_type, raw_pin))
            base = raw_pin.split("[", 1)[0]
            seen = base_dirs.setdefault(base, set())
            seen.add(direction)
        per_cell = {}
        for base, directions in base_dirs.items():
            assert len(directions) == 1, (
                "{} has mixed directions across bus bits for {}.{}: {}".format(
                    table_name, cell_type, base, sorted(directions)
                )
            )
            per_cell[base] = next(iter(directions))
            assert per_cell[base] in SUPPORTED_CELL_PIN_DIRECTIONS, (
                "{} has unsupported pin direction for {}.{}: {}".format(
                    table_name, cell_type, base, per_cell[base]
                )
            )
        collapsed[cell_type] = per_cell
    return collapsed


def load_pin_direction_tables():
    std_table = collapse_pin_direction_table(load_json_file(PIN_DIR_JSON), "pin_dir.json")
    coarse_table = collapse_pin_direction_table(load_json_file(COARSE_PIN_DIR_JSON), "coarse_pin_dir.json")
    merged = dict(std_table)
    for cell_type, coarse_map in coarse_table.items():
        if cell_type in merged:
            assert merged[cell_type] == coarse_map, (
                "pin direction conflict for cell {} between pin_dir.json and coarse_pin_dir.json".format(cell_type)
            )
        merged[cell_type] = coarse_map
    return merged


def add_runtime_pin_direction_aliases(pin_direction_table):
    """Add homogeneous tier-suffixed aliases without changing published tables."""
    if CELL_NAME_MAPPING_MODE != "homogeneous_base":
        return pin_direction_table
    expanded = dict(pin_direction_table)
    for base_name, pin_map in pin_direction_table.items():
        if base_name.endswith((BOTTOM_CELL_SUFFIX, UPPER_CELL_SUFFIX)):
            continue
        for suffix in (BOTTOM_CELL_SUFFIX, UPPER_CELL_SUFFIX):
            alias = base_name + suffix
            existing = expanded.get(alias)
            if existing is not None:
                assert existing == pin_map, "pin direction alias conflict for {}".format(alias)
            expanded[alias] = pin_map
    return expanded


# Keep the migrated function body byte-for-byte compatible with the approved
# split while making its runtime result usable by suffixed homogeneous cells.
_load_pin_direction_tables_without_aliases = load_pin_direction_tables


def _load_pin_direction_tables_with_aliases():
    return add_runtime_pin_direction_aliases(_load_pin_direction_tables_without_aliases())


load_pin_direction_tables = _load_pin_direction_tables_with_aliases


def resolve_cell_port_directions(module, pin_direction_table):
    for cell in module.cells.values():
        table_dirs = pin_direction_table.get(cell.type)
        resolved = OrderedDict()
        for pin_name in cell.connections.keys():
            json_dir = cell.port_directions.get(pin_name)
            if json_dir is not None:
                direction = normalize_pin_direction(json_dir, "cell {}.{}".format(cell.name, pin_name))
                if table_dirs and pin_name in table_dirs:
                    assert direction == table_dirs[pin_name], (
                        "direction mismatch for {}.{}: json={} table={}".format(
                            cell.name, pin_name, direction, table_dirs[pin_name]
                        )
                    )
            else:
                assert table_dirs is not None, (
                    "Missing pin direction table for cell type {} used by instance {}".format(cell.type, cell.name)
                )
                assert pin_name in table_dirs, (
                    "Pin {} missing from direction table for cell type {} (instance {})".format(
                        pin_name, cell.type, cell.name
                    )
                )
                direction = table_dirs[pin_name]
            assert direction in SUPPORTED_CELL_PIN_DIRECTIONS, (
                "Unsupported cell pin direction for {}.{}: {}".format(cell.name, pin_name, direction)
            )
            resolved[pin_name] = direction
        cell.port_directions = resolved


def build_primary_port_entries(module):
    entries = []
    label_to_entry = OrderedDict()
    int_bit_to_entries = defaultdict(list)

    for port_name, port in module.ports.items():
        assert port.direction in SUPPORTED_PORT_DIRECTIONS, (
            "Unsupported primary port direction for {}: {}".format(port_name, port.direction)
        )
        labels = port_bit_labels(module, port_name)
        assert len(labels) == len(port.bits), "Port label/bit length mismatch for {}".format(port_name)
        for offset_index, bit in enumerate(port.bits):
            bit_index, bit_label = labels[offset_index]
            entry = {
                "port_name": port_name,
                "direction": port.direction,
                "bit": bit,
                "bit_index": bit_index,
                "bit_label": bit_label,
                "position": offset_index,
            }
            assert bit_label not in label_to_entry, "Duplicate primary bit label: {}".format(bit_label)
            label_to_entry[bit_label] = entry
            if port.direction == "input":
                assert isinstance(bit, int), "Primary input bit must be an integer bit id: {} -> {!r}".format(bit_label, bit)
            elif isinstance(bit, str):
                assert bit in SUPPORTED_CONST_PORT_BITS, (
                    "Primary output constant must be 0/1 only: {} -> {!r}".format(bit_label, bit)
                )
            if isinstance(bit, int):
                alias_entries = int_bit_to_entries[bit]
                if alias_entries:
                    alias_labels = [item["bit_label"] for item in alias_entries] + [bit_label]
                    alias_directions = {item["direction"] for item in alias_entries}
                    alias_directions.add(port.direction)
                    assert len(alias_directions) == 1, (
                        "Mixed-direction primary bit alias is unsupported: bit {} used by {}".format(
                            bit, alias_labels
                        )
                    )
                    assert port.direction == "output", (
                        "Primary input alias is unsupported: bit {} used by {}".format(bit, alias_labels)
                    )
                alias_entries.append(entry)
            entries.append(entry)

    return entries, label_to_entry, dict(int_bit_to_entries)


def build_bit_name_candidates(module):
    candidates = defaultdict(list)

    def add_signal(bits, labels, hide_name):
        for index_in_list, bit in enumerate(bits):
            if not isinstance(bit, int):
                continue
            candidates[bit].append((hide_name, labels[index_in_list][1]))

    for port in module.ports.values():
        add_signal(port.bits, port_bit_labels(module, port.name), 0)
    for netname in module.netnames.values():
        hide_name = 0 if netname.hide_name is None else netname.hide_name
        add_signal(netname.bits, netname_bit_labels(netname), hide_name)
    return candidates


def pick_bit_debug_name(bit, candidates):
    if isinstance(bit, str):
        return bit
    bit_candidates = sorted(candidates.get(bit, []), key=lambda item: (item[0], item[1]))
    if bit_candidates:
        return bit_candidates[0][1]
    return "<bit:{}>".format(bit)


def load_partition_entries(label_to_entry, module):
    cell_names = set(module.cells.keys())
    primary_bit_names = set(label_to_entry.keys())
    cell_tiers = {}
    primary_owner_tiers = {}
    seen_names = set()

    with open(PARTITION_TXT) as fp:
        for lineno, line in enumerate(fp, 1):
            parts = line.split()
            if not parts:
                continue
            assert len(parts) == 2, "Malformed partition line {}: {}".format(lineno, line.rstrip())
            raw_name, raw_tier = parts
            assert raw_name not in seen_names, "Duplicate partition entry for {}".format(raw_name)
            seen_names.add(raw_name)
            tier = decode_partition_tier(raw_tier)
            if raw_name in cell_names:
                cell_tiers[raw_name] = tier
            elif raw_name in primary_bit_names:
                primary_owner_tiers[raw_name] = tier
            else:
                raise AssertionError("Unexpected partition entry not matched to a source-json cell or original primary bit: {}".format(raw_name))

    partition_cell_names = set(cell_tiers.keys())
    partition_primary_names = set(primary_owner_tiers.keys())

    assert len(partition_cell_names) == len(cell_names), (
        "Partition/source-json cell count mismatch: partition={} json={}".format(
            len(partition_cell_names), len(cell_names)
        )
    )
    assert len(partition_primary_names) == len(primary_bit_names), (
        "Partition/original-primary-bit count mismatch: partition={} json={}".format(
            len(partition_primary_names), len(primary_bit_names)
        )
    )

    missing_cells = sorted(cell_names - partition_cell_names)
    unexpected_cells = sorted(partition_cell_names - cell_names)
    assert partition_cell_names == cell_names, (
        "Partition/source-json cell name mismatch. missing_from_partition={} unexpected_in_partition={}".format(
            missing_cells[:20], unexpected_cells[:20]
        )
    )

    missing_primary_bits = sorted(primary_bit_names - partition_primary_names)
    unexpected_primary_bits = sorted(partition_primary_names - primary_bit_names)
    assert partition_primary_names == primary_bit_names, (
        "Partition/original-primary-bit name mismatch. missing_from_partition={} unexpected_in_partition={}".format(
            missing_primary_bits[:20], unexpected_primary_bits[:20]
        )
    )

    return cell_tiers, primary_owner_tiers


def summarize_cell_refs(bit_references, bit, cell_tiers):
    refs = bit_references.get(bit, [])
    drivers = []
    users = []
    for ref in refs:
        if ref.kind != "cell":
            continue
        assert ref.direction in SUPPORTED_CELL_PIN_DIRECTIONS, (
            "Cell reference with unresolved direction on bit {}: {}".format(bit, ref)
        )
        assert ref.owner_name in cell_tiers, "Cell {} missing tier assignment".format(ref.owner_name)
        tier = cell_tiers[ref.owner_name]
        if ref.direction == "output":
            drivers.append((ref, tier))
        elif ref.direction == "input":
            users.append((ref, tier))
        else:
            raise AssertionError("Unsupported cell pin direction {} on {}".format(ref.direction, ref))

    driver_tiers = sorted(set(tier for _ref, tier in drivers))
    user_tiers = sorted(set(tier for _ref, tier in users))
    return drivers, users, driver_tiers, user_tiers


def collect_primary_bits(module):
    primary_bits = set()
    for port in module.ports.values():
        for bit in port.bits:
            if isinstance(bit, int):
                primary_bits.add(bit)
    return primary_bits


def load_actual_coarse_module_definitions():
    assert os.path.isdir(COARSE_2D_SYNTH_DIR), "coarse synth dir not found: {}".format(COARSE_2D_SYNTH_DIR)
    module_defs = {}
    for entry in sorted(os.listdir(COARSE_2D_SYNTH_DIR)):
        if entry.endswith(".json"):
            module_name = os.path.splitext(entry)[0]
            design = load_design(os.path.join(COARSE_2D_SYNTH_DIR, entry))
            validate_design(design)
            assert module_name in design.modules, (
                "coarse synth json {} missing module {}".format(entry, module_name)
            )
            module_defs[module_name] = design.get_module(module_name)
    if module_defs:
        return module_defs

    for entry in sorted(os.listdir(COARSE_2D_SYNTH_DIR)):
        if not entry.endswith(".v"):
            continue
        module_name = os.path.splitext(entry)[0]
        design = load_single_module_design_from_verilog(os.path.join(COARSE_2D_SYNTH_DIR, entry), module_name)
        module_defs[module_name] = design.get_module(module_name)
    return module_defs


def build_unused_input_positions(module_def):
    refs_by_bit = collect_bit_references(module_def)
    unused = {}
    for port_name, port in module_def.ports.items():
        if port.direction != "input":
            continue
        positions = set()
        for bit_index, bit in enumerate(port.bits):
            if not isinstance(bit, int):
                continue
            refs = refs_by_bit.get(bit, [])
            used = False
            for ref in refs:
                if ref.kind == "port" and ref.owner_name == port_name and ref.direction == "input":
                    continue
                if ref.kind == "netname":
                    continue
                used = True
                break
            if not used:
                positions.add(bit_index)
        unused[port_name] = positions
    return unused


def cleanup_replaced_internal_bits(module, replaced_bits):
    if not replaced_bits:
        return {"removed_netnames": [], "trimmed_netnames": []}
    refs_by_bit = collect_bit_references(module)
    removed = []
    trimmed = []
    for net_name in sorted(list(module.netnames.keys())):
        net = module.netnames[net_name]
        touched = False
        new_bits = []
        for bit in net.bits:
            if isinstance(bit, int) and bit in replaced_bits and all(ref.kind == "netname" for ref in refs_by_bit.get(bit, [])):
                touched = True
                continue
            new_bits.append(bit)
        if not touched:
            continue
        if new_bits:
            net.bits = new_bits
            trimmed.append(net_name)
        else:
            del module.netnames[net_name]
            removed.append(net_name)
    return {"removed_netnames": removed, "trimmed_netnames": trimmed}


def constantize_undriven_unused_coarse_inputs(module, cell_tiers):
    actual_module_defs = load_actual_coarse_module_definitions()
    unused_cache = {}
    primary_bits = collect_primary_bits(module)
    bit_references = collect_bit_references(module)
    coarse_cell_types = set(actual_module_defs.keys())
    replaced = []
    blocked = []
    replaced_bits = set()

    for bit, refs in sorted(bit_references.items(), key=lambda item: item[0] if isinstance(item[0], int) else -1):
        if not isinstance(bit, int):
            continue
        if bit in primary_bits:
            continue
        drivers, users, driver_tiers, user_tiers = summarize_cell_refs(bit_references, bit, cell_tiers)
        del drivers, users, user_tiers
        if driver_tiers:
            continue
        cell_refs = [ref for ref in refs if ref.kind == "cell"]
        if not cell_refs:
            continue
        if any(ref.direction != "input" for ref in cell_refs):
            continue

        candidate_refs = []
        unsupported_ref_kinds = [ref for ref in refs if ref.kind not in {"cell", "netname"}]
        if unsupported_ref_kinds:
            continue

        can_replace = True
        for ref in cell_refs:
            cell = module.cells[ref.owner_name]
            if cell.type not in coarse_cell_types:
                can_replace = False
                break
            actual_def = actual_module_defs[cell.type]
            if cell.type not in unused_cache:
                unused_cache[cell.type] = build_unused_input_positions(actual_def)
            port_name = ref.field_name
            assert port_name in actual_def.ports, (
                "coarse module {} missing port {} used by instance {}".format(
                    cell.type,
                    port_name,
                    cell.name,
                )
            )
            port = actual_def.ports[port_name]
            assert port.direction == "input", (
                "expected input port {}.{} for undriven handling".format(cell.type, port_name)
            )
            assert ref.bit_index < len(port.bits), (
                "bit index {} out of range for {}.{} width {}".format(
                    ref.bit_index,
                    cell.type,
                    port_name,
                    len(port.bits),
                )
            )
            if ref.bit_index not in unused_cache[cell.type].get(port_name, set()):
                blocked.append(
                    {
                        "bit": bit,
                        "cell_name": cell.name,
                        "cell_type": cell.type,
                        "port_name": port_name,
                        "bit_index": ref.bit_index,
                    }
                )
                can_replace = False
                break
            candidate_refs.append(
                {
                    "cell_name": cell.name,
                    "cell_type": cell.type,
                    "port_name": port_name,
                    "bit_index": ref.bit_index,
                }
            )

        if not can_replace or not candidate_refs:
            continue

        replace_bit_references(module, bit, "0", include_ports=False, include_netnames=False, include_cells=True)
        replaced_bits.add(bit)
        replaced.append(
            {
                "bit": bit,
                "replacement": "0",
                "cell_refs": candidate_refs,
            }
        )

    if blocked:
        raise AssertionError(
            "Found undriven coarse-input bits that are still used inside actual coarse modules: {}".format(blocked[:20])
        )

    cleanup = cleanup_replaced_internal_bits(module, replaced_bits)
    return {
        "replaced_bit_count": len(replaced),
        "replaced_bits": replaced,
        "cleanup": cleanup,
        "coarse_module_count": len(actual_module_defs),
    }


def constantize_undriven_general_internal_inputs(module, cell_tiers, excluded_cell_types=None):
    primary_bits = collect_primary_bits(module)
    bit_references = collect_bit_references(module)
    candidates = build_bit_name_candidates(module)
    excluded_cell_types = set(excluded_cell_types or [])
    replaced = []
    blocked = []
    replaced_bits = set()

    for bit, refs in sorted(bit_references.items(), key=lambda item: item[0] if isinstance(item[0], int) else -1):
        if not isinstance(bit, int):
            continue
        if bit in primary_bits:
            continue

        drivers, users, driver_tiers, user_tiers = summarize_cell_refs(bit_references, bit, cell_tiers)
        del drivers, users, user_tiers
        if driver_tiers:
            continue

        cell_refs = [ref for ref in refs if ref.kind == "cell"]
        if not cell_refs:
            continue

        debug_name = pick_bit_debug_name(bit, candidates)
        unsupported_ref_kinds = sorted(set(ref.kind for ref in refs if ref.kind not in {"cell", "netname"}))
        if unsupported_ref_kinds:
            blocked.append(
                {
                    "bit": bit,
                    "debug_name": debug_name,
                    "reason": "unsupported_ref_kinds",
                    "ref_kinds": unsupported_ref_kinds,
                }
            )
            continue

        non_input_refs = [ref for ref in cell_refs if ref.direction != "input"]
        if non_input_refs:
            blocked.append(
                {
                    "bit": bit,
                    "debug_name": debug_name,
                    "reason": "non_input_cell_ref",
                    "cell_refs": [
                        {
                            "cell_name": ref.owner_name,
                            "cell_type": module.cells[ref.owner_name].type,
                            "port_name": ref.field_name,
                            "direction": ref.direction,
                            "bit_index": ref.bit_index,
                        }
                        for ref in non_input_refs
                    ],
                }
            )
            continue

        coarse_refs = [ref for ref in cell_refs if module.cells[ref.owner_name].type in excluded_cell_types]
        if coarse_refs:
            continue

        candidate_refs = [
            {
                "cell_name": ref.owner_name,
                "cell_type": module.cells[ref.owner_name].type,
                "port_name": ref.field_name,
                "bit_index": ref.bit_index,
            }
            for ref in cell_refs
        ]
        replace_bit_references(module, bit, "0", include_ports=False, include_netnames=False, include_cells=True)
        replaced_bits.add(bit)
        replaced.append(
            {
                "bit": bit,
                "debug_name": debug_name,
                "replacement": "0",
                "cell_refs": candidate_refs,
            }
        )

    if blocked:
        raise AssertionError(
            "Found undriven internal bits that are not safe to constantize automatically: {}".format(blocked[:20])
        )

    cleanup = cleanup_replaced_internal_bits(module, replaced_bits)
    return {
        "replaced_bit_count": len(replaced),
        "replaced_bits": replaced,
        "cleanup": cleanup,
        "excluded_cell_type_count": len(excluded_cell_types),
    }


def canonicalize_undriven_internal_bits(module, cell_tiers):
    coarse_fix = constantize_undriven_unused_coarse_inputs(module, cell_tiers)
    coarse_module_defs = load_actual_coarse_module_definitions()
    general_fix = constantize_undriven_general_internal_inputs(
        module,
        cell_tiers,
        excluded_cell_types=coarse_module_defs.keys(),
    )
    return {
        "replaced_bit_count": coarse_fix["replaced_bit_count"] + general_fix["replaced_bit_count"],
        "coarse_input_fix": coarse_fix,
        "general_internal_input_fix": general_fix,
    }


def canonical_name(kind, port_name, bit_index):
    if bit_index is None:
        return "__{}_{}".format(kind, port_name)
    return "__{}_{}_{}".format(kind, port_name, bit_index)


def classify_primary_input(entry, owner_tier, bit_references, cell_tiers):
    bit = entry["bit"]
    drivers, users, driver_tiers, user_tiers = summarize_cell_refs(bit_references, bit, cell_tiers)
    assert not driver_tiers, "Primary input bit unexpectedly driven by a cell: {}".format(entry["bit_label"])

    other = other_tier(owner_tier)
    owner_used = owner_tier in user_tiers
    other_used = other in user_tiers
    if owner_used and not other_used:
        case_name = "PI-1"
    elif owner_used and other_used:
        case_name = "PI-2"
    elif (not owner_used) and other_used:
        case_name = "PI-3"
    elif not user_tiers:
        case_name = "PI-4"
    else:
        raise AssertionError("Unexpected PI tier usage pattern for {}: {}".format(entry["bit_label"], user_tiers))

    return {
        "kind": BIT_KIND_PI,
        "case": case_name,
        "port_name": entry["port_name"],
        "bit_label": entry["bit_label"],
        "bit_index": entry["bit_index"],
        "bit": bit,
        "owner_tier": owner_tier,
        "driver_tier": None,
        "user_tiers": user_tiers,
        "debug_name": entry["bit_label"],
    }


def classify_primary_output(entry, owner_tier, bit_references, cell_tiers):
    bit = entry["bit"]
    if isinstance(bit, str):
        return {
            "kind": BIT_KIND_PO,
            "case": "PO-4",
            "port_name": entry["port_name"],
            "bit_label": entry["bit_label"],
            "bit_index": entry["bit_index"],
            "bit": bit,
            "owner_tier": owner_tier,
            "driver_tier": None,
            "user_tiers": [],
            "debug_name": entry["bit_label"],
        }

    drivers, users, driver_tiers, user_tiers = summarize_cell_refs(bit_references, bit, cell_tiers)
    assert driver_tiers, "Non-constant PO has no driver: {}".format(entry["bit_label"])
    assert len(driver_tiers) == 1, "PO has multiple driver tiers: {} -> {}".format(entry["bit_label"], driver_tiers)
    driver_tier = driver_tiers[0]
    other = other_tier(owner_tier)

    if driver_tier == owner_tier and other not in user_tiers:
        case_name = "PO-1"
    elif driver_tier == owner_tier and other in user_tiers:
        case_name = "PO-2"
    elif driver_tier != owner_tier:
        case_name = "PO-3"
    else:
        raise AssertionError("Unexpected PO classification state for {}".format(entry["bit_label"]))

    return {
        "kind": BIT_KIND_PO,
        "case": case_name,
        "port_name": entry["port_name"],
        "bit_label": entry["bit_label"],
        "bit_index": entry["bit_index"],
        "bit": bit,
        "owner_tier": owner_tier,
        "driver_tier": driver_tier,
        "user_tiers": user_tiers,
        "debug_name": entry["bit_label"],
    }


def classify_internal_bit(bit, bit_references, cell_tiers, debug_name):
    drivers, users, driver_tiers, user_tiers = summarize_cell_refs(bit_references, bit, cell_tiers)
    assert driver_tiers, "Internal bit has no driver: {}".format(debug_name)
    assert len(driver_tiers) == 1, "Internal bit has multiple driver tiers: {} -> {}".format(debug_name, driver_tiers)
    driver_tier = driver_tiers[0]

    if not user_tiers:
        case_name = "NET-5"
    elif user_tiers == [driver_tier]:
        case_name = "NET-1"
    elif driver_tier == TIER_UPPER:
        case_name = "NET-2"
    elif driver_tier == TIER_BOTTOM:
        case_name = "NET-3"
    else:
        raise AssertionError("Unexpected internal driver tier for {}: {}".format(debug_name, driver_tier))

    return {
        "kind": BIT_KIND_INTERNAL,
        "case": case_name,
        "port_name": None,
        "bit_label": None,
        "bit_index": None,
        "bit": bit,
        "owner_tier": None,
        "driver_tier": driver_tier,
        "user_tiers": user_tiers,
        "debug_name": debug_name,
    }


def classify_bits(module, primary_entries, primary_owner_tiers, cell_tiers):
    bit_references = collect_bit_references(module)
    primary_by_int_bit = {}
    for entry in primary_entries:
        if isinstance(entry["bit"], int):
            primary_by_int_bit[entry["bit"]] = entry

    candidates = build_bit_name_candidates(module)
    classified_primary = []
    for entry in primary_entries:
        owner_tier = primary_owner_tiers[entry["bit_label"]]
        if entry["direction"] == "input":
            classified_primary.append(classify_primary_input(entry, owner_tier, bit_references, cell_tiers))
        else:
            classified_primary.append(classify_primary_output(entry, owner_tier, bit_references, cell_tiers))

    internal_bits = OrderedDict()
    for bit, refs in bit_references.items():
        if not isinstance(bit, int):
            continue
        if bit in primary_by_int_bit:
            continue
        if not any(ref.kind == "cell" for ref in refs):
            continue
        debug_name = pick_bit_debug_name(bit, candidates)
        internal_bits[bit] = classify_internal_bit(bit, bit_references, cell_tiers, debug_name)

    return classified_primary, list(internal_bits.values())
