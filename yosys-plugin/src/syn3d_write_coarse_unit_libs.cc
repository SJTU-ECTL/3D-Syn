/*
 * Implements generation of 2D, bottom-tier, and upper-tier coarse-unit
 * Liberty views from handoff netlists and GNN area metrics.
 */
 
#include "kernel/yosys.h"
#include "syn3d_coarse_unit_libs.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <fstream>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <vector>

USING_YOSYS_NAMESPACE
PRIVATE_NAMESPACE_BEGIN

using namespace Syn3dCoarseUnitLibs;

static bool ends_with(const std::string &s, const std::string &suffix)
{
    return s.size() >= suffix.size() &&
           s.compare(s.size() - suffix.size(), suffix.size(), suffix) == 0;
}

static bool is_lib_file(const std::string &path)
{
    return ends_with(path, ".lib") || ends_with(path, ".processed.lib");
}

static std::string quote_path(const std::string &path)
{
    return std::string("\"") + path + "\"";
}

static void collect_lib_files_recursive(const std::string &dir,
                                        std::vector<std::string> &out)
{
    DIR *dp = opendir(dir.c_str());
    if (dp == nullptr)
        log_error("syn3d_write_coarse_unit_libs: cannot open library directory %s: %s\n",
                  dir.c_str(), strerror(errno));

    while (dirent *entry = readdir(dp)) {
        std::string name = entry->d_name;
        if (name == "." || name == "..")
            continue;

        std::string path = dir + "/" + name;
        struct stat st;
        if (stat(path.c_str(), &st) == 0) {
            if (S_ISDIR(st.st_mode)) {
                collect_lib_files_recursive(path, out);
                continue;
            }
            if (S_ISREG(st.st_mode) && is_lib_file(path))
                out.push_back(path);
            continue;
        }

        closedir(dp);
        log_error("syn3d_write_coarse_unit_libs: stat failed for %s: %s\n",
                  path.c_str(), strerror(errno));
    }

    closedir(dp);
}

static std::vector<std::string> collect_lib_files(const std::string &dir)
{
    std::vector<std::string> files;
    collect_lib_files_recursive(dir, files);
    std::sort(files.begin(), files.end());
    if (files.empty())
        log_error("syn3d_write_coarse_unit_libs: no liberty files found under %s\n",
                  dir.c_str());
    return files;
}

static std::string liberty_read_tcl(const std::vector<std::string> &lib_files)
{
    if (lib_files.empty())
        log_error("syn3d_write_coarse_unit_libs: cannot emit read_liberty commands for empty library list\n");

    std::string script;
    for (const auto &lib : lib_files)
        script += "read_liberty -lib \"" + lib + "\"\n";
    return script;
}

static bool file_exists_nonempty(const std::string &path)
{
    struct stat st;
    return stat(path.c_str(), &st) == 0 && S_ISREG(st.st_mode) && st.st_size > 0;
}

static std::string safe_token(std::string name)
{
    for (char &c : name)
        if (c == '\\' || c == '/')
            c = '_';
    return name;
}

static std::string footprint_metrics_path(const std::string &handoff_dir,
                                          const std::string &tier,
                                          const std::string &macro_name)
{
    return handoff_dir + "/metrics/" + tier + "/" + macro_name
         + "/metrics.json";
}

static std::string proxy_netlist_path(const std::string &handoff_dir,
                                      const std::string &tier,
                                      const std::string &macro_name)
{
    return handoff_dir + "/coarse_units/" + tier + "/" + macro_name + ".v";
}

static double read_footprint_area_from_metrics(const std::string &path,
                                               const std::string &macro_name)
{
    if (!file_exists_nonempty(path))
        log_error("syn3d_write_coarse_unit_libs: missing or empty GNN footprint metrics for %s: %s\n",
                  macro_name.c_str(), path.c_str());

    std::ifstream fp(path.c_str());
    if (!fp.is_open())
        log_error("syn3d_write_coarse_unit_libs: cannot open GNN footprint metrics %s\n",
                  path.c_str());

    std::ostringstream buffer;
    buffer << fp.rdbuf();
    std::string text = buffer.str();
    const std::string key = "\"footprint_area\"";
    size_t key_pos = text.find(key);
    if (key_pos == std::string::npos)
        log_error("syn3d_write_coarse_unit_libs: missing footprint_area for %s in %s\n",
                  macro_name.c_str(), path.c_str());

    size_t colon = text.find(':', key_pos + key.size());
    if (colon == std::string::npos)
        log_error("syn3d_write_coarse_unit_libs: malformed footprint_area entry for %s in %s\n",
                  macro_name.c_str(), path.c_str());

    size_t value_pos = colon + 1;
    while (value_pos < text.size() && std::isspace(static_cast<unsigned char>(text[value_pos])))
        value_pos++;

    errno = 0;
    char *endptr = nullptr;
    const char *start = text.c_str() + value_pos;
    double area = std::strtod(start, &endptr);
    if (errno != 0 || endptr == start)
        log_error("syn3d_write_coarse_unit_libs: invalid footprint_area for %s in %s\n",
                  macro_name.c_str(), path.c_str());
    if (!(area > 0.0) || !std::isfinite(area))
        log_error("syn3d_write_coarse_unit_libs: non-positive footprint_area for %s in %s: %.9f\n",
                  macro_name.c_str(), path.c_str(), area);

    while (*endptr != '\0' && std::isspace(static_cast<unsigned char>(*endptr)))
        endptr++;
    if (*endptr != ',' && *endptr != '}' && *endptr != '\0')
        log_error("syn3d_write_coarse_unit_libs: trailing characters after footprint_area for %s in %s\n",
                  macro_name.c_str(), path.c_str());

    return area;
}

static bool build_proxy_json_from_tier_netlist(const std::string &macro_name,
                                               const std::string &tier,
                                               const std::string &netlist_path,
                                               const std::string &log_dir,
                                               const std::string &out_v_path,
                                               const std::string &out_json_path,
                                               const PassConfig &cfg,
                                               const std::vector<std::string> &libs)
{
    if (!file_exists_nonempty(netlist_path))
        log_error("syn3d_write_coarse_unit_libs: missing or empty %s proxy netlist for %s: %s\n",
                  tier.c_str(), macro_name.c_str(), netlist_path.c_str());

    std::string safe = safe_token(macro_name);
    std::string tcl_path = log_dir + "/" + tier + "_actual_" + safe + ".tcl";
    std::string log_path = log_dir + "/" + tier + "_actual_" + safe + ".log";

    {
        std::ofstream tcl(tcl_path);
        if (tcl.is_open() == false) {
            log_warning("syn3d_write_coarse_unit_libs: cannot create %s\n", tcl_path.c_str());
            return false;
        }

        tcl << liberty_read_tcl(libs);
        tcl << "read_verilog \"" << netlist_path << "\"\n";
        tcl << "hierarchy -check -top " << macro_name << "\n";
        tcl << "write_verilog -noattr -noexpr \"" << out_v_path << "\"\n";
        tcl << "write_json \"" << out_json_path << "\"\n";
    }

    std::string cmd = quote_path(cfg.yosys_bin)
                    + " -s \"" + tcl_path + "\""
                    + " > \"" + log_path + "\" 2>&1";
    int ret = system(cmd.c_str());

    if (ret == 0 && file_exists_nonempty(out_json_path) && file_exists_nonempty(out_v_path)) {
        remove(tcl_path.c_str());
        return true;
    }

    log_warning("syn3d_write_coarse_unit_libs: %s netlist readback failed (exit %d) for %s\n",
                tier.c_str(), ret, macro_name.c_str());
    log_warning("syn3d_write_coarse_unit_libs: see log: %s\n", log_path.c_str());
    remove(tcl_path.c_str());
    return false;
}

struct Syn3dWriteCoarseUnitLibsPass : Pass {

    Syn3dWriteCoarseUnitLibsPass() : Pass("syn3d_write_coarse_unit_libs",
        "generate 2D and tier-specific coarse-unit liberty handoff") {}

    void help() override
    {
        log("\n");
        log("    syn3d_write_coarse_unit_libs\n");
        log("\n");
        log("Reads final footprint metrics and tier-specific coarse-unit netlists from\n");
        log("SYN3D_HANDOFF_DIR, then writes 2D, bottom, and upper bit-level timing\n");
        log("Liberty views under SYN3D_HANDOFF_DIR/lib using per-input path-depth\n");
        log("timing estimation.\n");
        log("\n");
    }

    void execute(std::vector<std::string> args, RTLIL::Design *d) override
    {
        log_header(d, "Executing SYN3D_WRITE_COARSE_UNIT_LIBS pass.\n");
        extra_args(args, 1, d);

        const char *env_handoff = getenv("SYN3D_HANDOFF_DIR");
        if (env_handoff == nullptr || std::string(env_handoff).empty())
            log_error("syn3d_write_coarse_unit_libs: SYN3D_HANDOFF_DIR is not set\n");
        const char *env_res = getenv("RESULTS_DIR");
        if (env_res == nullptr)
            log_error("syn3d_write_coarse_unit_libs: RESULTS_DIR is not set\n");

        std::string handoff_dir = env_handoff;
        std::string results_dir = env_res;
        std::string lib_2d_dir = handoff_dir + "/lib/2d";
        std::string lib_bottom_dir = handoff_dir + "/lib/bottom";
        std::string lib_upper_dir = handoff_dir + "/lib/upper";
        std::string synth_bottom_dir = handoff_dir + "/lib_topology/bottom";
        std::string synth_upper_dir = handoff_dir + "/lib_topology/upper";
        std::string log_dir   = results_dir + "/3dsyn_coarse_unit_lib_logs";

        PassConfig bottom_cfg = load_pass_config("bottom");
        PassConfig upper_cfg = load_pass_config("upper");
        std::vector<std::string> bottom_libs = collect_lib_files(bottom_cfg.lib_dir);
        std::vector<std::string> upper_libs = collect_lib_files(upper_cfg.lib_dir);

        for (auto &dir : {lib_2d_dir, lib_bottom_dir, lib_upper_dir,
                          synth_bottom_dir, synth_upper_dir, log_dir}) {
            if (system(("mkdir -p \"" + dir + "\"").c_str()) != 0)
                log_error("syn3d_write_coarse_unit_libs: cannot create directory %s\n", dir.c_str());
        }

        std::set<int> aggregate_2d_bus_widths;
        for (auto mod : d->modules()) {
            if (mod->get_bool_attribute(ID(syn3d_coarse_unit)) == false)
                continue;
            std::vector<PortInfo> input_ports, output_ports;
            std::set<int> module_bus_widths;
            collect_port_lists(mod, input_ports, output_ports, module_bus_widths);
            aggregate_2d_bus_widths.insert(module_bus_widths.begin(), module_bus_widths.end());
        }

        std::string aggregate_2d_lib_path = lib_2d_dir + "/coarse_units.lib";
        std::ofstream aggregate_2d_lib(aggregate_2d_lib_path);
        if (aggregate_2d_lib.is_open() == false)
            log_error("syn3d_write_coarse_unit_libs: cannot open %s\n", aggregate_2d_lib_path.c_str());
        emit_library_preamble(aggregate_2d_lib, "coarse_units__2d",
                              bottom_cfg, aggregate_2d_bus_widths);

        int total = 0;
        long long total_calc_ms = 0;
        log("syn3d_write_coarse_unit_libs: timing model=per-input path depth\n");
        log("  bottom scenario=%s lib dir=%s libs=%zu\n",
            bottom_cfg.scenario.c_str(), bottom_cfg.lib_dir.c_str(), bottom_libs.size());
        log("  upper scenario=%s lib dir=%s libs=%zu\n",
            upper_cfg.scenario.c_str(), upper_cfg.lib_dir.c_str(), upper_libs.size());

        for (auto mod : d->modules()) {
            if (mod->get_bool_attribute(ID(syn3d_coarse_unit)) == false)
                continue;

            std::string macro_name = RTLIL::unescape_id(mod->name);
            std::string rtlil_name = mod->name.str();
            std::string bottom_synth_v_path = synth_bottom_dir + "/" + macro_name + ".v";
            std::string bottom_synth_json_path = synth_bottom_dir + "/" + macro_name + ".json";
            std::string upper_synth_v_path = synth_upper_dir + "/" + macro_name + ".v";
            std::string upper_synth_json_path = synth_upper_dir + "/" + macro_name + ".json";
            std::string bottom_metrics_path = footprint_metrics_path(handoff_dir, "bottom", macro_name);
            std::string upper_metrics_path = footprint_metrics_path(handoff_dir, "upper", macro_name);
            std::string bottom_v_path = proxy_netlist_path(handoff_dir, "bottom", macro_name);
            std::string upper_v_path = proxy_netlist_path(handoff_dir, "upper", macro_name);

            std::vector<PortInfo> input_ports, output_ports;
            std::set<int> bus_widths;
            collect_port_lists(mod, input_ports, output_ports, bus_widths);
            SequentialCellModel seq_model = analyze_actual_sequential_model(
                macro_name, input_ports, output_ports);
            bool is_seq = seq_model.enabled;

            log("  [%s] reading tier footprint handoff ...\n", macro_name.c_str());
            double bottom_area = read_footprint_area_from_metrics(bottom_metrics_path, macro_name);
            double upper_area = read_footprint_area_from_metrics(upper_metrics_path, macro_name);
            if (!build_proxy_json_from_tier_netlist(macro_name, "bottom", bottom_v_path, log_dir,
                                                    bottom_synth_v_path, bottom_synth_json_path,
                                                    bottom_cfg, bottom_libs)) {
                log_error("syn3d_write_coarse_unit_libs: bottom netlist readback failed for %s\n"
                          "  netlist: %s\n  check log dir: %s\n",
                          macro_name.c_str(), bottom_v_path.c_str(), log_dir.c_str());
            }
            if (!build_proxy_json_from_tier_netlist(macro_name, "upper", upper_v_path, log_dir,
                                                    upper_synth_v_path, upper_synth_json_path,
                                                    upper_cfg, upper_libs)) {
                log_error("syn3d_write_coarse_unit_libs: upper netlist readback failed for %s\n"
                          "  netlist: %s\n  check log dir: %s\n",
                          macro_name.c_str(), upper_v_path.c_str(), log_dir.c_str());
            }

            log("  [%s] bottom/2D footprint area = %.3f um^2; upper footprint area = %.3f um^2\n",
                macro_name.c_str(), bottom_area, upper_area);

            auto calc_begin = std::chrono::steady_clock::now();
            std::unique_ptr<TimingModel> bottom_comb_model;
            std::unique_ptr<TimingModel> upper_comb_model;
            if (!is_seq) {
                bottom_comb_model.reset(new TimingModel(
                    build_timing_model(macro_name, rtlil_name, bottom_synth_json_path,
                                       input_ports, output_ports, bottom_cfg)));
                upper_comb_model.reset(new TimingModel(
                    build_timing_model(macro_name, rtlil_name, upper_synth_json_path,
                                       input_ports, output_ports, upper_cfg)));
            }
            auto calc_end = std::chrono::steady_clock::now();
            auto calc_ms = std::chrono::duration_cast<std::chrono::milliseconds>(calc_end - calc_begin).count();
            total_calc_ms += calc_ms;
            log("  [%s] delay calculation time = %lld ms\n",
                macro_name.c_str(), static_cast<long long>(calc_ms));

            write_lib(lib_2d_dir + "/" + macro_name + ".lib",
                      macro_name + "__2d",
                      macro_name, mod, seq_model, bottom_area, bottom_cfg,
                      bottom_comb_model.get());
            emit_library_cell(aggregate_2d_lib, macro_name, mod, seq_model,
                              bottom_area, bottom_cfg, bottom_comb_model.get());
            write_lib(lib_bottom_dir + "/" + macro_name + ".bottom.lib",
                      macro_name + "__bottom",
                      macro_name + "_bottom", mod, seq_model, bottom_area, bottom_cfg,
                      bottom_comb_model.get());
            write_lib(lib_upper_dir + "/" + macro_name + ".upper.lib",
                      macro_name + "__upper",
                      macro_name + "_upper", mod, seq_model, upper_area, upper_cfg,
                      upper_comb_model.get());
            total++;
        }

        aggregate_2d_lib << "}\n";
        aggregate_2d_lib.close();
        if (aggregate_2d_lib.fail())
            log_error("syn3d_write_coarse_unit_libs: failed to write %s\n",
                      aggregate_2d_lib_path.c_str());

        log_header(d, "syn3d_write_coarse_unit_libs: %d coarse-unit types written in three views\n",
                   total);
        log("  aggregate 2D LIB : %s\n", aggregate_2d_lib_path.c_str());
        log("  2D LIB dir : %s\n", lib_2d_dir.c_str());
        log("  bottom LIB dir : %s\n", lib_bottom_dir.c_str());
        log("  upper LIB dir : %s\n", lib_upper_dir.c_str());
        log("  bottom topology dir : %s\n", synth_bottom_dir.c_str());
        log("  upper topology dir : %s\n", synth_upper_dir.c_str());
        log("  total delay calculation time : %lld ms\n",
            static_cast<long long>(total_calc_ms));
    }
} Syn3dWriteCoarseUnitLibsPass;

PRIVATE_NAMESPACE_END
