"""bake_players.py -- decode KO PLAYER bodies into animated skinned .glb for the Godot
client to render instead of capsules.

Players are NOT in NPC_Looks. The client picks a player's body from its RACE via
Data\\UPC_DefaultLooks.tbl (the client resolves race -> body at character init). Each race row gives: [2] .n3joint  [3] .n3anim  [4..9] upper/lower/face/hands/
feet/hair .n3cpart  [18/19/20] jointRH/jointLH/jointLH2.

Two modes:
  (a) default bodies  -- one body per race with default face00/hair00.
  (b) equipped bodies -- a specific character's body: equipped armor .n3cpart REPLACE the
      matching body parts (part positions: breast->upper, leg->lower, head->hair/
      helmet, glove->hands, foot->feet) and the character's real face index is used. Keyed
      by an appearance hash (FNV-1a of race+face+armor) so World.cs resolves it, falling
      back to the race default body. Weapons/shields are plugs (attached at runtime) — not
      part of the body glb.

Equipment item id -> model file: look up Item_Org_us.tbl by
itemId//1000*1000, take dwIDResrc (col 6), then
  armor : Item\\{R//1e7}_{(R//1000)%1e4 + race:04}_{(R//10)%100:02}_{R%10}.n3cpart
  weapon: Item\\{R//1e7}_{(R//1000)%1e4:04}_{(R//10)%100:02}_{R%10}.n3cplug   (no race)

Assembly/conversion reuses bake_mobs.bake_model (KO->Godot negate-X, bind-pose verts,
quaternion sanitize, embedded textures).

Usage:
    python bake.py --only bake_players                         # all races, default look
    python bake.py --only bake_players --equipped 13 7  965001534 965002535 965003536 \\
        965004537 965005538 0 981110570 0                # race face g0..g7 (breast..lhand)
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
from _paths import ko as _ko, assets as _assets  # noqa: E402

KO_DIR = bake_mobs.KO_DIR
UPC_LOOKS = rf"{KO_DIR}\Data\UPC_DefaultLooks.tbl"
ITEM_TBL = rf"{KO_DIR}\Data\Item_Org_us.tbl"
DEFAULT_OUT = _assets() / "characters"

# UPC_DefaultLooks.tbl columns.
C_ID, C_NAME, C_JOINT, C_ANIM = 0, 1, 2, 3
C_UPPER, C_LOWER, C_FACE, C_HANDS, C_FEET, C_HAIR = 4, 5, 6, 7, 8, 9

PLAYER_RACES = [1, 2, 3, 4, 5, 6, 11, 12, 13, 14]

# Gear slot order (matches Net.cs EntityInfo/EnterInfo.Gear):
#   0 breast, 1 leg, 2 head, 3 glove, 4 foot, 5 shoulder, 6 right-hand, 7 left-hand.
# Body-affecting slots map to a body part (the rest are cloak/weapon attachments):
#   breast->UPPER, leg->LOWER, head->HAIR/HELMET, glove->HANDS, foot->FEET.
GEAR_BREAST, GEAR_LEG, GEAR_HEAD, GEAR_GLOVE, GEAR_FOOT = 0, 1, 2, 3, 4
BODY_SLOTS = [(GEAR_BREAST, C_UPPER), (GEAR_LEG, C_LOWER), (GEAR_GLOVE, C_HANDS),
              (GEAR_FOOT, C_FEET), (GEAR_HEAD, C_HAIR)]


def _variant(base: str, idx: int) -> str:
    root, ext = os.path.splitext(base)
    return f"{root}{idx:02d}{ext}"


def appearance_key(race: int, face: int, gear) -> str:
    """FNV-1a/32 of race+face+body-affecting armor (slots 0..4). Mirrored in World.cs
    so the client finds the baked equipped body by name. Weapon/shield don't affect it."""
    g = (list(gear) + [0] * 8)[:8]
    s = f"{race}_{face}_{g[0]}_{g[1]}_{g[2]}_{g[3]}_{g[4]}"
    h = 2166136261
    for b in s.encode("ascii"):
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"


_item_cache = None
_ext_cache = {}


def _item_rows():
    global _item_cache
    if _item_cache is None:
        _item_cache = {r[0]: r for r in read_tbl(ITEM_TBL).rows if r}
    return _item_cache


def _item_resrc():
    """Base item resource map retained for existing index-generation callers."""
    return {item_id: row[6] for item_id, row in _item_rows().items()}


def _extension_rows(category: int):
    if category in _ext_cache:
        return _ext_cache[category]
    data = Path(ITEM_TBL).parent
    path = data / f"Item_Ext_{category}_us.tbl"
    if not path.exists():
        path = data / f"item_ext_{category}_us.tbl"
    rows = {}
    if path.exists():
        for row in read_tbl(path).rows:
            if row and len(row) > 5:
                rows[(int(row[2]), int(row[0]))] = row
    _ext_cache[category] = rows
    return rows


def item_visual_resrc(item_id: int):
    """Retail resource precedence:
    a non-zero Item_Ext dwIDResrc (column 5) overrides Item_Org dwIDResrc.

    Item IDs are ``base + extension-id``.  Treating every equipped ID as the
    rounded base is what made items such as 160450255 (Chitin Bow) display the
    base Hunter's Bow model instead of their current retail model.
    """
    base_id = item_id // 1000 * 1000
    base = _item_rows().get(base_id)
    if base is None:
        return None
    ext_id = item_id - base_id
    if ext_id:
        category = int(base[1])
        ext = _extension_rows(category).get((base_id, ext_id))
        if ext is not None and int(ext[5]) != 0:
            return int(ext[5])
    return int(base[6]) if int(base[6]) != 0 else None


def iter_visual_item_ids():
    """Yield base items plus extension item IDs that override their visible model."""
    for base_id, base in _item_rows().items():
        yield base_id
        category = int(base[1])
        for (row_base, ext_id), ext in _extension_rows(category).items():
            if row_base == base_id and int(ext[5]) != 0:
                yield base_id + ext_id


def item_model_path(item_id: int, race: int, is_weapon: bool):
    """Equipped item id -> archive model path (.n3cpart armor / .n3cplug weapon), or None."""
    if item_id <= 0:
        return None
    v = item_visual_resrc(item_id)
    if not v:
        return None
    ext = ".n3cplug" if is_weapon else ".n3cpart"
    mid = (v // 1000) % 10000 + (0 if is_weapon else race)
    return f"Item\\{v // 10000000}_{mid:04d}_{(v // 10) % 100:02d}_{v % 10}{ext}"


def _default_body(row, face, hair):
    """The race default-look part list (joint+anim+4 body parts+face+hair)."""
    out = []
    for c in (C_JOINT, C_ANIM, C_UPPER, C_LOWER, C_HANDS, C_FEET):
        v = row[c] if c < len(row) else None
        if isinstance(v, str) and v.strip():
            out.append(v)
    if isinstance(row[C_FACE], str) and row[C_FACE].strip():
        out.append(_variant(row[C_FACE], face))
    if isinstance(row[C_HAIR], str) and row[C_HAIR].strip():
        out.append(_variant(row[C_HAIR], hair))
    return out


def _equipped_body(row, race, face, gear, resolver):
    """Part list with equipped armor replacing body parts; defaults fill empty slots.
    Face uses the character's real face index. Returns (parts, n_armor_used)."""
    parts = [row[C_JOINT], row[C_ANIM]]
    used = 0
    for gear_i, default_col in BODY_SLOTS:
        item = gear[gear_i] if gear_i < len(gear) else 0
        armor = item_model_path(item, race, is_weapon=False)
        if armor and resolver.read(armor):
            parts.append(armor)
            used += 1
        else:  # no/unknown armor -> race default for this slot
            dv = row[default_col] if default_col < len(row) else None
            if isinstance(dv, str) and dv.strip():
                parts.append(_variant(dv, 0) if default_col == C_HAIR else dv)
    if isinstance(row[C_FACE], str) and row[C_FACE].strip():
        parts.append(_variant(row[C_FACE], face))     # real face
    return [p for p in parts if isinstance(p, str) and p.strip()], used


def _stem(row) -> str:
    up = row[C_UPPER] if C_UPPER < len(row) else None
    if isinstance(up, str) and up.lower().endswith("_upper.n3cpart"):
        base = os.path.splitext(os.path.basename(up.replace("\\", "/")))[0]
        return safe_name(base[: -len("_upper")])
    jp = row[C_JOINT] if C_JOINT < len(row) else ""
    return safe_name(os.path.splitext(os.path.basename(str(jp).replace("\\", "/")))[0]) or "upc"


def _bake_default(races, face, hair, out_dir):
    looks = {r[0]: r for r in read_tbl(UPC_LOOKS).rows if r}
    resolver = AssetResolver(KO_DIR)
    index_path = out_dir / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    made = 0
    for race in races:
        row = looks.get(race)
        if row is None:
            print(f"[players] race {race}: not in UPC_DefaultLooks", file=sys.stderr)
            continue
        stem = _stem(row)
        try:
            res, warns = bake_mobs.bake_model(stem, _default_body(row, face, hair), resolver, out_dir)
        except Exception as e:
            print(f"[players] race {race} ({stem}): {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if res is None:
            print(f"[players] race {race} ({stem}): no parts ({'; '.join(warns[:2])})", file=sys.stderr)
            continue
        index[str(race)] = stem
        nbones, nskins, nclips, nbytes = res
        print(f"[players] race {race:2} {stem:13}: bones={nbones} parts={nskins} clips={nclips} -> {nbytes}B")
        made += 1
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(dict(sorted(index.items(), key=lambda kv: int(kv[0]))), indent=0))
    print(f"[players] default bodies: {made}/{len(races)} -> {out_dir}")
    return 0 if made else 1


def _bake_metadata(races, face, hair, out_dir):
    looks = {r[0]: r for r in read_tbl(UPC_LOOKS).rows if r}
    resolver = AssetResolver(KO_DIR)
    made = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for race in races:
        row = looks.get(race)
        if row is None:
            continue
        stem = _stem(row)
        try:
            anim = bake_mobs._animation_only(_default_body(row, face, hair), resolver)
        except Exception as e:
            print(f"[players] race {race} ({stem}): {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if anim is None:
            print(f"[players] race {race} ({stem}): no animation control", file=sys.stderr)
            continue
        bake_mobs._write_anim_metadata(out_dir / f"{stem}.anim.json", anim)
        print(f"[players] race {race:2} {stem:13}: {len(anim.animations)} clips -> {stem}.anim.json")
        made += 1
    print(f"[players] animation metadata: {made}/{len(races)} -> {out_dir}")
    return 0 if made else 1


def _bake_equipped(race, face, gear, out_dir):
    looks = {r[0]: r for r in read_tbl(UPC_LOOKS).rows if r}
    row = looks.get(race)
    if row is None:
        print(f"[players] race {race}: not in UPC_DefaultLooks", file=sys.stderr)
        return 1
    resolver = AssetResolver(KO_DIR)
    parts, used = _equipped_body(row, race, face, gear, resolver)
    key = appearance_key(race, face, gear)
    equip_dir = out_dir / "equip"
    try:
        res, warns = bake_mobs.bake_model(key, parts, resolver, equip_dir)
    except Exception as e:
        print(f"[players] equipped {key}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if res is None:
        print(f"[players] equipped {key}: no parts ({'; '.join(warns[:2])})", file=sys.stderr)
        return 1
    nbones, nskins, nclips, nbytes = res
    print(f"[players] equipped race {race} face {face}: key={key} armor={used}/5 "
          f"bones={nbones} parts={nskins} clips={nclips} -> {nbytes}B -> equip/{key}.glb")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Bake KO player bodies (default per-race, or one equipped character).")
    ap.add_argument("--races", nargs="+", type=int, help="race ids for default bodies (default: all)")
    ap.add_argument("--face", type=int, default=0, help="default-body face index")
    ap.add_argument("--hair", type=int, default=0, help="default-body hair index")
    ap.add_argument("--equipped", nargs="+", type=int, metavar="N",
                    help="equipped body: RACE FACE g0 g1 g2 g3 g4 g5 g6 g7 (gear = breast,leg,head,glove,foot,shoulder,rhand,lhand)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--metadata-only", action="store_true",
                    help="rewrite .anim.json sidecars only; do not rebuild GLBs")
    args = ap.parse_args()
    out_dir = Path(args.out)

    if args.metadata_only:
        return _bake_metadata(args.races or PLAYER_RACES, args.face, args.hair, out_dir)

    if args.equipped:
        vals = args.equipped
        if len(vals) < 2:
            print("--equipped needs at least RACE FACE", file=sys.stderr)
            return 2
        race, face = vals[0], vals[1]
        gear = (vals[2:] + [0] * 8)[:8]
        return _bake_equipped(race, face, gear, out_dir)

    return _bake_default(args.races or PLAYER_RACES, args.face, args.hair, out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
