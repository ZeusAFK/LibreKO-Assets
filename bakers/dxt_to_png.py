r"""dxt_to_png.py -- export Knight Online .dxt (Noah Texture File) images to PNG.

Self-contained on purpose: standard library only (struct/zlib/hashlib/argparse), no numpy, no PIL. Everything needed to read a KO texture is in this one file, and every layout
claim below was measured against a retail install
(898 loose .dxt plus 16,371 .dxt entries across the ui/item/fx/object .hdr archives).


CONTAINER
---------
A .dxt file is an N3 resource: a name string, then the __DXT_HEADER, then pixels.

    [int32]   nameLength
    [bytes]   name             whatever absolute path the artist's tool happened to save, up to
                               151 bytes in retail data and usually not the filename.
                               Never parse it, just skip it.
    [char3]   "NTF"            Noah Texture File
    [uint8]   version          3, 4 or 7 (see ENCRYPTION)
    [int32]   width
    [int32]   height
    [uint32]  format           D3DFORMAT
    [int32]   bMipMap          0/1
    [bytes]   pixel data

The same blob appears verbatim inside .hdr/.src archives and embedded in .n3shape/.n3cpart, so a
reader that takes bytes works everywhere. Some blobs start straight at "NTF" with no name prefix;
this reader accepts that too.


PIXEL DATA LAYOUT  (measured -- the client's own skip arithmetic disagrees and is not a guide)
----------------------------------------------------------------------------------------------
Whatever the format, the stored mip chain STOPS AT 4x4. It does not run down to 1x1.

  bMipMap = 1   levels from (w,h) halving while w >= 4 and h >= 4, back to back.
                Then, for a block-compressed format only, an uncompressed 16-bit chain of the same
                image starting at (w/2, h/2) and again halving down to 4x4 -- the fallback the 2002
                client uploaded on cards without DXT support, dead weight today.
                236 of 236 mipmapped DXT files matched this exactly.

  bMipMap = 0   level 0 only, then:
                  block-compressed  -> one 16-bit image at (w/2, h/2)   [verified: mean channel
                     delta 5/255 against level 0 point-sampled, so it is the same picture]
                  w >= 512 and h >= 512 -> a 256x256 16-bit image (the "Voodoo data" seek)
                  version 4 -> 8 zero bytes

Exporting level 0 therefore never needs any of the trailing data, and no size check on the payload
should be treated as a format error.


ENCRYPTION
----------
Only version 7 is encrypted. Version 4 is PLAIN -- it is version 3 plus that 8-byte trailer.
The obvious-looking `version & 4` test misreads all 11 retail v4 files as encrypted and turns them
into noise; the client tests `szID[3] == 7` and nothing else.

The cipher is Windows CryptoAPI RC4: CryptDeriveKey(CALG_RC4, SHA1(cipherString), flags), i.e. a 128-bit RC4
key = SHA1(cipherString)[:16]. The reset granularity is the part that costs people days, and it
falls out of the texture loader decrypting once per read unit while CryptDecrypt passes
Final=TRUE -- which resets the MS RC4 provider's keystream on every call:

  block-compressed   one ReadFile per MIP LEVEL -> the keystream restarts at each level and runs
                     continuously within a level
  uncompressed       one ReadFile per SCANLINE  -> the keystream restarts on every row,
                     chunk = width * bytesPerPixel

Get this wrong and the result is not subtly off, it is rainbow noise -- row 0 correct and the rest
garbage, in the continuous-stream case.

The trailing 16-bit fallback chain of a v7 compressed file is only seeked over by the client, never
decrypted, so its encryption state is unknown. This tool does not decode it.


FORMATS
-------
DXT1/2/3/4/5 plus the uncompressed D3DFORMATs KO ships. DXT2 and DXT4 carry the same bits as DXT3
and DXT5; only the alpha convention differs (premultiplied), and retail holds exactly one DXT4
file, so they decode down the same path. Retail uses DXT1, DXT3, DXT5, 21 (A8R8G8B8),
22 (X8R8G8B8), 25 (A1R5G5B5) and 26 (A4R4G4B4). The rest are here because the client supports
them, not because a file was found.

X8R8G8B8 has no alpha channel: its stored fourth byte is undefined and must be forced to 255.
The uncompressed 32-bit formats are stored BGRA, and 16-bit ones are little-endian words.


KNOWN ODDITIES IN RETAIL DATA
-----------------------------
898 loose files and 16,367 archive entries parse; 4 do not. ui.hdr's itemicon_3_2041_01_1..3 and
itemicon_3_3031_01_4 are 64x64 32-bit TGAs saved under a .dxt name, so the client's NTF magic check
rejects them and those icons never draw in retail either. Nothing to fix, but expect them.


USAGE
-----
    python dxt_to_png.py texture.dxt                     -> texture.png beside it
    python dxt_to_png.py texture.dxt -o out/icon.png
    python dxt_to_png.py Chr -o out --recursive          -> every .dxt under Chr/
    python dxt_to_png.py --info texture.dxt              -> header and level table, no PNG
    python dxt_to_png.py --archive UI/ui.hdr -o out      -> every .dxt in ui.hdr/ui.src
    python dxt_to_png.py --archive UI/ui.hdr -o out --filter "itemicon_*"
    python dxt_to_png.py texture.dxt -o out --mip all    -> texture.mip0.png, .mip1.png, ...

As a library:

    from dxt_to_png import read_dxt, write_png
    tex = read_dxt(open("texture.dxt", "rb").read())
    write_png("texture.png", tex.width, tex.height, tex.rgba())
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

CIPHER_STRING = b"owsd9012%$1as!wpow1033b%!@%12"

DXT1, DXT2, DXT3, DXT4, DXT5 = 0x31545844, 0x32545844, 0x33545844, 0x34545844, 0x35545844
R8G8B8, A8R8G8B8, X8R8G8B8 = 20, 21, 22
R5G6B5, X1R5G5B5, A1R5G5B5, A4R4G4B4 = 23, 24, 25, 26

BLOCK_SIZE = {DXT1: 8, DXT2: 16, DXT3: 16, DXT4: 16, DXT5: 16}
BYTES_PER_PIXEL = {R8G8B8: 3, A8R8G8B8: 4, X8R8G8B8: 4,
                   R5G6B5: 2, X1R5G5B5: 2, A1R5G5B5: 2, A4R4G4B4: 2}
FORMAT_NAMES = {DXT1: "DXT1", DXT2: "DXT2", DXT3: "DXT3", DXT4: "DXT4", DXT5: "DXT5",
                R8G8B8: "R8G8B8", A8R8G8B8: "A8R8G8B8", X8R8G8B8: "X8R8G8B8",
                R5G6B5: "R5G6B5", X1R5G5B5: "X1R5G5B5", A1R5G5B5: "A1R5G5B5",
                A4R4G4B4: "A4R4G4B4"}

MIN_MIP = 4


class DxtError(Exception):
    pass


def rc4_key(cipher_string: bytes = CIPHER_STRING) -> bytes:
    """CryptDeriveKey(CALG_RC4, SHA1(s), 0x800000) is the first 16 bytes of SHA1(s)."""
    return hashlib.sha1(cipher_string).digest()[:16]


def rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data))
    i = j = 0
    for n, b in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[n] = b ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


@dataclass
class NtfHeader:
    name: str
    version: int
    width: int
    height: int
    format: int
    has_mipmaps: bool
    pixel_offset: int

    @property
    def is_encrypted(self) -> bool:
        return self.version == 7

    @property
    def is_compressed(self) -> bool:
        return self.format in BLOCK_SIZE

    @property
    def format_name(self) -> str:
        return FORMAT_NAMES.get(self.format, f"0x{self.format:08X}")


def _looks_like_tga(blob: bytes) -> bool:
    return (len(blob) > 18 and blob[0] == 0 and blob[1] in (0, 1)
            and blob[2] in (1, 2, 3, 9, 10, 11) and blob[16] in (8, 15, 16, 24, 32))


def parse_header(blob: bytes) -> NtfHeader:
    if blob[:3] == b"NTF":
        name, off = "", 0
    else:
        if len(blob) < 24:
            raise DxtError("too short for an NTF resource")
        if _looks_like_tga(blob):
            raise DxtError("a TGA wearing a .dxt name, not an NTF texture")
        name_len = struct.unpack_from("<i", blob, 0)[0]
        if not 0 <= name_len <= 4096 or len(blob) < 4 + name_len + 20:
            raise DxtError(f"bad name length {name_len}")
        name = blob[4:4 + name_len].split(b"\0")[0].decode("latin-1")
        off = 4 + name_len
    if blob[off:off + 3] != b"NTF":
        raise DxtError(f"not an NTF texture (magic {blob[off:off + 3]!r})")
    version = blob[off + 3]
    width, height, fmt, mip = struct.unpack_from("<iiIi", blob, off + 4)
    if width <= 0 or height <= 0:
        raise DxtError(f"bad dimensions {width}x{height}")
    if fmt not in BLOCK_SIZE and fmt not in BYTES_PER_PIXEL:
        raise DxtError(f"unsupported D3DFORMAT 0x{fmt:08X}")
    return NtfHeader(name, version, width, height, fmt, bool(mip), off + 20)


def level_size(width: int, height: int, fmt: int) -> int:
    if fmt in BLOCK_SIZE:
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * BLOCK_SIZE[fmt]
    return width * height * BYTES_PER_PIXEL[fmt]


def level_dimensions(header: NtfHeader) -> list[tuple[int, int]]:
    """The stored mip chain: full size, halving while both axes stay >= 4."""
    if not header.has_mipmaps:
        return [(header.width, header.height)]
    levels, w, h = [], header.width, header.height
    while w >= MIN_MIP and h >= MIN_MIP:
        levels.append((w, h))
        w //= 2
        h //= 2
    return levels or [(header.width, header.height)]


def decrypt_level(payload: bytes, width: int, fmt: int, key: bytes) -> bytes:
    if fmt in BLOCK_SIZE:
        return rc4(key, payload)
    stride = width * BYTES_PER_PIXEL[fmt]
    return b"".join(rc4(key, payload[o:o + stride]) for o in range(0, len(payload), stride))


def _rgb565(c: int) -> tuple[int, int, int]:
    return (((c >> 11) & 0x1F) * 255 // 31, ((c >> 5) & 0x3F) * 255 // 63, (c & 0x1F) * 255 // 31)


def _color_table(c0: int, c1: int, punchthrough: bool) -> list[bytes]:
    r0, g0, b0 = _rgb565(c0)
    r1, g1, b1 = _rgb565(c1)
    table = [bytes((r0, g0, b0)), bytes((r1, g1, b1))]
    if punchthrough and c0 <= c1:
        table.append(bytes(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2)))
        table.append(b"\0\0\0")
    else:
        table.append(bytes(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3)))
        table.append(bytes(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3)))
    return table


def _dxt5_alpha_table(a0: int, a1: int) -> list[int]:
    if a0 > a1:
        return [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    return [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]


def decode_compressed(data: bytes, width: int, height: int, fmt: int) -> bytearray:
    """DXT1/2/3/4/5 -> RGBA8. Blocks are 4x4 in row-major order, partial edge blocks clipped."""
    block_size = BLOCK_SIZE[fmt]
    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)
    punchthrough = fmt == DXT1
    explicit_alpha = fmt in (DXT2, DXT3)
    interp_alpha = fmt in (DXT4, DXT5)
    row_bytes = width * 4
    out = bytearray(row_bytes * height)

    for by in range(blocks_y):
        for bx in range(blocks_x):
            offset = (by * blocks_x + bx) * block_size
            block = data[offset:offset + block_size]
            if len(block) < block_size:
                return out

            if explicit_alpha:
                alpha_bits = int.from_bytes(block[:8], "little")
                alphas = [((alpha_bits >> (4 * i)) & 0xF) * 17 for i in range(16)]
                color = block[8:]
            elif interp_alpha:
                table = _dxt5_alpha_table(block[0], block[1])
                alpha_bits = int.from_bytes(block[2:8], "little")
                alphas = [table[(alpha_bits >> (3 * i)) & 7] for i in range(16)]
                color = block[8:]
            else:
                alphas = None
                color = block

            c0, c1, bits = struct.unpack_from("<HHI", color, 0)
            colors = _color_table(c0, c1, punchthrough)
            cutout = punchthrough and c0 <= c1
            keep = min(16, (width - bx * 4) * 4)

            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    break
                row = bytearray()
                for px in range(4):
                    i = py * 4 + px
                    index = (bits >> (2 * i)) & 3
                    row += colors[index]
                    if alphas is not None:
                        row.append(alphas[i])
                    else:
                        row.append(0 if (cutout and index == 3) else 255)
                start = y * row_bytes + bx * 16
                out[start:start + keep] = row[:keep]
    return out


def decode_uncompressed(data: bytes, width: int, height: int, fmt: int) -> bytearray:
    """A8R8G8B8 / X8R8G8B8 / R8G8B8 (all BGRA-ordered) and the 16-bit formats -> RGBA8."""
    count = width * height
    out = bytearray(count * 4)
    if fmt in (A8R8G8B8, X8R8G8B8):
        opaque = fmt == X8R8G8B8
        for i in range(count):
            o = i * 4
            b, g, r, a = data[o], data[o + 1], data[o + 2], data[o + 3]
            out[o:o + 4] = bytes((r, g, b, 255 if opaque else a))
    elif fmt == R8G8B8:
        for i in range(count):
            o = i * 3
            out[i * 4:i * 4 + 4] = bytes((data[o + 2], data[o + 1], data[o], 255))
    else:
        for i, v in enumerate(struct.unpack_from(f"<{count}H", data, 0)):
            if fmt == A4R4G4B4:
                a = ((v >> 12) & 0xF) * 17
                r = ((v >> 8) & 0xF) * 17
                g = ((v >> 4) & 0xF) * 17
                b = (v & 0xF) * 17
            elif fmt in (A1R5G5B5, X1R5G5B5):
                a = 255 if (fmt == X1R5G5B5 or v & 0x8000) else 0
                r = ((v >> 10) & 0x1F) * 255 // 31
                g = ((v >> 5) & 0x1F) * 255 // 31
                b = (v & 0x1F) * 255 // 31
            else:
                a = 255
                r = ((v >> 11) & 0x1F) * 255 // 31
                g = ((v >> 5) & 0x3F) * 255 // 63
                b = (v & 0x1F) * 255 // 31
            out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    return out


class DxtTexture:
    def __init__(self, header: NtfHeader, blob: bytes, key: bytes):
        self.header = header
        self.blob = blob
        self.key = key
        self.levels = level_dimensions(header)

    @property
    def width(self) -> int:
        return self.header.width

    @property
    def height(self) -> int:
        return self.header.height

    def level_offset(self, level: int) -> int:
        offset = self.header.pixel_offset
        for w, h in self.levels[:level]:
            offset += level_size(w, h, self.header.format)
        return offset

    def rgba(self, level: int = 0) -> bytearray:
        """Decode one mip level to a width*height*4 RGBA buffer."""
        if not 0 <= level < len(self.levels):
            raise DxtError(f"level {level} out of range (0..{len(self.levels) - 1})")
        w, h = self.levels[level]
        fmt = self.header.format
        size = level_size(w, h, fmt)
        start = self.level_offset(level)
        payload = self.blob[start:start + size]
        if len(payload) < size:
            raise DxtError(f"truncated: level {level} wants {size} bytes, {len(payload)} present")
        if self.header.is_encrypted:
            payload = decrypt_level(payload, w, fmt, self.key)
        if fmt in BLOCK_SIZE:
            return decode_compressed(payload, w, h, fmt)
        return decode_uncompressed(payload, w, h, fmt)


def read_dxt(blob: bytes, cipher_string: bytes = CIPHER_STRING) -> DxtTexture:
    return DxtTexture(parse_header(blob), blob, rc4_key(cipher_string))


def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    """Minimal RGBA8 PNG: one IDAT, filter type 0 on every row."""
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def write_png(path: str | Path, width: int, height: int, rgba: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(width, height, rgba))


def read_hdr_index(hdr_path: str | Path) -> dict[str, tuple[int, int]]:
    """KO .hdr directory -> {entry name: (offset, size)} into the sibling .src.

    Retail layout: 8 bytes of version/count, then [uint16 nameLen][name][uint32 off][uint32 size].
    """
    data = Path(hdr_path).read_bytes()
    offset, entries = 8, {}
    while offset + 2 <= len(data):
        name_len = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        if name_len == 0 or offset + name_len + 8 > len(data):
            break
        name = data[offset:offset + name_len].decode("latin-1")
        offset += name_len
        file_offset, file_size = struct.unpack_from("<II", data, offset)
        offset += 8
        entries[name] = (file_offset, file_size)
    return entries


def convert(blob: bytes, out_path: Path, cipher: bytes = CIPHER_STRING, mip: str = "0") -> int:
    tex = read_dxt(blob, cipher)
    if mip == "all":
        for level, (w, h) in enumerate(tex.levels):
            write_png(out_path.with_suffix(f".mip{level}.png"), w, h, tex.rgba(level))
        return len(tex.levels)
    level = int(mip)
    w, h = tex.levels[level]
    write_png(out_path, w, h, tex.rgba(level))
    return 1


def describe(blob: bytes, label: str, cipher: bytes = CIPHER_STRING) -> None:
    tex = read_dxt(blob, cipher)
    hd = tex.header
    stored = sum(level_size(w, h, hd.format) for w, h in tex.levels)
    payload = len(blob) - hd.pixel_offset
    print(label)
    print(f"  name        {hd.name!r}")
    print(f"  version     {hd.version} ({'RC4 encrypted' if hd.is_encrypted else 'plain'})")
    print(f"  size        {hd.width}x{hd.height}")
    print(f"  format      {hd.format_name}")
    print(f"  mipmaps     {hd.has_mipmaps}")
    print(f"  pixels at   {hd.pixel_offset}")
    print(f"  levels      {len(tex.levels)}: " + ", ".join(f"{w}x{h}" for w, h in tex.levels))
    print(f"  level bytes {stored}, payload {payload}, trailing {payload - stored}")


_ERRORS = (DxtError, struct.error, ValueError, IndexError, OSError)


def _run_archive(args, cipher: bytes) -> int:
    hdr = Path(args.archive)
    src = hdr.with_suffix(".src")
    if not src.exists():
        print(f"missing {src}", file=sys.stderr)
        return 2
    out_dir = Path(args.output) if args.output else hdr.parent / (hdr.stem + "_png")
    index = read_hdr_index(hdr)
    names = sorted(n for n in index if n.lower().endswith(".dxt")
                   and fnmatch.fnmatch(n.lower(), args.filter.lower()))
    ok = failed = 0
    with open(src, "rb") as f:
        for name in names:
            offset, size = index[name]
            f.seek(offset)
            blob = f.read(size)
            try:
                if args.info:
                    describe(blob, name, cipher)
                else:
                    rel = Path(name.replace("\\", "/")).with_suffix(".png")
                    convert(blob, out_dir / rel, cipher, args.mip)
                ok += 1
            except _ERRORS as e:
                failed += 1
                print(f"{name}: {e}", file=sys.stderr)
    print(f"{ok} entries, {failed} failed" + ("" if args.info else f" -> {out_dir}"))
    return 1 if failed and not ok else 0


def _run_directory(source: Path, args, cipher: bytes) -> int:
    files = sorted(p for p in source.glob("**/*" if args.recursive else "*")
                   if p.suffix.lower() == ".dxt")
    out_dir = Path(args.output) if args.output else source
    ok = failed = 0
    for path in files:
        try:
            blob = path.read_bytes()
            if args.info:
                describe(blob, str(path), cipher)
            else:
                convert(blob, out_dir / path.relative_to(source).with_suffix(".png"),
                        cipher, args.mip)
            ok += 1
        except _ERRORS as e:
            failed += 1
            print(f"{path}: {e}", file=sys.stderr)
    print(f"{ok} files, {failed} failed" + ("" if args.info else f" -> {out_dir}"))
    return 1 if failed and not ok else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export Knight Online .dxt textures to PNG.")
    ap.add_argument("input", nargs="?", help="a .dxt file or a directory of them")
    ap.add_argument("-o", "--output", help="output .png (single file) or output directory")
    ap.add_argument("-r", "--recursive", action="store_true", help="recurse into subdirectories")
    ap.add_argument("--archive", help="read .dxt entries from a KO .hdr and its sibling .src")
    ap.add_argument("--filter", default="*", help="glob applied to archive entry names")
    ap.add_argument("--mip", default="0", help="mip level to export, or 'all'")
    ap.add_argument("--info", action="store_true", help="print the header instead of writing a PNG")
    ap.add_argument("--key", help="override the cipher string")
    args = ap.parse_args(argv)

    cipher = args.key.encode("latin-1") if args.key else CIPHER_STRING

    if args.archive:
        return _run_archive(args, cipher)
    if not args.input:
        ap.error("give an input path or --archive")

    source = Path(args.input)
    if source.is_dir():
        return _run_directory(source, args, cipher)

    blob = source.read_bytes()
    if args.info:
        describe(blob, str(source), cipher)
        return 0
    out = Path(args.output) if args.output else source.with_suffix(".png")
    if out.is_dir() or not out.suffix:
        out = out / (source.stem + ".png")
    n = convert(blob, out, cipher, args.mip)
    print(f"{source} -> {out}" + (f" ({n} levels)" if n > 1 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
