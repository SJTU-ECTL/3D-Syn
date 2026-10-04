"""Provide shared configuration, validation, SDC, and report helpers for Genus synthesis."""

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


def resolve_genus_max_cpus():
    """Use the public per-case CPU setting for every Genus invocation."""
    value = os.getenv("NUM_CORES", "1")
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise AssertionError("NUM_CORES must be a positive integer: {!r}".format(value))
    return value


DEFAULT_MAX_CPUS = resolve_genus_max_cpus()
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



def genus_syn_opt_command():
    if GENUS_OPT_MODE == "normal":
        return "syn_opt"
    if GENUS_OPT_MODE == "low_effort":
        return "syn_opt -incr -effort low"
    raise AssertionError("Unsupported SYN3D_GENUS_OPT_MODE: {}".format(GENUS_OPT_MODE))


def normalize_genus_primary_interface(output_design, reference_design, output_json):
    output_top = output_design.get_module(WRAPPER_TOP_MODULE_NAME)
    reference_top = reference_design.get_module(WRAPPER_TOP_MODULE_NAME)
    changed_ports = normalize_module_port_shapes(reference_top, output_top)
    assert module_port_shape_signature(output_top) == module_port_shape_signature(reference_top), (
        "Genus output primary interface differs from the wrapper input"
    )
    if changed_ports:
        dump_design(output_design, output_json)
    return changed_ports


def load_json_file(path):
    assert os.path.isfile(path), "Required JSON file not found: {}".format(path)
    with open(path) as fp:
        data = json.load(fp)
    assert isinstance(data, dict), "JSON file must contain a dictionary: {}".format(path)
    return data


def write_json_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fp:
        json.dump(data, fp, indent=2)
        fp.write("\n")
    loaded = load_json_file(path)
    assert loaded == data, "JSON readback differs from written file: {}".format(path)


def read_text(path):
    with open(path) as fp:
        return fp.read()


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fp:
        fp.write(text)


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def tcl_quote(value):
    return "{" + str(value).replace("}", "\\}") + "}"


def format_tcl_list(items):
    return " ".join(tcl_quote(item) for item in items)


def strip_required_suffix(value, suffix, label):
    assert value.endswith(suffix), "{} must end with {}: {}".format(label, suffix, value)
    base = value[: -len(suffix)]
    assert base, "{} becomes empty after removing suffix {}".format(label, suffix)
    return base


def homogeneous_constant_tie_specs():
    bottom = [
        (strip_required_suffix(TIELO_CELL_BOTTOM, BOTTOM_CELL_SUFFIX, "bottom TIELO cell"), TIELO_PIN_BOTTOM),
        (strip_required_suffix(TIEHI_CELL_BOTTOM, BOTTOM_CELL_SUFFIX, "bottom TIEHI cell"), TIEHI_PIN_BOTTOM),
    ]
    upper = [
        (strip_required_suffix(TIELO_CELL_UPPER, UPPER_CELL_SUFFIX, "upper TIELO cell"), TIELO_PIN_UPPER),
        (strip_required_suffix(TIEHI_CELL_UPPER, UPPER_CELL_SUFFIX, "upper TIEHI cell"), TIEHI_PIN_UPPER),
    ]
    assert bottom == upper, "Homogeneous tiers resolve to different base tie cells: {} != {}".format(bottom, upper)
    return bottom


def tier_constant_tie_specs(tier):
    if tier == "bottom":
        return [(TIELO_CELL_BOTTOM, TIELO_PIN_BOTTOM), (TIEHI_CELL_BOTTOM, TIEHI_PIN_BOTTOM)]
    if tier == "upper":
        return [(TIELO_CELL_UPPER, TIELO_PIN_UPPER), (TIEHI_CELL_UPPER, TIEHI_PIN_UPPER)]
    raise AssertionError("Unsupported tier for tie-cell configuration: {}".format(tier))


def render_constant_tie_configuration(tie_specs):
    assert len(tie_specs) == 2, "Expected TIELO/TIEHI specifications"
    assert tie_specs[0] != tie_specs[1], "TIELO and TIEHI specifications must differ"
    rendered_specs = " ".join(
        "[list {} {}]".format(tcl_quote(cell_name), tcl_quote(port_name))
        for cell_name, port_name in tie_specs
    )
    template = r"""proc syn3d_configure_constant_tie_cells {tie_specs} {
  set tie_cells {}
  foreach tie_spec $tie_specs {
    if {[llength $tie_spec] != 2} { error "Invalid constant tie-cell specification: $tie_spec" }
    set cell_name [lindex $tie_spec 0]
    set port_name [lindex $tie_spec 1]
    if {[regexp {[\[\]*?]} $cell_name]} {
      error "Constant tie-cell name must not contain glob metacharacters: $cell_name"
    }
    set cell_matches [get_db lib_cells */$cell_name]
    if {[llength $cell_matches] != 1} {
      error "Constant tie-cell $cell_name must match exactly one loaded library cell; matches=$cell_matches"
    }
    set tie_cell [lindex $cell_matches 0]
    set port_matches {}
    foreach lib_pin [get_db $tie_cell .lib_pins] {
      if {[get_db $lib_pin .base_name] eq $port_name} { lappend port_matches $lib_pin }
    }
    if {[llength $port_matches] != 1 || [get_db [lindex $port_matches 0] .direction] ne "out"} {
      error "Constant tie-cell port $cell_name/$port_name must resolve to exactly one output pin"
    }
    set_db $tie_cell .avoid false
    set_db $tie_cell .preserve false
    lappend tie_cells $tie_cell
  }
  if {[lindex $tie_cells 0] eq [lindex $tie_cells 1]} {
    error "TIELO and TIEHI resolve to the same library cell"
  }
  set_db use_tiehilo_for_const duplicate
  if {[get_db / .use_tiehilo_for_const] ne "duplicate"} {
    error "Failed to enable duplicate constant tie-cell mapping"
  }
  puts "SYN3D_CONSTANT_TIE_MAPPING=duplicate"
}
syn3d_configure_constant_tie_cells [list @TIE_SPECS@]
"""
    return template.replace("@TIE_SPECS@", rendered_specs)


def collect_all_d2d_channels(wrapper_plan):
    channels = []
    for channel in wrapper_plan["data_d2d_channels"]:
        channels.append(("data_d2d", channel))
    for channel in wrapper_plan["clock"]["clock_d2d_channels"]:
        channels.append(("clock_d2d", channel))
    return channels


def build_d2d_extra_rc_model(wrapper_plan):
    return build_hbt_model(
        wrapper_plan,
        LIBRARY,
        resolve_hbt_config(os.environ),
        {
            "upper": ABC_DRV_CELL_UPPER,
            "bottom": ABC_DRV_CELL_BOTTOM,
        },
    )


def write_d2d_extra_rc_artifacts(wrapper_plan):
    model = build_d2d_extra_rc_model(wrapper_plan)
    write_json_file(D2D_EXTRA_RC_JSON, model)
    write_text(D2D_EXTRA_RC_TCL, render_hbt_tcl(model))
    assert os.path.isfile(D2D_EXTRA_RC_TCL), "Failed to create {}".format(D2D_EXTRA_RC_TCL)
    return model


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


def build_read_liberty_tcl(lib_files):
    macro_libs = {os.path.realpath(path) for path in DESIGN_MACRO_LIB_FILES}
    commands = []
    for path in lib_files:
        options = "-lib -ignore_miss_func" if os.path.realpath(path) in macro_libs else "-ignore_miss_func"
        commands.append("read_liberty {} {}".format(options, path))
    return "\n".join(commands)


def assert_verilog_readback(v_path, module_name):
    tcl = """
{read_lib_tcl}
read_verilog {v_path}
hierarchy -check -top {module_name}
""".format(
        read_lib_tcl=build_read_liberty_tcl(SYNTHESIS_2D_LIB_FILES),
        v_path=v_path,
        module_name=module_name,
    )
    run_yosys_script(tcl, "verilog readback ({})".format(module_name))


def assert_verilog_readback_with_libs(v_path, module_name, lib_files):
    tcl = """
{read_lib_tcl}
read_verilog {v_path}
hierarchy -check -top {module_name}
""".format(
        read_lib_tcl=build_read_liberty_tcl(lib_files),
        v_path=v_path,
        module_name=module_name,
    )
    run_yosys_script(tcl, "verilog readback ({})".format(module_name))


def emit_verilog_to_json(in_v, top_module_name, out_json, lib_files):
    tcl = """
{read_lib_tcl}
read_verilog {in_v}
hierarchy -check -top {top_module_name}
write_json {out_json}
""".format(
        read_lib_tcl=build_read_liberty_tcl(lib_files),
        in_v=in_v,
        top_module_name=top_module_name,
        out_json=out_json,
    )
    run_yosys_script(tcl, "genus verilog -> json ({})".format(top_module_name))
    assert os.path.exists(out_json), "GENUS_OUTPUT_JSON was not created: {}".format(out_json)


def load_inputs():
    for path in [
        WRAPPER_JSON,
        WRAPPER_V,
        WRAPPER_PLAN_JSON,
        CLOCK_PORT_MAP_JSON,
        CELL_MAPPING_PLAN_JSON,
        DESIGN_SDC_FILE,
    ]:
        assert os.path.isfile(path), "Required 06 input missing: {}".format(path)
    wrapper_design = load_design(WRAPPER_JSON)
    validate_design(wrapper_design)
    wrapper_plan = load_json_file(WRAPPER_PLAN_JSON)
    clock_port_map = load_json_file(CLOCK_PORT_MAP_JSON)
    cell_mapping_plan = load_json_file(CELL_MAPPING_PLAN_JSON)
    return wrapper_design, wrapper_plan, clock_port_map, cell_mapping_plan


def assert_wrapper_input_contract(wrapper_design, wrapper_plan, clock_port_map, cell_mapping_plan):
    assert SYNTH_MODE == "genus_homo", (
        "06 homogeneous Genus currently supports SYNTH_MODE=genus_homo only, got {}".format(SYNTH_MODE)
    )
    assert CELL_NAME_MAPPING_MODE == SUPPORTED_HOMO_MAPPING_MODE, (
        "06 homogeneous Genus requires CELL_NAME_MAPPING_MODE={}, got {}".format(
            SUPPORTED_HOMO_MAPPING_MODE, CELL_NAME_MAPPING_MODE
        )
    )
    assert cell_mapping_plan["mapping_mode"] == CELL_NAME_MAPPING_MODE, (
        "CELL_MAPPING_PLAN_JSON mapping_mode mismatch: {} != {}".format(
            cell_mapping_plan["mapping_mode"], CELL_NAME_MAPPING_MODE
        )
    )
    assert WRAPPER_TOP_MODULE_NAME in wrapper_design.modules, "Wrapper top missing: {}".format(WRAPPER_TOP_MODULE_NAME)
    assert UPPER_COARSE_MODULE_NAME in wrapper_design.modules, "Upper module missing: {}".format(UPPER_COARSE_MODULE_NAME)
    assert BOTTOM_COARSE_MODULE_NAME in wrapper_design.modules, "Bottom module missing: {}".format(BOTTOM_COARSE_MODULE_NAME)

    modules_plan = wrapper_plan["modules"]
    assert modules_plan["wrapper_top"] == WRAPPER_TOP_MODULE_NAME
    assert modules_plan["upper_module"] == UPPER_COARSE_MODULE_NAME
    assert modules_plan["bottom_module"] == BOTTOM_COARSE_MODULE_NAME
    assert modules_plan["upper_instance"] == WRAPPER_UPPER_INSTANCE_NAME
    assert modules_plan["bottom_instance"] == WRAPPER_BOTTOM_INSTANCE_NAME
    assert wrapper_plan["identity"]["design"] == DESIGN
    assert wrapper_plan["identity"]["library"] == LIBRARY
    assert wrapper_plan["identity"]["mapping_mode"] == CELL_NAME_MAPPING_MODE

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
    assert clock_port_map["source_clock_bit_label"] == wrapper_plan["clock"]["source_clock_bit_label"]

    d2d_netnames = {
        name
        for name, netname in wrapper_top.netnames.items()
        if netname.attributes.get("syn3d_channel_kind") in {"data_d2d", "clock_d2d"}
    }
    plan_d2d = {item["d2d_name"] for item in wrapper_plan["data_d2d_channels"]}
    plan_d2d.update(item["d2d_name"] for item in wrapper_plan["clock"]["clock_d2d_channels"])
    assert d2d_netnames == plan_d2d, (
        "Wrapper d2d netnames mismatch: json={} plan={}".format(sorted(d2d_netnames), sorted(plan_d2d))
    )

    for instance_name, module_name in [
        (WRAPPER_UPPER_INSTANCE_NAME, UPPER_COARSE_MODULE_NAME),
        (WRAPPER_BOTTOM_INSTANCE_NAME, BOTTOM_COARSE_MODULE_NAME),
    ]:
        cell = wrapper_top.cells[instance_name]
        module = wrapper_design.get_module(module_name)
        assert set(cell.connections.keys()) == set(module.ports.keys()), (
            "Wrapper instance {} connections do not match module ports".format(instance_name)
        )
        assert cell.port_directions == {name: port.direction for name, port in module.ports.items()}, (
            "Wrapper instance {} port_directions do not match module ports".format(instance_name)
        )
        for direction in cell.port_directions.values():
            assert direction in SUPPORTED_CELL_PIN_DIRECTIONS, (
                "Unsupported wrapper instance pin direction: {}".format(direction)
            )

    assert_no_multiple_wrapper_drivers(wrapper_top)
    assert_tier_cells_are_base_named(wrapper_design)


def assert_no_multiple_wrapper_drivers(wrapper_top):
    drivers = OrderedDict()

    def add_driver(bit, label):
        if not isinstance(bit, int):
            return
        drivers.setdefault(bit, []).append(label)

    for port in wrapper_top.ports.values():
        if port.direction == "input":
            for bit in port.bits:
                add_driver(bit, "wrapper_input:{}".format(port.name))
    for cell in wrapper_top.cells.values():
        for pin_name, bits in cell.connections.items():
            if cell.port_directions.get(pin_name) != "output":
                continue
            for bit in bits:
                add_driver(bit, "{}.{}".format(cell.name, pin_name))
    multiple = {bit: names for bit, names in drivers.items() if len(names) > 1}
    assert not multiple, "Wrapper top has multiple drivers per bit: {}".format(multiple)


def assert_tier_cells_are_base_named(wrapper_design):
    bad = []
    for module_name in [UPPER_COARSE_MODULE_NAME, BOTTOM_COARSE_MODULE_NAME]:
        module = wrapper_design.get_module(module_name)
        for cell in module.cells.values():
            if cell.type in wrapper_design.modules:
                continue
            if cell.type.endswith(UPPER_CELL_SUFFIX) or cell.type.endswith(BOTTOM_CELL_SUFFIX):
                bad.append("{}.{}:{}".format(module_name, cell.name, cell.type))
    assert not bad, "Tier leaf cells must be base-named before 06 homo Genus: {}".format(bad[:20])


def collect_leaf_cell_types(design):
    module_names = set(design.modules.keys())
    leaf_types = set()
    for module_name, module in design.modules.items():
        if module_name == WRAPPER_TOP_MODULE_NAME:
            continue
        for cell in module.cells.values():
            if cell.type not in module_names:
                leaf_types.add(cell.type)
    assert leaf_types, "No leaf cells found in wrapper design"
    return leaf_types


def scan_liberty_cell_names(lib_files):
    cell_names = set()
    for lib_path in lib_files:
        assert os.path.isfile(lib_path), "Liberty file missing: {}".format(lib_path)
        assert os.path.getsize(lib_path) > 0, "Liberty file is empty: {}".format(lib_path)
        text = strip_comments(read_text(lib_path))
        cell_names.update(LIBERTY_CELL_RE.findall(text))
    assert cell_names, "No liberty cell definitions parsed from {}".format(lib_files)
    return cell_names


def assert_homo_2d_lib_coverage(wrapper_design):
    assert os.path.isdir(SYNTHESIS_2D_PLATFORM_DIR), (
        "SYNTHESIS_2D_PLATFORM_DIR missing: {}".format(SYNTHESIS_2D_PLATFORM_DIR)
    )
    assert os.path.isdir(SYNTHESIS_2D_LIB_DIR), "SYNTHESIS_2D_LIB_DIR missing: {}".format(SYNTHESIS_2D_LIB_DIR)
    assert SYNTHESIS_2D_LIB_FILES, "SYNTHESIS_2D_LIB_FILES is empty"
    leaf_types = collect_leaf_cell_types(wrapper_design)
    lib_cells = scan_liberty_cell_names(SYNTHESIS_2D_LIB_FILES)
    missing = sorted(leaf_types - lib_cells)
    assert not missing, (
        "2D liberty does not cover wrapper leaf cell types. Missing count={} examples={}".format(
            len(missing), missing[:50]
        )
    )
    checks = OrderedDict(
        [
            ("configured_platform_dir", SYNTHESIS_2D_PLATFORM_DIR),
            ("configured_lib_dir", SYNTHESIS_2D_LIB_DIR),
            ("configured_lib_files", list(SYNTHESIS_2D_LIB_FILES)),
            ("configured_lib_file_count", len(SYNTHESIS_2D_LIB_FILES)),
            ("leaf_cell_count", len(leaf_types)),
            ("lib_cell_count", len(lib_cells)),
            ("missing_leaf_cell_count", len(missing)),
            ("missing_leaf_cells", missing),
        ]
    )
    return checks, lib_cells


def parse_design_sdc_vars(path):
    vars_by_name = {}
    for line in read_text(path).splitlines():
        match = SDC_SET_RE.match(line)
        if not match:
            continue
        name = match.group(1)
        value = match.group(2).strip()
        if value.startswith("[") or value.startswith("$"):
            continue
        vars_by_name[name] = value
    for required in ["clk_name", "clk_port_name", "clk_period", "clk_io_pct"]:
        assert required in vars_by_name, "Required SDC variable {} missing from {}".format(required, path)
    return vars_by_name


def format_tcl_number(value):
    value = float(value)
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return str(int(rounded))
    return "{:.12g}".format(value)


def safe_tcl_var_suffix(name):
    suffix = re.sub(r"[^A-Za-z0-9_]+", "_", str(name)).strip("_")
    assert suffix, "Cannot build Tcl suffix from {!r}".format(name)
    if suffix[0].isdigit():
        suffix = "n_" + suffix
    return suffix


def clock_d2d_pin_path(channel):
    consumer_tier = channel["consumer_tier"]
    if consumer_tier == "upper":
        instance = WRAPPER_UPPER_INSTANCE_NAME
    elif consumer_tier == "bottom":
        instance = WRAPPER_BOTTOM_INSTANCE_NAME
    else:
        raise AssertionError("Unexpected clock d2d consumer tier: {}".format(channel))
    return "{}/{}".format(instance, channel["consumer_port"])


def write_generated_clock_sdc_lines(clock_d2d_channels):
    lines = []
    for index, channel in enumerate(clock_d2d_channels):
        d2d_name = channel["d2d_name"]
        suffix = safe_tcl_var_suffix("{}_{}".format(index, d2d_name))
        pin_path = clock_d2d_pin_path(channel)
        generated_name = "${{clk_name}}_{}".format(safe_tcl_var_suffix(d2d_name))
        lines.extend(
            [
                "",
                "# Generated clock crossing for {}.".format(d2d_name),
                "set clock_d2d_pin_{} [get_pins {}]".format(suffix, tcl_quote(pin_path)),
                'if {[llength $clock_d2d_pin_%s] != 1} { error "Expected exactly one clock d2d pin %s" }'
                % (suffix, pin_path),
                "create_generated_clock -name {} -source $clk_port -divide_by 1 $clock_d2d_pin_{}".format(
                    generated_name, suffix
                ),
            ]
        )
    return lines


def write_clock_sdc_lines(vars_by_name, clock_port, clock_d2d_channels=None):
    clock_d2d_channels = list(clock_d2d_channels or [])
    clock_period_ps = float(SDC_CLK_PERIOD_PS)
    assert clock_period_ps > 0, "SDC_CLK_PERIOD_PS must be positive, got {}".format(SDC_CLK_PERIOD_PS)
    return [
        "# Input clk_period {} {} normalized to {} ps for Genus.".format(
            SDC_CLK_PERIOD_RAW, SDC_CLK_PERIOD_UNIT, format_tcl_number(clock_period_ps)
        ),
        "set_units -time 1ps",
        "set_units -capacitance 1fF",
        "set clk_name {}".format(vars_by_name["clk_name"]),
        "set clk_port_name {}".format(clock_port),
        "set clk_period {}".format(format_tcl_number(clock_period_ps)),
        "set clk_io_pct {}".format(vars_by_name["clk_io_pct"]),
        "set clk_port [get_ports $clk_port_name]",
        'if {[llength $clk_port] != 1} { error "Expected exactly one clock port named $clk_port_name" }',
        "create_clock -name $clk_name -period $clk_period $clk_port",
        *write_generated_clock_sdc_lines(clock_d2d_channels),
        "set non_clock_inputs [all_inputs -no_clocks]",
        "set_input_delay [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs",
        "set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]",
    ]


def sdc_time_unit_to_ps_factor(unit_value, unit_name):
    value = float(unit_value)
    unit = unit_name.lower()
    if unit == "ps":
        return value
    if unit == "ns":
        return value * 1000.0
    raise AssertionError("Unsupported SDC time unit: {}{}".format(unit_value, unit_name))


def assert_sdc_clock_period_ps(path, expected_period_ps):
    text = read_text(path)
    vars_by_name = {}
    time_factor_ps = None
    clock_period = None
    for line in text.splitlines():
        set_match = SDC_SET_RE.match(line)
        if set_match:
            vars_by_name[set_match.group(1)] = set_match.group(2).strip()
        unit_match = SDC_SET_UNITS_TIME_RE.match(line.strip())
        if unit_match:
            time_factor_ps = sdc_time_unit_to_ps_factor(unit_match.group(1), unit_match.group(2))
            continue
        clock_match = SDC_CREATE_CLOCK_PERIOD_RE.search(line)
        if clock_match:
            assert clock_period is None, "Multiple create_clock -period lines found in {}".format(path)
            period_token = clock_match.group(1)
            if period_token.startswith("$"):
                period_var = period_token[1:]
                assert period_var in vars_by_name, (
                    "create_clock period variable {} not defined before use in {}".format(period_token, path)
                )
                period_token = vars_by_name[period_var]
            clock_period = float(period_token)
    assert time_factor_ps is not None, "SDC missing explicit set_units -time: {}".format(path)
    assert clock_period is not None, "SDC missing create_clock -period: {}".format(path)
    actual_period_ps = clock_period * time_factor_ps
    expected = float(expected_period_ps)
    assert abs(actual_period_ps - expected) < 1e-6, (
        "Clock period mismatch in {}: actual {} ps, expected {} ps".format(
            path, actual_period_ps, expected
        )
    )
    return OrderedDict(
        [
            ("path", path),
            ("time_unit_ps", time_factor_ps),
            ("clock_period_in_sdc_units", clock_period),
            ("clock_period_ps", actual_period_ps),
            ("expected_clock_period_ps", expected),
        ]
    )


def write_genus_sdc(wrapper_plan):
    vars_by_name = parse_design_sdc_vars(DESIGN_SDC_FILE)
    clock_port = wrapper_plan["clock"]["source_clock_port"]
    assert vars_by_name["clk_port_name"] == clock_port, (
        "DESIGN_SDC_FILE clk_port_name {} != wrapper clock port {}".format(
            vars_by_name["clk_port_name"], clock_port
        )
    )
    lines = [
        "# Generated by per-tier synthesis step 06.",
        *write_clock_sdc_lines(vars_by_name, clock_port, wrapper_plan["clock"]["clock_d2d_channels"]),
    ]
    text = "\n".join(lines) + "\n"
    write_text(GENUS_SDC, text)
    assert "create_clock" in read_text(GENUS_SDC), "GENUS_SDC missing create_clock after write"
    input_sdc_clock = assert_sdc_clock_period_ps(GENUS_SDC, SDC_CLK_PERIOD_PS)
    return OrderedDict(
        [
            ("clock_name", vars_by_name["clk_name"]),
            ("clock_port", clock_port),
            ("clock_period_raw", SDC_CLK_PERIOD_RAW),
            ("clock_period_unit", SDC_CLK_PERIOD_UNIT),
            ("clock_period_ps", SDC_CLK_PERIOD_PS),
            ("clock_io_pct", vars_by_name["clk_io_pct"]),
            ("input_sdc_clock", input_sdc_clock),
        ]
    )


def report_paths():
    prefix = WRAPPER_TOP_MODULE_NAME
    return OrderedDict(
        [
            ("check_design", os.path.join(GENUS_REPORT_DIR, "{}_check_design.rpt".format(prefix))),
            ("timing_intent", os.path.join(GENUS_REPORT_DIR, "{}_check_timing_intent.rpt".format(prefix))),
            ("pre_timing", os.path.join(GENUS_REPORT_DIR, "{}_pre_timing.rpt".format(prefix))),
            ("messages", os.path.join(GENUS_REPORT_DIR, "{}_messages.rpt".format(prefix))),
            ("gates", os.path.join(GENUS_REPORT_DIR, "{}_gates.rpt".format(prefix))),
            ("power", os.path.join(GENUS_REPORT_DIR, "{}_power.rpt".format(prefix))),
            ("area", os.path.join(GENUS_REPORT_DIR, "{}_area.rpt".format(prefix))),
            ("timing", os.path.join(GENUS_REPORT_DIR, "{}_timing.rpt".format(prefix))),
            ("ungroup", os.path.join(GENUS_REPORT_DIR, "{}_ungroup_modules.rpt".format(prefix))),
            ("upper_boundary", os.path.join(GENUS_REPORT_DIR, "{}_inst_upper_boundary_opt.rpt".format(prefix))),
            ("bottom_boundary", os.path.join(GENUS_REPORT_DIR, "{}_inst_bottom_boundary_opt.rpt".format(prefix))),
        ]
    )


def collect_protected_top_names(wrapper_design, wrapper_plan):
    wrapper_top = wrapper_design.get_module(WRAPPER_TOP_MODULE_NAME)
    names = set(wrapper_top.ports.keys())
    names.update(item["d2d_name"] for item in wrapper_plan["data_d2d_channels"])
    names.update(item["d2d_name"] for item in wrapper_plan["clock"]["clock_d2d_channels"])
    return sorted(names)

def assert_no_design_submodule_boundaries(output_design, module_name, leaf_module_types):
    reachable = collect_reachable_module_names(output_design, module_name, leaf_module_types)
    residual = [name for name in reachable if name != module_name]
    assert not residual, (
        "{} still has coarse-internal design submodule boundaries after Genus selective ungroup: {}".format(
            module_name, residual[:20]
        )
    )
    return OrderedDict(
        [
            ("module", module_name),
            ("reachable_design_module_count", len(reachable)),
            ("residual_design_submodule_count", len(residual)),
            ("residual_design_submodules", residual),
        ]
    )

def assert_no_direct_constants_in_synthesized_design(design, leaf_module_types):
    cell_pin_constants = []
    output_port_constants = []
    for module_name in collect_reachable_module_names(
        design, WRAPPER_TOP_MODULE_NAME, leaf_module_types
    ):
        module = design.get_module(module_name)
        for port_name, port in module.ports.items():
            if port.direction != "output":
                continue
            for bit_index, bit in enumerate(port.bits):
                if not isinstance(bit, int):
                    output_port_constants.append((module_name, port_name, bit_index, bit))
        for cell_name, cell in module.cells.items():
            for pin_name, bits in cell.connections.items():
                for bit_index, bit in enumerate(bits):
                    if not isinstance(bit, int):
                        cell_pin_constants.append((module_name, cell_name, pin_name, bit_index, bit))
    assert not cell_pin_constants, (
        "Genus output retains direct constants on cell pins; duplicate tie mapping failed: {}".format(
            cell_pin_constants[:20]
        )
    )
    assert not output_port_constants, (
        "Genus output retains direct constants on module outputs; duplicate tie mapping failed: {}".format(
            output_port_constants[:20]
        )
    )
    return OrderedDict(
        [
            ("cell_pin_constant_count", 0),
            ("output_port_constant_count", 0),
            ("mapping_mode", "duplicate"),
        ]
    )

def parse_dangerous_messages(paths):
    hits = []

    def append_hit(path, line_no, pattern, text):
        hits.append(
            OrderedDict(
                [
                    ("path", path),
                    ("line", line_no),
                    ("pattern", pattern),
                    ("text", text[:240]),
                ]
            )
        )

    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        for line_no, line in enumerate(read_text(path).splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            lowered = stripped.lower()

            summary_match = GENUS_SEVERITY_SUMMARY_RE.search(stripped)
            if summary_match:
                error_count = int(summary_match.group(1))
                fatal_count = int(summary_match.group(2))
                if error_count or fatal_count:
                    append_hit(path, line_no, "Error/Fatal summary", stripped)
                continue

            table_match = GENUS_MESSAGE_TABLE_RE.match(stripped)
            if table_match:
                severity = table_match.group(1)
                count = int(table_match.group(2))
                if count:
                    append_hit(path, line_no, "{} message count".format(severity), stripped)
                continue

            if GENUS_EXPLICIT_SEVERITY_RE.match(stripped):
                append_hit(path, line_no, "explicit Error/Fatal", stripped)
                continue

            if "check_design -unresolved" in lowered:
                continue
            if "no unresolved references" in lowered:
                continue
            if stripped == "Unresolved References & Empty Modules":
                continue
            if "unresolved" in lowered:
                append_hit(path, line_no, "unresolved", stripped)
                continue

            count_match = GENUS_REPORT_COUNT_RE.match(stripped)
            if count_match:
                label = count_match.group("label").strip().lower()
                count = int(count_match.group("count"))
                if label == "nets with multiple drivers":
                    if count:
                        append_hit(path, line_no, "multiple drivers", stripped)
                    continue
                if label == "loop-breaking cells for combinational feedback":
                    if count:
                        append_hit(path, line_no, "combinational loop", stripped)
                    continue

            if "multiple driver" in lowered or "multiple-driver" in lowered:
                append_hit(path, line_no, "multiple drivers", stripped)
                continue
            if "cdn_loop_breaker" in lowered or "combinational loop" in lowered:
                append_hit(path, line_no, "combinational loop", stripped)
                continue
            if "cannot find any clocks" in lowered:
                append_hit(path, line_no, "missing clock", stripped)
                continue
            if "no clock" in lowered and "no clocks" not in lowered:
                append_hit(path, line_no, "missing clock", stripped)
                continue
            if "no path" in lowered and "no paths" not in lowered:
                append_hit(path, line_no, "missing path", stripped)
                continue
    return hits


def parse_boundary_report(path):
    text = read_text(path)
    lowered = text.lower()
    has_change = "boundary change" in lowered
    no_boundary = "no boundary optimization done" in lowered or "no boundary optimization" in lowered
    return OrderedDict(
        [
            ("path", path),
            ("no_boundary_optimization", no_boundary and not has_change),
            ("has_boundary_change", has_change),
            ("preview", [line for line in text.splitlines()[:20]]),
        ]
    )


def parse_ungroup_report(path):
    text = read_text(path)
    interesting = []
    for line in text.splitlines():
        if UPPER_COARSE_MODULE_NAME in line or BOTTOM_COARSE_MODULE_NAME in line:
            interesting.append(line.strip())
    lowered = "\n".join(interesting).lower()
    ungrouped = any(word in lowered for word in ["ungrouped", "true", "yes"])
    return OrderedDict(
        [
            ("path", path),
            ("tier_module_lines", interesting),
            ("tier_modules_ungrouped", ungrouped),
        ]
    )


def analyze_unclocked_sequential_sources(path, wrapper_design, wrapper_plan):
    lines = read_text(path).splitlines()
    summary_counts = []
    records = []
    for line in lines:
        stripped = line.strip()
        count_match = GENUS_REPORT_COUNT_RE.match(stripped)
        if count_match and count_match.group("label").strip().lower() == (
            "sequential clock pins without clock waveform"
        ):
            summary_counts.append(int(count_match.group("count")))
        source_match = GENUS_UNCLOCKED_SOURCE_RE.match(stripped)
        if source_match:
            records.append(source_match.groupdict())

    assert len(summary_counts) == 1, "Expected one unclocked sequential-pin summary in {}".format(path)
    expected_count = summary_counts[0]
    assert len(records) == expected_count, (
        "Verbose timing-intent report lists {} unclocked sources but summary reports {}".format(
            len(records), expected_count
        )
    )

    top = wrapper_design.get_module(WRAPPER_TOP_MODULE_NAME)
    constrained_clock_port = wrapper_plan["clock"]["source_clock_port"]
    source_counts = OrderedDict()
    for record in records:
        prefix = WRAPPER_TOP_MODULE_NAME + "/"
        assert record["source"].startswith(prefix), (
            "Unclocked sequential pin is not driven by a wrapper primary input: {}".format(record)
        )
        source_name = record["source"][len(prefix) :]
        base_name = re.sub(r"\[[0-9]+\]$", "", source_name)
        assert base_name in top.ports, "Unclocked source port is absent from wrapper interface: {}".format(record)
        assert top.ports[base_name].direction == "input", (
            "Unclocked sequential source is not a primary input: {}".format(record)
        )
        assert base_name != constrained_clock_port, (
            "Configured source clock lost its waveform during Genus synthesis: {}".format(record)
        )
        source_counts[source_name] = source_counts.get(source_name, 0) + 1

    return OrderedDict(
        [
            ("pin_count", len(records)),
            ("source_port_counts", source_counts),
            ("classification", "original_unconstrained_primary_inputs"),
        ]
    )


def analyze_reports(reports, wrapper_design, wrapper_plan):
    log_path = GENUS_LOG_PREFIX + ".log"
    dangerous = parse_dangerous_messages(
        [log_path, reports["messages"], reports["check_design"]]
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
    post_sdc_clock = assert_sdc_clock_period_ps(GENUS_POST_SDC, SDC_CLK_PERIOD_PS)
    upper_boundary = parse_boundary_report(reports["upper_boundary"])
    bottom_boundary = parse_boundary_report(reports["bottom_boundary"])
    ungroup = parse_ungroup_report(reports["ungroup"])
    assert not dangerous, "Dangerous Genus log/report patterns found: {}".format(dangerous[:10])
    assert upper_boundary["no_boundary_optimization"], "Upper boundary optimization detected or unproven"
    assert bottom_boundary["no_boundary_optimization"], "Bottom boundary optimization detected or unproven"
    assert not ungroup["tier_modules_ungrouped"], "Tier modules appear ungrouped: {}".format(
        ungroup["tier_module_lines"]
    )
    return OrderedDict(
        [
            ("dangerous_messages", dangerous),
            ("dangerous_log_pattern_count", len(dangerous)),
            ("post_sdc_clock", post_sdc_clock),
            ("unclocked_sequential_sources", unclocked_sources),
            ("upper_boundary", upper_boundary),
            ("bottom_boundary", bottom_boundary),
            ("ungroup", ungroup),
        ]
    )

def collect_reachable_module_names(design, root_module_name, leaf_module_types=None):
    assert root_module_name in design.modules, "Root module not found: {}".format(root_module_name)
    leaf_module_types = set(leaf_module_types or [])
    reachable = OrderedDict()

    def visit(module_name):
        if module_name in reachable:
            return
        reachable[module_name] = True
        module = design.get_module(module_name)
        for cell in module.cells.values():
            if cell.type in design.modules and cell.type not in leaf_module_types:
                visit(cell.type)

    visit(root_module_name)
    return list(reachable.keys())
