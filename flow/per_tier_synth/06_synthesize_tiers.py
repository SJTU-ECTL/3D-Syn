#!/usr/bin/env python3
"""Select and run the configured per-tier Genus synthesis strategy."""

from flow_config import SYNTH_MODE
from modules.genus_heterogeneous import run_heterogeneous_genus
from modules.genus_homogeneous import run_homogeneous_genus


def main():
    if SYNTH_MODE == "genus_homo":
        run_homogeneous_genus()
        return
    if SYNTH_MODE == "genus_hetero":
        run_heterogeneous_genus()
        return
    raise AssertionError("Unsupported SYNTH_MODE: {}".format(SYNTH_MODE))


if __name__ == "__main__":
    main()

