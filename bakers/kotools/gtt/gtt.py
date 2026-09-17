"""GTT ground texture container — N sequential NTF textures in one file.

Format: 8 equal-sized blobs, each containing:
  [NTF header] name_len(4) + NTF magic(4) + width(4) + height(4) + format(4) + mipmap(4)
  [DXT mip chain] full mipmap chain in DXT format
  [Fallback mip chain] uncompressed A1R5G5B5 (for DXT1) or A4R4G4B4 at half resolution
"""

from pathlib import Path

from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..ntf.header import NTFHeader
from ..ntf.decoder import decode_to_rgba
from ..ntf.encoder import encode_from_rgba, generate_mipmaps
from ..ntf.texture import KOTexture
from ..formats.d3dformat import D3DFormat
from ..crypto.wincrypt import KOCipher, DEFAULT_KEY


def read_gtt(path: str | Path, key: bytes = DEFAULT_KEY) -> list[KOTexture]:
    """Read all textures from a GTT container."""
    with open(str(path), "rb") as f:
        data = f.read()

    ntf_offsets = []
    for i in range(len(data) - 3):
        if data[i:i + 3] == b"NTF":
            ntf_offsets.append(i - 4)

    textures = []
    for idx, blob_start in enumerate(ntf_offsets):
        reader = BinaryReader(data, blob_start)
        try:
            header = NTFHeader.from_reader(reader)
        except (ValueError, EOFError):
            continue

        mip0_size = header.format.compute_data_size(header.width, header.height)
        pixel_data = reader.read_bytes(mip0_size)

        if header.is_encrypted:
            cipher = KOCipher(key)
            pixel_data = cipher.decrypt(pixel_data)

        rgba = decode_to_rgba(pixel_data, header.width, header.height, header.format)
        if not header.resource_name:
            header.resource_name = f"tile_{idx}"
        textures.append(KOTexture(header=header, rgba_data=rgba))

    return textures


def read_gtt_entry(path: str | Path, index: int, key: bytes = DEFAULT_KEY) -> KOTexture:
    """Read a single texture by index from a GTT container."""
    textures = read_gtt(path, key)
    if index >= len(textures):
        raise IndexError(f"GTT index {index} out of range (file has {len(textures)} textures)")
    return textures[index]


def _encode_fallback_mipchain(rgba: bytearray, width: int, height: int,
                              dxt_fmt: D3DFormat) -> bytes:
    """Encode the uncompressed fallback mip chain at half resolution."""
    fallback_fmt = D3DFormat.A1R5G5B5 if dxt_fmt == D3DFormat.DXT1 else D3DFormat.A4R4G4B4

    # Start at half resolution
    from PIL import Image
    img = Image.frombytes("RGBA", (width, height), bytes(rgba))
    fw = max(1, width // 2)
    fh = max(1, height // 2)

    result = bytearray()
    while True:
        mip_img = img.resize((fw, fh), Image.LANCZOS)
        mip_rgba = bytearray(mip_img.tobytes())
        result.extend(encode_from_rgba(mip_rgba, fw, fh, fallback_fmt))
        if fw <= 1 and fh <= 1:
            break
        fw = max(1, fw // 2)
        fh = max(1, fh // 2)

    return bytes(result)


def write_gtt(textures: list[KOTexture], path: str | Path,
              version: int = 3, fmt: D3DFormat = D3DFormat.DXT1):
    """Write textures to a GTT container matching the original format.

    Each blob contains: NTF header + DXT mip chain + fallback uncompressed mip chain.
    All blobs are padded to equal size.
    """
    if not textures:
        raise ValueError("Need at least 1 texture to write GTT")

    # All textures must have the same dimensions
    w, h = textures[0].width, textures[0].height

    blobs = []
    for tex in textures:
        writer = BinaryWriter()

        # NTF header
        header = NTFHeader(
            resource_name="",
            version=version,
            width=w,
            height=h,
            format=fmt,
            has_mipmaps=True,
        )
        writer.write_bytes(header.to_bytes())

        # DXT mip chain
        mips = generate_mipmaps(tex.rgba_data, w, h)
        for mip_rgba, mw, mh in mips:
            writer.write_bytes(encode_from_rgba(mip_rgba, mw, mh, fmt))

        # Fallback uncompressed mip chain
        writer.write_bytes(_encode_fallback_mipchain(tex.rgba_data, w, h, fmt))

        blobs.append(writer.getvalue())

    # Pad all blobs to same size (max blob size)
    max_size = max(len(b) for b in blobs)
    with open(str(path), "wb") as f:
        for blob in blobs:
            f.write(blob)
            if len(blob) < max_size:
                f.write(b"\x00" * (max_size - len(blob)))
