"""opd_convert.py -- One-time: decode a zone's .opd object placements and bake a
compact Godot-native placement file (mirrors bake_terrain.py).

The Godot client NEVER reads .opd directly; this script converts it offline, like
the .gtd->.hmap/.ktx and tbl->npc pipelines.

The map name uses the standard 0x0816 LCG stream cipher, verified against a real
moradon.opd (it decodes to "moradon").

Layout:
  [i32 nameLen][name (LCG 0x0816)][u32 compFlag]
  [f32 mapWidth][f32 mapHeight]
  [i32 collisionFaceCount][ Vec3 * faceCount*3 ]          collision triangle soup
  --- cell grid (SkipCellGrid): for each 16-unit cell over WxH ---
      [u32 used]; if used: [i32 cellShapeCount]( cellShapeCount*u16 )
                  then 4x4 sub-cells: [i32 polyCount]( polyCount*3*4 bytes )
  [u32 shapeCount]   (RC4-gated if compFlag>=1)
  shapeCount * __ShapeEx:
      [u32 type][lpstr name][Vec3 pos][Quat rot][Vec3 scale]
      3 anim channels: [u32 nCount]; if nCount: [u32 isQuat][f32 fps][nCount*(16|12)]
      [lpstr collisionMesh1][lpstr collisionMesh2]
      [i32 partCount] * { Vec3 pivot; lpstr mesh; byte[92] material(RenderFlags@80);
                          u32 texCount; f32 texFps; texCount*lpstr }
      [i32 belong][i32 eventId][i32 eventType][i32 npcId][i32 npcStatus]
      NEW-format quirk: after shape (shapeCount/2 - 1), one extra [i32 len][len bytes].

Output: <output>/terrain/<zone>/placements.json (Godot-native; read via
FileAccess + JSON), a list of {name, p:[x,y,z], r:[qx,qy,qz,qw], s:[sx,sy,sz]} in
KO space, plus {e, et, b, n} = eventId/eventType/belong/npcId on the few shapes that
carry an object event (warp gates, bind stones, gates, anvils). A sibling .import
(importer="keep") ships it verbatim.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

_HERE = Path(__file__).resolve().parent
GTD_DIR = str(_ko() / "Zones")
OUT_DIR = _assets() / "terrain"
ZONES_DEFAULT = ["moradon", "elmo2004"]

RC4_KEY = bytes([0xf5, 0x06, 0x21, 0x61, 0x7d, 0x29, 0x19, 0x71,
                 0x5c, 0x81, 0x8d, 0x9d, 0xe9, 0xc9, 0xfd, 0x20])


def _rc4(data: bytes) -> bytes:
    S = list(range(256)); j = 0
    for i in range(256):
        j = (j + S[i] + RC4_KEY[i % 16]) & 0xFF
        S[i], S[j] = S[j], S[i]
    out = bytearray(); i = j = 0
    for b in data:
        i = (i + 1) & 0xFF; j = (j + S[i]) & 0xFF
        S[i], S[j] = S[j], S[i]
        out.append(b ^ S[(S[i] + S[j]) & 0xFF])
    return bytes(out)


def _lcg_name(enc: bytes) -> str:
    state = 0x0816
    out = bytearray()
    for b in enc:
        out.append(((state >> 8) & 0xFF) ^ b)
        state = ((b + state) * 0x6081 + 0x1608) & 0xFFFF
    return out.decode("latin-1", "replace").rstrip("\x00")


class _R:
    """Little-endian cursor over the .opd bytes."""
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]; self.p += 4; return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v

    def f32(self):
        v = struct.unpack_from("<f", self.d, self.p)[0]; self.p += 4; return v

    def bytes(self, n):
        v = self.d[self.p:self.p + n]; self.p += n; return v

    def vec3(self):
        return (self.f32(), self.f32(), self.f32())

    def lpstr(self, maxlen=4096):
        n = self.i32()
        if n < 0 or n > maxlen:
            raise ValueError(f"bad string len {n} at {self.p - 4}")
        if n == 0:
            return ""
        return self.bytes(n).split(b"\x00", 1)[0].decode("latin-1", "replace")

    def gated_u32(self, use_rc4):
        raw = self.bytes(4)
        return struct.unpack("<I", _rc4(raw) if use_rc4 else raw)[0]


def _skip_cell_grid(r: _R, w: float, h: float):
    cells_z = 0; fz = 0.0
    while fz < h:
        fz += 16.0; cells_z += 1
    cells_x = 0; fx = 0.0
    while fx < w:
        fx += 16.0; cells_x += 1
    for _z in range(cells_z):
        for _x in range(cells_x):
            if r.u32() != 0:
                cell_shapes = r.i32()
                if cell_shapes > 0:
                    if cell_shapes > 65535:
                        raise ValueError(f"cell-shape count {cell_shapes}")
                    r.bytes(cell_shapes * 2)
                for _ in range(16):
                    poly = r.i32()
                    if poly > 0:
                        if poly > 1_000_000:
                            raise ValueError(f"sub-cell poly {poly}")
                        r.bytes(poly * 3 * 4)


def _read_shape(r: _R):
    stype = r.u32()
    name = r.lpstr()
    pos = r.vec3()
    rot = (r.f32(), r.f32(), r.f32(), r.f32())
    scale = r.vec3()
    for _ in range(3):
        n = r.u32()
        if n != 0:
            if n > 100000:
                raise ValueError(f"anim-key count {n}")
            is_quat = r.u32() != 0
            r.f32()
            r.bytes(n * (16 if is_quat else 12))
    r.lpstr(1024)   # collision mesh 1
    r.lpstr(1024)   # collision mesh 2
    part_count = r.i32()
    if part_count < 0 or part_count > 4096:
        raise ValueError(f"partCount {part_count}")
    for _ in range(part_count):
        r.vec3()                 # pivot
        r.lpstr(1024)            # mesh name
        r.bytes(92)              # material struct
        tex_count = r.u32()
        r.f32()                  # texture fps
        if tex_count > 256:
            raise ValueError(f"texCount {tex_count}")
        for _ in range(tex_count):
            r.lpstr(1024)
    belong = r.i32(); event_id = r.i32(); event_type = r.i32()
    npc_id = r.i32(); npc_status = r.i32()
    return {
        "type": stype, "name": name, "pos": pos, "rot": rot, "scale": scale,
        "belong": belong, "event_id": event_id, "event_type": event_type,
        "npc_id": npc_id, "npc_status": npc_status,
    }


def read_opd(path: str) -> dict:
    r = _R(Path(path).read_bytes())
    name_len = r.i32()
    name = ""
    if 0 < name_len <= 255:
        name = _lcg_name(r.bytes(name_len))
    comp_flag = r.u32()
    map_w = r.f32()
    map_h = r.f32()
    # Sanity-guard the map size BEFORE _skip_cell_grid: a few zones (clanfight, freezone,
    # *_start, ...) use a variant .opd header this parser misreads, yielding astronomical
    # w/h that would make the cell-grid walk loop ~forever. Skip them instead of hanging.
    if not (0.0 < map_w <= 8192.0 and 0.0 < map_h <= 8192.0):
        raise ValueError(f"unsupported .opd header (map {map_w:g}x{map_h:g}) — variant/newer format")
    face_count = r.i32()
    if face_count < 0 or face_count > 1_000_000:
        raise ValueError(f"collisionFaceCount {face_count}")
    r.bytes(face_count * 3 * 12)            # collision verts (3 vec3 per face)
    _skip_cell_grid(r, map_w, map_h)
    shape_count = r.gated_u32(comp_flag >= 1)
    if shape_count < 0 or shape_count > 200000:
        raise ValueError(f"shapeCount {shape_count} at {r.p}")
    is_new = name_len != 1
    halfway = shape_count // 2
    shapes = []
    for j in range(shape_count):
        shapes.append(_read_shape(r))
        if is_new and j == halfway - 1:
            slen = r.i32()
            if 0 < slen < 4096:
                r.bytes(slen)
    return {
        "name": name, "comp_flag": comp_flag, "map_w": map_w, "map_h": map_h,
        "face_count": face_count, "shapes": shapes,
        "leftover": len(r.d) - r.p, "size": len(r.d),
    }


def _write_keep_import(p: Path) -> None:
    """Godot 'keep' import: ship verbatim so the client reads raw bytes via FileAccess."""
    (p.parent / (p.name + ".import")).write_text('[remap]\n\nimporter="keep"\n', encoding="utf-8")


def bake(zone: str, out_dir: Path) -> dict:
    info = read_opd(str(Path(GTD_DIR) / f"{zone}.opd"))
    shapes = info["shapes"]
    map_dir = out_dir / zone.lower()        # assets/terrain/<name>/
    map_dir.mkdir(parents=True, exist_ok=True)
    dst = map_dir / "placements.json"
    items = []
    for s in shapes:
        item = {"name": s["name"],
                "p": [float(v) for v in s["pos"]],
                "r": [float(v) for v in s["rot"]],
                "s": [float(v) for v in s["scale"]]}
        if s["event_id"] > 0:
            item["e"] = int(s["event_id"])
            item["et"] = int(s["event_type"])
            item["b"] = int(s["belong"])
            item["n"] = int(s["npc_id"])
        items.append(item)
    dst.write_text(json.dumps(items, separators=(",", ":")), encoding="utf-8")
    _write_keep_import(dst)
    info["bytes"] = dst.stat().st_size
    info["dst"] = str(dst)
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zones", nargs="+", default=ZONES_DEFAULT)
    ap.add_argument("--all", action="store_true",
                    help="every zone with both an .opd and a baked terrain dir")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    out_dir = Path(args.out)
    zones = args.zones
    if args.all:
        if not out_dir.is_dir():
            print(f"[opd] no baked terrain at {out_dir} -- run the terrain stage first",
                  file=sys.stderr)
            return 1
        have = {d.name.lower() for d in out_dir.iterdir() if d.is_dir()}
        zones = sorted(p.stem for p in Path(GTD_DIR).glob('*.opd')
                       if p.stem.lower() in have)
    ok = 0
    for zone in zones:
        try:
            if args.verify_only:
                info = read_opd(str(Path(GTD_DIR) / f"{zone}.opd"))
            else:
                info = bake(zone, out_dir)
        except Exception as e:
            print(f"[opd] {zone}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        shapes = info["shapes"]
        named = [s for s in shapes if s["name"]]
        xs = [s["pos"][0] for s in shapes] or [0]
        ys = [s["pos"][1] for s in shapes] or [0]
        zs = [s["pos"][2] for s in shapes] or [0]
        distinct = len({s["name"] for s in named})
        leftover = info["leftover"]
        tag = "OK" if leftover == 0 else f"LEFTOVER={leftover}"
        print(f"[opd] {zone}: name={info['name']!r} comp={info['comp_flag']} "
              f"map={info['map_w']:.0f}x{info['map_h']:.0f} shapes={len(shapes)} "
              f"named={len(named)} distinct={distinct} "
              f"X[{min(xs):.0f},{max(xs):.0f}] Y[{min(ys):.0f},{max(ys):.0f}] "
              f"Z[{min(zs):.0f},{max(zs):.0f}] {tag}"
              + (f" -> {info.get('bytes')}B" if not args.verify_only else ""))
        for s in shapes[:4]:
            print(f"        {s['name']!r} pos={tuple(round(v,1) for v in s['pos'])} "
                  f"scale={tuple(round(v,2) for v in s['scale'])}")
        ok += 1
    if ok != len(zones):
        print(f"[opd] {len(zones) - ok} zone(s) could not be decoded; see above", file=sys.stderr)
    return 0 if ok and (args.all or ok == len(zones)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
