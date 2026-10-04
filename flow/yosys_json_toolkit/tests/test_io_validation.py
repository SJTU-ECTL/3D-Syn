#!/usr/bin/env python3

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
FLOW_DIR = TEST_DIR.parents[1]
if str(FLOW_DIR) not in sys.path:
    sys.path.insert(0, str(FLOW_DIR))

from yosys_json_toolkit import (  # noqa: E402
    JsonFormatError,
    ValidationError,
    dump_design,
    dump_design_fp,
    load_design,
    load_design_fp,
    loads_design,
    validate_design,
)


def complete_design():
    return {
        "creator": "io-validation-test",
        "modules": {
            "child": {
                "attributes": {},
                "ports": {
                    "io": {"direction": "inout", "bits": [1]},
                },
                "cells": {},
                "memories": {},
                "netnames": {
                    "io": {"hide_name": 0, "bits": [1], "attributes": {}},
                    "constants": {"hide_name": 0, "bits": ["0", "1", "x", "z"], "attributes": {}},
                },
                "custom_module_field": ["preserve", 1],
            },
            "top": {
                "attributes": {"top": "1"},
                "ports": {},
                "cells": {
                    "u_child": {
                        "hide_name": 0,
                        "type": "child",
                        "parameters": {},
                        "attributes": {},
                        "port_directions": {"io": "inout"},
                        "connections": {"io": [2]},
                    }
                },
                "memories": {
                    "mem": {
                        "hide_name": 0,
                        "attributes": {},
                        "width": 1,
                        "start_offset": 0,
                        "size": 2,
                    }
                },
                "netnames": {
                    r"\escaped$name": {"hide_name": 0, "bits": [2], "attributes": {}},
                },
            },
        },
        "custom_design_field": {"preserve": True},
    }


class IoValidationTest(unittest.TestCase):
    def test_round_trip_preserves_hierarchy_constants_memories_and_extras(self):
        source = complete_design()
        design = loads_design(json.dumps(source))
        validate_design(design)
        normalized = design.to_dict()
        self.assertEqual(normalized["custom_design_field"], source["custom_design_field"])
        self.assertEqual(normalized["modules"]["child"]["custom_module_field"], ["preserve", 1])
        self.assertEqual(normalized["modules"]["child"]["netnames"]["constants"]["bits"], ["0", "1", "x", "z"])
        self.assertEqual(normalized["modules"]["top"]["memories"]["mem"]["size"], 2)
        self.assertEqual(loads_design(json.dumps(normalized)).to_dict(), normalized)

    def test_path_and_file_object_io_match(self):
        source = complete_design()
        design = loads_design(json.dumps(source))
        normalized = design.to_dict()
        with tempfile.TemporaryDirectory(prefix="syn3d_json_io_") as temp_dir:
            path = Path(temp_dir) / "design.json"
            dump_design(design, path)
            from_path = load_design(path)
            buffer = io.StringIO()
            dump_design_fp(design, buffer)
            buffer.seek(0)
            from_file = load_design_fp(buffer)
        self.assertEqual(from_path.to_dict(), normalized)
        self.assertEqual(from_file.to_dict(), normalized)

    def test_non_object_root_is_rejected(self):
        with self.assertRaisesRegex(JsonFormatError, "root must be a dictionary"):
            loads_design("[]")

    def test_missing_modules_is_rejected(self):
        with self.assertRaisesRegex(JsonFormatError, "modules"):
            loads_design("{}")

    def test_structural_errors_are_reported_together(self):
        source = complete_design()
        source["modules"]["child"]["ports"]["io"]["direction"] = "invalid"
        source["modules"]["top"]["netnames"][r"\escaped$name"]["bits"] = [True]
        design = loads_design(json.dumps(source))
        with self.assertRaises(ValidationError) as context:
            validate_design(design)
        message = str(context.exception)
        self.assertIn("invalid direction", message)
        self.assertIn("invalid bit value", message)


if __name__ == "__main__":
    unittest.main()
