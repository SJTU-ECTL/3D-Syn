/* Shared data structures and interfaces for timing analysis, Liberty generation, and pass orchestration. */

#ifndef SYN3D_COARSE_UNIT_LIBS_H
#define SYN3D_COARSE_UNIT_LIBS_H

#include "kernel/yosys.h"

#include <fstream>
#include <map>
#include <set>
#include <string>
#include <vector>

YOSYS_NAMESPACE_BEGIN

namespace Syn3dCoarseUnitLibs {

struct GenericLibParams {
    std::string nom_voltage;
    std::string nom_temperature;
    std::string time_unit;
    std::string cap_unit;
    std::string leakage_unit;
    std::string current_unit;
    std::string default_max_transition;
};

struct ScalarTimingUnits {
    double cell_rise;
    double cell_fall;
    double rise_transition;
    double fall_transition;
    int precision;
};

struct ScalarConstraintUnits {
    double setup_rise;
    double setup_fall;
    double hold_rise;
    double hold_fall;
    int precision;
};

struct TimingTable2D {
    std::vector<double> index_1;
    std::vector<double> index_2;
    std::string index_1_csv;
    std::string index_2_csv;
    std::vector<double> values;
    int rows = 0;
    int cols = 0;
    int precision = 3;
};

struct TimingTableBundle {
    TimingTable2D cell_rise;
    TimingTable2D cell_fall;
    TimingTable2D rise_transition;
    TimingTable2D fall_transition;
};

struct LibraryThresholdParams {
    double slew_lower_threshold_pct_fall = 0.0;
    double slew_upper_threshold_pct_fall = 0.0;
    double slew_lower_threshold_pct_rise = 0.0;
    double slew_upper_threshold_pct_rise = 0.0;
    double input_threshold_pct_fall = 0.0;
    double input_threshold_pct_rise = 0.0;
    double output_threshold_pct_fall = 0.0;
    double output_threshold_pct_rise = 0.0;
    int precision = 3;
};

struct ReferenceTableSet {
    TimingTableBundle combinational;
    TimingTableBundle sequential_output;
    LibraryThresholdParams thresholds;
    double reference_slew = 0.0;
    double reference_load = 0.0;
};

struct PassConfig {
    std::string scenario;
    std::string yosys_bin;
    std::string lib_dir;
    GenericLibParams generic_lib;
    ScalarTimingUnits scalar_timing;
    ScalarTimingUnits sequential_scalar_timing;
    ScalarConstraintUnits sequential_constraint_timing;
    double reference_slew;
    double reference_load;
};

struct PortInfo {
    std::string name;
    int width;
    bool is_output;
    bool is_input;
};

struct SequentialCellModel {
    bool enabled = false;
    RTLIL::IdString rtlil_type;
    std::string clock_port;
};

struct InputTimingBit {
    std::string port_name;
    int bit_index;
    std::string liberty_pin_name;
};

enum class OutputLogicKind {
    Dynamic,
    ConstantZero,
    ConstantOne,
};

struct OutputTimingArc {
    int input_bit_index;
    ScalarTimingUnits units;
    bool is_direct;
};

struct OutputBitTiming {
    OutputLogicKind logic_kind = OutputLogicKind::Dynamic;
    std::vector<OutputTimingArc> arcs;
};

struct TimingModel {
    std::vector<InputTimingBit> input_bits;
    std::map<std::string, std::vector<OutputBitTiming>> output_timings;
};

PassConfig load_pass_config(const std::string &tier);

const ReferenceTableSet &lookup_reference_table_set(const PassConfig &cfg);

void validate_table2d(const TimingTable2D &table,
                      const std::string &context);

void validate_table_bundle(const TimingTableBundle &bundle,
                           const std::string &context);

TimingTableBundle scale_timing_bundle(const TimingTableBundle &base,
                                      const ScalarTimingUnits &target,
                                      double reference_slew,
                                      double reference_load,
                                      const std::string &context);

TimingModel build_timing_model(const std::string &macro_name,
                               const std::string &rtlil_name,
                               const std::string &synth_json_path,
                               const std::vector<PortInfo> &input_ports,
                               const std::vector<PortInfo> &output_ports,
                               const PassConfig &cfg);

void collect_port_lists(RTLIL::Module *module,
                        std::vector<PortInfo> &input_ports,
                        std::vector<PortInfo> &output_ports,
                        std::set<int> &bus_widths);

SequentialCellModel analyze_actual_sequential_model(
    const std::string &macro_name,
    const std::vector<PortInfo> &input_ports,
    const std::vector<PortInfo> &output_ports);

void emit_library_preamble(std::ofstream &stream,
                           const std::string &library_name,
                           const PassConfig &cfg,
                           const std::set<int> &bus_widths);

void emit_library_cell(std::ofstream &stream,
                       const std::string &cell_name,
                       RTLIL::Module *module,
                       const SequentialCellModel &sequential_model,
                       double area,
                       const PassConfig &cfg,
                       const TimingModel *combinational_model);

void write_lib(const std::string &path,
               const std::string &library_name,
               const std::string &cell_name,
               RTLIL::Module *module,
               const SequentialCellModel &sequential_model,
               double area,
               const PassConfig &cfg,
               const TimingModel *combinational_model);

}  // namespace Syn3dCoarseUnitLibs

YOSYS_NAMESPACE_END

#endif
