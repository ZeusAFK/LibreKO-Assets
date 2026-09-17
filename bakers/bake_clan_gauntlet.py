"""bake_clan_gauntlet.py -- bake the clan gauntlet plugs into static .glb the client attaches
to a player's right hand by (race, clan grade).

Retail builds the plug name in code, not from a table -- the client formats
`Item\\ClanAddOn_%.3d_%d.n3cplug` with the race first and the clan grade second. The archive
holds 7 races x 5 grades:

    race   001 002 003 004 (Karus)   011 012 013 (El Morad)
    grade  1..5, matching the server's clan-grade bands

The mesh is per NATION (clanaddon_ka_<grade> / clanaddon_el_<grade>, ten in all) while the plug
is per race -- each race carries its own local transform onto the same nation mesh, which is why
there are 35 plugs for 10 meshes. Textures are whatever the plug names: grades 4 and 5 have their
own, the lower three share a clan texture.

Usage:
    python bake.py --only bake_clan_gauntlet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import n3_convert                                       # noqa: E402
import bake_players                                     # noqa: E402
import bake_weapons                                     # noqa: E402
from kotools.resolver import AssetResolver              # noqa: E402
from kotools.io.binary_reader import BinaryReader       # noqa: E402
from kotools.character.cplug import N3CPlug             # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

DEFAULT_OUT = _assets() / "items" / "clanaddon"
RACES = (1, 2, 3, 4, 11, 12, 13)
GRADES = (1, 2, 3, 4, 5)


def bake(out_dir: Path) -> dict:
    resolver = AssetResolver(str(bake_players.KO_DIR))
    index: dict[str, dict] = {}
    stem_cache: dict[str, tuple[bool, int]] = {}

    for race in RACES:
        for grade in GRADES:
            plug_path = f"Item\\ClanAddOn_{race:03d}_{grade}.n3cplug"
            blob = resolver.read(plug_path)
            if not blob:
                print(f"  missing {plug_path}")
                continue

            plug = N3CPlug.from_reader(BinaryReader(blob))
            stem = n3_convert.safe_name(n3_convert.base(plug.mesh_path))

            if stem not in stem_cache:
                mesh_blob = resolver.read(plug.mesh_path)
                if not mesh_blob:
                    print(f"  missing mesh {plug.mesh_path}")
                    continue
                pos, nrm, uv, idx = n3_convert.decode_part(mesh_blob)
                tid, tex = bake_weapons._decode_texture(resolver, plug.texture_path)
                out_dir.mkdir(parents=True, exist_ok=True)
                n3_convert.build_glb(
                    str(out_dir / f"{stem}.glb"),
                    [(bake_weapons._material_source(plug, tid, stem), pos, nrm, uv, idx)],
                    {tid: tex} if tid else {},
                    texture_dir=out_dir / "tex",
                    texture_uri_prefix="tex/",
                )
                stem_cache[stem] = (tid is not None, int(pos.shape[0]))

            gpos, gquat = bake_weapons._convert_transform(plug.position, plug.rot_matrix)
            textured, verts = stem_cache[stem]
            index[f"{race}_{grade}"] = {
                "stem": stem,
                "joint": plug.joint_index,
                "pos": gpos,
                "quat": gquat,
                "scale": list(plug.scale),
                "verts": verts,
                "tex": textured,
                "plug": plug_path.replace("\\", "/"),
                "mesh": plug.mesh_path.replace("\\", "/"),
                "texture": plug.texture_path.replace("\\", "/"),
            }

    return index


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    out_dir = Path(args.out)
    index = bake(out_dir)
    if not index:
        print("nothing baked")
        return 1

    (out_dir / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    meshes = sorted({e["stem"] for e in index.values()})
    joints = sorted({e["joint"] for e in index.values()})
    print(f"{len(index)} plugs -> {out_dir}")
    print(f"  {len(meshes)} meshes: {', '.join(meshes)}")
    print(f"  joint(s): {joints}")
    print(f"  untextured: {[k for k, e in index.items() if not e['tex']] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
