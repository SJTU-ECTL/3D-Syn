#!/usr/bin/env python3
"""Exercise backend partition reconciliation and shared view generation."""

import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path


STAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE_DIR))

from backend_partition_analysis import choose_tier  # noqa: E402


class TierChoiceTest(unittest.TestCase):
    def test_existing_neighbors_determine_the_lower_cut_tier(self):
        tier, reason = choose_tier(
            Counter({"bottom": 1}),
            Counter(),
            Counter({"bottom": 2}),
            "nangate45_3D",
            Counter(),
        )
        self.assertEqual((tier, reason), ("bottom", "edge_cost"))

    def test_heterogeneous_tie_defaults_to_upper(self):
        tier, reason = choose_tier(
            Counter(),
            Counter(),
            Counter(),
            "asap7_nangate45_3D",
            Counter(),
        )
        self.assertEqual((tier, reason), ("upper", "tie_hetero_default_upper"))


class PartitionUpdateIntegrationTest(unittest.TestCase):
    def test_added_buffer_is_appended_to_partition(self):
        flow_home = os.environ.get("TAIWEI_FLOW_HOME")
        yosys = os.environ.get("YOSYS")
        if not flow_home or not yosys:
            self.skipTest("TAIWEI_FLOW_HOME and YOSYS are required")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "results"
            results.mkdir()
            (results / "base.v").write_text(
                "module top(input A, output Y);\n"
                "  BUF_X1 u0 (.A(A), .Z(Y));\n"
                "endmodule\n",
                encoding="utf-8",
            )
            (results / "2_2_floorplan_io.v").write_text(
                "module top(input A, output Y);\n"
                "  wire n;\n"
                "  BUF_X1 u_added (.A(A), .Z(n));\n"
                "  BUF_X1 u0 (.A(n), .Z(Y));\n"
                "endmodule\n",
                encoding="utf-8",
            )
            (results / "partition.txt").write_text("u0 0\n", encoding="utf-8")

            command = [
                sys.executable,
                str(STAGE_DIR / "02_prepare_backend_partition.py"),
                "--results-dir",
                str(results),
                "--enablement",
                "nangate45_3D",
                "--design-nickname",
                "fixture",
                "--flow-root",
                flow_home,
                "--objects-dir",
                str(root / "objects"),
                "--log-dir",
                str(root / "reports"),
                "--yosys",
                yosys,
            ]
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            entries = (results / "partition.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(entries, ["u0 0", "u_added 0"], result.stdout)
            self.assertTrue(
                (results / "partition.before_preplace_update.txt").is_file()
            )


class BackendViewDriverTest(unittest.TestCase):
    def test_flow_root_selects_the_taiwei_view_helpers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper_dir = root / "flow" / "scripts_openroad"
            helper_dir.mkdir(parents=True)
            (helper_dir / "generate_3d_views.py").write_text(
                """
from pathlib import Path

def parse_partition_file(path):
    return {"u0": 0}

def swap_partition_labels(partition):
    return {"u0": 1}

def parse_cell_map_json(path):
    return ({}, {}, {}, {}, {}, {}, {}, False, False)

def rewrite_def(src, dst, partition, *args):
    assert partition == {"u0": 1}
    Path(dst).write_text(Path(src).read_text(encoding="utf-8"), encoding="utf-8")

def rewrite_verilog(src, dst, partition, *args):
    assert partition == {"u0": 1}
    Path(dst).write_text(Path(src).read_text(encoding="utf-8"), encoding="utf-8")
""",
                encoding="utf-8",
            )
            def_in = root / "in.def"
            verilog_in = root / "in.v"
            partition = root / "partition.txt"
            def_out = root / "out.def"
            verilog_out = root / "out.v"
            def_in.write_text("VERSION 5.8 ;\n", encoding="utf-8")
            verilog_in.write_text("module top; endmodule\n", encoding="utf-8")
            partition.write_text("u0 0\n", encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(STAGE_DIR / "03_generate_backend_views.py"),
                    "--flow-root",
                    str(root / "flow"),
                    "--def-in",
                    str(def_in),
                    "--v-in",
                    str(verilog_in),
                    "--partition",
                    str(partition),
                    "--def-out",
                    str(def_out),
                    "--v-out",
                    str(verilog_out),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(def_out.read_text(encoding="utf-8"), def_in.read_text(encoding="utf-8"))
            self.assertEqual(
                verilog_out.read_text(encoding="utf-8"),
                verilog_in.read_text(encoding="utf-8"),
            )


class BackendInputDriverTest(unittest.TestCase):
    def test_backend_input_is_tier_qualified_without_mutating_base_netlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper_dir = root / "flow" / "scripts_openroad"
            helper_dir.mkdir(parents=True)
            (helper_dir / "generate_3d_views.py").write_text(
                """
from pathlib import Path

def parse_partition_file(path):
    return {"u0": 0}

def swap_partition_labels(partition):
    return {name: 1 - die for name, die in partition.items()}

def parse_cell_map_json(path):
    return ({}, {}, {}, {}, {}, {}, {}, False, False)

def rewrite_verilog(src, dst, partition, *args):
    assert partition == {"u0": 1}
    text = Path(src).read_text(encoding="utf-8")
    Path(dst).write_text(text.replace("BUF_X1 u0", "BUF_X1_upper u0"), encoding="utf-8")
""",
                encoding="utf-8",
            )
            v_in = root / "base.v"
            partition = root / "partition.txt"
            v_out = root / "base.backend.v"
            original = "module top(input A, output Y); BUF_X1 u0 (.A(A), .Z(Y)); endmodule\n"
            v_in.write_text(original, encoding="utf-8")
            partition.write_text("u0 0\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(STAGE_DIR / "prepare_backend_netlist.py"),
                    "--flow-root",
                    str(root / "flow"),
                    "--v-in",
                    str(v_in),
                    "--partition",
                    str(partition),
                    "--v-out",
                    str(v_out),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(v_in.read_text(encoding="utf-8"), original)
            self.assertIn("BUF_X1_upper u0", v_out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
