"""Tests for the Python/EDA subprocess library boundary."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "flow"))

from env_isolation.eda_subprocess_env import (  # noqa: E402
    PRIVATE_LIBRARY_ENV,
    eda_subprocess_env,
)


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_private_library_is_removed_without_mutating_parent(self) -> None:
        private = "/opt/python/site-packages/torch/lib"
        original = {
            PRIVATE_LIBRARY_ENV: private,
            "LD_LIBRARY_PATH": f"{private}:/opt/cadence/lib:/opt/gcc/lib",
            "SYN3D_KEEP": "yes",
        }

        child = eda_subprocess_env(original)

        self.assertNotIn(PRIVATE_LIBRARY_ENV, child)
        self.assertEqual(child["LD_LIBRARY_PATH"], "/opt/cadence/lib:/opt/gcc/lib")
        self.assertEqual(child["SYN3D_KEEP"], "yes")
        self.assertEqual(original[PRIVATE_LIBRARY_ENV], private)
        self.assertEqual(original["LD_LIBRARY_PATH"].split(":")[0], private)

    def test_no_marker_preserves_library_path(self) -> None:
        original = {"LD_LIBRARY_PATH": "/opt/cadence/lib", "SYN3D_KEEP": "yes"}
        self.assertEqual(eda_subprocess_env(original), original)


if __name__ == "__main__":
    unittest.main()
