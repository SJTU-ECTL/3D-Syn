#!/usr/bin/env python3
"""Verify that the approved module split preserves every original function body."""

import ast
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
STAGE_DIR = TEST_DIR.parent
WORKSPACE = STAGE_DIR.parents[2]
SOURCE_DIR = (
    WORKSPACE
    / "TaiWei-Integration"
    / "TaiWei-Pin-3D"
    / "scripts_3dsyn"
    / "per_tier_synth"
)


SPLITS = {
    "02_split_tiers.py": [
        "02_split_tiers.py",
        "modules/tier_split_analysis.py",
        "modules/tier_split_builder.py",
    ],
    "05_build_wrapper.py": [
        "05_build_wrapper.py",
        "modules/wrapper_plan.py",
        "modules/wrapper_builder.py",
    ],
    "06_synthesize_tiers.py": [
        "06_synthesize_tiers.py",
        "modules/genus_common.py",
        "modules/genus_homogeneous.py",
        "modules/genus_heterogeneous.py",
    ],
}


def function_bodies(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            result.setdefault(node.name, []).append(ast.dump(node, include_attributes=False))
    return result


class SplitEquivalenceTest(unittest.TestCase):
    def test_original_definitions_are_preserved(self):
        if not SOURCE_DIR.is_dir():
            self.skipTest("Integration source tree is unavailable")
        for source_name, target_names in SPLITS.items():
            expected = function_bodies(SOURCE_DIR / source_name)
            observed = {}
            for target_name in target_names:
                for name, definitions in function_bodies(STAGE_DIR / target_name).items():
                    observed.setdefault(name, []).extend(definitions)
            for name, definitions in expected.items():
                self.assertIn(name, observed, "{} was not migrated".format(name))
                self.assertIn(
                    definitions[0],
                    observed[name],
                    "{} changed during the approved split".format(name),
                )


if __name__ == "__main__":
    unittest.main()
