"""bake_fx.py -- one-time conversion of KO .fxb effects -> Godot-native assets.

Reads the KO fx archive (fx.hdr/fx.src), parses each .fxb via tools/fxb_parse.py,
extracts the referenced .dxt textures to PNG, and writes a Godot-friendly JSON
descriptor per effect. The runtime (client/src/Fx.cs) builds GPUParticles3D /
billboard quads from the JSON + PNGs -- it never touches .fxb/.dxt/the archive.

Mirrors the project's other bakers (offline -> <output>, runtime reads baked).

Texture/mesh name rule (verified): texName has an "fx\\" prefix the archive keys
lack; textures append a 4-digit frame index + ".dxt"  ->  archive key =
  strip "fx\\" from texName, + "%04d.dxt" % frame.
Vectors are converted KO->Godot the same way as terrain/objects/water: negate X.

Usage:
    python bake.py --only bake_fx --all
    python bake.py --only bake_fx --name dragon_mp wrath_fire_b
    python bake.py --only bake_fx --list 40           # just print 40 effect names
"""
from __future__ import annotations
import argparse, io, json, math, os, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                      # vendored kotools
sys.path.insert(0, str(_HERE.parent / "docs"))      # fxb_parse.py

import struct                                          # noqa: E402
import numpy as np                                    # noqa: E402
from PIL import Image                                  # noqa: E402
from fxb_parse import parse_fxb, _hdr_index          # noqa: E402
from n3_convert import decode_part                    # noqa: E402  (.n3pmesh progressive-mesh decoder)
from kotools.ntf.texture import read_dxt_from_bytes   # noqa: E402
from kotools.ntf.header import NTFHeader              # noqa: E402
from kotools.ntf.decoder import decode_to_rgba        # noqa: E402
from kotools.crypto.wincrypt import KOCipher, DEFAULT_KEY, RC4  # noqa: E402
from kotools.io.binary_reader import BinaryReader     # noqa: E402
from kotools.formats.d3dformat import D3DFormat as _D3DFormat  # noqa: E402
import hashlib                                        # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

def _first_existing(*paths: str) -> str:
    for path in paths:
        if Path(path).exists():
            return path
    return paths[0]


KO_FX_HDR = str(_ko() / "fx" / "fx.hdr")
KO_FX_SRC = str(_ko() / "fx" / "fx.src")
OUT_DIR = _assets() / "fx"
TEX_DIR = OUT_DIR / "tex"
MESH_DIR = OUT_DIR / "mesh"   # baked FX Mesh-part geometry (.n3shape -> json), deduped by shape stem
TEX_MAX = 512   # fx textures are small (mostly 256); cap to be safe


def _strip_fx(name: str) -> str:
    name = name.replace("/", "\\")
    return name[3:] if name.lower().startswith("fx\\") else name


def _stem(archive_key: str) -> str:
    """archive key -> filesystem-safe PNG stem (no extension)."""
    base = os.path.splitext(archive_key)[0]
    return base.replace("\\", "__").replace("/", "__").replace(" ", "_")


def _blend(src: int, dest: int) -> str:
    # D3DBLEND dest == ONE(2) -> additive glow; otherwise alpha blend.
    return "add" if dest == 2 else "alpha"


def _argb_to_rgba(c: int):
    a = (c >> 24) & 0xFF; r = (c >> 16) & 0xFF; g = (c >> 8) & 0xFF; b = c & 0xFF
    return [round(r / 255, 4), round(g / 255, 4), round(b / 255, 4), round(a / 255, 4)]


def _negx(v):
    """KO->Godot vector (negate X), tolerant of None."""
    if not v:
        return [0.0, 0.0, 0.0]
    # Significant digits, not decimals: FX vectors range from micro-scale shape pivots to
    # thousand-unit scale keys (see _sig).
    return [_sig(-v[0]), _sig(v[1]), _sig(v[2])]


def _negx_quat(q):
    """KO->Godot quaternion under the fixed X-mirror diag(-1,1,1): negate y,z (conjugation), like
    the object placer's (qx,-qy,-qz,qw). Tolerant of None."""
    if not q or len(q) < 4:
        return [0.0, 0.0, 0.0, 1.0]
    return [round(q[0], 5), round(-q[1], 5), round(-q[2], 5), round(q[3], 5)]


def _scale3(v, default=1.0):
    if not v:
        return [default, default, default]
    return [round(v[0], 4), round(v[1], 4), round(v[2], 4)]


class _SR:
    """Tiny little-endian struct reader for the .n3shape format."""
    def __init__(self, d): self.d = d; self.p = 0
    def i32(self): v = struct.unpack_from("<i", self.d, self.p)[0]; self.p += 4; return v
    def u32(self): v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v
    def f32(self): v = struct.unpack_from("<f", self.d, self.p)[0]; self.p += 4; return v
    def vec3(self): v = struct.unpack_from("<3f", self.d, self.p); self.p += 12; return list(v)
    def quat(self): v = struct.unpack_from("<4f", self.d, self.p); self.p += 16; return list(v)
    def skip(self, n): self.p += n
    def path(self):
        n = self.i32()
        if n < 0 or n > 1024:
            raise ValueError("bad path len %d" % n)
        s = self.d[self.p:self.p + n].decode("latin-1"); self.p += n
        return s


def _anim_vec(r):
    """ReadVectorAnimationKey: count, [type,rate, keys]. Returns (keys|None, rate)."""
    n = r.i32()
    if n <= 0:
        return None, 30.0
    t = r.u32(); rate = r.f32()
    if t != 0:                       # quaternion-typed vector track (unusual) -> skip
        r.skip(n * 16); return None, rate
    return [r.vec3() for _ in range(n)], rate


def _anim_quat(r):
    n = r.i32()
    if n <= 0:
        return None, 30.0
    t = r.u32(); rate = r.f32()
    if t != 1:
        r.skip(n * 12); return None, rate
    return [r.quat() for _ in range(n)], rate


def _sig(x, digits=7):
    """Round to significant digits, not decimal places.

    FX shape geometry spans an enormous dynamic range: a shape's own transform carries the scale, so
    the vertex buffer may be authored in micro-units and blown up at runtime. `muder_a02\\sword_a04`
    (the NPC indicator's glow star) is the extreme case — its verts are ~3e-4 and its scale key track
    ramps to 2783. Rounding those verts to 4 DECIMALS quantised them onto a 3-value grid and
    collapsed most of the 6 quads to degenerate slivers, so the part rendered as nothing at all.
    Significant-digit rounding keeps both the micro-meshes and the ordinary metre-scale ones exact
    enough while staying compact in json."""
    return float("%.*g" % (digits, float(x)))


def _whole_frame(keys, rate):
    # Animation keys are 30 fps frame-time -> duration = count*30/rate.
    return (len(keys) * 30.0 / rate) if (keys and rate > 0) else 0.0


def _resolve_archive(arch: "Archive", path: str, sibling_dir: str):
    """Resolve a path referenced inside a .n3shape to a real archive key. Paths there are stored
    relative to the SHAPE's directory (the client resolves siblingDirectory/filename), so
    `object\\foo.n3pmesh` under shape `object\\sub\\bar.n3shape` lives at `object\\sub\\foo.n3pmesh`."""
    base = _strip_fx(path)
    cands = [base]
    if sibling_dir:
        cands.append(f"{sibling_dir}\\{base.split(chr(92))[-1]}")
    for c in cands:
        real = arch.low.get(c.lower())
        if real:
            return real
    return None


def _bake_shape(arch: "Archive", shape_name: str, made: dict, made_shapes: dict):
    """Parse a FX Mesh part's .n3shape (KoFxShapeReader format), decode its .n3pmesh geometry +
    textures, and write a deduped Godot-native mesh json (assets/fx/mesh/<stem>.json). Returns the
    stem to reference, or None if it can't be baked. KO->Godot: negate X on verts/normals/positions
    + mirror quaternions + flip triangle winding."""
    key = _strip_fx(shape_name)
    stem = _stem(key)
    if stem in made_shapes:
        return made_shapes[stem]
    out = MESH_DIR / f"{stem}.json"
    blob = arch.read(key)
    if blob is None:
        made_shapes[stem] = None
        return None
    sibling = key.rsplit("\\", 1)[0] if "\\" in key else ""
    try:
        r = _SR(blob)
        r.path()                                  # shape name (ignored)
        pos = _negx(r.vec3()); rot = _negx_quat(r.quat()); scale = r.vec3()
        pkeys, prate = _anim_vec(r)
        rkeys, rrate = _anim_quat(r)
        skeys, srate = _anim_vec(r)
        r.path(); r.path()                         # two collision-mesh paths (ignored)
        npart = r.i32()
        if npart < 0 or npart > 512:
            raise ValueError("bad partCount %d" % npart)
        parts = []
        for _ in range(npart):
            pivot = _negx(r.vec3())
            mesh_path = r.path()
            mat = r.d[r.p:r.p + 92]; r.skip(92)
            render_flags, src_b, dst_b = struct.unpack_from("<3I", mat, 80)
            tcount = r.i32(); tfps = r.f32()
            tex_paths = [r.path() for _ in range(max(0, tcount))]
            mreal = _resolve_archive(arch, mesh_path, sibling)
            mblob = arch.read(mreal) if mreal else None
            if mblob is None:
                continue
            vpos, vnrm, vuv, idx = decode_part(mblob)
            # negate X on positions + normals; flip winding (X-mirror reverses handedness)
            vpos = vpos.copy(); vpos[:, 0] *= -1.0
            tri = idx.reshape(-1, 3)[:, [0, 2, 1]].reshape(-1)
            tex_stems = []
            for tex_path in tex_paths:
                treal = _resolve_archive(arch, tex_path, sibling)
                if not treal:
                    continue
                ts = _stem(treal)
                if _extract_texture(arch, treal, ts, made):
                    tex_stems.append(ts)
            # A textured FX surface without a decodable texture must not be baked as an
            # opaque white mesh. That turns missing/corrupt source frames into giant white
            # planes in Godot (notably damage0_1). Truly untextured surfaces retain their
            # D3D diffuse colour below.
            if tex_paths and not tex_stems:
                continue
            diffuse = struct.unpack_from("<4f", mat, 0)
            parts.append({
                "pivot": pivot,
                # NOTE: the sub-part's own render_flags are deliberately NOT emitted. They never
                # reach the device: the part-mesh loader derives one flag word
                # from the FX PART's D3D states and writes it over every sub-part.
                # The sub-part material's src/dest blend is dead for the same reason — the part sets
                # SRCBLEND/DESTBLEND when it renders. Only "blend" is kept, as diagnostics.
                "blend": _blend(src_b, dst_b),
                "tex": tex_stems[0] if tex_stems else None,  # legacy runtime compatibility
                "texs": tex_stems,
                "color": [round(float(c), 4) for c in diffuse],
                "fps": round(tfps, 3),
                "pos": [_sig(x) for x in vpos.reshape(-1)],
                "uv": [_sig(x) for x in vuv.reshape(-1)],
                "idx": [int(i) for i in tri],
            })
        if not parts:
            # Do not leave a stale pre-fix mesh descriptor behind: the current effect
            # correctly omits this shape when every referenced texture is undecodable.
            if out.exists():
                out.unlink()
            made_shapes[stem] = None
            return None
        doc = {
            "base": {"pos": pos, "rot": rot, "scale": [_sig(s) for s in scale]},
            "wholeFrame": round(max(_whole_frame(pkeys, prate), _whole_frame(rkeys, rrate),
                                    _whole_frame(skeys, srate)), 3),
            "posKeys": [_negx(k) for k in pkeys] if pkeys else None, "posRate": round(prate, 3),
            "rotKeys": [_negx_quat(k) for k in rkeys] if rkeys else None, "rotRate": round(rrate, 3),
            "scaleKeys": [[_sig(c) for c in k] for k in skeys] if skeys else None, "scaleRate": round(srate, 3),
            "parts": parts,
        }
        MESH_DIR.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, separators=(",", ":")))
        made_shapes[stem] = stem
        return stem
    except Exception as e:
        print(f"[fx-mesh] SKIP {key}: {e}", file=sys.stderr)
        made_shapes[stem] = None
        return None


def warn_unimported() -> int:
    """Count baked PNGs Godot has never imported, and shout about them.

    A texture without a sibling .import is INVISIBLE to ResourceLoader, and both consumers fail
    SILENTLY: Fx.BuildParticles returns null (the part vanishes) and FxMesh.LoadTextures leaves
    AlbedoTexture unset (an additive part then renders as a flat WHITE surface). That is exactly how
    the repaired moraranker flash shell showed up as white bars over the ranking plaques even though
    its texture had decoded correctly. Fix with:
        <godot> --headless --path client --import        (watchdog it; mono headless can hang on exit)
    """
    missing = [p for p in TEX_DIR.glob("*.png") if not (TEX_DIR / (p.name + ".import")).exists()]
    if missing:
        print(f"[fx] WARNING: {len(missing)} baked texture(s) have no .import — Godot CANNOT load "
              f"them; mesh parts will render WHITE and particle parts will not render at all.\n"
              f"[fx]          run: <godot> --headless --path client --import", file=sys.stderr)
        for p in missing[:8]:
            print(f"[fx]          {p.name}", file=sys.stderr)
    return len(missing)


def _shape_centre(stem: str):
    """Local AABB centre of a baked shape's geometry — the emitter point retail uses (see the
    emitterRef comment in the Particles branch). None if the shape has no usable geometry."""
    p = MESH_DIR / f"{stem}.json"
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for part in doc.get("parts", []):
        v = part.get("pos") or []
        piv = part.get("pivot") or [0.0, 0.0, 0.0]
        for i in range(0, len(v) - 2, 3):
            for a in range(3):
                c = v[i + a] + piv[a]
                lo[a] = min(lo[a], c)
                hi[a] = max(hi[a], c)
    if lo[0] > hi[0]:
        return None
    return [_sig(0.5 * (lo[a] + hi[a])) for a in range(3)]


def _bundle_centre_y(out_parts: list) -> float:
    """The Y a bundle is composed around: the height most of its parts agree on. Deliberately ignores
    part SIZE — anchoring an NPC indicator on its lowest point instead makes a wide-winged bundle ride
    far higher than a compact one, which reads as the icons sitting at inconsistent heights."""
    tally: dict[float, int] = {}
    best, best_n = 0.0, 0
    for p in out_parts:
        y = round(p["initPos"][1], 2)
        tally[y] = tally.get(y, 0) + 1
        if tally[y] > best_n or (tally[y] == best_n and y > best):
            best, best_n = y, tally[y]
    return best


def _start_time(t):
    t = float(t)
    if not math.isfinite(t):
        raise ValueError(f"non-finite FX start time: {t}")
    return t


class Archive:
    def __init__(self, hdr, src):
        self.idx = _hdr_index(hdr)
        self.low = {k.lower(): k for k in self.idx}
        self.src = src

    def read(self, archive_key: str) -> bytes | None:
        real = self.low.get(archive_key.lower())
        if real is None:
            return None
        fo, fs = self.idx[real]
        with open(self.src, "rb") as f:
            f.seek(fo); return f.read(fs)

    def resolve_tex(self, tex_name: str, frame: int):
        """texName (+frame) -> (archive_key, stem) or None."""
        base = _strip_fx(tex_name).lower()
        for cand in (f"{base}{frame:04d}.dxt", f"{base}{frame}.dxt", f"{base}.dxt",
                     f"{base}{frame:04d}.tga", f"{base}.tga"):
            if cand in self.low:
                k = self.low[cand]
                return k, _stem(k)
        return None


def _img_noise(img) -> float:
    """Mean abs horizontal pixel delta — low for a real image, ~80+ for decrypt/format garbage."""
    a = np.asarray(img.convert("RGB"), np.int16)
    return float(np.abs(np.diff(a, axis=1)).mean()) if a.shape[1] > 1 else 0.0


_NOISE_GARBAGE = 45.0   # clean fx textures < ~21; decrypt/format garbage ~ 82

# NTF v7 decryption for UNCOMPRESSED surfaces. The RC4 key is derived as
# SHA1(cipher)[:16], and the texture loader decrypts a locked surface ROW BY ROW with
# CryptDecrypt(..., Final=TRUE): for the MS RC4 provider Final=TRUE RESETS the keystream, so every
# row starts again at keystream position 0 (chunk = bytesPerPixel * width). kotools'
# read_dxt_from_bytes models ONE continuous stream, which is correct for the DXT-compressed path
# (the client reads those in a single call) but turns every uncompressed v7 surface into rainbow
# noise -> _decode_clean rejected it -> the whole FX part was silently dropped. bake_items.py has
# carried this fix for the item icons since the inventory work; bake_fx.py never got it.
# Proof it is right, not tuned: all five mip levels of a v7 A8R8G8B8 fx texture decode with the
# payload exactly consumed, and each level is a 2x box downscale of the previous (mean abs error
# 1.9-3.3/255 = rounding). A wrong row length or keystream cannot be self-consistent across mips.
_RC4_KEY = hashlib.sha1(b"owsd9012%$1as!wpow1033b%!@%12").digest()[:16]
_BPP_UNCOMPRESSED = {20: 3, 21: 4, 22: 4, 23: 2, 24: 2, 25: 2, 26: 2}   # D3DFORMAT -> bytes/pixel


def _fmt_value(fmt) -> int:
    return fmt.value if hasattr(fmt, "value") else int(fmt)


def _decode_v7_rows(blob: bytes, h: "NTFHeader", bpp: int):
    """Decrypt an uncompressed v7 surface with the per-row keystream reset, then decode level 0
    through the shared kotools decoder (so channel order stays in one place)."""
    row = bpp * h.width
    px = blob[h.header_size:]
    dec = bytearray()
    for y in range(h.height):
        dec += RC4(_RC4_KEY).process(px[y * row:(y + 1) * row])
    rgba = decode_to_rgba(bytes(dec), h.width, h.height, h.format)
    return Image.frombytes("RGBA", (h.width, h.height), bytes(rgba[: h.width * h.height * 4]))


def _decode_clean(blob: bytes):
    """KO .dxt -> PIL image. Decode as the header says; if that's garbage (rainbow noise — happens
    for a few odd fx-tool textures, e.g. v7 + fmt=21 A8R8G8B8 that kotools can't decode either way),
    flip the decrypt decision and keep the cleaner result. Returns None if STILL garbage, so the
    caller skips it (no PNG → the part is skipped) rather than emitting a rainbow square."""
    h0 = NTFHeader.from_reader(BinaryReader(blob))
    if _fmt_value(h0.format) == 0x34545844:      # DXT4 == DXT5 blocks (premultiplied alpha), and
        h0.format = _D3DFormat.DXT5              # kotools has no DXT4 entry -> raises. 1 fx texture
        blob = h0.to_bytes() + blob[h0.header_size:]   # (30917vy_light\vy_light, used by obj_dunfx_yl01)
        h0 = NTFHeader.from_reader(BinaryReader(blob))
    bpp = _BPP_UNCOMPRESSED.get(_fmt_value(h0.format))
    if h0.is_encrypted and bpp is not None:
        try:
            img = _decode_v7_rows(blob, h0, bpp)
            if _img_noise(img) <= _NOISE_GARBAGE:
                return img
        except Exception:
            pass            # fall through to the single-stream path rather than lose the texture
    tex = read_dxt_from_bytes(blob)
    best = tex.to_pil_image()
    bestn = _img_noise(best)
    if bestn > _NOISE_GARBAGE:
        h = NTFHeader.from_reader(BinaryReader(blob))
        px = blob[h.header_size:]
        alt_px = px if tex.header.is_encrypted else KOCipher(DEFAULT_KEY).decrypt(px)
        try:
            rgba = decode_to_rgba(alt_px, h.width, h.height, h.format)
            alt = Image.frombytes("RGBA", (h.width, h.height), bytes(rgba[: h.width * h.height * 4]))
            an = _img_noise(alt)
            if an < bestn:
                best, bestn = alt, an
        except Exception:
            pass
    return best if bestn <= _NOISE_GARBAGE else None


def _extract_texture(arch: Archive, archive_key: str, stem: str, made: dict) -> bool:
    if stem in made:
        return made[stem]
    if (TEX_DIR / f"{stem}.png").exists():       # already extracted -> fast re-bake (JSON only)
        made[stem] = True
        return True
    blob = arch.read(archive_key)
    ok = False
    if blob:
        try:
            img = _decode_clean(blob)
            if img is None:                      # undecodable -> skip (no garbage PNG)
                made[stem] = False
                return False
            if max(img.size) > TEX_MAX:
                s = TEX_MAX / max(img.size)
                img = img.resize((max(1, int(img.size[0] * s)), max(1, int(img.size[1] * s))))
            TEX_DIR.mkdir(parents=True, exist_ok=True)
            buf = io.BytesIO(); img.save(buf, "PNG")
            (TEX_DIR / f"{stem}.png").write_bytes(buf.getvalue())
            ok = True
        except Exception:
            ok = False
    made[stem] = ok
    return ok


def _part_textures(arch: Archive, part: dict, made: dict):
    """Resolve + extract a part's frame textures; return list of PNG stems."""
    stems = []
    t1 = part.get("texName1")
    fc = max(1, int(part.get("frameCount") or 1))
    if t1:
        for frame in range(fc):
            r = arch.resolve_tex(t1, frame)
            if r is None:
                break
            key, stem = r
            if _extract_texture(arch, key, stem, made):
                stems.append(stem)
    return stems


def convert_one(arch: Archive, fxb_name: str, made: dict, made_shapes: dict):
    blob = arch.read(fxb_name)
    if blob is None:
        return None
    eff = parse_fxb(blob)
    out_parts = []
    for p in eff["parts"]:
        t = p["type"]
        stems = _part_textures(arch, p, made)
        op = {
            "type": t,
            "blend": _blend(p.get("srcBlend", 5), p.get("destBlend", 2)),
            # Preserve the authored D3D factors. "add" alone loses the distinction
            # between ONE+ONE (direct RGB addition) and SRC_ALPHA+ONE.
            "srcBlend": int(p.get("srcBlend", 5)),
            "destBlend": int(p.get("destBlend", 2)),
            # Per-part render state, read straight off the .fxb (the part loader derives
            # ZEnable/ZWrite/cull/lighting from this single dword). It was being parsed and then
            # DROPPED, so the runtime hardcoded one combination for every part: no depth write, no
            # backface cull. That is wrong for the 673 parts that author a depth write and the 296
            # that author no depth test at all. RF_ALPHABLENDING 0x1, RF_NOTUSEFOG 0x2,
            # RF_DOUBLESIDED 0x4, RF_BOARD_Y 0x8, RF_NOTUSELIGHT 0x40, RF_NOTZWRITE 0x100,
            # RF_NOTZBUFFER 0x400.
            "renderFlags": int(p.get("renderFlags", 0)),
            "tex": stems,
            "frameCount": int(p.get("frameCount") or 1),
            # animation/movement (the format notes): texFPS flipbook, fade, part life, start delay,
            # and per-part travel pos(t)=initPos + initVel*t + 0.5*accel*t^2 (accel = vecB/gravity).
            "texFPS": round(p.get("texFPS", 0.0), 4),
            "life": round(p.get("life", 0.0), 4),
            "fadeIn": round(p.get("fadeIn", 0.0), 4),
            "fadeOut": round(p.get("fadeOut", 0.0), 4),
            # show/hide duty cycle (@112/@116). Only meaningful when both are > 0 — retail's Save
            # zeroes the pair otherwise, and the tick gates each half on "> 0".
            "hideTime": round(float(p.get("hideTime", 0.0) or 0.0), 4),
            "showTime": round(float(p.get("showTime", 0.0) or 0.0), 4),
            "startTime": _start_time(p.get("fStartTime", 0.0)),
            "initVel": _negx(p.get("vecA")),
            "initPos": _negx(p.get("vecD")),
            "accel": _negx(p.get("vecB")),
            # base.rotationVelocity (vecC) — the REAL per-part spin rate (rad/s). Billboard/Ground spin
            # about Y; Mesh uses the full Euler. (Was a hardcoded 50 deg/s constant in the runtime.)
            "rotVel": _negx(p.get("vecC")),
        }
        cols, rows = p["atlasCols"], p["atlasRows"]
        if cols * rows > 1:
            op["atlas"] = [cols, rows]
            op["atlasFrames"] = p["atlasFrames"]
        if t == "Particles":
            # Spawn volume: each particle's local spawn point is a per-axis random lerp between
            # createRangeMin and createRangeMax, i.e. an axis-aligned BOX around the emitter.
            # KO authors the two corners in either order (min > max is common), so normalise to
            # centre + half-extent after the X negation.
            lo = p.get("createRangeMin") or (0.0, 0.0, 0.0)
            hi = p.get("createRangeMax") or (0.0, 0.0, 0.0)
            lo = (-lo[0], lo[1], lo[2])
            hi = (-hi[0], hi[1], hi[2])
            dx, dy, dz = p.get("emitDir") or (0.0, 0.0, 1.0)
            gather = p.get("gatherPoint")
            # Both pairs are random RANGES (retail lerps first->second by rand()%100/100), so the
            # authored order carries no meaning — sort them so the runtime gets min <= max.
            size_lo, size_hi = sorted((float(p.get("sizeMin", 1.0)), float(p.get("sizeMax", 1.0))))
            life_lo, life_hi = sorted((float(p.get("lifeMin", 1.0)), float(p.get("lifeMax", 1.0))))
            op.update({
                "numParticles": int(p.get("numParticles") or 16),
                "numCreate": int(p.get("numCreate") or 1),   # spawned per burst
                "sizeMin": size_lo, "sizeMax": size_hi,
                "lifeMin": life_lo, "lifeMax": life_hi,
                "emitInterval": p.get("emitInterval", 0.0),
                "emitType": int(p.get("emitType", 0)),       # 0 normal, 1 spread, 2 gather
                # KO draws the launch angle as rand(0..emitAngle) - emitAngle*0.5 about the emit
                # direction (the shared constant is 0.5), so the authored
                # value is a FULL cone angle while Godot's Spread is the half-angle in degrees.
                "spread": round(min(180.0, max(0.0, (p.get("spreadAngle") or 0.0) * 0.5)), 4),
                "gatherPoint": [-gather[0], gather[1], gather[2]] if gather else None,
                "emitBoxCentre": [round((a + b) * 0.5, 5) for a, b in zip(lo, hi)],
                "emitBoxExtent": [round(abs(b - a) * 0.5, 5) for a, b in zip(lo, hi)],
                "emitDir": [-dx, dy, dz],                    # a DIRECTION, not a position
                "speed": p.get("speed", 0.0),
                # The original client integrates dropVel += gravity*dt, dropY += dropVel*dt and
                # then SUBTRACTS dropY from the world Y, so the world-Y acceleration is the
                # NEGATED authored scalar: a negative gravity makes particles RISE. The old "gravity" here was base vecB, which
                # is already emitted as "accel" for emitter travel — it was applied twice.
                "gravity": [0.0, -float(p.get("ptGravity", 0.0)), 0.0],
                # ptAccel is DEAD: nothing outside load/save reads it and the per-particle accel
                # vector is never written when a particle is created, so "vel += accel*dt" is
                # always a no-op.
                # Kept in the descriptor for provenance only — the runtime must not apply it.
                "ptAccel": p.get("ptAccel", 0.0),
                "ptRotVel": p.get("ptRotVel", 0.0),
                "sizeOffset": p.get("sizeOffset", 0.0),
                # The particle SPRITE's own animation, the exact counterpart of the BillBoard part's
                # sizeVel/rot block. verA>=5 authors @425/@427/@428 and the particle tick
                # consumes them per particle, per frame:
                #   quad X extent = max(0, size + age*@427)
                #   quad Y extent = max(0, size + age*@428)
                #   roll          = Z rotation by age*@425 radians   (cos/sin into
                #                   an identity basis applied to the corner offsets)
                # These were parsed as `skip(12)` and thrown away, so every emitter in the game drew
                # fixed-size dots. 1860 of 2093 particle parts author a Y growth: the ranking-board
                # sparkles grow 0.3 -> 10.3 units TALL over their 1 s life, which is the "curtain of
                # light" retail draws over the plaque. Roll is negated with the X axis for the same
                # reason base rotVel is (see "rotVel" above): the world is mirrored in X, so a
                # screen-plane roll reverses.
                "sizeVel": [round(float(p.get("sizeVelX", 0.0) or 0.0), 4),
                            round(float(p.get("sizeVelY", 0.0) or 0.0), 4)],
                "rollRate": round(-float(p.get("rollRate", 0.0) or 0.0), 5),
                # Orientation mode. Retail picks one of four bases for the quad each tick:
                # the part's authored @256 matrix by default, a yaw-only camera facing when @1145
                # is set, a view-axis roll when @825 is set, and a fixed euler from @429..431 when
                # @1146 is set. Baked for provenance; the runtime still camera-billboards every
                # particle (see the format notes open items).
                "velAlign": bool(p.get("velAlign", 0)),
                "rotAbs": bool(p.get("rotAbs", 0)),
                "rot": [round(float(v), 4) for v in (p.get("rot") or (0.0, 0.0, 0.0))],
                "colorLUT": [_argb_to_rgba(c) for c in (p.get("colorLUT") or [])] or None,
                "shapeName": p.get("shapeName"),
            })
            # A Particles part's shapeName is an EMITTER SHAPE, not decoration. Retail offsets the
            # spawn point by the shape's animated bounding-segment CENTRE:
            #   when a shape is attached, spawnPos = partPos + shapeEmitPos,
            #   where shapeEmitPos is lerp(A, B, 0.5) —
            #   where A/B are the two extreme points transformed by the shape's animated matrix in
            #   the emit helper also sets radius = |B-A| * 0.5 at +348.
            # So bake the shape (it already carries the pos/rot/scale key tracks) plus the local AABB
            # centre of its geometry. moraranker_fx_emiter_01 is a 3-vertex triangle 1.22u off-axis at
            # y=2.93 with 90 rotation keys = a full 360 deg in 3 s, i.e. the sparkles ORBIT the plaque.
            if p.get("shapeName"):
                ref = _bake_shape(arch, p["shapeName"], made, made_shapes)
                op["emitterRef"] = ref
                op["emitterFps"] = float(p.get("shapeFps", 30.0))
                op["emitCentre"] = _shape_centre(ref) if ref else None
        elif t == "BillBoard":
            op.update({
                "sizeW": round(p.get("sizeW", 1.0), 4), "sizeH": round(p.get("sizeH", 1.0), 4),
                "num": int(p.get("overdraw") or 1),
                "sizeVel": [round(p.get("sizeVelX", 0.0), 4), round(p.get("sizeVelY", 0.0), 4)],
                "sizeAccel": [round(p.get("sizeAccelX", 0.0), 4), round(p.get("sizeAccelY", 0.0), 4)],
                "autoSpin": bool(p.get("autoSpin", 0)),
                # explicit-rotation gate is rotAbs@1097 (rotEnable@1096 is 0 in practice; angles are
                # only meaningful when rotAbs=1 — verified: non-rotAbs parts carry garbage in rotX).
                "rotEnable": bool(p.get("rotAbs", 0)),
                "rot": [p.get("rotX", 0.0), p.get("rotY", 0.0), p.get("rotZ", 0.0)] if p.get("rotAbs") else [0.0, 0.0, 0.0],
                "frameWrap": bool(p.get("frameWrap", 0)),
                "matrix": p.get("matrix"),
            })
        elif t == "BottomBoard":
            op.update({
                "mirroredQuarterUv": bool(p.get("mirroredQuarterUv", 0) and not p.get("headerUv", 0)),
                "sizeW": round(p.get("sizeW", 1.0), 4), "sizeH": round(p.get("sizeH", 1.0), 4),
                "num": 1,
                "sizeVel": [round(p.get("sizeVelX", 0.0), 4), round(p.get("sizeVelY", 0.0), 4)],
                "sizeAccel": [0.0, 0.0],
                # The ground-board part has no autoSpin field in the format at all — this used to be a
                # heuristic standing in for the real rotation, which the runtime now takes straight
                # from rotVel.Y (retail yaws the ground quad by rotVel.Y * elapsed).
                "autoSpin": False,
                "rotEnable": False, "rot": [0.0, 0.0, 0.0],
                "frameWrap": bool(p.get("frameWrap", 0)), "matrix": None, "flat": True,
                "gap": round(float(p.get("gap", 0.05)), 4),
            })
        elif t == "Mesh":
            # Bake the .n3shape geometry (deduped) so the runtime can render it (the largest part type).
            op["meshRef"] = _bake_shape(arch, p.get("meshName") or "", made, made_shapes) if p.get("meshName") else None
            op["mesh"] = _strip_fx(p.get("meshName") or "") or None   # kept for reference/diagnostics
            op["meshFps"] = round(float(p.get("meshFps", 30.0)), 4)
            op["textureLoop"] = bool(p.get("textureLoop", 0))
            op["shapeLoop"] = bool(p.get("shapeLoop", 0))
            op["viewFix"] = bool(p.get("viewFix", 0))
            op["textureMoveDirection"] = int(p.get("textureMoveDirection", 0) or 0)
            op["textureMove"] = [round(float(p.get("textureMoveU", 0.0)), 4),
                                 round(float(p.get("textureMoveV", 0.0)), 4)]
            op["unitScale"] = _scale3(p.get("unitScale"), 1.0)
            op["scaleVel"] = _scale3(p.get("scaleVelocity"), 0.0)
            op["scaleAccel"] = _scale3(p.get("scaleAcceleration"), 0.0)
        out_parts.append(op)
    name = os.path.splitext(fxb_name)[0]
    # scaleMode = the bundle's @352 uniform-scale byte. 0 -> parts use the 2-component scale
    # @328/@332 (both 1.0 unless something writes them); 1 -> parts multiply their drawn SIZE by the
    # uniform scale @356, which only the equipment glow setter ever writes.
    # velocity = the bundle's @120 field, its FLIGHT SPEED in units/sec. Each tick the client
    # moves a flying bundle by dir * dt * velocity, so a projectile effect carries its own
    # authored speed: 50 for both arrow_new_*_1st, 0 for every static bundle.
    return {"name": name, "version": eff["version"], "scaleMode": int(eff.get("scaleMode", 0)),
            "life": eff["life"], "velocity": round(float(eff.get("velocity", 0.0)), 4),
            "centreY": _bundle_centre_y(out_parts),
            "parts": out_parts}


def main():
    ap = argparse.ArgumentParser(description="Convert KO .fxb -> Godot fx JSON + PNG.")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--name", nargs="*", default=[])
    ap.add_argument("--list", type=int, default=0)
    args = ap.parse_args()

    arch = Archive(KO_FX_HDR, KO_FX_SRC)
    fxbs = sorted(n for n in arch.idx if n.lower().endswith(".fxb"))
    if args.list:
        for n in fxbs[:args.list]:
            print(os.path.splitext(n)[0])
        return 0

    if args.all:
        targets = fxbs
    else:
        want = {n.lower() for n in args.name}
        targets = [n for n in fxbs if os.path.splitext(n)[0].lower() in want
                   or os.path.basename(n).lower() in want]
        if not targets:
            print("no matching effects; use --list to see names", file=sys.stderr); return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = {}
    made_tex = {}
    made_shapes = {}
    ok = skipped = 0
    for n in targets:
        try:
            eff = convert_one(arch, n, made_tex, made_shapes)
        except Exception as e:
            print(f"[fx] SKIP {n}: {e}", file=sys.stderr); skipped += 1; continue
        if eff is None or not eff["parts"]:
            skipped += 1; continue
        (OUT_DIR / f"{eff['name']}.json").write_text(json.dumps(eff, separators=(",", ":")))
        index[eff["name"]] = {"parts": len(eff["parts"]), "life": eff["life"],
                              "velocity": eff.get("velocity", 0.0)}
        ok += 1
    (OUT_DIR / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    ntex = sum(1 for v in made_tex.values() if v)
    nshape = sum(1 for v in made_shapes.values() if v)
    print(f"[fx] {ok} effects -> {OUT_DIR}  ({ntex} textures, {nshape} meshes, {len(index)} indexed, {skipped} skipped)")
    warn_unimported()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
