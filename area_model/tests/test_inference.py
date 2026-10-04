#!/usr/bin/env python3
"""Validate feature compatibility and the released inference contract."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch


REPO_ROOT = Path(__file__).resolve().parents[2]
AREA_MODEL_DIR = REPO_ROOT / "area_model"
WORKSPACE_ROOT = REPO_ROOT.parent
PLATFORM_DIR = Path(
    os.environ.get(
        "SYN3D_TEST_PLATFORM_DIR",
        WORKSPACE_ROOT / "TaiWei/TaiWei-Pin-3D/platforms/asap7_nangate45_3D",
    )
)
LEGACY_DIR = Path(
    os.environ.get(
        "SYN3D_TEST_LEGACY_AREA_DIR",
        WORKSPACE_ROOT
        / "TaiWei-Integration/TaiWei-Pin-3D/scripts_3dsyn/coarse_synth/area",
    )
)
PYTHON = Path(sys.executable)

sys.path.insert(0, str(AREA_MODEL_DIR))
import predict_util as current


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = load_module("legacy_predict_util", LEGACY_DIR / "predict_util.py")
characterize = load_module(
    "characterize_coarse_units",
    REPO_ROOT / "flow/coarse_synth/characterize_coarse_units.py",
)


NETLISTS = {
    "bottom": """\
module _add_A1_B1_Y1(A, B, Y);
  input A;
  input B;
  output Y;
  wire n;
  AND2_X1_bottom u0 (.A1(A), .A2(B), .ZN(n));
  BUF_X1_bottom u1 (.A(n), .Z(Y));
endmodule
""",
    "upper": """\
module _add_A1_B1_Y1(A, B, Y);
  input A;
  input B;
  output Y;
  wire n;
  AND2x2_ASAP7_75t_R_upper u0 (.A(A), .B(B), .Y(n));
  BUFx2_ASAP7_75t_R_upper u1 (.A(n), .Y(Y));
endmodule
""",
}


def liberty_paths(technology):
    return [
        PLATFORM_DIR / relative
        for relative in current.TECHNOLOGIES[technology]["liberties"]
    ]


class InferenceCompatibilityTest(unittest.TestCase):
    def test_legacy_and_current_features_match(self):
        schema = current.load_json(
            AREA_MODEL_DIR / "resources/port_schema.json", "port schema"
        )
        vocabulary = current.load_json(
            AREA_MODEL_DIR / "resources/cell_type_vocab.json", "cell vocabulary"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            for technology, source in NETLISTS.items():
                netlist = temporary_directory / f"{technology}.v"
                netlist.write_text(source, encoding="utf-8")
                areas = current.parse_lib_areas(liberty_paths(technology))
                legacy_module = legacy.parse_netlist(netlist, areas)
                current_module = current.parse_netlist(
                    netlist, areas, "_add_A1_B1_Y1"
                )

                old_graph, old_nets, old_edges = legacy.build_cell_graph(legacy_module)
                new_graph, new_nets, new_edges = current.build_cell_graph(current_module)
                self.assertEqual(old_edges, new_edges)
                self.assertEqual(
                    legacy.extract_graph_features(
                        legacy_module, old_graph, old_nets, areas
                    ),
                    current.extract_graph_features(
                        current_module, new_graph, new_nets, areas
                    ),
                )
                self.assertEqual(
                    legacy.extract_node_features(legacy_module, old_graph, areas),
                    current.extract_node_features(current_module, new_graph, areas),
                )
                self.assertEqual(
                    legacy.build_bitwidth_vec("ADD", legacy_module["ports"], schema),
                    current.build_bitwidth_vec("ADD", current_module["ports"], schema),
                )

                x, _, graph_features, bitwidth_vector = current.build_input_tensors(
                    current_module, "_add_A1_B1_Y1", areas, schema, vocabulary
                )
                checkpoint = current.TECHNOLOGIES[technology]["checkpoint"]
                norm_stats = torch.load(
                    AREA_MODEL_DIR / "pretrained" / checkpoint / "norm_stats.pt",
                    weights_only=True,
                    map_location="cpu",
                )
                new_values = current.apply_norm(
                    x, graph_features, bitwidth_vector, norm_stats
                )
                old_values = legacy.apply_norm(
                    x, graph_features, bitwidth_vector, norm_stats
                )
                for new_tensor, old_tensor in zip(new_values, old_values):
                    self.assertTrue(torch.equal(new_tensor, old_tensor))

    def test_cli_predictions_match_and_ignore_cwd(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            for technology, source in NETLISTS.items():
                netlist = temporary_directory / f"{technology}.v"
                netlist.write_text(source, encoding="utf-8")
                common_args = [
                    str(netlist),
                    "_add_A1_B1_Y1",
                    technology,
                    "--common-platform-dir",
                    str(PLATFORM_DIR),
                ]
                old_result = subprocess.run(
                    [str(PYTHON), str(LEGACY_DIR / "predict_util.py"), *common_args],
                    cwd=temporary_directory,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                new_result = subprocess.run(
                    [str(PYTHON), str(AREA_MODEL_DIR / "predict_util.py"), *common_args],
                    cwd=Path("/tmp"),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertAlmostEqual(
                    float(old_result.stdout), float(new_result.stdout), places=6
                )

    def test_manifest_and_checkpoint_failures_are_explicit(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkpoint_root = Path(temporary_directory) / "pretrained"
            shutil.copytree(
                AREA_MODEL_DIR / "pretrained/nangate45",
                checkpoint_root / "nangate45",
            )
            manifest_path = checkpoint_root / "nangate45/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["platform"] = "asap7"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(current.PredictionError, "manifest platform"):
                current.validate_manifest(
                    checkpoint_root / "nangate45",
                    AREA_MODEL_DIR / "resources",
                    "bottom",
                )

            manifest["platform"] = "nangate45"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            (checkpoint_root / "nangate45/best_model.pt").unlink()
            with self.assertRaisesRegex(current.PredictionError, "missing or empty"):
                current.validate_manifest(
                    checkpoint_root / "nangate45",
                    AREA_MODEL_DIR / "resources",
                    "bottom",
                )

            resource_dir = Path(temporary_directory) / "resources"
            shutil.copytree(AREA_MODEL_DIR / "resources", resource_dir)
            schema_path = resource_dir / "port_schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            schema["ADD"]["parameterized_ports"][0]["slot_idx"] = 4
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            with self.assertRaisesRegex(current.PredictionError, "checksum mismatch"):
                current.validate_manifest(
                    AREA_MODEL_DIR / "pretrained/nangate45",
                    resource_dir,
                    "bottom",
                )

    def test_invalid_graph_schema_and_values_are_rejected(self):
        schema = current.load_json(
            AREA_MODEL_DIR / "resources/port_schema.json", "port schema"
        )
        vocabulary = current.load_json(
            AREA_MODEL_DIR / "resources/cell_type_vocab.json", "cell vocabulary"
        )
        with self.assertRaisesRegex(current.PredictionError, "empty cell graph"):
            current.extract_graph_features(
                {"instances": [], "port_nets": set()},
                current.nx.Graph(),
                {},
                {},
            )
        with self.assertRaisesRegex(current.PredictionError, "port schema mismatch"):
            current.build_bitwidth_vec(
                "ADD", {"A": {"width": 1}, "Y": {"width": 1}}, schema
            )
        with self.assertRaisesRegex(current.PredictionError, "absent"):
            current.build_input_tensors(
                {
                    "ports": {
                        "A": {"width": 1},
                        "B": {"width": 1},
                        "Y": {"width": 1},
                    },
                    "port_nets": {"A", "B", "Y"},
                    "instances": [
                        {"cell": "UNKNOWN_CELL", "inst": "u0", "pins": {"A": "A"}}
                    ],
                },
                "_add_A1_B1_Y1",
                {"UNKNOWN_CELL": 1.0},
                schema,
                vocabulary,
            )
        invalid_stats = {
            "x_mean": torch.full((9,), float("nan")),
            "x_std": torch.ones(9),
            "gf_mean": torch.zeros(17),
            "gf_std": torch.ones(17),
        }
        with self.assertRaisesRegex(current.PredictionError, "NaN or Inf"):
            current.validate_norm_stats(invalid_stats)

    def test_coarse_synthesis_prediction_adapter(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            netlist = temporary_directory / "mapped.v"
            netlist.write_text(NETLISTS["bottom"], encoding="utf-8")
            args = SimpleNamespace(
                fixed_util=None,
                python=str(PYTHON),
                predict_util=AREA_MODEL_DIR / "predict_util.py",
                checkpoint_root=AREA_MODEL_DIR / "pretrained",
            )
            config = {"common_root": PLATFORM_DIR}
            raw, used, source = characterize.predict_utilization(
                args,
                config,
                "_add_A1_B1_Y1",
                netlist,
                "bottom",
                temporary_directory / "adapter",
            )
            self.assertTrue(0.0 < raw <= 1.0)
            self.assertTrue(0.3 <= used <= 1.0)
            self.assertEqual(source, "gnn")


if __name__ == "__main__":
    unittest.main(verbosity=2)
