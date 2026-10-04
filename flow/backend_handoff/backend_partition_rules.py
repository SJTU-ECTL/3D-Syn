"""Define platform mappings and library rules for backend partition updates."""

import json
import os
import re
import shlex
from pathlib import Path




KNOWN_TIERS = ("upper", "bottom")
TIER_TO_DIE = {"bottom": 0, "upper": 1}
DIE_TO_TIER = {0: "bottom", 1: "upper"}

BUFFER_LIKE_ALLOWLIST = {
    "asap7_3D": {
        "BUFx10_ASAP7_75t_R",
        "BUFx12_ASAP7_75t_R",
        "BUFx12f_ASAP7_75t_R",
        "BUFx16f_ASAP7_75t_R",
        "BUFx24_ASAP7_75t_R",
        "BUFx2_ASAP7_75t_R",
        "BUFx3_ASAP7_75t_R",
        "BUFx4_ASAP7_75t_R",
        "BUFx4f_ASAP7_75t_R",
        "BUFx5_ASAP7_75t_R",
        "BUFx6f_ASAP7_75t_R",
        "BUFx8_ASAP7_75t_R",
        "CKINVDCx10_ASAP7_75t_R",
        "CKINVDCx11_ASAP7_75t_R",
        "CKINVDCx12_ASAP7_75t_R",
        "CKINVDCx14_ASAP7_75t_R",
        "CKINVDCx16_ASAP7_75t_R",
        "CKINVDCx20_ASAP7_75t_R",
        "CKINVDCx5p33_ASAP7_75t_R",
        "CKINVDCx6p67_ASAP7_75t_R",
        "CKINVDCx8_ASAP7_75t_R",
        "CKINVDCx9p33_ASAP7_75t_R",
        "HB1xp67_ASAP7_75t_R",
        "HB2xp67_ASAP7_75t_R",
        "HB3xp67_ASAP7_75t_R",
        "HB4xp67_ASAP7_75t_R",
        "INVx11_ASAP7_75t_R",
        "INVx13_ASAP7_75t_R",
        "INVx1_ASAP7_75t_R",
        "INVx2_ASAP7_75t_R",
        "INVx3_ASAP7_75t_R",
        "INVx4_ASAP7_75t_R",
        "INVx5_ASAP7_75t_R",
        "INVx6_ASAP7_75t_R",
        "INVx8_ASAP7_75t_R",
        "INVxp33_ASAP7_75t_R",
        "INVxp67_ASAP7_75t_R",
    },
    "nangate45_3D": {
        "BUF_X1",
        "BUF_X16",
        "BUF_X2",
        "BUF_X32",
        "BUF_X4",
        "BUF_X8",
        "CLKBUF_X1",
        "CLKBUF_X2",
        "CLKBUF_X3",
        "INV_X1",
        "INV_X16",
        "INV_X2",
        "INV_X32",
        "INV_X4",
        "INV_X8",
    },
    "asap7_nangate45_3D": {
        "BUF_X1",
        "BUF_X2",
        "BUF_X4",
        "BUF_X8",
        "INV_X1",
        "INV_X2",
        "INV_X4",
        "INV_X8",
    },
}

PIN_LIBS = {
    "asap7_3D": [
        "platforms/asap7/lib/NLDM/asap7sc7p5t_AO_RVT_FF_nldm_211120.lib",
        "platforms/asap7/lib/NLDM/asap7sc7p5t_INVBUF_RVT_FF_nldm_220122.lib",
        "platforms/asap7/lib/NLDM/asap7sc7p5t_OA_RVT_FF_nldm_211120.lib",
        "platforms/asap7/lib/NLDM/asap7sc7p5t_SIMPLE_RVT_FF_nldm_211120.lib",
        "platforms/asap7/lib/NLDM/asap7sc7p5t_SEQ_RVT_FF_nldm_220123.lib",
        "platforms/asap7/lib/NLDM/asap7sc7p5t_DFFHQNH2V2X_RVT_TT_nldm_FAKE.lib",
        "platforms/asap7/lib/NLDM/asap7sc7p5t_DFFHQNV2X_RVT_TT_nldm_FAKE.lib",
    ],
    "nangate45_3D": [
        "platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib",
    ],
    "asap7_nangate45_3D": [
        "platforms/asap7_nangate45/lib/NangateOpenCellLibrary_typical.processed.lib",
        "platforms/asap7_nangate45_3D/lib_upper/NLDM/asap7sc7p5t_AO_RVT_FF_nldm_211120.upper.processed.lib",
        "platforms/asap7_nangate45_3D/lib_upper/NLDM/asap7sc7p5t_INVBUF_RVT_FF_nldm_220122.upper.processed.lib",
        "platforms/asap7_nangate45_3D/lib_upper/NLDM/asap7sc7p5t_OA_RVT_FF_nldm_211120.upper.processed.lib",
        "platforms/asap7_nangate45_3D/lib_upper/NLDM/asap7sc7p5t_SIMPLE_RVT_FF_nldm_211120.upper.processed.lib",
        "platforms/asap7_nangate45_3D/lib_upper/NLDM/asap7sc7p5t_SEQ_RVT_FF_nldm_220123.upper.processed.lib",
    ],
}


def fail(message):
    raise AssertionError(message)


def norm_name(value):
    text = str(value).strip()
    if text.startswith("\\"):
        text = text[1:]
    return text.replace("\\[", "[").replace("\\]", "]")


def strip_tier_suffix(master):
    if master.endswith("_upper"):
        return master[:-6]
    if master.endswith("_bottom"):
        return master[:-7]
    return master


def tier_from_master(master):
    if master.endswith("_upper"):
        return "upper"
    if master.endswith("_bottom"):
        return "bottom"
    return ""

def load_map_json(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    cells = data.get("cells")
    if not isinstance(cells, dict) or not cells:
        fail("map.json missing non-empty cells dict: {}".format(path))

    base_to_entry = {}
    macro_to_base = {}
    for key, entry in cells.items():
        if not isinstance(entry, dict):
            fail("map.json entry for {} is not a dict".format(key))
        raw_base = entry.get("base", key)
        if isinstance(raw_base, dict):
            base = raw_base.get("macro")
        else:
            base = raw_base
        if not isinstance(base, str) or not base:
            fail("map.json entry {} missing base.macro".format(key))
        bottom = entry.get("bottom", {})
        upper = entry.get("upper", {})
        if not isinstance(bottom, dict) or not bottom.get("macro"):
            fail("map.json entry {} missing bottom.macro".format(base))
        if not isinstance(upper, dict) or not upper.get("macro"):
            fail("map.json entry {} missing upper.macro".format(base))
        base_to_entry[base] = entry
        macro_to_base[base] = base
        macro_to_base[bottom["macro"]] = base
        macro_to_base[upper["macro"]] = base
    return base_to_entry, macro_to_base


def liberty_blocks(text, kind):
    pat = re.compile(r"\b" + re.escape(kind) + r"\s*\(([^)]+)\)\s*\{")
    pos = 0
    while True:
        match = pat.search(text, pos)
        if not match:
            break
        name = match.group(1).strip()
        i = match.end() - 1
        depth = 0
        j = i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    yield name, text[i + 1 : j]
                    pos = j + 1
                    break
            j += 1
        else:
            break


def load_pin_directions(repo_root, enablement):
    pin_dirs = {}
    lib_paths = [repo_root / rel for rel in PIN_LIBS[enablement]]
    lib_paths.extend(Path(path) for path in shlex.split(os.environ.get("ADDITIONAL_LIBS", "")))
    for path in lib_paths:
        if not path.is_absolute():
            path = repo_root / path
        if not path.is_file():
            fail("Required liberty file not found: {}".format(path))
        text = path.read_text(encoding="utf-8", errors="ignore")
        for cell_name, cell_body in liberty_blocks(text, "cell"):
            pins = pin_dirs.setdefault(cell_name, {})
            for group_kind in ("pin", "bus"):
                for pin_name, pin_body in liberty_blocks(cell_body, group_kind):
                    match = re.search(r"\bdirection\s*:\s*([^;]+);", pin_body)
                    if not match:
                        continue
                    direction = match.group(1).strip().lower()
                    previous = pins.get(pin_name)
                    if previous is not None and previous != direction:
                        fail(
                            "Conflicting pin direction for {}/{}: {} vs {}".format(
                                cell_name, pin_name, previous, direction
                            )
                        )
                    pins[pin_name] = direction
    if not pin_dirs:
        fail("No pin directions loaded for {}".format(enablement))
    return pin_dirs


def resolve_pin_direction(cell, pin, pin_dirs):
    if cell.port_directions and pin in cell.port_directions:
        return cell.port_directions[pin].lower()
    master = strip_tier_suffix(cell.type)
    if master in pin_dirs and pin in pin_dirs[master]:
        return pin_dirs[master][pin]
    if cell.type in pin_dirs and pin in pin_dirs[cell.type]:
        return pin_dirs[cell.type][pin]
    fail("Cannot resolve pin direction for {} pin {} type {}".format(cell.name, pin, cell.type))

def base_master_for_mode(master, enablement, macro_to_base):
    stripped = strip_tier_suffix(master)
    if enablement == "asap7_nangate45_3D":
        if stripped in macro_to_base:
            return macro_to_base[stripped]
        fail("Cannot map hetero master {} to map.json base cell".format(master))
    return stripped


def target_macro_for_mode(base, tier, enablement, base_to_entry):
    if enablement == "asap7_nangate45_3D":
        entry = base_to_entry.get(base)
        if not entry:
            fail("Base cell {} missing in map.json".format(base))
        macro = entry[tier]["macro"]
        if not macro:
            fail("Base cell {} missing {} macro".format(base, tier))
        return macro
    suffix = "_upper" if tier == "upper" else "_bottom"
    return base + suffix
