#!/usr/bin/env python3
"""Run OpenROAD placement and routing to produce utilization labels.

The floorplan strategy matches the original data generator:

* core area is ``total_cell_area / target_util``;
* width and height are snapped to the process site grid;
* height grows when necessary to fit all I/O pins on the left edge;
* small designs receive a process-specific routing-stability floor; and
* the label is placed-cell area divided by the routed instance bounding box.

Batch mode processes one netlist group.  ``--target`` selects one existing
sample, using ``MODULE W`` for ``ff_fa`` and ``MODULE A B Y`` for arithmetic
groups.  A successful existing label is skipped unless ``--force`` is passed.
Every attempt writes a status record, and failed samples never enter step 05.

ASAP7 detailed routing requires the run-local LEF produced by
``00_patch_cell_lef.py``; pass it with ``--cell-lef``.  Nangate45 uses the
TaiWei cell LEF by default.

Example:
    python 04_run_pnr.py arith_same --target ADD 2 2 2 \
      --technology asap7 --platform-dir path/to/platform \
      --cell-lef path/to/work/00_resources/asap7_patched.lef \
      --openroad-bin path/to/openroad --data-root path/to/work
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import TRAINING_TECHNOLOGIES, resolve_training_platform


GROUPS = ("ff_fa", "arith_same", "arith_rand")
DEFAULT_THREADS = 4
DEFAULT_WORKERS = 4
SITE_WIDTH = {"nangate45": 0.19, "asap7": 0.054}
SITE_HEIGHT = {"nangate45": 1.4, "asap7": 0.270}
PIN_PITCH = {"nangate45": 0.19, "asap7": 0.108}
MIN_ROWS = {"nangate45": 10, "asap7": 30}
# The widest Nangate45 standard cell is 24 sites (4.56 um).  Keep six
# additional sites so legalization has room beside a wide sequential cell.
MIN_COLS = {"nangate45": 30, "asap7": 30}


def tcl_path(path: Path) -> str:
    """Quote one filesystem path as a Tcl word."""
    return "{" + str(path).replace("}", "\\}") + "}"


def count_io_bits(netlist_path: Path) -> int:
    """Return the total I/O bit width of the filename-matched top module."""
    source = netlist_path.read_text(encoding="utf-8", errors="replace")
    top_module = netlist_path.stem
    match = re.search(
        r"\bmodule\s+" + re.escape(top_module) + r"\b.*?endmodule",
        source,
        re.DOTALL,
    )
    if not match:
        raise ValueError(f"module {top_module} was not found in {netlist_path}")
    total = 0
    for port in re.finditer(
        r"\b(?:input|output|inout)\s+(?:\[(\d+):(\d+)\]\s+)?\w+\s*;",
        match.group(0),
    ):
        total += abs(int(port.group(1)) - int(port.group(2))) + 1 \
            if port.group(1) is not None else 1
    return total


def compute_core_dims(technology: str, total_cell_area: float,
                      num_io_bits: int, target_util: float) -> tuple[float, float]:
    """Calculate a site-aligned core satisfying area, pin and minimum-size rules."""
    if not math.isfinite(total_cell_area) or total_cell_area <= 0:
        raise ValueError(f"total cell area must be finite and positive: {total_cell_area}")
    site_width = SITE_WIDTH[technology]
    site_height = SITE_HEIGHT[technology]
    core_area = total_cell_area / target_util
    raw_side = math.sqrt(core_area)
    core_height = math.ceil(raw_side / site_height) * site_height
    core_width = math.ceil(raw_side / site_width) * site_width

    pin_minimum = num_io_bits * PIN_PITCH[technology]
    if core_height < pin_minimum:
        core_height = math.ceil(pin_minimum / site_height) * site_height
        core_width = math.ceil((core_area / core_height) / site_width) * site_width
    minimum_height = MIN_ROWS[technology] * site_height
    if core_height < minimum_height:
        core_height = minimum_height
        core_width = math.ceil((core_area / core_height) / site_width) * site_width
    core_width = max(core_width, MIN_COLS[technology] * site_width)
    return round(core_width, 6), round(core_height, 6)


def platform_config(platform: dict, cell_lef: Path) -> str:
    read_libs = "\n".join(
        f"read_liberty {tcl_path(path)}" for path in platform["liberties"]
    )
    return (
        f"read_lef {tcl_path(platform['tech_lef'])}\n"
        f"read_lef {tcl_path(cell_lef)}\n"
        f"{read_libs}\n"
    )


def platform_floorplan(technology: str, core_width: float,
                       core_height: float) -> str:
    site_width = SITE_WIDTH[technology]
    site_height = SITE_HEIGHT[technology]
    core_x0 = site_width
    core_y0 = site_height
    core_x1 = round(core_x0 + core_width, 6)
    core_y1 = round(core_y0 + core_height, 6)
    die_width = round(core_x1 + site_width, 6)
    die_height = round(core_y1 + site_height, 6)
    site = "FreePDK45_38x28_10R_NP_162NW_34O" \
        if technology == "nangate45" else "asap7sc7p5t"
    base = f"""\
initialize_floorplan \\
    -die_area  "0 0 {die_width} {die_height}" \\
    -core_area "{core_x0} {core_y0} {core_x1} {core_y1}" \\
    -site {site}
"""
    if technology == "nangate45":
        tracks = """\
make_tracks M1  -x_offset 0.095 -x_pitch 0.19 -y_offset 0.07 -y_pitch 0.14
make_tracks M2  -x_offset 0.095 -x_pitch 0.19 -y_offset 0.07 -y_pitch 0.14
make_tracks M3  -x_offset 0.095 -x_pitch 0.19 -y_offset 0.07 -y_pitch 0.14
make_tracks M4  -x_offset 0.095 -x_pitch 0.28 -y_offset 0.07 -y_pitch 0.28
make_tracks M5  -x_offset 0.095 -x_pitch 0.28 -y_offset 0.07 -y_pitch 0.28
make_tracks M6  -x_offset 0.095 -x_pitch 0.28 -y_offset 0.07 -y_pitch 0.28
make_tracks M7  -x_offset 0.095 -x_pitch 0.8  -y_offset 0.07 -y_pitch 0.8
make_tracks M8  -x_offset 0.095 -x_pitch 0.8  -y_offset 0.07 -y_pitch 0.8
make_tracks M9  -x_offset 0.095 -x_pitch 1.6  -y_offset 0.07 -y_pitch 1.6
make_tracks M10 -x_offset 0.095 -x_pitch 1.6  -y_offset 0.07 -y_pitch 1.6
"""
    else:
        tracks = """\
make_tracks M1  -x_offset 0.095 -x_pitch 0.19 -y_offset 0.07 -y_pitch 0.14
make_tracks M2  -x_offset 0.095 -x_pitch 0.19 -y_offset 0.07 -y_pitch 0.14
make_tracks M3  -x_offset 0.095 -x_pitch 0.19 -y_offset 0.07 -y_pitch 0.14
make_tracks M4  -x_offset 0.095 -x_pitch 0.28 -y_offset 0.07 -y_pitch 0.28
make_tracks M5  -x_offset 0.095 -x_pitch 0.28 -y_offset 0.07 -y_pitch 0.28
make_tracks M6  -x_offset 0.095 -x_pitch 0.28 -y_offset 0.07 -y_pitch 0.28
make_tracks M7  -x_offset 0.095 -x_pitch 0.8  -y_offset 0.07 -y_pitch 0.8
make_tracks M8  -x_offset 0.095 -x_pitch 0.8  -y_offset 0.07 -y_pitch 0.8
make_tracks M9  -x_offset 0.095 -x_pitch 1.6  -y_offset 0.07 -y_pitch 1.6
make_tracks M10 -x_offset 0.095 -x_pitch 1.6  -y_offset 0.07 -y_pitch 1.6
make_tracks M6_m -x_offset 0.016 -x_pitch 0.064 -y_offset 0.016 -y_pitch 0.064
make_tracks M5_m -x_offset 0.012 -x_pitch 0.048 -y_offset 0.012 -y_pitch 0.048
make_tracks M4_m -x_offset 0.009 -x_pitch 0.036 -y_offset 0.012 -y_pitch 0.048
make_tracks M3_m -x_offset 0.009 -x_pitch 0.036 -y_offset 0.009 -y_pitch 0.036
make_tracks M2_m -x_offset 0.009 -x_pitch 0.036 -y_offset 0.045 -y_pitch 0.270
make_tracks M2_m -x_offset 0.009 -x_pitch 0.036 -y_offset 0.081 -y_pitch 0.270
make_tracks M2_m -x_offset 0.009 -x_pitch 0.036 -y_offset 0.117 -y_pitch 0.270
make_tracks M2_m -x_offset 0.009 -x_pitch 0.036 -y_offset 0.153 -y_pitch 0.270
make_tracks M2_m -x_offset 0.009 -x_pitch 0.036 -y_offset 0.189 -y_pitch 0.270
make_tracks M2_m -x_offset 0.009 -x_pitch 0.036 -y_offset 0.225 -y_pitch 0.270
make_tracks M2_m -x_offset 0.009 -x_pitch 0.036 -y_offset 0.270 -y_pitch 0.270
make_tracks M1_m -x_offset 0.009 -x_pitch 0.036 -y_offset 0.009 -y_pitch 0.036
"""
    return base + tracks


def platform_placement(technology: str, density: float) -> str:
    if technology == "nangate45":
        return f"""\
insert_tiecells "LOGIC0_X1_bottom/Z" -prefix "TIE_LO_"
insert_tiecells "LOGIC1_X1_bottom/Z" -prefix "TIE_HI_"
place_pins -hor_layers M3 -ver_layers M2 \\
    -exclude top:* -exclude right:* -exclude bottom:* \\
    -corner_avoidance 0 \\
    -min_distance 1 -min_distance_in_tracks
global_placement -density {density}
detailed_placement
check_placement -verbose
"""
    return f"""\
insert_tiecells "TIEHIx1_ASAP7_75t_R_upper/H" -prefix "TIE_HI_"
insert_tiecells "TIELOx1_ASAP7_75t_R_upper/L" -prefix "TIE_LO_"
place_pins -hor_layers M4_m -ver_layers M5_m \\
    -exclude top:* -exclude right:* -exclude bottom:* \\
    -corner_avoidance 0 \\
    -min_distance 1 -min_distance_in_tracks
global_placement -density {density}
detailed_placement
check_placement -verbose
"""


def platform_rc(technology: str) -> str:
    if technology == "nangate45":
        return """\
set_layer_rc -layer M1 -resistance 5.4286e-03 -capacitance 7.41819E-02
set_layer_rc -layer M2 -resistance 3.5714e-03 -capacitance 6.74606E-02
set_layer_rc -layer M3 -resistance 3.5714e-03 -capacitance 8.88758E-02
set_layer_rc -layer M4 -resistance 1.5000e-03 -capacitance 1.07121E-01
set_layer_rc -layer M5 -resistance 1.5000e-03 -capacitance 1.08964E-01
set_layer_rc -layer M6 -resistance 1.5000e-03 -capacitance 1.02044E-01
set_layer_rc -layer M7 -resistance 1.8750e-04 -capacitance 1.10436E-01
set_layer_rc -layer M8 -resistance 1.8750e-04 -capacitance 9.69714E-02
set_wire_rc -signal -layer M3
set_wire_rc -clock -layer M5
"""
    return """\
set_layer_rc -layer M1_m -resistance 1.267515E+00 -capacitance 1.000000E-08
set_layer_rc -layer M2_m -resistance 8.321600E-01 -capacitance 3.076000E-03
set_layer_rc -layer M3_m -resistance 6.538520E-01 -capacitance 2.566000E-03
set_layer_rc -layer M4_m -resistance 4.873990E-01 -capacitance 2.368000E-03
set_layer_rc -layer M5_m -resistance 4.632120E-01 -capacitance 2.145000E-03
set_layer_rc -layer M6_m -resistance 3.795810E-01 -capacitance 1.651000E-03
set_layer_rc -via V1_m -resistance 17.2
set_layer_rc -via V2_m -resistance 17.2
set_layer_rc -via V3_m -resistance 17.2
set_layer_rc -via V4_m -resistance 11.8
set_layer_rc -via V5_m -resistance 11.8
set_wire_rc -signal -resistance 3.23151E-02 -capacitance 1.73323E-01
set_wire_rc -clock -resistance 5.13971E-02 -capacitance 1.44549E-01
"""


def platform_routing(technology: str, output_dir: Path) -> str:
    guide = tcl_path(output_dir / "route.guide")
    drc = tcl_path(output_dir / "drc.rpt")
    if technology == "nangate45":
        return f"""\
set_global_routing_layer_adjustment M2-M3 0.25
set_global_routing_layer_adjustment M4-M8 0.1
set_routing_layers -signal M2-M8
global_route -guide_file {guide}
detailed_route -output_drc {drc} -droute_end_iter 2
"""
    return f"""\
set_global_routing_layer_adjustment M6_m-M2_m 0.25
set_routing_layers -signal M6_m-M2_m
set_routing_layers -clock M6_m-M4_m
global_route -critical_nets_percentage 0 -guide_file {guide}
set_routing_layers -signal M6_m-M2_m
detailed_route -output_drc {drc} -droute_end_iter 2 \\
    -via_in_pin_bottom_layer M1_m -via_in_pin_top_layer M3_m
"""


def build_tcl(netlist: Path, technology: str, platform: dict, cell_lef: Path,
              core_width: float, core_height: float, output_dir: Path,
              total_cell_area: float, density: float, threads: int) -> str:
    """Assemble the process-specific OpenROAD script for one sample."""
    final_def = tcl_path(output_dir / "final.def")
    image = tcl_path(output_dir / "layout.png")
    return (
        f"set_thread_count {threads}\n"
        + platform_config(platform, cell_lef)
        + f"read_verilog {tcl_path(netlist)}\nlink_design {netlist.stem}\n"
        + platform_floorplan(technology, core_width, core_height)
        + platform_placement(technology, density)
        + platform_rc(technology)
        + platform_routing(technology, output_dir)
        + f"set placed_cell_area_um2 {total_cell_area}\n"
        + """
set block [ord::get_db_block]
set dbu [[ord::get_db] getDbuPerMicron]
set xmin 1e15
set ymin 1e15
set xmax -1e15
set ymax -1e15
foreach inst [$block getInsts] {
    if {[$inst isFixed]} { continue }
    set bbox [$inst getBBox]
    set xmin [expr {min($xmin, [$bbox xMin])}]
    set ymin [expr {min($ymin, [$bbox yMin])}]
    set xmax [expr {max($xmax, [$bbox xMax])}]
    set ymax [expr {max($ymax, [$bbox yMax])}]
}
set bbox_w_um [expr {double($xmax - $xmin) / $dbu}]
set bbox_h_um [expr {double($ymax - $ymin) / $dbu}]
set bbox_area [expr {$bbox_w_um * $bbox_h_um}]
set util [expr {$placed_cell_area_um2 / $bbox_area}]
puts "BBOX_W_UM: $bbox_w_um"
puts "BBOX_H_UM: $bbox_h_um"
puts "BBOX_AREA_UM2: $bbox_area"
puts "UTILIZATION: $util"
"""
        + f"write_def {final_def}\nsave_image -resolution 0.001 {image}\n"
    )


def parse_log(log_path: Path) -> dict | None:
    """Read the four label metrics emitted by the generated Tcl."""
    content = log_path.read_text(encoding="utf-8", errors="replace")
    patterns = {
        "utilization": r"UTILIZATION:\s*([\d.eE+\-]+)",
        "bbox_w_um": r"BBOX_W_UM:\s*([\d.eE+\-]+)",
        "bbox_h_um": r"BBOX_H_UM:\s*([\d.eE+\-]+)",
        "bbox_area_um2": r"BBOX_AREA_UM2:\s*([\d.eE+\-]+)",
    }
    matches = {key: re.search(pattern, content) for key, pattern in patterns.items()}
    if not all(matches.values()):
        return None
    metrics = {key: float(match.group(1)) for key, match in matches.items()}
    if any(not math.isfinite(value) or value <= 0 for value in metrics.values()):
        return None
    return metrics


def locate_target(netlist_root: Path, group: str, values: list[str]) -> Path:
    expected = 1 if group == "ff_fa" else 3
    if len(values) != expected + 1:
        syntax = "MODULE W" if expected == 1 else "MODULE A B Y"
        raise ValueError(f"--target for {group} expects {syntax}")
    module = values[0].upper()
    prefix = f"{module}_" + "_".join(str(int(value)) for value in values[1:])
    directory = netlist_root / group / module
    matches = sorted(directory.glob(f"{prefix}_*.v"))
    exact = directory / f"{prefix}.v"
    if exact.is_file():
        matches.append(exact)
    matches = sorted(set(matches))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected one netlist matching {directory / (prefix + '*.v')}, "
            f"found {len(matches)}"
        )
    return matches[0]


def write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_one(task: tuple) -> tuple[str, str, str]:
    (netlist, netlist_root, feature_root, pnr_root, status_root,
     openroad_bin, technology, platform, cell_lef, target_util,
     density, threads, force) = task
    relative = netlist.relative_to(netlist_root)
    feature_file = feature_root / relative.with_suffix(".json")
    output_dir = pnr_root / relative.parent / netlist.stem
    log_file = output_dir / "pnr.log"
    label_file = output_dir / "label.json"
    status_file = status_root / relative.with_suffix(".json")
    wrapper = netlist.stem
    base_status = {
        "stage": "pnr",
        "technology": technology,
        "sample": wrapper,
        "input": str(netlist),
        "features": str(feature_file),
        "label": str(label_file),
        "log": str(log_file),
    }
    if label_file.is_file() and not force:
        try:
            metrics = json.loads(label_file.read_text(encoding="utf-8"))
            if all(math.isfinite(float(metrics[key])) and float(metrics[key]) > 0
                   for key in ("utilization", "bbox_w_um", "bbox_h_um", "bbox_area_um2")):
                write_status(status_file, {**base_status, "status": "skipped"})
                return wrapper, "skipped", str(label_file)
        except (KeyError, ValueError, OSError, json.JSONDecodeError):
            pass
    if not feature_file.is_file():
        message = f"feature file not found: {feature_file}"
        write_status(status_file, {**base_status, "status": "failed", "error": message})
        return wrapper, "failed", message
    try:
        features = json.loads(feature_file.read_text(encoding="utf-8"))
        total_cell_area = float(features["graph_features"]["total_cell_area"])
        core_width, core_height = compute_core_dims(
            technology, total_cell_area, count_io_bits(netlist), target_util
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        script = build_tcl(
            netlist, technology, platform, cell_lef, core_width, core_height,
            output_dir, total_cell_area, density, threads,
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl", delete=False,
                                         encoding="utf-8") as stream:
            stream.write(script)
            script_path = Path(stream.name)
        try:
            environment = os.environ.copy()
            environment["QT_QPA_PLATFORM"] = "offscreen"
            with log_file.open("w", encoding="utf-8") as log:
                completed = subprocess.run(
                    [str(openroad_bin), "-exit", str(script_path)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=environment,
                    check=False,
                )
        finally:
            script_path.unlink(missing_ok=True)
        metrics = parse_log(log_file) if completed.returncode == 0 else None
        if metrics is None:
            message = f"OpenROAD failed or emitted no valid metrics; see {log_file}"
            write_status(status_file, {
                **base_status, "status": "failed", "returncode": completed.returncode,
                "error": message,
            })
            return wrapper, "failed", message
        label_file.write_text(json.dumps({
            **metrics,
            "technology": technology,
            "target_utilization": target_util,
            "core_width_um": core_width,
            "core_height_um": core_height,
        }, indent=2) + "\n", encoding="utf-8")
        write_status(status_file, {
            **base_status, "status": "ok", "returncode": completed.returncode,
        })
        return wrapper, "ok", f"util={metrics['utilization']:.6f}"
    except Exception as error:
        write_status(status_file, {**base_status, "status": "failed",
                                   "error": str(error)})
        return wrapper, "failed", str(error)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate P&R utilization labels.")
    parser.add_argument("group", choices=GROUPS, help="netlist group to process")
    parser.add_argument("--target", nargs="+", metavar=("MODULE", "WIDTH"),
                        help="one sample: MODULE W or MODULE A B Y")
    parser.add_argument("--technology", choices=sorted(TRAINING_TECHNOLOGIES),
                        required=True, help="physical process")
    parser.add_argument("--platform-dir", type=Path, required=True,
                        help="TaiWei hybrid platform directory")
    parser.add_argument("--cell-lef", type=Path,
                        help="cell LEF override; required for patched ASAP7 routing")
    parser.add_argument("--openroad-bin", type=Path, required=True,
                        help="OpenROAD executable for this run")
    parser.add_argument("--data-root", type=Path, required=True,
                        help="external generation root")
    parser.add_argument("--target-util", type=float, default=0.3,
                        help="floorplan target utilization (default: 0.3)")
    parser.add_argument("--placement-density", type=float, default=2.0,
                        help="OpenROAD global-placement density (default: 2.0)")
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS,
                        help=f"threads per OpenROAD process (default: {DEFAULT_THREADS})")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"parallel OpenROAD processes (default: {DEFAULT_WORKERS})")
    parser.add_argument("--force", action="store_true",
                        help="rerun samples with existing valid labels")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 < args.target_util <= 1:
        raise ValueError("target-util must be in (0, 1]")
    if args.placement_density <= 0 or args.threads <= 0 or args.workers <= 0:
        raise ValueError("placement-density, threads and workers must be positive")
    openroad_bin = args.openroad_bin.expanduser().resolve()
    if not openroad_bin.is_file() or not openroad_bin.stat().st_mode & 0o111:
        raise FileNotFoundError(f"OpenROAD executable not found: {openroad_bin}")
    platform = resolve_training_platform(args.platform_dir, args.technology)
    cell_lef = (args.cell_lef.expanduser().resolve()
                if args.cell_lef else platform["cell_lef"])
    if not cell_lef.is_file():
        raise FileNotFoundError(f"cell LEF not found: {cell_lef}")
    if args.technology == "asap7" and args.cell_lef is None:
        raise ValueError(
            "ASAP7 requires --cell-lef pointing to the run-local output of "
            "00_patch_cell_lef.py"
        )

    data_root = args.data_root.expanduser().resolve()
    netlist_root = data_root / "02_netlist" / args.technology
    if args.target:
        netlists = [locate_target(netlist_root, args.group, args.target)]
    else:
        group_root = netlist_root / args.group
        netlists = sorted(group_root.rglob("*.v")) if group_root.is_dir() else []
    if not netlists:
        raise FileNotFoundError(f"no netlists found for group {args.group}")

    feature_root = data_root / "03_features" / args.technology
    pnr_root = data_root / "04_pnr" / args.technology
    status_root = data_root / "status/04_pnr" / args.technology
    tasks = [
        (path, netlist_root, feature_root, pnr_root, status_root,
         openroad_bin, args.technology, platform, cell_lef,
         args.target_util, args.placement_density, args.threads, args.force)
        for path in netlists
    ]
    counts = {"ok": 0, "skipped": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for wrapper, state, detail in executor.map(run_one, tasks):
            counts[state] += 1
            print(f"[{state}] {wrapper}: {detail}")
    print("Done. " + " ".join(f"{key}={value}" for key, value in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
