"""Analyze pre-place connectivity and select a tier for each added instance."""

from collections import Counter, defaultdict

from backend_partition_rules import (
    DIE_TO_TIER,
    KNOWN_TIERS,
    fail,
    norm_name,
    resolve_pin_direction,
    tier_from_master,
)


def bit_key(bit):
    return "b{}".format(bit) if isinstance(bit, int) else "c{}".format(bit)


def scalar_bits(bits):
    return [bit for bit in bits if isinstance(bit, int)]

def build_bit_netnames(module):
    bit_to_names = defaultdict(list)
    for net_name, net in module.netnames.items():
        for bit in scalar_bits(net.bits):
            bit_to_names[bit].append(norm_name(net_name))
    for names in bit_to_names.values():
        names.sort(key=lambda n: (n.startswith("$"), len(n), n))
    return bit_to_names


def names_for_bits(bits, bit_to_names):
    labels = []
    for bit in scalar_bits(bits):
        labels.append((bit_to_names.get(bit) or ["<bit:{}>".format(bit)])[0])
    return labels


def build_cell_tiers(pre_module, partition):
    tiers = {}
    for inst_name, cell in pre_module.cells.items():
        if inst_name in partition:
            tiers[inst_name] = DIE_TO_TIER[partition[inst_name]]
            continue
        tier = tier_from_master(cell.type)
        if tier:
            tiers[inst_name] = tier
    return tiers


def build_bit_connections(module, cell_tiers, pin_dirs):
    bit_to_conns = defaultdict(list)
    for inst_name, cell in module.cells.items():
        tier = cell_tiers.get(inst_name, "unknown")
        for pin, bits in cell.connections.items():
            direction = resolve_pin_direction(cell, pin, pin_dirs)
            for bit in scalar_bits(bits):
                bit_to_conns[bit].append(
                    {
                        "kind": "cell",
                        "inst": inst_name,
                        "master": cell.type,
                        "pin": pin,
                        "direction": direction,
                        "tier": tier,
                    }
                )
    return bit_to_conns


def counter_for_conns(conns):
    c = Counter()
    for conn in conns:
        tier = conn.get("tier")
        if tier in KNOWN_TIERS:
            c[tier] += 1
    return c


def format_counter(counter):
    return ";".join("{}:{}".format(tier, counter[tier]) for tier in KNOWN_TIERS if counter[tier])


def format_conns(conns, limit=80):
    vals = []
    for conn in conns:
        vals.append(
            "{}:{}:{}:{}:{}".format(
                conn.get("inst", ""),
                conn.get("master", ""),
                conn.get("pin", ""),
                conn.get("direction", ""),
                conn.get("tier", ""),
            )
        )
    vals.sort()
    if len(vals) > limit:
        vals = vals[:limit] + ["...(+{} more)".format(len(vals) - limit)]
    return ";".join(vals)

def edge_cost(driver_counter, output_load_counter, target_tier):
    cost = 0
    for tier in KNOWN_TIERS:
        if tier != target_tier:
            cost += driver_counter[tier]
            cost += output_load_counter[tier]
    return cost


def net_cost(input_existing_counter, output_load_counter, target_tier):
    before = {tier for tier in KNOWN_TIERS if input_existing_counter[tier]}
    after = set(before)
    after.add(target_tier)
    input_delta = int(len(after) > 1) - int(len(before) > 1)
    output = {tier for tier in KNOWN_TIERS if output_load_counter[tier]}
    output.add(target_tier)
    return input_delta + int(len(output) > 1)


def choose_tier(driver_counter, input_side_counter, output_load_counter, enablement, assigned_new_count):
    upper = edge_cost(driver_counter, output_load_counter, "upper")
    bottom = edge_cost(driver_counter, output_load_counter, "bottom")
    if upper < bottom:
        return "upper", "edge_cost"
    if bottom < upper:
        return "bottom", "edge_cost"
    if enablement == "asap7_nangate45_3D":
        return "upper", "tie_hetero_default_upper"
    if assigned_new_count["upper"] < assigned_new_count["bottom"]:
        return "upper", "tie_homo_balance_new_count"
    if assigned_new_count["bottom"] < assigned_new_count["upper"]:
        return "bottom", "tie_homo_balance_new_count"
    return "upper", "tie_homo_equal_default_upper"


def analyze_added_instance(inst_name, cell, bit_to_conns, bit_to_names, pin_dirs, added_set):
    driver_conns = []
    input_side_load_conns = []
    output_load_conns = []
    output_other_driver_conns = []
    skipped_added = []
    input_net_names = []
    output_net_names = []

    for pin, bits in cell.connections.items():
        direction = resolve_pin_direction(cell, pin, pin_dirs)
        for bit in scalar_bits(bits):
            if direction == "output":
                output_net_names.extend(names_for_bits([bit], bit_to_names))
            else:
                input_net_names.extend(names_for_bits([bit], bit_to_names))

            for conn in bit_to_conns.get(bit, []):
                if conn["inst"] == inst_name:
                    continue
                if conn["inst"] in added_set:
                    skipped_added.append(conn)
                    continue
                if direction == "output":
                    if conn["direction"] == "output":
                        output_other_driver_conns.append(conn)
                    else:
                        output_load_conns.append(conn)
                else:
                    if conn["direction"] == "output":
                        driver_conns.append(conn)
                    else:
                        input_side_load_conns.append(conn)

    if output_other_driver_conns:
        fail("Added instance {} output net has other drivers: {}".format(inst_name, format_conns(output_other_driver_conns, 10)))
    if not driver_conns and not output_load_conns and not skipped_added:
        fail("Added instance {} has no driver/load connectivity".format(inst_name))

    driver_counter = counter_for_conns(driver_conns)
    input_side_counter = counter_for_conns(input_side_load_conns)
    output_load_counter = counter_for_conns(output_load_conns)
    decision_basis = "existing_neighbors"
    if not (sum(driver_counter.values()) or sum(output_load_counter.values())):
        decision_basis = "no_known_existing_neighbor_after_skip"

    return {
        "driver_conns": driver_conns,
        "input_side_load_conns": input_side_load_conns,
        "output_load_conns": output_load_conns,
        "skipped_added": skipped_added,
        "driver_counter": driver_counter,
        "input_side_counter": input_side_counter,
        "output_load_counter": output_load_counter,
        "input_net_names": sorted(set(input_net_names)),
        "output_net_names": sorted(set(output_net_names)),
        "decision_basis": decision_basis,
    }
