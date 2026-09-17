"""bake_capes.py -- decode KO CLAN CAPES (retail calls them "cloaks") for the Godot client.

What the original client does, cross-checked against Data\\Cloak.tbl:

  * `Data\\Cloak.tbl` is the cape catalogue. Columns:
        0 id  1 name  2 buyPrice  3 duration  4 grade  5 buyLoyalty
        6 ranking  7 (unused str)  8 MESH FAMILY (1/2/3)  9 flag
    Column 8 drives which mesh family the cape wears -- retail switches on it:
        1 -> Item\\Cloak_%.3d.n3cplug     2 -> Item\\Cloak_1%.2d.n3cplug
        3 -> Item\\Cloak_2%.2d.n3cplug
    and the %d is the CHARACTER'S RACE, so every race has its own fitted cape
    (cloak_001..014 = family 1, cloak_101..114 = family 2, cloak_201..214 = family 3).

  * Textures come from the cape id itself, NOT from the table:
        base  = Item\\Cloak_C_%.2d.dxt   with   (id %% 10000) %% 100
        mark  = Item\\Cloak_M_%.2d.dxt   with   (id %% 10000) // 100   (0 = no mark)
    plus the clan symbol (symbol\\s_0_5_%d.dxt), composited over the base.
    That decomposition is exactly why the catalogue is laid out as
    <pattern>*100 + <colour>: 3 = plain red, 103 = pattern01 red, 203 = pattern02 red.

  * The mesh is ALWAYS a 45-vertex / 192-index cloth grid: 5 columns x 9 rows, UV u in
    {0,.25,.5,.75,1} and v in {0,.125,..,1}. Row 8 (v=0) is the narrow top that pins at the
    shoulders; row 0 (v=1) is the wide hem. Retail re-simulates it every frame -- the file
    only stores the REST pose, which is what we bake. The client runs its own cloth solver.

Output (<output>/capes/):
    capes.json          {grid, meshes{"<family>:<race>": {pos,nrm,uv,idx}}, table{id: {...}}}
    tex/c_<NN>.png      base cloth colours       (cloak_c_NN.dxt)
    tex/m_<NN>.png      pattern/mark overlays    (cloak_m_NN.dxt, alpha-keyed)

Vertices are emitted in GODOT space (KO is left-handed -> negate X per client/src/Coord.cs)
with the triangle winding reversed to match.

Usage:
    python bake.py --only bake_capes
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from ko_texture import decode_ntf                      # noqa: E402
from n3_convert import Archive, load_index             # noqa: E402
from tbl_reader import read_tbl                        # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

KO_DIR = _ko()
ITEM_HDR = KO_DIR / "Item" / "item.hdr"
ITEM_SRC = KO_DIR / "Item" / "item.src"
CLOAK_TBL = KO_DIR / "Data" / "Cloak.tbl"
OUT_DIR = _assets() / "capes"

GRID_COLS, GRID_ROWS = 5, 9
VERT_COUNT = GRID_COLS * GRID_ROWS      # 45
INDEX_COUNT = (GRID_COLS - 1) * (GRID_ROWS - 1) * 6   # 192

# Cloak.tbl column indices (see the module docstring).
COL_ID, COL_NAME, COL_GRADE, COL_RANKING, COL_FAMILY = 0, 1, 4, 6, 8
COL_PRICE, COL_POINTS = 2, 5

# Mesh-family -> the printf retail uses for the .n3mesh stem, keyed by race.
FAMILY_FMT = {1: "cloak_%03d", 2: "cloak_1%02d", 3: "cloak_2%02d"}


def _read_mesh(blob: bytes) -> dict | None:
    """Parse a cloak .n3mesh: u32 nVerts, nVerts*(pos3,nrm3,uv2) f32, u32 nIdx, u16 idx[].

    Returns Godot-space arrays (X negated, winding reversed) or None if the file isn't the
    45/192 cloth grid every cape must be (the original client asserts exactly that when it loads a cloak).
    """
    if blob is None or len(blob) < 8:
        return None
    (nv,) = struct.unpack_from("<I", blob, 0)
    if nv != VERT_COUNT:
        return None
    need = 4 + nv * 32 + 4
    if len(blob) < need:
        return None
    v = np.frombuffer(blob, dtype=np.float32, count=nv * 8, offset=4).reshape(nv, 8)
    (ni,) = struct.unpack_from("<I", blob, 4 + nv * 32)
    if ni != INDEX_COUNT or len(blob) < need + ni * 2:
        return None
    idx = np.frombuffer(blob, dtype=np.uint16, count=ni, offset=need).astype(np.int32)

    pos = v[:, 0:3].copy()
    nrm = v[:, 3:6].copy()
    pos[:, 0] *= -1.0        # KO left-handed -> Godot (client/src/Coord.cs)
    nrm[:, 0] *= -1.0
    tri = idx.reshape(-1, 3)[:, ::-1]        # negating one axis flips winding

    return {
        "pos": [round(float(x), 6) for x in pos.reshape(-1)],
        "nrm": [round(float(x), 6) for x in nrm.reshape(-1)],
        "uv": [round(float(x), 6) for x in v[:, 6:8].reshape(-1)],
        "idx": [int(x) for x in tri.reshape(-1)],
    }


def _save_tex(arc: Archive, ko_name: str, out: Path) -> bool:
    blob = arc.get(ko_name)
    if blob is None:
        return False
    try:
        _w, _h, px = decode_ntf(blob)
    except Exception as exc:                       # noqa: BLE001 - report and skip
        print(f"  ! {ko_name}: decode failed ({exc})")
        return False
    Image.fromarray(np.asarray(px)).save(out)
    return True


def main() -> int:
    arc = Archive(str(ITEM_HDR), str(ITEM_SRC))
    index = load_index(str(ITEM_HDR))
    tex_dir = OUT_DIR / "tex"
    tex_dir.mkdir(parents=True, exist_ok=True)

    # ---- catalogue ----------------------------------------------------------
    tbl = read_tbl(str(CLOAK_TBL))
    table: dict[str, dict] = {}
    for row in tbl.rows:
        cid = int(row[COL_ID])
        family = int(row[COL_FAMILY])
        if family not in FAMILY_FMT:
            continue                              # unusable row (no mesh family)
        table[str(cid)] = {
            "name": str(row[COL_NAME]).strip(),
            "family": family,
            "grade": int(row[COL_GRADE]),
            "ranking": int(row[COL_RANKING]),
            "price": int(row[COL_PRICE]),
            "points": int(row[COL_POINTS]),
            "c": (cid % 10000) % 100,             # base cloth texture index
            "m": (cid % 10000) // 100,            # mark/pattern index (0 = none)
        }
    print(f"Cloak.tbl: {len(table)} capes")

    # ---- meshes: every (family, race) fitted cape present in the archive ----
    meshes: dict[str, dict] = {}
    for family, fmt in FAMILY_FMT.items():
        for race in range(0, 20):
            stem = fmt % race
            blob = arc.get(stem + ".n3mesh")
            m = _read_mesh(blob)
            if m is None:
                continue
            meshes[f"{family}:{race}"] = m
    print(f"meshes: {len(meshes)} fitted cloth grids "
          f"({sorted({k.split(':')[0] for k in meshes})} families)")

    # ---- textures: only the ones the catalogue can actually reference -------
    want_c = sorted({e["c"] for e in table.values()})
    want_m = sorted({e["m"] for e in table.values() if e["m"] > 0})
    have_c, have_m = [], []
    for n in want_c:
        if _save_tex(arc, f"cloak_c_{n:02d}.dxt", tex_dir / f"c_{n:02d}.png"):
            have_c.append(n)
    for n in want_m:
        if _save_tex(arc, f"cloak_m_{n:02d}.dxt", tex_dir / f"m_{n:02d}.png"):
            have_m.append(n)
    print(f"textures: {len(have_c)} base (c_), {len(have_m)} mark (m_)")

    missing = sorted({e["c"] for e in table.values()} - set(have_c))
    if missing:
        print(f"  note: {len(missing)} cape colours have no cloak_c_NN.dxt: {missing}")

    # Drop catalogue rows whose base texture doesn't ship - retail also renders nothing
    # for a cape id that misses its assets (the table lookup fails -> empty cloak string).
    usable = {k: v for k, v in table.items() if v["c"] in set(have_c)}
    print(f"usable capes: {len(usable)} / {len(table)}")

    doc = {
        "grid": {"cols": GRID_COLS, "rows": GRID_ROWS},
        "meshes": meshes,
        "table": usable,
        "textures": {"c": have_c, "m": have_m},
    }
    out_json = OUT_DIR / "capes.json"
    out_json.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out_json} ({out_json.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
