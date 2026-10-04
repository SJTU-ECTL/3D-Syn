"""Read-only queries and contract checks for typed Yosys JSON designs."""

from collections import Counter, namedtuple

from .model import Module

BitReference = namedtuple("BitReference", ["kind", "owner_name", "field_name", "bit_index", "direction"])
BitReference.__new__.__defaults__ = (None,)
def _assert_netname_dictionary_contract(module):
    for key, netname in module.netnames.items():
        assert key == netname.name, (
            "Module {!r} netname dictionary key {!r} does not match Netname.name {!r}.".format(
                module.name, key, netname.name
            )
        )


def _assert_legal_nonempty_net_bits(module, netname):
    assert isinstance(netname.bits, list) and netname.bits, (
        "Module {!r} netname {!r} must have a non-empty bit list.".format(module.name, netname.name)
    )
    for index, bit in enumerate(netname.bits):
        legal_integer = isinstance(bit, int) and not isinstance(bit, bool) and bit >= 0
        legal_constant = isinstance(bit, str) and bit in {"0", "1", "x", "z"}
        assert legal_integer or legal_constant, (
            "Module {!r} netname {!r} has invalid bit {!r} at index {}.".format(
                module.name, netname.name, bit, index
            )
        )


def _assert_used_integer_bits_have_visible_netnames(module):
    visible_bits = set()
    for netname in module.netnames.values():
        if netname.hide_name in (None, 0):
            visible_bits.update(bit for bit in netname.bits if isinstance(bit, int) and not isinstance(bit, bool))

    used_bits = set()
    for port in module.ports.values():
        used_bits.update(bit for bit in port.bits if isinstance(bit, int) and not isinstance(bit, bool))
    for cell in module.cells.values():
        for bits in cell.connections.values():
            used_bits.update(bit for bit in bits if isinstance(bit, int) and not isinstance(bit, bool))

    missing = sorted(used_bits - visible_bits)
    assert not missing, (
        "Module {!r} has port/cell integer bits without a visible netname: {}.".format(
            module.name, missing[:20]
        )
    )


def _is_backend_safe_name(name):
    return (
        isinstance(name, str)
        and bool(name)
        and not ("0" <= name[0] <= "9")
        and all(
            ord(character) < 128
            and (
                ("a" <= character <= "z")
                or ("A" <= character <= "Z")
                or ("0" <= character <= "9")
                or character == "_"
            )
            for character in name
        )
    )


def _assert_backend_netname_input_contract(module):
    _assert_netname_dictionary_contract(module)
    selected_names = [name for name in module.netnames if "\\" in name]
    port_names = set(module.ports)
    invalid_ports = sorted(name for name in port_names if "\\" in name)
    assert not invalid_ports, (
        "Module {!r} primary ports contain backslashes and cannot be normalized as internal nets: {}.".format(
            module.name, invalid_ports[:20]
        )
    )
    for name in selected_names:
        assert name not in port_names, (
            "Module {!r} primary port {!r} contains a backslash and cannot be normalized as an internal net.".format(
                module.name, name
            )
        )
        _assert_legal_nonempty_net_bits(module, module.netnames[name])
    _assert_used_integer_bits_have_visible_netnames(module)
    return selected_names


def assert_backend_netname_normalization(before_module, after_module, mapping):
    """Assert that only selected netname keys and Netname.name values changed."""
    assert isinstance(mapping, dict), "Backend netname mapping must be a dictionary."
    assert before_module.name == after_module.name, (
        "Module name changed during backend netname normalization: {!r} -> {!r}.".format(
            before_module.name, after_module.name
        )
    )
    _assert_netname_dictionary_contract(before_module)
    _assert_netname_dictionary_contract(after_module)

    selected_names = set(name for name in before_module.netnames if "\\" in name)
    assert set(mapping) == selected_names, (
        "Backend netname mapping keys do not match selected names: missing={} extra={}.".format(
            sorted(selected_names - set(mapping))[:20], sorted(set(mapping) - selected_names)[:20]
        )
    )
    assert len(set(mapping.values())) == len(mapping), "Backend netname mapping is not one-to-one."
    assert all(isinstance(name, str) and name and "\\" not in name for name in mapping.values()), (
        "Backend netname mapping contains an empty or backslash-containing output name."
    )
    assert all(_is_backend_safe_name(name) for name in mapping.values()), (
        "Backend netname mapping contains a name outside the backend-safe character policy."
    )

    before_data = before_module.to_dict()
    after_data = after_module.to_dict()
    before_netnames = before_data.pop("netnames")
    after_netnames = after_data.pop("netnames")
    assert before_data == after_data, (
        "Module {!r} changed outside netname keys/Netname.name during backend normalization.".format(
            before_module.name
        )
    )
    assert len(before_netnames) == len(after_netnames), (
        "Module {!r} netname count changed during backend normalization: {} -> {}.".format(
            before_module.name, len(before_netnames), len(after_netnames)
        )
    )

    reserved_names = (
        (set(before_module.netnames) - selected_names)
        | set(before_module.ports)
        | set(before_module.cells)
        | set(before_module.memories)
    )
    collisions = sorted(set(mapping.values()) & reserved_names)
    assert not collisions, "Normalized netnames collide with reserved module names: {}.".format(collisions[:20])

    expected_names = (set(before_module.netnames) - selected_names) | set(mapping.values())
    assert set(after_module.netnames) == expected_names, "Normalized module netname set does not match the mapping."
    for old_name, old_netname in before_module.netnames.items():
        new_name = mapping.get(old_name, old_name)
        assert new_name in after_module.netnames, "Expected normalized netname {!r} is missing.".format(new_name)
        new_netname = after_module.netnames[new_name]
        assert new_netname.name == new_name
        assert old_netname.to_dict() == new_netname.to_dict(), (
            "Netname {!r} changed fields other than its name during backend normalization.".format(old_name)
        )

    remaining = sorted(name for name in after_module.netnames if "\\" in name)
    assert not remaining, "Backend netnames still contain backslashes: {}.".format(remaining[:20])
    _assert_used_integer_bits_have_visible_netnames(after_module)


def assert_backend_netnames_normalized(design, module_name):
    """Check the Stage 07 backend-netname handoff contract without renaming."""
    from .validate import validate_design

    validate_design(design)
    assert module_name in design.modules, "Backend module {!r} does not exist.".format(module_name)
    module = design.modules[module_name]
    _assert_netname_dictionary_contract(module)
    invalid_ports = sorted(name for name in module.ports if "\\" in name)
    assert not invalid_ports, "Module {!r} primary ports contain backslashes: {}.".format(
        module_name, invalid_ports[:20]
    )
    _assert_used_integer_bits_have_visible_netnames(module)
    remaining = sorted(name for name in module.netnames if "\\" in name)
    assert not remaining, (
        "Module {!r} contains backend netnames with backslashes: {}.".format(module_name, remaining[:20])
    )


def _assert_backend_netname_runtime_result(
    module,
    original_netnames,
    selected_netname_data,
    original_nonnet_container_ids,
    original_nonnet_object_ids,
    mapping,
):
    selected_names = set(selected_netname_data)
    assert len(module.netnames) == len(original_netnames), (
        "Module {!r} netname count changed during backend normalization.".format(module.name)
    )
    assert set(mapping) == selected_names, "Backend netname mapping does not cover the selected names exactly."
    assert len(set(mapping.values())) == len(mapping), "Backend netname mapping is not one-to-one."
    assert all(_is_backend_safe_name(name) for name in mapping.values()), (
        "Backend netname mapping contains a name outside the backend-safe character policy."
    )
    reserved_names = (
        (set(original_netnames) - selected_names)
        | set(module.ports)
        | set(module.cells)
        | set(module.memories)
    )
    collisions = sorted(set(mapping.values()) & reserved_names)
    assert not collisions, "Normalized netnames collide with reserved module names: {}.".format(collisions[:20])

    current_nonnet_container_ids = tuple(
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
    assert current_nonnet_container_ids == original_nonnet_container_ids, (
        "Module {!r} non-netname containers changed during backend normalization.".format(module.name)
    )
    current_nonnet_object_ids = (
        tuple((name, id(port)) for name, port in module.ports.items()),
        tuple((name, id(cell)) for name, cell in module.cells.items()),
        tuple((name, id(memory)) for name, memory in module.memories.items()),
    )
    assert current_nonnet_object_ids == original_nonnet_object_ids, (
        "Module {!r} port/cell/memory objects changed during backend normalization.".format(module.name)
    )

    expected_names = (set(original_netnames) - selected_names) | set(mapping.values())
    assert set(module.netnames) == expected_names, "Normalized module netname set does not match the mapping."
    for old_name, original_netname in original_netnames.items():
        new_name = mapping.get(old_name, old_name)
        assert new_name in module.netnames, "Expected normalized netname {!r} is missing.".format(new_name)
        current_netname = module.netnames[new_name]
        assert current_netname is original_netname, "Netname {!r} was rebuilt during normalization.".format(old_name)
        assert current_netname.name == new_name
        if old_name in selected_netname_data:
            assert current_netname.to_dict() == selected_netname_data[old_name], (
                "Netname {!r} changed fields other than its name during backend normalization.".format(old_name)
            )

    _assert_netname_dictionary_contract(module)
    remaining = sorted(name for name in module.netnames if "\\" in name)
    assert not remaining, "Backend netnames still contain backslashes: {}.".format(remaining[:20])
    _assert_used_integer_bits_have_visible_netnames(module)


def collect_bit_references(module):
    references = {}

    def add(bit, reference):
        references.setdefault(bit, []).append(reference)

    for port in module.ports.values():
        for index, bit in enumerate(port.bits):
            add(bit, BitReference("port", port.name, None, index, port.direction))

    for netname in module.netnames.values():
        for index, bit in enumerate(netname.bits):
            add(bit, BitReference("netname", netname.name, None, index, None))

    for cell in module.cells.values():
        for conn_name, bits in cell.connections.items():
            direction = cell.port_directions.get(conn_name)
            for index, bit in enumerate(bits):
                add(bit, BitReference("cell", cell.name, conn_name, index, direction))

    return references


def collect_all_bits(module, include_constants=True):
    ordered = []
    seen = set()
    for bit in collect_bit_references(module).keys():
        if not include_constants and isinstance(bit, str):
            continue
        if bit not in seen:
            seen.add(bit)
            ordered.append(bit)
    return ordered


def max_integer_bit(obj):
    """Return the maximum integer bit id used by a module or design.

    Constant bits such as "0" and "x" are ignored. Empty modules/designs
    return 0 so callers can start allocation from 1.
    """
    if isinstance(obj, Module):
        modules = [obj]
    elif hasattr(obj, "modules"):
        modules = list(obj.modules.values())
    else:
        raise TypeError("Expected Module or Design-like object, got {!r}.".format(type(obj)))

    max_bit = 0
    for module in modules:
        for bit in collect_all_bits(module, include_constants=False):
            if bit > max_bit:
                max_bit = bit
    return max_bit


def module_interface_signature(module):
    """Return a deterministic summary of a module's port interface."""
    signature = []
    for name, port in module.ports.items():
        signature.append(
            {
                "name": name,
                "direction": port.direction,
                "bits": list(port.bits),
                "width": len(port.bits),
                "offset": port.offset,
                "upto": port.upto,
                "signed": port.signed,
            }
        )
    return signature


def collect_instance_types(module):
    instance_types = set()
    for cell in module.cells.values():
        instance_types.add(cell.type)
    return instance_types


def collect_instance_type_counts(module):
    counts = Counter()
    for cell in module.cells.values():
        counts[cell.type] += 1
    return dict(counts)


