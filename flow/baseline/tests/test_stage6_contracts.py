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
    def test_driver_has_only_frontend_responsibility(self) -> None:
        text = (BASELINE / "run_baseline_synth.sh").read_text(encoding="utf-8")
        self.assertIn("ord-synth", text)
        self.assertIn("3dsyn-baseline-genus", text)
        self.assertLess(text.index("ord-synth"), text.index("3dsyn-baseline-genus"))
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
esac
""",
                encoding="utf-8",
            )
            fake_make.chmod(0o755)

            fake_tool = bin_dir / "tool"
            fake_tool.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_tool.chmod(0o755)

            design_config = temp_dir / "config2d.mk"
            design_sdc = temp_dir / "constraint.sdc"
            design_config.write_text("export DESIGN_NAME = test\n", encoding="utf-8")
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
                            "--run-dir",
                            str(run_dir),
                            "--yosys-bin",
                            str(fake_tool),
                            "--genus-bin",
                            str(fake_tool),
                            "--design-config",
                            str(design_config),
                            "--design-sdc",
                            str(design_sdc),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )

                    calls = make_log.read_text(encoding="utf-8").split("CALL\n")
                    calls = [call for call in calls if call]
                    self.assertEqual(len(calls), 2)
                    for call in calls:
                        self.assertIn(f"PLATFORM={platform_2d}\n", call)
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
            "--genus-bin",
            "--synth-opt-mode",
        ):
            self.assertIn(option, result.stdout)

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
            self.assertTrue((case_dir / "config.mk").is_file())
            self.assertTrue((case_dir / "constraint.sdc").is_file())


if __name__ == "__main__":
    unittest.main()
