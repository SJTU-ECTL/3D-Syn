#!/usr/bin/env python3
"""Validate the frontend-to-backend handoff manifest and all referenced files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+$")
DESIGN_NAME_ASSIGNMENT = re.compile(
    r"^\s*export\s+DESIGN_NAME\s*(?:[?:+]?=)\s*([A-Za-z0-9_.-]+)\s*$",
    re.MULTILINE,
)


class HandoffValidationError(ValueError):
    """Raised when a backend handoff violates the published contract."""


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HandoffValidationError(f"{label} must be a JSON object")
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HandoffValidationError(f"{label} must be a non-empty string")
    return value


def _validate_file(
    value: Any,
    label: str,
    results_dir: Path,
    expected_name: str,
) -> Path:
    path = Path(_require_text(value, label)).resolve()
    if path.parent != results_dir:
        raise HandoffValidationError(
            f"{label} must be directly under results directory {results_dir}: {path}"
        )
    if path.name != expected_name:
        raise HandoffValidationError(
            f"{label} has unexpected filename; expected {expected_name}, got {path.name}"
        )
    if not path.is_file() or path.stat().st_size == 0:
        raise HandoffValidationError(f"missing or empty {label}: {path}")
    return path


def _read_design_name(design_config: Path) -> str:
    design_config = design_config.resolve()
    if not design_config.is_file() or design_config.stat().st_size == 0:
        raise HandoffValidationError(f"missing or empty 3D design config: {design_config}")
    try:
        text = design_config.read_text(encoding="utf-8")
    except OSError as exc:
        raise HandoffValidationError(f"cannot read 3D design config: {exc}") from exc
    matches = DESIGN_NAME_ASSIGNMENT.findall(text)
    if len(matches) != 1:
        raise HandoffValidationError(
            f"3D design config must contain exactly one literal DESIGN_NAME assignment: "
            f"{design_config}"
        )
    return matches[0]


def validate_backend_handoff(
    manifest_path: Path,
    results_dir: Path,
    design_config: Path,
    expected_platform: str,
    expected_flow_variant: str,
    expected_backend: str,
) -> dict[str, Any]:
    """Validate one published handoff and return its normalized identity."""
    results_dir = results_dir.resolve()
    manifest_path = manifest_path.resolve()
    expected_manifest = results_dir / "backend_handoff_manifest.json"
    if manifest_path != expected_manifest:
        raise HandoffValidationError(
            f"manifest must be {expected_manifest}, got {manifest_path}"
        )
    if not manifest_path.is_file() or manifest_path.stat().st_size == 0:
        raise HandoffValidationError(f"missing or empty backend handoff manifest: {manifest_path}")

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HandoffValidationError(f"cannot read backend handoff manifest: {exc}") from exc
    payload = _require_mapping(payload, "backend handoff manifest")

    if payload.get("stage") != "3dsyn_backend_handoff":
        raise HandoffValidationError(
            f"unexpected handoff stage: {payload.get('stage')!r}"
        )
    if payload.get("platform") != expected_platform:
        raise HandoffValidationError(
            f"handoff platform mismatch: expected {expected_platform}, "
            f"got {payload.get('platform')!r}"
        )
    if payload.get("flow_variant") != expected_flow_variant:
        raise HandoffValidationError(
            f"handoff flow variant mismatch: expected {expected_flow_variant}, "
            f"got {payload.get('flow_variant')!r}"
        )
    manifest_backend = payload.get("backend")
    if manifest_backend is not None and manifest_backend != expected_backend:
        raise HandoffValidationError(
            f"handoff backend mismatch: expected {expected_backend}, got {manifest_backend!r}"
        )

    design_name = _require_text(payload.get("design"), "handoff design")
    if not SAFE_TOKEN.fullmatch(design_name):
        raise HandoffValidationError(f"handoff design contains unsupported characters: {design_name}")
    configured_design_name = _read_design_name(design_config)
    if design_name != configured_design_name:
        raise HandoffValidationError(
            f"handoff design mismatch: 3D config expects {configured_design_name}, "
            f"manifest contains {design_name}"
        )

    inputs = _require_mapping(payload.get("inputs"), "handoff inputs")
    intermediate = _require_mapping(
        payload.get("intermediate_outputs"), "handoff intermediate_outputs"
    )
    outputs = _require_mapping(payload.get("backend_outputs"), "handoff backend_outputs")

    input_names = {
        "base_netlist": "base.v",
        "backend_input_netlist": "base.backend.v",
        "partition": "partition_from_flow.txt",
        "sdc": "1_synth.sdc",
    }
    for key, expected_name in input_names.items():
        _validate_file(inputs.get(key), f"handoff inputs.{key}", results_dir, expected_name)

    intermediate_names = {
        "preplace_netlist": "2_2_floorplan_io.backend.v",
        "preplace_def": "2_2_floorplan_io.backend.def",
        "partition": "partition.txt",
    }
    for key, expected_name in intermediate_names.items():
        _validate_file(
            intermediate.get(key),
            f"handoff intermediate_outputs.{key}",
            results_dir,
            expected_name,
        )
    if "preplace_odb" in intermediate:
        _validate_file(
            intermediate["preplace_odb"],
            "handoff intermediate_outputs.preplace_odb",
            results_dir,
            "2_2_floorplan_io.backend.odb",
        )

    netlist = _validate_file(
        outputs.get("netlist"),
        "handoff backend_outputs.netlist",
        results_dir,
        f"{design_name}_3D.fp.v",
    )
    floorplan = _validate_file(
        outputs.get("def"),
        "handoff backend_outputs.def",
        results_dir,
        f"{design_name}_3D.fp.def",
    )
    return {
        "design": design_name,
        "backend": manifest_backend or expected_backend,
        "netlist": str(netlist),
        "def": str(floorplan),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--design-config", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--flow-variant", required=True)
    parser.add_argument("--backend", required=True, choices=("ord", "cds"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        resolved = validate_backend_handoff(
            args.manifest,
            args.results_dir,
            args.design_config,
            args.platform,
            args.flow_variant,
            args.backend,
        )
    except HandoffValidationError as exc:
        print(f"[3D-Syn] ERROR: Invalid backend handoff: {exc}", file=sys.stderr)
        return 2
    print(
        "[3D-Syn] Backend handoff validated: "
        f"design={resolved['design']} platform={args.platform} backend={resolved['backend']}"
    )
    print(f"[3D-Syn] Backend handoff netlist: {resolved['netlist']}")
    print(f"[3D-Syn] Backend handoff floorplan: {resolved['def']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
