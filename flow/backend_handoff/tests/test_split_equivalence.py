#!/usr/bin/env python3
"""Verify that backend partition helpers preserve the Integration implementation."""

import ast
import unittest
from pathlib import Path


STAGE_DIR = Path(__file__).resolve().parents[1]
WORKSPACE = STAGE_DIR.parents[2]
SOURCE = (
    WORKSPACE
    / "TaiWei-Integration"
    / "TaiWei-Pin-3D"
    / "scripts_3dsyn"
    / "handoff"
    / "ord"
    / "02_prepare_backend_partition.py"
)
TARGETS = [
    STAGE_DIR / "02_prepare_backend_partition.py",
    STAGE_DIR / "backend_partition_rules.py",
    STAGE_DIR / "backend_partition_analysis.py",
]


def functions(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name: ast.dump(node, include_attributes=False)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


class BackendPartitionSplitTest(unittest.TestCase):
    def test_helper_function_bodies_are_unchanged(self):
        if not SOURCE.is_file():
            self.skipTest("Integration source tree is unavailable")
        expected = functions(SOURCE)
        observed = {}
        for target in TARGETS:
            observed.update(functions(target))
        for name, definition in expected.items():
            if name == "main":
                continue
            self.assertEqual(observed.get(name), definition, name)


if __name__ == "__main__":
    unittest.main()

