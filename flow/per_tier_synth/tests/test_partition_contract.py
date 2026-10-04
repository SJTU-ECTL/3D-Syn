#!/usr/bin/env python3
"""Check homogeneous normalization and heterogeneous partition passthrough."""

import tempfile
import unittest
import sys
from pathlib import Path

STAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE_DIR))

from partition_contract import build_effective_partition


class PartitionContractTest(unittest.TestCase):
    def write_inputs(self, root, partition_text):
        partition = root / "partition.txt"
        floorplan = root / "floorplan.def"
        partition.write_text(partition_text, encoding="utf-8")
        floorplan.write_text(
            "PINS 2 ;\n- clk + NET clk ;\n- rst + NET rst ;\nEND PINS\n",
            encoding="utf-8",
        )
        return partition, floorplan

    def test_homogeneous_labels_are_preserved_when_zero_owns_more_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            partition, floorplan = self.write_inputs(root, "clk 0\nrst 0\nu0 1\n")
            output = root / "effective.txt"
            summary = build_effective_partition(
                "asap7_3D", str(partition), str(floorplan), str(output)
            )
            self.assertFalse(summary["swap_applied"])
            self.assertEqual(output.read_text(encoding="utf-8"), "clk 0\nrst 0\nu0 1\n")

    def test_homogeneous_labels_are_swapped_for_legacy_orientation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            partition, floorplan = self.write_inputs(root, "clk 1\nrst 1\nu0 0\n")
            output = root / "effective.txt"
            summary = build_effective_partition(
                "nangate45_3D", str(partition), str(floorplan), str(output)
            )
            self.assertTrue(summary["swap_applied"])
            self.assertEqual(output.read_text(encoding="utf-8"), "clk 0\nrst 0\nu0 1\n")

    def test_heterogeneous_partition_is_copied_verbatim(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            partition, floorplan = self.write_inputs(root, "\\u[0] 1\n")
            output = root / "effective.txt"
            summary = build_effective_partition(
                "asap7_nangate45_3D", str(partition), str(floorplan), str(output)
            )
            self.assertEqual(summary["mode"], "heterogeneous_passthrough")
            self.assertEqual(output.read_bytes(), partition.read_bytes())


if __name__ == "__main__":
    unittest.main()
