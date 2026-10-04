"""Generate and validate coarse-unit metrics, LEF, Liberty, and netlist views."""

import json
import math
import os
import re
from collections import OrderedDict
from pathlib import Path

FLOW_DIR = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(FLOW_DIR))

from yosys_json_toolkit import Design, dump_design, load_design, validate_design

from hierarchy_variants import (
    BOTTOM,
    FLOW_HOME,
    HETERO_PLATFORM,
    TRUE_ATTR,
    TWO_DIMENSIONAL,
    UPPER,
    build_map_tables,
    load_family_metrics,
    load_platform_lib_cell_areas,
    prepare_output_locations,
    remap_module_for_target,
    run_yosys_script,
    write_single_module_verilog,
)


def round_to_milli(value):
    return round(float(value) + 1e-9, 3)


def build_actual_metrics(actual_name, family_name, platform, bottom_area, upper_area, family_metrics):
    bottom_family = family_metrics[BOTTOM]
    upper_family = family_metrics[UPPER]

    predicted_util = bottom_family.get("predicted_utilization")
    if predicted_util is None or not (float(predicted_util) > 0.0):
        raise RuntimeError("family {} bottom metrics missing predicted_utilization".format(family_name))
    predicted_util = float(predicted_util)
    bottom_footprint = round_to_milli(bottom_area / predicted_util)

    scale_factor = upper_family.get("scale_factor")
    if scale_factor is None:
        upper_family_area = float(upper_family["footprint_area"])
        bottom_family_area = float(bottom_family["footprint_area"])
        if not (bottom_family_area > 0.0):
            raise RuntimeError("invalid family bottom footprint area for {}".format(family_name))
        scale_factor = upper_family_area / bottom_family_area
    scale_factor = float(scale_factor)
    if not (scale_factor > 0.0):
        raise RuntimeError("invalid scale_factor {} for family {}".format(scale_factor, family_name))
    upper_footprint = round_to_milli(bottom_footprint * scale_factor)

    metrics = {
        BOTTOM: {
            "schema_version": 1,
            "macro_name": actual_name,
            "rtlil_name": "\\{}".format(actual_name),
            "library": platform,
            "tier": BOTTOM,
            "synthesis_source": "genus_actual_variant_preserved",
            "util_source": bottom_family.get("util_source", "family_reuse"),
            "synthesis_lib_role": bottom_family.get("synthesis_lib_role"),
            "gnn_tech": bottom_family.get("gnn_tech"),
            "cell_area": round_to_milli(bottom_area),
            "raw_predicted_utilization": bottom_family.get("raw_predicted_utilization"),
            "predicted_utilization": round_to_milli(predicted_util),
            "footprint_area": bottom_footprint,
            "footprint_source": "actual_cell_area / family_predicted_utilization",
            "family_name": family_name,
        },
        UPPER: {
            "schema_version": 1,
            "macro_name": actual_name,
            "rtlil_name": "\\{}".format(actual_name),
            "library": platform,
            "tier": UPPER,
            "synthesis_source": "genus_actual_variant_preserved",
            "util_source": upper_family.get("util_source", "family_reuse"),
            "synthesis_lib_role": upper_family.get("synthesis_lib_role"),
            "gnn_tech": upper_family.get("gnn_tech"),
            "cell_area": round_to_milli(upper_area),
            "raw_predicted_utilization": upper_family.get("raw_predicted_utilization"),
            "predicted_utilization": upper_family.get("predicted_utilization"),
            "footprint_area": upper_footprint,
            "footprint_source": "scaled_from_bottom",
            "scale_from_tier": BOTTOM,
            "scale_factor": round_to_milli(scale_factor),
            "family_name": family_name,
        },
    }
    return metrics


def write_metrics(handoff_dir, actual_name, metrics_by_tier):
    for tier, metrics in metrics_by_tier.items():
        metrics_dir = Path(handoff_dir) / "metrics" / tier / actual_name
        metrics_dir.mkdir(parents=True, exist_ok=True)
        with open(metrics_dir / "metrics.json", "w", encoding="utf-8") as fp:
            json.dump(metrics, fp, indent=2, sort_keys=False)
            fp.write("\n")


def coarse_unit_lef_style(platform, view):
    if view not in {TWO_DIMENSIONAL, BOTTOM, UPPER}:
        raise RuntimeError("unsupported coarse-unit LEF view: {}".format(view))

    if platform == "asap7_3D":
        layer = "M4_m" if view == UPPER else "M4"
        return {"layer": layer, "pin_rect_w": 0.024, "pin_rect_h": 0.024, "grid": 0.001}

    if platform == "nangate45_3D":
        layer = "M3_m" if view == UPPER else "M3"
        return {"layer": layer, "pin_rect_w": 0.070, "pin_rect_h": 0.070, "grid": 0.005}

    if platform == HETERO_PLATFORM:
        if view == UPPER:
            return {"layer": "M4_m", "pin_rect_w": 0.024, "pin_rect_h": 0.024, "grid": 0.001}
        return {"layer": "M3", "pin_rect_w": 0.070, "pin_rect_h": 0.070, "grid": 0.001}

    raise RuntimeError("unsupported platform: {}".format(platform))


def snap_up_to_grid(value, grid):
    if grid <= 0.0:
        raise RuntimeError("invalid grid {}".format(grid))
    return math.ceil(float(value) / float(grid) - 1e-12) * float(grid)


def snap_to_grid(value, grid):
    if grid <= 0.0:
        raise RuntimeError("invalid grid {}".format(grid))
    return round(float(value) / float(grid)) * float(grid)


def clampf(value, lo, hi):
    return max(lo, min(value, hi))


def format_coord(value):
    return "{:.3f}".format(float(value))


def module_pin_entries(module_obj):
    pins = []
    for port_name, port in module_obj.ports.items():
        width = len(port.bits)
        if width == 1:
            pins.append({"name": port_name, "is_output": port.direction == "output"})
        else:
            for index in range(width):
                pins.append({"name": "{}[{}]".format(port_name, index), "is_output": port.direction == "output"})
    return pins


def write_macro(fh, macro_name, module_obj, area, style):
    side = snap_up_to_grid(math.sqrt(float(area)), style["grid"])
    pin_w = max(style["grid"], snap_up_to_grid(style["pin_rect_w"], style["grid"]))
    pin_h = max(style["grid"], snap_up_to_grid(style["pin_rect_h"], style["grid"]))
    side = max(side, pin_w, pin_h)

    fh.write("MACRO {}\n".format(macro_name))
    fh.write("  CLASS BLOCK ;\n")
    fh.write("  FOREIGN {} 0 0 ;\n".format(macro_name))
    fh.write("  ORIGIN 0 0 ;\n")
    fh.write("  SYMMETRY X Y R90 ;\n")
    fh.write("  SIZE {} BY {} ;\n\n".format(format_coord(side), format_coord(side)))

    pins = module_pin_entries(module_obj)
    count = len(pins)
    y_limit = max(0.0, side - pin_h)
    y_step = (y_limit / float(count + 1)) if count > 0 else y_limit
    for index, pin in enumerate(pins):
        y_lo = snap_to_grid(y_step * float(index + 1), style["grid"])
        y_lo = clampf(y_lo, 0.0, y_limit)
        y_hi = snap_to_grid(y_lo + pin_h, style["grid"])
        if y_hi > side:
            y_hi = side
            y_lo = clampf(snap_to_grid(y_hi - pin_h, style["grid"]), 0.0, y_limit)
        direction = "OUTPUT" if pin["is_output"] else "INPUT"
        fh.write("  PIN {}\n".format(pin["name"]))
        fh.write("    DIRECTION {} ;\n".format(direction))
        fh.write("    USE SIGNAL ;\n")
        fh.write("    SHAPE ABUTMENT ;\n")
        fh.write("    PORT\n")
        fh.write("      LAYER {} ;\n".format(style["layer"]))
        fh.write(
            "        RECT 0.000 {} {} {} ;\n".format(
                format_coord(y_lo),
                format_coord(pin_w),
                format_coord(y_hi),
            )
        )
        fh.write("    END\n")
        fh.write("  END {}\n\n".format(pin["name"]))
    fh.write("END {}\n\n".format(macro_name))


def write_macro_lef(path, modules_by_name, metrics_by_name, platform, view):
    style = coarse_unit_lef_style(platform, view)
    metrics_tier = BOTTOM if view == TWO_DIMENSIONAL else view
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("VERSION 5.7 ;\n")
        fh.write("BUSBITCHARS \"[]\" ;\n")
        fh.write("DIVIDERCHAR \"/\" ;\n\n")
        for module_name in sorted(modules_by_name.keys()):
            macro_name = module_name if view == TWO_DIMENSIONAL else "{}_{}".format(module_name, view)
            write_macro(
                fh,
                macro_name,
                modules_by_name[module_name],
                metrics_by_name[module_name][metrics_tier]["footprint_area"],
                style,
            )
        fh.write("END LIBRARY\n")


def build_pass_input_json(work_dir, modules_by_name):
    pass_design = Design(creator="finalize_hierarchy_output.py", modules=OrderedDict())
    for module_name in sorted(modules_by_name.keys()):
        module_obj = modules_by_name[module_name].copy()
        module_obj.attributes["syn3d_coarse_unit"] = TRUE_ATTR
        pass_design.modules[module_name] = module_obj
    json_path = Path(work_dir) / "actual_coarse_units_for_lib_writer.json"
    dump_design(pass_design, json_path)
    validate_design(load_design(json_path))
    return json_path


def write_coarse_unit_libs(yosys_exe, work_dir, platform, handoff_dir, results_dir,
                           modules_by_name):
    if not modules_by_name:
        return
    input_json = build_pass_input_json(work_dir, modules_by_name)
    env = os.environ.copy()
    env["FLOW_HOME"] = FLOW_HOME
    env["LIBRARY"] = platform
    env["SYN3D_HANDOFF_DIR"] = str(handoff_dir)
    env["RESULTS_DIR"] = str(results_dir)
    env["YOSYS_EXE"] = yosys_exe
    lib_tcl = """
read_json {input_json}
syn3d_write_coarse_unit_libs
""".format(input_json=input_json)
    run_yosys_script(yosys_exe, lib_tcl, "final coarse-unit LIB generation", env=env)


def collect_macro_names(lef_path):
    macro_names = set()
    with open(lef_path, encoding="utf-8") as fp:
        for line in fp:
            match = re.match(r"\s*MACRO\s+(\S+)\s*$", line)
            if match:
                macro_names.add(match.group(1))
    return macro_names


def validate_published_variant_sets(handoff_dir, retained_actual_names):
    handoff_dir = Path(handoff_dir)
    expected = set(retained_actual_names)
    observed = OrderedDict()
    observed["coarse_units/bottom"] = {
        path.stem for path in (handoff_dir / "coarse_units" / BOTTOM).glob("*.v")
    }
    observed["coarse_units/upper"] = {
        path.stem for path in (handoff_dir / "coarse_units" / UPPER).glob("*.v")
    }
    observed["metrics/bottom"] = {
        path.name for path in (handoff_dir / "metrics" / BOTTOM).iterdir() if path.is_dir()
    }
    observed["metrics/upper"] = {
        path.name for path in (handoff_dir / "metrics" / UPPER).iterdir() if path.is_dir()
    }
    observed["lib/2d"] = {
        path.stem
        for path in (handoff_dir / "lib" / TWO_DIMENSIONAL).glob("*.lib")
        if path.name != "coarse_units.lib"
    }
    observed["lib/bottom"] = {
        path.name[: -len(".bottom.lib")]
        for path in (handoff_dir / "lib" / BOTTOM).glob("*.bottom.lib")
    }
    observed["lib/upper"] = {
        path.name[: -len(".upper.lib")]
        for path in (handoff_dir / "lib" / UPPER).glob("*.upper.lib")
    }

    lef_expectations = {
        "lef/2d": expected,
        "lef/bottom": {"{}_bottom".format(name) for name in expected},
        "lef/upper": {"{}_upper".format(name) for name in expected},
    }
    for view, view_expected in lef_expectations.items():
        tier = view.split("/", 1)[1]
        lef_path = handoff_dir / "lef" / tier / "coarse_units.lef"
        observed[view] = collect_macro_names(lef_path)
        if observed[view] != view_expected:
            raise RuntimeError(
                "published variant set mismatch for {}: expected={} observed={}".format(
                    view, sorted(view_expected), sorted(observed[view])
                )
            )

    for output_name, output_names in observed.items():
        if output_name.startswith("lef/"):
            continue
        if output_names != expected:
            raise RuntimeError(
                "published variant set mismatch for {}: expected={} observed={}".format(
                    output_name, sorted(expected), sorted(output_names)
                )
            )


def publish_retained_actual_outputs(args, actual_modules, variant_data):
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    results_dir = Path(args.output_verilog).resolve().parent
    family_metrics_cache = {}
    for info in actual_modules.values():
        family_name = info["family_name"]
        if family_name not in family_metrics_cache:
            family_metrics_cache[family_name] = load_family_metrics(
                args.family_metrics_dir, family_name)

    prepare_output_locations(args.handoff_dir)

    bottom_modules = OrderedDict()
    upper_modules = OrderedDict()
    metrics_by_name = OrderedDict()
    actual_report = OrderedDict()

    for actual_name, info in actual_modules.items():
        if actual_name not in variant_data:
            raise RuntimeError("missing prepared data for retained actual unit {}".format(actual_name))
        prepared = variant_data[actual_name]
        if prepared["eliminated"]:
            raise RuntimeError("eliminated actual unit reached publish stage: {}".format(actual_name))
        family_name = info["family_name"]
        family_metrics = family_metrics_cache[family_name]

        bottom_module = prepared["bottom_module"].copy()
        bottom_area = prepared["bottom_area"]
        write_single_module_verilog(
            args.yosys,
            actual_name,
            bottom_module,
            Path(args.handoff_dir) / "coarse_units" / BOTTOM / "{}.v".format(actual_name),
            work_dir,
            "bottom",
        )

        upper_module = prepared["upper_module"].copy()
        upper_area = prepared["upper_area"]
        write_single_module_verilog(
            args.yosys,
            actual_name,
            upper_module,
            Path(args.handoff_dir) / "coarse_units" / UPPER / "{}.v".format(actual_name),
            work_dir,
            "upper",
        )

        metrics = build_actual_metrics(
            actual_name,
            family_name,
            args.platform,
            bottom_area,
            upper_area,
            family_metrics,
        )
        write_metrics(args.handoff_dir, actual_name, metrics)

        bottom_modules[actual_name] = bottom_module
        upper_modules[actual_name] = upper_module
        metrics_by_name[actual_name] = metrics
        actual_report[actual_name] = {
            "family_name": family_name,
            "top_instance_count": info["top_instance_count"],
            "bottom_cell_area": metrics[BOTTOM]["cell_area"],
            "upper_cell_area": metrics[UPPER]["cell_area"],
            "bottom_cell_count": prepared["bottom_cell_count"],
            "upper_cell_count": prepared["upper_cell_count"],
            "bottom_footprint_area": metrics[BOTTOM]["footprint_area"],
            "upper_footprint_area": metrics[UPPER]["footprint_area"],
        }

    write_macro_lef(Path(args.handoff_dir) / "lef" / BOTTOM / "coarse_units.lef",
                    bottom_modules, metrics_by_name, args.platform, BOTTOM)
    write_macro_lef(Path(args.handoff_dir) / "lef" / UPPER / "coarse_units.lef",
                    upper_modules, metrics_by_name, args.platform, UPPER)
    write_macro_lef(Path(args.handoff_dir) / "lef" / TWO_DIMENSIONAL / "coarse_units.lef",
                    bottom_modules, metrics_by_name, args.platform, TWO_DIMENSIONAL)
    write_coarse_unit_libs(
        args.yosys,
        work_dir,
        args.platform,
        args.handoff_dir,
        results_dir,
        bottom_modules,
    )
    validate_published_variant_sets(args.handoff_dir, actual_modules.keys())

    return {
        "actual_variants": actual_report,
    }
