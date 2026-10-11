#!/usr/bin/env python3
"""Generate parameterized Verilog wrappers for area-model training.

The three groups preserve the original sampling contract:

``ff_fa``
    FA and sequential wrappers.  Batch mode enumerates one shared width.
``arith_same``
    Arithmetic wrappers with A_WIDTH=B_WIDTH=Y_WIDTH.
``arith_rand``
    Arithmetic wrappers sampled from independent A/B/Y widths with a fixed
    seed and with same-width points excluded.

Filenames encode the full five-slot width vector, for example
``DFF_8_8_0_0_0.v`` and ``ADD_8_16_32_0_0.v``.  Existing files are skipped
unless ``--force`` is supplied, so interrupted runs can be resumed.

Examples:
    python 01_generate_rtl.py ff_fa --target DFF 2 \
      --simlib ../resources/simlib.v --port-schema path/to/port_schema.json \
      --data-root path/to/work

    python 01_generate_rtl.py arith_rand --width-max 32 \
      --random-samples 64 --seed 42 --simlib ... --port-schema ... \
      --data-root path/to/work
"""

import argparse
import json
import random
import re
from pathlib import Path


FF_FA_MODULES = [
    "FA", "DFF", "DFFE", "DFFSR", "DFFSRE", "ADFF", "ADFFE",
    "SDFF", "SDFFE", "SDFFCE",
]
ARITH_MODULES = [
    "ADD", "SUB", "MUL", "DIV", "MOD", "ALU", "MACC",
]
GROUPS = ("ff_fa", "arith_same", "arith_rand")
VECTOR_LEN = 5


def sanitize_name(raw: str) -> str:
    name = raw.strip().lstrip("\\").lstrip("$")
    return re.sub(r"[^A-Za-z0-9_]", "_", name).upper()


def extract_modules(source: str) -> dict:
    """Extract supported Yosys simulation modules without changing bodies."""
    wanted = set(FF_FA_MODULES + ARITH_MODULES)
    pattern = re.compile(r"(module\s+(\\?\$?[\w\\$]+)\s*\(.*?endmodule)",
                         re.DOTALL)
    modules = {}
    for match in pattern.finditer(source):
        raw_name = match.group(2)
        safe_name = sanitize_name(raw_name)
        if safe_name in wanted:
            modules[safe_name] = match.group(1).replace(raw_name, safe_name, 1)
    return modules


def resolve_params(schema: dict, width_values: dict) -> dict:
    """Return only width parameters used by the module, in port order."""
    result = {}
    for port in schema["parameterized_ports"]:
        parameter = port["width_param"]
        if parameter not in result and parameter in width_values:
            result[parameter] = width_values[parameter]
    return result


def make_full_vec(schema: dict, param_values: dict) -> list[int]:
    """Expand parameter values into the stable five-slot width vector."""
    vector = [0] * VECTOR_LEN
    for port in schema["parameterized_ports"]:
        slot = port["slot_idx"]
        if not 0 <= slot < VECTOR_LEN:
            raise ValueError(f"invalid slot_idx {slot} for port {port['name']}")
        vector[slot] = param_values[port["width_param"]]
    return vector


def build_wrapper(module_name: str, schema: dict,
                  param_values: dict) -> tuple[str, str]:
    """Return the wrapper name and Verilog module body."""
    suffix = "_".join(str(value) for value in make_full_vec(schema, param_values))
    wrapper_name = f"{module_name}_{suffix}"
    parameterized = schema["parameterized_ports"]
    fixed = schema["fixed_ports"]
    ports = parameterized + fixed
    lines = [f"module {wrapper_name} ({', '.join(p['name'] for p in ports)});"]
    for port in parameterized:
        width = param_values[port["width_param"]]
        lines.append(f"  {port['direction']} [{width - 1}:0] {port['name']};")
    for port in fixed:
        lines.append(f"  {port['direction']} {port['name']};")
    lines.append("")
    parameter_text = ", ".join(f".{name}({value})"
                               for name, value in param_values.items())
    port_text = ", ".join(f".{port['name']}({port['name']})" for port in ports)
    lines.extend([
        f"  {module_name} #({parameter_text}) u0 ({port_text});",
        "",
        "endmodule",
    ])
    return wrapper_name, "\n".join(lines)


def width_points(group: str, width_max: int, random_samples: int,
                 seed: int) -> list[tuple[int, ...]]:
    """Build the deterministic width tuples for one generation group."""
    if group in ("ff_fa", "arith_same"):
        return [(width,) if group == "ff_fa" else (width, width, width)
                for width in range(1, width_max + 1)]

    diagonal = {(width, width, width) for width in range(1, width_max + 1)}
    available = width_max ** 3 - len(diagonal)
    if random_samples > available:
        raise ValueError(
            f"random_samples={random_samples} exceeds {available} non-diagonal points"
        )
    rng = random.Random(seed)
    samples, seen = [], set()
    while len(samples) < random_samples:
        point = tuple(rng.randint(1, width_max) for _ in range(3))
        if point not in diagonal and point not in seen:
            samples.append(point)
            seen.add(point)
    return samples


def parameter_values(group: str, widths: tuple[int, ...]) -> dict:
    if group == "ff_fa":
        width = widths[0]
        return {"WIDTH": width, "A_WIDTH": width,
                "B_WIDTH": width, "Y_WIDTH": width}
    return {"A_WIDTH": widths[0], "B_WIDTH": widths[1], "Y_WIDTH": widths[2]}


def generate_one(module_name: str, widths: tuple[int, ...], group: str,
                 modules: dict, schema: dict, rtl_root: Path,
                 status_root: Path, force: bool) -> str:
    if module_name not in modules:
        raise KeyError(f"module {module_name} is absent from simlib")
    if module_name not in schema:
        raise KeyError(f"module {module_name} is absent from port schema")
    params = resolve_params(schema[module_name], parameter_values(group, widths))
    wrapper_name, wrapper = build_wrapper(module_name, schema[module_name], params)
    output = rtl_root / group / module_name / f"{wrapper_name}.v"
    state = "skipped" if output.is_file() and output.stat().st_size and not force else "ok"
    if state == "ok":
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(modules[module_name] + "\n\n" + wrapper + "\n",
                          encoding="utf-8")
    status = status_root / group / module_name / f"{wrapper_name}.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(json.dumps({
        "stage": "rtl_generation",
        "status": state,
        "group": group,
        "module": module_name,
        "wrapper": wrapper_name,
        "widths": list(widths),
        "output": str(output),
    }, indent=2) + "\n", encoding="utf-8")
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate area-model RTL wrappers.")
    parser.add_argument("group", choices=GROUPS, help="sample group to generate")
    parser.add_argument("--target", nargs="+", metavar=("MODULE", "WIDTH"),
                        help="one case: MODULE W or MODULE A B Y")
    parser.add_argument("--simlib", type=Path, required=True,
                        help="Yosys simulation-library Verilog")
    parser.add_argument("--port-schema", type=Path, required=True,
                        help="port_schema.json from step 00")
    parser.add_argument("--data-root", type=Path, required=True,
                        help="external root for all generated data")
    parser.add_argument("--width-max", type=int, default=32,
                        help="maximum generated width (default: 32)")
    parser.add_argument("--random-samples", type=int, default=64,
                        help="random points per arithmetic module (default: 64)")
    parser.add_argument("--seed", type=int, default=42,
                        help="random sampling seed (default: 42)")
    parser.add_argument("--force", action="store_true",
                        help="replace existing RTL files")
    return parser.parse_args()


def parse_target(group: str, values: list[str]) -> tuple[str, tuple[int, ...]]:
    expected_widths = 1 if group == "ff_fa" else 3
    if len(values) != expected_widths + 1:
        expected = "MODULE W" if expected_widths == 1 else "MODULE A B Y"
        raise ValueError(f"--target for {group} expects {expected}")
    module = values[0].upper()
    allowed = FF_FA_MODULES if group == "ff_fa" else ARITH_MODULES
    if module not in allowed:
        raise ValueError(f"module {module} is not valid for group {group}")
    widths = tuple(int(value) for value in values[1:])
    if any(width <= 0 for width in widths):
        raise ValueError("all widths must be positive")
    return module, widths


def main() -> int:
    args = parse_args()
    if args.width_max <= 0 or args.random_samples < 0:
        raise ValueError("width-max must be positive and random-samples non-negative")
    simlib = args.simlib.expanduser().resolve()
    schema_path = args.port_schema.expanduser().resolve()
    if not simlib.is_file() or not schema_path.is_file():
        raise FileNotFoundError("simlib and port-schema must both exist")
    modules = extract_modules(simlib.read_text(encoding="utf-8", errors="replace"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    data_root = args.data_root.expanduser().resolve()
    rtl_root = data_root / "01_rtl"
    status_root = data_root / "status/01_rtl"

    if args.target:
        cases = [parse_target(args.group, args.target)]
    else:
        selected = FF_FA_MODULES if args.group == "ff_fa" else ARITH_MODULES
        points = width_points(args.group, args.width_max, args.random_samples, args.seed)
        cases = [(module, point) for module in selected for point in points]

    counts = {"ok": 0, "skipped": 0}
    for module, widths in cases:
        state = generate_one(module, widths, args.group, modules, schema,
                             rtl_root, status_root, args.force)
        counts[state] += 1
        print(f"[{state}] {module} {' '.join(map(str, widths))}")
    print(f"Done. generated={counts['ok']} skipped={counts['skipped']} root={rtl_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
