"""bake_weapon_fx.py -- bake the equipped-weapon enchant GLOW map (the worn blade shine).

Derived from the retail client (the format notes). The element/enchant glow on an
equipped weapon is ONE table lookup, NOT a byDamage* path:

    weapon item id -> base = id//1000*1000 -> Item_Org row -> category = col1 (byExtIndex)
                   -> ext  = id % 1000     -> Item_Ext_<cat>_us.tbl[ext] -> COLUMN 4 = glow fx-id
        fx.tbl[fxid] (col2) -> "fx\\<name>.fxb"   (the worn blade glow, attached to the weapon)

col4 == 0 => plain weapon, no glow. Verified: Item_Ext_8[49] col4=12031 -> poison_sword0_1 (Raptor).
The 120XX series = fire 12001 / ice 12011 / lightning 12021 / poison 12031 (each +1 tail / +2 target,
which are COMBAT effects -- not baked here). Other non-zero col4 (32xxx/39xxx/40xxx) = tier glows,
resolved generically through fx.tbl.

Output `<output>/items/weapon/glow.json`:
    { "weaponCat": { "<baseId>": <cat> },              # only baked weapons (items/weapon/index.json)
      "cats":      { "<cat>": { "<ext>": "<fxName>" } } # non-zero col4 that resolve to a BAKED fx }
Runtime (World.AttachWeapons): cat = weaponCat[base]; name = cats[cat][id%1000]; Fx.Spawn(name, weapon).

Usage:
    python bake.py --only bake_weapon_fx
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bake_players                                   # noqa: E402  (KO_DIR, ITEM_TBL)
from tbl_reader import read_tbl                       # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

DATA = Path(bake_players.KO_DIR) / "Data"
ITEM_ORG = Path(bake_players.ITEM_TBL)                # Data\Item_Org_us.tbl
FX_TBL = DATA / "fx.tbl"
WEAPON_DIR = _assets() / "items" / "weapon"
FX_DIR = _assets() / "fx"

# fx.tbl columns: [u32 fxId, str(unused), str fxbPath, u32 soundId, byte, byte]
_FX_ID, _FX_PATH = 0, 2
# Item_Org columns: [u32 dwID, u8 byExtIndex(=category), ...]
_ORG_ID, _ORG_CAT = 0, 1
# Item_Ext columns: [u32 ext, str name, u32 linked, str desc, u32 glowFxId(col4), ...]
_EXT_KEY, _EXT_GLOW = 0, 4


def _fx_stem(path: str) -> str | None:
    """'fx\\poison_sword0_1.fxb' -> 'poison_sword0_1' iff that fx is baked (assets/fx/<stem>.json)."""
    stem = path.replace("/", "\\").split("\\")[-1]
    if stem.lower().endswith(".fxb"):
        stem = stem[:-4]
    if (FX_DIR / f"{stem}.json").exists():
        return stem
    # case-insensitive fallback (fx.tbl mixes case; baked names may differ)
    for j in FX_DIR.glob("*.json"):
        if j.stem.lower() == stem.lower():
            return j.stem
    return None


def main() -> int:
    index_path = WEAPON_DIR / "index.json"
    if not index_path.exists():
        print("run bake_weapons.py first (no items/weapon/index.json)", file=sys.stderr)
        return 2
    weapon_ids = {int(k) for k in json.loads(index_path.read_text())}

    # fx.tbl: fxId -> baked fx stem (skip ids whose fx isn't baked)
    fx_stem = {}
    for r in read_tbl(str(FX_TBL)).rows:
        if r and isinstance(r[_FX_ID], int) and isinstance(r[_FX_PATH], str):
            s = _fx_stem(r[_FX_PATH])
            if s:
                fx_stem[r[_FX_ID]] = s

    # Item_Org: base id -> category (col1), restricted to baked weapons
    org_cat = {}
    for r in read_tbl(str(ITEM_ORG)).rows:
        if r and isinstance(r[_ORG_ID], int) and r[_ORG_ID] in weapon_ids:
            org_cat[r[_ORG_ID]] = int(r[_ORG_CAT])

    # for each category in use, Item_Ext_<cat>_us.tbl: ext -> glow fx stem (non-zero col4 only)
    cats: dict[str, dict[str, str]] = {}
    tails: dict[str, dict[str, str]] = {}
    missing_fx = set()
    for cat in sorted(set(org_cat.values())):
        ext_tbl = DATA / f"Item_Ext_{cat}_us.tbl"
        if not ext_tbl.exists():
            continue
        rows = {}
        for r in read_tbl(str(ext_tbl)).rows:
            if not r or not isinstance(r[_EXT_KEY], int) or len(r) <= _EXT_GLOW:
                continue
            glow = r[_EXT_GLOW]
            if not isinstance(glow, int) or glow == 0:
                continue
            stem = fx_stem.get(glow)
            if stem:
                # The tail is ALWAYS fx.tbl[glow + 1] — retail looks up the glow row and
                # the row after it and attaches BOTH at equip (the table object is
                # Datax.tbl). The names are not derivable from the glow's: a sword enchant is
                # fire_sword0_1 / fire_sword_tail0_1 but a BOW enchant is fire_bow_1 / fire_bowsp_1,
                # so it has to come from the table.
                rows[str(r[_EXT_KEY])] = stem
                tail = fx_stem.get(glow + 1)
                if tail:
                    tails.setdefault(str(cat), {})[str(r[_EXT_KEY])] = tail
            else:
                missing_fx.add(glow)
        if rows:
            cats[str(cat)] = rows

    out = {"weaponCat": {str(b): c for b, c in sorted(org_cat.items())},
           "cats": cats, "tails": tails}
    (WEAPON_DIR / "glow.json").write_text(json.dumps(out, indent=0))
    n_glow = sum(len(v) for v in cats.values())
    print(f"[weapon_fx] {len(org_cat)} weapons, {len(cats)} categories, {n_glow} (cat,ext) glow entries "
          f"-> {WEAPON_DIR / 'glow.json'}")
    if missing_fx:
        print(f"[weapon_fx] {len(missing_fx)} glow fx-ids skipped (fx not baked): "
              f"{sorted(missing_fx)[:12]}{'...' if len(missing_fx) > 12 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
