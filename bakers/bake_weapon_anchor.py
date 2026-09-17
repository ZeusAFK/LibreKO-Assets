"""bake_weapon_anchor.py -- bake each weapon's original FX guide mesh and anchor.

KO weapons embed an "FX guide mesh" in the .n3cplug (its embedded guide PMesh) that marks WHERE
the enchant glow goes — i.e. the blade. We decode it (kotools N3CPlug.embedded_mesh_data -> the same
.n3pmesh format the visible mesh uses) and record its centroid + vertical half-extent, in the SAME
negate-X space as the baked weapon .glb, so World.AttachWeaponGlow can anchor the aura on the real blade
instead of guessing from the mesh AABB (which lands off-blade on curved weapons like the scythe).

Patches <output>/items/weapon/index.json in place (adds "fxp", "fxr", and "fxg") and writes one
small untextured GLB per distinct guide to items/weapon/fxguide. Legacy uses this exact mesh with the
original KO flipbook texture; modern may still use the anchor for procedural presentation.

Usage:  python bake.py --only bake_weapon_anchor
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import n3_convert                                        # noqa: E402
import bake_players                                      # noqa: E402
from kotools.resolver import AssetResolver               # noqa: E402
from kotools.io.binary_reader import BinaryReader        # noqa: E402
from kotools.character.cplug import N3CPlug              # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

WEAPON_DIR = _assets() / "items" / "weapon"
_P = np.diag([-1.0, 1.0, 1.0])   # negate-X (KO -> Godot), matches n3_convert.build_glb


def main() -> int:
    index_path = WEAPON_DIR / "index.json"
    index = json.loads(index_path.read_text())
    res = AssetResolver(bake_players.KO_DIR)
    res._ensure_archives_loaded()
    guide_dir = WEAPON_DIR / "fxguide"
    guide_dir.mkdir(parents=True, exist_ok=True)

    done = miss = 0
    baked_guides: set[str] = set()
    for key in index:
        base = int(key)
        plug_path = bake_players.item_model_path(base, race=0, is_weapon=True)
        blob = res.read(plug_path) if plug_path else None
        if not blob:
            miss += 1
            continue
        try:
            plug = N3CPlug.from_reader(BinaryReader(blob))
        except Exception:
            miss += 1
            continue
        guide = plug.embedded_mesh_data
        if not guide:
            miss += 1
            continue   # no FX guide mesh -> leave the record (AABB fallback at runtime)
        try:
            gpos, gnrm, guv, gidx = n3_convert.decode_part(guide)
        except Exception:
            miss += 1
            continue
        gv = np.asarray(gpos, float) @ _P.T
        centroid = gv.mean(axis=0)
        ext = gv.max(axis=0) - gv.min(axis=0)
        radius = float(np.clip(ext[1] * 0.5, 0.2, 1.2))   # vertical half-extent -> natural flame height
        index[key]["fxp"] = [round(float(centroid[0]), 4), round(float(centroid[1]), 4), round(float(centroid[2]), 4)]
        index[key]["fxr"] = round(radius, 4)
        guide_stem = n3_convert.safe_name(index[key]["stem"])
        index[key]["fxg"] = guide_stem
        if guide_stem not in baked_guides:
            source = {
                "tid": "",
                "name": f"{guide_stem}_fxguide",
                "render_flags": 0,
                "src_blend": 5,
                "dest_blend": 6,
                "diffuse": [1.0, 1.0, 1.0, 1.0],
                "emissive": [0.0, 0.0, 0.0],
                "pivot": [0.0, 0.0, 0.0],
            }
            n3_convert.build_glb(
                str(guide_dir / f"{guide_stem}.glb"),
                [(source, gpos, gnrm, guv, gidx)],
                {},
            )
            baked_guides.add(guide_stem)
        done += 1

    index_path.write_text(json.dumps(index, indent=0))
    print(f"[weapon_anchor] {done} weapons got FX guide data ({len(baked_guides)} distinct GLBs); "
          f"{miss} without (AABB fallback) -> {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
