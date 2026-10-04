#!/usr/bin/env python3
"""Exercise both coarse-partition drivers with a controlled make executable."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


STAGE_DIR = Path(__file__).resolve().parents[1]


class CoarsePartitionDriverTest(unittest.TestCase):
    def run_driver(self, backend):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bin_dir = root / "bin"
            results = root / "results"
            handoff = root / "coarse" / "handoff"
            bin_dir.mkdir()
            results.mkdir()
            for path in [
                results / "1_synth.v",
                results / "1_synth.sdc",
                handoff / "lef" / "2d" / "coarse_units.lef",
                handoff / "lib" / "2d" / "coarse_units.lib",
            ]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")

            fake_make = bin_dir / "make"
            fake_make.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "${CALL_LOG}"
case " $* " in
  *" ${EXPECTED_TARGET} "*)
    for name in 2_2_floorplan_io.def 2_2_floorplan_io.v 2_2_floorplan_io.odb \\
      partition.txt partition.result.tcl partition.simple_plan.txt; do
      printf 'fixture\\n' > "${RESULTS_DIR}/${name}"
    done
    ;;
esac
""",
                encoding="utf-8",
            )
            fake_make.chmod(0o755)

            call_log = root / "make.calls"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": "{}:{}".format(bin_dir, env["PATH"]),
                    "CALL_LOG": str(call_log),
                    "EXPECTED_TARGET": "{}-tier-partition".format(backend),
                    "FLOW_HOME": str(root / "taiwei"),
                    "DESIGN_CONFIG": str(root / "config.mk"),
                    "SYN3D_COARSE_SYNTH_DIR": str(root / "coarse"),
                    "RESULTS_DIR": str(results),
                    "LEF_FILES": str(root / "base.lef"),
                    "LIB_FILES": str(root / "base.lib"),
                }
            )
            driver = STAGE_DIR / "run_{}_coarse_partition.sh".format(backend)
            subprocess.run([str(driver)], check=True, env=env, capture_output=True, text=True)

            calls = call_log.read_text(encoding="utf-8")
            self.assertIn("{}-preplace".format(backend), calls)
            self.assertIn("{}-tier-partition".format(backend), calls)
            self.assertIn("coarse_units.lef", calls)
            self.assertIn("coarse_units.lib", calls)
            self.assertTrue((results / "partition.coarse.txt").is_file())
            self.assertFalse((results / "partition.txt").exists())

    def test_ord_driver_contract(self):
        self.run_driver("ord")

    def test_cds_driver_contract(self):
        self.run_driver("cds")


if __name__ == "__main__":
    unittest.main()

