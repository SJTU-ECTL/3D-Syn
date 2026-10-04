# Yosys JSON Toolkit

This package provides the shared Python representation and transformation
operations used when 3D-Syn reads or writes Yosys JSON netlists.

## Responsibilities

- Preserve Yosys modules, ports, cells, memories, netnames, attributes, and
  extra fields during round trips.
- Represent integer signal bits and constant bits without changing their
  meaning.
- Validate structural contracts before publishing a transformed design.
- Provide explicit operations for ports, cells, netnames, bit references,
  module interfaces, cell-type mapping, and backend netname normalization.
- Centralize behavior shared by coarse synthesis, per-tier synthesis, and
  backend handoff.

The Python toolkit is independent from the C++ helper in `yosys-plugin/src`.
The Python package transforms serialized Yosys JSON between flow stages; the
C++ helper supports data handling inside the loadable Yosys passes.

## Module boundaries

| Module | Purpose |
| --- | --- |
| `model.py` | Lossless Python objects for the Yosys JSON schema |
| `io.py` | Loading, dumping, and format errors |
| `interface.py` | Port and module-interface helpers |
| `inspect.py` | Read-only netlist queries and operation contract checks |
| `edit.py` | Checked netlist editing and identifier allocation |
| `validate.py` | Structural validation and actionable diagnostics |
| `__init__.py` | Deliberate public API exports |

Callers should import only names exported by the package. Private object
layout and helper functions are not part of the flow contract.

## Usage contract

Load and validate a design before applying transformations, then write the
result through the package-level interface:

```python
from yosys_json_toolkit import dump_design, load_design, validate_design

design = load_design(input_path)
validate_design(design)
# Apply package-level inspection or editing operations here.
dump_design(design, output_path)
```

Integer bit IDs and the constant strings `0`, `1`, `x`, and `z` retain their
Yosys meanings. Unknown JSON fields are preserved in each model object's
`extras` dictionary so a load/dump round trip does not discard data owned by
another Yosys stage.
