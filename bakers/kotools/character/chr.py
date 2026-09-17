"""N3Chr — Character assembly manifest.

References joint, parts, plugs, animation, and FX by file path.

Binary layout:
  [N3 name string]
  [transform: pos, rot, scale, key_pos, key_rot, key_scale]
  [collision: collisionMeshPath, climbMeshPath]
  [path] jointFilePath
  [int32] partCount
  for each: [path] partFilePath (.n3cpart)
  [int32] plugCount
  for each: [path] plugFilePath (.n3cplug)
  [path] animCtrlFilePath (.n3anim)
  [int32[2]] jointPartStarts
  [int32[2]] jointPartEnds
  [path] fxPlugFilePath (.n3fxplug)
"""

from dataclasses import dataclass, field
from pathlib import Path

from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..skeleton.animkey import AnimKey


@dataclass
class N3Chr:
    """Character assembly — top-level file referencing all character resources."""
    name: str = ""
    # Transform
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    key_pos: AnimKey = field(default_factory=AnimKey)
    key_rot: AnimKey = field(default_factory=AnimKey)
    key_scale: AnimKey = field(default_factory=AnimKey)
    # Collision
    collision_mesh_path: str = ""
    climb_mesh_path: str = ""
    # Character resources
    joint_path: str = ""
    part_paths: list[str] = field(default_factory=list)
    plug_paths: list[str] = field(default_factory=list)
    anim_ctrl_path: str = ""
    joint_part_starts: tuple[int, int] = (0, 0)
    joint_part_ends: tuple[int, int] = (0, 0)
    fx_plug_path: str = ""

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3Chr":
        name = reader.read_n3_name()

        pos = reader.read_float3()
        rot = reader.read_float4()
        scl = reader.read_float3()
        kp = AnimKey.from_reader(reader)
        kr = AnimKey.from_reader(reader)
        ks = AnimKey.from_reader(reader)

        col_path = reader.read_n3_path()
        climb_path = reader.read_n3_path()

        joint_path = reader.read_n3_path()

        part_count = reader.read_int32()
        part_paths = [reader.read_n3_path() for _ in range(part_count)]

        plug_count = reader.read_int32()
        plug_paths = [reader.read_n3_path() for _ in range(plug_count)]

        anim_path = reader.read_n3_path()

        jp_starts = (0, 0)
        jp_ends = (0, 0)
        fx_path = ""

        if reader.remaining() >= 16:
            jp_starts = (reader.read_int32(), reader.read_int32())
            jp_ends = (reader.read_int32(), reader.read_int32())
        if reader.remaining() >= 4:
            fx_path = reader.read_n3_path()

        return cls(
            name=name, position=pos, rotation=rot, scale=scl,
            key_pos=kp, key_rot=kr, key_scale=ks,
            collision_mesh_path=col_path, climb_mesh_path=climb_path,
            joint_path=joint_path, part_paths=part_paths, plug_paths=plug_paths,
            anim_ctrl_path=anim_path, joint_part_starts=jp_starts,
            joint_part_ends=jp_ends, fx_plug_path=fx_path,
        )

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
        w.write_n3_path(self.joint_path)
        w.write_int32(len(self.part_paths))
        for p in self.part_paths:
            w.write_n3_path(p)
        w.write_int32(len(self.plug_paths))
        for p in self.plug_paths:
            w.write_n3_path(p)
        w.write_n3_path(self.anim_ctrl_path)
        w.write_int32(self.joint_part_starts[0])
        w.write_int32(self.joint_part_starts[1])
        w.write_int32(self.joint_part_ends[0])
        w.write_int32(self.joint_part_ends[1])
        w.write_n3_path(self.fx_plug_path)
        return w.getvalue()

    def all_referenced_paths(self) -> list[str]:
        """List all external file paths referenced by this character."""
        paths = [self.joint_path, self.anim_ctrl_path]
        paths.extend(self.part_paths)
        paths.extend(self.plug_paths)
        if self.collision_mesh_path:
            paths.append(self.collision_mesh_path)
        if self.climb_mesh_path:
            paths.append(self.climb_mesh_path)
        if self.fx_plug_path:
            paths.append(self.fx_plug_path)
        return [p for p in paths if p]


def read_chr(path: str | Path) -> N3Chr:
    with open(str(path), "rb") as f:
        data = f.read()
    return N3Chr.from_reader(BinaryReader(data))


def write_chr(chr_data: N3Chr, path: str | Path):
    with open(str(path), "wb") as f:
        f.write(chr_data.to_bytes())
