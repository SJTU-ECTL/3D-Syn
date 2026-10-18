"""Run the two-pass heterogeneous Genus synthesis sequence for upper and bottom tiers."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
STAGE_DIR = os.path.dirname(MODULE_DIR)
FLOW_DIR = os.path.dirname(STAGE_DIR)
sys.path.insert(0, STAGE_DIR)
sys.path.insert(0, FLOW_DIR)

from env_isolation.eda_subprocess_env import eda_subprocess_env


_REAL_SUBPROCESS = subprocess


class _EdaSubprocess:
    """Keep the approved runner body while cleaning the child EDA environment."""

    @staticmethod
    def run(*args, **kwargs):
        kwargs.setdefault("env", eda_subprocess_env())
        return _REAL_SUBPROCESS.run(*args, **kwargs)


subprocess = _EdaSubprocess()
from flow_config import (
    BOTTOM_CELL_SUFFIX,
    BOTTOM_COARSE_MODULE_NAME,
    ABC_DRV_CELL_BOTTOM,
    ABC_DRV_CELL_UPPER,
    BOTTOM_LIB_DIR,
    BOTTOM_LIB_FILES,
    CELL_MAPPING_PLAN_JSON,
    CELL_NAME_MAPPING_MODE,
    CLOCK_PORT_MAP_JSON,
    DESIGN,
    DESIGN_MACRO_LIB_FILES,
    DESIGN_SDC_FILE,
    FLOW_VARIANT,
    GENUS,
    GENUS_DB_DIR,
    GENUS_INPUT_V,
    GENUS_LOG_PREFIX,
    GENUS_OUTPUT_JSON,
    GENUS_OUTPUT_V,
    GENUS_OPT_MODE,
    GENUS_PLAN_JSON,
    GENUS_POST_SDC,
    GENUS_REPORT_DIR,
    GENUS_SDC,
    GENUS_TCL,
    GENUS_TMP_DIR,
    LIBRARY,
    MAP_JSON,
    SDC_CLK_PERIOD_PS,
    SDC_CLK_PERIOD_RAW,
    SDC_CLK_PERIOD_UNIT,
    SYNTHESIS_2D_LIB_DIR,
    SYNTHESIS_2D_LIB_FILES,
    SYNTHESIS_2D_PLATFORM_DIR,
    SYNTH_MODE,
    TIEHI_CELL_BOTTOM,
    TIEHI_CELL_UPPER,
    TIEHI_PIN_BOTTOM,
    TIEHI_PIN_UPPER,
    TIELO_CELL_BOTTOM,
    TIELO_CELL_UPPER,
    TIELO_PIN_BOTTOM,
    TIELO_PIN_UPPER,
    TOP_MODULE_NAME,
    UPPER_CELL_SUFFIX,
    UPPER_COARSE_MODULE_NAME,
    UPPER_LIB_DIR,
    UPPER_LIB_FILES,
    WRAPPER_BOTTOM_INSTANCE_NAME,
    WRAPPER_JSON,
    WRAPPER_PLAN_JSON,
    WRAPPER_TOP_MODULE_NAME,
    WRAPPER_UPPER_INSTANCE_NAME,
    WRAPPER_V,
    YOSYS,
)
from yosys_json_toolkit import (
    dump_design,
    load_design,
    module_port_shape_signature,
    normalize_module_port_shapes,
    validate_design,
)
from modules.hbt_model import build_hbt_model, render_hbt_tcl, resolve_hbt_config


SUPPORTED_HOMO_MAPPING_MODE = "homogeneous_base"
SUPPORTED_HETERO_MAPPING_MODE = "heterogeneous_map_json"
SUPPORTED_CELL_PIN_DIRECTIONS = {"input", "output"}
LIBERTY_CELL_RE = re.compile(r"\bcell\s*\(\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\)")
SDC_SET_RE = re.compile(r"^\s*set\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*$")
SDC_SET_UNITS_TIME_RE = re.compile(r"^\s*set_units\s+-time\s+([0-9.]+)\s*(ps|ns)\s*$", re.IGNORECASE)
SDC_CREATE_CLOCK_PERIOD_RE = re.compile(r"\bcreate_clock\b.*\s-period\s+\"?([0-9.]+|\$[A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)
GENUS_SEVERITY_SUMMARY_RE = re.compile(r"\bError\s*=\s*(\d+)\s*,\s*Fatal\s*=\s*(\d+)", re.IGNORECASE)
GENUS_MESSAGE_TABLE_RE = re.compile(r"^\|\s*[^|]+\|\s*(Error|Fatal)\s*\|\s*(\d+)\s*\|", re.IGNORECASE)
GENUS_EXPLICIT_SEVERITY_RE = re.compile(r"^\s*(\*+\s*)?(Error|Fatal)\s*[:(]", re.IGNORECASE)
GENUS_REPORT_COUNT_RE = re.compile(r"^(?P<label>.+?)\s+(?P<count>\d+)\s*$")
GENUS_UNCLOCKED_SOURCE_RE = re.compile(
    r"^pin:(?P<pin>\S+)\s+port:(?P<source>\S+)\s+\{Unclocked source\}$"
)
D2D_EXTRA_RC_TCL = os.path.join(GENUS_TMP_DIR, "06_hbt_model.tcl")
D2D_EXTRA_RC_JSON = os.path.join(GENUS_TMP_DIR, "06_hbt_model.json")



from modules.genus_common import (
    DEFAULT_MAX_CPUS,
    assert_no_design_submodule_boundaries,
    assert_no_direct_constants_in_synthesized_design,
    assert_no_multiple_wrapper_drivers,
    assert_sdc_clock_period_ps,
    analyze_unclocked_sequential_sources,
    assert_verilog_readback_with_libs,
    collect_protected_top_names,
    collect_reachable_module_names,
    emit_verilog_to_json,
    format_tcl_list,
    genus_syn_opt_command,
    load_inputs,
    load_json_file,
    normalize_genus_primary_interface,
    parse_boundary_report,
    parse_dangerous_messages,
    parse_design_sdc_vars,
    parse_ungroup_report,
    read_text,
    render_constant_tie_configuration,
    scan_liberty_cell_names,
    tcl_quote,
    tier_constant_tie_specs,
    write_clock_sdc_lines,
    write_d2d_extra_rc_artifacts,
    write_json_file,
    write_text,
)

def all_hetero_lib_files():
    libs = list(UPPER_LIB_FILES) + list(BOTTOM_LIB_FILES)
    assert libs, "Heterogeneous Genus lib list is empty"
    return libs


def module_signature(module):
    return OrderedDict(
        [
            (
                "ports",
                OrderedDict(
                    (
                        name,
                        OrderedDict(
                            [
                                ("direction", port.direction),
                                ("bits", list(port.bits)),
                                ("offset", port.offset),
                                ("upto", port.upto),
                                ("signed", port.signed),
                            ]
                        ),
                    )
                    for name, port in sorted(module.ports.items())
                ),
            ),
            (
                "cells",
                OrderedDict(
                    (
                        name,
                        OrderedDict(
                            [
                                ("type", cell.type),
                                ("parameters", OrderedDict(sorted(cell.parameters.items()))),
                                (
                                    "port_directions",
                                    OrderedDict(sorted(cell.port_directions.items())),
                                ),
                                (
                                    "connections",
                                    OrderedDict((key, list(value)) for key, value in sorted(cell.connections.items())),
                                ),
                            ]
                        ),
                    )
                    for name, cell in sorted(module.cells.items())
                ),
            ),
            (
                "netnames",
                OrderedDict(
                    (
                        name,
                        OrderedDict(
                            [
                                ("bits", list(netname.bits)),
                                ("offset", netname.offset),
                                ("upto", netname.upto),
                                ("signed", netname.signed),
                            ]
                        ),
                    )
                    for name, netname in sorted(module.netnames.items())
                ),
            ),
        ]
    )


def module_frozen_behavior_signature(module):
    return OrderedDict(
        [
            (
                "ports",
                OrderedDict(
                    (
                        name,
                        OrderedDict(
                            [
                                ("direction", port.direction),
                                ("bits", list(port.bits)),
                                ("offset", port.offset),
                                ("upto", port.upto),
                                ("signed", port.signed),
                            ]
                        ),
                    )
                    for name, port in sorted(module.ports.items())
                ),
            ),
            (
                "cells",
                OrderedDict(
                    (
                        name,
                        OrderedDict(
                            [
                                ("type", cell.type),
                                ("parameters", OrderedDict(sorted(cell.parameters.items()))),
                                (
                                    "port_directions",
                                    OrderedDict(sorted(cell.port_directions.items())),
                                ),
                                (
                                    "connections",
                                    OrderedDict((key, list(value)) for key, value in sorted(cell.connections.items())),
                                ),
                            ]
                        ),
                    )
                    for name, cell in sorted(module.cells.items())
                ),
            ),
        ]
    )

def collect_leaf_cell_types_from_root(design, root_module_name, leaf_module_types=None):
    leaf_module_types = set(leaf_module_types or [])
    leaf_types = set()
    for module_name in collect_reachable_module_names(design, root_module_name, leaf_module_types):
        module = design.get_module(module_name)
        for cell in module.cells.values():
            if cell.type not in design.modules or cell.type in leaf_module_types:
                leaf_types.add(cell.type)
    return leaf_types


def collect_leaf_cell_instances_from_root(design, root_module_name, leaf_module_types=None):
    leaf_module_types = set(leaf_module_types or [])
    leaf_cells = []
    for module_name in collect_reachable_module_names(design, root_module_name, leaf_module_types):
        module = design.get_module(module_name)
        for cell in module.cells.values():
            if cell.type not in design.modules or cell.type in leaf_module_types:
                leaf_cells.append((module_name, cell.name, cell.type))
    return leaf_cells


def assert_hetero_wrapper_input_contract(wrapper_design, wrapper_plan, clock_port_map, cell_mapping_plan):
    assert SYNTH_MODE == "genus_hetero", (
        "06 heterogeneous Genus requires SYNTH_MODE=genus_hetero, got {}".format(SYNTH_MODE)
    )
    assert CELL_NAME_MAPPING_MODE == SUPPORTED_HETERO_MAPPING_MODE, (
        "06 heterogeneous Genus requires CELL_NAME_MAPPING_MODE={}, got {}".format(
            SUPPORTED_HETERO_MAPPING_MODE, CELL_NAME_MAPPING_MODE
        )
    )
    assert MAP_JSON and os.path.isfile(MAP_JSON), "Heterogeneous Genus requires MAP_JSON: {}".format(MAP_JSON)
    assert cell_mapping_plan["mapping_mode"] == CELL_NAME_MAPPING_MODE, (
        "CELL_MAPPING_PLAN_JSON mapping_mode mismatch: {} != {}".format(
            cell_mapping_plan["mapping_mode"], CELL_NAME_MAPPING_MODE
        )
    )
    assert wrapper_plan["identity"]["mapping_mode"] == CELL_NAME_MAPPING_MODE
    assert wrapper_plan["identity"]["library"] == LIBRARY
    assert wrapper_plan["identity"]["design"] == DESIGN
    assert WRAPPER_TOP_MODULE_NAME in wrapper_design.modules, "Wrapper top missing: {}".format(WRAPPER_TOP_MODULE_NAME)
    assert UPPER_COARSE_MODULE_NAME in wrapper_design.modules, "Upper module missing: {}".format(UPPER_COARSE_MODULE_NAME)
    assert BOTTOM_COARSE_MODULE_NAME in wrapper_design.modules, "Bottom module missing: {}".format(BOTTOM_COARSE_MODULE_NAME)

    modules_plan = wrapper_plan["modules"]
    assert modules_plan["wrapper_top"] == WRAPPER_TOP_MODULE_NAME
    assert modules_plan["upper_module"] == UPPER_COARSE_MODULE_NAME
    assert modules_plan["bottom_module"] == BOTTOM_COARSE_MODULE_NAME
    assert modules_plan["upper_instance"] == WRAPPER_UPPER_INSTANCE_NAME
    assert modules_plan["bottom_instance"] == WRAPPER_BOTTOM_INSTANCE_NAME

    wrapper_top = wrapper_design.get_module(WRAPPER_TOP_MODULE_NAME)
    expected_cells = {WRAPPER_UPPER_INSTANCE_NAME, WRAPPER_BOTTOM_INSTANCE_NAME}
    assert set(wrapper_top.cells.keys()) == expected_cells, (
        "Wrapper top must contain only inst_upper/inst_bottom, got {}".format(sorted(wrapper_top.cells.keys()))
    )
    assert wrapper_top.cells[WRAPPER_UPPER_INSTANCE_NAME].type == UPPER_COARSE_MODULE_NAME
    assert wrapper_top.cells[WRAPPER_BOTTOM_INSTANCE_NAME].type == BOTTOM_COARSE_MODULE_NAME
    assert len(wrapper_plan["data_d2d_channels"]) == wrapper_plan["checks"]["data_d2d_count"]
    assert len(wrapper_plan["clock"]["clock_d2d_channels"]) == wrapper_plan["checks"]["clock_d2d_count"]
    assert clock_port_map["source_clock_port_name"] == wrapper_plan["clock"]["source_clock_port"]
    assert_no_multiple_wrapper_drivers(wrapper_top)


def assert_hetero_lib_coverage(wrapper_design):
    assert os.path.isdir(UPPER_LIB_DIR), "UPPER_LIB_DIR missing: {}".format(UPPER_LIB_DIR)
    assert os.path.isdir(BOTTOM_LIB_DIR), "BOTTOM_LIB_DIR missing: {}".format(BOTTOM_LIB_DIR)
    assert UPPER_LIB_FILES, "UPPER_LIB_FILES is empty"
    assert BOTTOM_LIB_FILES, "BOTTOM_LIB_FILES is empty"
    upper_lib_cells = scan_liberty_cell_names(UPPER_LIB_FILES)
    bottom_lib_cells = scan_liberty_cell_names(BOTTOM_LIB_FILES)
    overlap = sorted(upper_lib_cells & bottom_lib_cells)
    assert not overlap, "Upper/bottom liberty cell sets overlap: {}".format(overlap[:50])

    all_lib_cells = upper_lib_cells | bottom_lib_cells
    upper_leaf = collect_leaf_cell_types_from_root(wrapper_design, UPPER_COARSE_MODULE_NAME, all_lib_cells)
    bottom_leaf = collect_leaf_cell_types_from_root(wrapper_design, BOTTOM_COARSE_MODULE_NAME, all_lib_cells)
    assert upper_leaf, "No upper leaf cells found before hetero Genus"
    assert bottom_leaf, "No bottom leaf cells found before hetero Genus"
    upper_missing = sorted(upper_leaf - upper_lib_cells)
    bottom_missing = sorted(bottom_leaf - bottom_lib_cells)
    upper_wrong = sorted(upper_leaf & bottom_lib_cells)
    bottom_wrong = sorted(bottom_leaf & upper_lib_cells)
    assert not upper_missing, "Upper leaf cells missing from upper liberty: {}".format(upper_missing[:50])
    assert not bottom_missing, "Bottom leaf cells missing from bottom liberty: {}".format(bottom_missing[:50])
    assert not upper_wrong, "Upper module contains bottom library cells: {}".format(upper_wrong[:50])
    assert not bottom_wrong, "Bottom module contains upper library cells: {}".format(bottom_wrong[:50])

    checks = OrderedDict(
        [
            ("upper_lib_dir", UPPER_LIB_DIR),
            ("bottom_lib_dir", BOTTOM_LIB_DIR),
            ("upper_lib_files", list(UPPER_LIB_FILES)),
            ("bottom_lib_files", list(BOTTOM_LIB_FILES)),
            ("upper_lib_file_count", len(UPPER_LIB_FILES)),
            ("bottom_lib_file_count", len(BOTTOM_LIB_FILES)),
            ("upper_lib_cell_count", len(upper_lib_cells)),
            ("bottom_lib_cell_count", len(bottom_lib_cells)),
            ("upper_bottom_overlap_count", len(overlap)),
            ("initial_upper_leaf_cell_count", len(upper_leaf)),
            ("initial_bottom_leaf_cell_count", len(bottom_leaf)),
            ("initial_upper_missing_cell_count", len(upper_missing)),
            ("initial_bottom_missing_cell_count", len(bottom_missing)),
        ]
    )
    return checks, upper_lib_cells, bottom_lib_cells


def write_hetero_genus_sdc(wrapper_plan):
    vars_by_name = parse_design_sdc_vars(DESIGN_SDC_FILE)
    clock_port = wrapper_plan["clock"]["source_clock_port"]
    assert vars_by_name["clk_port_name"] == clock_port, (
        "DESIGN_SDC_FILE clk_port_name {} != wrapper clock port {}".format(
            vars_by_name["clk_port_name"], clock_port
        )
    )
    lines = [
        "# Generated by per-tier synthesis step 06 in heterogeneous mode.",
        "# Output load intentionally disabled by policy in the first hetero implementation.",
        *write_clock_sdc_lines(vars_by_name, clock_port, wrapper_plan["clock"]["clock_d2d_channels"]),
    ]
    write_text(GENUS_SDC, "\n".join(lines) + "\n")
    assert "set_load" not in read_text(GENUS_SDC), "Hetero GENUS_SDC must not set output load"
    input_sdc_clock = assert_sdc_clock_period_ps(GENUS_SDC, SDC_CLK_PERIOD_PS)
    return OrderedDict(
        [
            ("output_load_status", "disabled_by_policy"),
            ("clock_name", vars_by_name["clk_name"]),
            ("clock_port", clock_port),
            ("clock_period_raw", SDC_CLK_PERIOD_RAW),
            ("clock_period_unit", SDC_CLK_PERIOD_UNIT),
            ("clock_period_ps", SDC_CLK_PERIOD_PS),
            ("clock_io_pct", vars_by_name["clk_io_pct"]),
            ("input_sdc_clock", input_sdc_clock),
        ]
    )


def hetero_pass_paths(pass_name):
    pass_dir = os.path.join(GENUS_TMP_DIR, "hetero", pass_name)
    return OrderedDict(
        [
            ("pass_dir", pass_dir),
            ("input_v", os.path.join(pass_dir, "input.v")),
            ("input_json", os.path.join(pass_dir, "input.json")),
            ("sdc", os.path.join(pass_dir, "genus.sdc")),
            ("tcl", os.path.join(pass_dir, "run.tcl")),
            ("output_v", os.path.join(pass_dir, "output.v")),
            ("output_json", os.path.join(pass_dir, "output.json")),
            ("output_sdc", os.path.join(pass_dir, "output.sdc")),
            ("log_prefix", os.path.join(pass_dir, "genus")),
            ("report_dir", os.path.join(pass_dir, "reports")),
            ("db_dir", os.path.join(pass_dir, "db")),
        ]
    )


def hetero_report_paths(paths):
    report_dir = paths["report_dir"]
    return OrderedDict(
        [
            ("check_design", os.path.join(report_dir, "check_design.rpt")),
            ("timing_intent", os.path.join(report_dir, "check_timing_intent.rpt")),
            ("pre_messages", os.path.join(report_dir, "pre_messages.rpt")),
            ("post_messages", os.path.join(report_dir, "post_messages.rpt")),
            ("pre_gates", os.path.join(report_dir, "pre_gates.rpt")),
            ("post_gates", os.path.join(report_dir, "post_gates.rpt")),
            ("post_area", os.path.join(report_dir, "post_area.rpt")),
            ("post_timing", os.path.join(report_dir, "post_timing.rpt")),
            ("final", os.path.join(report_dir, "final.rpt")),
            ("final_area", os.path.join(report_dir, "final_area.rpt")),
            ("final_gates", os.path.join(report_dir, "final_gates.rpt")),
            ("final_qor", os.path.join(report_dir, "final_qor.rpt")),
            ("final_time", os.path.join(report_dir, "final_time.rpt")),
            ("ungroup", os.path.join(report_dir, "ungroup_modules.rpt")),
            ("active_boundary_before", os.path.join(report_dir, "active_boundary_before.rpt")),
            ("frozen_boundary_before", os.path.join(report_dir, "frozen_boundary_before.rpt")),
            ("active_boundary_after", os.path.join(report_dir, "active_boundary_after.rpt")),
            ("frozen_boundary_after", os.path.join(report_dir, "frozen_boundary_after.rpt")),
        ]
    )


def dont_use_pattern_for_frozen_tier(frozen_tier):
    if frozen_tier == "upper":
        return "*/*{}".format(UPPER_CELL_SUFFIX)
    if frozen_tier == "bottom":
        return "*/*{}".format(BOTTOM_CELL_SUFFIX)
    raise AssertionError("Unsupported frozen tier: {}".format(frozen_tier))


def build_hetero_pass_descriptors(upper_lib_cells, bottom_lib_cells, wrapper_plan):
    protected_top_names = collect_protected_top_names(load_design(WRAPPER_JSON), wrapper_plan)
    pass_specs = [
        ("upper_first", "upper", "bottom"),
        ("bottom_final", "bottom", "upper"),
    ]
    descriptors = OrderedDict()
    for pass_name, active_tier, frozen_tier in pass_specs:
        paths = hetero_pass_paths(pass_name)
        reports = hetero_report_paths(paths)
        active_module = UPPER_COARSE_MODULE_NAME if active_tier == "upper" else BOTTOM_COARSE_MODULE_NAME
        frozen_module = UPPER_COARSE_MODULE_NAME if frozen_tier == "upper" else BOTTOM_COARSE_MODULE_NAME
        active_instance = WRAPPER_UPPER_INSTANCE_NAME if active_tier == "upper" else WRAPPER_BOTTOM_INSTANCE_NAME
        frozen_instance = WRAPPER_UPPER_INSTANCE_NAME if frozen_tier == "upper" else WRAPPER_BOTTOM_INSTANCE_NAME
        active_lib_files = list(UPPER_LIB_FILES) if active_tier == "upper" else list(BOTTOM_LIB_FILES)
        frozen_lib_files = list(UPPER_LIB_FILES) if frozen_tier == "upper" else list(BOTTOM_LIB_FILES)
        frozen_lib_cells = upper_lib_cells if frozen_tier == "upper" else bottom_lib_cells
        descriptors[pass_name] = OrderedDict(
            [
                ("pass_name", pass_name),
                ("active_tier", active_tier),
                ("frozen_tier", frozen_tier),
                ("active_module", active_module),
                ("frozen_module", frozen_module),
                ("active_instance", active_instance),
                ("frozen_instance", frozen_instance),
                ("active_lib_files", active_lib_files),
                ("frozen_lib_files", frozen_lib_files),
                ("dont_use_pattern", dont_use_pattern_for_frozen_tier(frozen_tier)),
                ("dont_use_cell_count", len(frozen_lib_cells)),
                ("protected_top_names", protected_top_names),
                ("output_load_status", "disabled_by_policy"),
                ("paths", paths),
                ("reports", reports),
            ]
        )
    return descriptors


def write_hetero_pass_tcl(desc):
    paths = desc["paths"]
    reports = desc["reports"]
    db_opt = os.path.join(paths["db_dir"], "{}_opt.db".format(desc["pass_name"]))
    tcl = """# Generated by per-tier synthesis step 06 in heterogeneous mode.
set_db max_cpus_per_server {max_cpus}
set_db super_thread_servers "localhost"
set_db hdl_flatten_complex_port true
set_db hdl_record_naming_style %s_%s
set_db auto_ungroup both

set lib_list [list {lib_files}]
set link_library $lib_list
set target_library $lib_list
set_db library $lib_list

set in_v {in_v}
set in_sdc {in_sdc}
set out_v {out_v}
set out_sdc {out_sdc}
set report_dir {report_dir}
set db_dir {db_dir}
set d2d_extra_rc_tcl {d2d_extra_rc_tcl}

foreach d [list $report_dir $db_dir] {{
  if {{![file exists $d]}} {{
    file mkdir $d
  }}
}}

read_hdl -sv $in_v
elaborate {wrapper_top}
init_design
read_sdc $in_sdc

{constant_tie_configuration}

set upper_mod [get_db modules {upper_module}]
set bottom_mod [get_db modules {bottom_module}]
set active_hinst [get_db hinsts {active_instance}]
set frozen_hinst [get_db hinsts {frozen_instance}]

if {{[llength $upper_mod] != 1}} {{ error "Unable to resolve upper module {upper_module}" }}
if {{[llength $bottom_mod] != 1}} {{ error "Unable to resolve bottom module {bottom_module}" }}
if {{[llength $active_hinst] != 1}} {{ error "Unable to resolve active hinst {active_instance}" }}
if {{[llength $frozen_hinst] != 1}} {{ error "Unable to resolve frozen hinst {frozen_instance}" }}

set_db / .boundary_opto false
set_db / .boundary_optimize_invert_hpins false
set_db / .boundary_optimize_feedthrough_hpins false
set_db $upper_mod .ungroup_ok false
set_db $bottom_mod .ungroup_ok false
set_db $upper_mod .boundary_opto strict_no
set_db $bottom_mod .boundary_opto strict_no
set_db $upper_mod .boundary_optimize_invert_hpins false
set_db $bottom_mod .boundary_optimize_invert_hpins false
set_db $upper_mod .boundary_optimize_feedthrough_hpins false
set_db $bottom_mod .boundary_optimize_feedthrough_hpins false
catch {{ set_db $active_hinst .ungroup_ok false }}
catch {{ set_db $frozen_hinst .ungroup_ok false }}
catch {{ set_db $frozen_hinst .preserve true }}
catch {{ set_db [get_db insts -if {{.base_name =~ {frozen_instance}/*}}] .dont_touch true }}

set active_internal_design_hinsts [get_db hinsts -if {{.name =~ {active_instance}/* && .module.name =~ _*}}]
puts "SYN3D_ACTIVE_INTERNAL_DESIGN_HINST_COUNT=[llength $active_internal_design_hinsts]"
if {{[llength $active_internal_design_hinsts] > 0}} {{
  ungroup $active_internal_design_hinsts
}}

set protected_top_name_count 0
set protected_top_object_count 0
foreach one_name [list {protected_top_names}] {{
  incr protected_top_name_count
  set net_objs [get_db nets $one_name]
  if {{[llength $net_objs] > 0}} {{
    incr protected_top_object_count [llength $net_objs]
    catch {{ set_db $net_objs .dont_touch true }}
  }}
}}

set frozen_lib_cells [get_db lib_cells {dont_use_pattern}]
if {{[llength $frozen_lib_cells] == 0}} {{ error "No frozen lib cells matched {dont_use_pattern}" }}
set_dont_use $frozen_lib_cells

puts "SYN3D_HETERO_PASS={pass_name}"
puts "SYN3D_ACTIVE_TIER={active_tier}"
puts "SYN3D_FROZEN_TIER={frozen_tier}"
puts "SYN3D_DONT_USE_PATTERN={dont_use_pattern}"
puts "SYN3D_DONT_USE_CELL_COUNT=[llength $frozen_lib_cells]"
puts "SYN3D_PROTECTED_TOP_NAME_COUNT=$protected_top_name_count"
puts "SYN3D_PROTECTED_TOP_OBJECT_COUNT=$protected_top_object_count"
puts "SYN3D_AUTO_UNGROUP=[get_db / .auto_ungroup]"
puts "SYN3D_OUTPUT_LOAD_STATUS=disabled_by_policy"
source $d2d_extra_rc_tcl
puts "INFO: SYN3D_GENUS_OPT_MODE = {genus_opt_mode}"

check_design -unresolved > {check_design}
check_timing_intent -verbose > {timing_intent}
report_messages > {pre_messages}
report_gates > {pre_gates}
report_ungroup_modules > {ungroup}
report_boundary_opt $active_hinst > {active_boundary_before}
report_boundary_opt $frozen_hinst > {frozen_boundary_before}

# Optimize the existing mapped netlist using the configured mode.
set syn_opt_rc [catch {{ {syn_opt_command} }} syn_opt_msg]

write_reports -directory $report_dir -tag final
report_messages > {post_messages}
report_gates > {post_gates}
report_area > {post_area}
report_timing > {post_timing}
report_boundary_opt $active_hinst > {active_boundary_after}
report_boundary_opt $frozen_hinst > {frozen_boundary_after}
write_db {db_opt}

write_hdl > $out_v
write_sdc > $out_sdc
if {{$syn_opt_rc != 0}} {{
  puts stderr $syn_opt_msg
  exit 2
}}
exit
""".format(
        max_cpus=DEFAULT_MAX_CPUS,
        lib_files=format_tcl_list(all_hetero_lib_files()),
        in_v=tcl_quote(paths["input_v"]),
        in_sdc=tcl_quote(paths["sdc"]),
        out_v=tcl_quote(paths["output_v"]),
        out_sdc=tcl_quote(paths["output_sdc"]),
        report_dir=tcl_quote(paths["report_dir"]),
        db_dir=tcl_quote(paths["db_dir"]),
        d2d_extra_rc_tcl=tcl_quote(D2D_EXTRA_RC_TCL),
        wrapper_top=WRAPPER_TOP_MODULE_NAME,
        upper_module=UPPER_COARSE_MODULE_NAME,
        bottom_module=BOTTOM_COARSE_MODULE_NAME,
        active_instance=desc["active_instance"],
        frozen_instance=desc["frozen_instance"],
        protected_top_names=format_tcl_list(desc["protected_top_names"]),
        dont_use_pattern=desc["dont_use_pattern"],
        pass_name=desc["pass_name"],
        active_tier=desc["active_tier"],
        frozen_tier=desc["frozen_tier"],
        genus_opt_mode=GENUS_OPT_MODE,
        syn_opt_command=genus_syn_opt_command(),
        constant_tie_configuration=render_constant_tie_configuration(
            tier_constant_tie_specs(desc["active_tier"])
        ),
        db_opt=tcl_quote(db_opt),
        **{key: tcl_quote(value) for key, value in reports.items()}
    )
    write_text(paths["tcl"], tcl)
    loaded = read_text(paths["tcl"])
    assert "set_load" not in loaded, "Hetero Tcl must not set output load"
    assert "set_max_transition" not in loaded, "Hetero Tcl must not set max transition"
    assert "set_db auto_ungroup both" in loaded, "Hetero Tcl must allow selective internal ungroup"
    assert "ungroup $active_internal_design_hinsts" in loaded, "Hetero Tcl missing explicit active internal hinst ungroup"
    expected_syn_opt = "set syn_opt_rc [catch {{ {} }} syn_opt_msg]".format(genus_syn_opt_command())
    assert expected_syn_opt in loaded, "Hetero Tcl missing configured syn_opt command"
    assert "source $d2d_extra_rc_tcl" in loaded, "Hetero Tcl missing d2d extra RC source"
    assert "SYN3D_CONSTANT_TIE_MAPPING=duplicate" in loaded, "Hetero Tcl missing duplicate constant tie mapping"
    return db_opt


def run_genus_with_tcl(tcl_path, log_prefix):
    assert os.path.exists(GENUS), "Genus not found: {}".format(GENUS)
    cmd = [
        GENUS,
        "-no_gui",
        "-batch",
        "-abort_on_error",
        "-overwrite",
        "-log",
        log_prefix,
        "-files",
        tcl_path,
    ]
    result = subprocess.run(cmd, check=False)
    return result.returncode, cmd


def assert_hetero_pass_output_files(desc, db_file):
    paths = desc["paths"]
    reports = desc["reports"]
    required = [
        paths["output_v"],
        paths["output_sdc"],
        paths["log_prefix"] + ".log",
        paths["log_prefix"] + ".cmd",
        db_file,
    ]
    required.extend(reports.values())
    for path in required:
        assert os.path.isfile(path), "Hetero pass expected output missing: {}".format(path)
        if path == reports["ungroup"]:
            continue
        assert os.path.getsize(path) > 0, "Hetero pass expected output is empty: {}".format(path)


def assert_hetero_top_shell_contract(output_design, wrapper_plan):
    validate_design(output_design)
    top = output_design.get_module(WRAPPER_TOP_MODULE_NAME)
    expected_cells = {WRAPPER_UPPER_INSTANCE_NAME, WRAPPER_BOTTOM_INSTANCE_NAME}
    assert set(top.cells.keys()) == expected_cells, (
        "Hetero output wrapper top must contain only inst_upper/inst_bottom, got {}".format(
            sorted(top.cells.keys())
        )
    )
    assert top.cells[WRAPPER_UPPER_INSTANCE_NAME].type == UPPER_COARSE_MODULE_NAME
    assert top.cells[WRAPPER_BOTTOM_INSTANCE_NAME].type == BOTTOM_COARSE_MODULE_NAME
    for channel in wrapper_plan["data_d2d_channels"]:
        name = channel["d2d_name"]
        assert name in top.netnames, "Hetero output wrapper top lost data d2d netname: {}".format(name)
    return OrderedDict(
        [
            ("top_cell_count", len(top.cells)),
            ("top_instance_names", sorted(top.cells.keys())),
            ("top_leaf_cell_count", 0),
            ("data_d2d_count", len(wrapper_plan["data_d2d_channels"])),
            ("clock_d2d_count", len(wrapper_plan["clock"]["clock_d2d_channels"])),
        ]
    )


def assert_no_wrong_lib_cells(output_design, upper_lib_cells, bottom_lib_cells):
    all_lib_cells = upper_lib_cells | bottom_lib_cells
    upper_wrong = []
    bottom_wrong = []
    upper_unknown = []
    bottom_unknown = []
    for module_name, cell_name, cell_type in collect_leaf_cell_instances_from_root(
        output_design, UPPER_COARSE_MODULE_NAME, all_lib_cells
    ):
        if cell_type in bottom_lib_cells:
            upper_wrong.append("{}.{}:{}".format(module_name, cell_name, cell_type))
        if cell_type not in upper_lib_cells:
            upper_unknown.append("{}.{}:{}".format(module_name, cell_name, cell_type))
    for module_name, cell_name, cell_type in collect_leaf_cell_instances_from_root(
        output_design, BOTTOM_COARSE_MODULE_NAME, all_lib_cells
    ):
        if cell_type in upper_lib_cells:
            bottom_wrong.append("{}.{}:{}".format(module_name, cell_name, cell_type))
        if cell_type not in bottom_lib_cells:
            bottom_unknown.append("{}.{}:{}".format(module_name, cell_name, cell_type))
    assert not upper_wrong, "Upper tier contains bottom lib cells: {}".format(upper_wrong[:50])
    assert not bottom_wrong, "Bottom tier contains upper lib cells: {}".format(bottom_wrong[:50])
    assert not upper_unknown, "Upper tier contains unknown leaf cells: {}".format(upper_unknown[:50])
    assert not bottom_unknown, "Bottom tier contains unknown leaf cells: {}".format(bottom_unknown[:50])
    return OrderedDict(
        [
            ("upper_wrong_lib_cell_count", len(upper_wrong)),
            ("bottom_wrong_lib_cell_count", len(bottom_wrong)),
            ("upper_unknown_cell_count", len(upper_unknown)),
            ("bottom_unknown_cell_count", len(bottom_unknown)),
        ]
    )


def assert_active_tier_internal_boundaries_removed(output_design, desc, upper_lib_cells, bottom_lib_cells):
    if desc["active_tier"] == "upper":
        leaf_module_types = upper_lib_cells
    elif desc["active_tier"] == "bottom":
        leaf_module_types = bottom_lib_cells
    else:
        raise AssertionError("Unsupported active tier: {}".format(desc["active_tier"]))
    return assert_no_design_submodule_boundaries(output_design, desc["active_module"], leaf_module_types)


def assert_final_tier_internal_boundaries_removed(output_design, upper_lib_cells, bottom_lib_cells):
    return OrderedDict(
        [
            (
                "upper",
                assert_no_design_submodule_boundaries(output_design, UPPER_COARSE_MODULE_NAME, upper_lib_cells),
            ),
            (
                "bottom",
                assert_no_design_submodule_boundaries(output_design, BOTTOM_COARSE_MODULE_NAME, bottom_lib_cells),
            ),
        ]
    )


def assert_frozen_reachable_modules_exact_match(input_design, output_design, frozen_root_module, leaf_module_types=None):
    input_modules = collect_reachable_module_names(input_design, frozen_root_module, leaf_module_types)
    output_modules = collect_reachable_module_names(output_design, frozen_root_module, leaf_module_types)
    assert set(input_modules) == set(output_modules), (
        "Frozen reachable module set changed for {}: input={} output={}".format(
            frozen_root_module, sorted(input_modules), sorted(output_modules)
        )
    )
    changed = []
    netname_alias_changed = []
    for module_name in sorted(input_modules):
        input_module = input_design.get_module(module_name)
        output_module = output_design.get_module(module_name)
        if module_frozen_behavior_signature(input_module) != module_frozen_behavior_signature(output_module):
            changed.append(module_name)
        elif module_signature(input_module) != module_signature(output_module):
            netname_alias_changed.append(module_name)
    assert not changed, "Frozen modules changed for {}: {}".format(frozen_root_module, changed)
    return OrderedDict(
        [
            ("frozen_root_module", frozen_root_module),
            ("frozen_reachable_module_count", len(input_modules)),
            ("frozen_behavior_exact_match", True),
            ("frozen_netname_alias_change_count", len(netname_alias_changed)),
            ("frozen_netname_alias_changed_modules", netname_alias_changed),
        ]
    )


def analyze_hetero_pass_reports(desc, wrapper_design, wrapper_plan):
    reports = desc["reports"]
    log_path = desc["paths"]["log_prefix"] + ".log"
    dangerous = parse_dangerous_messages(
        [log_path, reports["post_messages"], reports["check_design"]]
    )
    timing_dangerous = parse_dangerous_messages([reports["timing_intent"]])
    timing_dangerous = [
        hit
        for hit in timing_dangerous
        if not (
            hit["pattern"] == "missing clock"
            and "have no clock waveform driving them" in hit["text"].lower()
        )
    ]
    dangerous.extend(timing_dangerous)
    unclocked_sources = analyze_unclocked_sequential_sources(
        reports["timing_intent"], wrapper_design, wrapper_plan
    )
    post_sdc_clock = assert_sdc_clock_period_ps(desc["paths"]["output_sdc"], SDC_CLK_PERIOD_PS)
    active_boundary = parse_boundary_report(reports["active_boundary_after"])
    frozen_boundary = parse_boundary_report(reports["frozen_boundary_after"])
    ungroup = parse_ungroup_report(reports["ungroup"])
    assert not dangerous, "Dangerous Genus log/report patterns found in {}: {}".format(
        desc["pass_name"], dangerous[:10]
    )
    assert active_boundary["no_boundary_optimization"], (
        "{} active boundary optimization detected or unproven".format(desc["pass_name"])
    )
    assert frozen_boundary["no_boundary_optimization"], (
        "{} frozen boundary optimization detected or unproven".format(desc["pass_name"])
    )
    assert not ungroup["tier_modules_ungrouped"], "{} tier modules appear ungrouped: {}".format(
        desc["pass_name"], ungroup["tier_module_lines"]
    )
    return OrderedDict(
        [
            ("dangerous_messages", dangerous),
            ("dangerous_log_pattern_count", len(dangerous)),
            ("post_sdc_clock", post_sdc_clock),
            ("unclocked_sequential_sources", unclocked_sources),
            ("active_boundary", active_boundary),
            ("frozen_boundary", frozen_boundary),
            ("ungroup", ungroup),
        ]
    )


def prepare_hetero_inputs(wrapper_plan, descriptors):
    os.makedirs(GENUS_TMP_DIR, exist_ok=True)
    os.makedirs(os.path.join(GENUS_TMP_DIR, "hetero"), exist_ok=True)
    shutil.copyfile(WRAPPER_V, GENUS_INPUT_V)
    assert_verilog_readback_with_libs(GENUS_INPUT_V, WRAPPER_TOP_MODULE_NAME, all_hetero_lib_files())
    timing_constraints = write_hetero_genus_sdc(wrapper_plan)
    d2d_extra_rc_model = write_d2d_extra_rc_artifacts(wrapper_plan)
    for desc in descriptors.values():
        paths = desc["paths"]
        os.makedirs(paths["pass_dir"], exist_ok=True)
        os.makedirs(paths["report_dir"], exist_ok=True)
        os.makedirs(paths["db_dir"], exist_ok=True)
        shutil.copyfile(GENUS_SDC, paths["sdc"])
    shutil.copyfile(GENUS_INPUT_V, descriptors["upper_first"]["paths"]["input_v"])
    return timing_constraints, d2d_extra_rc_model


def run_hetero_pass(desc, wrapper_design, wrapper_plan, upper_lib_cells, bottom_lib_cells):
    paths = desc["paths"]
    emit_verilog_to_json(paths["input_v"], WRAPPER_TOP_MODULE_NAME, paths["input_json"], all_hetero_lib_files())
    input_design = load_design(paths["input_json"])
    validate_design(input_design)
    db_file = write_hetero_pass_tcl(desc)
    print("[06] running hetero Genus pass {} ...".format(desc["pass_name"]))
    return_code, cmd = run_genus_with_tcl(paths["tcl"], paths["log_prefix"])
    assert return_code == 0, "Genus pass {} failed with exit code {}".format(desc["pass_name"], return_code)
    assert_hetero_pass_output_files(desc, db_file)
    emit_verilog_to_json(paths["output_v"], WRAPPER_TOP_MODULE_NAME, paths["output_json"], all_hetero_lib_files())
    output_design = load_design(paths["output_json"])
    normalized_ports = normalize_genus_primary_interface(output_design, wrapper_design, paths["output_json"])
    top_checks = assert_hetero_top_shell_contract(output_design, wrapper_plan)
    top_checks["normalized_primary_port_count"] = len(normalized_ports)
    top_checks["normalized_primary_ports"] = normalized_ports
    wrong_lib_checks = assert_no_wrong_lib_cells(output_design, upper_lib_cells, bottom_lib_cells)
    active_boundary_checks = assert_active_tier_internal_boundaries_removed(
        output_design, desc, upper_lib_cells, bottom_lib_cells
    )
    frozen_checks = assert_frozen_reachable_modules_exact_match(
        input_design, output_design, desc["frozen_module"], upper_lib_cells | bottom_lib_cells
    )
    report_checks = analyze_hetero_pass_reports(desc, wrapper_design, wrapper_plan)
    return OrderedDict(
        [
            ("pass_name", desc["pass_name"]),
            ("active_tier", desc["active_tier"]),
            ("frozen_tier", desc["frozen_tier"]),
            ("active_module", desc["active_module"]),
            ("frozen_module", desc["frozen_module"]),
            ("active_instance", desc["active_instance"]),
            ("frozen_instance", desc["frozen_instance"]),
            ("paths", desc["paths"]),
            ("reports", desc["reports"]),
            ("genus_return_code", return_code),
            ("genus_cmd", cmd),
            ("db_file", db_file),
            ("dont_use_pattern", desc["dont_use_pattern"]),
            ("dont_use_cell_count", desc["dont_use_cell_count"]),
            ("protected_top_name_count", len(desc["protected_top_names"])),
            ("output_load_status", desc["output_load_status"]),
            ("top", top_checks),
            ("wrong_lib", wrong_lib_checks),
            ("active_internal_boundary", active_boundary_checks),
            ("frozen", frozen_checks),
            ("report", report_checks),
        ]
    )


def promote_hetero_final_outputs(final_desc):
    final_paths = final_desc["paths"]
    os.makedirs(GENUS_REPORT_DIR, exist_ok=True)
    os.makedirs(GENUS_DB_DIR, exist_ok=True)
    shutil.copyfile(final_paths["output_v"], GENUS_OUTPUT_V)
    shutil.copyfile(final_paths["output_json"], GENUS_OUTPUT_JSON)
    shutil.copyfile(final_paths["output_sdc"], GENUS_POST_SDC)
    shutil.copyfile(final_paths["tcl"], GENUS_TCL)
    for suffix in [".log", ".cmd"]:
        src = final_paths["log_prefix"] + suffix
        if os.path.isfile(src):
            shutil.copyfile(src, GENUS_LOG_PREFIX + suffix)
    for name in os.listdir(final_paths["report_dir"]):
        src = os.path.join(final_paths["report_dir"], name)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(GENUS_REPORT_DIR, name))
    for name in os.listdir(final_paths["db_dir"]):
        src = os.path.join(final_paths["db_dir"], name)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(GENUS_DB_DIR, name))
    assert os.path.isfile(GENUS_OUTPUT_V), "Failed to promote hetero GENUS_OUTPUT_V"
    assert os.path.isfile(GENUS_OUTPUT_JSON), "Failed to promote hetero GENUS_OUTPUT_JSON"


def build_hetero_genus_plan(liberty_checks, timing_constraints, d2d_extra_rc_model, descriptors, pass_results, final_checks):
    return OrderedDict(
        [
            (
                "identity",
                OrderedDict(
                    [
                        ("design", DESIGN),
                        ("library", LIBRARY),
                        ("flow_variant", FLOW_VARIANT),
                        ("synth_mode", SYNTH_MODE),
                        ("genus_opt_mode", GENUS_OPT_MODE),
                        ("mapping_mode", CELL_NAME_MAPPING_MODE),
                    ]
                ),
            ),
            (
                "inputs",
                OrderedDict(
                    [
                        ("wrapper_json", WRAPPER_JSON),
                        ("wrapper_v", WRAPPER_V),
                        ("wrapper_plan_json", WRAPPER_PLAN_JSON),
                        ("design_sdc", DESIGN_SDC_FILE),
                        ("clock_port_map_json", CLOCK_PORT_MAP_JSON),
                        ("cell_mapping_plan_json", CELL_MAPPING_PLAN_JSON),
                        ("map_json", MAP_JSON),
                        ("upper_lib_dir", UPPER_LIB_DIR),
                        ("upper_lib_files", list(UPPER_LIB_FILES)),
                        ("bottom_lib_dir", BOTTOM_LIB_DIR),
                        ("bottom_lib_files", list(BOTTOM_LIB_FILES)),
                    ]
                ),
            ),
            (
                "outputs",
                OrderedDict(
                    [
                        ("genus_input_v", GENUS_INPUT_V),
                        ("genus_sdc", GENUS_SDC),
                        ("genus_tcl", GENUS_TCL),
                        ("d2d_extra_rc_tcl", D2D_EXTRA_RC_TCL),
                        ("d2d_extra_rc_json", D2D_EXTRA_RC_JSON),
                        ("genus_output_v", GENUS_OUTPUT_V),
                        ("genus_output_json", GENUS_OUTPUT_JSON),
                        ("genus_post_sdc", GENUS_POST_SDC),
                        ("genus_plan_json", GENUS_PLAN_JSON),
                        ("log_prefix", GENUS_LOG_PREFIX),
                        ("report_dir", GENUS_REPORT_DIR),
                        ("db_dir", GENUS_DB_DIR),
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
            (
                "synthesis_strategy",
                OrderedDict(
                    [
                        ("name", "heterogeneous_sequential_freeze"),
                        ("pass_order", ["upper_first", "bottom_final"]),
                        ("final_pass", "bottom_final"),
                        ("auto_ungroup", "both"),
                        ("kept_boundaries", [UPPER_COARSE_MODULE_NAME, BOTTOM_COARSE_MODULE_NAME]),
                        ("top_shell_protection", "enabled"),
                        ("boundary_settings_applied", True),
                        ("timing_constraints", timing_constraints),
                        ("hbt_model", d2d_extra_rc_model),
                    ]
                ),
            ),
            ("liberty_checks", liberty_checks),
            ("pass_descriptors", descriptors),
            ("passes", pass_results),
            (
                "checks",
                OrderedDict(
                    [
                        ("pass_return_codes", OrderedDict((name, result["genus_return_code"]) for name, result in pass_results.items())),
                        ("final", final_checks),
                        ("unsupported_count", 0),
                    ]
                ),
            ),
        ]
    )


def assert_hetero_genus_plan_matches_outputs(plan):
    assert plan["identity"]["synth_mode"] == "genus_hetero"
    assert plan["checks"]["unsupported_count"] == 0
    assert plan["checks"]["pass_return_codes"] == OrderedDict([("upper_first", 0), ("bottom_final", 0)])
    assert plan["synthesis_strategy"]["timing_constraints"]["output_load_status"] == "disabled_by_policy"
    assert plan["outputs"]["d2d_extra_rc_tcl"] == D2D_EXTRA_RC_TCL
    assert os.path.isfile(plan["outputs"]["d2d_extra_rc_tcl"])
    assert plan["outputs"]["d2d_extra_rc_json"] == D2D_EXTRA_RC_JSON
    assert os.path.isfile(plan["outputs"]["d2d_extra_rc_json"])
    assert os.path.isfile(plan["outputs"]["genus_output_v"])
    assert os.path.isfile(plan["outputs"]["genus_output_json"])
    assert plan["liberty_checks"]["upper_bottom_overlap_count"] == 0
    assert plan["liberty_checks"]["initial_upper_missing_cell_count"] == 0
    assert plan["liberty_checks"]["initial_bottom_missing_cell_count"] == 0


def run_heterogeneous_genus():
    wrapper_design, wrapper_plan, clock_port_map, cell_mapping_plan = load_inputs()
    assert_hetero_wrapper_input_contract(wrapper_design, wrapper_plan, clock_port_map, cell_mapping_plan)
    liberty_checks, upper_lib_cells, bottom_lib_cells = assert_hetero_lib_coverage(wrapper_design)
    descriptors = build_hetero_pass_descriptors(upper_lib_cells, bottom_lib_cells, wrapper_plan)
    timing_constraints, d2d_extra_rc_model = prepare_hetero_inputs(wrapper_plan, descriptors)

    pass_results = OrderedDict()
    pass_results["upper_first"] = run_hetero_pass(
        descriptors["upper_first"], wrapper_design, wrapper_plan, upper_lib_cells, bottom_lib_cells
    )
    shutil.copyfile(descriptors["upper_first"]["paths"]["output_v"], descriptors["bottom_final"]["paths"]["input_v"])
    pass_results["bottom_final"] = run_hetero_pass(
        descriptors["bottom_final"], wrapper_design, wrapper_plan, upper_lib_cells, bottom_lib_cells
    )

    promote_hetero_final_outputs(descriptors["bottom_final"])
    final_design = load_design(GENUS_OUTPUT_JSON)
    normalized_ports = normalize_genus_primary_interface(final_design, wrapper_design, GENUS_OUTPUT_JSON)
    final_checks = assert_hetero_top_shell_contract(final_design, wrapper_plan)
    final_checks["normalized_primary_port_count"] = len(normalized_ports)
    final_checks["normalized_primary_ports"] = normalized_ports
    final_checks.update(assert_no_wrong_lib_cells(final_design, upper_lib_cells, bottom_lib_cells))
    final_checks["internal_boundaries"] = assert_final_tier_internal_boundaries_removed(
        final_design, upper_lib_cells, bottom_lib_cells
    )
    final_checks["direct_constants"] = assert_no_direct_constants_in_synthesized_design(
        final_design, upper_lib_cells | bottom_lib_cells
    )

    plan = build_hetero_genus_plan(
        liberty_checks=liberty_checks,
        timing_constraints=timing_constraints,
        d2d_extra_rc_model=d2d_extra_rc_model,
        descriptors=descriptors,
        pass_results=pass_results,
        final_checks=final_checks,
    )
    write_json_file(GENUS_PLAN_JSON, plan)
    assert_hetero_genus_plan_matches_outputs(load_json_file(GENUS_PLAN_JSON))
    print("[06] done")
    print("       genus_v    -> {}".format(GENUS_OUTPUT_V))
    print("       genus_json -> {}".format(GENUS_OUTPUT_JSON))
    print("       genus_plan -> {}".format(GENUS_PLAN_JSON))
