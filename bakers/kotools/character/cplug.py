"""N3CPlug — Character plugin (weapon/shield attachment).

Binary layout:
  [N3 name string]
  [int32]  plugType (0=NORMAL, 1=CLOAK)
  [int32]  jointIndex
  [float3] position
  [float4x4] rotMatrix (64 bytes)
  [float3] scale
  [Material] material (92 bytes)
  [path]   meshPath (.n3pmesh)
  [path]   texturePath (.dxt)
  if plugType == NORMAL:
    [int32]  traceStep
    if traceStep > 0:
      [uint32] traceColor (D3DCOLOR ARGB)
      [float]  trace0
      [float]  trace1
    [int32]  useVMesh (0 or 1; missing at the end of some files, read as 0)
    if useVMesh:
      [N3PMesh] embedded guide mesh
    [PlugTail]

PlugTail (shared with .n3chrplug):
  [int32]  version (missing = 0)
  if version >= 1:
    [int32]  reserved (always 0)
    5 x [int32 len][len bytes]  trace texture names (normal, fire, ice, lightning, poison)
  if version >= 2:
    8 x PlugBlock  race blocks: default, race 1, 2, 3, 4/5/13, 11, 12, 6/14
  if version >= 3:
    [uint8]  v3Flag
    [int32]  lookCount
    lookCount x PlugBlock  transformation-look blocks

PlugBlock (92 bytes): [int32 jointIndex][float3 position][float4x4 rotMatrix][float3 scale]
"""

from dataclasses import dataclass, field
from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..formats.material import Material
from ..mesh.pmesh import N3PMesh


PLUGTYPE_NORMAL = 0
PLUGTYPE_CLOAK = 1

TRACE_TEXTURE_SLOTS = 5
RACE_BLOCKS = 8
MAX_TRACE_TEXTURE_NAME = 0x103
VERSION_TRACE_TEXTURES = 1
VERSION_RACE_BLOCKS = 2
VERSION_LOOK_BLOCKS = 3


def _identity() -> list[list[float]]:
    return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


@dataclass
class PlugBlock:
    """One per-race (or per-look) placement override."""
    joint_index: int = 0
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rot_matrix: list[list[float]] = field(default_factory=_identity)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)

    @classmethod
    def read(cls, reader: BinaryReader) -> "PlugBlock":
        joint_index = reader.read_int32()
        position = reader.read_float3()
        rot_matrix = reader.read_matrix4x4()
        scale = reader.read_float3()
        return cls(joint_index=joint_index, position=position, rot_matrix=rot_matrix, scale=scale)

    def write(self, w: BinaryWriter):
        w.write_int32(self.joint_index)
        w.write_float3(*self.position)
        w.write_matrix4x4(self.rot_matrix)
        w.write_float3(*self.scale)


@dataclass
class PlugTail:
    """The versioned tail after the guide mesh (and after the chr path of a .n3chrplug)."""
    version: int = 0
    reserved: int = 0
    trace_textures: list[str] = field(default_factory=list)
    race_blocks: list[PlugBlock] = field(default_factory=list)
    v3_flag: int = 0
    look_blocks: list[PlugBlock] = field(default_factory=list)

    @classmethod
    def read(cls, reader: BinaryReader) -> "PlugTail":
        tail = cls()
        if reader.remaining() < 4:
            return tail
        tail.version = reader.read_int32()
        if tail.version >= VERSION_TRACE_TEXTURES:
            tail.reserved = reader.read_int32()
            for _ in range(TRACE_TEXTURE_SLOTS):
                n = reader.read_int32()
                name = b""
                if 1 <= n <= MAX_TRACE_TEXTURE_NAME:
                    name = reader.read_bytes(n)
                tail.trace_textures.append(name.decode("latin-1"))
        if tail.version >= VERSION_RACE_BLOCKS:
            tail.race_blocks = [PlugBlock.read(reader) for _ in range(RACE_BLOCKS)]
        if tail.version >= VERSION_LOOK_BLOCKS:
            tail.v3_flag = reader.read_uint8()
            count = reader.read_int32()
            tail.look_blocks = [PlugBlock.read(reader) for _ in range(max(0, count))]
        return tail

    def write(self, w: BinaryWriter):
        if self.version <= 0:
            return
        w.write_int32(self.version)
        if self.version >= VERSION_TRACE_TEXTURES:
            w.write_int32(self.reserved)
            names = (self.trace_textures + [""] * TRACE_TEXTURE_SLOTS)[:TRACE_TEXTURE_SLOTS]
            for name in names:
                data = name.encode("latin-1")
                w.write_int32(len(data))
                w.write_bytes(data)
        if self.version >= VERSION_RACE_BLOCKS:
            blocks = (self.race_blocks + [PlugBlock()] * RACE_BLOCKS)[:RACE_BLOCKS]
            for block in blocks:
                block.write(w)
        if self.version >= VERSION_LOOK_BLOCKS:
            w.write_uint8(self.v3_flag)
            w.write_int32(len(self.look_blocks))
            for block in self.look_blocks:
                block.write(w)


def read_embedded_mesh(reader: BinaryReader) -> bytes:
    """Read the useVMesh flag and, when set, return the embedded N3PMesh bytes exactly."""
    if reader.remaining() < 4 or reader.read_int32() == 0:
        return b""
    start = reader.tell()
    N3PMesh.from_reader(reader)
    end = reader.tell()
    reader.seek(start)
    return reader.read_bytes(end - start)


@dataclass
class N3CPlug:
    """Weapon/shield/attachment plugin."""
    name: str = ""
    plug_type: int = PLUGTYPE_NORMAL
    joint_index: int = 0
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rot_matrix: list[list[float]] = field(default_factory=_identity)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    material: Material = field(default_factory=Material)
    mesh_path: str = ""
    texture_path: str = ""
    trace_step: int = 0
    trace_color: int = 0
    trace0: float = 0.0
    trace1: float = 0.0
    embedded_mesh_data: bytes = b""
    tail: PlugTail = field(default_factory=PlugTail)

    @property
    def version(self) -> int:
        return self.tail.version

    @property
    def race_blocks(self) -> list[PlugBlock]:
        return self.tail.race_blocks

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3CPlug":
        name = reader.read_n3_name()
        plug_type = reader.read_int32()
        joint_index = reader.read_int32()
        position = reader.read_float3()
        rot_matrix = reader.read_matrix4x4()
        scale = reader.read_float3()
        material = Material.read(reader)
        mesh_path = reader.read_n3_path()
        texture_path = reader.read_n3_path()

        trace_step = 0
        trace_color = 0
        trace0 = trace1 = 0.0
        embedded_mesh_data = b""
        tail = PlugTail()

        if plug_type == PLUGTYPE_NORMAL and reader.remaining() >= 4:
            trace_step = reader.read_int32()
            if trace_step > 0 and reader.remaining() >= 12:
                trace_color = reader.read_dword()
                trace0 = reader.read_float()
                trace1 = reader.read_float()
            embedded_mesh_data = read_embedded_mesh(reader)
            tail = PlugTail.read(reader)

        return cls(name=name, plug_type=plug_type, joint_index=joint_index,
                   position=position, rot_matrix=rot_matrix, scale=scale,
                   material=material, mesh_path=mesh_path, texture_path=texture_path,
                   trace_step=trace_step, trace_color=trace_color,
                   trace0=trace0, trace1=trace1, embedded_mesh_data=embedded_mesh_data,
                   tail=tail)

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_n3_name(self.name)
        w.write_int32(self.plug_type)
        w.write_int32(self.joint_index)
        w.write_float3(*self.position)
        w.write_matrix4x4(self.rot_matrix)
        w.write_float3(*self.scale)
        self.material.write(w)
        w.write_n3_path(self.mesh_path)
        w.write_n3_path(self.texture_path)
        if self.plug_type == PLUGTYPE_NORMAL:
            w.write_int32(self.trace_step)
            if self.trace_step > 0:
                w.write_dword(self.trace_color)
                w.write_float(self.trace0)
                w.write_float(self.trace1)
            w.write_int32(1 if self.embedded_mesh_data else 0)
            if self.embedded_mesh_data:
                w.write_bytes(self.embedded_mesh_data)
            self.tail.write(w)
        return w.getvalue()
