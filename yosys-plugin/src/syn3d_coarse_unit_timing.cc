/* Builds technology- and tier-specific timing models from reference Liberty data and synthesized Yosys JSON. */

#include "syn3d_coarse_unit_libs.h"
#include "yosys_json_toolkit.h"
#include "passes/techmap/libparse.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cctype>
#include <cstdlib>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

YOSYS_NAMESPACE_BEGIN

namespace Syn3dCoarseUnitLibs {

static constexpr ScalarTimingUnits kAsap7ScalarTiming = {
    13.8556,
    9.09450,
    13.6987,
    8.56356,
    5
};

static constexpr ScalarTimingUnits kAsap7SequentialScalarTiming = {
    37.8917,
    36.4610,
    12.6113,
    12.8952,
    5
};

static constexpr ScalarConstraintUnits kAsap7SequentialConstraintTiming = {
    9.91753,
    -0.248072,
    -3.48437,
    12.2836,
    6
};

static constexpr ScalarTimingUnits kNangate45ScalarTiming = {
    0.0141826,
    0.0110663,
    0.00779760,
    0.00652247,
    7
};

static constexpr ScalarTimingUnits kNangate45SequentialScalarTiming = {
    0.0894747,
    0.0834509,
    0.00623822,
    0.00509874,
    7
};

static constexpr ScalarConstraintUnits kNangate45SequentialConstraintTiming = {
    0.0316336,
    0.0379624,
    0.0131348,
    0.00560629,
    7
};

static constexpr int kNegDepth = -1000000000;
struct LibertyCellInfo {
    std::map<std::string, std::string> pin_directions;
    std::map<std::string, std::map<std::string, ScalarTimingUnits>> comb_timings;
};

struct LibertyDatabase {
    std::map<std::string, LibertyCellInfo> cells;
};

struct SignalDependency {
    YosysJsonToolkit::Bit input_bit;
};

struct SignalDriver {
    std::vector<SignalDependency> deps;
};

struct DepthMemoEntry {
    bool done = false;
    bool visiting = false;
    std::vector<int> values;
};

static std::string trim(const std::string &s)
{
    size_t begin = 0;
    while (begin < s.size() && std::isspace(static_cast<unsigned char>(s[begin])))
        begin++;
    size_t end = s.size();
    while (end > begin && std::isspace(static_cast<unsigned char>(s[end - 1])))
        end--;
    return s.substr(begin, end - begin);
}

static std::string strip_quotes(const std::string &s)
{
    std::string t = trim(s);
    if (t.size() >= 2 && ((t.front() == '"' && t.back() == '"') || (t.front() == '\'' && t.back() == '\'')))
        return t.substr(1, t.size() - 2);
    return t;
}

static std::string liberty_bit_name(const std::string &name, int bit)
{
    return name + "[" + std::to_string(bit) + "]";
}

static void validate_scalar_timing_units(const ScalarTimingUnits &units,
                                         const std::string &context)
{
    if (units.cell_rise < 0.0 || units.cell_fall < 0.0 ||
        units.rise_transition < 0.0 || units.fall_transition < 0.0) {
        log_error("syn3d_write_coarse_unit_libs: %s has negative scalar timing unit(s)\n",
                  context.c_str());
    }
    if (!std::isfinite(units.cell_rise) || !std::isfinite(units.cell_fall) ||
        !std::isfinite(units.rise_transition) || !std::isfinite(units.fall_transition)) {
        log_error("syn3d_write_coarse_unit_libs: %s has non-finite scalar timing unit(s)\n",
                  context.c_str());
    }
    if (units.precision < 0) {
        log_error("syn3d_write_coarse_unit_libs: %s has invalid scalar timing precision %d\n",
                  context.c_str(), units.precision);
    }
}

static PassConfig make_asap7_config(const std::string &scenario,
                                    const std::string &yosys_bin,
                                    const std::string &lib_dir)
{
    return PassConfig{
        scenario,
        yosys_bin,
        lib_dir,
        {"0.77", "0", "1ps", "ff", "1pW", "1mA", "320"},
        kAsap7ScalarTiming,
        kAsap7SequentialScalarTiming,
        kAsap7SequentialConstraintTiming,
        20.0,
        0.72,
    };
}

static PassConfig make_nangate45_config(const std::string &scenario,
                                        const std::string &yosys_bin,
                                        const std::string &lib_dir)
{
    return PassConfig{
        scenario,
        yosys_bin,
        lib_dir,
        {"1.1", "25.000", "1ns", "ff", "1nW", "1mA", "0.198535"},
        kNangate45ScalarTiming,
        kNangate45SequentialScalarTiming,
        kNangate45SequentialConstraintTiming,
        0.0171859,
        0.365616,
    };
}

PassConfig load_pass_config(const std::string &tier)
{
    const char *flow_home_env = getenv("FLOW_HOME");
    if (flow_home_env == nullptr)
        log_error("syn3d_write_coarse_unit_libs: FLOW_HOME is not set\n");

    const char *library_env = getenv("LIBRARY");
    if (library_env == nullptr || std::string(library_env).empty())
        log_error("syn3d_write_coarse_unit_libs: LIBRARY is not set\n");

    std::string flow_home = flow_home_env;
    std::string library = library_env;
    if (tier != "bottom" && tier != "upper")
        log_error("syn3d_write_coarse_unit_libs: unsupported tier %s\n", tier.c_str());

    const char *yosys_env = getenv("YOSYS_EXE");
    if (yosys_env == nullptr || std::string(yosys_env).empty())
        log_error("syn3d_write_coarse_unit_libs: YOSYS_EXE is not set\n");
    std::string yosys_bin = yosys_env;

    if (library == "asap7_3D")
        return make_asap7_config(library, yosys_bin,
                                 flow_home + "/platforms/asap7_3D/lib_" + tier);
    if (library == "nangate45_3D")
        return make_nangate45_config(library, yosys_bin,
                                     flow_home + "/platforms/nangate45_3D/lib_" + tier);
    if (library == "asap7_nangate45_3D") {
        std::string lib_dir = flow_home + "/platforms/asap7_nangate45_3D/lib_" + tier;
        if (tier == "upper")
            return make_asap7_config("asap7_3D", yosys_bin, lib_dir);
        return make_nangate45_config(library, yosys_bin, lib_dir);
    }

    log_error("syn3d_write_coarse_unit_libs: unsupported LIBRARY=%s\n", library.c_str());
    return PassConfig{};
}

static std::vector<InputTimingBit> collect_input_timing_bits(const std::vector<PortInfo> &input_ports)
{
    std::vector<InputTimingBit> input_bits;
    for (const auto &ip : input_ports) {
        for (int bit = 0; bit < ip.width; bit++) {
            input_bits.push_back(InputTimingBit{
                ip.name,
                bit,
                ip.width == 1 ? ip.name : liberty_bit_name(ip.name, bit),
            });
        }
    }
    return input_bits;
}

static std::vector<double> extract_numbers(const std::string &text)
{
    std::vector<double> nums;
    std::string token;
    auto flush = [&]() {
        if (token.empty())
            return;
        try {
            nums.push_back(std::stod(token));
        } catch (...) {
            log_error("syn3d_write_coarse_unit_libs: failed to parse numeric token '%s' in liberty table\n",
                      token.c_str());
        }
        token.clear();
    };

    for (char c : text) {
        if (std::isdigit(static_cast<unsigned char>(c)) || c == '.' || c == '-' || c == '+' || c == 'e' || c == 'E')
            token += c;
        else
            flush();
    }
    flush();
    return nums;
}

void validate_table2d(const TimingTable2D &table,
                      const std::string &context)
{
    if (table.rows <= 0 || table.cols <= 0)
        log_error("syn3d_write_coarse_unit_libs: %s has invalid table shape %d x %d\n",
                  context.c_str(), table.rows, table.cols);
    if (GetSize(table.index_1) != table.rows || GetSize(table.index_2) != table.cols)
        log_error("syn3d_write_coarse_unit_libs: %s has axis size mismatch\n",
                  context.c_str());
    if (GetSize(table.values) != table.rows * table.cols)
        log_error("syn3d_write_coarse_unit_libs: %s has %d values but expected %d\n",
                  context.c_str(), GetSize(table.values), table.rows * table.cols);
    if (table.index_1_csv.empty() || table.index_2_csv.empty())
        log_error("syn3d_write_coarse_unit_libs: %s has empty index csv\n",
                  context.c_str());
    if (table.precision < 0)
        log_error("syn3d_write_coarse_unit_libs: %s has invalid precision %d\n",
                  context.c_str(), table.precision);
    for (double v : table.values) {
        if (!std::isfinite(v))
            log_error("syn3d_write_coarse_unit_libs: %s has non-finite table value\n",
                      context.c_str());
    }
}

void validate_table_bundle(const TimingTableBundle &bundle,
                           const std::string &context)
{
    validate_table2d(bundle.cell_rise, context + " cell_rise");
    validate_table2d(bundle.cell_fall, context + " cell_fall");
    validate_table2d(bundle.rise_transition, context + " rise_transition");
    validate_table2d(bundle.fall_transition, context + " fall_transition");
}

static void validate_library_thresholds(const LibraryThresholdParams &thresholds,
                                        const std::string &context)
{
    const std::array<double, 8> values = {
        thresholds.slew_lower_threshold_pct_fall,
        thresholds.slew_upper_threshold_pct_fall,
        thresholds.slew_lower_threshold_pct_rise,
        thresholds.slew_upper_threshold_pct_rise,
        thresholds.input_threshold_pct_fall,
        thresholds.input_threshold_pct_rise,
        thresholds.output_threshold_pct_fall,
        thresholds.output_threshold_pct_rise,
    };
    for (double value : values) {
        if (!std::isfinite(value) || value < 0.0 || value > 100.0) {
            log_error("syn3d_write_coarse_unit_libs: %s has invalid threshold value %.6f\n",
                      context.c_str(), value);
        }
    }
    if (thresholds.slew_lower_threshold_pct_fall >= thresholds.slew_upper_threshold_pct_fall ||
        thresholds.slew_lower_threshold_pct_rise >= thresholds.slew_upper_threshold_pct_rise) {
        log_error("syn3d_write_coarse_unit_libs: %s has non-increasing slew threshold window\n",
                  context.c_str());
    }
    if (thresholds.precision < 0) {
        log_error("syn3d_write_coarse_unit_libs: %s has invalid threshold precision %d\n",
                  context.c_str(), thresholds.precision);
    }
}

static TimingTable2D make_table2d(const char *index_1_csv,
                                  const char *index_2_csv,
                                  std::initializer_list<const char *> row_values,
                                  int precision)
{
    TimingTable2D table;
    table.index_1_csv = index_1_csv;
    table.index_2_csv = index_2_csv;
    table.index_1 = extract_numbers(index_1_csv);
    table.index_2 = extract_numbers(index_2_csv);
    table.rows = GetSize(table.index_1);
    table.cols = GetSize(table.index_2);
    table.precision = precision;

    for (const char *row : row_values) {
        std::vector<double> parsed = extract_numbers(row);
        if (GetSize(parsed) != table.cols) {
            log_error("syn3d_write_coarse_unit_libs: malformed row in static table, got %d cols expected %d\n",
                      GetSize(parsed), table.cols);
        }
        table.values.insert(table.values.end(), parsed.begin(), parsed.end());
    }
    if (GetSize(row_values) != table.rows) {
        log_error("syn3d_write_coarse_unit_libs: malformed static table, got %d rows expected %d\n",
                  GetSize(row_values), table.rows);
    }
    validate_table2d(table, "static table");
    return table;
}

static int find_axis_index(const std::vector<double> &axis,
                           double target,
                           const std::string &context)
{
    constexpr double kTol = 1e-9;
    for (int i = 0; i < GetSize(axis); i++) {
        if (std::fabs(axis[i] - target) <= kTol)
            return i;
    }
    log_error("syn3d_write_coarse_unit_libs: reference value %.9f not found in %s\n",
              target, context.c_str());
    return -1;
}

static double reference_point_value(const TimingTable2D &table,
                                    double reference_slew,
                                    double reference_load,
                                    const std::string &context)
{
    validate_table2d(table, context);
    int row = find_axis_index(table.index_1, reference_slew, context + " index_1");
    int col = find_axis_index(table.index_2, reference_load, context + " index_2");
    return table.values[row * table.cols + col];
}

static void validate_reference_point(const TimingTableBundle &bundle,
                                     const ScalarTimingUnits &scalar,
                                     double reference_slew,
                                     double reference_load,
                                     const std::string &context)
{
    auto check_close = [&](double actual, double expected, const std::string &name) {
        if (std::fabs(actual - expected) > 1e-4) {
            log_error("syn3d_write_coarse_unit_libs: %s %s mismatch at reference point: actual=%.9f expected=%.9f\n",
                      context.c_str(), name.c_str(), actual, expected);
        }
    };

    check_close(reference_point_value(bundle.cell_rise, reference_slew, reference_load, context + " cell_rise"),
                scalar.cell_rise, "cell_rise");
    check_close(reference_point_value(bundle.cell_fall, reference_slew, reference_load, context + " cell_fall"),
                scalar.cell_fall, "cell_fall");
    check_close(reference_point_value(bundle.rise_transition, reference_slew, reference_load, context + " rise_transition"),
                scalar.rise_transition, "rise_transition");
    check_close(reference_point_value(bundle.fall_transition, reference_slew, reference_load, context + " fall_transition"),
                scalar.fall_transition, "fall_transition");
}

static TimingTable2D scale_table2d(const TimingTable2D &base,
                                   double target_scalar,
                                   double reference_slew,
                                   double reference_load,
                                   const std::string &context)
{
    double base_reference = reference_point_value(base, reference_slew, reference_load, context);
    if (!std::isfinite(base_reference) || base_reference <= 0.0) {
        log_error("syn3d_write_coarse_unit_libs: %s has invalid base reference value %.9f\n",
                  context.c_str(), base_reference);
    }
    if (!std::isfinite(target_scalar) || target_scalar <= 0.0) {
        log_error("syn3d_write_coarse_unit_libs: %s has invalid target scalar %.9f\n",
                  context.c_str(), target_scalar);
    }

    TimingTable2D scaled = base;
    double scale = target_scalar / base_reference;
    for (double &v : scaled.values)
        v *= scale;
    validate_table2d(scaled, context + " scaled");
    return scaled;
}

TimingTableBundle scale_timing_bundle(const TimingTableBundle &base,
                                             const ScalarTimingUnits &target,
                                             double reference_slew,
                                             double reference_load,
                                             const std::string &context)
{
    validate_scalar_timing_units(target, context + " target");
    TimingTableBundle scaled;
    scaled.cell_rise = scale_table2d(base.cell_rise, target.cell_rise,
                                     reference_slew, reference_load,
                                     context + " cell_rise");
    scaled.cell_fall = scale_table2d(base.cell_fall, target.cell_fall,
                                     reference_slew, reference_load,
                                     context + " cell_fall");
    scaled.rise_transition = scale_table2d(base.rise_transition, target.rise_transition,
                                           reference_slew, reference_load,
                                           context + " rise_transition");
    scaled.fall_transition = scale_table2d(base.fall_transition, target.fall_transition,
                                           reference_slew, reference_load,
                                           context + " fall_transition");
    return scaled;
}

static ReferenceTableSet make_asap7_reference_tables()
{
    static const char *kIndex1 = "5, 10, 20, 40, 80, 160, 320";
    static const char *kIndex2 = "0.72, 1.44, 2.88, 5.76, 11.52, 23.04, 46.08";

    ReferenceTableSet set;
    set.thresholds = {10.0, 90.0, 10.0, 90.0, 50.0, 50.0, 50.0, 50.0, 3};
    set.reference_slew = 20.0;
    set.reference_load = 0.72;

    set.combinational.cell_rise = make_table2d(kIndex1, kIndex2, {
        "8.66548, 10.9732, 15.4335, 24.2069, 41.6758, 76.5718, 146.334",
        "10.4685, 12.7419, 17.215, 26.0176, 43.4849, 78.3874, 148.072",
        "13.8556, 16.4572, 21.0022, 29.6965, 47.0911, 82.1627, 151.757",
        "18.8534, 22.317, 28.0604, 37.3931, 54.8051, 89.4981, 159.282",
        "25.9516, 30.6787, 38.5843, 50.9196, 70.0921, 104.969, 174.323",
        "36.3576, 42.7782, 53.386, 70.5963, 96.3085, 135.786, 205.275",
        "52.278, 60.6338, 75.0943, 98.4039, 133.596, 187.415, 267.249"
    }, 5);
    set.combinational.rise_transition = make_table2d(kIndex1, kIndex2, {
        "10.5436, 15.4423, 25.2582, 44.915, 84.2329, 162.817, 320.03",
        "11.2618, 15.9035, 25.4175, 44.9214, 84.2212, 162.84, 320.017",
        "13.6987, 17.9674, 26.9229, 45.6213, 84.2975, 162.842, 320.015",
        "18.7833, 23.3797, 31.6898, 49.1399, 86.3025, 163.101, 319.992",
        "26.13, 32.0568, 42.2226, 59.1695, 93.9208, 167.381, 320.548",
        "36.5397, 44.7072, 58.2692, 80.2035, 114.292, 183.747, 329.899",
        "52.384, 63.8826, 81.2553, 110.171, 155.52, 225.359, 362.475"
    }, 5);
    set.combinational.cell_fall = make_table2d(kIndex1, kIndex2, {
        "6.39449, 8.10781, 11.43, 18.0605, 31.2872, 57.7001, 110.531",
        "7.54607, 9.34745, 12.7197, 19.354, 32.5751, 59.0063, 111.828",
        "9.0945, 11.2117, 14.9702, 21.737, 34.9538, 61.4035, 114.197",
        "11.1598, 13.8356, 18.3449, 26.0158, 39.6089, 66.1011, 118.97",
        "13.3454, 17.0184, 23.0235, 32.3517, 48.12, 75.4579, 128.432",
        "14.7203, 19.9828, 28.3896, 41.0576, 60.4414, 92.2261, 147.128",
        "13.5748, 21.0571, 33.2356, 50.9876, 77.1709, 116.923, 180.651"
    }, 5);
    set.combinational.fall_transition = make_table2d(kIndex1, kIndex2, {
        "6.60507, 10.1228, 17.2355, 31.4889, 60.0198, 117.093, 231.209",
        "7.11694, 10.5339, 17.4915, 31.5689, 60.0233, 117.087, 231.209",
        "8.56356, 11.916, 18.5184, 32.2871, 60.3132, 117.081, 231.22",
        "10.9057, 14.6606, 21.6439, 34.7862, 61.9674, 117.798, 231.229",
        "15.32, 19.6273, 27.2425, 41.1919, 67.4983, 121.514, 232.812",
        "23.42, 28.4955, 37.4458, 52.8327, 80.8518, 133.1, 240.581",
        "38.6663, 44.7046, 55.454, 73.3706, 104.723, 159.644, 264.159"
    }, 5);

    set.sequential_output.cell_rise = make_table2d(kIndex1, kIndex2, {
        "33.7549, 37.0862, 42.5329, 51.6495, 67.6078, 98.0141, 158.312",
        "35.3278, 38.6193, 44.0722, 53.2093, 69.1807, 99.5755, 159.863",
        "37.8917, 41.225, 46.6707, 55.7888, 71.7458, 102.15, 162.447",
        "41.5975, 44.9369, 50.3807, 59.4977, 75.4545, 105.863, 166.15",
        "46.4214, 49.7606, 55.2055, 64.3239, 80.2705, 110.691, 170.977",
        "52.6827, 56.0193, 61.4665, 70.5944, 86.5556, 116.977, 177.287",
        "60.1412, 63.481, 68.9239, 78.0464, 94.0076, 124.42, 184.711"
    }, 5);
    set.sequential_output.rise_transition = make_table2d(kIndex1, kIndex2, {
        "12.6084, 17.3879, 25.9087, 41.9873, 74.3191, 140.257, 274.328",
        "12.611, 17.3867, 25.9157, 41.9899, 74.3195, 140.238, 274.332",
        "12.6113, 17.3888, 25.9182, 41.9879, 74.3213, 140.252, 274.335",
        "12.6371, 17.3965, 25.9315, 42.0046, 74.3305, 140.272, 274.34",
        "12.6287, 17.413, 26.014, 42.007, 74.3219, 140.248, 274.362",
        "12.6149, 17.3956, 25.9832, 42.0162, 74.7972, 140.358, 274.383",
        "12.6096, 17.4115, 25.9496, 42.1874, 74.3678, 141.433, 274.709"
    }, 5);
    set.sequential_output.cell_fall = make_table2d(kIndex1, kIndex2, {
        "32.221, 35.789, 41.5655, 50.4255, 65.2031, 92.4605, 145.878",
        "33.746, 37.3171, 43.095, 51.954, 66.7169, 93.9906, 147.409",
        "36.461, 40.0331, 45.8049, 54.6668, 69.46, 96.7046, 150.123",
        "40.3932, 43.9543, 49.7248, 58.5865, 73.3693, 100.627, 154.045",
        "45.564, 49.1282, 54.8955, 63.7651, 78.5317, 105.809, 159.231",
        "52.3004, 55.8437, 61.6145, 70.4879, 85.2735, 112.537, 165.966",
        "60.604, 64.1543, 69.9242, 78.7897, 93.5819, 120.861, 174.31"
    }, 5);
    set.sequential_output.fall_transition = make_table2d(kIndex1, kIndex2, {
        "12.8819, 17.3363, 24.751, 38.2219, 64.7143, 118.828, 229.907",
        "12.8815, 17.3387, 24.7481, 38.2341, 64.7216, 118.828, 229.907",
        "12.8952, 17.3497, 24.7635, 38.2407, 64.7506, 118.83, 229.908",
        "12.9391, 17.3865, 24.7984, 38.2676, 64.7472, 118.835, 229.909",
        "12.9647, 17.4038, 24.8545, 38.2899, 64.759, 118.836, 229.943",
        "12.991, 17.4475, 24.8702, 38.2876, 64.9404, 118.849, 229.913",
        "13.201, 17.5822, 24.957, 38.5621, 64.8113, 118.867, 230.977"
    }, 5);

    validate_table_bundle(set.combinational, "asap7 combinational reference");
    validate_table_bundle(set.sequential_output, "asap7 sequential reference");
    validate_library_thresholds(set.thresholds, "asap7 library thresholds");
    validate_reference_point(set.combinational, kAsap7ScalarTiming,
                             set.reference_slew, set.reference_load,
                             "asap7 combinational reference");
    validate_reference_point(set.sequential_output, kAsap7SequentialScalarTiming,
                             set.reference_slew, set.reference_load,
                             "asap7 sequential reference");
    return set;
}

static ReferenceTableSet make_nangate45_reference_tables()
{
    static const char *kIndex1 = "0.00117378,0.00472397,0.0171859,0.0409838,0.0780596,0.130081,0.198535";
    static const char *kIndex2Comb = "0.365616,1.854900,3.709790,7.419590,14.839200,29.678300,59.356700";
    static const char *kIndex2Seq = "0.365616,1.897810,3.795620,7.591250,15.182500,30.365000,60.730000";

    ReferenceTableSet set;
    set.thresholds = {30.0, 70.0, 30.0, 70.0, 50.0, 50.0, 50.0, 50.0, 3};
    set.reference_slew = 0.0171859;
    set.reference_load = 0.365616;

    set.combinational.cell_rise = make_table2d(kIndex1, kIndex2Comb, {
        "0.00743070,0.0112099,0.0157672,0.0247561,0.0426101,0.0782368,0.149445",
        "0.00896317,0.0127084,0.0173000,0.0263569,0.0442815,0.0799642,0.151206",
        "0.0141826,0.0189535,0.0236392,0.0325101,0.0503637,0.0860462,0.157306",
        "0.0198673,0.0266711,0.0336357,0.0448850,0.0628232,0.0981540,0.169220",
        "0.0262799,0.0348883,0.0438330,0.0586475,0.0818511,0.117889,0.188351",
        "0.0334985,0.0438815,0.0546771,0.0727012,0.101569,0.145562,0.216015",
        "0.0415987,0.0537162,0.0663517,0.0874425,0.121509,0.174517,0.253405"
    }, 7);
    set.combinational.rise_transition = make_table2d(kIndex1, kIndex2Comb, {
        "0.00474878,0.00814768,0.0123804,0.0208480,0.0377848,0.0716838,0.139435",
        "0.00475427,0.00814708,0.0123814,0.0208446,0.0377762,0.0716641,0.139428",
        "0.00779760,0.00997800,0.0130179,0.0208500,0.0378031,0.0716776,0.139430",
        "0.0122628,0.0156758,0.0191464,0.0247382,0.0382858,0.0716833,0.139437",
        "0.0178385,0.0220827,0.0266676,0.0342116,0.0458454,0.0726908,0.139429",
        "0.0249336,0.0298045,0.0352101,0.0445099,0.0592803,0.0822832,0.139806",
        "0.0337631,0.0391600,0.0452534,0.0559346,0.0736025,0.100571,0.148264"
    }, 7);
    set.combinational.cell_fall = make_table2d(kIndex1, kIndex2Comb, {
        "0.00683090,0.0100142,0.0139116,0.0216539,0.0370876,0.0679222,0.129575",
        "0.00801780,0.0112344,0.0151696,0.0229509,0.0384185,0.0692724,0.130935",
        "0.0110663,0.0155786,0.0201499,0.0279195,0.0433427,0.0741885,0.135850",
        "0.0127253,0.0190883,0.0256634,0.0364481,0.0533390,0.0839029,0.145413",
        "0.0128360,0.0209729,0.0293903,0.0433808,0.0656528,0.0997348,0.160709",
        "0.0112031,0.0210972,0.0313125,0.0483362,0.0756855,0.118141,0.182879",
        "0.00772654,0.0192683,0.0312780,0.0512843,0.0834863,0.133996,0.210841"
    }, 7);
    set.combinational.fall_transition = make_table2d(kIndex1, kIndex2Comb, {
        "0.00376100,0.00644406,0.00978480,0.0164658,0.0298164,0.0565256,0.109943",
        "0.00375484,0.00644493,0.00978762,0.0164644,0.0298190,0.0565239,0.109935",
        "0.00652247,0.00865044,0.0109402,0.0165623,0.0298184,0.0565245,0.109937",
        "0.0108210,0.0136816,0.0166969,0.0217745,0.0313966,0.0565209,0.109935",
        "0.0165636,0.0201004,0.0238534,0.0302379,0.0406327,0.0597444,0.109934",
        "0.0238974,0.0281323,0.0325754,0.0401642,0.0526647,0.0726144,0.112884",
        "0.0329635,0.0378907,0.0430574,0.0518038,0.0662468,0.0894970,0.126801"
    }, 7);

    set.sequential_output.cell_rise = make_table2d(kIndex1, kIndex2Seq, {
        "0.0828120,0.0863005,0.0900538,0.0980799,0.115488,0.151502,0.224142",
        "0.0843002,0.0877892,0.0915421,0.0995682,0.116978,0.152987,0.225636",
        "0.0894747,0.0929639,0.0967178,0.104737,0.122148,0.158157,0.230802",
        "0.0956394,0.0991291,0.102879,0.110903,0.128306,0.164314,0.236967",
        "0.100388,0.103879,0.107634,0.115656,0.133046,0.169055,0.241706",
        "0.103693,0.107179,0.110951,0.118968,0.136339,0.172342,0.244989",
        "0.105393,0.108883,0.112684,0.120677,0.138011,0.173995,0.246637"
    }, 7);
    set.sequential_output.rise_transition = make_table2d(kIndex1, kIndex2Seq, {
        "0.00623451,0.00895183,0.0123373,0.0202600,0.0373722,0.0719784,0.141272",
        "0.00623484,0.00895495,0.0123329,0.0202604,0.0373685,0.0719779,0.141269",
        "0.00623822,0.00895003,0.0123317,0.0202595,0.0373725,0.0719774,0.141268",
        "0.00624078,0.00895914,0.0123355,0.0202606,0.0373701,0.0719776,0.141269",
        "0.00624423,0.00896103,0.0123353,0.0202645,0.0373728,0.0719740,0.141265",
        "0.00624876,0.00896943,0.0123402,0.0202634,0.0373720,0.0719776,0.141271",
        "0.00627065,0.00898833,0.0123576,0.0202666,0.0373714,0.0719777,0.141271"
    }, 7);
    set.sequential_output.cell_fall = make_table2d(kIndex1, kIndex2Seq, {
        "0.0766596,0.0798075,0.0830620,0.0887201,0.0987256,0.117420,0.154208",
        "0.0781451,0.0812961,0.0845506,0.0902064,0.100214,0.118901,0.155689",
        "0.0834509,0.0865996,0.0898538,0.0955104,0.105520,0.124208,0.161007",
        "0.0898134,0.0929656,0.0962190,0.101880,0.111891,0.130580,0.167368",
        "0.0948029,0.0979521,0.101199,0.106852,0.116848,0.135526,0.172330",
        "0.0980374,0.101184,0.104444,0.110089,0.120091,0.138781,0.175556",
        "0.0991226,0.102262,0.105515,0.111166,0.121175,0.139862,0.176647"
    }, 7);
    set.sequential_output.fall_transition = make_table2d(kIndex1, kIndex2Seq, {
        "0.00509944,0.00667713,0.00843811,0.0118927,0.0190266,0.0342192,0.0659466",
        "0.00509868,0.00668041,0.00844158,0.0118927,0.0190271,0.0342175,0.0659329",
        "0.00509874,0.00668007,0.00844172,0.0118938,0.0190260,0.0342101,0.0659479",
        "0.00510320,0.00667800,0.00844104,0.0118947,0.0190254,0.0342112,0.0659275",
        "0.00510062,0.00667299,0.00843851,0.0118941,0.0190265,0.0342181,0.0659411",
        "0.00510293,0.00667931,0.00844589,0.0118941,0.0190310,0.0342213,0.0659159",
        "0.00510747,0.00668127,0.00844377,0.0118974,0.0190248,0.0342139,0.0659304"
    }, 7);

    validate_table_bundle(set.combinational, "nangate45 combinational reference");
    validate_table_bundle(set.sequential_output, "nangate45 sequential reference");
    validate_library_thresholds(set.thresholds, "nangate45 library thresholds");
    validate_reference_point(set.combinational, kNangate45ScalarTiming,
                             set.reference_slew, set.reference_load,
                             "nangate45 combinational reference");
    validate_reference_point(set.sequential_output, kNangate45SequentialScalarTiming,
                             set.reference_slew, set.reference_load,
                             "nangate45 sequential reference");
    return set;
}

const ReferenceTableSet &lookup_reference_table_set(const PassConfig &cfg)
{
    static const ReferenceTableSet asap7 = make_asap7_reference_tables();
    static const ReferenceTableSet nangate45 = make_nangate45_reference_tables();

    if (cfg.scenario == "asap7_3D")
        return asap7;
    if (cfg.scenario == "nangate45_3D" || cfg.scenario == "asap7_nangate45_3D")
        return nangate45;

    log_error("syn3d_write_coarse_unit_libs: no reference tables for scenario %s\n",
              cfg.scenario.c_str());
    return asap7;
}

static double interpolate_1d(const std::vector<double> &axis,
                             const std::vector<double> &values,
                             double x,
                             const std::string &context)
{
    if (axis.size() != values.size() || axis.empty())
        log_error("syn3d_write_coarse_unit_libs: malformed 1D liberty table in %s\n", context.c_str());
    if (axis.size() == 1)
        return values[0];

    int lo = 0;
    int hi = 1;
    if (x <= axis.front()) {
        lo = 0;
        hi = 1;
    } else if (x >= axis.back()) {
        lo = GetSize(axis) - 2;
        hi = GetSize(axis) - 1;
    } else {
        hi = int(std::upper_bound(axis.begin(), axis.end(), x) - axis.begin());
        lo = hi - 1;
    }

    double dx = axis[hi] - axis[lo];
    if (dx == 0.0)
        return values[lo];
    double t = (x - axis[lo]) / dx;
    return values[lo] + (values[hi] - values[lo]) * t;
}

static double lookup_table_value(const LibertyAst *table_ast,
                                 double slew,
                                 double load,
                                 const std::string &context)
{
    const LibertyAst *values_ast = table_ast->find("values");
    if (values_ast == nullptr)
        log_error("syn3d_write_coarse_unit_libs: liberty table in %s has no values()\n", context.c_str());

    std::vector<double> values = extract_numbers(values_ast->value);
    const LibertyAst *index1_ast = table_ast->find("index_1");
    const LibertyAst *index2_ast = table_ast->find("index_2");
    std::vector<double> index1 = index1_ast ? extract_numbers(index1_ast->value) : std::vector<double>{};
    std::vector<double> index2 = index2_ast ? extract_numbers(index2_ast->value) : std::vector<double>{};

    if (index1.empty() && index2.empty()) {
        if (GetSize(values) != 1)
            log_error("syn3d_write_coarse_unit_libs: scalar liberty table in %s must contain exactly one value\n",
                      context.c_str());
        return values[0];
    }

    if (index2.empty())
        return interpolate_1d(index1, values, slew, context);

    if (index1.empty())
        log_error("syn3d_write_coarse_unit_libs: liberty table in %s has index_2 but no index_1\n",
                  context.c_str());

    int rows = GetSize(index1);
    int cols = GetSize(index2);
    if (GetSize(values) != rows * cols)
        log_error("syn3d_write_coarse_unit_libs: liberty table in %s has %d values but expected %d x %d = %d\n",
                  context.c_str(), GetSize(values), rows, cols, rows * cols);

    auto table_value = [&](int r, int c) -> double {
        return values[r * cols + c];
    };

    if (rows == 1) {
        std::vector<double> row(cols);
        for (int c = 0; c < cols; c++)
            row[c] = table_value(0, c);
        return interpolate_1d(index2, row, load, context);
    }

    if (cols == 1) {
        std::vector<double> col(rows);
        for (int r = 0; r < rows; r++)
            col[r] = table_value(r, 0);
        return interpolate_1d(index1, col, slew, context);
    }

    auto locate = [](const std::vector<double> &axis, double x, int &lo, int &hi, double &t) {
        if (x <= axis.front()) {
            lo = 0;
            hi = 1;
        } else if (x >= axis.back()) {
            lo = GetSize(axis) - 2;
            hi = GetSize(axis) - 1;
        } else {
            hi = int(std::upper_bound(axis.begin(), axis.end(), x) - axis.begin());
            lo = hi - 1;
        }
        double dx = axis[hi] - axis[lo];
        t = dx == 0.0 ? 0.0 : (x - axis[lo]) / dx;
    };

    int r0 = 0, r1 = 1, c0 = 0, c1 = 1;
    double tr = 0.0, tc = 0.0;
    locate(index1, slew, r0, r1, tr);
    locate(index2, load, c0, c1, tc);

    double v00 = table_value(r0, c0);
    double v01 = table_value(r0, c1);
    double v10 = table_value(r1, c0);
    double v11 = table_value(r1, c1);
    double vr0 = v00 + (v01 - v00) * tc;
    double vr1 = v10 + (v11 - v10) * tc;
    return vr0 + (vr1 - vr0) * tr;
}

static ScalarTimingUnits lookup_comb_timing_units(const LibertyAst *timing_ast,
                                                  const PassConfig &cfg,
                                                  const std::string &context)
{
    const LibertyAst *cell_rise_ast = timing_ast->find("cell_rise");
    const LibertyAst *cell_fall_ast = timing_ast->find("cell_fall");
    const LibertyAst *rise_transition_ast = timing_ast->find("rise_transition");
    const LibertyAst *fall_transition_ast = timing_ast->find("fall_transition");
    if (cell_rise_ast == nullptr || cell_fall_ast == nullptr ||
        rise_transition_ast == nullptr || fall_transition_ast == nullptr) {
        log_error("syn3d_write_coarse_unit_libs: incomplete combinational timing arc in %s\n",
                  context.c_str());
    }

    return ScalarTimingUnits{
        lookup_table_value(cell_rise_ast, cfg.reference_slew, cfg.reference_load, context + " cell_rise"),
        lookup_table_value(cell_fall_ast, cfg.reference_slew, cfg.reference_load, context + " cell_fall"),
        lookup_table_value(rise_transition_ast, cfg.reference_slew, cfg.reference_load, context + " rise_transition"),
        lookup_table_value(fall_transition_ast, cfg.reference_slew, cfg.reference_load, context + " fall_transition"),
        cfg.scalar_timing.precision,
    };
}

[[maybe_unused]] static LibertyDatabase build_liberty_database(const std::vector<std::string> &lib_files,
                                                               const PassConfig &cfg)
{
    LibertyDatabase db;
    for (const auto &lib_path : lib_files) {
        std::ifstream f(lib_path);
        if (f.is_open() == false)
            log_error("syn3d_write_coarse_unit_libs: cannot open liberty file %s\n", lib_path.c_str());

        LibertyParser parser(f, lib_path);
        const LibertyAst *ast = parser.ast;
        if (ast == nullptr || ast->id != "library")
            log_error("syn3d_write_coarse_unit_libs: top-level entity in %s is not library()\n",
                      lib_path.c_str());

        for (const LibertyAst *cell_ast : ast->children) {
            if (cell_ast->id != "cell" || GetSize(cell_ast->args) != 1)
                continue;

            std::string cell_name = strip_quotes(cell_ast->args[0]);
            LibertyCellInfo &info = db.cells[cell_name];

            for (const LibertyAst *pin_ast : cell_ast->children) {
                if (pin_ast->id != "pin" || GetSize(pin_ast->args) != 1)
                    continue;

                std::string pin_name = strip_quotes(pin_ast->args[0]);
                const LibertyAst *direction_ast = pin_ast->find("direction");
                if (direction_ast == nullptr)
                    continue;
                std::string direction = strip_quotes(direction_ast->value);
                info.pin_directions[pin_name] = direction;

                if (direction != "output")
                    continue;

                for (const LibertyAst *timing_ast : pin_ast->children) {
                    if (timing_ast->id != "timing")
                        continue;
                    const LibertyAst *related_pin_ast = timing_ast->find("related_pin");
                    if (related_pin_ast == nullptr)
                        continue;
                    if (timing_ast->find("cell_rise") == nullptr || timing_ast->find("cell_fall") == nullptr ||
                        timing_ast->find("rise_transition") == nullptr || timing_ast->find("fall_transition") == nullptr)
                        continue;

                    std::string related_pin = strip_quotes(related_pin_ast->value);
                    if (related_pin.find_first_of(" ,") != std::string::npos) {
                        log_error("syn3d_write_coarse_unit_libs: unsupported related_pin '%s' in cell %s from %s\n",
                                  related_pin.c_str(), cell_name.c_str(), lib_path.c_str());
                    }
                    if (info.comb_timings[pin_name].count(related_pin)) {
                        log_error("syn3d_write_coarse_unit_libs: duplicate combinational timing arc %s.%s <- %s in %s\n",
                                  cell_name.c_str(), pin_name.c_str(), related_pin.c_str(), lib_path.c_str());
                    }

                    std::string context = cell_name + "." + pin_name + "<-" + related_pin;
                    info.comb_timings[pin_name][related_pin] = lookup_comb_timing_units(timing_ast, cfg, context);
                }
            }
        }
    }
    return db;
}

class NetlistTimingAnalyzer
{
  public:
    NetlistTimingAnalyzer(const YosysJsonToolkit::Module &module,
                          const std::vector<PortInfo> &input_ports,
                          const std::vector<InputTimingBit> &input_bits,
                          const std::vector<PortInfo> &output_ports,
                          const std::string &context)
        : module_(module), input_ports_(input_ports), input_bits_(input_bits),
          output_ports_(output_ports), context_(context)
    {
        validate_top_ports();
        build_input_sources();
        build_drivers();
    }

    YosysJsonToolkit::Bit output_signal_bit(const std::string &output_name, int bit) const
    {
        const YosysJsonToolkit::Port &port = require_port(output_name, "output");
        if (bit < 0 || bit >= GetSize(port.bits)) {
            log_error("syn3d_write_coarse_unit_libs: output bit %s[%d] is out of range in %s\n",
                      output_name.c_str(), bit, context_.c_str());
        }
        return port.bits[bit];
    }

    bool has_internal_driver(const YosysJsonToolkit::Bit &signal) const
    {
        return drivers_.count(signal) != 0;
    }

    const std::vector<int> &compute_depth(const YosysJsonToolkit::Bit &signal)
    {
        auto &entry = depth_memo_[signal];
        if (entry.done)
            return entry.values;
        if (entry.visiting)
            log_error("syn3d_write_coarse_unit_libs: combinational loop detected while computing depth at bit %s in %s\n",
                      YosysJsonToolkit::bit_to_string(signal).c_str(), context_.c_str());

        entry.visiting = true;
        entry.values.assign(GetSize(input_bits_), kNegDepth);

        auto src_it = input_sources_.find(signal);
        if (src_it != input_sources_.end()) {
            for (int port_idx : src_it->second)
                entry.values[port_idx] = 0;
        }

        auto drv_it = drivers_.find(signal);
        if (drv_it != drivers_.end()) {
            for (const auto &dep : drv_it->second.deps) {
                const auto &pred = compute_depth(dep.input_bit);
                for (int i = 0; i < GetSize(pred); i++) {
                    if (pred[i] == kNegDepth)
                        continue;
                    entry.values[i] = std::max(entry.values[i], pred[i] + 1);
                }
            }
        }

        entry.visiting = false;
        entry.done = true;
        return entry.values;
    }

  private:
    const YosysJsonToolkit::Port &require_port(const std::string &port_name,
                                               const std::string &expected_direction) const
    {
        const YosysJsonToolkit::Port *port = YosysJsonToolkit::find_port(module_, port_name);
        if (port == nullptr) {
            log_error("syn3d_write_coarse_unit_libs: top-level port %s is missing from synthesized JSON netlist in %s\n",
                      port_name.c_str(), context_.c_str());
        }
        if (port->direction != expected_direction) {
            log_error("syn3d_write_coarse_unit_libs: top-level port %s direction mismatch in %s: expected %s, got %s\n",
                      port_name.c_str(), context_.c_str(), expected_direction.c_str(), port->direction.c_str());
        }
        return *port;
    }

    void validate_top_ports() const
    {
        for (const auto &ip : input_ports_) {
            const YosysJsonToolkit::Port &port = require_port(ip.name, "input");
            if (GetSize(port.bits) != ip.width) {
                log_error("syn3d_write_coarse_unit_libs: synthesized JSON width mismatch for input %s in %s: expected %d, got %d\n",
                          ip.name.c_str(), context_.c_str(), ip.width, GetSize(port.bits));
            }
        }
        for (const auto &op : output_ports_) {
            const YosysJsonToolkit::Port &port = require_port(op.name, "output");
            if (GetSize(port.bits) != op.width) {
                log_error("syn3d_write_coarse_unit_libs: synthesized JSON width mismatch for output %s in %s: expected %d, got %d\n",
                          op.name.c_str(), context_.c_str(), op.width, GetSize(port.bits));
            }
        }
    }

    void build_input_sources()
    {
        for (int input_bit_idx = 0; input_bit_idx < GetSize(input_bits_); input_bit_idx++) {
            const auto &input_bit = input_bits_[input_bit_idx];
            const YosysJsonToolkit::Port &port = require_port(input_bit.port_name, "input");
            if (input_bit.bit_index < 0 || input_bit.bit_index >= GetSize(port.bits)) {
                log_error("syn3d_write_coarse_unit_libs: input bit index %d is out of range for %s in %s\n",
                          input_bit.bit_index, input_bit.port_name.c_str(), context_.c_str());
            }

            const YosysJsonToolkit::Bit &sig = port.bits[input_bit.bit_index];
            if (sig.is_const) {
                log_error("syn3d_write_coarse_unit_libs: input port %s[%d] is unexpectedly constant in %s\n",
                          input_bit.port_name.c_str(), input_bit.bit_index, context_.c_str());
            }
            input_sources_[sig].insert(input_bit_idx);
        }
    }

    void build_drivers()
    {
        for (const auto &entry : module_.cells) {
            const YosysJsonToolkit::Cell &cell = entry.second;

            if (cell.port_directions.empty()) {
                log_error("syn3d_write_coarse_unit_libs: synthesized JSON cell %s (%s) in %s has no port_directions; expected read_liberty -lib to provide them\n",
                          cell.name.c_str(), cell.type.c_str(), context_.c_str());
            }
            for (const auto &conn_entry : cell.connections) {
                if (cell.port_directions.count(conn_entry.first) == 0) {
                    log_error("syn3d_write_coarse_unit_libs: synthesized JSON cell %s (%s) in %s is missing direction for connected port %s\n",
                              cell.name.c_str(), cell.type.c_str(), context_.c_str(), conn_entry.first.c_str());
                }
            }
            for (const auto &dir_entry : cell.port_directions) {
                if (cell.connections.count(dir_entry.first) == 0) {
                    log_error("syn3d_write_coarse_unit_libs: synthesized JSON cell %s (%s) in %s has direction metadata for unconnected port %s\n",
                              cell.name.c_str(), cell.type.c_str(), context_.c_str(), dir_entry.first.c_str());
                }
            }

            for (const auto &dir_entry : cell.port_directions) {
                const std::string &output_port = dir_entry.first;
                if (dir_entry.second != "output") {
                    if (dir_entry.second != "input") {
                        log_error("syn3d_write_coarse_unit_libs: unsupported port direction %s on cell %s (%s) in %s\n",
                                  dir_entry.second.c_str(), cell.name.c_str(), cell.type.c_str(), context_.c_str());
                    }
                    continue;
                }

                auto conn_it = cell.connections.find(output_port);
                if (conn_it == cell.connections.end()) {
                    log_error("syn3d_write_coarse_unit_libs: missing connection for output port %s on cell %s (%s) in %s\n",
                              output_port.c_str(), cell.name.c_str(), cell.type.c_str(), context_.c_str());
                }

                const std::vector<YosysJsonToolkit::Bit> &out_bits = conn_it->second;
                if (GetSize(out_bits) != 1 || out_bits[0].is_const) {
                    log_error("syn3d_write_coarse_unit_libs: expected one non-constant output bit on %s.%s in %s, got %s\n",
                              cell.name.c_str(), output_port.c_str(), context_.c_str(),
                              YosysJsonToolkit::bits_to_string(out_bits).c_str());
                }
                const YosysJsonToolkit::Bit &out_sig = out_bits[0];
                if (drivers_.count(out_sig)) {
                    log_error("syn3d_write_coarse_unit_libs: multiple drivers for bit %s in %s\n",
                              YosysJsonToolkit::bit_to_string(out_sig).c_str(), context_.c_str());
                }

                SignalDriver driver;
                std::set<YosysJsonToolkit::Bit> seen_inputs;
                bool has_input_port = false;
                for (const auto &input_dir_entry : cell.port_directions) {
                    const std::string &input_port = input_dir_entry.first;
                    if (input_dir_entry.second != "input")
                        continue;
                    has_input_port = true;

                    auto rel_conn_it = cell.connections.find(input_port);
                    if (rel_conn_it == cell.connections.end()) {
                        log_error("syn3d_write_coarse_unit_libs: missing connection for input port %s on cell %s (%s) in %s\n",
                                  input_port.c_str(), cell.name.c_str(), cell.type.c_str(), context_.c_str());
                    }

                    const std::vector<YosysJsonToolkit::Bit> &in_bits = rel_conn_it->second;
                    if (GetSize(in_bits) != 1) {
                        log_error("syn3d_write_coarse_unit_libs: expected one related input bit on %s.%s in %s, got %s\n",
                                  cell.name.c_str(), input_port.c_str(), context_.c_str(),
                                  YosysJsonToolkit::bits_to_string(in_bits).c_str());
                    }
                    if (in_bits[0].is_const)
                        continue;

                    const YosysJsonToolkit::Bit &dep_sig = in_bits[0];
                    if (!seen_inputs.insert(dep_sig).second)
                        continue;

                    driver.deps.push_back(SignalDependency{dep_sig});
                }

                if (driver.deps.empty() && has_input_port) {
                    log_error("syn3d_write_coarse_unit_libs: output %s of cell %s (%s) in %s has no non-constant timing dependency\n",
                              output_port.c_str(), cell.name.c_str(), cell.type.c_str(), context_.c_str());
                }
                drivers_[out_sig] = driver;
            }
        }
    }

    const YosysJsonToolkit::Module &module_;
    std::vector<PortInfo> input_ports_;
    std::vector<InputTimingBit> input_bits_;
    std::vector<PortInfo> output_ports_;
    std::string context_;
    std::map<YosysJsonToolkit::Bit, std::set<int> > input_sources_;
    std::map<YosysJsonToolkit::Bit, SignalDriver> drivers_;
    std::map<YosysJsonToolkit::Bit, DepthMemoEntry> depth_memo_;
};

static ScalarTimingUnits scale_timing_units(const ScalarTimingUnits &base, int scale)
{
    if (scale <= 0)
        log_error("syn3d_write_coarse_unit_libs: invalid non-positive timing scale %d\n", scale);
    return ScalarTimingUnits{
        base.cell_rise * scale,
        base.cell_fall * scale,
        base.rise_transition * scale,
        base.fall_transition * scale,
        base.precision,
    };
}

static void add_output_timing_arc(OutputBitTiming &bit_timing,
                                  int input_bit_index,
                                  const ScalarTimingUnits &units,
                                  bool is_direct)
{
    bit_timing.arcs.push_back(OutputTimingArc{input_bit_index, units, is_direct});
}

static const YosysJsonToolkit::Module &find_json_module_or_die(const YosysJsonToolkit::Design &json_design,
                                                               const std::string &json_path,
                                                               const std::string &macro_name,
                                                               const std::string &rtlil_name)
{
    if (json_design.modules.empty()) {
        log_error("syn3d_write_coarse_unit_libs: synthesized JSON netlist %s contains no modules\n",
                  json_path.c_str());
    }

    if (json_design.modules.size() == 1)
        return json_design.modules.begin()->second;

    std::vector<std::string> candidates;
    candidates.push_back(macro_name);
    candidates.push_back(rtlil_name);
    candidates.push_back(RTLIL::escape_id(macro_name));
    candidates.push_back(RTLIL::unescape_id(rtlil_name));

    for (const auto &candidate : candidates) {
        if (candidate.empty())
            continue;
        const YosysJsonToolkit::Module *module = YosysJsonToolkit::find_module(json_design, candidate);
        if (module != nullptr)
            return *module;
    }

    log_error("syn3d_write_coarse_unit_libs: cannot resolve top module for macro %s in synthesized JSON netlist %s (modules=%zu)\n",
              macro_name.c_str(), json_path.c_str(), json_design.modules.size());
    return json_design.modules.begin()->second;
}

TimingModel build_timing_model(const std::string &macro_name,
                                      const std::string &rtlil_name,
                                      const std::string &synth_json_path,
                                      const std::vector<PortInfo> &input_ports,
                                      const std::vector<PortInfo> &output_ports,
                                      const PassConfig &cfg)
{
    TimingModel model;
    model.input_bits = collect_input_timing_bits(input_ports);
    for (const auto &op : output_ports) {
        model.output_timings[op.name] = std::vector<OutputBitTiming>(op.width);
    }

    YosysJsonToolkit::Design json_design = YosysJsonToolkit::load_design(synth_json_path);
    const YosysJsonToolkit::Module &json_module = find_json_module_or_die(json_design, synth_json_path,
                                                                          macro_name, rtlil_name);
    NetlistTimingAnalyzer analyzer(json_module, input_ports, model.input_bits, output_ports, macro_name);

    for (const auto &op : output_ports) {
        auto &per_bit = model.output_timings.at(op.name);
        for (int bit = 0; bit < op.width; bit++) {
            YosysJsonToolkit::Bit signal = analyzer.output_signal_bit(op.name, bit);
            if (signal.is_const) {
                if (signal.const_value == '0') {
                    per_bit[bit].logic_kind = OutputLogicKind::ConstantZero;
                } else if (signal.const_value == '1') {
                    per_bit[bit].logic_kind = OutputLogicKind::ConstantOne;
                } else {
                    log_error("syn3d_write_coarse_unit_libs: output bit %s[%d] has unsupported constant %c in macro %s\n",
                              op.name.c_str(), bit, signal.const_value, macro_name.c_str());
                }
                log_warning("syn3d_write_coarse_unit_libs: output bit %s[%d] is tied to constant %c in macro %s; emitting constant function without timing arcs\n",
                            op.name.c_str(), bit, signal.const_value, macro_name.c_str());
                continue;
            }

            const auto &depths = analyzer.compute_depth(signal);
            int direct_source_count = 0;
            for (int depth : depths) {
                if (depth == 0)
                    direct_source_count++;
            }
            if (direct_source_count > 0) {
                if (direct_source_count != 1) {
                    log_error("syn3d_write_coarse_unit_libs: output bit %s[%d] aliases %d input bits in macro %s\n",
                              op.name.c_str(), bit, direct_source_count, macro_name.c_str());
                }
                if (analyzer.has_internal_driver(signal)) {
                    log_error("syn3d_write_coarse_unit_libs: output bit %s[%d] is both an input alias and internally driven in macro %s\n",
                              op.name.c_str(), bit, macro_name.c_str());
                }
            }

            bool saw_path = false;
            for (int i = 0; i < GetSize(depths); i++) {
                if (depths[i] == kNegDepth)
                    continue;
                if (depths[i] < 0) {
                    log_error("syn3d_write_coarse_unit_libs: invalid negative PI->PO depth %d for %s[%d] from input %s in macro %s\n",
                              depths[i], op.name.c_str(), bit, model.input_bits[i].liberty_pin_name.c_str(), macro_name.c_str());
                }
                bool is_direct = depths[i] == 0;
                int effective_depth = is_direct ? 1 : depths[i];
                if (is_direct) {
                    log_warning("syn3d_write_coarse_unit_libs: direct PI->PO path for %s[%d] from input %s in macro %s; using one reference timing stage\n",
                                op.name.c_str(), bit, model.input_bits[i].liberty_pin_name.c_str(), macro_name.c_str());
                }
                add_output_timing_arc(per_bit[bit], i,
                                      scale_timing_units(cfg.scalar_timing, effective_depth),
                                      is_direct);
                saw_path = true;
            }
            if (!saw_path) {
                log_warning("syn3d_write_coarse_unit_libs: no PI->PO path reaches %s[%d] in macro %s; emitting output pin without timing arcs\n",
                            op.name.c_str(), bit, macro_name.c_str());
            }
        }
    }

    return model;
}


}  // namespace Syn3dCoarseUnitLibs

YOSYS_NAMESPACE_END
