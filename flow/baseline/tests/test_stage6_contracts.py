"""Check the Stage 6 baseline and shared backend source contracts."""

from __future__ import annotations

import subprocess
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
