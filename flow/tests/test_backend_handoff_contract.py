"""Exercise backend-handoff validation for every published design configuration."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_BACKEND = ROOT / "flow" / "run_backend.sh"
ASSIGNMENT = re.compile(
    r"^\s*export\s+DESIGN_NAME\s*(?:[?:+]?=)\s*([A-Za-z0-9_.-]+)\s*$",
    re.MULTILINE,
)


def design_name(config: Path) -> str:
    match = ASSIGNMENT.search(config.read_text(encoding="utf-8"))
    if not match:
        raise AssertionError(f"No literal DESIGN_NAME assignment in {config}")
    return match.group(1)


def write_nonempty(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("handoff contract fixture\n", encoding="utf-8")


class BackendHandoffContractTests(unittest.TestCase):
    def test_every_config_validates_for_both_backends(self) -> None:
        configs = sorted((ROOT / "configs").glob("*_3D/*/config.mk"))
        self.assertEqual(len(configs), 20)

        with tempfile.TemporaryDirectory(prefix="3dsyn-backend-handoff-") as temp:
            run_root = Path(temp)
            for config_3d in configs:
                platform = config_3d.parents[1].name
                design = config_3d.parent.name
                config_2d = config_3d.with_name("config2d.mk")
                self.assertTrue(config_2d.is_file(), config_2d)
                top = design_name(config_3d)
                self.assertEqual(design_name(config_2d), top, config_3d.parent)

                for backend in ("ord", "cds"):
                    with self.subTest(platform=platform, design=design, backend=backend):
                        variant = f"handoff_contract_{platform}_{design}_{backend}"
                        results = run_root / "results" / platform / design / variant
                        paths = {
                            "base": results / "base.v",
                            "backend_input": results / "base.backend.v",
                            "partition_from_flow": results / "partition_from_flow.txt",
                            "sdc": results / "1_synth.sdc",
                            "preplace_v": results / "2_2_floorplan_io.backend.v",
                            "preplace_def": results / "2_2_floorplan_io.backend.def",
                            "partition": results / "partition.txt",
                            "netlist": results / f"{top}_3D.fp.v",
                            "def": results / f"{top}_3D.fp.def",
                        }
                        if backend == "ord":
                            paths["preplace_odb"] = results / "2_2_floorplan_io.backend.odb"
                        for path in paths.values():
                            write_nonempty(path)

                        intermediate = {
                            "preplace_netlist": str(paths["preplace_v"]),
                            "preplace_def": str(paths["preplace_def"]),
                            "partition": str(paths["partition"]),
                        }
                        if backend == "ord":
                            intermediate["preplace_odb"] = str(paths["preplace_odb"])
                        manifest = {
                            "stage": "3dsyn_backend_handoff",
                            "backend": backend,
                            "design": top,
                            "platform": platform,
                            "flow_variant": variant,
                            "backend_preplace_optimization": False,
                            "inputs": {
                                "base_netlist": str(paths["base"]),
                                "backend_input_netlist": str(paths["backend_input"]),
                                "partition": str(paths["partition_from_flow"]),
                                "sdc": str(paths["sdc"]),
                            },
                            "intermediate_outputs": intermediate,
                            "backend_outputs": {
                                "netlist": str(paths["netlist"]),
                                "def": str(paths["def"]),
                            },
                        }
                        manifest_path = results / "backend_handoff_manifest.json"
                        manifest_path.write_text(
                            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                        )

                        env = os.environ.copy()
                        env.update(
                            {
                                "SYN3D_ROOT": str(ROOT),
                                "TAIWEI_FLOW_HOME": str(ROOT),
                                "PLATFORM": platform,
                                "DESIGN": design,
                                "BACKEND": backend,
                                "FLOW_VARIANT": variant,
                                "RUN_DIR": str(run_root),
                                "DESIGN_CONFIG_3D": str(config_3d),
                                "DESIGN_SDC": str(config_3d.parent / "constraint.sdc"),
                                "OPENROAD_EXE": "/bin/true",
                                "INNOVUS_EXE": "/bin/true",
                                "PYTHON_EXE": sys.executable,
                                "NUM_CORES": "1",
                            }
                        )
                        completed = subprocess.run(
                            ["bash", str(RUN_BACKEND), "--validate-handoff-only"],
                            env=env,
                            text=True,
                            capture_output=True,
                            check=False,
                        )
                        self.assertEqual(
                            completed.returncode,
                            0,
                            completed.stdout + completed.stderr,
                        )
                        self.assertIn(
                            f"design={top} platform={platform} backend={backend}",
                            completed.stdout,
                        )
                        self.assertIn(
                            "Backend handoff validation-only completed",
                            completed.stdout,
                        )
                        self.assertNotIn("Running backend stage", completed.stdout)
                        if platform == "asap7_3D" and design == "aes" and backend == "ord":
                            alias_netlist = results / "aes_3D.fp.v"
                            write_nonempty(alias_netlist)
                            manifest["backend_outputs"]["netlist"] = str(alias_netlist)
                            manifest_path.write_text(
                                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                            )
                            rejected = subprocess.run(
                                ["bash", str(RUN_BACKEND), "--validate-handoff-only"],
                                env=env,
                                text=True,
                                capture_output=True,
                                check=False,
                            )
                            self.assertEqual(rejected.returncode, 2)
                            self.assertIn(
                                "expected aes_cipher_top_3D.fp.v, got aes_3D.fp.v",
                                rejected.stderr,
                            )


if __name__ == "__main__":
    unittest.main()
