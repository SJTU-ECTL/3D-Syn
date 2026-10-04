#!/usr/bin/env python3
"""Run the former width-mapping experiment as an optional Yosys check.

Set ``SYN3D_TEST_YOSYS_BIN`` and ``SYN3D_TEST_NANGATE45_LIB`` to enable this
tool-backed check.  It verifies that all comparison-width cases map and emit a
Liberty-based area report; it is not part of dataset generation.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


CASES = {
    "lt_4_4": "module lt_4_4(input [3:0] a,b, output c); assign c=(a<b); endmodule\n",
    "lt_8_8": "module lt_8_8(input [7:0] a,b, output c); assign c=(a<b); endmodule\n",
    "lt_8_4": "module lt_8_4(input [7:0] a,input [3:0] b,output c);assign c=(a<b);endmodule\n",
    "eq_4_4": "module eq_4_4(input [3:0] a,b, output c); assign c=(a==b); endmodule\n",
    "eq_8_8": "module eq_8_8(input [7:0] a,b, output c); assign c=(a==b); endmodule\n",
    "eq_8_4": "module eq_8_4(input [7:0] a,input [3:0] b,output c);assign c=(a==b);endmodule\n",
}


class WidthMappingTest(unittest.TestCase):
    def test_width_cases_map_with_area_statistics(self):
        yosys = Path(os.environ.get("SYN3D_TEST_YOSYS_BIN", ""))
        liberty = Path(os.environ.get("SYN3D_TEST_NANGATE45_LIB", ""))
        if not yosys.is_file() or not liberty.is_file():
            self.skipTest("Yosys width-mapping test paths are not configured")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for name, source in CASES.items():
                rtl = root / f"{name}.v"
                rtl.write_text(source, encoding="utf-8")
                rtl_arg = '"' + str(rtl).replace('"', '\\"') + '"'
                liberty_arg = '"' + str(liberty).replace('"', '\\"') + '"'
                command = (
                    f"read_verilog {rtl_arg}; synth -top {name} -flatten; "
                    f"dfflibmap -liberty {liberty_arg}; "
                    f"abc -liberty {liberty_arg}; stat -liberty {liberty_arg}"
                )
                result = subprocess.run(
                    [str(yosys), "-p", command], text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("Chip area for module", result.stdout)


if __name__ == "__main__":
    unittest.main()
