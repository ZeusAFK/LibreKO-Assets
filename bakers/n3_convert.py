"""Offline converter: KO N3 object meshes -> glTF (.glb), one per distinct .opd shape.

One-time bake (like the terrain/opd bakers; the Godot client never reads N3/.opd directly):
  <zone>.opd   -> shape list (name, transform, parts[mesh + pivot + textures])
  object.src   -> .n3pmesh geometry per part (progressive mesh; base LOD only)
  object.src   -> .dxt textures (NTF; v7 RC4 decrypted) -> PNG in the shared tex/ pool
  -> per distinct shape: merge parts (pivot offset), group by texture into glTF
     primitives, write a self-contained .glb (geometry + materials + textures).

Output is standard glTF (textures referenced by relative tex/ URI) so the same files are interchangeable with
Blender (export glb to upgrade a model in place) and imported natively by Godot — no
custom runtime loader, no Blender->KO-format step. Object MODELS are SHARED across maps
(KO ships one global object.src; ~42% of shapes are reused between Moradon and elmo2004),
so they go into ONE flat pool dir keyed by the globally-unique shape name; only the
PLACEMENTS (.kobj, per map) differ. Vertices are emitted in Godot space (KO is
left-handed: negate X to match client/src/Coord.cs, reverse winding) so the imported
mesh lands where the .kobj placement expects it. Placement pos/rot/scale apply at runtime.
"""
import hashlib
import io
import os
import struct
import sys
from pathlib import Path

import numpy as np
import pygltflib as gl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opd_convert as oc
import ko_texture
from _paths import ko as _ko, assets as _assets  # noqa: E402

OBJ_HDR = str(_ko() / "Object" / "object.hdr")
OBJ_SRC = str(_ko() / "Object" / "object.src")
DEFAULT_OUT = str(_assets() / "objects")
ZONES_DIR = str(_ko() / "Zones")

_ARRAY, _ELEM = 34962, 34963
_FLOAT, _USHORT, _UINT = 5126, 5123, 5125


def load_index(hdr_path):
    hdr = open(hdr_path, "rb").read()
    off = 8
    idx = {}
    while off + 2 <= len(hdr):
        nl = struct.unpack_from("<H", hdr, off)[0]
        off += 2
        if nl == 0 or nl > 260 or off + nl + 8 > len(hdr):
            break
        nm = hdr[off:off + nl].decode("latin-1", "replace")
        off += nl
        fo, fs = struct.unpack_from("<II", hdr, off)
        off += 8
        idx[nm.lower()] = (fo, fs)
    return idx


class Archive:
    def __init__(self, hdr, src):
        self.idx = load_index(hdr)
        self.src = open(src, "rb")

    def get(self, name):
        k = name.lower()
        if k not in self.idx:
            return None
        fo, fs = self.idx[k]
        self.src.seek(fo)
        return self.src.read(fs)


def base(p):
    return os.path.basename(p.replace("\\", "/")).lower()


def safe_name(name):
    return "".join(c if (c.isalnum() or c in "_-.") else "_" for c in name.lower())


def tex_id(p):
    """Texture identity: lowercased basename without extension, safe for filenames."""
    return safe_name(os.path.splitext(base(p))[0])


def read_shapes(opd_path):
    """Re-parse an .opd via opd_convert but capture the per-part data it discards."""
    shapes = []

    def patched(r):
        stype = r.u32(); name = r.lpstr(); pos = r.vec3()
        rot = (r.f32(), r.f32(), r.f32(), r.f32()); scale = r.vec3()
        for _ in range(3):
            n = r.u32()
            if n != 0:
                is_quat = r.u32() != 0; r.f32(); r.bytes(n * (16 if is_quat else 12))
        r.lpstr(1024); r.lpstr(1024)
        pc = r.i32(); parts = []
        for _ in range(pc):
            pivot = r.vec3(); mesh = r.lpstr(1024); material = r.bytes(92); tc = r.u32(); tex_fps = r.f32()
            texs = [r.lpstr(1024) for _ in range(tc)]
            # D3DMATERIAL9 (17 floats) + KO extension (color op/args, render flags,
            # source blend and destination blend). Keeping this per part matters:
            # many KO objects use one atlas for both opaque stone and translucent,
            # self-lit effects (the Moradon nation portals are a prominent example).
            mv = struct.unpack("<17f6I", material)
            parts.append({
                "pivot": pivot, "mesh": mesh, "texs": texs, "tex_fps": tex_fps,
                "diffuse": mv[0:4], "ambient": mv[4:8], "specular": mv[8:12],
                "emissive": mv[12:16], "power": mv[16],
                "color_op": mv[17], "color_arg1": mv[18], "color_arg2": mv[19],
                "render_flags": mv[20], "src_blend": mv[21], "dest_blend": mv[22],
            })
        r.i32(); r.i32(); r.i32(); r.i32(); r.i32()
        shapes.append({"name": name, "pos": pos, "rot": rot, "scale": scale, "parts": parts})
        return {"name": name, "pos": pos, "rot": rot, "scale": scale, "type": stype,
                "belong": 0, "event_id": 0, "event_type": 0, "npc_id": 0, "npc_status": 0}

    orig = oc._read_shape
    oc._read_shape = patched
    try:
        oc.read_opd(opd_path)
    finally:
        oc._read_shape = orig
    return shapes


def decode_part(blob):
    """Decode a .n3pmesh to full-resolution (pos, nrm, uv, idx).

    .n3pmesh is a PROGRESSIVE mesh: the on-disk index buffer is stored at MINIMUM LOD
    (the index count starts at the minimum), and full
    detail is reached by replaying a vertex-SPLIT per collapse record, each rewriting
    indices[allIndexChanges[k]] = (runningVertexCount - 1).
    Reading the raw buffer as full-res yields collapsed/shattered triangles on the ~80%
    of object meshes that carry collapses (buildings/pillars/arches). Verified: replaying
    splits converges exactly to (maxVerts, maxIndices) with in-range indices on all 5788
    collapse meshes in object.src. Layout (little-endian):
      i32 nameLen + name | i32 nc, totalIdxChanges, maxV, maxI, minV, minI
      | maxV * 32B (pos3f, nrm3f, uv2f) | maxI * u16 indices
      | nc * 24B collapse (5*i32: nIdxLose,nIdxChange,nVtxLose,iIdxChanges,collapseTo; bool+3pad)
      | totalIdxChanges * i32 | i32 lodCount + lodCount*8B   (LOD tail ignored)
    """
    o = 0
    nl = struct.unpack_from("<i", blob, o)[0]; o += 4
    if nl > 0:
        o += nl
    nc, tic, maxV, maxI, minV, minI = struct.unpack_from("<6i", blob, o); o += 24

    vbytes = np.frombuffer(blob, dtype="<f4", count=maxV * 8, offset=o).reshape(maxV, 8)
    o += maxV * 32
    pos = vbytes[:, 0:3].astype("<f4")
    nrm = vbytes[:, 3:6].astype("<f4")
    uv = vbytes[:, 6:8].astype("<f4")
    indices = list(struct.unpack_from(f"<{maxI}H", blob, o)); o += maxI * 2

    collapses = []
    for _ in range(nc):
        rec = struct.unpack_from("<5i", blob, o); o += 20
        o += 4   # bool bShouldCollapse + 3 pad
        collapses.append(rec)
    all_changes = struct.unpack_from(f"<{tic}i", blob, o) if tic else ()

    # Replay splits min->max LOD so the index buffer reaches full resolution.
    num_v = minV
    for (nIdxLose, nIdxChange, nVtxLose, iIdxChanges, collapseTo) in collapses:
        num_v += nVtxLose
        for k in range(iIdxChanges, iIdxChanges + nIdxChange):
            if 0 <= k < len(all_changes):
                p = all_changes[k]
                if 0 <= p < maxI:
                    indices[p] = num_v - 1

    idx = np.array(indices, "<u4")
    return pos, nrm, uv, idx


def decode_textures(arch, texmap):
    """Decode each distinct texture ONCE -> {tid: (png_bytes, has_alpha)}; the PNG bytes
    are EMBEDDED into every glb that uses the texture (self-contained models)."""
    from PIL import Image
    out = {}
    made = failed = 0
    for tid, key in sorted(texmap.items()):
        blob = arch.get(key)
        res = None
        if blob is not None:
            try:
                res = ko_texture.decode_ntf(blob)
            except Exception:
                res = None
        if res is None:
            failed += 1
            continue
        w, h, rgba = res
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, "PNG")
        out[tid] = (buf.getvalue(), bool(rgba[:, :, 3].min() < 250))
        made += 1
    print(f"textures: {made} decoded, {failed} failed")
    return out


def write_flipbook_frames(tex_path, digest, source, textures):
    """Frames 1..N-1 of an animated part ship beside frame 0 as <digest>_f<i>.png; the
    client derives their paths from the material's albedo texture (World.Environment)."""
    for index, frame_tid in enumerate(source.get("frame_tids") or ()):
        if index == 0 or frame_tid not in textures:
            continue
        frame_path = tex_path / f"{digest}_f{index}.png"
        if not frame_path.exists():
            frame_path.write_bytes(textures[frame_tid][0])


def build_glb(path, groups, textures, texture_dir=None, texture_uri_prefix=""):
    """Write a self-contained .glb from groups = list of (material, pos, nrm, uv, idx),
    one glTF primitive per group; textures (PNG bytes) embedded as bufferView images.
    Geometry is converted KO->Godot here (negate X, reverse winding)."""
    blob = bytearray()
    views, accessors = [], []
    main_primitives, meshes, nodes = [], [], []
    materials, gtex, images = [], [], []
    samplers = [gl.Sampler(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497)]
    mat_for_key, img_for_tid, shared_tex = {}, {}, {}
    uses_unlit = False

    def add_view(data, target=None):
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(data)
        bv = gl.BufferView(buffer=0, byteOffset=off, byteLength=len(data))
        if target is not None:
            bv.target = target
        views.append(bv)
        return len(views) - 1

    def add_accessor(**kw):
        accessors.append(gl.Accessor(**kw))
        return len(accessors) - 1

    def material_for(source):
        nonlocal uses_unlit
        tid = source["tid"]
        flags = source["render_flags"]
        key = (tid, flags, source["src_blend"], source["dest_blend"],
               tuple(round(float(v), 6) for v in source["diffuse"]),
               tuple(round(float(v), 6) for v in source["emissive"]),
               tuple(source.get("frame_tids") or ()))
        if key in mat_for_key:
            return mat_for_key[key]
        alpha_blend = bool(flags & 0x001)
        no_light = bool(flags & 0x040)
        double_sided = bool(flags & 0x004)
        if tid and tid in textures:
            if tid not in img_for_tid:
                png, has_alpha = textures[tid]
                digest = hashlib.sha1(png).hexdigest()[:16]
                shared = shared_tex.get(digest)
                if shared is not None:
                    img_for_tid[tid] = (shared, has_alpha)
                else:
                    if texture_dir is not None:
                        tex_path = Path(texture_dir)
                        tex_path.mkdir(parents=True, exist_ok=True)
                        blob_path = tex_path / f"{digest}.png"
                        if not blob_path.exists():
                            blob_path.write_bytes(png)
                        images.append(gl.Image(uri=f"{texture_uri_prefix}{digest}.png",
                                               mimeType="image/png"))
                        write_flipbook_frames(tex_path, digest, source, textures)
                    else:
                        iv = add_view(png)              # image bufferView: no target
                        images.append(gl.Image(bufferView=iv, mimeType="image/png"))
                    gtex.append(gl.Texture(source=len(images) - 1, sampler=0))
                    shared_tex[digest] = len(gtex) - 1
                    img_for_tid[tid] = (len(gtex) - 1, has_alpha)
            ti, has_alpha = img_for_tid[tid]
            mat = gl.Material(
                name=source["name"],
                pbrMetallicRoughness=gl.PbrMetallicRoughness(
                    baseColorTexture=gl.TextureInfo(index=ti),
                    baseColorFactor=list(source["diffuse"]),
                    metallicFactor=0.0, roughnessFactor=1.0),
                alphaMode="BLEND" if alpha_blend else ("MASK" if has_alpha else "OPAQUE"),
                alphaCutoff=None if alpha_blend else (0.5 if has_alpha else None),
                doubleSided=double_sided)
        else:
            mat = gl.Material(
                name=source["name"],
                pbrMetallicRoughness=gl.PbrMetallicRoughness(
                    baseColorFactor=list(source["diffuse"]),
                    metallicFactor=0.0, roughnessFactor=1.0),
                alphaMode="BLEND" if alpha_blend else "OPAQUE",
                doubleSided=double_sided)
        if no_light:
            # KHR_materials_unlit maps KO's NO_LIGHT render flag without inventing
            # environment-dependent brightness. Godot imports it as an unshaded material.
            mat.extensions = {"KHR_materials_unlit": {}}
            uses_unlit = True
        mat_for_key[key] = len(materials)
        materials.append(mat)
        return mat_for_key[key]

    for (source, pos, nrm, uv, idx) in groups:
        pos = pos.copy(); pos[:, 0] = -pos[:, 0]
        nrm = nrm.copy(); nrm[:, 0] = -nrm[:, 0]
        idx = idx.reshape(-1, 3)[:, ::-1].reshape(-1)
        n = pos.shape[0]
        mat = material_for(source)

        a_pos = add_accessor(bufferView=add_view(pos.astype("<f4").tobytes(), _ARRAY),
                             componentType=_FLOAT, count=n, type="VEC3",
                             min=pos.min(axis=0).tolist(), max=pos.max(axis=0).tolist())
        a_nrm = add_accessor(bufferView=add_view(nrm.astype("<f4").tobytes(), _ARRAY),
                             componentType=_FLOAT, count=n, type="VEC3")
        a_uv = add_accessor(bufferView=add_view(uv.astype("<f4").tobytes(), _ARRAY),
                            componentType=_FLOAT, count=n, type="VEC2")
        use32 = n >= 65536
        idx_arr = idx.astype("<u4" if use32 else "<u2")
        a_idx = add_accessor(bufferView=add_view(idx_arr.tobytes(), _ELEM),
                             componentType=(_UINT if use32 else _USHORT),
                             count=int(idx.shape[0]), type="SCALAR")

        primitive = gl.Primitive(
            attributes=gl.Attributes(POSITION=a_pos, NORMAL=a_nrm, TEXCOORD_0=a_uv),
            indices=a_idx, material=mat)
        if source["render_flags"] & 0x008:  # RF_BOARD_Y: rotate this part about its own pivot
            meshes.append(gl.Mesh(primitives=[primitive], name=source["name"]))
            px, py, pz = source["pivot"]
            nodes.append(gl.Node(
                mesh=len(meshes) - 1,
                name=source["name"],
                translation=[-float(px), float(py), float(pz)]))
        else:
            main_primitives.append(primitive)

    if main_primitives:
        meshes.append(gl.Mesh(primitives=main_primitives, name="static_parts"))
        nodes.insert(0, gl.Node(mesh=len(meshes) - 1, name="static_parts"))

    g = gl.GLTF2(
        scene=0,
        scenes=[gl.Scene(nodes=list(range(len(nodes))))],
        nodes=nodes,
        meshes=meshes,
        materials=materials, textures=gtex, images=images, samplers=samplers,
        accessors=accessors, bufferViews=views,
        buffers=[gl.Buffer(byteLength=len(blob))])
    if uses_unlit:
        g.extensionsUsed = ["KHR_materials_unlit"]
    g.set_binary_blob(bytes(blob))
    g.save_binary(path)


def convert(opd_path, out_dir, skip_existing=False, only=None):
    """Convert all distinct shapes in one .opd into the SHARED model pool out_dir
    (flat, keyed by globally-unique shape name). Safe to run per-zone; reused models
    are written identically each time (or skipped with skip_existing)."""
    os.makedirs(out_dir, exist_ok=True)
    arch = Archive(OBJ_HDR, OBJ_SRC)
    shapes = read_shapes(opd_path)
    seen = {}
    for s in shapes:
        seen.setdefault(s["name"].lower(), s)
    if only:
        wanted = {name.lower() for name in only}
        seen = {name: shape for name, shape in seen.items() if name in wanted}

    texmap = {}
    for s in seen.values():
        for part in s["parts"]:
            nz = [t for t in part["texs"] if t]
            for frame in (nz if part["tex_fps"] > 0 else nz[:1]):
                texmap[tex_id(frame)] = base(frame)
    textures = decode_textures(arch, texmap)

    made = skipped = missing_parts = empty = big = existing = 0
    for key, s in seen.items():
        out_path = os.path.join(out_dir, safe_name(key) + ".glb")
        if skip_existing and os.path.exists(out_path):
            existing += 1
            continue
        parts = s["parts"]
        if not parts:
            empty += 1
            continue
        groups = []
        for part_index, part in enumerate(parts):
            pivot, mesh, texs = part["pivot"], part["mesh"], part["texs"]
            blob = arch.get(base(mesh))
            if blob is None:
                missing_parts += 1
                continue
            try:
                pos, nrm, uv, idx = decode_part(blob)
            except Exception:
                missing_parts += 1
                continue
            if pos.shape[0] == 0 or idx.shape[0] == 0:
                continue
            # RF_BOARD_Y parts must retain local vertices and a separate pivoted node;
            # ordinary parts remain baked into one shared static mesh for draw/runtime cost.
            if not (part["render_flags"] & 0x008):
                pos = pos + np.array(pivot, "<f4")
            nz = [t for t in texs if t]
            source = dict(part)
            source["tid"] = tex_id(nz[0]) if nz else ""
            source["frame_tids"] = [tex_id(t) for t in nz] if part["tex_fps"] > 0 and len(nz) > 1 else []
            # Preserve KO render state in the imported material name as well. glTF can
            # represent alpha/unlit, but not additive blending, disabled depth writes,
            # point sampling, etc. World.Environment applies these remaining flags to
            # Godot's StandardMaterial3D once when the shared mesh is cached.
            source["name"] = (
                f"{safe_name(os.path.splitext(base(mesh))[0])}_p{part_index}"
                f"__ko_f{source['render_flags']:x}_s{source['src_blend']}_d{source['dest_blend']}"
                + (f"_a{part['tex_fps']:g}x{len(source['frame_tids'])}"
                   if source["frame_tids"] else "")
            )
            groups.append((source, pos, nrm, uv, idx))
        if not groups:
            skipped += 1
            continue
        total_v = sum(group[1].shape[0] for group in groups)
        if total_v >= 65536:
            big += 1
        build_glb(out_path, groups, textures,
                  texture_dir=Path(out_dir) / "tex", texture_uri_prefix="tex/")
        made += 1

    print(f"shapes={len(shapes)} distinct={len(seen)} glb_written={made} "
          f"skipped(no usable parts)={skipped} empty(0 parts)={empty} "
          f"already-existed={existing} missing/failed parts={missing_parts} big(>=64k verts)={big}")
    print(f"out_dir={out_dir}")


def convert_all_zones(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    zones = sorted(name for name in os.listdir(ZONES_DIR) if name.lower().endswith(".opd"))
    ok = failed = 0
    for filename in zones:
        try:
            convert(os.path.join(ZONES_DIR, filename), out_dir, skip_existing=True)
            ok += 1
        except Exception as e:
            failed += 1
            print(f"[objects] {filename}: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"[objects] {ok} zone(s) converted, {failed} skipped -> {out_dir}")
    return 0 if ok else 1


def convert_all_affected(out_dir, dry_run=False, board_only=False, everything=False):
    """Rebuild existing object assets whose authored KO material state was lost by
    the old texture-only grouping. First definition wins, matching the shared model pool."""
    existing = {
        os.path.splitext(name)[0].lower()
        for name in os.listdir(out_dir)
        if name.lower().endswith(".glb")
    }
    claimed = set()
    batches = []
    failures = 0
    from collections import Counter
    blend_pairs = Counter()
    flag_values = Counter()
    for filename in sorted(os.listdir(ZONES_DIR)):
        if not filename.lower().endswith(".opd"):
            continue
        opd = os.path.join(ZONES_DIR, filename)
        try:
            shapes = read_shapes(opd)
        except Exception:
            failures += 1
            continue
        wanted = []
        for shape in shapes:
            key = shape["name"].lower()
            if key in claimed or safe_name(key) not in existing:
                continue
            # Non-zero flags contain alpha blend, double-sided, billboard, point
            # filtering, windy, unlit and/or depth state. The previous bake lost all.
            is_affected = any(part["render_flags"] != 0 for part in shape["parts"])
            if board_only:
                is_affected = any(part["render_flags"] & 0x008 for part in shape["parts"])
            if everything:
                is_affected = True
            if is_affected:
                claimed.add(key)
                wanted.append(key)
                for part in shape["parts"]:
                    if part["render_flags"] != 0:
                        flag_values[part["render_flags"]] += 1
                        blend_pairs[(part["src_blend"], part["dest_blend"])] += 1
        if wanted:
            batches.append((opd, wanted))

    scope = ("every existing object" if everything else
             "RF_BOARD_Y objects" if board_only else "affected existing objects")
    print(f"{scope}={len(claimed)} across {len(batches)} zone files "
          f"(unsupported zone files skipped={failures})")
    print(f"blend pairs={dict(blend_pairs)}")
    print(f"render flags={dict(flag_values)}")
    if dry_run:
        return
    for index, (opd, wanted) in enumerate(batches, 1):
        print(f"[{index}/{len(batches)}] {os.path.basename(opd)}: {len(wanted)} objects")
        convert(opd, out_dir, only=wanted)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="KO N3 object meshes -> shared glb model pool.")
    ap.add_argument("--opd", default=r"<retail install>\Zones\moradon.opd")
    ap.add_argument("--out", default=DEFAULT_OUT, help="shared model pool dir")
    ap.add_argument("--skip-existing", action="store_true",
                    help="don't re-bake a model whose .glb already exists in the pool")
    ap.add_argument("--only", action="append",
                    help="convert only this exact shape name (repeatable)")
    ap.add_argument("--all-affected", action="store_true",
                    help="rebuild every existing object whose KO render flags were lost")
    ap.add_argument("--all-board-y", action="store_true",
                    help="rebuild existing objects containing pivoted camera-facing parts")
    ap.add_argument("--all-existing", action="store_true",
                    help="rebuild every object already in the pool (used after a pipeline change)")
    ap.add_argument("--all", action="store_true",
                    help="convert every shape placed in any zone .opd, skipping models already in the pool")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --all-affected, report scope without writing")
    a = ap.parse_args()
    if a.all:
        return convert_all_zones(a.out)
    if a.all_affected or a.all_board_y or a.all_existing:
        convert_all_affected(a.out, a.dry_run, board_only=a.all_board_y,
                             everything=a.all_existing)
    else:
        convert(a.opd, a.out, a.skip_existing, a.only)


if __name__ == "__main__":
    raise SystemExit(main() or 0)
