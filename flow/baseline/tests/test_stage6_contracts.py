"""Check the Stage 6 baseline and shared backend source contracts."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BASELINE = ROOT / "flow" / "baseline"
BACKEND = ROOT / "flow" / "backend"
OVERLAY = ROOT / "taiwei" / "3dsyn.mk"


class BaselineContractTests(unittest.TestCase):
    def test_driver_runs_synthesis_before_taiwei_physical_stages(self) -> None:
        text = (BASELINE / "run_baseline_synth.sh").read_text(encoding="utf-8")
        self.assertIn("ord-synth", text)
        self.assertIn("3dsyn-baseline-genus", text)
        self.assertLess(text.index("ord-synth"), text.index("3dsyn-baseline-genus"))
        self.assertLess(text.index("3dsyn-baseline-genus"), text.index('"${backend}-preplace"'))
        self.assertIn("ord-3d-floorplan", text)
        self.assertIn("cds-3d-floorplan", text)
        self.assertIn('"3dsyn-${stage}"', text)
        self.assertIn('"TIER_ALLOW_NET=upper-only"', text)
        self.assertIn('"TIER_ALLOW_NET=bottom-only"', text)
        self.assertNotIn("flow/run_backend.sh", text)
        self.assertNotIn("backend_handoff_manifest", text)
        self.assertIn("1_synth_yosys.v", text)
        self.assertIn("1_synth_netlist.v", text)
        self.assertIn(
            'cp -f -- "${design_sdc}" "${results_dir}/1_synth.sdc"', text
        )
        self.assertNotIn(
            'cp -f -- "${results_dir}/1_synth_netlist.sdc" '
            '"${results_dir}/1_synth.sdc"',
            text,
        )
        self.assertNotIn("README_backend.sh", text)
        self.assertNotIn("TAIWEI_WORKFLOW_RUN_BACKEND", text)

    def test_driver_forwards_2d_platform_and_keeps_3d_output_namespace(self) -> None:
        platform_pairs = (
            ("nangate45_3D", "nangate45"),
            ("asap7_3D", "asap7"),
            ("asap7_nangate45_3D", "asap7_nangate45"),
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            taiwei_home = temp_dir / "TaiWei"
            flow_home = taiwei_home / "TaiWei-Pin-3D"
            flow_home.mkdir(parents=True)
            (flow_home / "Makefile").write_text("# test fixture\n", encoding="utf-8")

            bin_dir = temp_dir / "bin"
            bin_dir.mkdir()
            make_log = temp_dir / "make.log"
            fake_make = bin_dir / "make"
            fake_make.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
printf 'CALL\\n' >> \"${BASELINE_TEST_MAKE_LOG}\"
printf '%s\\n' \"$@\" >> \"${BASELINE_TEST_MAKE_LOG}\"
results_dir=''
for argument in \"$@\"; do
  case \"${argument}\" in
    RESULTS_DIR=*) results_dir=\"${argument#RESULTS_DIR=}\" ;;
  esac
done
mkdir -p -- \"${results_dir}\"
case \" $* \" in
  *' ord-synth '*) printf 'module test; endmodule\\n' > \"${results_dir}/1_synth.v\" ;;
  *' 3dsyn-baseline-genus '*)
    printf 'module test; endmodule\\n' > \"${results_dir}/1_synth_netlist.v\"
    printf 'create_clock -period 1 clk\\n' > \"${results_dir}/1_synth_netlist.sdc\"
    ;;
  *' ord-preplace '*|*' cds-preplace '*)
    printf 'module test; endmodule\\n' > \"${results_dir}/2_2_floorplan_io.v\"
    printf 'VERSION 5.8 ;\\nEND DESIGN\\n' > \"${results_dir}/2_2_floorplan_io.def\"
    ;;
  *' 3dsyn-ord-tier-partition '*|*' 3dsyn-cds-tier-partition '*)
    printf 'u0 0\\n' > \"${results_dir}/partition.txt\"
    ;;
  *' 3dsyn-ord-3d-floorplan '*|*' 3dsyn-cds-3d-floorplan '*)
    printf 'module test; endmodule\\n' > \"${results_dir}/2_3_floorplan_3d.v\"
    printf 'VERSION 5.8 ;\\nEND DESIGN\\n' > \"${results_dir}/2_3_floorplan_3d.def\"
    ;;
esac
""",
                encoding="utf-8",
            )
            fake_make.chmod(0o755)

            fake_tool = bin_dir / "tool"
            fake_tool.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_tool.chmod(0o755)

            design_config = temp_dir / "config2d.mk"
            design_config_3d = temp_dir / "config.mk"
            design_sdc = temp_dir / "constraint.sdc"
            design_config.write_text("export DESIGN_NAME = test\n", encoding="utf-8")
            design_config_3d.write_text("export DESIGN_NAME = test\n", encoding="utf-8")
            design_sdc.write_text("create_clock -period 1 clk\n", encoding="utf-8")

            environment = os.environ.copy()
            environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
            environment["BASELINE_TEST_MAKE_LOG"] = str(make_log)
            environment["LC_ALL"] = "C"
            environment["LANG"] = "C"

            for platform_3d, platform_2d in platform_pairs:
                with self.subTest(platform=platform_3d):
                    make_log.write_text("", encoding="utf-8")
                    run_dir = temp_dir / f"run-{platform_3d}"
                    subprocess.run(
                        [
                            str(BASELINE / "run_baseline_synth.sh"),
                            "--taiwei-home",
                            str(taiwei_home),
                            "--platform",
                            platform_3d,
                            "--design",
                            "test",
                            "--backend",
                            "ord",
                            "--run-dir",
                            str(run_dir),
                            "--yosys-bin",
                            str(fake_tool),
                            "--openroad-bin",
                            str(fake_tool),
                            "--genus-bin",
                            str(fake_tool),
                            "--python-bin",
                            str(fake_tool),
                            "--design-config",
                            str(design_config),
                            "--design-config-3d",
                            str(design_config_3d),
                            "--design-sdc",
                            str(design_sdc),
                            "--stop-stage",
                            "3d-floorplan",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )

                    calls = make_log.read_text(encoding="utf-8").split("CALL\n")
                    calls = [call for call in calls if call]
                    self.assertEqual(len(calls), 7)
                    for call in calls[1:5]:
                        self.assertIn(f"PLATFORM={platform_2d}\n", call)
                    for call in (calls[0], calls[5], calls[6]):
                        self.assertIn(f"PLATFORM={platform_3d}\n", call)
                    self.assertTrue(
                        (
                            run_dir
                            / "results"
                            / platform_3d
                            / "test"
                            / "baseline"
                            / "1_synth.v"
                        ).is_file()
                    )

    def test_driver_exposes_complete_help(self) -> None:
        result = subprocess.run(
            [str(BASELINE / "run_baseline_synth.sh"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        for option in (
            "--taiwei-home",
            "--design-sdc",
            "--yosys-bin",
            "--openroad-bin",
            "--genus-bin",
            "--innovus-bin",
            "--python-bin",
            "--backend",
            "--design-config-3d",
            "--synth-opt-mode",
            "--stop-stage",
        ):
            self.assertIn(option, result.stdout)

    def test_view_generation_rc_override_is_stage_local(self) -> None:
        driver = (BASELINE / "run_baseline_synth.sh").read_text(encoding="utf-8")
        rc_path = (
            BASELINE
            / "platform_overrides"
            / "asap7_nangate45_3D"
            / "setRC_6M10M.tcl"
        )
        rc_text = rc_path.read_text(encoding="utf-8")

        self.assertIn('"${backend}" == cds', driver)
        self.assertIn('"${platform}" == asap7_nangate45_3D', driver)
        self.assertIn('"${stage}" == cds-pre', driver)
        self.assertIn('"SET_RC_TCL=${view_generation_set_rc}"', driver)
        common_3d_arguments = driver.split("make_3d_args=(", 1)[1].split(")", 1)[0]
        self.assertNotIn(
            '"SET_RC_TCL=${view_generation_set_rc}"', common_3d_arguments
        )

        for missing_layer in ("M2_add", "M3_add", "V1_add", "V2_add"):
            option = "via" if missing_layer.startswith("V") else "layer"
            self.assertNotIn(f"-{option} {missing_layer} ", rc_text)
        for retained_layer in (
            "M1",
            "M10",
            "M1_m",
            "M6_m",
            "V1_m",
            "V5_m",
            "hb_layer",
        ):
            self.assertIn(retained_layer, rc_text)

    def test_cds_tier_transitions_normalize_rows(self) -> None:
        driver = (BASELINE / "run_baseline_synth.sh").read_text(encoding="utf-8")

        self.assertIn("3dsyn-cds-normalize-tier-handoff", driver)
        for call in (
            "normalize_cds_handoff bottom place_init",
            "normalize_cds_handoff upper place_init_upper",
            "normalize_cds_handoff bottom place_init_bottom",
            'normalize_cds_handoff upper "prects_upper_${iteration}"',
            'normalize_cds_handoff bottom "prects_bottom_${iteration}"',
            "normalize_cds_handoff upper legalize_upper",
            "normalize_cds_handoff bottom legalize_bottom",
            "normalize_cds_handoff owner cts_owner_tree",
            "normalize_cds_handoff receive cts_receive_opt",
        ):
            self.assertIn(call, driver)
        for target in (
            "cds-cts-owner-tree",
            "cds-cts-receive-opt",
            "cds-cts-finalize",
        ):
            self.assertIn(target, driver)

    def test_genus_consumes_only_the_yosys_netlist(self) -> None:
        text = (BASELINE / "run_genus_on_netlist.tcl").read_text(encoding="utf-8")
        self.assertIn("read_hdl -sv $NETLIST_IN", text)
        self.assertNotIn("$rtl_all", text)
        self.assertNotIn("VERILOG_FILES", text)
        self.assertNotIn("syn_generic\n", text)
        self.assertNotIn("syn_map\n", text)
        self.assertIn("normal {\n      syn_opt", text)
        self.assertIn("low_effort {\n      syn_opt -incr -effort low", text)

    def test_overlay_defines_all_stage6_targets(self) -> None:
        text = OVERLAY.read_text(encoding="utf-8")
        for target in (
            "3dsyn-baseline-genus",
            "3dsyn-ord-preplace",
            "3dsyn-cds-preplace",
            "3dsyn-ord-3d-floorplan",
            "3dsyn-cds-3d-floorplan",
        ):
            self.assertIn(target + ":", text)
        self.assertIn('cd "$(OBJECTS_DIR)" && /usr/bin/env', text)


class BackendContractTests(unittest.TestCase):
    def test_preplace_modes_preserve_public_semantics(self) -> None:
        ord_text = (BACKEND / "ord" / "floorplan.tcl").read_text(encoding="utf-8")
        cds_text = (BACKEND / "cds" / "innovus_preplace.tcl").read_text(encoding="utf-8")
        self.assertIn('if {$enable_opt eq "1"}', ord_text)
        self.assertIn("remove_buffers", ord_text)
        self.assertIn("place_design -noPrePlaceOpt", cds_text)
        self.assertIn("pc::run_global_place_step preplace", cds_text)

    def test_fixed_area_formula_matches_between_backends(self) -> None:
        ord_text = (BACKEND / "ord" / "floorplan_3d.tcl").read_text(encoding="utf-8")
        cds_text = (BACKEND / "cds" / "innovus_3d_floorplan.tcl").read_text(encoding="utf-8")
        self.assertIn("sqrt($core_area_um2 / double($aspect_ratio))", ord_text)
        self.assertIn("sqrt($requested_core_area / double($ASPECT_RATIO))", cds_text)
        for text in (ord_text, cds_text):
            self.assertIn("FIXED_CORE_AREA", text)
            self.assertIn("Final snapped core W/H/area/aspect", text)

    def test_cadence_attribution_is_preserved(self) -> None:
        for name in ("innovus_preplace.tcl", "innovus_3d_floorplan.tcl"):
            text = (BACKEND / "cds" / name).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# This script was written and developed"))
            self.assertIn("copyrighted by Cadence", text)

    def test_all_repository_cases_have_explicit_constraints(self) -> None:
        configs = ROOT / "configs"
        case_dirs = sorted(path.parent for path in configs.glob("*/*/config2d.mk"))
        self.assertEqual(len(case_dirs), 20)
        for case_dir in case_dirs:
            config = case_dir / "config.mk"
            self.assertTrue(config.is_file())
            self.assertTrue((case_dir / "constraint.sdc").is_file())
            config_text = config.read_text(encoding="utf-8")
            self.assertEqual(config_text.count("export FIXED_CORE_AREA ="), 1)


if __name__ == "__main__":
    unittest.main()
