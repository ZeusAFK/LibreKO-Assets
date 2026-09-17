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
    [int32]  useVMesh (0 or 1)
    if useVMesh:
      [N3PMesh] embedded mesh
"""

from dataclasses import dataclass, field
from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..formats.material import Material


PLUGTYPE_NORMAL = 0
PLUGTYPE_CLOAK = 1


@dataclass
class N3CPlug:
    """Weapon/shield/attachment plugin."""
    name: str = ""
    plug_type: int = PLUGTYPE_NORMAL
    joint_index: int = 0
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rot_matrix: list[list[float]] = field(default_factory=lambda: [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]])
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    material: Material = field(default_factory=Material)
    mesh_path: str = ""
    texture_path: str = ""
    trace_step: int = 0
    trace_color: int = 0
    trace0: float = 0.0
    trace1: float = 0.0
    embedded_mesh_data: bytes = b""

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

        if plug_type == PLUGTYPE_NORMAL and reader.remaining() >= 4:
            trace_step = reader.read_int32()
            if trace_step > 0 and reader.remaining() >= 12:
                trace_color = reader.read_dword()
                trace0 = reader.read_float()
                trace1 = reader.read_float()
            if reader.remaining() >= 4:
                use_vmesh = reader.read_int32()
                if use_vmesh != 0 and reader.remaining() > 0:
                    embedded_mesh_data = reader.read_bytes(reader.remaining())

        return cls(name=name, plug_type=plug_type, joint_index=joint_index,
                   position=position, rot_matrix=rot_matrix, scale=scale,
                   material=material, mesh_path=mesh_path, texture_path=texture_path,
                   trace_step=trace_step, trace_color=trace_color,
                   trace0=trace0, trace1=trace1, embedded_mesh_data=embedded_mesh_data)

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
        return w.getvalue()
