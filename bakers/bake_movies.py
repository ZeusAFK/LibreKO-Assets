"""bake_movies.py -- the Under the Castle movies the server starts between boss phases.

Data\\movie_scene.tbl maps a movie id to a Scene\\*.n3scene + .n3scenex pair and its fade; n3scene_parse
reads the scene, its cameras (eye / look-at / up key tracks) and its characters. The characters of a
cutscene play their own .n3joint keyframes, so each is baked to a glb with one clip over the whole timeline.

Output: <output>/movies/movies.json and <output>/movies/actors/<chr>.glb

Usage:
    python bake.py --only movies
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bake_mobs  # noqa: E402
import n3scene_parse  # noqa: E402
from kotools.resolver import AssetResolver  # noqa: E402
from tbl_reader import read_tbl  # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

DEFAULT_OUT = _assets() / "movies"
ACTORS_DIR = "actors"
TEX_DIR = "tex"
COL_ID, COL_SCENE, COL_NO_SKIP, COL_END_LEAD, COL_LOOP = 0, 1, 7, 8, 9
COL_FADE_HOLD, COL_FADE_IN, COL_FADE_OUT, COL_WHITE = 10, 11, 12, 13
SCENE_FPS = 30.0


def godot_position(v):
    return [-v[0], v[1], v[2]]


def godot_rotation(q):
    return [q[0], -q[1], -q[2], q[3]]


def rate_of(track, fallback):
    return track.get("samplingRate") or fallback


def camera_json(camera):
    world = camera["world"]
    return {
        "fov": camera["fovDegrees"],
        "near": camera["near"],
        "eye": [godot_position(v) for v in world["eye"]],
        "eyeRate": rate_of(camera["eyeTrack"], SCENE_FPS),
        "at": [godot_position(v) for v in world["at"]],
        "atRate": rate_of(camera["atTrack"], SCENE_FPS) if camera.get("atSource") != "staticAt" else SCENE_FPS,
        "up": [godot_position(v) for v in world["up"]],
        "upRate": rate_of(camera["upTrack"], SCENE_FPS),
    }


def actor_json(character, stem):
    placement = character.get("placement") or {}
    effective = character.get("effective") or {}
    return {
        "model": stem,
        "appear": placement.get("appearFrame", -1.0),
        "disappear": placement.get("disappearFrame", -1.0),
        "fadeRate": placement.get("fadeOutRate", 0.0),
        "position": godot_position(effective.get("position", [0.0, 0.0, 0.0])),
        "rotation": godot_rotation(effective.get("rotation", [0.0, 0.0, 0.0, 1.0])),
        "scale": effective.get("scale", [1.0, 1.0, 1.0]),
    }


def fade_json(fade):
    colour = fade.get("color") or {}
    return {
        "start": fade["startFrame"],
        "cover": fade["fadeInFrames"],
        "clear": fade["fadeOutFrames"],
        "hold": fade["holdFrames"],
        "color": "#{:02x}{:02x}{:02x}".format(colour.get("r", 0), colour.get("g", 0), colour.get("b", 0)),
    }


def bake_actor(chr_path, resolver, out_dir):
    from kotools.exchange.gltf_export import export_character_to_gltf

    stem = Path(chr_path.replace("\\", "/")).stem.lower()
    joint, skins, texs, alphas, _, warnings = bake_mobs._assemble([chr_path], resolver)
    if not skins or joint is None:
        return None, warnings
    bake_mobs._convert_joint(joint)
    for skin in skins:
        bake_mobs._convert_skin(skin)
    out_dir.mkdir(parents=True, exist_ok=True)
    export_character_to_gltf(joint, skins, texs, None, str(out_dir / f"{stem}.glb"), name=stem,
                             texture_max_size=bake_mobs.TEX_MAX, alpha_modes=alphas,
                             texture_dir=out_dir / TEX_DIR, texture_uri_prefix=f"{TEX_DIR}/")
    return stem, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ko", type=Path, default=_ko())
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    table = read_tbl(args.ko / "Data" / "movie_scene.tbl")
    resolver = AssetResolver(str(args.ko))
    actors_dir = args.out / ACTORS_DIR
    baked: dict[str, str | None] = {}
    movies = []
    for row in table.rows:
        movie_id = row[COL_ID]
        if movie_id == 0:
            continue
        movie = {
            "id": movie_id,
            "endLead": row[COL_END_LEAD],
            "noSkip": row[COL_NO_SKIP] == 1,
            "loop": row[COL_LOOP] == 1,
            "fade": {"hold": row[COL_FADE_HOLD], "cover": row[COL_FADE_OUT], "clear": row[COL_FADE_IN], "white": row[COL_WHITE] == 1},
        }
        scene_path = row[COL_SCENE]
        if scene_path:
            scene = n3scene_parse.convert_scene(str(args.ko), str(args.ko / scene_path))
            movie["scene"] = scene["scene"]
            movie["frameStart"] = scene["frameStart"]
            movie["frameEnd"] = scene["frameEnd"]
            movie["cameras"] = [camera_json(camera) for camera in scene["cameras"]]
            movie["schedule"] = scene["cameraSchedule"]
            movie["fades"] = [fade_json(fade) for fade in scene["fades"]]
            movie["actors"] = []
            for character in scene["characters"]:
                chr_path = character["file"]
                if chr_path not in baked:
                    stem, warnings = bake_actor(chr_path, resolver, actors_dir)
                    baked[chr_path] = stem
                    for warning in warnings:
                        print(f"[movies] {chr_path}: {warning}")
                if baked[chr_path]:
                    movie["actors"].append(actor_json(character, baked[chr_path]))
            for warning in scene["warnings"]:
                print(f"[movies] movie {movie_id}: {warning}")
        movies.append(movie)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "movies.json").write_text(json.dumps(n3scene_parse.clean(movies), indent=1), encoding="utf-8")
    print(f"[movies] {len(movies)} movies, {sum(1 for v in baked.values() if v)} actor models -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
