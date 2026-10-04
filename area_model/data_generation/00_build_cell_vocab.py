#!/usr/bin/env python3
"""Build a cell vocabulary by enumerating supported-platform Liberty cells.

Each cell name receives one deterministic integer identifier.  The command can
read one or both TaiWei process views and writes only the explicitly selected
output file.

Examples:
    python 00_build_cell_vocab.py \
      --platform-dir path/to/TaiWei-Pin-3D/platforms/asap7_nangate45_3D \
      --output path/to/work/00_resources/cell_type_vocab.json

    python 00_build_cell_vocab.py --technology asap7 ...
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import TRAINING_TECHNOLOGIES, resolve_training_platform


def extract_cell_names(lib_path: Path) -> list[str]:
    """Return all cell names found in one Liberty file."""
    names = []
    with lib_path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            match = re.match(r"^\s*cell\s*\(\s*(\S+?)\s*\)\s*\{", line)
            if match:
                names.append(match.group(1))
    return names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a deterministic cell vocabulary from TaiWei Liberty files."
    )
    parser.add_argument("--platform-dir", type=Path, required=True,
                        help="TaiWei hybrid platform directory")
    parser.add_argument("--technology", choices=sorted(TRAINING_TECHNOLOGIES),
                        action="append", dest="technologies",
                        help="process to include; repeatable (default: both)")
    parser.add_argument("--output", type=Path, required=True,
                        help="output cell_type_vocab.json")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing output file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output.expanduser().resolve()
    if output.exists() and not args.force:
        raise FileExistsError(f"output already exists; pass --force to replace: {output}")

    technologies = args.technologies or list(TRAINING_TECHNOLOGIES)
    vocabulary = {}
    for technology in technologies:
        platform = resolve_training_platform(args.platform_dir, technology)
        print(f"[{technology}]")
        for lib_path in platform["liberties"]:
            names = extract_cell_names(lib_path)
            new_names = [name for name in names if name not in vocabulary]
            for name in new_names:
                vocabulary[name] = len(vocabulary)
            print(f"  {lib_path.name}: {len(names)} cells ({len(new_names)} new)")

    if not vocabulary:
        raise RuntimeError("no cells were found in the selected Liberty files")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(vocabulary, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"Done. {len(vocabulary)} unique cells -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
