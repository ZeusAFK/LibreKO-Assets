"""bake_boss_phases.py -- the Under the Castle boss phases (Data\\bossphase_us.tbl) and boss shouts
(Data\\bossshout_us.tbl).

A phase keeps its NPC look but plays its own .n3anim, which slices the look's one .n3joint timeline at
other frame ranges. The look's glb is baked again with every phase range as an extra clip, and each phase
gets <stem>.<anim>.anim.json naming the clip each motion plays. Runs after the mobs step: it reads
npcs/index.json and rewrites the boss looks' glbs.

Output: <output>/npcs/boss_phases.json, <output>/npcs/<stem>.glb, <stem>.<anim>.anim.json

Usage:
    python bake.py --only boss-phases
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bake_mobs  # noqa: E402
from bake_mobs import AssetResolver, BinaryReader, N3AnimControl  # noqa: E402
from tbl_reader import read_tbl  # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

DEFAULT_OUT = _assets() / "npcs"
PHASES_JSON = "boss_phases.json"
TEX_DIR = "tex"
COL_NAME, COL_PROTO, COL_LOOK, COL_PHASE, COL_MAX, COL_KIND, COL_ANIM = 1, 2, 3, 4, 5, 6, 8
COL_RIGHT, COL_LEFT, COL_SCALE, COL_SHOUT, COL_GROUND, COL_RISE, COL_WEATHER = 19, 20, 21, 22, 23, 24, 25
BURIED_KIND = 2
SHOUT_ID, SHOUT_INTERVAL, SHOUT_CHAT, SHOUT_BANNER, SHOUT_TEXT = 0, 5, 6, 7, 8
KO_FPS = 30.0


def stem_of(path):
    return Path(path.replace("\\", "/")).stem.lower()


def number(value):
    return int(value) if float(value).is_integer() else round(float(value), 3)


def range_key(entry):
    return number(entry.frame_start), number(entry.frame_end), number(entry.fps if entry.fps > 0 else KO_FPS)


def exported(entry):
    return entry.name and entry.name != "No Name" and entry.frame_end >= entry.frame_start


def default_clips(anim):
    used: dict[str, int] = {}
    clips = {}
    for entry in anim.animations:
        if not exported(entry):
            continue
        name = entry.name
        if name in used:
            used[name] += 1
            name = f"{name}_{used[name]}"
        else:
            used[name] = 0
        clips.setdefault(range_key(entry), name)
    return clips


def clip_for(entry, defaults):
    return defaults.get(range_key(entry)) or "phase_" + "_".join(str(v).replace(".", "p") for v in range_key(entry))


def read_anim(resolver, path):
    blob = resolver.read(path)
    return N3AnimControl.from_reader(BinaryReader(blob)) if blob else None


def bake_look(stem, row, resolver, out_dir, phase_anims):
    from kotools.exchange.gltf_export import export_character_to_gltf

    joint, skins, texs, alphas, anim, warnings = bake_mobs._assemble(row, resolver)
    if not skins or joint is None or anim is None:
        return None, warnings + ["nothing to bake"]
    defaults = default_clips(anim)
    extra = {}
    for phase_anim in phase_anims:
        for entry in phase_anim.animations:
            name = clip_for(entry, defaults)
            if exported(entry) and range_key(entry) not in defaults and name not in extra:
                clip = copy.copy(entry)
                clip.name = name
                extra[name] = clip
    if not extra:
        return defaults, warnings
    bake_mobs._convert_joint(joint)
    for skin in skins:
        bake_mobs._convert_skin(skin)
    clips = N3AnimControl(animations=list(anim.animations) + list(extra.values()))
    export_character_to_gltf(joint, skins, texs, clips, str(out_dir / f"{stem}.glb"), name=stem,
                             texture_max_size=bake_mobs.TEX_MAX, alpha_modes=alphas,
                             texture_dir=out_dir / TEX_DIR, texture_uri_prefix=f"{TEX_DIR}/")
    bake_mobs._write_anim_metadata(out_dir / f"{stem}.anim.json", anim)
    return defaults, warnings


def write_phase_metadata(path, phase_anim, defaults):
    bake_mobs._write_anim_metadata(path, phase_anim)
    data = json.loads(path.read_text())
    for meta, entry in zip(data["animations"], phase_anim.animations):
        if meta["exported"]:
            meta["clip"] = clip_for(entry, defaults)
    path.write_text(json.dumps(data, separators=(",", ":")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ko", type=Path, default=_ko())
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    data = args.ko / "Data"
    resolver = AssetResolver(str(args.ko))
    looks = bake_mobs._looks_index()
    index = json.loads((args.out / "index.json").read_text())

    phases = []
    anims_by_look: dict[int, dict[str, N3AnimControl]] = {}
    for row in read_tbl(data / "bossphase_us.tbl").rows:
        look = row[COL_LOOK]
        anim_stem = stem_of(row[COL_ANIM]) if row[COL_ANIM] else None
        if anim_stem:
            phase_anim = read_anim(resolver, row[COL_ANIM])
            if phase_anim is None:
                print(f"[boss] {row[COL_PROTO]}: {row[COL_ANIM]} missing")
                anim_stem = None
            else:
                anims_by_look.setdefault(look, {})[anim_stem] = phase_anim
        phases.append({
            "proto": row[COL_PROTO],
            "name": row[COL_NAME],
            "look": look,
            "phase": row[COL_PHASE],
            "maxPhase": row[COL_MAX],
            "buried": row[COL_KIND] == BURIED_KIND,
            "groundOffset": number(row[COL_GROUND]),
            "riseSpeed": number(row[COL_RISE]),
            "anim": anim_stem,
            "right": row[COL_RIGHT],
            "left": row[COL_LEFT],
            "scale": number(row[COL_SCALE]),
            "shout": row[COL_SHOUT],
            "weather": row[COL_WEATHER],
        })

    for look, phase_anims in sorted(anims_by_look.items()):
        stem = index.get(str(look))
        if stem is None or look not in looks:
            print(f"[boss] look {look} is not baked")
            continue
        defaults, warnings = bake_look(stem, looks[look], resolver, args.out, phase_anims.values())
        for warning in warnings:
            print(f"[boss] {stem}: {warning}")
        if defaults is None:
            continue
        for anim_stem, phase_anim in phase_anims.items():
            write_phase_metadata(args.out / f"{stem}.{anim_stem}.anim.json", phase_anim, defaults)
        print(f"[boss] {stem}: {len(phase_anims)} phase animation sets")

    shouts = [{
        "id": row[SHOUT_ID],
        "text": row[SHOUT_TEXT],
        "interval": row[SHOUT_INTERVAL],
        "chat": row[SHOUT_CHAT] != 0,
        "banner": row[SHOUT_BANNER] != 0,
    } for row in read_tbl(data / "bossshout_us.tbl").rows]
    (args.out / PHASES_JSON).write_text(json.dumps({"phases": phases, "shouts": shouts}, indent=1), encoding="utf-8")
    print(f"[boss] {len(phases)} phases, {len(shouts)} shouts -> {args.out / PHASES_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
