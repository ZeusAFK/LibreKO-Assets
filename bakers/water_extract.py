r"""water_extract.py -- Knight Online river/water extractor.

Water surfaces are appended to each zone's `.gtd` after the terrain, in TWO sections loaded by
the terrain loader: rivers first, then ponds. Both are grids of 44-byte vertices (11 floats); vertex[1] = the flat water-surface Y
(the water level). The two sections use DIFFERENT layouts:

  RIVER patch (name AFTER the verts):
      u32   vertexCount
      vtx   vertex[vertexCount]          # 44 B each
      u32   indexCount                   # iIC; indices are NOT stored (grid is regular)
      u32   texLen ; char tex[texLen]    # -> misc\river\<tex>
      if managerVersion >= 1:
          u32  tex2Len ; char tex2[]
          byte params[80]                # 20 floats (flow/shader params)
    cols/rows are NOT stored -> derived from (vertexCount, indexCount): the grid is cols x rows
    with indexCount/6 == (cols-1)*(rows-1); the true cols is the one whose row-END vertex steps
    jump (raster wrap) ~3x the in-row steps.

  POND patch (name BEFORE the verts):
      u32   vertexCount
      u32   cols                         # rows = vertexCount/cols
      u32   texLen ; char tex[texLen]
      vtx   vertex[vertexCount]
      f32   heightOffset                 # added to Y at load (engine default 0.2 if 0)
      u32   flowOrColor
      if managerVersion >= 1:
          u32  tex2Len ; char tex2[] ; byte params[80]

Each section is preceded by a manager header: u32 nameLen; char name[nameLen] (encrypted, skipped);
u32 version; u32 count.

The previous extractor scanned for `misc\river\<tex>.dxt` names with [vertexCount][cols] right
before them -- that only matches POND patches, so it silently dropped EVERY river patch (e.g. the
El Morad river around KO (456,570)). This version parses both sections deterministically; if that
fails (e.g. an unexpected lightmap section), it falls back to the old scan so it never regresses.

Vertices stay in raw KO world space (same frame the server addresses entities -- validated:
identity mapping puts every patch on its riverbed). The Godot client converts via Terrain.KoToWorld.
Default KO Zones dir matches tools/bake_terrain.py (<retail install>).
"""
import math
import re
import struct
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
from gtd_reader import rc4_decrypt  # noqa: E402

DEFAULT_HEIGHT_OFFSET = 0.2
_VTX = 44   # bytes per water vertex (11 floats)


# --------------------------------------------------------------------------- helpers
def _u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def _bbox(d, vstart, vc):
    bb = [1e30, -1e30, 1e30, -1e30, 1e30, -1e30]   # minx maxx miny maxy minz maxz
    verts = []
    for i in range(vc):
        x, y, z = struct.unpack_from("<3f", d, vstart + _VTX * i)
        verts.append((x, y, z))
        bb[0] = min(bb[0], x); bb[1] = max(bb[1], x)
        bb[2] = min(bb[2], y); bb[3] = max(bb[3], y)
        bb[4] = min(bb[4], z); bb[5] = max(bb[5], z)
    return verts, bb


def _patch(tex, cols, vc, verts, bb, height_off, offset):
    return {
        "texture": tex,
        "cols": cols, "rows": vc // cols, "vertexCount": vc,
        "footprint_x": (bb[0], bb[1]), "footprint_z": (bb[4], bb[5]),
        "water_level_y": (bb[2], bb[3]),
        "height_offset": height_off,
        "vertices": verts,
        "file_offset": offset,
    }


def _derive_cols(verts, vc, iIC):
    """River patches store no cols. The grid is cols x rows with iIC/6 == (cols-1)*(rows-1);
    pick the factor whose row-END steps jump ~3x the in-row steps (the raster wrap)."""
    if iIC % 6 != 0:
        return None
    q = iIC // 6
    cands = [c for c in range(2, vc) if vc % c == 0 and (c - 1) * (vc // c - 1) == q]
    if not cands:
        return None
    d = [math.dist(verts[i], verts[i + 1]) for i in range(vc - 1)]
    best, best_ratio = None, -1.0
    for c in cands:
        ends = [d[i] for i in range(vc - 1) if i % c == c - 1]
        mids = [d[i] for i in range(vc - 1) if i % c != c - 1]
        if not ends or not mids:
            continue
        ratio = (sum(ends) / len(ends)) / max(sum(mids) / len(mids), 1e-6)
        if ratio > best_ratio:
            best, best_ratio = c, ratio
    return best


def _river_section_start(d):
    """Replicate the gtd preamble to find the byte offset just after the (deprecated, count 0)
    lightmap header == the start of the river section. Returns (offset, compFlag) or None.
    compFlag>=1 means the manager COUNT fields are RC4-encrypted (like the tile path count)."""
    n = len(d)
    o = 0
    nl = struct.unpack_from("<i", d, o)[0]; o += 4
    if nl < 0 or nl > 512 or o + nl > n:
        return None
    o += nl
    cf_raw = d[o:o + 4]; o += 4
    cf = struct.unpack_from("<I", cf_raw)[0]
    if cf > 4:
        cf = struct.unpack_from("<I", rc4_decrypt(cf_raw))[0]
    ms_raw = d[o:o + 4]; o += 4
    ms = struct.unpack_from("<I", rc4_decrypt(ms_raw) if cf >= 1 else ms_raw)[0]
    if ms < 2 or ms > 4096:
        return None
    o += ms * ms * 8                       # cell grid (f32 height + u32 packed)
    pps = (ms - 1) // 8
    o += 3 * pps * pps * 4                  # per-patch cull bounds
    o += ms * ms                           # grass bytes
    o += 260                               # base tile path
    if o + 8 > n:
        return None
    slot = struct.unpack_from("<i", d, o)[0]; o += 4
    pc_raw = d[o:o + 4]; o += 4
    pc = struct.unpack("<I", rc4_decrypt(pc_raw) if cf >= 1 else pc_raw)[0]
    if not (0 <= slot <= 65535 and 0 <= pc <= 65535):
        return None
    o += pc * 260 + slot * 4
    if o + 4 > n:
        return None
    num_lm = _u32(d, o); o += 4
    if num_lm != 0:                        # lightmaps are deprecated in retail; bail to the scan
        return None
    return o, cf


def _read_manager(d, o, comp_flag):
    """nameLen, name[nameLen] (encrypted, skipped), version, count -> (version, count, offset).
    count is RC4-encrypted when comp_flag>=1 (same scheme as the tile path count)."""
    n = len(d)
    if o + 4 > n:
        return None
    nlen = _u32(d, o); o += 4
    if nlen > 512 or o + nlen + 8 > n:
        return None
    o += nlen
    version = _u32(d, o); o += 4
    cnt_raw = d[o:o + 4]; o += 4
    count = struct.unpack("<I", rc4_decrypt(cnt_raw) if comp_flag >= 1 else cnt_raw)[0]
    if version > 8 or count > 4096:
        return None
    return version, count, o


def _parse_deterministic(d):
    n = len(d)
    start = _river_section_start(d)
    if start is None:
        return None
    o, comp_flag = start
    patches = []

    def read_tex(o):
        tlen = _u32(d, o); o += 4
        if tlen > 64 or o + tlen > n:
            return None, o
        return d[o:o + tlen].split(b"\x00", 1)[0].decode("latin-1", "replace"), o + tlen

    # ---- RIVER (name after verts) ----
    mgr = _read_manager(d, o, comp_flag)
    if mgr is None:
        return None
    version, count, o = mgr
    for _ in range(count):
        if o + 4 > n:
            return None
        vc = _u32(d, o); o += 4
        if not (0 < vc <= 2_000_000) or o + _VTX * vc + 4 > n:
            return None
        verts, bb = _bbox(d, o, vc); o += _VTX * vc
        iIC = _u32(d, o); o += 4
        tex, o = read_tex(o)
        if tex is None:
            return None
        if version >= 1:
            t2, o = read_tex(o)
            if t2 is None or o + 80 > n:
                return None
            o += 80
        cols = _derive_cols(verts, vc, iIC)
        if cols is None:
            return None
        patches.append(_patch(tex, cols, vc, verts, bb, 0.0, o))

    # ---- POND (name before verts) ----
    if o < n - 8:
        mgr = _read_manager(d, o, comp_flag)
        if mgr is None:
            return None
        version, count, o = mgr
        for _ in range(count):
            if o + 12 > n:
                return None
            vc = _u32(d, o); o += 4
            cols = _u32(d, o); o += 4
            if not (0 < vc <= 2_000_000 and 0 < cols <= 4096 and vc % cols == 0):
                return None
            tex, o = read_tex(o)
            if tex is None or o + _VTX * vc + 8 > n:
                return None
            verts, bb = _bbox(d, o, vc); o += _VTX * vc
            height_off = struct.unpack_from("<f", d, o)[0]; o += 4
            o += 4   # flowOrColor
            if version >= 1:
                t2, o = read_tex(o)
                if t2 is None or o + 80 > n:
                    return None
                o += 80
            patches.append(_patch(tex, cols, vc, verts, bb, height_off, o))

    # The two sections should consume the file to the end; a clean finish validates the layout.
    if abs(o - n) > 16:
        return None
    return patches


# --------------------------------------------------------------------------- legacy scan (fallback)
def _scan(d):
    n = len(d)
    rivers = []
    for m in re.finditer(rb'[ -~]{4,48}\.(?:dxt|DXT)', d):
        O = m.start()
        if O < 12:
            continue
        L = _u32(d, O - 4)
        name = d[O:m.end()]
        if L not in (len(name), len(name) + 1):
            continue
        cols = _u32(d, O - 8)
        vcount = _u32(d, O - 12)
        if not (0 < cols <= 4096 and 0 < vcount <= 2_000_000 and vcount % cols == 0):
            continue
        vstart = O + L
        if vstart + _VTX * vcount + 4 > n:
            continue
        verts, bb = _bbox(d, vstart, vcount)
        height_off = struct.unpack_from("<f", d, vstart + _VTX * vcount)[0]
        rivers.append(_patch(name.decode("latin-1"), cols, vcount, verts, bb, height_off, O))
    return rivers


def extract_rivers(gtd_path):
    d = open(gtd_path, "rb").read()
    res = _parse_deterministic(d)
    if res is not None:
        return res
    return _scan(d)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: water_extract.py <zone.gtd> [zone.gtd ...]")
        sys.exit(1)
    for p in sys.argv[1:]:
        rs = extract_rivers(p)
        det = _parse_deterministic(open(p, "rb").read()) is not None
        print(f"\n=== {p}: {len(rs)} water patch(es) [{'deterministic' if det else 'scan-fallback'}] ===")
        for r in rs:
            yl, yh = r["water_level_y"]
            print(f"  '{r['texture']}'  {r['cols']}x{r['rows']} ({r['vertexCount']} verts)  "
                  f"X[{r['footprint_x'][0]:.1f}..{r['footprint_x'][1]:.1f}] "
                  f"Z[{r['footprint_z'][0]:.1f}..{r['footprint_z'][1]:.1f}]  "
                  f"level Y={yl:.2f}" + ("" if abs(yh - yl) < 0.05 else f"..{yh:.2f}"))
