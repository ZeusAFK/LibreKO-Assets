"""gtd_reader.py — Knight Online .gtd terrain file parser.

Implements two decryption ciphers per the format notes §6.1/§6.2:
  - LCG stream cipher (name + body): init=0x0816, state=(0x6081*(state+byte)+0x1608)&0xFFFF, out=byte^(state>>8)
  - RC4 (count fields when compFlag>=1): 16-byte key f5 06 21 61 7d 29 19 71 5c 81 8d 9d e9 c9 fd 20

GTD cell grid layout (mapSize^2 cells, 8 bytes each):
  [f32 height][u32 packed]
  packed bits:
    bit0        : walkable
    bits 1-5    : blendA (texture blend A index)
    bits 6-10   : blendB (texture blend B index)
    bits 11-20  : primaryTile (10 bits)
    bits 21-30  : secondaryTile (1023 = no blend)
    bit 31      : unused

NOTE: After the cell grid there are additional sections (per-patch bbox, grass bytes, tile path,
tile-data tables). These are documented here but parsing stops at the cell grid — tail parsing
is TODO (see RE_REQUESTS for full format confirmation).

Usage (CLI):
    python gtd_reader.py <path-to.gtd>
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# RC4 key for count fields when compFlag >= 1
# ---------------------------------------------------------------------------
RC4_KEY = bytes([
    0xf5, 0x06, 0x21, 0x61, 0x7d, 0x29, 0x19, 0x71,
    0x5c, 0x81, 0x8d, 0x9d, 0xe9, 0xc9, 0xfd, 0x20,
])


# ---------------------------------------------------------------------------
# LCG stream cipher (standard KO cipher)
# ---------------------------------------------------------------------------
def lcg_decrypt(data: bytes | bytearray, init_state: int = 0x0816) -> bytearray:
    """Decrypt a byte string with the KO LCG XOR stream cipher.

    init_state  : initial LCG state (0x0816 for GTD/OPD name+body; 0x0418 for newer .tbl layer2)
    recurrence  : state = (0x6081 * (state + encrypted_byte) + 0x1608) & 0xFFFF
    output byte : encrypted_byte ^ (state >> 8)
    """
    state = init_state
    out = bytearray(len(data))
    for i, e in enumerate(data):
        out[i] = e ^ ((state >> 8) & 0xFF)
        state = (0x6081 * (state + e) + 0x1608) & 0xFFFF
    return out


# ---------------------------------------------------------------------------
# RC4
# ---------------------------------------------------------------------------
def rc4_decrypt(data: bytes | bytearray, key: bytes = RC4_KEY) -> bytearray:
    """Plain RC4 decryption."""
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) & 0xFF
        S[i], S[j] = S[j], S[i]

    out = bytearray(len(data))
    i = j = 0
    for idx, byte in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + S[i]) & 0xFF
        S[i], S[j] = S[j], S[i]
        out[idx] = byte ^ S[(S[i] + S[j]) & 0xFF]
    return out


# ---------------------------------------------------------------------------
# Core reader
# ---------------------------------------------------------------------------
def read_gtd(path: str | Path) -> dict:
    """Parse a .gtd terrain file.

    Returns a dict with:
        map_size   : int (e.g. 257)
        comp_flag  : int
        name       : str
        heights    : list[list[float]]  (map_size x map_size, row-major)
        tiles      : list[int]          (packed u32, map_size^2, row-major)
    """
    path = Path(path)
    raw = path.read_bytes()
    offset = 0

    def _read(fmt: str):
        nonlocal offset
        size = struct.calcsize(fmt)
        val = struct.unpack_from(fmt, raw, offset)
        offset += size
        return val[0] if len(val) == 1 else val

    # --- Name (LCG-encrypted) ---
    name_len = _read("<i")
    if name_len < 0 or name_len > 512:
        raise ValueError(f"Implausible nameLen={name_len}")

    enc_name = raw[offset: offset + name_len]
    offset += name_len
    dec_name = lcg_decrypt(enc_name)
    name = dec_name.rstrip(b"\x00").decode("latin-1", errors="replace")

    # --- compFlag and mapSize ---
    # When compFlag >= 1, the next two u32 fields are RC4-encrypted individually
    comp_flag_raw = raw[offset: offset + 4]
    offset += 4

    # Try plain read first; if the result looks wrong, decrypt
    comp_flag = struct.unpack_from("<I", comp_flag_raw)[0]

    # If compFlag is absurdly large, try RC4 decrypt
    if comp_flag > 4:
        decrypted_cf = rc4_decrypt(comp_flag_raw)
        comp_flag = struct.unpack_from("<I", decrypted_cf)[0]

    map_size_raw = raw[offset: offset + 4]
    offset += 4

    if comp_flag >= 1:
        map_size_bytes = rc4_decrypt(map_size_raw)
    else:
        map_size_bytes = map_size_raw

    map_size = struct.unpack_from("<I", map_size_bytes)[0]

    if map_size < 2 or map_size > 4096:
        raise ValueError(f"Implausible mapSize={map_size}")

    # --- Cell grid: mapSize^2 * 8 bytes ---
    n_cells = map_size * map_size
    cell_bytes = 8  # f32 height + u32 packed

    needed = offset + n_cells * cell_bytes
    if needed > len(raw):
        raise ValueError(
            f"File too short for grid: need {needed}, have {len(raw)}"
        )

    heights_flat: list[float] = []
    tiles_flat: list[int] = []

    # Read the entire grid in one pass using struct.unpack_from
    grid_data = raw[offset: offset + n_cells * cell_bytes]
    for c in range(n_cells):
        base = c * cell_bytes
        h = struct.unpack_from("<f", grid_data, base)[0]
        p = struct.unpack_from("<I", grid_data, base + 4)[0]
        heights_flat.append(h)
        tiles_flat.append(p)

    offset += n_cells * cell_bytes

    # Reshape heights into 2D (row-major)
    heights_2d = [
        heights_flat[row * map_size: (row + 1) * map_size]
        for row in range(map_size)
    ]

    # --- Trailer: tile path table + slot table. Ported verbatim from the
    # authoritative KO.Formats GtdFile.TryLoadEncrypted (the server.Client) and
    # verified on moradon: slotCount=174, pathCount=42, all 42 paths resolve in
    # DTex, slot[0] = dtex\2017_esl_a001_0.gtt chunk 2 (matches KO_TERRAIN doc).
    # Layout after the cell grid:
    #   patch floats: 3 * ((mapSize-1)//8)^2 * f32   (per-patch cull bounds; skip)
    #   grass bytes : mapSize^2                       (dead in retail; skip)
    #   tile path   : 260 bytes base dir              (ignore)
    #   [i32 tileSlotCount]                           RAW
    #   [u32 pathCount]                               RC4 per-field if comp_flag>=1
    #   pathCount * char[260]  .gtt relative paths    RAW (NUL-terminated)
    #   tileSlotCount * { u16 pathIdx; u16 chunkIdx } RAW
    tile_paths: list[str] = []
    tile_slots: list[tuple] = []   # (path_idx, chunk_idx)
    try:
        pps = (map_size - 1) // 8
        offset += 3 * pps * pps * 4          # patch floats
        offset += n_cells                    # grass bytes
        offset += 260                        # base tile path
        slot_count = struct.unpack_from("<i", raw, offset)[0]; offset += 4
        pc_raw = raw[offset:offset + 4]; offset += 4
        path_count = struct.unpack(
            "<I", rc4_decrypt(pc_raw) if comp_flag >= 1 else pc_raw)[0]
        if (0 <= slot_count <= 65535 and 0 <= path_count <= 65535
                and offset + path_count * 260 + slot_count * 4 <= len(raw)):
            for _ in range(path_count):
                s = raw[offset:offset + 260]; offset += 260
                tile_paths.append(s.split(b"\x00", 1)[0].decode("latin-1", "replace"))
            for _ in range(slot_count):
                p_idx, c_idx = struct.unpack_from("<HH", raw, offset); offset += 4
                tile_slots.append((p_idx, c_idx))
    except (struct.error, IndexError):
        tile_paths, tile_slots = [], []   # heights/tiles still valid without them

    # --- Align the .gtd grid to the SERVER's entity coordinate frame ----------
    # The server's (koX, koZ) axes are TRANSPOSED relative to the raw .gtd cell grid
    # (the .gtd's stored x/z are swapped vs how the server addresses world position).
    # Found by the user tuning the live terrain node transform to put the town under a
    # FIXED player marker at KO(815,922) -> rot270+mirZ; a deterministic pixel-match
    # then proved the equivalent DATA rearrangement that lets the node stay IDENTITY is
    # a pure transpose (new[z][x] = old[x][z]; matched at diff 0.02 vs 27 for every
    # other dihedral). Apply it HERE, the single parse point both the .hmap and .ktx
    # bakes consume, so an entity at KO(x,z) (cell x/4, z/4) lands on its real terrain
    # cell with the Terrain node left at identity.
    old_h = heights_2d
    heights_2d = [[old_h[x][z] for x in range(map_size)]
                  for z in range(map_size)]
    old_t = tiles_flat
    tiles_flat = [old_t[x * map_size + z]
                  for z in range(map_size) for x in range(map_size)]

    return {
        "map_size": map_size,
        "comp_flag": comp_flag,
        "name": name,
        "heights": heights_2d,
        "tiles": tiles_flat,
        "tile_paths": tile_paths,   # list[str] of .gtt relative paths (e.g. "dtex\\x.gtt")
        "tile_slots": tile_slots,   # list[(pathIdx, chunkIdx)]; index via tile_primary/secondary
    }


# ---------------------------------------------------------------------------
# Helpers for tile bit-field extraction
# ---------------------------------------------------------------------------
def tile_walkable(packed: int) -> bool:
    return bool(packed & 0x1)


# NOTE: bits 1-5 / 6-10 are NOT "blend alpha" (the the format notes doc was wrong) —
# they are Tex1Dir / Tex2Dir, the tile STAMP DIRECTION (0-7), an index into the
# TileDirU/V[8][4] corner-UV table in the terrain renderer. The blend between the two
# tiles is ADDITIVE (D3DTOP_ADD), not alpha.
def tile_dir1(packed: int) -> int:
    return (packed >> 1) & 0x1F


def tile_dir2(packed: int) -> int:
    return (packed >> 6) & 0x1F


def tile_is_full(packed: int) -> bool:
    return bool(packed & 0x1)


# Back-compat aliases (old names referenced "blend"); same bits, corrected meaning.
tile_blend_a = tile_dir1
tile_blend_b = tile_dir2


def tile_primary(packed: int) -> int:
    return (packed >> 11) & 0x3FF


def tile_secondary(packed: int) -> int:
    return (packed >> 21) & 0x3FF


# ---------------------------------------------------------------------------
# CLI summary
# ---------------------------------------------------------------------------
def _summarize(path: str) -> None:
    print(f"Reading: {path}")
    data = read_gtd(path)

    ms = data["map_size"]
    heights_flat = [h for row in data["heights"] for h in row]
    finite = [h for h in heights_flat if math.isfinite(h)]
    walkable = sum(1 for p in data["tiles"] if tile_walkable(p))

    print(f"  Name        : {data['name']!r}")
    print(f"  compFlag    : {data['comp_flag']}")
    print(f"  map_size    : {ms}  ({ms}x{ms} = {ms*ms} cells)")
    if finite:
        print(f"  Height range: {min(finite):.3f} .. {max(finite):.3f}")
    else:
        print("  Height range: NO FINITE VALUES")
    print(f"  Walkable    : {walkable} / {ms*ms} cells ({100*walkable/(ms*ms):.1f}%)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python gtd_reader.py <path.gtd>", file=sys.stderr)
        sys.exit(1)
    _summarize(sys.argv[1])
