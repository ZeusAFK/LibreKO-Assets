"""`.n3chrplug`, used instead of `.n3cplug` when Item_Org column 39 is set."""

from dataclasses import dataclass, field
from ..io.binary_reader import BinaryReader
from ..formats.material import Material
from .cplug import PlugBlock, PlugTail, read_embedded_mesh


@dataclass
class N3ChrPlug:
    name: str = ""
    plug_type: int = 0
    joint_index: int = 0
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rot_matrix: list[list[float]] = field(default_factory=lambda: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    material: Material = field(default_factory=Material)
    mesh_path: str = ""
    texture_path: str = ""
    trace_step: int = 0
    trace_color: int = 0
    trace0: float = 0.0
    trace1: float = 0.0
    embedded_mesh: bytes = b""
    chr_path: str = ""
    tail: PlugTail = field(default_factory=PlugTail)

    @property
    def version(self) -> int:
        return self.tail.version

    @property
    def race_blocks(self) -> list[PlugBlock]:
        return self.tail.race_blocks

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3ChrPlug":
        p = cls()
        p.name = reader.read_n3_name()
        p.plug_type = reader.read_int32()
        p.joint_index = reader.read_int32()
        p.position = reader.read_float3()
        p.rot_matrix = reader.read_matrix4x4()
        p.scale = reader.read_float3()
        p.material = Material.read(reader)
        p.mesh_path = reader.read_n3_path()
        p.texture_path = reader.read_n3_path()
        p.trace_step = reader.read_int32()
        if p.trace_step > 0:
            p.trace_color = reader.read_dword()
            p.trace0 = reader.read_float()
            p.trace1 = reader.read_float()
        p.embedded_mesh = read_embedded_mesh(reader)
        p.chr_path = reader.read_n3_path()
        p.tail = PlugTail.read(reader)
        return p
