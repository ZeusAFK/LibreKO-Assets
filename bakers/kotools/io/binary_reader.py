"""Binary reader with little-endian struct helpers for N3 engine files."""

import struct


class BinaryReader:
    """Reads binary data with a tracked position cursor."""

    __slots__ = ("_data", "_pos", "_len")

    def __init__(self, data: bytes | bytearray | memoryview, offset: int = 0):
        self._data = data if isinstance(data, (bytes, bytearray)) else bytes(data)
        self._pos = offset
        self._len = len(self._data)

    def read_bytes(self, n: int) -> bytes:
        end = self._pos + n
        if end > self._len:
            raise EOFError(f"Cannot read {n} bytes at offset {self._pos} (file size: {self._len})")
        result = self._data[self._pos:end]
        self._pos = end
        return result

    def read_int8(self) -> int:
        return struct.unpack_from("<b", self._data, self._advance(1))[0]

    def read_uint8(self) -> int:
        return struct.unpack_from("<B", self._data, self._advance(1))[0]

    def read_int16(self) -> int:
        return struct.unpack_from("<h", self._data, self._advance(2))[0]

    def read_uint16(self) -> int:
        return struct.unpack_from("<H", self._data, self._advance(2))[0]

    def read_int32(self) -> int:
        return struct.unpack_from("<i", self._data, self._advance(4))[0]

    def read_uint32(self) -> int:
        return struct.unpack_from("<I", self._data, self._advance(4))[0]

    def read_float(self) -> float:
        return struct.unpack_from("<f", self._data, self._advance(4))[0]

    def read_float3(self) -> tuple[float, float, float]:
        off = self._advance(12)
        return struct.unpack_from("<fff", self._data, off)

    def read_float4(self) -> tuple[float, float, float, float]:
        off = self._advance(16)
        return struct.unpack_from("<ffff", self._data, off)

    def read_float_array(self, count: int) -> list[float]:
        off = self._advance(count * 4)
        return list(struct.unpack_from(f"<{count}f", self._data, off))

    def read_matrix4x4(self) -> list[list[float]]:
        """Read a 4x4 float matrix (64 bytes) row-major."""
        vals = self.read_float_array(16)
        return [vals[i * 4:(i + 1) * 4] for i in range(4)]

    def read_color4(self) -> tuple[float, float, float, float]:
        """Read D3DCOLORVALUE (4 floats: r, g, b, a)."""
        return self.read_float4()

    def read_dword(self) -> int:
        return self.read_uint32()

    def read_bool32(self) -> bool:
        return self.read_uint32() != 0

    def read_string(self, length: int) -> str:
        return self.read_bytes(length).decode("ascii", errors="replace")

    def read_n3_name(self) -> str:
        """Read an N3 name string: 4-byte length prefix + ASCII string."""
        length = self.read_int32()
        if length <= 0:
            return ""
        return self.read_string(length)

    def read_n3_path(self) -> str:
        """Read a file path reference (same format as n3_name, may be empty)."""
        return self.read_n3_name()

    def skip(self, n: int):
        self._pos = min(self._pos + n, self._len)

    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int):
        self._pos = max(0, min(pos, self._len))

    def remaining(self) -> int:
        return self._len - self._pos

    def at_eof(self) -> bool:
        return self._pos >= self._len

    def peek_bytes(self, n: int) -> bytes:
        end = min(self._pos + n, self._len)
        return self._data[self._pos:end]

    def _advance(self, n: int) -> int:
        off = self._pos
        end = off + n
        if end > self._len:
            raise EOFError(f"Cannot read {n} bytes at offset {off} (file size: {self._len})")
        self._pos = end
        return off
