"""Knight Online .opd (Object Post Data) reader.

Parses the per-object placement records in a zone's .opd file: for each placed
static object it yields the model/instance name, world position, rotation
(quaternion), scale, sub-part meshes, and the event/NPC metadata used for gates,
levers, bind points, warps, anvils, artifacts and NPCs.

Structures: object post data, shape-manager collision data and shape records.

Scope (IMPORTANT): the **OLD .opd format** is fully supported and verified by a
full shape-list parse to EOF (leftover == 0) on karus_start, elmorad_start and
eslantzone-style zones. The **NEW format** (`old_format == False`, e.g. moradon)
uses a different record/collision layout that is not yet decoded; read_opd raises
`OpdFormatError` for those rather than returning wrong data. See RE_REQUESTS RE-001.

Top-level layout
----------------
  [i32 nameLen]                      (== 1 sentinel => OLD format, real i32 follows)
  [nameLen bytes] map name           new: LCG-encrypted (§6.1); old: raw
  [u32 unknown]                      new format only (usually 0/1)
  --- collision ---
  [f32 mapWidth][f32 mapLength]
  [i32 collisionFaceCount]
  [vec3 * faceCount*3]               collision triangle vertices
  cell grid (encoding varies by build)
  --- shape list ---
  [i32 shapeCount]
  shapeCount * ( [u32 dwType] __ShapeEx )

The cell-grid encoding varies across builds, so rather than parsing it exactly we
take the end of the collision vertex array as a safe lower bound and locate the
real [i32 shapeCount] by scanning forward for the offset whose shape list parses
cleanly to end-of-file (the full parse is the validator).
"""
from __future__ import annotations

import struct
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


class OpdFormatError(NotImplementedError):
    """Raised when a .opd cannot be parsed with the known (old-format) layout."""


# --- name cipher (standard LCG XOR, handoff §6.1; same as gtd name) ----------
def _decrypt_name(enc: bytes) -> str:
    """LCG stream cipher for .opd/.gtd map-name strings.

    state init 0x0816; out = (state>>8) ^ byte; state = (byte+state)*0x6081 + 0x1608.
    """
    state = 0x0816
    out = bytearray()
    for b in enc:
        out.append(((state >> 8) & 0xFF) ^ b)
        state = ((b + state) * 0x6081 + 0x1608) & 0xFFFF
    nul = out.find(0)
    if nul >= 0:
        out = out[:nul]
    return out.decode("latin-1", errors="replace")


@dataclass
class OpdObject:
    """One placed object (a shape instance) from the .opd shape list."""
    name: str                                  # model/instance name
    ex_flag: bool                              # dwType & 0x1000 (OBJ_SHAPE_EXTRA)
    pos: tuple                                 # (x, y, z) world units
    rot: tuple                                 # quaternion (x, y, z, w)
    scale: tuple                               # (x, y, z)
    parts: list = field(default_factory=list)  # part mesh names
    # event metadata (extended shape tail); 0 unless this is an event object
    belong: int = 0          # nation: 0=all, 1=Karus, 2=ElMorad
    event_id: int = 0        # >0 => gate/lever/bind/warp/anvil/artifact/npc
    event_type: int = 0      # see _EVENT_TYPE_NAMES
    npc_id: int = 0
    npc_status: int = 0


@dataclass
class OpdData:
    map_name: str
    map_width: float
    map_length: float
    collision_face_count: int
    objects: list
    old_format: bool
    shape_list_offset: int   # byte offset of the [i32 shapeCount] field


OBJ_SHAPE_EXTRA = 0x1000

_EVENT_TYPE_NAMES = {
    0: "bind", 1: "gate", 2: "gate2", 3: "gate_lever", 4: "flag",
    5: "warp_gate", 6: "barricade", 7: "remove_bind", 8: "anvil",
    9: "artifact", 11: "npc",
}


# ---------------------------------------------------------------------------
# Low-level cursor
# ---------------------------------------------------------------------------
class _Reader:
    __slots__ = ("d", "o")

    def __init__(self, data, offset=0):
        self.d = data
        self.o = offset

    def _need(self, n):
        if self.o + n > len(self.d):
            raise EOFError(f"opd: need {n} bytes at offset {self.o}, "
                           f"have {len(self.d) - self.o}")

    def u32(self):
        self._need(4); v = struct.unpack_from("<I", self.d, self.o)[0]; self.o += 4; return v

    def i32(self):
        self._need(4); v = struct.unpack_from("<i", self.d, self.o)[0]; self.o += 4; return v

    def f32(self):
        self._need(4); v = struct.unpack_from("<f", self.d, self.o)[0]; self.o += 4; return v

    def vec3(self):
        self._need(12); v = struct.unpack_from("<3f", self.d, self.o); self.o += 12; return v

    def quat(self):
        self._need(16); v = struct.unpack_from("<4f", self.d, self.o); self.o += 16; return v

    def raw(self, n):
        self._need(n); v = self.d[self.o:self.o + n]; self.o += n; return v

    def skip(self, n):
        self._need(n); self.o += n


def _read_name_string(r):
    """ReadDecryptString: returns (decoded_name, old_format)."""
    count = r.i32()
    old = False
    if count == 1:
        old = True
        count = r.i32()
    raw = r.raw(count)
    if old:
        nul = raw.find(0)
        return (raw[:nul if nul >= 0 else len(raw)].decode("latin-1", "replace"), True)
    return (_decrypt_name(raw), False)


def _read_ascii(r):
    n = r.u32()
    if n == 0:
        return ""
    b = r.raw(n)
    nul = b.find(0)
    return b[:nul if nul >= 0 else len(b)].decode("latin-1", "replace")


# ---------------------------------------------------------------------------
# Shape record — OLD format
# ---------------------------------------------------------------------------
def _read_shape(r, ex_flag):
    name = _read_ascii(r)
    pos = r.vec3()
    rot = r.quat()
    scale = r.vec3()

    # 3 animation-key channels (pos, rot, scale).
    # Per channel: [u32 count]; if count>0: [i32 keyType][f32 samplingRate] then
    # `count` keys (keyType 1 = quaternion 16 B, else vec3 12 B). Matches the
    # the N3 reader in this package; verified by elmorad_start
    # (which has animated shapes) parsing exactly to EOF.
    for _ in range(3):
        n = r.u32()
        if n > 0:
            key_type = r.i32()
            r.f32()                               # sampling rate
            r.skip(n * (16 if key_type == 1 else 12))

    _read_ascii(r)                                # collision mesh name 1
    _read_ascii(r)                                # collision mesh name 2

    parts = []
    part_count = r.i32()
    for _ in range(part_count):
        r.skip(12)                                # __Vector3 pivot
        part_name = _read_ascii(r)
        r.skip(92)                                # __Material: _D3DMATERIAL8(68) + 6*u32(24)
        tex_count = r.u32()
        r.f32()                                   # texture FPS
        for _ in range(tex_count):
            _read_ascii(r)                        # texture name (len-prefixed)
        parts.append(part_name)

    belong = r.i32()
    event_id = r.i32()
    event_type = r.i32()
    npc_id = r.i32()
    npc_status = r.i32()

    return OpdObject(
        name=name, ex_flag=ex_flag, pos=pos, rot=rot, scale=scale, parts=parts,
        belong=belong, event_id=event_id, event_type=event_type,
        npc_id=npc_id, npc_status=npc_status,
    )


# ---------------------------------------------------------------------------
# Shape list parse / validation
# ---------------------------------------------------------------------------
def _parse_shape_list(data, start, collect):
    """Parse the shape list whose [i32 shapeCount] is at byte offset `start`.

    Returns (objects_or_None, end_offset). Returns (None, start) on any
    structural error — so the same routine both collects objects and validates a
    candidate offset. dwType is unconstrained (the client ignores its value).
    """
    try:
        r = _Reader(data, start)
        cnt = r.i32()
        if not (1 <= cnt < 100000):
            return None, start
        objs = [] if collect else None
        for _ in range(cnt):
            dw = r.u32()                          # dwType (value unconstrained)
            obj = _read_shape(r, bool(dw & OBJ_SHAPE_EXTRA))
            if not obj.name:                      # cheap reject for a wrong offset
                return None, start
            if collect:
                objs.append(obj)
        return (objs if collect else True), r.o
    except EOFError:
        return None, start


# A correct shape-list offset parses ALL `shapeCount` records and ends exactly at
# (or within a few footer bytes of) EOF. We require a tight EOF margin so a wrong
# offset that happens to parse a handful of records can't be mistaken for the list.
_EOF_SLACK = 16
_SCAN_MIN_CNT = 100        # real zones have hundreds-to-thousands of objects


def _scan_for_shape_list(data, lower_bound):
    """Scan forward from `lower_bound` for the [i32 shapeCount] whose shape list
    parses to ~EOF. Return (objects, off) or None."""
    n = len(data)
    for off in range(max(0, lower_bound), n - 8):
        cnt = struct.unpack_from("<i", data, off)[0]
        if not (_SCAN_MIN_CNT <= cnt < 100000):
            continue
        objs, end = _parse_shape_list(data, off, collect=True)
        if objs is not None and (n - end) <= _EOF_SLACK:
            return objs, off
    return None


def read_opd(path):
    """Parse a .opd file into an OpdData (map metadata + placed objects).

    OLD format is fully supported. NEW format (moradon etc.) is NOT decoded: the
    name decrypts and we consume the trailing u32, but a byte-for-byte shape scan
    finds no old-style list — the NEW shape record differs and is unconfirmed.
    the format notes §5 marks the OPD layout [verify] and says NEW
    counts are RC4-gated (§2.3); the authoritative reader is the the add-on Blender
    plugin (to port). Until then we raise OpdFormatError for NEW rather than
    fabricate placements.
    """
    data = Path(path).read_bytes()
    r = _Reader(data)

    map_name, old = _read_name_string(r)
    if not old:
        raise OpdFormatError(
            f"{Path(path).name}: NEW .opd format (map_name={map_name!r}) not decoded "
            f"— shape record differs from OLD and is unconfirmed. Port the the add-on "
            f"Blender OPD reader; see the format notes §5 / §2.3."
        )

    map_w = r.f32()
    map_l = r.f32()
    face_count = r.i32()
    verts_end = r.o + (face_count * 3 * 12 if face_count > 0 else 0)

    found = _scan_for_shape_list(data, verts_end)
    if found is None:
        raise OpdFormatError(
            f"{Path(path).name}: could not locate the shape list "
            f"(map_name={map_name!r}, old_format={old})."
        )
    objects, shape_list_off = found

    return OpdData(
        map_name=map_name, map_width=map_w, map_length=map_l,
        collision_face_count=face_count, objects=objects, old_format=old,
        shape_list_offset=shape_list_off,
    )


def _summary(path):
    try:
        opd = read_opd(path)
    except OpdFormatError as e:
        print(f"OPD: {Path(path).name}")
        print(f"  UNSUPPORTED: {e}")
        return
    print(f"OPD: {Path(path).name}")
    print(f"  map name   : {opd.map_name!r}  (old_format={opd.old_format})")
    print(f"  extent     : {opd.map_width:g} x {opd.map_length:g}")
    print(f"  collision  : {opd.collision_face_count} faces")
    print(f"  shape list : offset {opd.shape_list_offset}")
    print(f"  objects    : {len(opd.objects)}")

    events = [o for o in opd.objects if o.event_id > 0]
    print(f"  event objs : {len(events)}")
    et = Counter(_EVENT_TYPE_NAMES.get(o.event_type, f"?{o.event_type}") for o in events)
    if et:
        print("    by type  : " + ", ".join(f"{k}={v}" for k, v in sorted(et.items())))

    models = Counter(o.name for o in opd.objects)
    print(f"  distinct models: {len(models)}")
    print("  first 8 objects:")
    for o in opd.objects[:8]:
        tag = (f"  EVENT id={o.event_id} type={_EVENT_TYPE_NAMES.get(o.event_type, o.event_type)}"
               if o.event_id > 0 else "")
        print(f"    {o.name!r:34} pos=({o.pos[0]:.1f},{o.pos[1]:.1f},{o.pos[2]:.1f}) "
              f"scale=({o.scale[0]:.2f},{o.scale[1]:.2f},{o.scale[2]:.2f}){tag}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python opd_reader.py <file.opd>")
        sys.exit(2)
    _summary(sys.argv[1])
