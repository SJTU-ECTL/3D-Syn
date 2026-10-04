#!/usr/bin/env python3
"""Prepare the tier-qualified Verilog consumed by 2D backend pre-placement.

The per-tier flow deliberately publishes ``base.v`` with canonical (unsuffixed)
cell masters.  ORD and Innovus load that netlist against the 3D tier libraries,
so they need a private input in which each instance is qualified with its
partition tier before linking.  This driver shares the TaiWei view-rewrite
helpers with the later canonical handoff step and does not modify ``base.v``.
"""

import argparse
import os
import sys


def require_file(label, path):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError("{} not found or empty: {}".format(label, path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-root", required=True)
    parser.add_argument("--v-in", required=True)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--cell-map", default="")
    parser.add_argument("--v-out", required=True)
    args = parser.parse_args()

    openroad_scripts_dir = os.path.join(args.flow_root, "scripts_openroad")
    if openroad_scripts_dir not in sys.path:
        sys.path.insert(0, openroad_scripts_dir)
    from generate_3d_views import (  # noqa: E402
        parse_cell_map_json,
        parse_partition_file,
        rewrite_verilog,
        swap_partition_labels,
    )

    require_file("per-tier base netlist", args.v_in)
    require_file("per-tier partition", args.partition)

    canonical_partition = parse_partition_file(args.partition)
    if not canonical_partition:
        raise RuntimeError("backend partition is empty: {}".format(args.partition))

    # The shared TaiWei helper uses 0=upper and 1=bottom.  3D-Syn publishes
    # 0=bottom and 1=upper, so swap once at this boundary.
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

    parent = os.path.dirname(os.path.abspath(args.v_out))
    if parent:
        os.makedirs(parent, exist_ok=True)
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
    require_file("backend input netlist", args.v_out)
    print("[3D-Syn] Prepared tier-qualified backend netlist: {}".format(args.v_out))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("[3D-Syn] {}".format(error), file=sys.stderr)
        raise SystemExit(1)
