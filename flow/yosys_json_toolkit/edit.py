"""Checked editing operations for typed Yosys JSON designs."""

from collections import OrderedDict, namedtuple
from copy import deepcopy

from .inspect import (
    _assert_backend_netname_input_contract,
    _assert_backend_netname_runtime_result,
    _is_backend_safe_name,
    max_integer_bit,
)
from .model import Cell, Module, Netname, Port

NetnameNormalizationResult = namedtuple(
    "NetnameNormalizationResult", ["mapping", "selected_count", "collision_count"]
)
def _backend_safe_netname(name):
    """Apply the character policy used by syn3d_normalize_instance_names."""
    assert isinstance(name, str), "Netname must be a string, got {!r}.".format(type(name))
    unescaped = name[1:] if name.startswith("\\") else name
    normalized = []
    previous_was_underscore = False
    for character in unescaped:
        is_alnum = ("a" <= character <= "z") or ("A" <= character <= "Z") or ("0" <= character <= "9")
        replacement = character if is_alnum or character == "_" else "_"
        if replacement == "_":
            if not previous_was_underscore:
                normalized.append(replacement)
            previous_was_underscore = True
        else:
            normalized.append(replacement)
            previous_was_underscore = False

    candidate = "".join(normalized)
    if not candidate:
        candidate = "net"
    if "0" <= candidate[0] <= "9":
        candidate = "net_" + candidate
    assert _is_backend_safe_name(candidate)
    return candidate

def normalize_backend_internal_netnames(design, module_name):
    """Normalize backslash-containing internal netnames for backend handoff."""
    from .validate import validate_design

    validate_design(design)
    assert module_name in design.modules, "Backend module {!r} does not exist.".format(module_name)
    module = design.modules[module_name]
    selected_names = _assert_backend_netname_input_contract(module)
    original_netnames = OrderedDict(module.netnames)
    selected_netname_data = OrderedDict(
        (name, module.netnames[name].to_dict()) for name in selected_names
    )
    original_nonnet_container_ids = tuple(
        id(value)
        for value in [
            module.attributes,
            module.parameter_default_values,
            module.ports,
            module.cells,
            module.memories,
            module.extras,
        ]
    )
    original_nonnet_object_ids = (
        tuple((name, id(port)) for name, port in module.ports.items()),
        tuple((name, id(cell)) for name, cell in module.cells.items()),
        tuple((name, id(memory)) for name, memory in module.memories.items()),
    )

    reserved_names = (
        (set(module.netnames) - set(selected_names))
        | set(module.ports)
        | set(module.cells)
        | set(module.memories)
    )
    mapping = OrderedDict()
    collision_count = 0
    for old_name in sorted(selected_names):
        candidate = _backend_safe_netname(old_name)
        final_name = candidate
        suffix = 1
        while final_name in reserved_names:
            collision_count += 1
            final_name = "{}_{}".format(candidate, suffix)
            suffix += 1
        mapping[old_name] = final_name
        reserved_names.add(final_name)

    if mapping:
        renamed_netnames = OrderedDict()
        for old_name, netname in module.netnames.items():
            new_name = mapping.get(old_name, old_name)
            assert new_name not in renamed_netnames, "Duplicate normalized netname {!r}.".format(new_name)
            if new_name != old_name:
                netname.name = new_name
            renamed_netnames[new_name] = netname
        module.netnames = renamed_netnames

    _assert_backend_netname_runtime_result(
        module,
        original_netnames,
        selected_netname_data,
        original_nonnet_container_ids,
        original_nonnet_object_ids,
        mapping,
    )
    validate_design(design)
    return NetnameNormalizationResult(mapping, len(selected_names), collision_count)


def clone_design(design):
    return design.copy()


def clone_module(module):
    return module.copy()


def add_module(design, module, clone=True):
    if not isinstance(module, Module):
        raise TypeError("Expected Module, got {!r}.".format(type(module)))
    if module.name in design.modules:
        raise ValueError("Module {!r} already exists in design.".format(module.name))
    new_module = clone_module(module) if clone else module
    design.modules[new_module.name] = new_module
    return new_module


def merge_modules(dst_design, src_design, module_names):
    merged = []
    seen = set()
    for module_name in module_names:
        if module_name in seen:
            raise ValueError("Module {!r} was requested more than once for merge.".format(module_name))
        seen.add(module_name)
        if module_name not in src_design.modules:
            raise KeyError("Module {!r} does not exist in the source design.".format(module_name))
        merged.append(add_module(dst_design, src_design.modules[module_name], clone=True))
    return merged


def add_port(module, name, direction, bits, offset=None, upto=None, signed=None, extras=None):
    if name in module.ports:
        raise ValueError("Port {!r} already exists in module {!r}.".format(name, module.name))
    port = Port(name=name, direction=direction, bits=list(bits), offset=offset, upto=upto, signed=signed)
    if extras:
        port.extras.update(deepcopy(extras))
    module.ports[name] = port
    return port


def add_netname(
    module,
    name,
    bits,
    hide_name=0,
    attributes=None,
    offset=None,
    upto=None,
    signed=None,
    extras=None,
):
    if name in module.netnames:
        raise ValueError("Netname {!r} already exists in module {!r}.".format(name, module.name))
    netname = Netname(
        name=name,
        hide_name=hide_name,
        bits=list(bits),
        attributes=deepcopy(attributes or {}),
        offset=offset,
        upto=upto,
        signed=signed,
    )
    if extras:
        netname.extras.update(deepcopy(extras))
    module.netnames[name] = netname
    return netname


def add_port_with_netname(
    module,
    name,
    direction,
    bits,
    offset=None,
    upto=None,
    signed=None,
    port_extras=None,
    netname_hide_name=0,
    netname_attributes=None,
    netname_extras=None,
):
    """Add a module port and a same-name netname over the same bits.

    This is a common Yosys JSON pattern for top-level interfaces. The helper
    keeps the two objects synchronized at creation time and deliberately fails
    if either name already exists.
    """
    if name in module.ports:
        raise ValueError("Port {!r} already exists in module {!r}.".format(name, module.name))
    if name in module.netnames:
        raise ValueError("Netname {!r} already exists in module {!r}.".format(name, module.name))
    bits = list(bits)
    port = add_port(
        module,
        name,
        direction,
        bits,
        offset=offset,
        upto=upto,
        signed=signed,
        extras=port_extras,
    )
    netname = add_netname(
        module,
        name,
        bits,
        hide_name=netname_hide_name,
        attributes=netname_attributes,
        offset=offset,
        upto=upto,
        signed=signed,
        extras=netname_extras,
    )
    return port, netname


def add_cell(
    module,
    name,
    cell_type,
    connections,
    hide_name=0,
    parameters=None,
    attributes=None,
    port_directions=None,
    extras=None,
):
    if name in module.cells:
        raise ValueError("Cell {!r} already exists in module {!r}.".format(name, module.name))
    cell = Cell(
        name=name,
        hide_name=hide_name,
        cell_type=cell_type,
        parameters=deepcopy(parameters or {}),
        attributes=deepcopy(attributes or {}),
        port_directions=deepcopy(port_directions or {}),
        connections={key: list(bits) for key, bits in connections.items()},
    )
    if extras:
        cell.extras.update(deepcopy(extras))
    module.cells[name] = cell
    return cell


def remove_port(module, name):
    return module.ports.pop(name)


def remove_netname(module, name):
    return module.netnames.pop(name)


def remove_cell(module, name):
    return module.cells.pop(name)


def rename_port(module, old_name, new_name):
    if new_name in module.ports:
        raise ValueError("Port {!r} already exists in module {!r}.".format(new_name, module.name))
    port = module.ports.pop(old_name)
    port.name = new_name
    module.ports[new_name] = port
    return port


def rename_port_and_netname(module, old_name, new_name):
    """Rename a port and its same-name netname as one checked operation."""
    if old_name not in module.ports:
        raise KeyError("Port {!r} does not exist in module {!r}.".format(old_name, module.name))
    if old_name not in module.netnames:
        raise KeyError("Netname {!r} does not exist in module {!r}.".format(old_name, module.name))
    if new_name in module.ports:
        raise ValueError("Port {!r} already exists in module {!r}.".format(new_name, module.name))
    if new_name in module.netnames:
        raise ValueError("Netname {!r} already exists in module {!r}.".format(new_name, module.name))
    port = module.ports[old_name]
    netname = module.netnames[old_name]
    if list(port.bits) != list(netname.bits):
        raise ValueError(
            "Port/netname {!r} in module {!r} have different bits: port={} netname={}".format(
                old_name, module.name, port.bits, netname.bits
            )
        )
    renamed_port = rename_port(module, old_name, new_name)
    renamed_netname = rename_netname(module, old_name, new_name)
    return renamed_port, renamed_netname


def rename_netname(module, old_name, new_name):
    if new_name in module.netnames:
        raise ValueError("Netname {!r} already exists in module {!r}.".format(new_name, module.name))
    netname = module.netnames.pop(old_name)
    netname.name = new_name
    module.netnames[new_name] = netname
    return netname


def rename_cell(module, old_name, new_name):
    if new_name in module.cells:
        raise ValueError("Cell {!r} already exists in module {!r}.".format(new_name, module.name))
    cell = module.cells.pop(old_name)
    cell.name = new_name
    module.cells[new_name] = cell
    return cell


def remove_port_and_optional_netname(module, name, expected_bits=None):
    """Remove a port and, when present, its same-name netname after checks."""
    if name not in module.ports:
        raise KeyError("Port {!r} does not exist in module {!r}.".format(name, module.name))
    port = module.ports[name]
    if expected_bits is not None and list(port.bits) != list(expected_bits):
        raise ValueError(
            "Port {!r} in module {!r} has bits {}, expected {}.".format(
                name, module.name, port.bits, list(expected_bits)
            )
        )
    netname = module.netnames.get(name)
    if netname is not None:
        if list(netname.bits) != list(port.bits):
            raise ValueError(
                "Port/netname {!r} in module {!r} have different bits: port={} netname={}".format(
                    name, module.name, port.bits, netname.bits
                )
            )
        remove_netname(module, name)
    return remove_port(module, name)


def _replace_in_bits(bits, old_bit, new_bit):
    replacements = 0
    for index, bit in enumerate(bits):
        if bit == old_bit:
            bits[index] = new_bit
            replacements += 1
    return replacements


def replace_bit_references(
    module,
    old_bit,
    new_bit,
    include_ports=True,
    include_netnames=True,
    include_cells=True,
):
    replacements = 0
    if include_ports:
        for port in module.ports.values():
            replacements += _replace_in_bits(port.bits, old_bit, new_bit)
    if include_netnames:
        for netname in module.netnames.values():
            replacements += _replace_in_bits(netname.bits, old_bit, new_bit)
    if include_cells:
        for cell in module.cells.values():
            for bits in cell.connections.values():
                replacements += _replace_in_bits(bits, old_bit, new_bit)
    return replacements


class BitAllocator(object):
    """Deterministic allocator for new Yosys JSON integer bit ids."""

    def __init__(self, start=None, used_bits=None):
        if start is None:
            start = 1
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            raise ValueError("BitAllocator start must be a non-negative integer, got {!r}.".format(start))
        self._next = start
        self._used = set()
        for bit in used_bits or []:
            if not isinstance(bit, int) or isinstance(bit, bool) or bit < 0:
                raise ValueError("Used bit ids must be non-negative integers, got {!r}.".format(bit))
            self._used.add(bit)

    @classmethod
    def after(cls, obj):
        return cls(start=max_integer_bit(obj) + 1)

    def allocate_one(self):
        while self._next in self._used:
            self._next += 1
        bit = self._next
        self._used.add(bit)
        self._next += 1
        return bit

    def allocate_many(self, width):
        if not isinstance(width, int) or isinstance(width, bool) or width < 0:
            raise ValueError("Allocation width must be a non-negative integer, got {!r}.".format(width))
        return [self.allocate_one() for _ in range(width)]


def remap_cell_types(module, type_map, cell_names=None):
    """Rewrite `cell.type` values for the selected cells.

    The helper intentionally edits only the instance type string. It does not
    rename the instance itself and does not touch connections, ports, netnames,
    or any module-level structure.
    """
    if cell_names is None:
        target_cell_names = list(module.cells.keys())
    else:
        target_cell_names = []
        seen = set()
        missing = []
        for cell_name in cell_names:
            if cell_name in seen:
                raise ValueError("Cell {!r} was requested more than once for remap.".format(cell_name))
            seen.add(cell_name)
            if cell_name not in module.cells:
                missing.append(cell_name)
            target_cell_names.append(cell_name)
        if missing:
            raise KeyError("Cells do not exist in module {!r}: {}".format(module.name, missing))

    remapped = 0
    for cell_name in target_cell_names:
        cell = module.cells[cell_name]
        if cell.type not in type_map:
            continue
        new_type = type_map[cell.type]
        if not isinstance(new_type, str) or not new_type:
            raise ValueError("Invalid replacement type {!r} for cell {!r}.".format(new_type, cell_name))
        if cell.type == new_type:
            continue
        cell.type = new_type
        remapped += 1
    return remapped


def remap_cell_connection_ports(module, port_map, cell_names=None, strict=False):
    """Rewrite selected cell connection keys and matching port-direction keys.

    The helper renames cell-local port names only. It does not change bit
    connectivity, cell instance names, cell types, module ports, or netnames.
    """
    if cell_names is None:
        target_cell_names = list(module.cells.keys())
    else:
        target_cell_names = []
        seen = set()
        missing = []
        for cell_name in cell_names:
            if cell_name in seen:
                raise ValueError("Cell {!r} was requested more than once for port remap.".format(cell_name))
            seen.add(cell_name)
            if cell_name not in module.cells:
                missing.append(cell_name)
            target_cell_names.append(cell_name)
        if missing:
            raise KeyError("Cells do not exist in module {!r}: {}".format(module.name, missing))

    for old_name, new_name in port_map.items():
        if not isinstance(old_name, str) or not old_name:
            raise ValueError("Invalid source port name {!r}.".format(old_name))
        if not isinstance(new_name, str) or not new_name:
            raise ValueError("Invalid destination port name {!r} for {!r}.".format(new_name, old_name))

    remapped = 0
    for cell_name in target_cell_names:
        cell = module.cells[cell_name]
        old_connections = cell.connections
        if strict:
            missing_ports = [port_name for port_name in old_connections.keys() if port_name not in port_map]
            if missing_ports:
                raise ValueError(
                    "Cell {!r} in module {!r} has unmapped connection ports: {}".format(
                        cell_name, module.name, missing_ports
                    )
                )

        new_connections = {}
        for old_name, bits in old_connections.items():
            new_name = port_map.get(old_name, old_name)
            if new_name in new_connections:
                raise ValueError(
                    "Port remap on cell {!r} in module {!r} would create duplicate connection key {!r}.".format(
                        cell_name, module.name, new_name
                    )
                )
            new_connections[new_name] = list(bits)

        new_port_directions = {}
        for old_name, direction in cell.port_directions.items():
            if strict and old_name not in port_map:
                raise ValueError(
                    "Cell {!r} in module {!r} has unmapped port_direction entry {!r}.".format(
                        cell_name, module.name, old_name
                    )
                )
            new_name = port_map.get(old_name, old_name)
            if new_name in new_port_directions:
                raise ValueError(
                    "Port remap on cell {!r} in module {!r} would create duplicate port_direction key {!r}.".format(
                        cell_name, module.name, new_name
                    )
                )
            new_port_directions[new_name] = direction

        if new_connections != old_connections or new_port_directions != cell.port_directions:
            remapped += 1
        cell.connections = new_connections
        cell.port_directions = new_port_directions

    return remapped
