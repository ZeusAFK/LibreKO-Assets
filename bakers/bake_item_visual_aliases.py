"""Bake exact Item_Ext visual aliases into the runtime equipment indexes.

The original client gives a non-zero Item_Ext dwIDResrc
precedence over Item_Org.dwIDResrc.  The original the client indexes contained only
rounded base IDs, so an equipped ``base + extension`` always displayed the base
model.  This lightweight pass adds exact IDs without rebuilding geometry:

* weapon/visual_aliases.json: exact ID -> an existing weapon/index.json key
* armor/index.json: exact ID -> overridden resource ID

Run after bake_weapons.py / bake_parts.py. Missing weapon meshes are reported and
can then be baked with ``bake_weapons.py <exact-item-id>``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bake_players  # noqa: E402
import n3_convert  # noqa: E402
from kotools.character.cplug import N3CPlug  # noqa: E402
from kotools.io.binary_reader import BinaryReader  # noqa: E402
from kotools.resolver import AssetResolver  # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402


WEAPON_INDEX = _assets() / "items" / "weapon" / "index.json"
WEAPON_ALIASES = _assets() / "items" / "weapon" / "visual_aliases.json"
ARMOR_INDEX = _assets() / "items" / "armor" / "index.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weapon-index", default=str(WEAPON_INDEX))
    ap.add_argument("--weapon-aliases", default=str(WEAPON_ALIASES))
    ap.add_argument("--armor-index", default=str(ARMOR_INDEX))
    ap.add_argument(
        "--strict",
        action="store_true",
        help="fail when a table override references a plug absent from the retail archive",
    )
    args = ap.parse_args()

    weapon_path = Path(args.weapon_index)
    alias_path = Path(args.weapon_aliases)
    armor_path = Path(args.armor_index)
    weapons = json.loads(weapon_path.read_text())
    aliases = json.loads(alias_path.read_text()) if alias_path.exists() else {}
    armor_root = json.loads(armor_path.read_text())
    armor = armor_root.setdefault("items", {})

    # The tools are used from both WSL and native Windows Python.
    ko_candidates = [
        Path(bake_players.KO_DIR),
        _ko(),
        _ko(),
    ]
    ko_dir = next((p for p in ko_candidates if (p / "Data" / "Item_Org_us.tbl").exists()), None)
    if ko_dir is None:
        raise FileNotFoundError("current KnightOnlineEn client was not found")
    bake_players.KO_DIR = str(ko_dir)
    bake_players.ITEM_TBL = str(ko_dir / "Data" / "Item_Org_us.tbl")
    bake_players._item_cache = None
    bake_players._ext_cache.clear()

    # A resource can be shared by many Item_Org rows. Index by the converted mesh
    # stem, which is authoritative after reading the current retail plug.
    by_stem = {}
    for key, record in weapons.items():
        if isinstance(record, dict) and record.get("stem"):
            # Prefer a regular Item_Org entry as the compact canonical record.
            # If a resource only exists through Item_Ext, retain one exact entry.
            candidate = by_stem.get(record["stem"])
            if candidate is None or ("resrc" in candidate[1] and "resrc" not in record):
                by_stem[record["stem"]] = (key, record)

    # Migrate expanded aliases produced by older versions of this tool.
    for key, record in list(weapons.items()):
        if not isinstance(record, dict) or "resrc" not in record or not record.get("stem"):
            continue
        canonical_key, _ = by_stem[record["stem"]]
        if key != canonical_key:
            aliases[key] = canonical_key
            del weapons[key]

    resolver = AssetResolver(ko_dir)
    resolver._ensure_archives_loaded()
    bases = bake_players._item_rows()
    weapon_added = armor_added = missing = 0
    missing_resources = set()

    for item_id in bake_players.iter_visual_item_ids():
        base_id = item_id // 1000 * 1000
        if item_id == base_id:
            continue
        base = bases.get(base_id)
        if base is None:
            continue
        slot = int(base[12])
        resrc = bake_players.item_visual_resrc(item_id)
        if not resrc:
            continue

        # UPC attach points: hand plugs are 0..4, body armor parts are 5..9.
        if 0 <= slot <= 4:
            plug_path = bake_players.item_model_path(item_id, race=0, is_weapon=True)
            blob = resolver.read(plug_path) if plug_path else None
            if not blob:
                missing += 1
                missing_resources.add((resrc, plug_path or ""))
                continue
            plug = N3CPlug.from_reader(BinaryReader(blob))
            stem = n3_convert.safe_name(n3_convert.base(plug.mesh_path))
            template_entry = by_stem.get(stem)
            if template_entry is None:
                missing += 1
                missing_resources.add((resrc, plug_path or ""))
                continue
            canonical_key, _ = template_entry
            key = str(item_id)
            if key not in weapons and aliases.get(key) != canonical_key:
                aliases[key] = canonical_key
                weapon_added += 1
        elif 5 <= slot <= 9:
            key = str(item_id)
            if armor.get(key) != resrc:
                armor[key] = resrc
                armor_added += 1

    weapon_path.write_text(json.dumps(weapons, indent=0))
    alias_path.write_text(json.dumps(dict(sorted(aliases.items(), key=lambda kv: int(kv[0]))), indent=0))
    armor_path.write_text(json.dumps(armor_root, indent=0))
    print(
        f"[item_visual_aliases] weapon +{weapon_added}, armor +{armor_added}, "
        f"missing weapon aliases={missing}"
    )
    for resrc, path in sorted(missing_resources):
        print(f"  missing resrc={resrc} plug={path}")
    return 1 if args.strict and missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
