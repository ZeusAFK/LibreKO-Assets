"""bake_mobs.py -- decode KO character/monster models into ANIMATED skinned .glb
the Godot client renders for NPCs/mobs.

Resolves skeleton + skinned meshes + animation clips + textures via the field-tested
vendored kotools (export_character_to_gltf), one .glb per model keyed by a readable name
(joint/.n3chr stem), plus index.json mapping the server model id -> name. Each .glb
carries a Skeleton3D + skinned mesh + named clips: basic(=idle), walk, run, walk_back,
attack0/1, struck, guard, dead0/1, talk* (NPCs).

The full rig (joints + bind + skin verts + animation keys) is converted KO->Godot by
negating X (see _convert_joint/_convert_skin) so it's self-consistent — NO runtime node
mirror (negative scale distorts Godot skeletons). Skin verts use the true bind pose
(SkinVertex.origin). Textures embedded, capped to 256px.

Resolution: server modelId -> NPC_Looks.tbl row -> .n3joint + .n3anim + parts
(an item\\*.n3cpart list, or a chr\\*.n3chr manifest).

Usage:
    python bake.py --only bake_mobs --models 500 11000 31100 --out <output>/npcs
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from tbl_reader import read_tbl                              # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402
# Keep metadata-only baking usable in lightweight Python environments where the
# static-object converter's optional numpy/pygltflib dependencies are absent.
def safe_name(name):
    return "".join(c if (c.isalnum() or c in "_-.") else "_" for c in name.lower())

from kotools.resolver import AssetResolver                   # noqa: E402
from kotools.io.binary_reader import BinaryReader            # noqa: E402
from kotools.animation.animcontrol import N3AnimControl      # noqa: E402
from kotools.character.chr import N3Chr                      # noqa: E402
from kotools.fx.fxplug import N3FXPlug                      # noqa: E402

KO_DIR = str(_ko()
             if _ko().exists()
             else _ko())
NPC_LOOKS = str(Path(KO_DIR) / "Data" / "NPC_Looks.tbl")
DEFAULT_OUT = _assets() / "npcs"
TEX_MAX = 1024   # DEFAULT cap on texture dimension; override with --tex-max (KO/enhanced art is up to 2048)


def _looks_index():
    return {row[0]: row for row in read_tbl(NPC_LOOKS).rows if row}


def _model_name(row):
    """Human-readable model name from the .n3joint (or .n3chr) path stem in the
    NPC_Looks row — e.g. 'chr\\mob_wolf.n3joint' -> 'mob_wolf'. None if absent."""
    strs = [c for c in row if isinstance(c, str) and c]
    src = (next((c for c in strs if c.lower().endswith(".n3joint")), None)
           or next((c for c in strs if c.lower().endswith(".n3chr")), None))
    return os.path.splitext(os.path.basename(src.replace("\\", "/")))[0] if src else None


RF_ALPHABLENDING = 0x001


def _blended_alpha_modes(cparts):
    """Per-part "never alpha-TEST this" hints from KO's own render flags.

    Only RF_ALPHABLENDING means anything here — every KO part carries SRCALPHA/INVSRCALPHA as
    its default blend state, so the factors alone say nothing. A blended part alpha-tested
    instead loses the gradient it is made of (obj_el_crystal02 shredded into slivers), and the
    verdict travels with the TEXTURE: obj_el_crystal02's two shells share one .dxt and only the
    outer one sets the flag, so a cut-out judgement on that image is wrong for both."""
    blended_tex = {
        (getattr(c, "texture_path", "") or "").lower()
        for c in cparts
        if c is not None and getattr(getattr(c, "material", None), "render_flags", 0) & RF_ALPHABLENDING
    }
    blended_tex.discard("")
    return ["BLEND" if c is not None
            and (getattr(c, "texture_path", "") or "").lower() in blended_tex
            else None
            for c in cparts]


def _assemble(row, resolver):
    """Resolve a NPC_Looks row to (joint, skins, textures, alpha_modes, anim_ctrl, warnings),
    with skins[i] aligned to textures[i]. Handles both the .n3chr manifest and the
    bare joint + .n3cpart-list forms."""
    # Heavy mesh/texture modules are optional for metadata-only operation.
    from kotools.character.loader import load_character
    from kotools.character.cpart import N3CPart
    from kotools.character.cskins import N3CPartSkins
    from kotools.skeleton.joint import N3Joint
    from kotools.ntf.texture import read_dxt_from_bytes

    strs = [c for c in row if isinstance(c, str) and c]
    chr_path = next((c for c in strs if c.lower().endswith(".n3chr")), None)
    if chr_path:
        lc = load_character(chr_path, resolver)
        skins, texs, cparts = [], [], []
        for p in lc.parts:               # keep skin/texture pairing aligned
            if p.skin and p.skin.mesh.vertex_count > 0:
                skins.append(p.skin)
                texs.append(p.texture)
                cparts.append(p.part)
        return lc.joint, skins, texs, _blended_alpha_modes(cparts), lc.anim_ctrl, list(lc.warnings)

    warnings = []
    jp = next((c for c in strs if c.lower().endswith(".n3joint")), None)
    ap = next((c for c in strs if c.lower().endswith(".n3anim")), None)
    joint = None
    if jp and resolver.read(jp):
        try:
            joint = N3Joint.from_reader(BinaryReader(resolver.read(jp)))
        except Exception as e:
            warnings.append(f"joint {jp}: {e}")
    anim = None
    if ap and resolver.read(ap):
        try:
            anim = N3AnimControl.from_reader(BinaryReader(resolver.read(ap)))
        except Exception as e:
            warnings.append(f"anim {ap}: {e}")

    skins, texs, cparts = [], [], []
    for pp in [c for c in strs if c.lower().endswith(".n3cpart")]:
        d = resolver.read(pp)
        if not d:
            warnings.append(f"part missing {pp}")
            continue
        try:
            cpart = N3CPart.from_reader(BinaryReader(d))
            skin = N3CPartSkins.from_reader(BinaryReader(resolver.read(cpart.skins_path))).highest_lod
        except Exception as e:
            warnings.append(f"part {pp}: {e}")
            continue
        tex = None
        if cpart.texture_path and resolver.read(cpart.texture_path):
            try:
                tex = read_dxt_from_bytes(resolver.read(cpart.texture_path))
            except Exception:
                tex = None
        if skin and skin.mesh.vertex_count > 0:
            skins.append(skin)
            texs.append(tex)
            cparts.append(cpart)
    return joint, skins, texs, _blended_alpha_modes(cparts), anim, warnings


def _animation_only(row, resolver):
    """Read only a look's animation control, without decoding meshes/textures."""
    strs = [c for c in row if isinstance(c, str) and c]
    chr_path = next((c for c in strs if c.lower().endswith(".n3chr")), None)
    if chr_path:
        blob = resolver.read(chr_path)
        if not blob:
            return None
        manifest = N3Chr.from_reader(BinaryReader(blob))
        anim_blob = resolver.read(manifest.anim_ctrl_path) if manifest.anim_ctrl_path else None
        return N3AnimControl.from_reader(BinaryReader(anim_blob)) if anim_blob else None
    anim_path = next((c for c in strs if c.lower().endswith(".n3anim")), None)
    blob = resolver.read(anim_path) if anim_path else None
    return N3AnimControl.from_reader(BinaryReader(blob)) if blob else None


def _fx_plug_only(row, resolver):
    """Resolve the look's character FX plug.

    NPC_Looks may store the .n3fxplug directly (the common object/mob form) or
    indirectly through an .n3chr manifest.  These effects were previously discarded,
    which removed the anvil/seller/object glows and 170+ other NPC effect families.
    """
    strs = [c for c in row if isinstance(c, str) and c]
    path = next((c for c in strs if c.lower().endswith(".n3fxplug")), None)
    if path is None:
        chr_path = next((c for c in strs if c.lower().endswith(".n3chr")), None)
        blob = resolver.read(chr_path) if chr_path else None
        if blob:
            manifest = N3Chr.from_reader(BinaryReader(blob))
            path = manifest.fx_plug_path or None
    blob = resolver.read(path) if path else None
    if not blob:
        return None
    return N3FXPlug.from_reader(BinaryReader(blob))


# ---------------------------------------------------------------------------
# KO (left-handed) -> glTF/Godot (right-handed) by negating X, applied to the
# WHOLE rig so the skinned animation stays self-consistent (no node-level mirror,
# which distorts Godot skeletons). Reflection P=diag(-1,1,1): positions x->-x;
# rotation quats (x,y,z,w)->(x,-y,-z,w) (verified via P·R·Pᵀ); winding reversed.
# Skin vertex positions use the true BIND POSE (SkinVertex.origin), fixing kotools'
# add_skinned_mesh skinning to the base/modelling-pose verts.
# ---------------------------------------------------------------------------
def _negx3(t):
    return (-t[0], t[1], t[2])


def _negxq(q):
    return (q[0], -q[1], -q[2], q[3])


def _safeq(q, fallback=(0.0, 0.0, 0.0, 1.0)):
    """Finite, unit-norm quaternion or the fallback. KO .n3anim clips carry degenerate
    rotation keys (all-zero / non-finite) for un-animated joints; written verbatim they
    reach Godot as NaN ("quaternion must be normalized") and corrupt the skeleton. We
    fall back to the joint's bind rotation so the bone simply holds its rest pose."""
    x, y, z, w = q
    if not all(math.isfinite(v) for v in (x, y, z, w)):
        return fallback
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-6:
        return fallback
    return (x / n, y / n, z / n, w / n)


def _convert_joint(j):
    # Bind pose = FRAME 0 of the animation keys, not the static position/rotation/scale fields.
    # Those static fields are stale "modelling-pose" values on many skeletons (ElMorad males,
    # mobs: 16-19 of ~24 joints disagree with key[0]; ElMorad female happens to match — which
    # is why she alone looked right). The mesh is skinned against the frame-0 pose, so the bone
    # rest / inverse-bind MUST come from key[0]. (Matches kohd_blender armature.py + N3Joint.)
    kp = getattr(j, "key_pos", None)
    kr = getattr(j, "key_rot", None)
    ks = getattr(j, "key_scale", None)
    if kp is not None and kp.data:
        j.position = kp.data[0]
    if kr is not None and kr.data:
        j.rotation = kr.data[0]
    if ks is not None and ks.data:
        j.scale = ks.data[0]

    j.position = _negx3(j.position)
    j.rotation = _safeq(_negxq(j.rotation))           # sanitized bind rotation (now = frame 0)
    for key, is_quat, fb in ((getattr(j, "key_pos", None), False, None),
                             (getattr(j, "key_rot", None), True, j.rotation),
                             (getattr(j, "key_orient", None), True, None)):
        if key is not None and key.data:
            if is_quat:
                key.data = [_safeq(_negxq(d), fb) if fb else _safeq(_negxq(d)) for d in key.data]
            else:
                key.data = [_negx3(d) for d in key.data]
    # key_scale: magnitudes, unaffected by the reflection.
    for c in j.children:
        _convert_joint(c)


def _convert_skin(skin):
    m = skin.mesh
    sv = skin.skin_vertices
    for vi, vert in enumerate(m.vertices):
        ox, oy, oz = sv[vi].origin if vi < len(sv) else (vert.x, vert.y, vert.z)
        vert.x, vert.y, vert.z = -ox, oy, oz     # bind-pose origin, negate X
        vert.nx = -vert.nx                        # normal negate X
    for arr in (m.vertex_indices, m.uv_indices):  # reverse winding per triangle
        for t in range(0, len(arr) - 2, 3):
            arr[t + 1], arr[t + 2] = arr[t + 2], arr[t + 1]


def _strip_joint_anim(j):
    """Drop a joint tree's built-in keyframes so export emits NO animation (the rest pose
    is kept for skin binding). Grafted equipment parts ride the body rig's skeleton, so a
    per-part copy of the ~1.9MB joint animation is pure bloat."""
    for key in (getattr(j, "key_pos", None), getattr(j, "key_rot", None), getattr(j, "key_orient", None)):
        if key is not None:
            key.data = []      # count is a read-only property derived from data -> 0
    for c in j.children:
        _strip_joint_anim(c)


def bake_model(out_stem, row, resolver, out_dir, strip_anim=False, tex_max=TEX_MAX,
               tex_dir="tex"):
    from kotools.exchange.gltf_export import export_character_to_gltf
    joint, skins, texs, alphas, anim, warnings = _assemble(row, resolver)
    if not skins:
        return None, warnings
    if joint is not None:
        _convert_joint(joint)
        if strip_anim:
            _strip_joint_anim(joint)
            anim = None
    for s in skins:
        _convert_skin(s)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{out_stem}.glb"
    if anim is not None:
        _write_anim_metadata(out_dir / f"{out_stem}.anim.json", anim)
    try:
        fx_plug = _fx_plug_only(row, resolver)
        if fx_plug is not None:
            _write_fx_plug_metadata(out_dir / f"{out_stem}.fxplug.json", fx_plug)
    except Exception as e:
        warnings.append(f"fx plug: {e}")
    export_character_to_gltf(joint, skins, texs, anim, str(out_path),
                             name=out_stem, texture_max_size=tex_max, alpha_modes=alphas,
                             texture_dir=(out_dir / tex_dir) if tex_dir else None,
                             texture_uri_prefix=f"{tex_dir}/" if tex_dir else "")
    nbones = joint.bone_count() if joint else 0
    nclips = len(anim.animations) if anim else 0
    return (nbones, len(skins), nclips, out_path.stat().st_size), warnings


def _write_fx_plug_metadata(path: Path, plug: N3FXPlug) -> None:
    parts = []
    for part in plug.parts:
        raw = part.bundle_path.replace("\\", "/").rsplit("/", 1)[-1]
        fx = raw[:-4] if raw.lower().endswith(".fxb") else raw
        if not fx:
            continue
        parts.append({
            "fx": fx,
            "bone": int(part.ref_index),
            # The complete rig is reflected KO -> Godot across X, as are plug offsets.
            "p": [-float(part.offset_pos[0]), float(part.offset_pos[1]), float(part.offset_pos[2])],
            "dir": [-float(part.offset_dir[0]), float(part.offset_dir[1]), float(part.offset_dir[2])],
            # Bundle-wide SIZE multiplier: retail scales mesh scale
            # and billboard quad corners by it — NOT part positions. Down to
            # 0.07 in the shipped data, so dropping it drew those attachments up to 14x oversized.
            "scale": round(float(part.scale), 5) if part.scale == part.scale else 1.0,
        })
    if parts:
        path.write_text(json.dumps({"parts": parts}, separators=(",", ":")), encoding="utf-8")
        (path.parent / f"{path.name}.import").write_text(
            '[remap]\n\nimporter="keep"\n', encoding="utf-8")


def _write_anim_metadata(path: Path, anim: N3AnimControl) -> None:
    """Preserve the original N3AnimControl index and event frames. glTF keeps clip
    names/ranges but otherwise discards the numeric IDs used by skill tables and the
    strike/trace/sound timing used by combat presentation."""
    used: dict[str, int] = {}
    entries = []
    for source_index, a in enumerate(anim.animations):
        exported = None
        # Mirror gltf_export's export filter EXACTLY, or `exported` lies about what the glb holds
        # and slot lookups resolve to the wrong clip. A zero source FPS is a data slip (the entry
        # still has a real frame range) and is exported at KO's 30 Hz; only an INVERTED range drops.
        if a.name and a.name != "No Name" and a.frame_end >= a.frame_start:
            exported = a.name
            if exported in used:
                used[exported] += 1
                exported = f"{exported}_{used[exported]}"
            else:
                used[exported] = 0
        entries.append({
            "index": source_index,
            "name": a.name,
            "exported": exported,
            "start": a.frame_start,
            "end": a.frame_end,
            # The EFFECTIVE rate the clip was exported at, so consumers computing a strike time
            # from (frame - start) / fps never divide by a slipped zero.
            "fps": a.fps if a.fps > 0 else 30.0,
            "blend": a.blend_time,
            "blendFlags": a.blend_flags,
            "plugTraceStart": a.plug_trace_start,
            "plugTraceEnd": a.plug_trace_end,
            "sound0": a.sound0_frame,
            "sound1": a.sound1_frame,
            "strike0": a.strike0_frame,
            "strike1": a.strike1_frame,
        })
    path.write_text(json.dumps({"animations": entries}, separators=(",", ":")))


def main():
    ap = argparse.ArgumentParser(description="Bake KO NPC/mob models -> animated skinned .glb.")
    ap.add_argument("--models", nargs="+", type=int, help="explicit model ids")
    ap.add_argument("--models-file", help="file with one model id per line")
    ap.add_argument("--all-indexed", action="store_true",
                    help="rebuild every model already present in the output index")
    ap.add_argument("--all-looks", action="store_true",
                    help="bake every model the NPC looks table names")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--tex-max", type=int, default=TEX_MAX,
                    help=f"max texture dimension (default {TEX_MAX}; 2048 keeps full KO/enhanced art)")
    ap.add_argument("--metadata-only", action="store_true",
                    help="write .anim.json sidecars for already-baked indexed models; do not rebuild GLBs")
    args = ap.parse_args()

    ids = list(args.models or [])
    if args.models_file:
        ids += [int(x) for x in Path(args.models_file).read_text().split() if x.strip().isdigit()]
    ids = sorted(set(ids))
    if not ids and args.all_looks:
        ids = sorted(int(k) for k in _looks_index() if str(k).isdigit() or isinstance(k, int))
    if not ids and (args.metadata_only or args.all_indexed):
        index_path = Path(args.out) / "index.json"
        if index_path.exists():
            ids = [int(x) for x in json.loads(index_path.read_text()).keys()]
    if not ids:
        print("no model ids given", file=sys.stderr)
        return 2

    looks = _looks_index()
    resolver = AssetResolver(KO_DIR)
    out_dir = Path(args.out)

    # Human-readable filename per model; disambiguate ids that share a model name.
    used, stems = {}, {}
    for mid in ids:
        row = looks.get(mid)
        if row is None:
            continue
        stem = safe_name(_model_name(row) or f"mob_{mid}")
        if used.get(stem, mid) != mid:
            stem = f"{stem}_{mid}"
        used[stem] = mid
        stems[mid] = stem

    index_path = out_dir / "index.json"
    index = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text())
        except Exception:
            index = {}

    made = skipped = 0
    for mid in ids:
        row = looks.get(mid)
        if row is None:
            print(f"[mobs] {mid}: not in NPC_Looks", file=sys.stderr)
            skipped += 1
            continue
        stem = stems[mid]
        if args.metadata_only:
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                # The .n3fxplug sidecar is metadata too — regenerating it must not require
                # rebuilding every GLB (the plug layout/scale fix applies to 267 shipped plugs).
                try:
                    fx_plug = _fx_plug_only(row, resolver)
                    if fx_plug is not None:
                        _write_fx_plug_metadata(out_dir / f"{stem}.fxplug.json", fx_plug)
                except Exception as e:
                    print(f"[mobs] {mid}: fx plug: {type(e).__name__}: {e}", file=sys.stderr)
                anim = _animation_only(row, resolver)
                if anim is None:
                    skipped += 1
                    continue
                _write_anim_metadata(out_dir / f"{stem}.anim.json", anim)
                made += 1
            except Exception as e:
                print(f"[mobs] {mid}: animation metadata: {type(e).__name__}: {e}", file=sys.stderr)
                skipped += 1
            continue
        try:
            res, warnings = bake_model(stem, row, resolver, out_dir, tex_max=args.tex_max)
        except Exception as e:
            print(f"[mobs] {mid}: {type(e).__name__}: {e}", file=sys.stderr)
            skipped += 1
            continue
        if res is None:
            print(f"[mobs] {mid}: no usable parts ({'; '.join(warnings[:2])})", file=sys.stderr)
            skipped += 1
            continue
        index[str(mid)] = stem
        nbones, nskins, nclips, nbytes = res
        print(f"[mobs] {mid} -> {stem}.glb: bones={nbones} skins={nskins} clips={nclips} -> {nbytes}B")
        made += 1

    if args.metadata_only:
        print(f"[mobs] animation metadata: {made}/{len(ids)} -> {out_dir}")
        return 0 if made else 1
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(dict(sorted(index.items(), key=lambda kv: int(kv[0]))), indent=0))
    print(f"[mobs] done {made}/{len(ids)} -> {out_dir}  (index: {len(index)} ids)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
