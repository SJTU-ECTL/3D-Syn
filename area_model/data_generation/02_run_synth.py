#!/usr/bin/env python3
"""Run Yosys synthesis for generated area-model RTL wrappers.

Batch mode processes one complete RTL group.  ``--target`` selects a single
existing wrapper for debugging or smoke validation:

    python 02_run_synth.py ff_fa --target DFF 8 \
      --technology nangate45 --platform-dir path/to/platform \
      --yosys-bin path/to/yosys --data-root path/to/work

    python 02_run_synth.py arith_same --target ADD 8 8 8 \
      --technology asap7 --platform-dir path/to/platform \
      --yosys-bin path/to/yosys --data-root path/to/work

For ``ff_fa``, the target has one width.  Arithmetic targets have A, B and Y
widths.  Outputs mirror the RTL hierarchy under
``02_netlist/<technology>/``.  Valid outputs are skipped unless ``--force`` is
supplied; every attempted sample receives an explicit JSON status record.
"""

import argparse
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import TRAINING_TECHNOLOGIES, resolve_training_platform


GROUPS = ("ff_fa", "arith_same", "arith_rand")


def yosys_path(path: Path) -> str:
    """Quote one filesystem path for the Yosys command language."""
    value = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def build_tcl(rtl_file: Path, top_module: str, output_file: Path,
              liberty_files: tuple[Path, ...], abc_liberty: Path,
              dff_liberty: Path, abc_fast: bool) -> str:
    """Build the original Yosys mapping sequence with explicit resources."""
    read_libs = "\n".join(
        f"read_liberty -ignore_miss_func {yosys_path(path)}"
        for path in liberty_files
    )
    liberty_args = " ".join(f"-liberty {yosys_path(path)}" for path in liberty_files)
    abc_option = " -fast" if abc_fast else ""
    return f"""\
{read_libs}
read_verilog {yosys_path(rtl_file)}
hierarchy -check -top {top_module}
proc
flatten
opt
techmap
opt
dfflibmap -liberty {yosys_path(dff_liberty)}
abc{abc_option} -liberty {yosys_path(abc_liberty)}
clean
stat {liberty_args}
write_verilog {yosys_path(output_file)}
"""


def locate_target(rtl_root: Path, group: str, values: list[str]) -> Path:
    expected = 1 if group == "ff_fa" else 3
    if len(values) != expected + 1:
        syntax = "MODULE W" if expected == 1 else "MODULE A B Y"
        raise ValueError(f"--target for {group} expects {syntax}")
    module = values[0].upper()
    widths = [int(value) for value in values[1:]]
    prefix = f"{module}_" + "_".join(str(value) for value in widths)
    directory = rtl_root / group / module
    matches = sorted(directory.glob(f"{prefix}_*.v"))
    exact = directory / f"{prefix}.v"
    if exact.is_file():
        matches.append(exact)
    matches = sorted(set(matches))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected one RTL matching {directory / (prefix + '*.v')}, "
            f"found {len(matches)}"
        )
    return matches[0]


def write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_one(task: tuple) -> tuple[str, str, str]:
    (rtl_file, rtl_root, output_root, status_root, yosys_bin,
     liberty_files, abc_liberty, dff_liberty, abc_fast, technology, force) = task
    relative = rtl_file.relative_to(rtl_root)
    output_file = output_root / relative
    log_file = output_file.with_suffix(".log")
    status_file = status_root / relative.with_suffix(".json")
    wrapper = rtl_file.stem
    base_status = {
        "stage": "synthesis",
        "technology": technology,
        "sample": wrapper,
        "input": str(rtl_file),
        "output": str(output_file),
        "log": str(log_file),
    }
    if output_file.is_file() and output_file.stat().st_size > 0 and not force:
        write_status(status_file, {**base_status, "status": "skipped"})
        return wrapper, "skipped", str(output_file)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    script = build_tcl(
        rtl_file, wrapper, output_file, liberty_files,
        abc_liberty, dff_liberty, abc_fast,
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ys", delete=False,
                                     encoding="utf-8") as stream:
        stream.write(script)
        script_path = Path(stream.name)
    try:
        with log_file.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                [str(yosys_bin), "-s", str(script_path)],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        success = completed.returncode == 0 and output_file.is_file() \
            and output_file.stat().st_size > 0
        state = "ok" if success else "failed"
        write_status(status_file, {
            **base_status,
            "status": state,
            "returncode": completed.returncode,
        })
        detail = str(output_file) if success else f"see {log_file}"
        return wrapper, state, detail
    except Exception as error:
        write_status(status_file, {**base_status, "status": "failed",
                                   "error": str(error)})
        return wrapper, "failed", str(error)
    finally:
        script_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthesize area-model RTL with Yosys.")
    parser.add_argument("group", choices=GROUPS, help="RTL group to process")
    parser.add_argument("--target", nargs="+", metavar=("MODULE", "WIDTH"),
                        help="one sample: MODULE W or MODULE A B Y")
    parser.add_argument("--technology", choices=sorted(TRAINING_TECHNOLOGIES),
                        required=True, help="target process")
    parser.add_argument("--platform-dir", type=Path, required=True,
                        help="TaiWei hybrid platform directory")
    parser.add_argument("--yosys-bin", type=Path, required=True,
                        help="Yosys executable for this run")
    parser.add_argument("--data-root", type=Path, required=True,
                        help="external root containing 01_rtl and outputs")
    parser.add_argument("--workers", type=int, default=1,
                        help="parallel Yosys processes (default: 1)")
    parser.add_argument("--force", action="store_true",
                        help="rerun samples with an existing netlist")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.workers <= 0:
        raise ValueError("workers must be positive")
    yosys_bin = args.yosys_bin.expanduser().resolve()
    if not yosys_bin.is_file() or not yosys_bin.stat().st_mode & 0o111:
        raise FileNotFoundError(f"Yosys executable not found: {yosys_bin}")
    platform = resolve_training_platform(args.platform_dir, args.technology)
    data_root = args.data_root.expanduser().resolve()
    rtl_root = data_root / "01_rtl"
    if args.target:
        rtl_files = [locate_target(rtl_root, args.group, args.target)]
    else:
        group_root = rtl_root / args.group
        rtl_files = sorted(group_root.rglob("*.v")) if group_root.is_dir() else []
    if not rtl_files:
        raise FileNotFoundError(f"no RTL inputs found for group {args.group}")

    output_root = data_root / "02_netlist" / args.technology
    status_root = data_root / "status/02_synth" / args.technology
    tasks = [
        (path, rtl_root, output_root, status_root, yosys_bin,
         platform["liberties"], platform["abc_liberty"],
         platform["dff_liberty"], platform["abc_fast"],
         args.technology, args.force)
        for path in rtl_files
    ]
    counts = {"ok": 0, "skipped": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for wrapper, state, detail in executor.map(run_one, tasks):
            counts[state] += 1
            print(f"[{state}] {wrapper}: {detail}")
    print("Done. " + " ".join(f"{key}={value}" for key, value in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
