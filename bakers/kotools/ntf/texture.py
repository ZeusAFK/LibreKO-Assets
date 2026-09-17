"""High-level KO texture read/write API with encryption support."""

import os
from dataclasses import dataclass
from pathlib import Path

from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..formats.d3dformat import D3DFormat
from ..crypto.wincrypt import KOCipher, DEFAULT_KEY
from .header import NTFHeader
from .decoder import decode_to_rgba
from .encoder import encode_from_rgba, generate_mipmaps


@dataclass
class KOTexture:
    """A Knight Online texture with header and decoded RGBA pixel data."""
    header: NTFHeader
    rgba_data: bytearray  # width * height * 4 bytes (RGBA)

    @property
    def width(self) -> int:
        return self.header.width

    @property
    def height(self) -> int:
        return self.header.height

    def to_pil_image(self):
        from PIL import Image
        return Image.frombytes("RGBA", (self.width, self.height), bytes(self.rgba_data))

    def save_png(self, output_path: str | Path):
        self.to_pil_image().save(str(output_path))

    @classmethod
    def from_pil_image(cls, img, name: str = "", fmt: D3DFormat = D3DFormat.DXT1) -> "KOTexture":
        from PIL import Image
        img = img.convert("RGBA")
        header = NTFHeader(
            resource_name=name,
            version=3,
            width=img.width,
            height=img.height,
            format=fmt,
            has_mipmaps=True,
        )
        return cls(header=header, rgba_data=bytearray(img.tobytes()))

    @classmethod
    def from_png(cls, path: str | Path, name: str = "", fmt: D3DFormat = D3DFormat.DXT1) -> "KOTexture":
        from PIL import Image
        img = Image.open(str(path))
        if not name:
            name = Path(path).stem
        return cls.from_pil_image(img, name, fmt)


def read_dxt_from_bytes(data: bytes, key: bytes = DEFAULT_KEY) -> KOTexture:
    """Parse a .dxt file from raw bytes, decrypting if v7."""
    reader = BinaryReader(data)
    header = NTFHeader.from_reader(reader)
    pixel_data = data[header.header_size:]

    if header.is_encrypted:
        cipher = KOCipher(key)
        pixel_data = cipher.decrypt(pixel_data)

    rgba = decode_to_rgba(pixel_data, header.width, header.height, header.format)
    return KOTexture(header=header, rgba_data=rgba)


def read_dxt(path: str | Path, key: bytes = DEFAULT_KEY) -> KOTexture:
    """Load and decode a .dxt texture file."""
    with open(str(path), "rb") as f:
        data = f.read()
    return read_dxt_from_bytes(data, key)


def write_dxt_to_bytes(
    texture: KOTexture,
    version: int = 3,
    encrypt: bool = False,
    with_mipmaps: bool = True,
    key: bytes = DEFAULT_KEY,
) -> bytes:
    """Serialize a KOTexture to .dxt file bytes."""
    fmt = texture.header.format
    w = BinaryWriter()

    # Build header
    header = NTFHeader(
        resource_name=texture.header.resource_name,
        version=version if not encrypt else 7,
        width=texture.width,
        height=texture.height,
        format=fmt,
        has_mipmaps=with_mipmaps,
    )
    w.write_bytes(header.to_bytes())

    # Encode pixel data
    if with_mipmaps:
        mips = generate_mipmaps(texture.rgba_data, texture.width, texture.height)
    else:
        mips = [(texture.rgba_data, texture.width, texture.height)]

    pixel_buf = bytearray()
    for rgba, mw, mh in mips:
        pixel_buf.extend(encode_from_rgba(rgba, mw, mh, fmt))

    # Encrypt if needed
    if encrypt:
        cipher = KOCipher(key)
        pixel_buf = bytearray(cipher.encrypt(bytes(pixel_buf)))

    w.write_bytes(pixel_buf)
    return w.getvalue()


def write_dxt(
    texture: KOTexture,
    path: str | Path,
    version: int = 3,
    encrypt: bool = False,
    with_mipmaps: bool = True,
    key: bytes = DEFAULT_KEY,
):
    """Write a KOTexture to a .dxt file."""
    data = write_dxt_to_bytes(texture, version, encrypt, with_mipmaps, key)
    with open(str(path), "wb") as f:
        f.write(data)
