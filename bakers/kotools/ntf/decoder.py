"""DXT1/DXT3/DXT5 and uncompressed pixel format decoders."""

import struct
from ..formats.d3dformat import D3DFormat


def _unpack_rgb565(c: int) -> tuple[int, int, int]:
    r = ((c >> 11) & 0x1F) * 255 // 31
    g = ((c >> 5) & 0x3F) * 255 // 63
    b = (c & 0x1F) * 255 // 31
    return (r, g, b)


def decode_dxt1_block(block: bytes) -> list[tuple[int, int, int, int]]:
    c0, c1 = struct.unpack_from("<HH", block, 0)
    bits = struct.unpack_from("<I", block, 4)[0]
    r0, g0, b0 = _unpack_rgb565(c0)
    r1, g1, b1 = _unpack_rgb565(c1)

    colors = [(r0, g0, b0, 255), (r1, g1, b1, 255)]
    if c0 > c1:
        colors.append(((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3, 255))
        colors.append(((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3, 255))
    else:
        colors.append(((r0+r1)//2, (g0+g1)//2, (b0+b1)//2, 255))
        colors.append((0, 0, 0, 0))

    return [colors[(bits >> (2*i)) & 0x03] for i in range(16)]


def decode_dxt3_block(block: bytes) -> list[tuple[int, int, int, int]]:
    alpha_data = struct.unpack_from("<Q", block, 0)[0]
    alphas = [((alpha_data >> (4*i)) & 0x0F) * 255 // 15 for i in range(16)]

    c0, c1 = struct.unpack_from("<HH", block, 8)
    bits = struct.unpack_from("<I", block, 12)[0]
    r0, g0, b0 = _unpack_rgb565(c0)
    r1, g1, b1 = _unpack_rgb565(c1)

    colors = [
        (r0, g0, b0), (r1, g1, b1),
        ((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3),
        ((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3),
    ]
    return [(*colors[(bits >> (2*i)) & 0x03], alphas[i]) for i in range(16)]


def decode_dxt5_block(block: bytes) -> list[tuple[int, int, int, int]]:
    a0, a1 = block[0], block[1]
    alpha_lut = [a0, a1]
    if a0 > a1:
        for i in range(1, 7):
            alpha_lut.append(((7-i)*a0 + i*a1) // 7)
    else:
        for i in range(1, 5):
            alpha_lut.append(((5-i)*a0 + i*a1) // 5)
        alpha_lut.extend([0, 255])

    alpha_bits = 0
    for i in range(6):
        alpha_bits |= block[2+i] << (8*i)
    alphas = [alpha_lut[(alpha_bits >> (3*i)) & 0x07] for i in range(16)]

    c0, c1 = struct.unpack_from("<HH", block, 8)
    bits = struct.unpack_from("<I", block, 12)[0]
    r0, g0, b0 = _unpack_rgb565(c0)
    r1, g1, b1 = _unpack_rgb565(c1)

    colors = [
        (r0, g0, b0), (r1, g1, b1),
        ((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3),
        ((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3),
    ]
    return [(*colors[(bits >> (2*i)) & 0x03], alphas[i]) for i in range(16)]


def _decode_compressed(data: bytes, width: int, height: int, fmt: D3DFormat) -> bytearray:
    block_size = fmt.block_size
    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)

    decode_fn = {
        D3DFormat.DXT1: decode_dxt1_block,
        D3DFormat.DXT3: decode_dxt3_block,
        D3DFormat.DXT5: decode_dxt5_block,
    }[fmt]

    out = bytearray(width * height * 4)
    for by in range(blocks_y):
        for bx in range(blocks_x):
            idx = by * blocks_x + bx
            bd = data[idx * block_size:(idx + 1) * block_size]
            if len(bd) < block_size:
                break
            pixels = decode_fn(bd)
            for py in range(4):
                for px in range(4):
                    x, y = bx*4 + px, by*4 + py
                    if x < width and y < height:
                        r, g, b, a = pixels[py*4 + px]
                        off = (y * width + x) * 4
                        out[off] = r; out[off+1] = g; out[off+2] = b; out[off+3] = a
    return out


def _decode_uncompressed(data: bytes, width: int, height: int, fmt: D3DFormat) -> bytearray:
    out = bytearray(width * height * 4)
    if fmt in (D3DFormat.A8R8G8B8, D3DFormat.X8R8G8B8):
        for i in range(width * height):
            off = i * 4
            b, g, r, a = data[off], data[off+1], data[off+2], data[off+3]
            if fmt == D3DFormat.X8R8G8B8:
                a = 255
            out[i*4:i*4+4] = bytes([r, g, b, a])
    elif fmt == D3DFormat.A4R4G4B4:
        for i in range(width * height):
            val = struct.unpack_from("<H", data, i*2)[0]
            b = (val & 0x0F) * 255 // 15
            g = ((val >> 4) & 0x0F) * 255 // 15
            r = ((val >> 8) & 0x0F) * 255 // 15
            a = ((val >> 12) & 0x0F) * 255 // 15
            out[i*4:i*4+4] = bytes([r, g, b, a])
    elif fmt == D3DFormat.A1R5G5B5:
        for i in range(width * height):
            val = struct.unpack_from("<H", data, i*2)[0]
            b = (val & 0x1F) * 255 // 31
            g = ((val >> 5) & 0x1F) * 255 // 31
            r = ((val >> 10) & 0x1F) * 255 // 31
            a = 255 if (val >> 15) else 0
            out[i*4:i*4+4] = bytes([r, g, b, a])
    return out


def decode_to_rgba(data: bytes, width: int, height: int, fmt: D3DFormat) -> bytearray:
    """Decode pixel data of any supported format to RGBA bytearray."""
    if fmt.is_compressed:
        return _decode_compressed(data, width, height, fmt)
    return _decode_uncompressed(data, width, height, fmt)
