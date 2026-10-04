#!/usr/bin/env python3
"""Create a run-local ASAP7 cell LEF with additional pin-access geometry.

For every upper-tier pin PORT containing M1_m rectangles, the script duplicates
those rectangles onto M3_m so OpenROAD detailed routing can find access points.
The input TaiWei LEF is never modified.

Usage:
    python 00_patch_cell_lef.py INPUT.lef OUTPUT.lef [--force]
"""

import argparse
from pathlib import Path


def patch_lef(input_path: Path, output_path: Path) -> None:
    """Duplicate M1_m pin rectangles onto M3_m in a derived LEF."""
    lines = input_path.read_text(encoding="utf-8", errors="replace").splitlines(
        keepends=True
    )
    output, in_port, collecting, pending_rects = [], False, False, []
    for line in lines:
        stripped = line.strip()
        if stripped == "PORT":
            in_port, collecting, pending_rects = True, False, []
            output.append(line)
            continue
        if stripped == "END" and in_port:
            if pending_rects:
                indent = pending_rects[0][:len(pending_rects[0]) - len(pending_rects[0].lstrip())]
                output.append(f"{indent[:-2] if len(indent) >= 2 else indent}LAYER M3_m ;\n")
                output.extend(pending_rects)
            in_port, collecting, pending_rects = False, False, []
            output.append(line)
            continue
        if in_port and stripped == "LAYER M1_m ;":
            collecting, pending_rects = True, []
            output.append(line)
            continue
        if in_port and collecting:
            if stripped.startswith("RECT"):
                pending_rects.append(line)
                output.append(line)
                continue
            if pending_rects:
                indent = pending_rects[0][:len(pending_rects[0]) - len(pending_rects[0].lstrip())]
                output.append(f"{indent[:-2] if len(indent) >= 2 else indent}LAYER M3_m ;\n")
                output.extend(pending_rects)
                pending_rects = []
            collecting = False
        output.append(line)
    output_path.write_text("".join(output), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patch an ASAP7 cell LEF copy.")
    parser.add_argument("input", type=Path, help="source TaiWei cell LEF")
    parser.add_argument("output", type=Path, help="run-local derived LEF")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing output file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.input.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"input LEF not found: {source}")
    if source == output:
        raise ValueError("input and output LEF must be different files")
    if output.exists() and not args.force:
        raise FileExistsError(f"output already exists; pass --force to replace: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    patch_lef(source, output)
    print(f"Done: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
