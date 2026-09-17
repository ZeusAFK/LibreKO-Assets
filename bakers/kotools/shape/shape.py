"""N3Shape — Static shape/object (buildings, trees, weapons, etc.)."""

from dataclasses import dataclass, field
from pathlib import Path

from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..formats.material import Material
from ..skeleton.animkey import AnimKey


@dataclass
class N3SPart:
    """Shape part: pivot + mesh + material + textures."""
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0)
    mesh_path: str = ""
    material: Material = field(default_factory=Material)
    texture_paths: list[str] = field(default_factory=list)
    tex_fps: float = 0.0

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3SPart":
        pivot = reader.read_float3()
        mesh_path = reader.read_n3_path()
        material = Material.read(reader)
        tex_count = reader.read_int32()
        tex_fps = reader.read_float()
        texture_paths = [reader.read_n3_path() for _ in range(tex_count)]
        return cls(pivot=pivot, mesh_path=mesh_path, material=material,
                   texture_paths=texture_paths, tex_fps=tex_fps)

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_float3(*self.pivot)
        w.write_n3_path(self.mesh_path)
        self.material.write(w)
        w.write_int32(len(self.texture_paths))
        w.write_float(self.tex_fps)
        for tp in self.texture_paths:
            w.write_n3_path(tp)
        return w.getvalue()


@dataclass
class N3Shape:
    """Static shape/object with multiple parts."""
    name: str = ""
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    key_pos: AnimKey = field(default_factory=AnimKey)
    key_rot: AnimKey = field(default_factory=AnimKey)
    key_scale: AnimKey = field(default_factory=AnimKey)
    collision_mesh_path: str = ""
    climb_mesh_path: str = ""
    parts: list[N3SPart] = field(default_factory=list)
    belong: int = 0
    event_id: int = 0
    event_type: int = 0
    npc_id: int = 0
    npc_status: int = 0

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3Shape":
        name = reader.read_n3_name()
        pos = reader.read_float3()
        rot = reader.read_float4()
        scl = reader.read_float3()
        kp = AnimKey.from_reader(reader)
        kr = AnimKey.from_reader(reader)
        ks = AnimKey.from_reader(reader)
        col = reader.read_n3_path()
        climb = reader.read_n3_path()
        part_count = reader.read_int32()
        parts = [N3SPart.from_reader(reader) for _ in range(part_count)]
        belong = reader.read_int32()
        event_id = reader.read_int32()
        event_type = reader.read_int32()
        npc_id = reader.read_int32()
        npc_status = reader.read_int32()
        return cls(name=name, position=pos, rotation=rot, scale=scl,
                   key_pos=kp, key_rot=kr, key_scale=ks,
                   collision_mesh_path=col, climb_mesh_path=climb,
                   parts=parts, belong=belong, event_id=event_id,
                   event_type=event_type, npc_id=npc_id, npc_status=npc_status)

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_n3_name(self.name)
        w.write_float3(*self.position)
        w.write_float4(*self.rotation)
        w.write_float3(*self.scale)
        w.write_bytes(self.key_pos.to_bytes())
        w.write_bytes(self.key_rot.to_bytes())
        w.write_bytes(self.key_scale.to_bytes())
        w.write_n3_path(self.collision_mesh_path)
        w.write_n3_path(self.climb_mesh_path)
        w.write_int32(len(self.parts))
        for p in self.parts:
            w.write_bytes(p.to_bytes())
        w.write_int32(self.belong)
        w.write_int32(self.event_id)
        w.write_int32(self.event_type)
        w.write_int32(self.npc_id)
        w.write_int32(self.npc_status)
        return w.getvalue()


def read_shape(path: str | Path) -> N3Shape:
    with open(str(path), "rb") as f:
        data = f.read()
    return N3Shape.from_reader(BinaryReader(data))


def write_shape(shape: N3Shape, path: str | Path):
    with open(str(path), "wb") as f:
        f.write(shape.to_bytes())
