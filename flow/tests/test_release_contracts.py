"""Static contracts for the compact public release interface."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ReleaseContractTests(unittest.TestCase):
    def test_public_make_interface(self) -> None:
        text = (ROOT / "Makefile").read_text(encoding="utf-8")
        for target in ("plugin:", "run:", "baseline:"):
            self.assertIn(target, text)
        self.assertNotIn("setup:", text)
        self.assertNotIn("python-env:", text)
        self.assertNotIn("check:", text)
        self.assertIn("run: _run-all", text)
        self.assertNotIn("MODE ?=", text)
        self.assertNotIn("run_all:", text)
        self.assertNotIn("run_backend:", text)

    def test_public_launchers_and_internal_drivers_are_separate(self) -> None:
        self.assertTrue((ROOT / "run_3d_syn.sh").is_file())
        self.assertTrue((ROOT / "run_baseline.sh").is_file())
        self.assertFalse((ROOT / "flow" / "run_3d_syn.sh").exists())
        self.assertTrue((ROOT / "flow" / "run_frontend.sh").is_file())
        self.assertTrue((ROOT / "flow" / "run_backend.sh").is_file())
        self.assertFalse((ROOT / "flow" / "internal").exists())
        self.assertFalse((ROOT / "requirements.txt").exists())
        self.assertFalse((ROOT / "tools" / "setup_python_env.sh").exists())
        self.assertFalse((ROOT / "taiwei" / "check_config.sh").exists())

    def test_launcher_exposes_only_paper_cases(self) -> None:
        for name in ("run_3d_syn.sh", "run_baseline.sh"):
            text = (ROOT / name).read_text(encoding="utf-8")
            config = text.split("# End user configuration", 1)[0]
            self.assertIn('TECH="nangate45_3D"', config)
            self.assertIn("asap7_3D, nangate45_3D, asap7_nangate45_3D", config)
            self.assertIn(
                "gcd|aes|ibex|jpeg|ariane133|bp_quad|swerv_wrapper", text
            )

    def test_complete_launcher_defaults_and_fixed_util_contract(self) -> None:
        text = (ROOT / "run_3d_syn.sh").read_text(encoding="utf-8")
        config = text.split("# End user configuration", 1)[0]
        self.assertIn('BACKEND="cds"', config)
        self.assertIn('RUN_TAG="example_3dsyn"', config)
        self.assertIn('FIXED_UTIL=1.0', config)
        self.assertIn('SYNTH_OPT_MODE="low_effort"', config)
        self.assertIn('HBT_R_OHM=1.0', config)
        self.assertIn('HBT_C_FF=0.5', config)
        self.assertNotIn("--mode", text)
        self.assertIn('if [[ "${AREA_MODE}" == fixed ]]', text)

        baseline = (ROOT / "run_baseline.sh").read_text(encoding="utf-8")
        baseline_config = baseline.split("# End user configuration", 1)[0]
        self.assertIn('BACKEND="cds"', baseline_config)
        self.assertIn('RUN_TAG="example_baseline"', baseline_config)
        self.assertIn('SYNTH_OPT_MODE="low_effort"', baseline_config)
        self.assertIn('case "${BACKEND}" in ord|cds)', baseline)
        self.assertIn('"BASELINE_STOP_STAGE=${BASELINE_STOP_STAGE}"', baseline)

    def test_direct_hbt_defaults_reach_per_tier_synthesis(self) -> None:
        overlay = (ROOT / "taiwei" / "3dsyn.mk").read_text(encoding="utf-8")
        frontend = (ROOT / "flow" / "run_frontend.sh").read_text(encoding="utf-8")
        stage = (ROOT / "flow" / "per_tier_synth" / "run_per_tier_synth.sh").read_text(
            encoding="utf-8"
        )
        model = (ROOT / "flow" / "per_tier_synth" / "modules" / "hbt_model.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("SYN3D_HBT_MODEL_MODE ?= direct", overlay)
        self.assertIn("SYN3D_HBT_R_OHM ?= 1", overlay)
        self.assertIn("SYN3D_HBT_C_FF ?= 0.5", overlay)
        self.assertIn('hbt_model_mode="${SYN3D_HBT_MODEL_MODE:-direct}"', frontend)
        self.assertIn('"SYN3D_HBT_R_OHM=${hbt_r_ohm}"', frontend)
        self.assertIn('"SYN3D_HBT_C_FF=${hbt_c_ff}"', frontend)
        self.assertIn('SYN3D_HBT_MODEL_MODE:-direct', stage)
        self.assertIn('DEFAULT_MODEL_MODE = "direct"', model)
        self.assertIn('DEFAULT_DIRECT_R_OHM = "1"', model)
        self.assertIn('DEFAULT_DIRECT_C_FF = "0.5"', model)

    def test_release_config_matrix(self) -> None:
        configs = sorted((ROOT / "configs").glob("*_3D/*/config.mk"))
        observed = {(path.parents[1].name, path.parent.name) for path in configs}
        common = {"gcd", "aes", "ibex", "jpeg", "ariane133", "swerv_wrapper"}
        expected = {
            (tech, design)
            for tech in ("asap7_3D", "nangate45_3D", "asap7_nangate45_3D")
            for design in common
        }
        expected.update(
            {("asap7_3D", "bp_quad"), ("nangate45_3D", "bp_quad")}
        )
        self.assertEqual(observed, expected)
        for config in configs:
            self.assertTrue(config.with_name("config2d.mk").is_file())
            self.assertTrue(config.with_name("constraint.sdc").is_file())

    def test_help_commands_are_side_effect_free(self) -> None:
        for command in (
            ["make", "--no-print-directory", "-C", str(ROOT), "help"],
            [str(ROOT / "run_3d_syn.sh"), "--help"],
            [str(ROOT / "run_baseline.sh"), "--help"],
        ):
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
