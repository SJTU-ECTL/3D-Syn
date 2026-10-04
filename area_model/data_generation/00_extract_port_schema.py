#!/usr/bin/env python3
"""Parse simlib.v and emit the canonical wrapper-port schema.

Slot assignment follows declaration order.  Each parameterized port receives
its own slot even when several ports share one WIDTH parameter; wrapper
generation later assigns the same value to every port using that parameter.

Examples:
    FA  (A,B,C,X,Y share WIDTH) -> FA_8_8_8_8_8
    ALU (A_WIDTH/B_WIDTH/WIDTH) -> ALU_12_23_5_5_5
    DFF (D,Q share WIDTH)       -> DFF_8_8_0_0_0

Usage:
    python 00_extract_port_schema.py \
      --simlib ../resources/simlib.v \
      --output path/to/work/00_resources/port_schema.json
"""

import argparse
import json
import re
from pathlib import Path


TARGET_MODULES = [
    "ADD", "SUB", "MUL", "DIV", "MOD", "DIVFLOOR", "MODFLOOR",
    "ALU", "FA", "MACC", "DFF", "DFFE", "DFFSR", "DFFSRE",
    "ADFF", "ADFFE", "SDFF", "SDFFE", "SDFFCE",
]
TARGET_SET = set(TARGET_MODULES)


def sanitize_name(raw: str) -> str:
    name = raw.strip().lstrip("\\").lstrip("$")
    return re.sub(r"[^A-Za-z0-9_]", "_", name).upper()


def extract_modules(source: str) -> dict:
    pattern = re.compile(r"(module\s+(\\?\$?[\w\\$]+)\s*\(.*?endmodule)",
                         re.DOTALL)
    modules = {}
    for match in pattern.finditer(source):
        raw_name = match.group(2)
        safe_name = sanitize_name(raw_name)
        if safe_name in TARGET_SET:
            modules[safe_name] = match.group(1).replace(raw_name, safe_name, 1)
    return modules


def parse_port_schema(module_text: str) -> dict:
    """Extract parameterized and fixed ports from one simulation module."""
    width_params = set(re.findall(r"parameter\s+(\w*WIDTH\w*)\s*=", module_text))
    clean = re.sub(r"\b(function|task)\b.*?\bend(function|task)\b", "",
                   module_text, flags=re.DOTALL)
    parameterized, fixed, seen = [], [], set()
    for direction in ("input", "output"):
        pattern = rf"\b{direction}\b\s*(?:reg\s+)?(\[([^\]]*)\])?\s*([\w][\w\s,]*?)\s*;"
        for match in re.finditer(pattern, clean):
            bracket = match.group(2) or ""
            width_param = next((item for item in width_params if item in bracket), None)
            for name in (item.strip() for item in match.group(3).split(",")):
                if not name or name in seen:
                    continue
                seen.add(name)
                if width_param:
                    parameterized.append({
                        "name": name,
                        "direction": direction,
                        "width_param": width_param,
                        "slot_idx": len(parameterized),
                    })
                else:
                    fixed.append({"name": name, "direction": direction})
    return {"parameterized_ports": parameterized, "fixed_ports": fixed}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract a wrapper-port schema.")
    parser.add_argument("--simlib", type=Path, required=True,
                        help="Yosys simulation-library Verilog")
    parser.add_argument("--output", type=Path, required=True,
                        help="output port_schema.json")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing output file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    simlib = args.simlib.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not simlib.is_file():
        raise FileNotFoundError(f"simlib not found: {simlib}")
    if output.exists() and not args.force:
        raise FileExistsError(f"output already exists; pass --force to replace: {output}")

    modules = extract_modules(simlib.read_text(encoding="utf-8", errors="replace"))
    schema = {}
    for name in TARGET_MODULES:
        if name not in modules:
            print(f"[WARN] {name} was not found in {simlib}")
            continue
        schema[name] = parse_port_schema(modules[name])
        print(f"{name:20s} parameterized={len(schema[name]['parameterized_ports'])} "
              f"fixed={len(schema[name]['fixed_ports'])}")
    missing = sorted(TARGET_SET - set(schema))
    if missing:
        raise RuntimeError(f"required modules missing from simlib: {', '.join(missing)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"Done. {len(schema)} modules -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
