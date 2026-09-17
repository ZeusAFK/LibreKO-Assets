"""Binary writer with little-endian struct helpers for N3 engine files."""

import struct


class BinaryWriter:
    """Builds binary data in a bytearray."""

    __slots__ = ("_buf",)

    def __init__(self):
        self._buf = bytearray()

    def write_bytes(self, data: bytes | bytearray):
        self._buf.extend(data)

    def write_int8(self, val: int):
        self._buf.extend(struct.pack("<b", val))

    def write_uint8(self, val: int):
        self._buf.extend(struct.pack("<B", val))

    def write_int16(self, val: int):
        self._buf.extend(struct.pack("<h", val))

    def write_uint16(self, val: int):
        self._buf.extend(struct.pack("<H", val))

    def write_int32(self, val: int):
        self._buf.extend(struct.pack("<i", val))

    def write_uint32(self, val: int):
        self._buf.extend(struct.pack("<I", val))

    def write_float(self, val: float):
        self._buf.extend(struct.pack("<f", val))

    def write_float3(self, x: float, y: float, z: float):
        self._buf.extend(struct.pack("<fff", x, y, z))

    def write_float4(self, x: float, y: float, z: float, w: float):
        self._buf.extend(struct.pack("<ffff", x, y, z, w))

    def write_float_array(self, vals: list[float]):
        self._buf.extend(struct.pack(f"<{len(vals)}f", *vals))

    def write_matrix4x4(self, matrix: list[list[float]]):
        for row in matrix:
            self.write_float_array(row)

    def write_color4(self, r: float, g: float, b: float, a: float):
        self.write_float4(r, g, b, a)

    def write_dword(self, val: int):
        self.write_uint32(val)

    def write_bool32(self, val: bool):
        self.write_uint32(1 if val else 0)

    def write_string(self, s: str):
        self._buf.extend(s.encode("ascii"))

    def write_n3_name(self, name: str):
        """Write an N3 name string: 4-byte length prefix + ASCII string."""
        encoded = name.encode("ascii")
        self.write_int32(len(encoded))
        self._buf.extend(encoded)

    def write_n3_path(self, path: str):
        """Write a file path reference (same format as n3_name)."""
        self.write_n3_name(path)

    def tell(self) -> int:
        return len(self._buf)

    def getvalue(self) -> bytes:
        return bytes(self._buf)
