#!/usr/bin/env python3
"""Exercise deterministic, path-independent parts of the offline pipeline."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate = load_script("offline_generate_rtl", "01_generate_rtl.py")
pnr = load_script("offline_run_pnr", "04_run_pnr.py")
dataset_builder = load_script("offline_build_dataset", "05_build_dataset.py")


class OfflinePipelineUnitTest(unittest.TestCase):
    def test_supported_units_match_the_published_dataset(self):
        self.assertEqual(
            generate.FF_FA_MODULES,
            [
                "FA", "DFF", "DFFE", "DFFSR", "DFFSRE", "ADFF", "ADFFE",
                "SDFF", "SDFFE", "SDFFCE",
            ],
        )
        self.assertEqual(
            generate.ARITH_MODULES,
            ["ADD", "SUB", "MUL", "DIV", "MOD", "ALU", "MACC"],
        )

    def test_default_generation_has_992_candidates_per_technology(self):
        ff_fa = len(generate.FF_FA_MODULES) * len(
            generate.width_points("ff_fa", 32, 64, 42)
        )
        arithmetic_same = len(generate.ARITH_MODULES) * len(
            generate.width_points("arith_same", 32, 64, 42)
        )
        arithmetic_random = len(generate.ARITH_MODULES) * len(
            generate.width_points("arith_rand", 32, 64, 42)
        )
        self.assertEqual(ff_fa, 320)
        self.assertEqual(arithmetic_same, 224)
        self.assertEqual(arithmetic_random, 448)
        self.assertEqual(ff_fa + arithmetic_same + arithmetic_random, 992)

    def test_random_width_points_are_reproducible_and_non_diagonal(self):
        first = generate.width_points("arith_rand", 8, 20, 42)
        second = generate.width_points("arith_rand", 8, 20, 42)
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(set(first)))
        self.assertTrue(all(not (a == b == y) for a, b, y in first))

    def test_core_dimensions_obey_process_grids(self):
        for technology in ("asap7", "nangate45"):
            width, height = pnr.compute_core_dims(technology, 20.0, 12, 0.3)
            self.assertGreater(width, 0)
            self.assertGreater(height, 0)
            self.assertAlmostEqual(
                width / pnr.SITE_WIDTH[technology],
                round(width / pnr.SITE_WIDTH[technology]),
                places=5,
            )
            self.assertAlmostEqual(
                height / pnr.SITE_HEIGHT[technology],
                round(height / pnr.SITE_HEIGHT[technology]),
                places=5,
            )

    def test_split_manifest_has_no_source_leakage(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for group, names in {
                "ff_fa": ("DFF_1_1_0_0_0", "DFF_2_2_0_0_0", "DFF_3_3_0_0_0"),
                "arith_same": ("ADD_1_1_1_0_0", "ADD_2_2_2_0_0", "ADD_3_3_3_0_0"),
            }.items():
                graph_dir = root / group / "graphs"
                graph_dir.mkdir(parents=True)
                for name in names:
                    (graph_dir / f"{name}.pt").write_bytes(b"placeholder")
            path = dataset_builder.write_split_manifest(root, "asap7", 42, 0.7, 0.15)
            manifest = json.loads(path.read_text(encoding="utf-8"))
            origin_sets = [set(manifest["origins"][name])
                           for name in ("train", "validation", "test")]
            self.assertTrue(all(origin_sets))
            self.assertFalse(origin_sets[0] & origin_sets[1])
            self.assertFalse(origin_sets[0] & origin_sets[2])
            self.assertFalse(origin_sets[1] & origin_sets[2])


if __name__ == "__main__":
    unittest.main()
