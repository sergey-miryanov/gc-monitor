"""Minimal protobuf decoder for test assertions."""

import struct

__all__ = [
    "ProtoField",
    "decode_message",
]


class ProtoField:
    def __init__(self, field_number: int, wire_type: int, value: int | bytes) -> None:
        self.field_number = field_number
        self.wire_type = wire_type
        self.value: int | bytes = value

    def __repr__(self) -> str:
        return f"ProtoField({self.field_number}, wire={self.wire_type}, value={self.value!r})"


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


def decode_message(data: bytes) -> list[ProtoField]:
    fields: list[ProtoField] = []
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field_number = key >> 3
        wire_type = key & 0x07

        val: int | bytes
        if wire_type == 0:
            val, pos = _read_varint(data, pos)
            fields.append(ProtoField(field_number, wire_type, val))
        elif wire_type == 1:
            val = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
            fields.append(ProtoField(field_number, wire_type, val))
        elif wire_type == 2:
            length, pos = _read_varint(data, pos)
            val = data[pos : pos + length]
            pos += length
            fields.append(ProtoField(field_number, wire_type, val))
        elif wire_type == 5:
            val = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            fields.append(ProtoField(field_number, wire_type, val))
        else:
            raise ValueError(f"Unknown wire type {wire_type} at pos {pos}")

    return fields


def get_field(fields: list[ProtoField], field_number: int) -> ProtoField | None:
    for f in fields:
        if f.field_number == field_number:
            return f
    return None


def get_fields(fields: list[ProtoField], field_number: int) -> list[ProtoField]:
    return [f for f in fields if f.field_number == field_number]


def get_varint(fields: list[ProtoField], field_number: int) -> int | None:
    f = get_field(fields, field_number)
    if f is not None and f.wire_type == 0:
        assert isinstance(f.value, int)
        return f.value
    return None


def get_string(fields: list[ProtoField], field_number: int) -> str | None:
    f = get_field(fields, field_number)
    if f is not None and f.wire_type == 2:
        assert isinstance(f.value, bytes)
        return f.value.decode("utf-8")
    return None


def get_bytes(fields: list[ProtoField], field_number: int) -> bytes | None:
    f = get_field(fields, field_number)
    if f is not None and f.wire_type == 2:
        assert isinstance(f.value, bytes)
        return f.value
    return None


def get_double(fields: list[ProtoField], field_number: int) -> float | None:
    f = get_field(fields, field_number)
    if f is not None and f.wire_type == 1:
        assert isinstance(f.value, int)
        result: float = struct.unpack("<d", struct.pack("<Q", f.value))[0]
        return result
    return None
