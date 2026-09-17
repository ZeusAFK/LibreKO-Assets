"""bake_wings.py -- decode KO COSPRE ATTACHMENTS into animated skinned .glb for the Godot client.

What the original client does when it equips and animates a wing:

  * The catalogue is `Data\\ChrTypePartItem.tbl`, loaded once at startup --
    which is the exact table `SetWing` looks up. `Data\\Wing.tbl` is DEAD legacy data: the string
    "Wing.tbl" appears nowhere in the retail client, the file is from 2013 against a 2025
    ChrTypePartItem, and its only two unique ids have no `Item_Org` row.

  * Columns (`[UINT, STRING, INT, BYTE, BYTE, BYTE]`):
        0 item id
        1 `chr\\<name>.n3chr`
        2 pre-Load part/plug vector resize -- `Load` overwrites it, no runtime meaning
        3 BONE INDEX into the wearer's joint matrices (`player+432`, 64-byte stride, bounds-checked)
        4 ATTACH SLOT 0..3
        5 slot-1 flag (only read when slot == 1; set on the three "Oreads" rows)

    The wing holder keeps four of everything (model, active flag, bone, item id) because column 4
    is a real slot, so a character wears up to four
    of these at once. The slot lines up exactly with the item's cospre slot:

        slot 0 = wings (item slot 110)      slot 2 = talisman (113)
        slot 1 = fairy (111)                slot 3 = emblem   (114)

  * Bone 2 is `Chest2` on the 24-joint player skeletons; three rows use bone 0 (`Hips`).
    KURIAN (races 6/14, `upc_kurian.n3joint`, 53 joints) IGNORES column 3 -- retail passes bone 44
    = `joint30`, a dedicated socket parented to `Chest2`, and scales the attachment 1.1x, but only
    while the player is NOT transformed (a flag on the player).

  * Each attachment is a full `.n3chr` (own joints, own skinned parts, own `.n3anim`) whose clips
    mirror the wearer's state: 0 run, 1 breath, 2 hit, 3 attack, 4 sit, 5 die. The wing tick
    drives them from the wearer: moving -> clip 0 at speed 1.35, state 1 -> clip 3,
    state 3 -> clip 2, state 5 -> clip 5, otherwise clip 1. Clip 4 is never selected by retail.

Output (<output>/wings/):
    index.json          {"<itemId>": {"model": "<stem>", "bone": <n>, "slot": <n>}, "_kurianBone": 44}
    <stem>.glb          animated skinned attachment, textures in tex/

Meshes/skeletons/animation are converted KO->Godot by bake_mobs.bake_model, so these are reflected
across X exactly like every other character rig.

Usage:
    python bake.py --only bake_wings
    python bake.py --only bake_wings --ids 810263000 --tex-max 512
    python bake.py --only bake_wings --slots 0            # wings only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bake_mobs                                              # noqa: E402
from tbl_reader import read_tbl                                # noqa: E402
from kotools.resolver import AssetResolver                     # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

PART_TBL = str(Path(bake_mobs.KO_DIR) / "Data" / "ChrTypePartItem.tbl")
DEFAULT_OUT = _assets() / "wings"

COL_ID, COL_CHR, COL_PART_RESET, COL_BONE, COL_SLOT, COL_FLAG = range(6)
KURIAN_BONE = 44
SLOT_NAMES = {0: "wings", 1: "fairy", 2: "talisman", 3: "emblem"}


def _rows():
    out = []
    for row in read_tbl(PART_TBL).rows:
        if not row or len(row) <= COL_SLOT:
            continue
        chr_path = row[COL_CHR]
        if not isinstance(chr_path, str) or not chr_path.lower().endswith(".n3chr"):
            continue
        out.append((int(row[COL_ID]), chr_path, int(row[COL_BONE]), int(row[COL_SLOT])))
    return out


def main():
    ap = argparse.ArgumentParser(description="Bake KO cospre attachments -> animated skinned .glb.")
    ap.add_argument("--ids", nargs="+", type=int, help="only these item ids")
    ap.add_argument("--slots", nargs="+", type=int, choices=[0, 1, 2, 3],
                    help="only these attach slots (0 wings, 1 fairy, 2 talisman, 3 emblem)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--tex-max", type=int, default=bake_mobs.TEX_MAX)
    ap.add_argument("--metadata-only", action="store_true",
                    help="rewrite index.json only; do not rebuild GLBs")
    args = ap.parse_args()

    rows = _rows()
    if args.ids:
        wanted = set(args.ids)
        rows = [r for r in rows if r[0] in wanted]
    if args.slots:
        keep = set(args.slots)
        rows = [r for r in rows if r[3] in keep]
    if not rows:
        print("no rows selected", file=sys.stderr)
        return 2

    resolver = AssetResolver(bake_mobs.KO_DIR)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    index = {"_kurianBone": KURIAN_BONE}
    if args.metadata_only and (out_dir / "index.json").exists():
        index = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))
        index["_kurianBone"] = KURIAN_BONE

    baked, failed, per_slot = {}, [], {}
    for item_id, chr_path, bone, slot in rows:
        stem = bake_mobs.safe_name(Path(chr_path.replace("\\", "/")).stem)
        if args.metadata_only:
            if stem in baked or (out_dir / f"{stem}.glb").exists():
                index[str(item_id)] = {"model": stem, "bone": bone, "slot": slot}
            continue
        if stem not in baked:
            if resolver.read(chr_path) is None:
                failed.append((item_id, chr_path, "no such .n3chr in the client data"))
                baked[stem] = False
            else:
                try:
                    res, warnings = bake_mobs.bake_model(
                        stem, [item_id, chr_path], resolver, out_dir, tex_max=args.tex_max)
                except Exception as e:
                    failed.append((item_id, chr_path, f"{type(e).__name__}: {e}"))
                    baked[stem] = False
                    res = None
                    warnings = []
                if res is None:
                    if baked.get(stem) is not False:
                        failed.append((item_id, chr_path,
                                       f"no usable parts ({'; '.join(warnings[:2])})"))
                    baked[stem] = False
                else:
                    bones, nskins, nclips, size = res
                    baked[stem] = True
                    print(f"[wings] slot {slot} {SLOT_NAMES.get(slot, '?'):9s} {stem:34s} "
                          f"bone={bone:<3d} joints={bones:<4d} parts={nskins} clips={nclips} "
                          f"{size // 1024}KB")
                    for w in warnings:
                        print(f"          ! {w}", file=sys.stderr)
        if baked.get(stem):
            index[str(item_id)] = {"model": stem, "bone": bone, "slot": slot}
            per_slot[slot] = per_slot.get(slot, 0) + 1

    (out_dir / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True),
                                        encoding="utf-8")
    ok_models = sum(1 for v in baked.values() if v)
    print(f"[wings] {ok_models} models, "
          + ", ".join(f"{n} {SLOT_NAMES.get(s, s)}" for s, n in sorted(per_slot.items()))
          + f" -> {out_dir}")
    for item_id, chr_path, why in failed:
        print(f"[wings] SKIP {item_id} {chr_path}: {why}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
