"""bake_hand_fx.py -- decode KO COSPRE GLOVE hand effects into the client's FX plug index.

What the original client does when it builds the resource name and equips a cospre glove:

  * `Item_Org` col12 is the equip slot. Slots >= 100 are cospre; the path builder reduces it with
    `slot % 100` and treats **0..4 as a HAND PLUG** -- the same group as the weapon slots 1..4 --
    so cospre glove items (slot 100) mount to a hand exactly like a weapon does.

  * `Item_Ext` col7 == **8** is the switch that turns that plug into a **`.n3fxplug`** instead of a
    `.n3cplug` mesh: an FX-only attachment. That is the whole selection rule -- there is no
    `fx.tbl` id involved (`Item_Ext` col4 is 0 on every gauntlet row) and no `ChrTypePartItem`
    row, which is why these never showed up with the wing/fairy work.

  * The file name is built from the resource id (`Item_Ext` col5 if non-zero, else `Item_Org` col6)
    plus a trailing HAND code, `1` = right / `2` = left, from the caller: `SetCospreGlove(0, ..)`
    passes 2 and `(1, ..)` passes 3, which the builder maps to 1 and 2. Ids below 1e9 use the
    1-digit leading field, 1e9..4e9 the 2-digit one:

        Chr\\%.1d_%.4d_%.2d_%.1d_%.1d.n3fxplug          <- (resrc/1e7, resrc/1e3%1e4, resrc/10%100,
        Chr\\%.2d_%.4d_%.2d_%.1d_%.1d.n3fxplug             resrc%10, hand)

    KURIAN (races 6/14) appends a sixth `_1` field -- but only while the player is NOT transformed
    (a flag on the player), the same override the Kurian wing socket uses.

  * The plug holds its own bone index per part. Shipped data: right hand = bone 12, left hand =
    bone 7, Kurian right hand = bone 23 (Kurian left reuses 7). Offsets are all zero, dir is
    (0,0,1) and scale 1.0 in every shipped part, so only `fx` + `bone` actually carry information.

  * Suppressed by zone: `SetCospreGlove` bails on a zone check (zones 57-60) and on the shared
    attachment gate (zones 29, 45, 85, 89).

The only items in the retail data that satisfy the rule are the 16 Pathos' Glove ("Aurora") ids --
8 resource variants (4 attack colours red/green/violet/blue, 4 defense) x base + Limited Edition.

Output (<output>/handfx/):
    index.json      {"<itemId>": {"r"|"l"|"rk"|"lk": [{"fx": <stem>, "bone": <n>}, ...]}}
                    ("p" and "scale" are written only when they differ from zero / 1.0)

The referenced effects are already baked by `bake_fx.py`; this baker only writes the index, so it
is cheap to re-run. Parts whose effect has no baked descriptor are reported and dropped.

Usage:
    python bake.py --only bake_hand_fx
    python bake.py --only bake_hand_fx --ids 502573462
    python bake.py --only bake_hand_fx --keep-unbaked
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bake_players                                            # noqa: E402
from kotools.io.binary_reader import BinaryReader              # noqa: E402
from kotools.fx.fxplug import N3FXPlug                        # noqa: E402
from kotools.resolver import AssetResolver                     # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

DEFAULT_OUT = _assets() / "handfx"
FX_DIR = _assets() / "fx"

ORG_CAT, ORG_RESRC, ORG_SLOT = 1, 6, 12
EXT_RESRC, EXT_PLUG_KIND = 5, 7

COSPRE_SLOT_BASE = 100
PLUG_SLOT_MAX = 4
FXPLUG_KIND = 8

HAND_RIGHT, HAND_LEFT = 1, 2
VARIANTS = {"r": (HAND_RIGHT, False), "l": (HAND_LEFT, False),
            "rk": (HAND_RIGHT, True), "lk": (HAND_LEFT, True)}


def plug_path(item_id: int, resrc: int, hand: int, kurian: bool) -> str:
    lead = item_id // 1_000_000_000
    head = f"{resrc // 10_000_000:02d}" if 1 <= lead <= 3 else f"{resrc // 10_000_000:d}"
    name = (f"{head}_{resrc // 1000 % 10000:04d}_{resrc // 10 % 100:02d}"
            f"_{resrc % 10:d}_{hand:d}" + ("_1" if kurian else ""))
    return f"Chr\\{name}.n3fxplug"


def fx_plug_items():
    orgs = bake_players._item_rows()
    for cat in sorted({int(o[ORG_CAT]) for o in orgs.values()}):
        for (base_id, ext_id), ext in bake_players._extension_rows(cat).items():
            org = orgs.get(base_id)
            if org is None or int(ext[EXT_PLUG_KIND]) != FXPLUG_KIND:
                continue
            if int(org[ORG_SLOT]) % COSPRE_SLOT_BASE > PLUG_SLOT_MAX:
                continue
            resrc = int(ext[EXT_RESRC]) or int(org[ORG_RESRC])
            if resrc:
                yield base_id + ext_id, resrc, str(org[2])


def read_plug(resolver, path):
    blob = resolver.read(path)
    return N3FXPlug.from_reader(BinaryReader(blob)) if blob else None


def main():
    ap = argparse.ArgumentParser(description="Bake KO cospre glove hand effects -> index.json.")
    ap.add_argument("--ids", nargs="+", type=int, help="only these item ids")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--keep-unbaked", action="store_true",
                    help="keep parts whose effect has no baked descriptor")
    args = ap.parse_args()

    resolver = AssetResolver(bake_players.KO_DIR)
    baked_fx = {p.stem.lower() for p in FX_DIR.glob("*.json")} if FX_DIR.is_dir() else set()
    wanted = set(args.ids) if args.ids else None

    index, missing_fx, no_files = {}, set(), []
    for item_id, resrc, name in fx_plug_items():
        if wanted is not None and item_id not in wanted:
            continue
        entry = {}
        for key, (hand, kurian) in VARIANTS.items():
            plug = read_plug(resolver, plug_path(item_id, resrc, hand, kurian))
            if plug is None:
                continue
            parts = []
            for part in plug.parts:
                raw = part.bundle_path.replace("\\", "/").rsplit("/", 1)[-1]
                fx = raw[:-4] if raw.lower().endswith(".fxb") else raw
                if not fx:
                    continue
                if fx.lower() not in baked_fx:
                    missing_fx.add(fx)
                    if not args.keep_unbaked:
                        continue
                scale = float(part.scale)
                out = {"fx": fx, "bone": int(part.ref_index)}
                pos = [-float(part.offset_pos[0]) + 0.0, float(part.offset_pos[1]) + 0.0,
                       float(part.offset_pos[2]) + 0.0]
                if any(pos):
                    out["p"] = pos
                if scale == scale and scale > 0 and round(scale, 5) != 1.0:
                    out["scale"] = round(scale, 5)
                parts.append(out)
            if parts:
                entry[key] = parts
        if entry:
            index[str(item_id)] = entry
            bones = sorted({p["bone"] for v in entry.values() for p in v})
            print(f"[handfx] {item_id:>11} resrc {resrc} {name[:44]:44s} "
                  f"{'/'.join(sorted(entry))} bones={bones} "
                  f"parts={sum(len(v) for v in entry.values())}")
        else:
            no_files.append((item_id, resrc, name))

    if not index:
        print("[handfx] nothing to write", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.json").write_text(
        json.dumps(index, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    print(f"[handfx] {len(index)} items -> {out_dir / 'index.json'}")
    for fx in sorted(missing_fx):
        print(f"[handfx] no baked effect '{fx}' -- run bake_fx.py", file=sys.stderr)
    for item_id, resrc, name in no_files:
        print(f"[handfx] SKIP {item_id} resrc {resrc} {name}: no .n3fxplug in the client data",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
