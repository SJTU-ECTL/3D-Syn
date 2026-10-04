#!/usr/bin/env python3

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
MIGRATED_ROOT = TEST_DIR.parent
SYN3D_ROOT = TEST_DIR.parents[2]
REFERENCE_ROOT = Path(
    os.environ.get(
        "SYN3D_REFERENCE_TOOLKIT",
        SYN3D_ROOT.parent
        / "TaiWei-Integration"
        / "TaiWei-Pin-3D"
        / "scripts_3dsyn"
        / "coarse_synth"
        / "common"
        / "yosys_json_toolkit",
    )
)


def load_package(name, package_root):
    spec = importlib.util.spec_from_file_location(
        name,
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sample_design_data():
    return {
        "creator": "3D-Syn migration test",
        "modules": {
            "top": {
                "attributes": {"top": "1"},
                "parameter_default_values": {"WIDTH": "00000010"},
                "ports": {
                    "clk": {"direction": "input", "bits": [1]},
                    "bus": {
                        "direction": "inout",
                        "offset": 2,
                        "upto": 0,
                        "signed": 0,
                        "bits": [2, 3],
                    },
                },
                "cells": {
                    "u$0": {
                        "hide_name": 0,
                        "type": "$not",
                        "parameters": {"A_WIDTH": "00000010"},
                        "attributes": {"src": "sample.v:1"},
                        "port_directions": {"A": "input", "Y": "output"},
                        "connections": {"A": [2, "0"], "Y": [4]},
                    }
                },
                "memories": {
                    "mem": {
                        "hide_name": 0,
                        "attributes": {"ram_style": "block"},
                        "width": 2,
                        "start_offset": 0,
                        "size": 4,
                        "custom_memory": "preserved",
                    }
                },
                "netnames": {
                    "clk": {"hide_name": 0, "bits": [1], "attributes": {}},
                    "bus": {
                        "hide_name": 0,
                        "offset": 2,
                        "upto": 0,
                        "signed": 0,
                        "bits": [2, 3],
                        "attributes": {},
                    },
                    r"\internal$node": {
                        "hide_name": 0,
                        "bits": [4],
                        "attributes": {"keep": "1"},
                    },
                    "internal_node": {"hide_name": 0, "bits": [5], "attributes": {}},
                    "constants": {"hide_name": 0, "bits": ["0", "1", "x", "z"], "attributes": {}},
                },
                "custom_module": {"owner": "test"},
            }
        },
        "models": {"model": ["0", "1"]},
        "custom_design": {"preserved": True},
    }


class MigrationEquivalenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not REFERENCE_ROOT.is_dir():
            raise unittest.SkipTest("Reference toolkit not found: {}".format(REFERENCE_ROOT))
        cls.reference = load_package("reference_yosys_json_toolkit", REFERENCE_ROOT)
        cls.migrated = load_package("migrated_yosys_json_toolkit", MIGRATED_ROOT)
        cls.design_text = json.dumps(sample_design_data())

    def load_pair(self):
        return (
            self.reference.loads_design(self.design_text),
            self.migrated.loads_design(self.design_text),
        )

    def test_public_api_is_unchanged(self):
        self.assertEqual(self.migrated.__all__, self.reference.__all__)
        for name in self.reference.__all__:
            self.assertTrue(hasattr(self.migrated, name), name)

    def test_round_trip_and_validation_match(self):
        reference, migrated = self.load_pair()
        self.reference.validate_design(reference)
        self.migrated.validate_design(migrated)
        self.assertEqual(
            json.loads(self.migrated.dumps_design(migrated)),
            json.loads(self.reference.dumps_design(reference)),
        )

    def test_inspection_results_match(self):
        reference, migrated = self.load_pair()
        reference_module = reference.modules["top"]
        migrated_module = migrated.modules["top"]
        self.assertEqual(
            self.migrated.module_interface_signature(migrated_module),
            self.reference.module_interface_signature(reference_module),
        )
        self.assertEqual(
            self.migrated.module_port_shape_signature(migrated_module),
            self.reference.module_port_shape_signature(reference_module),
        )
        self.assertEqual(
            self.migrated.collect_all_bits(migrated_module),
            self.reference.collect_all_bits(reference_module),
        )
        self.assertEqual(
            self.migrated.collect_instance_type_counts(migrated_module),
            self.reference.collect_instance_type_counts(reference_module),
        )

    def test_backend_netname_normalization_matches(self):
        reference, migrated = self.load_pair()
        reference_result = self.reference.normalize_backend_internal_netnames(reference, "top")
        migrated_result = self.migrated.normalize_backend_internal_netnames(migrated, "top")
        self.assertEqual(dict(migrated_result.mapping), dict(reference_result.mapping))
        self.assertEqual(migrated_result.selected_count, reference_result.selected_count)
        self.assertEqual(migrated_result.collision_count, reference_result.collision_count)
        self.assertEqual(migrated.to_dict(), reference.to_dict())

    def test_edit_sequence_matches(self):
        outputs = []
        for package in (self.reference, self.migrated):
            design = package.loads_design(self.design_text)
            module = design.modules["top"]
            allocator = package.BitAllocator.after(design)
            new_bits = allocator.allocate_many(2)
            package.add_port_with_netname(module, "extra", "output", new_bits)
            package.rename_port_and_netname(module, "extra", "renamed_extra")
            package.add_cell(
                module,
                "buffer",
                "BUF",
                {"A": [4], "Y": [new_bits[0]]},
                port_directions={"A": "input", "Y": "output"},
            )
            package.remap_cell_types(module, {"BUF": "INV"}, ["buffer"])
            package.remap_cell_connection_ports(module, {"A": "I", "Y": "O"}, ["buffer"], strict=True)
            package.replace_bit_references(module, new_bits[0], new_bits[1])
            package.rename_cell(module, "buffer", "buffer_renamed")
            package.remove_cell(module, "buffer_renamed")
            current_bits = list(module.ports["renamed_extra"].bits)
            package.remove_port_and_optional_netname(module, "renamed_extra", expected_bits=current_bits)
            package.validate_design(design)
            outputs.append(design.to_dict())
        self.assertEqual(outputs[1], outputs[0])


if __name__ == "__main__":
    unittest.main()
