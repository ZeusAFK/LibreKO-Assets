"""NTF texture header: parse and serialize."""

from dataclasses import dataclass
from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..formats.d3dformat import D3DFormat


@dataclass
class NTFHeader:
    """Parsed NTF (__DXT_HEADER) texture header.

    Binary layout:
      [int32]  nameLength
      [char[]] resourceName
      [char4]  szID = "NTF" + version_byte
      [int32]  width
      [int32]  height
      [int32]  format (D3DFORMAT)
      [int32]  bMipMap
    """
    resource_name: str
    version: int
    width: int
    height: int
    format: D3DFormat
    has_mipmaps: bool
    header_size: int = 0

    @property
    def is_encrypted(self) -> bool:
        return (self.version & 0x04) != 0

    def compute_mip0_size(self) -> int:
        return self.format.compute_data_size(self.width, self.height)

    def compute_total_data_size(self) -> int:
        if not self.has_mipmaps:
            return self.compute_mip0_size()
        return self.format.compute_mipchain_size(self.width, self.height)

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "NTFHeader":
        start = reader.tell()
        resource_name = reader.read_n3_name()

        magic = reader.read_bytes(3)
        if magic != b"NTF":
            raise ValueError(f"Invalid NTF magic: {magic!r} (expected b'NTF')")
        version = reader.read_uint8()

        width = reader.read_int32()
        height = reader.read_int32()
        fmt_raw = reader.read_uint32()
        fmt = D3DFormat.from_value(fmt_raw)
        has_mipmaps = reader.read_bool32()

        return cls(
            resource_name=resource_name,
            version=version,
            width=width,
            height=height,
            format=fmt,
            has_mipmaps=has_mipmaps,
            header_size=reader.tell() - start,
        )

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_n3_name(self.resource_name)
        w.write_bytes(b"NTF")
        w.write_uint8(self.version)
        w.write_int32(self.width)
        w.write_int32(self.height)
        w.write_uint32(self.format.value)
        w.write_bool32(self.has_mipmaps)
        return w.getvalue()
