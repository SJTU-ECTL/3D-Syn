#!/usr/bin/env python3
"""Generate canonical backend DEF and Verilog views from a 3D-Syn partition."""

import argparse
import os
import sys


def require_file(label, path):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError("{} not found or empty: {}".format(label, path))


def augment_tier_macro_aliases(
    base_to_bottom,
    base_to_upper,
    base_to_bottom_pin_map,
    base_to_upper_pin_map,
    base_to_bottom_const_pins,
    base_to_upper_const_pins,
):
    """Allow heterogeneous tier macros to resolve back to their map base.

    The shared TaiWei rewriter strips ``_upper``/``_bottom`` but does not
    reverse-map a heterogeneous upper macro such as
    ``NOR2xp33_ASAP7_75t_R_upper`` to the map base ``NOR2_X1``.  Add aliases
    only in these in-memory dictionaries so both input and output tier views
    remain compatible without changing the upstream helper or map file.
    """
    for base in list(base_to_bottom):
        bottom_macro = base_to_bottom[base]
        upper_macro = base_to_upper[base]
        bottom_alias = bottom_macro.removesuffix("_bottom")
        upper_alias = upper_macro.removesuffix("_upper")
        aliases = {base, bottom_alias, upper_alias}
        for alias in aliases:
            for table, value in (
                (base_to_bottom, bottom_macro),
                (base_to_upper, upper_macro),
            ):
                previous = table.get(alias)
                if previous is not None and previous != value:
                    raise ValueError(
                        "conflicting tier macro alias {}: {} vs {}".format(
                            alias, previous, value
                        )
                    )
                table[alias] = value

        canonical_bottom = base_to_bottom_pin_map[base]
        canonical_upper = base_to_upper_pin_map[base]
        # Canonical inputs use map keys (e.g. CK); an already-qualified input
        # uses the tier macro's physical names (e.g. CLK).  Keep same-tier
        # pins unchanged and provide the inverse map for a cross-tier move.
        bottom_to_upper = {
            bottom_pin: canonical_upper[canonical_pin]
            for canonical_pin, bottom_pin in canonical_bottom.items()
            if canonical_pin in canonical_upper
        }
        upper_to_bottom = {
            upper_pin: canonical_bottom[canonical_pin]
            for canonical_pin, upper_pin in canonical_upper.items()
            if canonical_pin in canonical_bottom
        }
        base_to_bottom_pin_map[bottom_alias] = {
            **bottom_to_upper,
            **{pin: pin for pin in canonical_bottom.values()},
        }
        base_to_upper_pin_map[bottom_alias] = {
            **{pin: pin for pin in canonical_bottom.values()},
            **bottom_to_upper,
        }
        base_to_bottom_pin_map[upper_alias] = {
            **{pin: pin for pin in canonical_upper.values()},
            **upper_to_bottom,
        }
        base_to_upper_pin_map[upper_alias] = {
            **upper_to_bottom,
            **{pin: pin for pin in canonical_upper.values()},
        }

        for table, source in (
            (base_to_bottom_const_pins, base_to_bottom_const_pins),
            (base_to_upper_const_pins, base_to_upper_const_pins),
        ):
            if base in source:
                table[bottom_alias] = source[base]
                table[upper_alias] = source[base]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-root", required=True)
    parser.add_argument("--def-in", required=True)
    parser.add_argument("--v-in", required=True)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--cell-map", default="")
    parser.add_argument("--def-out", required=True)
    parser.add_argument("--v-out", required=True)
    args = parser.parse_args()

    openroad_scripts_dir = os.path.join(args.flow_root, "scripts_openroad")
    if openroad_scripts_dir not in sys.path:
        sys.path.insert(0, openroad_scripts_dir)
    from generate_3d_views import (  # noqa: E402
        parse_cell_map_json,
        parse_partition_file,
        rewrite_def,
        rewrite_verilog,
        swap_partition_labels,
    )

    require_file("backend preplace DEF", args.def_in)
    require_file("backend preplace netlist", args.v_in)
    require_file("backend partition", args.partition)

    canonical_partition = parse_partition_file(args.partition)
    if not canonical_partition:
        raise RuntimeError("backend partition is empty: {}".format(args.partition))

    # Shared TaiWei rewrite helpers use 0=upper and 1=bottom. 3D-Syn publishes
    # 0=bottom and 1=upper, so convert exactly once and skip orientation inference.
    internal_partition = swap_partition_labels(canonical_partition)
    (
        base_to_bottom,
        base_to_upper,
        base_to_bottom_pin_map,
        base_to_upper_pin_map,
        base_to_bottom_const_pins,
        base_to_upper_const_pins,
        _base_to_tier_areas,
        _has_heterogeneous_area_map,
        has_cell_map,
    ) = parse_cell_map_json(args.cell_map)
    augment_tier_macro_aliases(
        base_to_bottom,
        base_to_upper,
        base_to_bottom_pin_map,
        base_to_upper_pin_map,
        base_to_bottom_const_pins,
        base_to_upper_const_pins,
    )

    rewrite_def(
        args.def_in,
        args.def_out,
        internal_partition,
        base_to_bottom,
        base_to_upper,
        base_to_bottom_pin_map,
        base_to_upper_pin_map,
        has_cell_map,
    )
    rewrite_verilog(
        args.v_in,
        args.v_out,
        internal_partition,
        base_to_bottom,
        base_to_upper,
        base_to_bottom_pin_map,
        base_to_upper_pin_map,
        base_to_bottom_const_pins,
        base_to_upper_const_pins,
        has_cell_map,
    )

    require_file("3D backend DEF", args.def_out)
    require_file("3D backend netlist", args.v_out)
    print("[3D-Syn] Published 3D DEF     : {}".format(args.def_out))
    print("[3D-Syn] Published 3D netlist : {}".format(args.v_out))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("[3D-Syn] {}".format(error), file=sys.stderr)
        raise SystemExit(1)
