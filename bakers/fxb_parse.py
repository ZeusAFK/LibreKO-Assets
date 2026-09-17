"""Knight Online .fxb effect bundle parser.

Field tags (@NNN) name the fields consistently across the fx bakers.
Validated: 1272/1273 real .fxb in fx.hdr parse with perfect part-boundary alignment.

Usage:
    from fxb_parse import parse_fxb, extract_from_archive
    eff = parse_fxb(open("some.fxb","rb").read())
    # or pull straight out of the fx.hdr/fx.src archive:
    eff = parse_fxb(extract_from_archive(r"...\fx\fx.hdr", "20060222_mora_light_01.fxb"))

Returns a dict:
    { 'version':int, 'life':float, 'velocity':float, 'parts':[ {part...}, ... ] }
each part:
    { 'type':'Particles'|'BillBoard'|'Mesh'|'BottomBoard', 'verA':int, 'verB':int,
      'texName1':str, 'texName2':str|None, 'frameCount':int, 'srcBlend':int,'destBlend':int,
      'renderFlags':int, 'vecA/B/C/D':(x,y,z), plus type-specific fields:
      BillBoard: 'matrix'(16 floats or None);  Mesh: 'meshName','meshName2';
      Particles: 'numParticles','createRangeMin/Max','numCreate','emitType',
                 'spreadAngle'|'gatherPoint','emitDir','speed','ptAccel','ptRotVel',
                 'ptGravity','shapeName' }
Stdlib only.
"""
import struct

PART_NAMES = {1: "Particles", 2: "BillBoard", 3: "Mesh", 4: "BottomBoard"}


def _trailing_size(ver):
    return ((1 if ver >= 2 else 0) + (5 if ver >= 3 else 0) + (5 if ver >= 4 else 0)
            + (1 if ver >= 5 else 0) + (1 if ver >= 6 else 0))


class _R:
    def __init__(self, d): self.d = d; self.p = 0
    def rem(self): return len(self.d) - self.p
    def u8(self):  v = self.d[self.p]; self.p += 1; return v
    def u32(self): v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v
    def f32(self): v = struct.unpack_from("<f", self.d, self.p)[0]; self.p += 4; return v
    def vec3(self): v = struct.unpack_from("<3f", self.d, self.p); self.p += 12; return v
    def skip(self, n): self.p += n
    def floats(self, n): v = struct.unpack_from("<%df" % n, self.d, self.p); self.p += 4 * n; return list(v)
    def cstr(self, n=260):
        v = self.d[self.p:self.p + n].split(b"\0")[0].decode("latin-1", "replace"); self.p += n; return v


def _base(r, part):
    part["verA"] = r.u8()
    part["verB"] = b = r.u8()
    part["life"] = r.f32()                      # @108 life (sec)
    # A show/hide duty cycle ("blink"), not padding. the loader reads @112 then
    # @116 and the tick drives them off a second timer at @200:
    # once @116 seconds of visible time elapse the hidden flag @120 is set and the
    # timer restarts; after @112 seconds hidden it clears again. Both must be > 0 to mean
    # anything (Save zeroes the pair unless both are positive).
    part["hideTime"] = 0.0
    part["showTime"] = 0.0
    if b >= 3:
        part["hideTime"] = r.f32()              # @112 seconds spent hidden
        part["showTime"] = r.f32()              # @116 seconds spent visible
    r.u8()                                      # @104
    part["vecA"] = r.vec3(); part["vecB"] = r.vec3(); part["vecC"] = r.vec3()
    r.u8()                                      # @244
    part["vecD"] = r.vec3()
    fc = r.u32(); part["texFPS"] = r.f32()       # @776 frameCount, @780 texFPS
    if fc > 100_000_000: raise ValueError("bad frameCount %d" % fc)
    part["frameCount"] = fc
    part["texName1"] = r.cstr()
    part["srcBlend"] = r.u32(); part["destBlend"] = r.u32()
    # Read order is fadeOut THEN fadeIn. Semantics follow the alpha computation in the tick:
    # @788 is the window alpha ramps UP over from currLife=0, and @784 is the window it
    # ramps DOWN over at the end. These two were previously assigned the wrong way round.
    part["fadeOut"] = r.f32(); part["fadeIn"] = r.f32()   # @784 fadeOut, @788 fadeIn (sec)
    part["renderFlags"] = r.u32()
    part["texName2"] = r.cstr() if b >= 4 else None
    part["atlasCols"] = 1
    part["atlasRows"] = 1
    part["atlasFrames"] = 1
    return part["verA"]


def _billboard(r, part):
    a = _base(r, part)                          # also exposes life/fadeIn/fadeOut/texFPS
    part["overdraw"] = r.u32()                     # @920 overdraw count (multiply, NOT scatter)
    part["sizeW"] = r.f32()                      # @924 base quad width (full extent)
    part["sizeH"] = r.f32()                      # @928 base quad height
    part["frameWrap"] = r.u8()                   # @932 loop texture frames
    part["thr944"] = r.f32()                     # @944 (minor)
    if a >= 3: part["mode952"] = r.u8()           # @952 orientation mode
    if a >= 4:
        part["sizeVelX"] = r.f32(); part["sizeVelY"] = r.f32()      # @956/@960 size growth velocity
        part["sizeAccelX"] = r.f32(); part["sizeAccelY"] = r.f32()  # @964/@968 size growth accel
    part["matrix"] = r.floats(16) if a >= 5 else None                # @988 4x4 transform
    if a >= 6: part["rotEnable"] = r.u8()         # @1096 explicit-rotation enable
    if a >= 7: part["autoSpin"] = r.u8()          # @1068 continuous spin enable
    if a >= 8:
        part["rotAbs"] = r.u8()                   # @1097
        part["rotX"] = r.f32(); part["rotY"] = r.f32(); part["rotZ"] = r.f32()   # @1100/04/08 deg
    if a >= 9:
        part["atlasCols"] = r.u32()
        part["atlasRows"] = r.u32()
        part["atlasFrames"] = r.u32()


def _bottomboard(r, part):
    a = _base(r, part)
    part["sizeW"] = r.f32()                      # ground size X
    part["sizeH"] = r.f32()                      # ground size Z
    part["sizeVelX"] = r.f32()
    part["sizeVelY"] = r.f32()
    part["frameWrap"] = r.u8()                   # texture loop
    part["gap"] = 0.0
    part["mirroredQuarterUv"] = 0
    part["headerUv"] = 0
    if a >= 1: part["gap"] = r.f32()
    if a >= 2: part["mirroredQuarterUv"] = r.u8()
    if a >= 3: part["headerUv"] = r.u8()


def _mesh(r, part):
    a = _base(r, part)
    part["meshName"] = r.cstr()
    part["textureMoveDirection"] = r.u8()
    part["textureMoveU"] = r.f32()
    part["textureMoveV"] = r.f32()
    part["scaleVelocity"] = r.vec3()
    part["textureLoop"] = 0
    part["scaleAcceleration"] = (0.0, 0.0, 0.0)
    part["meshFps"] = 30.0
    part["unitScale"] = (1.0, 1.0, 1.0)
    part["shapeLoop"] = 0
    part["viewFix"] = 0
    part["useFadeShowLife"] = 0
    if a >= 2: part["textureLoop"] = r.u8()
    if a >= 3: part["scaleAcceleration"] = r.vec3()
    if a >= 4: part["meshFps"] = r.f32()
    if a >= 5: part["unitScale"] = r.vec3()
    if a >= 6: part["shapeLoop"] = r.u8()
    if a >= 7: part["viewFix"] = r.u8()
    if a >= 8: part["useFadeShowLife"] = r.u8()
    part["meshName2"] = r.cstr() if a >= 9 else None


def _particles(r, part):
    a = _base(r, part)
    if a < 3: raise ValueError("particles requires verA>=3")
    part["gravity"] = part["vecB"]               # accel/gravity = base vecB (@160)
    part["numParticles"] = r.u32()               # @273 pool size
    if a >= 4:
        part["sizeMin"] = r.f32(); part["sizeMax"] = r.f32()   # @275,@276
    else:
        s = r.f32(); part["sizeMin"] = part["sizeMax"] = s
    part["lifeMin"] = r.f32(); part["lifeMax"] = r.f32()        # @277,@278 (seconds)
    # @287 createRangeMin / @290 createRangeMax: the box (LOCAL to the part origin) each
    # particle's spawn point is drawn from. The loader reads both unconditionally.
    # These were previously read-and-discarded, which collapsed every
    # emitter in the game to a single point.
    part["createRangeMin"] = list(r.vec3())       # @287
    part["createRangeMax"] = list(r.vec3())       # @290
    part["emitInterval"] = r.f32()                # @283 (sec per burst)
    part["numCreate"] = r.u32()                   # @285 (particles spawned per burst)
    # @299 emit type: 0=NORMAL, 1=SPREAD (+@300 angle), 2=GATHER (+@300..@302 target point).
    # This is the emit STYLE, not a spawn shape — the spawn shape is createRangeMin/Max above.
    # (The old name "emitShape" with "volExtents" for type 2 mis-read the gather point,
    # a convergence target, as a box half-extent.)
    part["emitType"] = m = r.u32()                # @299
    part["spreadAngle"] = None
    part["gatherPoint"] = None
    if m == 1:   part["spreadAngle"] = r.f32()     # @300 emit angle (degrees)
    elif m == 2: part["gatherPoint"] = [r.f32(), r.f32(), r.f32()]  # @300..@302 vGatherPoint
    part["emitDir"] = list(r.vec3())              # @313 a DIRECTION, not a position
    part["speed"] = r.f32()                       # @316
    part["ptAccel"] = r.f32()                     # @317 (along the emit direction)
    part["ptRotVel"] = r.f32()                    # @318
    part["ptGravity"] = r.f32()                   # @319 (scalar, pulls -Y)
    part["colorLUT"] = None                        # @320[100] D3DCOLOR (color-over-life)
    if r.u8():                                    # @420 colour-over-life flag
        c = r.u32(); part["colorLUT"] = [r.u32() for _ in range(c)]
    # @424: the particle draws a shape mesh instead of a quad. Retail reads the mesh FPS then a
    # FIXED MAX_PATH (260 byte) shape file name buffer.
    # Byte consumption is unchanged from the old "subFx" reading; only the meaning was wrong.
    part["shapeName"] = None
    part["shapeFps"] = 0.0
    if r.u8():
        part["shapeFps"] = r.f32()                # @423 mesh FPS
        part["shapeName"] = r.cstr()              # MAX_PATH name buffer
    # The particle sprite's own animation block — the exact counterpart of the BillBoard part's
    # @956/@960 sizeVel + @1097 rotAbs + @1100..1108 rot, and it was being SKIPPED. Retail
    # reads @425, @427, @428 (note: NOT 426, which is the a>=10 size offset) and the particle
    # tick consumes them as:
    #   @425  roll:  angle = age * @425, applied as a Z rotation of the quad
    #                (cos/sin of it are built into an identity basis)
    #   @427  quad X extent = max(0, size + age * @427)
    #   @428  quad Y extent = max(0, size + age * @428)
    # so a particle GROWS over its life exactly like a billboard does. Dropping @427/@428 left
    # every emitter drawing fixed-size dots instead of the streaks retail draws.
    part["rollRate"] = 0.0
    part["sizeVelX"] = 0.0
    part["sizeVelY"] = 0.0
    if a >= 5:
        part["rollRate"] = r.f32()                # @425 roll rate (rad/s)
        part["sizeVelX"] = r.f32()                # @427 quad width growth  (units/sec)
        part["sizeVelY"] = r.f32()                # @428 quad height growth (units/sec)
    part["p286"] = 0
    if a >= 6: part["p286"] = r.u8()              # @286
    # @1145/@1146 are BYTE flags, and both pick an orientation mode in the tick: @1145 chooses
    # the velocity-aligned path and @1146 replaces the camera basis with the fixed
    # euler below — i.e. a world-oriented quad instead of a billboard.
    part["velAlign"] = 0
    part["rotAbs"] = 0
    part["rot"] = (0.0, 0.0, 0.0)
    if a >= 7: part["velAlign"] = r.u8()          # @1145
    if a >= 8:
        part["rotAbs"] = r.u8()                   # @1146
        part["rot"] = r.vec3()                    # @429..431 fixed rotation (degrees)
    if a >= 9: r.skip(8)                          # @432/@433
    part["sizeOffset"] = 0.0
    if a >= 10:
        part["sizeOffset"] = r.f32(); r.u8()       # @426 size offset, @312
    if a >= 11:
        part["atlasCols"] = r.u32()
        part["atlasRows"] = r.u32()
        part["atlasFrames"] = r.u32()


_PARSERS = {1: _particles, 2: _billboard, 3: _mesh, 4: _bottomboard}


def parse_fxb(data):
    r = _R(data)
    # The bundle header is FOUR fields before the part-slot array:
    #   u32  @88   version
    #   f32  @128  life          (0 = never expires)
    #   f32  @120  velocity      (bundle flight speed, units/sec)
    #   u8   @352  uniformScale
    # i.e. a 13-byte header. This used to skip only 9 and the slot loop silently swallowed the
    # 4-byte remainder as a "tag 0 = empty slot".
    # @120 is the flight velocity: each tick the client moves a flying bundle by dir * dt * velocity.
    # It is 0 for every STATIC bundle (which is why it looked unused) and non-zero exactly on the
    # projectiles: 50 for arrow_new_karus_1st / arrow_new_elmo_1st, 77 for the held arrow.
    ver = r.u32()
    life = r.f32()
    velocity = r.f32()                          # @120 velocity
    # @352 uniformScale. The header is exactly [@88 ver][@128 life][@120][@352(1)]
    # before the part-slot array. Every consumer of the bundle scale gates on this byte:
    # the billboard quad, the particle birth size,
    # the mesh path and the trail path — flag set -> use the UNIFORM scale @356, else the 2-component
    # scale @328/@332. 179 of 1273 retail bundles set it. @356 defaults to 1.0 and is only ever
    # written by the glow setter, so in practice the flag only bites on the weapon
    # enchant tails (fire/ice/poison _sword_tail0_1 all set it; lighting's does not).
    part_scale_mode = r.u8()
    ts = _trailing_size(ver)
    maxslots = 8 if ver == 0 else 26
    parts = []
    for _ in range(maxslots):
        if r.rem() == ts or r.rem() < 4:         # robust end-of-slot-array detection
            break
        tag = r.u32()
        if tag in _PARSERS:
            extra = r.u32()                      # per-slot fStartTime (float bits) — the format notes §5.1
            part = {"type": PART_NAMES[tag]}
            _PARSERS[tag](r, part)
            part["fStartTime"] = struct.unpack("<f", struct.pack("<I", extra))[0]
            parts.append(part)
        # tag == 0 (empty) or unknown -> just advance to next slot
    return {"version": ver, "life": round(life, 4), "velocity": round(velocity, 4),
            "scaleMode": part_scale_mode, "parts": parts}


# ---- optional: pull a .fxb straight out of the fx.hdr/fx.src archive ----
def _hdr_index(hdr_path):
    d = open(hdr_path, "rb").read(); off = 8; e = {}
    while off + 2 <= len(d):
        nl = struct.unpack_from("<H", d, off)[0]; off += 2
        if nl == 0 or off + nl + 8 > len(d): break
        name = d[off:off + nl].decode("latin-1"); off += nl
        fo, fs = struct.unpack_from("<II", d, off); off += 8
        e[name] = (fo, fs)
    return e


def extract_from_archive(hdr_path, entry_name):
    import os
    fo, fs = _hdr_index(hdr_path)[entry_name]
    src = os.path.splitext(hdr_path)[0] + ".src"
    with open(src, "rb") as f:
        f.seek(fo); return f.read(fs)


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) == 3:          # fxb_parse.py <fx.hdr> <entry.fxb>
        blob = extract_from_archive(sys.argv[1], sys.argv[2])
    else:                            # fxb_parse.py <file.fxb>
        blob = open(sys.argv[1], "rb").read()
    print(json.dumps(parse_fxb(blob), indent=2))
