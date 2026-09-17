"""Decode a KO NTF texture blob (as packed in object.src / ui.src / item.src) to RGBA.

Container: [i32 nameLen][name][ "NTF" ][u8 version][i32 w][i32 h][u32 fmt][i32 mipFlag][payload]
  version: 3 = plain, 4 = plain (+8 byte trailer), 7 = RC4 encrypted.
  fmt    : D3DFORMAT; DXT1/3/5 fourCC are block-compressed (payload is a mip CHAIN),
           the rest are uncompressed single images.

Pixel decoding is delegated to the proven kotools NTF reader (NTFHeader + decode_to_rgba);
this module only adds the part kotools gets wrong: the v7 RC4 DECRYPTION, whose reset
granularity differs by storage class (both empirically verified against plain calibrations):
  - UNCOMPRESSED v7: RC4 resets PER SCANLINE (stride = w * bytesPerPixel). Proven on the
    minimaps (a continuous stream is correct for row 0 then turns to noise).
  - COMPRESSED (DXT) v7: ONE continuous RC4 keystream over the mip0 block data. Verified
    here: continuous beat per-block-row and per-scanline 10/10 on v7 DXT1 by a ~4x
    structure margin (epMAD ~12-26 vs ~84). Only mip0 is decrypted/used for rendering.
"""
import struct
import sys

import numpy as np

from kotools.io.binary_reader import BinaryReader            # noqa: E402
from kotools.ntf.header import NTFHeader                     # noqa: E402
from kotools.ntf.decoder import decode_to_rgba               # noqa: E402
from kotools.formats.d3dformat import D3DFormat              # noqa: E402

from gtd_reader import RC4_KEY                                # noqa: E402


def _rc4(key, data):
    s = list(range(256)); j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data)); i = j = 0
    for n, b in enumerate(data):
        i = (i + 1) & 0xFF; j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[n] = b ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def _decrypt_rows(data, stride):
    out = bytearray()
    for o in range(0, len(data), stride):
        out += _rc4(RC4_KEY, data[o:o + stride])
    return bytes(out)


def parse_header_raw(blob):
    """Lightweight header peek (no kotools) -> dict; tolerant of unknown formats."""
    r = 0
    nl = struct.unpack_from("<i", blob, r)[0]; r += 4
    name = blob[r:r + nl].decode("latin-1", "replace"); r += nl
    if blob[r:r + 3] != b"NTF":
        raise ValueError(f"not NTF (magic={blob[r:r+3]!r})")
    r += 3
    ver = blob[r]; r += 1
    w = struct.unpack_from("<i", blob, r)[0]; r += 4
    h = struct.unpack_from("<i", blob, r)[0]; r += 4
    fmt = struct.unpack_from("<I", blob, r)[0]; r += 4
    mip = struct.unpack_from("<i", blob, r)[0] != 0; r += 4
    return dict(name=name, ver=ver, w=w, h=h, fmt=fmt, mip=mip, pix=r)


def decode_ntf(blob):
    """Decode a packed NTF blob -> (w, h, rgba uint8 array HxWx4). mip0 only.

    Returns None for formats kotools can't decode (logged by the caller)."""
    hd = parse_header_raw(blob)
    w, h, ver, pix = hd["w"], hd["h"], hd["ver"], hd["pix"]
    try:
        fmt = D3DFormat.from_value(hd["fmt"])
    except ValueError:
        return None  # unknown D3DFORMAT (rare); skip rather than guess

    mip0 = fmt.compute_data_size(w, h)
    payload = blob[pix:pix + mip0]
    if len(payload) < mip0:
        return None

    if (ver & 0x04) != 0:  # v7 encrypted
        if fmt.is_compressed:
            payload = _rc4(RC4_KEY, payload)                 # one continuous keystream
        else:
            bpp = fmt.bits_per_pixel // 8
            payload = _decrypt_rows(payload, w * bpp)        # reset per scanline

    rgba = bytes(decode_to_rgba(payload, w, h, fmt))
    out = np.frombuffer(rgba, np.uint8).reshape(h, w, 4).copy()
    # X8R8G8B8 has no real alpha; a fully-transparent result means "unused" -> force opaque.
    if fmt == D3DFormat.X8R8G8B8 or out[:, :, 3].max() == 0:
        out[:, :, 3] = 255
    return w, h, out
