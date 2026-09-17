"""bake_parts.py -- bake individual KO body/armor PARTS as skinned .glb that World.cs
grafts onto a race body rig at runtime (modular character assembly = no per-character
body bake).

Each part (.n3cpart: armor piece or face/hair) is baked skinned to its RACE skeleton
(UPC_DefaultLooks joint), exactly like the body rig, so its skin binds 1:1 to the rig's
shared Skeleton3D when grafted. One MeshInstance3D + the rig skeleton, no animation.

Runtime resolution (mirrors World.cs):
  - armor: item base id -> dwIDResrc (Item_Org_us.tbl) -> stem
           f"{R//1e7}_{(R//1000)%1e4 + race}_{(R//10)%100}_{R%10}"  -> items/armor/<stem>.glb
  - face/hair: race face/hair base name (UPC_DefaultLooks) + 2-digit index
           e.g. upc_el_rf_face07 -> characters/upc_el_rf_face07.glb  (co-located with body rigs)
Indexes: items/armor/index.json = {items:{baseId:resrc}}; characters/faces.json = {races:{race:{facePart,hairPart}}}.

Usage:
    python bake.py --only bake_parts --race 13 --face 7 --items 965001000 965002000 965003000 965004000 965005000
    python bake.py --only bake_parts --all          # all armor items x playable races + all face/hair variants
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from tbl_reader import read_tbl                    # noqa: E402
from n3_convert import safe_name                   # noqa: E402
from kotools.resolver import AssetResolver         # noqa: E402
import bake_mobs                                    # noqa: E402
import bake_players                                 # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

KO_DIR = bake_players.KO_DIR
ARMOR_OUT = _assets() / "items" / "armor"   # equippable armor parts (KO Item\)
FACE_OUT = _assets() / "characters"            # face/hair parts (character appearance)
C = bake_players  # column constants C.C_JOINT etc.
PLAYER_RACES = bake_players.PLAYER_RACES


def _part_stem(path: str) -> str:
    return safe_name(os.path.splitext(os.path.basename(str(path).replace("\\", "/")))[0])


def _bake_part(stem, joint_path, part_path, resolver, out_dir, baked):
    """Bake one part (joint + single .n3cpart, no anim) -> parts/<stem>.glb. Dedup by stem."""
    if stem in baked:
        return baked[stem]
    if not part_path or not resolver.read(part_path):
        baked[stem] = False
        return False
    try:
        res, _ = bake_mobs.bake_model(stem, [joint_path, part_path], resolver, out_dir, strip_anim=True)
    except Exception as e:
        print(f"[parts] {stem}: {type(e).__name__}: {e}", file=sys.stderr)
        baked[stem] = False
        return False
    baked[stem] = res is not None
    return baked[stem]


def main():
    ap = argparse.ArgumentParser(description="Bake KO body/armor parts -> skinned .glb for runtime grafting.")
    ap.add_argument("--race", type=int, help="race id (prototype mode)")
    ap.add_argument("--face", type=int, default=0, help="face variant to bake (prototype)")
    ap.add_argument("--items", nargs="*", type=int, default=[], help="armor item ids (prototype)")
    ap.add_argument("--all", action="store_true", help="all armor items x playable races + all face/hair")
    args = ap.parse_args()
    armor_out, face_out = ARMOR_OUT, FACE_OUT
    resolver = AssetResolver(KO_DIR)
    resolver._ensure_archives_loaded()
    archive_keys = set(resolver._archive_index.keys())
    looks = {r[0]: r for r in read_tbl(bake_players.UPC_LOOKS).rows if r}
    def have(path):
        return bool(path) and path.split("\\")[-1].lower() in archive_keys

    armor_index_path = armor_out / "index.json"        # {items:{baseId:resrc}}
    faces_index_path = face_out / "faces.json"          # {races:{race:{facePart,hairPart}}}
    armor_index = json.loads(armor_index_path.read_text()) if armor_index_path.exists() else {}
    armor_index.setdefault("items", {})
    faces_index = json.loads(faces_index_path.read_text()) if faces_index_path.exists() else {}
    faces_index.setdefault("races", {})
    baked = {}
    nparts = 0

    # face/hair index: per-race base names (rig grafts <facePart><NN>.glb from assets/characters/).
    for race in (PLAYER_RACES if args.all else ([args.race] if args.race else [])):
        row = looks.get(race)
        if not row:
            continue
        face_base = row[C.C_FACE] if isinstance(row[C.C_FACE], str) else ""
        hair_base = row[C.C_HAIR] if isinstance(row[C.C_HAIR], str) else ""
        faces_index["races"][str(race)] = {
            "facePart": _stem_base(face_base),
            "hairPart": _stem_base(hair_base),
        }

    def bake_for_race(race, faces, items):
        nonlocal nparts
        row = looks.get(race)
        if not row:
            return
        joint = row[C.C_JOINT]
        # face + hair variants -> assets/characters/ (character appearance, with the body rigs)
        for base_col, variants in ((C.C_FACE, faces), (C.C_HAIR, faces)):
            base = row[base_col]
            if not isinstance(base, str) or not base.strip():
                continue
            for v in variants:
                path = bake_players._variant(base, v)
                if have(path) and _bake_part(_part_stem(path), joint, path, resolver, face_out, baked):
                    nparts += 1
        # armor items -> assets/items/armor/ (equippable, KO Item\)
        for item in items:
            # Keep the exact item ID. Item_Ext may replace dwIDResrc, so two
            # extensions of one base item can legitimately graft different parts.
            path = bake_players.item_model_path(item, race, is_weapon=False)
            if not have(path):
                continue
            if _bake_part(_part_stem(path), joint, path, resolver, armor_out, baked):
                nparts += 1
            visual_resrc = bake_players.item_visual_resrc(item)
            if visual_resrc:
                armor_index["items"][str(item)] = visual_resrc

    if args.all:
        armor_items = list(bake_players.iter_visual_item_ids())
        for race in PLAYER_RACES:
            bake_for_race(race, list(range(0, 10)), armor_items)   # faces/hairs 00-09
    elif args.race:
        bake_for_race(args.race, [args.face], args.items)
    else:
        print("give --race (+--items) or --all", file=sys.stderr)
        return 2

    armor_out.mkdir(parents=True, exist_ok=True)
    face_out.mkdir(parents=True, exist_ok=True)
    armor_index_path.write_text(json.dumps(armor_index, indent=0))
    faces_index_path.write_text(json.dumps(faces_index, indent=0))
    print(f"[parts] baked {nparts} part glbs ({sum(1 for v in baked.values() if v)} distinct ok) "
          f"-> armor:{armor_out.name} face/hair:{face_out.name}  "
          f"(index: {len(armor_index['items'])} armor items, {len(faces_index['races'])} races)")
    return 0 if nparts else 1


def _stem_base(path: str) -> str:
    """'item\\upc_el_rf_face.n3cpart' -> 'upc_el_rf_face' (the variant prefix)."""
    return safe_name(os.path.splitext(os.path.basename(str(path).replace("\\", "/")))[0]) if path else ""


if __name__ == "__main__":
    raise SystemExit(main())
