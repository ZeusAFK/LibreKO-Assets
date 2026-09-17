"""bake_weapons.py -- bake KO weapon/shield plug meshes into static .glb that World.cs
attaches to a player's hand bone at runtime.

A weapon item -> Item_Org_us.tbl dwIDResrc -> Item\\<...>.n3cplug (no race
offset for weapons). The .n3cplug gives the mesh (.n3pmesh), a texture, and a LOCAL
transform (position + 4x4 rot + scale) relative to a hand joint. We bake the mesh exactly
like a static object (n3_convert decode_part -> build_glb: negate-X, reversed winding,
embedded texture) and record the converted (pos, quat, scale) + the plug's joint_index.

The index is keyed by **base item id** (id//1000*1000) so every enchant level of a weapon
maps to the same baked plug; World.cs looks up `gear[slot] / 1000 * 1000`.

Usage:
    python bake.py --only bake_weapons --all          # every weapon/shield in Item_Org_us.tbl
    python bake.py --only bake_weapons 981110570       # specific item id(s)
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import n3_convert                                       # noqa: E402
import bake_players                                     # noqa: E402  (item_resrc, KO_DIR)
from tbl_reader import read_tbl                          # noqa: E402
from kotools.resolver import AssetResolver              # noqa: E402
from kotools.io.binary_reader import BinaryReader       # noqa: E402
from kotools.character.cplug import N3CPlug, PLUGTYPE_NORMAL   # noqa: E402
from kotools.ntf.texture import read_dxt_from_bytes      # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

KO_DIR = bake_players.KO_DIR
ITEM_TBL = bake_players.ITEM_TBL
DEFAULT_OUT = _assets() / "items" / "weapon"
_P = np.diag([-1.0, 1.0, 1.0])


def _convert_transform(position, rot_matrix):
    """KO plug local transform -> (godot_pos, godot_quat). KO/N3 matrices are D3D
    row-major, so the column-vector rotation is the transpose; the negate-X reflection
    is applied by conjugation P*Rt*P."""
    pos = [-position[0], position[1], position[2]]
    m = np.array([row[:3] for row in rot_matrix[:3]], dtype=float)
    rg = _P @ m.T @ _P
    t = np.trace(rg)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        w = 0.25 * s
        x = (rg[2][1] - rg[1][2]) / s; y = (rg[0][2] - rg[2][0]) / s; z = (rg[1][0] - rg[0][1]) / s
    else:
        i = int(np.argmax([rg[0][0], rg[1][1], rg[2][2]]))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = np.sqrt(max(1e-9, 1.0 + rg[i][i] - rg[j][j] - rg[k][k])) * 2
        q = [0.0, 0.0, 0.0]; q[i] = 0.25 * s
        q[j] = (rg[j][i] + rg[i][j]) / s; q[k] = (rg[k][i] + rg[i][k]) / s
        w = (rg[k][j] - rg[j][k]) / s; x, y, z = q
    n = np.sqrt(x * x + y * y + z * z + w * w) or 1.0
    return pos, [x / n, y / n, z / n, w / n]


def _decode_texture(resolver, tex_path):
    """item .dxt -> (tid, (png_bytes, has_alpha)) via read_dxt_from_bytes (the proven item
    texture decoder — kotools has no decode_ntf). Returns (None, None) on failure."""
    if not tex_path:
        return None, None
    blob = resolver.read(tex_path)
    if not blob:
        return None, None
    try:
        tex = read_dxt_from_bytes(blob)
        img = tex.to_pil_image()
        buf = io.BytesIO(); img.save(buf, "PNG")
        has_alpha = img.mode == "RGBA" and img.getextrema()[3][0] < 250
        return n3_convert.tex_id(tex_path), (buf.getvalue(), bool(has_alpha))
    except Exception:
        return None, None


def _material_source(plug, tid, stem):
    """Translate the plug's authored N3 material into the common static-GLB
    material descriptor used by n3_convert.

    Weapon baking predates the material-preserving object converter and used to
    pass only a texture id.  Besides breaking once build_glb's API was tightened,
    that discarded the current client's render flags and material colours.  Keep
    this conversion at the plug boundary so weapons and objects now follow the
    same N3 -> Godot material rules.
    """
    m = plug.material
    return {
        "tid": tid or "",
        "name": (
            f"{stem}__ko_f{m.render_flags:x}"
            f"_s{m.src_blend}_d{m.dest_blend}"
        ),
        "render_flags": int(m.render_flags),
        "src_blend": int(m.src_blend),
        "dest_blend": int(m.dest_blend),
        "diffuse": [m.diffuse.r, m.diffuse.g, m.diffuse.b, m.diffuse.a],
        "emissive": [m.emissive.r, m.emissive.g, m.emissive.b],
        "pivot": [0.0, 0.0, 0.0],
    }


def _bake_one(item_id, resolver, out_dir, stem_cache, archive_keys):
    """Bake one weapon item -> index record (dedup mesh by stem). None if not a weapon."""
    plug_path = bake_players.item_model_path(item_id, race=0, is_weapon=True)
    if not plug_path or plug_path.split("\\")[-1].lower() not in archive_keys:
        return None
    plug_blob = resolver.read(plug_path)
    if not plug_blob:
        return None
    plug = N3CPlug.from_reader(BinaryReader(plug_blob))
    stem = n3_convert.safe_name(n3_convert.base(plug.mesh_path))
    if stem not in stem_cache:                       # bake the mesh once per distinct plug
        mesh_blob = resolver.read(plug.mesh_path)
        if not mesh_blob:
            return None
        pos, nrm, uv, idx = n3_convert.decode_part(mesh_blob)
        tid, tex = _decode_texture(resolver, plug.texture_path)
        textures = {tid: tex} if tid else {}
        out_dir.mkdir(parents=True, exist_ok=True)
        source = _material_source(plug, tid, stem)
        n3_convert.build_glb(
            str(out_dir / f"{stem}.glb"),
            [(source, pos, nrm, uv, idx)],
            textures,
            texture_dir=out_dir / "tex",
            texture_uri_prefix="tex/",
        )
        stem_cache[stem] = (tid is not None, int(pos.shape[0]))
    gpos, gquat = _convert_transform(plug.position, plug.rot_matrix)
    textured, verts = stem_cache[stem]
    return {
        "stem": stem,
        "joint": plug.joint_index,
        "pos": gpos,
        "quat": gquat,
        "scale": list(plug.scale),
        "verts": verts,
        "tex": textured,
        # Provenance makes archive-generation mistakes diagnosable without
        # reverse-engineering a baked filename after the fact.
        "resrc": bake_players.item_visual_resrc(item_id),
        "plug": plug_path.replace("\\", "/"),
        "mesh": plug.mesh_path.replace("\\", "/"),
        "texture": plug.texture_path.replace("\\", "/"),
        **_trace_fields(plug),
    }


def _trace_fields(plug) -> dict:
    """The swing-trail ribbon: N steps of history between two points up the blade."""
    if plug.plug_type != PLUGTYPE_NORMAL or plug.trace_step <= 0 or plug.trace1 <= plug.trace0:
        return {}
    return {
        "trstep": int(plug.trace_step),
        "trcol": int(plug.trace_color) & 0xFFFFFFFF,
        "tr0": round(float(plug.trace0), 4),
        "tr1": round(float(plug.trace1), 4),
    }


def _amend_traces(index, resolver, archive_keys) -> int:
    """Add only the trace fields to an existing index, leaving every other field
    (including bake_weapon_anchor's fxp/fxr/fxg) untouched."""
    changed = 0
    for key, rec in index.items():
        path = rec.get("plug")
        if not path:
            continue
        path = path.replace("/", "\\")
        if path.split("\\")[-1].lower() not in archive_keys:
            continue
        blob = resolver.read(path)
        if not blob:
            continue
        try:
            plug = N3CPlug.from_reader(BinaryReader(blob))
        except Exception:
            continue
        for k in ("trstep", "trcol", "tr0", "tr1"):
            rec.pop(k, None)
        fields = _trace_fields(plug)
        rec.update(fields)
        if fields:
            changed += 1
    return changed


def main():
    ap = argparse.ArgumentParser(description="Bake KO weapon/shield plug meshes -> static .glb.")
    ap.add_argument("items", nargs="*", type=int, help="weapon/shield item ids (omit with --all)")
    ap.add_argument("--all", action="store_true", help="bake every weapon/shield in Item_Org_us.tbl")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--trace-only", action="store_true",
                    help="amend the existing index with the swing-trail fields; rebuild nothing")
    args = ap.parse_args()
    out_dir = Path(args.out)
    resolver = AssetResolver(KO_DIR)
    resolver._ensure_archives_loaded()
    archive_keys = set(resolver._archive_index.keys())   # bare basenames, lowercased

    if args.trace_only:
        index_path = out_dir / "index.json"
        index = json.loads(index_path.read_text())
        n = _amend_traces(index, resolver, archive_keys)
        index_path.write_text(json.dumps(index, indent=0))
        print(f"[weapons] swing trail: {n}/{len(index)} items carry a trace -> {index_path}")
        return 0

    if args.all:
        # Include extension IDs whose Item_Ext row overrides dwIDResrc. Retail's
        # MakeResrcFileNameForUPC gives that resource precedence over Item_Org.
        item_ids = sorted(set(bake_players.iter_visual_item_ids()))
    else:
        # Keep the exact item ID: its final three digits select Item_Ext.
        item_ids = args.items
    if not item_ids:
        print("give item ids or --all", file=sys.stderr)
        return 2

    index_path = out_dir / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    stem_cache, made, weapons = {}, 0, 0
    for item_id in item_ids:
        rec = _bake_one(item_id, resolver, out_dir, stem_cache, archive_keys)
        if rec is None:
            continue
        weapons += 1
        key = str(item_id)
        if key not in index:
            made += 1
        index[key] = rec
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=0))
    notex = sum(1 for v in index.values() if not v.get("tex"))
    print(f"[weapons] {weapons} weapon items ({len(stem_cache)} distinct meshes, {notex} untextured) "
          f"-> {out_dir}  (index: {len(index)} ids, +{made} new)")
    return 0 if index else 1


if __name__ == "__main__":
    raise SystemExit(main())
