"""bake_itembox.py -- bake a KO loot-chest .n3shape into a static .glb.

KO has no separate "treasure chest" for mob drops in-engine (the retail client marks
the corpse as lootable), but it ships real ItemBox props. Two sources:

  * loose files under the client's Misc\\ dir -- `Misc\\itembox.n3shape` (the small old box), and
  * the OBJECT ARCHIVE (Object\\object.hdr/.src), which holds the 2022 chest set that no map
    places, so the .opd-driven object bake never sees it: `itembox_jo_a1..a3` / `itembox_jo_elmo`
    / `itembox_jo_k1..k3` / `itembox_jo_karu` (texture `itembox2022_*.dxt`).

Either is baked the same way static world objects are (n3_convert.decode_part -> build_glb:
pivot offset, negate-X, reversed winding, embedded texture, KO render state kept in the material
name for World.Environment to re-apply) so World.Loot can drop an authentic KO box.

The shape is looked up loose first, then in the object archive, so a bare shape NAME works.

Usage:
    python bake.py --only bake_itembox                                  # Misc/itembox.n3shape
    python bake.py --only bake_itembox --shape itembox_jo_a1.n3shape --stem itembox_jo_a1
    python bake.py --only bake_itembox --ko "<retail install>" --out <output>/objects
    python bake.py --only bake_itembox --shape Misc/Co_targetsymbol.n3shape --stem legacy_move_target
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import n3_convert                                        # noqa: E402
from kotools.shape.shape import read_shape               # noqa: E402
from kotools.ntf.texture import read_dxt_from_bytes      # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

DEFAULT_KO = _ko()
DEFAULT_OUT = _assets() / "objects"
SHAPE = "Misc/itembox.n3shape"
STEM = "itembox"


class Source:
    """Loose-file-first, object-archive-second blob lookup by KO path or bare basename."""

    def __init__(self, ko_dir: Path):
        self.ko_dir = ko_dir
        self._arc = None

    @property
    def arc(self):
        if self._arc is None:
            self._arc = n3_convert.Archive(n3_convert.OBJ_HDR, n3_convert.OBJ_SRC)
        return self._arc

    def get(self, ko_path: str) -> bytes | None:
        rel = ko_path.replace("\\", "/")
        for p in (self.ko_dir / rel, self.ko_dir / "Misc" / Path(rel).name):
            if p.exists():
                return p.read_bytes()
        return self.arc.get(n3_convert.base(rel))   # archive keys are lowercased basenames


def _rgba(c) -> tuple[float, float, float, float]:
    """kotools ColorValue -> plain tuple (build_glb rounds/serialises these)."""
    return (float(c.r), float(c.g), float(c.b), float(c.a))


def main() -> int:
    ap = argparse.ArgumentParser(description="Bake a KO loot-chest .n3shape into a static GLB")
    ap.add_argument("--ko", default=str(DEFAULT_KO), help="KO client dir (contains Misc/, Object/)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--shape", default=SHAPE, help="KO-relative .n3shape path, or a bare archive name")
    ap.add_argument("--stem", default=STEM, help="output GLB filename without extension")
    args = ap.parse_args()
    src = Source(Path(args.ko))
    out_dir = Path(args.out)

    shape_blob = src.get(args.shape)
    if shape_blob is None:
        print(f"ERROR: shape not found loose or in the object archive: {args.shape}", file=sys.stderr)
        return 1
    # read_shape wants a path: stage the blob next to the output so archive shapes work too.
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = out_dir / f".{Path(args.shape).name}"
    staged.write_bytes(shape_blob)
    try:
        shape = read_shape(str(staged))
    finally:
        staged.unlink(missing_ok=True)

    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    textures: dict[str, tuple[bytes, bool]] = {}
    for part_index, part in enumerate(shape.parts):
        mesh_blob = src.get(part.mesh_path)
        if mesh_blob is None:
            print(f"ERROR: mesh not found: {part.mesh_path}", file=sys.stderr)
            return 1
        pos, nrm, uv, idx = n3_convert.decode_part(mesh_blob)
        if pos.shape[0] == 0 or idx.shape[0] == 0:
            continue
        pos = pos + np.array(part.pivot, "<f4")

        texs = [t for t in part.texture_paths if t]
        tid = n3_convert.tex_id(texs[0]) if texs else ""
        if tid and tid not in textures:
            try:
                blob = src.get(texs[0])
                if blob is None:
                    raise FileNotFoundError(texs[0])
                img = read_dxt_from_bytes(blob).to_pil_image()
                buf = io.BytesIO(); img.save(buf, "PNG")
                has_alpha = img.mode == "RGBA" and img.getextrema()[3][0] < 250
                textures[tid] = (buf.getvalue(), bool(has_alpha))
            except Exception as e:
                print(f"[misc-shape] texture decode failed ({texs[0]}): {e}", file=sys.stderr)
                tid = ""

        m = part.material
        # Same source dict + material naming the object bake uses, so KO render state that glTF
        # can't express (additive blend, no depth write, point sampling) survives to the runtime.
        source = {
            "tid": tid,
            "render_flags": m.render_flags,
            "src_blend": m.src_blend,
            "dest_blend": m.dest_blend,
            "diffuse": _rgba(m.diffuse),
            "emissive": _rgba(m.emissive),
            "pivot": part.pivot,
            "name": (
                f"{n3_convert.safe_name(os.path.splitext(n3_convert.base(part.mesh_path))[0])}_p{part_index}"
                f"__ko_f{m.render_flags:x}_s{m.src_blend}_d{m.dest_blend}"
            ),
        }
        # One primitive per distinct material; geometry sharing a material is merged.
        key = (tid, m.render_flags, m.src_blend, m.dest_blend,
               source["diffuse"], source["emissive"])
        g = groups.get(key)
        if g is None:
            g = {"src": source, "P": [], "N": [], "U": [], "I": [], "voff": 0}
            groups[key] = g; order.append(key)
        g["I"].append(idx + g["voff"])
        g["P"].append(pos); g["N"].append(nrm); g["U"].append(uv)
        g["voff"] += pos.shape[0]

    if not order:
        print("ERROR: no usable mesh parts", file=sys.stderr)
        return 1

    out_groups, mins, maxs = [], [], []
    for key in order:
        g = groups[key]
        gp = np.concatenate(g["P"]); gn = np.concatenate(g["N"])
        gu = np.concatenate(g["U"]); gi = np.concatenate(g["I"])
        mins.append(gp.min(axis=0)); maxs.append(gp.max(axis=0))
        out_groups.append((g["src"], gp, gn, gu, gi))

    out_path = out_dir / f"{args.stem}.glb"
    n3_convert.build_glb(str(out_path), out_groups, textures,
                         texture_dir=out_dir / "tex", texture_uri_prefix="tex/")

    lo = np.min(mins, axis=0); hi = np.max(maxs, axis=0); size = hi - lo
    print(f"[misc-shape] {out_path}  parts={len(out_groups)} verts={sum(g[1].shape[0] for g in out_groups)} "
          f"tex={'yes' if textures else 'NO'}  KO-size XYZ=({size[0]:.2f},{size[1]:.2f},{size[2]:.2f})  "
          f"base-Y={lo[1]:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
