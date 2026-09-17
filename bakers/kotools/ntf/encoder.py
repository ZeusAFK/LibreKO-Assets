"""RGBA to DXT1/DXT3/DXT5 and uncompressed pixel format encoders.

Uses PCA-based endpoint selection with one refinement iteration for
high-quality DXT compression.  Falls back to bounding-box diagonal
if the block is degenerate (constant color, single-axis gradient).
"""

import struct
import numpy as np
from ..formats.d3dformat import D3DFormat


# ── RGB565 helpers ──────────────────────────────────────────────────────────

def _pack_rgb565(r: int, g: int, b: int) -> int:
    return ((r * 31 // 255) << 11) | ((g * 63 // 255) << 5) | (b * 31 // 255)


def _color_distance_sq(a: tuple, b: tuple) -> int:
    return (a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2


# ── PCA endpoint selection ─────────────────────────────────────────────────

def _find_endpoints(pixels: list[tuple[int, int, int, int]]) -> tuple[tuple, tuple]:
    """Find the two best color endpoints for a 4x4 DXT block using PCA.

    1. Compute the principal axis of color variation via power iteration.
    2. Pick the pixels at the extremes of projection onto that axis.
    3. One refinement: build palette, assign pixels, recompute endpoints
       using weighted averages (palette interpolation weights).

    Returns (max_color, min_color) as (R, G, B) tuples, ordered so that
    max is the "brighter" endpoint along the principal axis.
    """
    colors = np.array([(r, g, b) for r, g, b, _ in pixels], dtype=np.float64)

    # Degenerate: all pixels identical
    cmin = colors.min(axis=0)
    cmax = colors.max(axis=0)
    if (cmax - cmin).max() < 1.0:
        c = (int(cmin[0]), int(cmin[1]), int(cmin[2]))
        return c, c

    # PCA: find principal axis via power iteration on 3×3 covariance
    mean = colors.mean(axis=0)
    centered = colors - mean
    cov = centered.T @ centered  # (3, 3)

    axis = np.array([1.0, 1.0, 1.0])
    for _ in range(8):
        new_axis = cov @ axis
        norm = np.linalg.norm(new_axis)
        if norm < 1e-12:
            # Degenerate covariance — fall back to bbox diagonal
            axis = cmax - cmin
            norm = np.linalg.norm(axis)
            if norm < 1e-12:
                axis = np.array([1.0, 0.0, 0.0])
            else:
                axis /= norm
            break
        axis = new_axis / norm

    # Project onto principal axis and pick extremes
    proj = centered @ axis
    ep0 = colors[np.argmax(proj)].copy()
    ep1 = colors[np.argmin(proj)].copy()

    # --- One refinement iteration ---
    # Build 4-color palette from current endpoints
    palette = np.stack([
        ep0,
        ep1,
        (2.0 * ep0 + ep1) / 3.0,
        (ep0 + 2.0 * ep1) / 3.0,
    ])  # (4, 3)

    # Assign each pixel to the nearest palette entry
    dists = np.sum((colors[:, None, :] - palette[None, :, :]) ** 2, axis=2)  # (16, 4)
    assignments = np.argmin(dists, axis=1)  # (16,)

    # Recompute endpoints using interpolation weights:
    # palette[0] = ep0, palette[1] = ep1
    # palette[2] = 2/3 ep0 + 1/3 ep1, palette[3] = 1/3 ep0 + 2/3 ep1
    w0_table = np.array([1.0, 0.0, 2.0/3.0, 1.0/3.0])
    w1_table = np.array([0.0, 1.0, 1.0/3.0, 2.0/3.0])
    w0 = w0_table[assignments]  # (16,) weight of each pixel toward ep0
    w1 = w1_table[assignments]

    sum0 = w0.sum()
    sum1 = w1.sum()
    if sum0 > 1e-6:
        ep0 = np.clip((colors * w0[:, None]).sum(axis=0) / sum0, 0, 255)
    if sum1 > 1e-6:
        ep1 = np.clip((colors * w1[:, None]).sum(axis=0) / sum1, 0, 255)

    return (
        (int(ep0[0] + 0.5), int(ep0[1] + 0.5), int(ep0[2] + 0.5)),
        (int(ep1[0] + 0.5), int(ep1[1] + 0.5), int(ep1[2] + 0.5)),
    )


# ── Block encoders ─────────────────────────────────────────────────────────

def encode_dxt1_block(pixels: list[tuple[int, int, int, int]]) -> bytes:
    """Encode 16 RGBA pixels to an 8-byte DXT1 block."""
    has_alpha = any(a < 128 for _, _, _, a in pixels)
    max_c, min_c = _find_endpoints(pixels)

    c0 = _pack_rgb565(*max_c)
    c1 = _pack_rgb565(*min_c)

    if not has_alpha and c0 < c1:
        c0, c1 = c1, c0
        max_c, min_c = min_c, max_c
    elif has_alpha and c0 > c1:
        c0, c1 = c1, c0
        max_c, min_c = min_c, max_c

    if c0 > c1:
        palette = [
            max_c,
            min_c,
            ((2*max_c[0]+min_c[0])//3, (2*max_c[1]+min_c[1])//3, (2*max_c[2]+min_c[2])//3),
            ((max_c[0]+2*min_c[0])//3, (max_c[1]+2*min_c[1])//3, (max_c[2]+2*min_c[2])//3),
        ]
    else:
        palette = [
            min_c if c0 <= c1 else max_c,
            max_c if c0 <= c1 else min_c,
            ((max_c[0]+min_c[0])//2, (max_c[1]+min_c[1])//2, (max_c[2]+min_c[2])//2),
            (0, 0, 0),  # transparent
        ]

    bits = 0
    for i in range(16):
        r, g, b, a = pixels[i]
        if has_alpha and a < 128:
            idx = 3
        else:
            best = 0
            best_dist = _color_distance_sq((r, g, b), palette[0])
            for j in range(1, 4 if not has_alpha else 3):
                d = _color_distance_sq((r, g, b), palette[j])
                if d < best_dist:
                    best_dist = d
                    best = j
            idx = best
        bits |= idx << (2 * i)

    return struct.pack("<HHI", c0, c1, bits)


def encode_dxt5_block(pixels: list[tuple[int, int, int, int]]) -> bytes:
    """Encode 16 RGBA pixels to a 16-byte DXT5 block."""
    alphas = [a for _, _, _, a in pixels]
    a0 = max(alphas)
    a1 = min(alphas)

    if a0 == a1:
        alpha_lut = [a0, a1] + [a0] * 6
    elif a0 > a1:
        alpha_lut = [a0, a1]
        for i in range(1, 7):
            alpha_lut.append(((7-i)*a0 + i*a1) // 7)
    else:
        alpha_lut = [a0, a1]
        for i in range(1, 5):
            alpha_lut.append(((5-i)*a0 + i*a1) // 5)
        alpha_lut.extend([0, 255])

    alpha_bits = 0
    for i in range(16):
        best = 0
        best_d = abs(alphas[i] - alpha_lut[0])
        for j in range(1, len(alpha_lut)):
            d = abs(alphas[i] - alpha_lut[j])
            if d < best_d:
                best_d = d
                best = j
        alpha_bits |= best << (3 * i)

    alpha_bytes = bytes([a0, a1]) + alpha_bits.to_bytes(6, "little")

    # Color block (same as DXT1 but always 4-color mode)
    max_c, min_c = _find_endpoints(pixels)
    c0 = _pack_rgb565(*max_c)
    c1 = _pack_rgb565(*min_c)
    if c0 < c1:
        c0, c1 = c1, c0
        max_c, min_c = min_c, max_c

    palette = [
        max_c, min_c,
        ((2*max_c[0]+min_c[0])//3, (2*max_c[1]+min_c[1])//3, (2*max_c[2]+min_c[2])//3),
        ((max_c[0]+2*min_c[0])//3, (max_c[1]+2*min_c[1])//3, (max_c[2]+2*min_c[2])//3),
    ]

    bits = 0
    for i in range(16):
        r, g, b, _ = pixels[i]
        best = 0
        best_dist = _color_distance_sq((r, g, b), palette[0])
        for j in range(1, 4):
            d = _color_distance_sq((r, g, b), palette[j])
            if d < best_dist:
                best_dist = d
                best = j
        bits |= best << (2 * i)

    return alpha_bytes + struct.pack("<HHI", c0, c1, bits)


def encode_dxt3_block(pixels: list[tuple[int, int, int, int]]) -> bytes:
    """Encode 16 RGBA pixels to a 16-byte DXT3 block."""
    alpha_data = 0
    for i in range(16):
        a4 = pixels[i][3] * 15 // 255
        alpha_data |= a4 << (4 * i)
    alpha_bytes = struct.pack("<Q", alpha_data)

    # Color block
    max_c, min_c = _find_endpoints(pixels)
    c0 = _pack_rgb565(*max_c)
    c1 = _pack_rgb565(*min_c)
    if c0 < c1:
        c0, c1 = c1, c0
        max_c, min_c = min_c, max_c

    palette = [
        max_c, min_c,
        ((2*max_c[0]+min_c[0])//3, (2*max_c[1]+min_c[1])//3, (2*max_c[2]+min_c[2])//3),
        ((max_c[0]+2*min_c[0])//3, (max_c[1]+2*min_c[1])//3, (max_c[2]+2*min_c[2])//3),
    ]
    bits = 0
    for i in range(16):
        r, g, b, _ = pixels[i]
        best = 0
        best_dist = _color_distance_sq((r, g, b), palette[0])
        for j in range(1, 4):
            d = _color_distance_sq((r, g, b), palette[j])
            if d < best_dist:
                best_dist = d
                best = j
        bits |= best << (2 * i)

    return alpha_bytes + struct.pack("<HHI", c0, c1, bits)


# ── Pipeline ────────────────────────────────────────────────────────────────

def _encode_compressed(rgba: bytearray, width: int, height: int, fmt: D3DFormat) -> bytes:
    block_size = fmt.block_size
    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)

    encode_fn = {
        D3DFormat.DXT1: encode_dxt1_block,
        D3DFormat.DXT3: encode_dxt3_block,
        D3DFormat.DXT5: encode_dxt5_block,
    }[fmt]

    out = bytearray()
    for by in range(blocks_y):
        for bx in range(blocks_x):
            pixels = []
            for py in range(4):
                for px in range(4):
                    x, y = bx*4 + px, by*4 + py
                    if x < width and y < height:
                        off = (y * width + x) * 4
                        pixels.append((rgba[off], rgba[off+1], rgba[off+2], rgba[off+3]))
                    else:
                        pixels.append((0, 0, 0, 0))
            out.extend(encode_fn(pixels))
    return bytes(out)


def _encode_uncompressed(rgba: bytearray, width: int, height: int, fmt: D3DFormat) -> bytes:
    out = bytearray()
    if fmt in (D3DFormat.A8R8G8B8, D3DFormat.X8R8G8B8):
        for i in range(width * height):
            off = i * 4
            r, g, b, a = rgba[off], rgba[off+1], rgba[off+2], rgba[off+3]
            if fmt == D3DFormat.X8R8G8B8:
                a = 255
            out.extend(bytes([b, g, r, a]))
    elif fmt == D3DFormat.A4R4G4B4:
        for i in range(width * height):
            off = i * 4
            r, g, b, a = rgba[off], rgba[off+1], rgba[off+2], rgba[off+3]
            val = ((a*15//255) << 12) | ((r*15//255) << 8) | ((g*15//255) << 4) | (b*15//255)
            out.extend(struct.pack("<H", val))
    elif fmt == D3DFormat.A1R5G5B5:
        for i in range(width * height):
            off = i * 4
            r, g, b, a = rgba[off], rgba[off+1], rgba[off+2], rgba[off+3]
            val = ((1 if a >= 128 else 0) << 15) | ((r*31//255) << 10) | ((g*31//255) << 5) | (b*31//255)
            out.extend(struct.pack("<H", val))
    return bytes(out)


def encode_from_rgba(rgba: bytearray, width: int, height: int, fmt: D3DFormat) -> bytes:
    """Encode RGBA pixel data to the specified D3DFormat."""
    if fmt.is_compressed:
        return _encode_compressed(rgba, width, height, fmt)
    return _encode_uncompressed(rgba, width, height, fmt)


def generate_mipmaps(rgba: bytearray, width: int, height: int) -> list[tuple[bytearray, int, int]]:
    """Generate mipmap chain from RGBA data using Pillow for quality downsampling."""
    from PIL import Image
    levels = [(rgba, width, height)]
    img = Image.frombytes("RGBA", (width, height), bytes(rgba))
    w, h = width, height
    while w > 1 or h > 1:
        w = max(1, w // 2)
        h = max(1, h // 2)
        mip = img.resize((w, h), Image.LANCZOS)
        levels.append((bytearray(mip.tobytes()), w, h))
    return levels
