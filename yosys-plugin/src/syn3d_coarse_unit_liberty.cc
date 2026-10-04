/* Generates coarse-unit Liberty definitions from timing models and physical area data. */

#include "syn3d_coarse_unit_libs.h"

#include <algorithm>
#include <cmath>
#include <cctype>
#include <fstream>
#include <iomanip>
#include <set>
#include <sstream>
#include <string>
#include <vector>

YOSYS_NAMESPACE_BEGIN

namespace Syn3dCoarseUnitLibs {

enum class SequentialInputRole {
    Clock,
    Timed,
    AsyncControl,
};

static std::string uppercase(std::string s)
{
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return std::toupper(c); });
    return s;
}

static std::string format_float(double value, int precision = 3)
{
    std::ostringstream oss;
    oss << std::fixed << std::setprecision(precision) << value;
    return oss.str();
}

static std::string liberty_bit_name(const std::string &name, int bit)
{
    return name + "[" + std::to_string(bit) + "]";
}

static void validate_scalar_constraint_units(const ScalarConstraintUnits &units,
                                             const std::string &context)
{
    if (!std::isfinite(units.setup_rise) || !std::isfinite(units.setup_fall) ||
        !std::isfinite(units.hold_rise) || !std::isfinite(units.hold_fall)) {
        log_error("syn3d_write_coarse_unit_libs: %s has non-finite scalar constraint unit(s)\n",
                  context.c_str());
    }
    if (units.precision < 0) {
        log_error("syn3d_write_coarse_unit_libs: %s has invalid scalar constraint precision %d\n",
                  context.c_str(), units.precision);
    }
}

static bool is_sequential_output_name(const std::string &name)
{
    return uppercase(name) == "Q";
}

static bool is_clock_port_name(const std::string &name)
{
    return uppercase(name) == "CLK";
}

static bool is_async_control_name(const std::string &name)
{
    std::string upper = uppercase(name);
    static const std::set<std::string> async_names = {
        "ARST", "ARESET", "ARESETN", "ARSTN", "RESET", "RESETN", "RST",
        "RSTN", "SET", "SETN", "PRE", "PREN", "CLR", "CLRN", "RN", "SN"
    };
    return async_names.count(upper) != 0;
}

static bool is_sequential_macro_by_ports(const std::vector<PortInfo> &input_ports)
{
    for (const auto &ip : input_ports)
        if (is_clock_port_name(ip.name))
            return true;
    return false;
}

static SequentialInputRole classify_sequential_input_role_name(const std::string &name)
{
    if (is_clock_port_name(name))
        return SequentialInputRole::Clock;
    if (is_async_control_name(name))
        return SequentialInputRole::AsyncControl;
    return SequentialInputRole::Timed;
}

void collect_port_lists(Module *mod,
                               std::vector<PortInfo> &input_ports,
                               std::vector<PortInfo> &output_ports,
                               std::set<int> &bus_widths)
{
    for (auto port_name : mod->ports) {
        Wire *w = mod->wire(port_name);
        std::string pname = RTLIL::unescape_id(port_name);
        PortInfo pi{pname, GetSize(w), w->port_output, w->port_input};
        if (pi.width > 1)
            bus_widths.insert(pi.width);
        if (pi.is_input)
            input_ports.push_back(pi);
        if (pi.is_output)
            output_ports.push_back(pi);
    }
}

SequentialCellModel analyze_actual_sequential_model(const std::string &macro_name,
                                                           const std::vector<PortInfo> &input_ports,
                                                           const std::vector<PortInfo> &output_ports)
{
    SequentialCellModel model;
    if (!is_sequential_macro_by_ports(input_ports))
        return model;

    model.enabled = true;

    bool saw_clock = false;
    for (const auto &ip : input_ports) {
        SequentialInputRole role = classify_sequential_input_role_name(ip.name);
        if (role != SequentialInputRole::Clock)
            continue;
        if (saw_clock) {
            log_error("syn3d_write_coarse_unit_libs: sequential macro %s has multiple CLK ports\n",
                      macro_name.c_str());
        }
        if (ip.width != 1) {
            log_error("syn3d_write_coarse_unit_libs: sequential macro %s has non-scalar clock port %s[%d]\n",
                      macro_name.c_str(), ip.name.c_str(), ip.width);
        }
        model.clock_port = ip.name;
        saw_clock = true;
    }

    if (!saw_clock) {
        log_error("syn3d_write_coarse_unit_libs: sequential macro %s has no CLK input\n",
                  macro_name.c_str());
    }

    bool saw_q_output = false;
    for (const auto &op : output_ports) {
        if (!is_sequential_output_name(op.name)) {
            log_error("syn3d_write_coarse_unit_libs: sequential macro %s has unsupported output %s; only Q is supported\n",
                      macro_name.c_str(), op.name.c_str());
        }
        saw_q_output = true;
    }

    if (!saw_q_output) {
        log_error("syn3d_write_coarse_unit_libs: sequential macro %s has no Q output\n",
                  macro_name.c_str());
    }

    return model;
}


namespace {

struct LibertyWriter {
    static constexpr const char *kCombDelayTemplateName = "delay_template_comb_7x7";
    static constexpr const char *kSeqDelayTemplateName = "delay_template_seq_7x7";
    void emit_scalar_constraint_table(std::ofstream &f,
                                      const std::string &indent,
                                      const std::string &table_name,
                                      double value,
                                      int precision)
    {
        f << indent << table_name << "(scalar) {\n";
        f << indent << "  values (\"" << format_float(value, precision) << "\");\n";
        f << indent << "}\n";
    }

    void emit_table2d(std::ofstream &f,
                      const std::string &indent,
                      const std::string &table_name,
                      const char *template_name,
                      const TimingTable2D &table,
                      const std::string &context)
    {
        validate_table2d(table, context);
        f << indent << table_name << "(" << template_name << ") {\n";
        f << indent << "  index_1 (\"" << table.index_1_csv << "\");\n";
        f << indent << "  index_2 (\"" << table.index_2_csv << "\");\n";
        f << indent << "  values ( \\\n";
        for (int row = 0; row < table.rows; row++) {
            f << indent << "    \"";
            for (int col = 0; col < table.cols; col++) {
                if (col != 0)
                    f << ", ";
                f << format_float(table.values[row * table.cols + col], table.precision);
            }
            f << "\"";
            if (row + 1 == table.rows)
                f << " );\n";
            else
                f << ", \\\n";
        }
        f << indent << "}\n";
    }

    void emit_library_template(std::ofstream &f,
                               const char *template_name,
                               const TimingTable2D &table,
                               const std::string &context)
    {
        validate_table2d(table, context);
        f << "  lu_table_template(" << template_name << ") {\n";
        f << "    variable_1 : input_net_transition;\n";
        f << "    variable_2 : total_output_net_capacitance;\n";
        f << "    index_1 (\"" << table.index_1_csv << "\");\n";
        f << "    index_2 (\"" << table.index_2_csv << "\");\n";
        f << "  }\n\n";
    }

    void emit_library_templates(std::ofstream &f,
                                const ReferenceTableSet &ref_tables)
    {
        emit_library_template(f, kCombDelayTemplateName,
                              ref_tables.combinational.cell_rise,
                              "combinational library template");
        emit_library_template(f, kSeqDelayTemplateName,
                              ref_tables.sequential_output.cell_rise,
                              "sequential library template");
    }

    void emit_output_timing_table2d(std::ofstream &f,
                                    const std::string &indent,
                                    const std::string &related_pin,
                                    const char *timing_type,
                                    const char *template_name,
                                    const TimingTableBundle &bundle,
                                    const std::string &context,
                                    const char *timing_sense = "non_unate")
    {
        validate_table_bundle(bundle, context);
        f << indent << "timing() {\n";
        f << indent << "  related_pin : \"" << related_pin << "\";\n";
        f << indent << "  timing_sense : " << timing_sense << ";\n";
        f << indent << "  timing_type : " << timing_type << ";\n";
        emit_table2d(f, indent + "  ", "cell_rise", template_name,
                     bundle.cell_rise, context + " cell_rise");
        emit_table2d(f, indent + "  ", "cell_fall", template_name,
                     bundle.cell_fall, context + " cell_fall");
        emit_table2d(f, indent + "  ", "rise_transition", template_name,
                     bundle.rise_transition, context + " rise_transition");
        emit_table2d(f, indent + "  ", "fall_transition", template_name,
                     bundle.fall_transition, context + " fall_transition");
        f << indent << "}\n";
    }

    void emit_combinational_output_function(std::ofstream &f,
                                            const std::string &indent,
                                            const OutputBitTiming &bit_timing,
                                            const std::vector<InputTimingBit> &input_bits)
    {
        if (bit_timing.logic_kind == OutputLogicKind::ConstantZero) {
            f << indent << "function : \"0\";\n";
            return;
        }
        if (bit_timing.logic_kind == OutputLogicKind::ConstantOne) {
            f << indent << "function : \"1\";\n";
            return;
        }

        const OutputTimingArc *direct_arc = nullptr;
        for (const auto &arc : bit_timing.arcs) {
            if (!arc.is_direct)
                continue;
            if (direct_arc != nullptr) {
                log_error("syn3d_write_coarse_unit_libs: multiple direct timing arcs reached Liberty emission\n");
            }
            direct_arc = &arc;
        }
        if (direct_arc == nullptr)
            return;
        if (GetSize(bit_timing.arcs) != 1) {
            log_error("syn3d_write_coarse_unit_libs: direct output also has non-direct timing arcs\n");
        }
        if (direct_arc->input_bit_index < 0 || direct_arc->input_bit_index >= GetSize(input_bits)) {
            log_error("syn3d_write_coarse_unit_libs: invalid direct input bit index %d during Liberty emission\n",
                      direct_arc->input_bit_index);
        }
        f << indent << "function : \""
          << input_bits[direct_arc->input_bit_index].liberty_pin_name
          << "\";\n";
    }

    void emit_constraint_timing(std::ofstream &f,
                                const std::string &indent,
                                const std::string &related_pin,
                                const char *timing_type,
                                double rise_value,
                                double fall_value,
                                int precision,
                                const std::string &context)
    {
        validate_scalar_constraint_units({rise_value, fall_value, rise_value, fall_value, precision}, context);
        f << indent << "timing() {\n";
        f << indent << "  related_pin : \"" << related_pin << "\";\n";
        f << indent << "  timing_type : " << timing_type << ";\n";
        emit_scalar_constraint_table(f, indent + "  ", "rise_constraint", rise_value, precision);
        emit_scalar_constraint_table(f, indent + "  ", "fall_constraint", fall_value, precision);
        f << indent << "}\n";
    }

    void emit_input_pin(std::ofstream &f,
                        const PortInfo &ip,
                        const SequentialCellModel &seq_model,
                        const PassConfig &cfg)
    {
        bool emit_constraints = false;
        SequentialInputRole role = SequentialInputRole::Timed;
        if (seq_model.enabled) {
            role = classify_sequential_input_role_name(ip.name);
            emit_constraints = (role == SequentialInputRole::Timed);
            if (role == SequentialInputRole::Clock && ip.width != 1) {
                log_error("syn3d_write_coarse_unit_libs: sequential macro clock port %s must be scalar\n",
                          ip.name.c_str());
            }
        }

        if (ip.width == 1) {
            f << "    pin(" << ip.name << ") {\n";
            f << "      direction : input;\n";
            if (seq_model.enabled && role == SequentialInputRole::Clock)
                f << "      clock : true;\n";
            f << "      capacitance : 1.0;\n";
            if (emit_constraints) {
                emit_constraint_timing(f, "      ", seq_model.clock_port, "setup_rising",
                                       cfg.sequential_constraint_timing.setup_rise,
                                       cfg.sequential_constraint_timing.setup_fall,
                                       cfg.sequential_constraint_timing.precision,
                                       "sequential setup scalar timing");
                emit_constraint_timing(f, "      ", seq_model.clock_port, "hold_rising",
                                       cfg.sequential_constraint_timing.hold_rise,
                                       cfg.sequential_constraint_timing.hold_fall,
                                       cfg.sequential_constraint_timing.precision,
                                       "sequential hold scalar timing");
            }
            f << "    }\n";
            return;
        }

        f << "    bus(" << ip.name << ") {\n";
        f << "      bus_type : bus_" << ip.width << ";\n";
        f << "      direction : input;\n";
        for (int i = 0; i < ip.width; i++) {
            f << "      pin(" << liberty_bit_name(ip.name, i) << ") {\n";
            f << "        capacitance : 1.0;\n";
            if (emit_constraints) {
                emit_constraint_timing(f, "        ", seq_model.clock_port, "setup_rising",
                                       cfg.sequential_constraint_timing.setup_rise,
                                       cfg.sequential_constraint_timing.setup_fall,
                                       cfg.sequential_constraint_timing.precision,
                                       "sequential setup scalar timing");
                emit_constraint_timing(f, "        ", seq_model.clock_port, "hold_rising",
                                       cfg.sequential_constraint_timing.hold_rise,
                                       cfg.sequential_constraint_timing.hold_fall,
                                       cfg.sequential_constraint_timing.precision,
                                       "sequential hold scalar timing");
            }
            f << "      }\n";
        }
        f << "    }\n";
    }

    void emit_combinational_output_pin(std::ofstream &f,
                                       const PortInfo &op,
                                       int bit,
                                       const OutputBitTiming &bit_timing,
                                       const std::vector<InputTimingBit> &input_bits,
                                       const ReferenceTableSet &ref_tables)
    {
        std::string bit_name = liberty_bit_name(op.name, bit);
        f << "      pin(" << bit_name << ") {\n";
        emit_combinational_output_function(f, "        ", bit_timing, input_bits);
        for (const auto &arc : bit_timing.arcs) {
            if (arc.input_bit_index < 0 || arc.input_bit_index >= GetSize(input_bits)) {
                log_error("syn3d_write_coarse_unit_libs: invalid input bit index %d for output %s[%d]\n",
                          arc.input_bit_index, op.name.c_str(), bit);
            }
            TimingTableBundle scaled = scale_timing_bundle(ref_tables.combinational,
                                                           arc.units,
                                                           ref_tables.reference_slew,
                                                           ref_tables.reference_load,
                                                           std::string("combinational output ")
                                                               + op.name + "[" + std::to_string(bit) + "]");
            emit_output_timing_table2d(f, "        ", input_bits[arc.input_bit_index].liberty_pin_name,
                                       "combinational", kCombDelayTemplateName,
                                       scaled,
                                       "combinational timing",
                                       arc.is_direct ? "positive_unate" : "non_unate");
        }
        f << "      }\n";
    }

    void emit_output_pin(std::ofstream &f,
                         const PortInfo &op,
                         const SequentialCellModel &seq_model,
                         const TimingModel *comb_model,
                         const ReferenceTableSet &ref_tables)
    {
        if (seq_model.enabled) {
            if (op.width == 1) {
                f << "    pin(" << op.name << ") {\n";
                f << "      direction : output;\n";
                emit_output_timing_table2d(f, "      ", seq_model.clock_port, "rising_edge",
                                           kSeqDelayTemplateName,
                                           ref_tables.sequential_output,
                                           "sequential output timing");
                f << "    }\n";
                return;
            }

            f << "    bus(" << op.name << ") {\n";
            f << "      bus_type : bus_" << op.width << ";\n";
            f << "      direction : output;\n";
            for (int i = 0; i < op.width; i++) {
                f << "      pin(" << liberty_bit_name(op.name, i) << ") {\n";
                emit_output_timing_table2d(f, "        ", seq_model.clock_port, "rising_edge",
                                           kSeqDelayTemplateName,
                                           ref_tables.sequential_output,
                                           "sequential output timing");
                f << "      }\n";
            }
            f << "    }\n";
            return;
        }

        if (comb_model == nullptr)
            log_error("syn3d_write_coarse_unit_libs: missing combinational timing model for output %s\n",
                      op.name.c_str());
        auto it = comb_model->output_timings.find(op.name);
        if (it == comb_model->output_timings.end())
            log_error("syn3d_write_coarse_unit_libs: no timing entry recorded for output %s\n",
                      op.name.c_str());
        const auto &per_bit = it->second;
        if (GetSize(per_bit) != op.width)
            log_error("syn3d_write_coarse_unit_libs: output width mismatch for %s\n", op.name.c_str());

        if (op.width == 1) {
            f << "    pin(" << op.name << ") {\n";
            f << "      direction : output;\n";
            emit_combinational_output_function(f, "      ", per_bit[0], comb_model->input_bits);
            for (const auto &arc : per_bit[0].arcs) {
                if (arc.input_bit_index < 0 || arc.input_bit_index >= GetSize(comb_model->input_bits)) {
                    log_error("syn3d_write_coarse_unit_libs: invalid input bit index %d for output %s\n",
                              arc.input_bit_index, op.name.c_str());
                }
                TimingTableBundle scaled = scale_timing_bundle(ref_tables.combinational,
                                                               arc.units,
                                                               ref_tables.reference_slew,
                                                               ref_tables.reference_load,
                                                               std::string("combinational output ") + op.name);
                emit_output_timing_table2d(f, "      ", comb_model->input_bits[arc.input_bit_index].liberty_pin_name,
                                           "combinational", kCombDelayTemplateName,
                                           scaled,
                                           "combinational timing",
                                           arc.is_direct ? "positive_unate" : "non_unate");
            }
            f << "    }\n";
            return;
        }

        f << "    bus(" << op.name << ") {\n";
        f << "      bus_type : bus_" << op.width << ";\n";
        f << "      direction : output;\n";
        for (int bit = 0; bit < op.width; bit++)
            emit_combinational_output_pin(f, op, bit, per_bit[bit], comb_model->input_bits,
                                          ref_tables);
        f << "    }\n";
    }

    void emit_library_preamble(std::ofstream &f,
                               const std::string &library_name,
                               const PassConfig &cfg,
                               const std::set<int> &bus_widths)
    {
        const GenericLibParams &p = cfg.generic_lib;
        const ReferenceTableSet &ref_tables = lookup_reference_table_set(cfg);
        const LibraryThresholdParams &thresholds = ref_tables.thresholds;

        f << "library(" << library_name << ") {\n";
        f << "  technology (cmos);\n";
        f << "  delay_model : table_lookup;\n";
        f << "  time_unit : \"" << p.time_unit << "\";\n";
        f << "  voltage_unit : \"1V\";\n";
        f << "  current_unit : \"" << p.current_unit << "\";\n";
        f << "  leakage_power_unit : \"" << p.leakage_unit << "\";\n";
        f << "  nom_process : 1;\n";
        f << "  nom_temperature : " << p.nom_temperature << ";\n";
        f << "  nom_voltage : " << p.nom_voltage << ";\n";
        f << "  capacitive_load_unit (1, " << p.cap_unit << ");\n";
        f << "  pulling_resistance_unit : \"1kohm\";\n\n";

        f << "  operating_conditions(tt_default) {\n";
        f << "    process : 1;\n";
        f << "    temperature : " << p.nom_temperature << ";\n";
        f << "    voltage : " << p.nom_voltage << ";\n";
        f << "    tree_type : balanced_tree;\n";
        f << "  }\n\n";

        f << "  default_cell_leakage_power : 0.0;\n";
        f << "  default_fanout_load : 1;\n";
        f << "  default_inout_pin_cap : 0.0;\n";
        f << "  default_input_pin_cap : 0.0;\n";
        f << "  default_output_pin_cap : 0.0;\n";
        f << "  default_max_transition : " << p.default_max_transition << ";\n";
        f << "  default_operating_conditions : tt_default;\n";
        f << "  default_leakage_power_density : 0.0;\n\n";

        f << "  slew_derate_from_library : 1.000;\n";
        f << "  slew_lower_threshold_pct_fall : "
          << format_float(thresholds.slew_lower_threshold_pct_fall, thresholds.precision) << ";\n";
        f << "  slew_upper_threshold_pct_fall : "
          << format_float(thresholds.slew_upper_threshold_pct_fall, thresholds.precision) << ";\n";
        f << "  slew_lower_threshold_pct_rise : "
          << format_float(thresholds.slew_lower_threshold_pct_rise, thresholds.precision) << ";\n";
        f << "  slew_upper_threshold_pct_rise : "
          << format_float(thresholds.slew_upper_threshold_pct_rise, thresholds.precision) << ";\n";
        f << "  input_threshold_pct_fall : "
          << format_float(thresholds.input_threshold_pct_fall, thresholds.precision) << ";\n";
        f << "  input_threshold_pct_rise : "
          << format_float(thresholds.input_threshold_pct_rise, thresholds.precision) << ";\n";
        f << "  output_threshold_pct_fall : "
          << format_float(thresholds.output_threshold_pct_fall, thresholds.precision) << ";\n";
        f << "  output_threshold_pct_rise : "
          << format_float(thresholds.output_threshold_pct_rise, thresholds.precision) << ";\n\n";

        for (int bw : bus_widths) {
            f << "  type (bus_" << bw << ") {\n";
            f << "    base_type : array;\n";
            f << "    data_type : bit;\n";
            f << "    bit_width : " << bw << ";\n";
            f << "    bit_from  : " << (bw - 1) << ";\n";
            f << "    bit_to    : 0;\n";
            f << "    downto    : true;\n";
            f << "  }\n\n";
        }

        emit_library_templates(f, ref_tables);
    }

    void emit_library_cell(std::ofstream &f,
                           const std::string &cell_name,
                           Module *mod,
                           const SequentialCellModel &seq_model,
                           double area,
                           const PassConfig &cfg,
                           const TimingModel *comb_model)
    {
        std::vector<PortInfo> input_ports, output_ports;
        std::set<int> unused_bus_widths;
        collect_port_lists(mod, input_ports, output_ports, unused_bus_widths);
        const ReferenceTableSet &ref_tables = lookup_reference_table_set(cfg);

        f << "  cell(" << cell_name << ") {\n";
        f << "    area : " << area << ";\n";
        f << "    cell_leakage_power : 0.0;\n\n";

        for (const auto &ip : input_ports)
            emit_input_pin(f, ip, seq_model, cfg);

        for (const auto &op : output_ports)
            emit_output_pin(f, op, seq_model, comb_model, ref_tables);

        f << "  }\n\n";
    }

    void write_lib(const std::string &path,
                   const std::string &library_name,
                   const std::string &cell_name,
                   Module *mod,
                   const SequentialCellModel &seq_model,
                   double area,
                   const PassConfig &cfg,
                   const TimingModel *comb_model)
    {
        std::ofstream f(path);
        if (f.is_open() == false)
            log_error("syn3d_write_coarse_unit_libs: cannot open %s\n", path.c_str());

        std::vector<PortInfo> input_ports, output_ports;
        std::set<int> bus_widths;
        collect_port_lists(mod, input_ports, output_ports, bus_widths);
        emit_library_preamble(f, library_name, cfg, bus_widths);
        emit_library_cell(f, cell_name, mod, seq_model, area, cfg, comb_model);
        f << "}\n";
    }


};

}  // namespace

void emit_library_preamble(std::ofstream &stream,
                           const std::string &library_name,
                           const PassConfig &cfg,
                           const std::set<int> &bus_widths)
{
    LibertyWriter writer;
    writer.emit_library_preamble(stream, library_name, cfg, bus_widths);
}

void emit_library_cell(std::ofstream &stream,
                       const std::string &cell_name,
                       RTLIL::Module *module,
                       const SequentialCellModel &sequential_model,
                       double area,
                       const PassConfig &cfg,
                       const TimingModel *combinational_model)
{
    LibertyWriter writer;
    writer.emit_library_cell(stream, cell_name, module, sequential_model,
                             area, cfg, combinational_model);
}

void write_lib(const std::string &path,
               const std::string &library_name,
               const std::string &cell_name,
               RTLIL::Module *module,
               const SequentialCellModel &sequential_model,
               double area,
               const PassConfig &cfg,
               const TimingModel *combinational_model)
{
    LibertyWriter writer;
    writer.write_lib(path, library_name, cell_name, module, sequential_model,
                     area, cfg, combinational_model);
}

}  // namespace Syn3dCoarseUnitLibs

YOSYS_NAMESPACE_END
