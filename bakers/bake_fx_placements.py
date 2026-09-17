"""Knight Online .opdsub (map effect-placement layer) baker.

A zone's `.opdsub` is the FX counterpart to `.opd`/`.opdext`: it attaches `.fxb`
particle effects to the zone's placed objects (street/torch lights on lamp posts,
portal glows, forge fire, water sparkle). Unlike `.opd` it is UNENCRYPTED — verified
by the retail loader and a parse that lands exactly on EOF.

Byte format (see the format notes §7b):
    [u32 version=0]          NEW format only (OLD format omits this word)
    u32 slotCount            == the .opd object/shape count (moradon = 8070)
    slot[slotCount]:         a PARALLEL array — slot i belongs to object i
        u32 nameLen
        if nameLen == 0:      empty slot (object i has no FX) — 4 bytes, skip
        if nameLen  > 0:
            char  path[nameLen]   ASCII, e.g. "fx\\20060222_mora_light_01.fxb"
            float pos[3]          offset from object i's POSITION, in world units — it is
                                  added, never transformed (see `_compose_world`)
            float scale           the effect's own uniform render scale (1.0/0.5)
            float quat[4]         the effect's own orientation (X,Y,Z,W); retail applies it
                                  to the bundle's direction vector, not to the offset

The slot index is the join key: slot i's effect belongs to object i. The composition is a
PLAIN VECTOR ADDITION of the object's position and the slot offset — no object rotation,
no object scale, no matrix (see `_compose_world`). We do it here in KO space and emit an
ABSOLUTE KO-space placement so the runtime reuses the proven object path
(Terrain.KoToWorld + the same quaternion handedness flip). A zero-offset effect therefore
lands exactly on its parent object. The effect's render scale is the slot `scale` (NOT the
parent's mesh scale — region-marker anchors like `zz_fxx_alpha` carry huge scales).

Output: assets/terrain/<zone>/fx_placements.json (Godot 'keep' import), a list of
    { "fx": "<baked name>", "p":[x,y,z], "s": scale, "q":[qx,qy,qz,qw] }  (KO space)
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

_HERE = Path(__file__).resolve().parent
GTD_DIR = Path(str(_ko() / "Zones"))
OUT_DIR = _assets() / "terrain"
FX_DIR = _assets() / "fx"


def _fx_name(path: str) -> str:
    """'fx\\20060222_mora_light_01.fxb' -> '20060222_mora_light_01'."""
    s = path.replace("\\", "/")
    s = s.rsplit("/", 1)[-1]            # drop any directory
    if s.lower().endswith(".fxb"):
        s = s[:-4]
    return s


def read_opdsub(path: Path) -> list[dict]:
    """Parse a .opdsub into populated effect placements (KO world space).

    NEW format (moradon etc.): [u32 version=0][u32 slotCount][slots].
    OLD format (elmorad_start/karus_start/freezone): [u32 slotCount][slots] — no
    version word. Discriminate on the first word: NEW's version is always 0; OLD's
    leading slotCount is always > 0.
    """
    d = path.read_bytes()
    if len(d) < 8:
        raise ValueError(f"{path.name}: too small ({len(d)} B)")
    first = struct.unpack_from("<I", d, 0)[0]
    if first == 0:                                    # NEW: version word present
        ver, count = struct.unpack_from("<II", d, 0)
        off = 8
    else:                                             # OLD: first word is the count
        ver, count = -1, first
        off = 4
    if count > 1_000_000:
        raise ValueError(f"{path.name}: implausible slotCount {count}")
    out = []
    for i in range(count):
        if off + 4 > len(d):
            raise ValueError(f"{path.name}: truncated at slot {i}/{count} (off {off})")
        nl = struct.unpack_from("<I", d, off)[0]
        off += 4
        if nl == 0:
            continue                                  # empty slot
        if nl > 256 or off + nl + 32 > len(d):
            raise ValueError(f"{path.name}: bad slot {i} nameLen={nl} at off {off}")
        name = d[off:off + nl].decode("latin-1"); off += nl
        px, py, pz = struct.unpack_from("<3f", d, off); off += 12
        scale = struct.unpack_from("<f", d, off)[0]; off += 4
        qx, qy, qz, qw = struct.unpack_from("<4f", d, off); off += 16
        out.append({
            "slot": i,                                      # join key: object/shape index
            "fx": _fx_name(name),
            "p": (float(px), float(py), float(pz)),         # LOCAL offset to object i
            "s": float(scale),
            "q": (float(qx), float(qy), float(qz), float(qw)),  # LOCAL rotation
        })
    leftover = len(d) - off
    if leftover != 0:
        # version/slot framing is verified; surface drift rather than silently trust it.
        print(f"[opdsub] WARN {path.name}: {leftover} trailing bytes "
              f"(ver={ver} count={count}, parsed {len(out)} populated)", file=sys.stderr)
    return out


def _compose_world(parent, local):
    """Where an .opdsub slot's effect actually goes (KO space).

    parent = {p:[x,y,z], r:[qx,qy,qz,qw], s:[sx,sy,sz]}; local placement dict.
    Returns (worldPos, worldQuat) in KO space.

    **worldPos = parentPosition + slotOffset.** A plain vector addition — the parent's
    rotation, scale and object matrix are NOT involved. Traced end to end in

      * the `.opdsub` loader reads nameLen/name/pos[3]/scale/quat[4] and calls
        the placement call takes (object, ..., quat, pos.x, pos.y, pos.z, scale, 0);
      * it stores the offset and the quaternion RAW on the object, and pushes the slot scale
        to the bundle's render scale;
      * the object tick then does, every frame:
            bundleWorldPos = slotOffset + objectPos
        reading the object's POSITION, not its matrix, so neither rotation nor scale can enter;
      * that bundle position is exactly what the particle create path adds as the world
        translation of every particle.

    The previous version multiplied the offset by the parent scale and rotated it by the
    parent quaternion, citing the matrix multiply in the tick path. That block builds
    its matrix from the SLOT QUATERNION alone and applies it to the bundle's own direction
    vector — it never touches the parent
    transform. The symptom was the third lantern of every `obj_zq_mora_garo_01` street lamp:
    a `(0, 7.70, 0)` offset on a lamp scaled 1.406 landed 10.83 units up instead of 7.70,
    which is 4.9 units ABOVE the top of a mesh only 5.93 units tall.

    The quaternion is likewise the slot's own, not composed with the parent's.
    """
    pp = parent["p"]
    lp = local["p"]
    world_pos = (pp[0] + lp[0], pp[1] + lp[1], pp[2] + lp[2])
    wq = local["q"]
    n = math.sqrt(sum(c * c for c in wq)) or 1.0
    world_quat = tuple(c / n for c in wq)
    return world_pos, world_quat


def _write_keep_import(p: Path) -> None:
    (p.parent / (p.name + ".import")).write_text('[remap]\n\nimporter="keep"\n', encoding="utf-8")


def _baked_fx() -> set[str]:
    """Set of effect names that have a baked descriptor (<output>/fx/<name>.json)."""
    if not FX_DIR.is_dir():
        return set()
    return {p.stem for p in FX_DIR.glob("*.json") if p.name != "index.json"}


def _load_objects(zone: str, out_dir: Path):
    """The zone's baked object placements (placements.json) — the .opdsub join target."""
    p = out_dir / zone.lower() / "placements.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _bounds(objs):
    """Map extent (KO) from object positions, for the off-map sanity guard."""
    xs = [o["p"][0] for o in objs] or [0.0]
    ys = [o["p"][1] for o in objs] or [0.0]
    zs = [o["p"][2] for o in objs] or [0.0]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def bake(zone: str, out_dir: Path, baked: set[str]) -> dict:
    placements = read_opdsub(GTD_DIR / f"{zone}.opdsub")
    map_dir = out_dir / zone.lower()
    dst = map_dir / "fx_placements.json"
    objs = _load_objects(zone, out_dir)
    if objs is None:
        # .opdsub offsets are LOCAL to objects; with no baked object positions we
        # cannot resolve world placement. Emit an empty list (clears any stale file)
        # and flag it so opd_convert can be run for this zone later.
        map_dir.mkdir(parents=True, exist_ok=True)
        dst.write_text("[]", encoding="utf-8")
        _write_keep_import(dst)
        return {"total": len(placements), "kept": 0, "dropped": {}, "offmap": 0,
                "noparent": 0, "distinct": 0, "bytes": dst.stat().st_size,
                "dst": str(dst), "no_objects": True}
    (xlo, xhi), (ylo, yhi), (zlo, zhi) = _bounds(objs)
    # generous margin: region-anchored area effects legitimately spread past the objects
    mx = max(40.0, 0.5 * (xhi - xlo)); mz = max(40.0, 0.5 * (zhi - zlo))
    nobj = len(objs)

    kept, dropped, offmap, noparent = [], {}, 0, 0
    for pl in placements:
        if baked and pl["fx"] not in baked:
            dropped[pl["fx"]] = dropped.get(pl["fx"], 0) + 1
            continue
        i = pl["slot"]
        if i >= nobj:
            noparent += 1
            continue
        wpos, wquat = _compose_world(objs[i], pl)       # parentPos + slotOffset (see above)
        guard = wpos                                    # same retail composition for the bounds guard
        if (guard[0] < xlo - mx or guard[0] > xhi + mx or
                guard[2] < zlo - mz or guard[2] > zhi + mz or
                guard[1] < ylo - 60.0 or guard[1] > yhi + 80.0):
            offmap += 1                                   # e.g. zz_fxx_alpha region anchors
            continue
        kept.append({"fx": pl["fx"],
                     "p": [round(c, 3) for c in wpos],
                     "s": round(pl["s"] if pl["s"] > 0 else 1.0, 4),
                     "q": [round(c, 5) for c in wquat]})

    map_dir.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(kept, separators=(",", ":")), encoding="utf-8")
    _write_keep_import(dst)
    return {"total": len(placements), "kept": len(kept), "dropped": dropped,
            "offmap": offmap, "noparent": noparent,
            "distinct": len({p["fx"] for p in kept}),
            "bytes": dst.stat().st_size, "dst": str(dst)}


def _default_zones() -> list[str]:
    """All zones that have BOTH a .opdsub and a baked terrain dir (so shipped maps get FX)."""
    if not GTD_DIR.is_dir():
        return ["moradon", "elmo2004"]
    have_opdsub = {p.stem for p in GTD_DIR.glob("*.opdsub")}
    have_terrain = {p.name for p in OUT_DIR.iterdir() if p.is_dir()} if OUT_DIR.is_dir() else set()
    # terrain dirs are lowercased; match case-insensitively
    tl = {t.lower() for t in have_terrain}
    return sorted(z for z in have_opdsub if z.lower() in tl)


def main() -> int:
    global GTD_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--zones", nargs="+", default=None, help="default: all shipped zones with a .opdsub")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--zones-dir", default=str(GTD_DIR),
                    help="directory containing the source .opdsub files")
    ap.add_argument("--no-filter", action="store_true", help="keep placements even if the fx is not baked")
    args = ap.parse_args()
    GTD_DIR = Path(args.zones_dir)
    out_dir = Path(args.out)
    baked = set() if args.no_filter else _baked_fx()
    zones = args.zones if args.zones else _default_zones()
    if not zones:
        print("[opdsub] no zones to bake", file=sys.stderr)
        return 1
    ok = 0
    for zone in zones:
        try:
            info = bake(zone, out_dir, baked)
        except (FileNotFoundError, ValueError) as e:
            print(f"[opdsub] {zone}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        ndrop = sum(info["dropped"].values())
        extra = []
        if ndrop: extra.append(f"unbaked={ndrop}({len(info['dropped'])} fx)")
        if info["offmap"]: extra.append(f"offmap={info['offmap']}")
        if info["noparent"]: extra.append(f"noparent={info['noparent']}")
        tag = (" " + " ".join(extra)) if extra else ""
        print(f"[opdsub] {zone}: {info['kept']}/{info['total']} placed, "
              f"{info['distinct']} distinct fx{tag} -> {info['bytes']}B")
        ok += 1
    return 0 if ok == len(zones) else 1


if __name__ == "__main__":
    raise SystemExit(main())
