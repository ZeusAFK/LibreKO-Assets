"""N3Joint — recursive bone tree with transforms and animation keys.

Binary layout (recursive):
  [N3 name string]               — bone name
  [transform]:
    [float3]  position
    [float4]  rotation (quaternion: x, y, z, w)
    [float3]  scale
    [AnimKey]  key_pos
    [AnimKey]  key_rot
    [AnimKey]  key_scale
  [AnimKey]  key_orient           — joint orient
  [int32]  childCount
  for each child:
    [N3Joint]                     — recursive
"""

from dataclasses import dataclass, field
from pathlib import Path

from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from .animkey import AnimKey


@dataclass
class N3Joint:
    """Bone/joint in a skeleton hierarchy."""
    name: str = ""
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    key_pos: AnimKey = field(default_factory=AnimKey)
    key_rot: AnimKey = field(default_factory=AnimKey)
    key_scale: AnimKey = field(default_factory=AnimKey)
    key_orient: AnimKey = field(default_factory=AnimKey)
    children: list["N3Joint"] = field(default_factory=list)

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3Joint":
        name = reader.read_n3_name()
        position = reader.read_float3()
        rotation = reader.read_float4()
        scale = reader.read_float3()
        key_pos = AnimKey.from_reader(reader)
        key_rot = AnimKey.from_reader(reader)
        key_scale = AnimKey.from_reader(reader)
        key_orient = AnimKey.from_reader(reader)

        child_count = reader.read_int32()
        children = [N3Joint.from_reader(reader) for _ in range(child_count)]

        return cls(
            name=name, position=position, rotation=rotation, scale=scale,
            key_pos=key_pos, key_rot=key_rot, key_scale=key_scale,
            key_orient=key_orient, children=children,
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
        w.write_bytes(self.key_orient.to_bytes())
        w.write_int32(len(self.children))
        for child in self.children:
            w.write_bytes(child.to_bytes())
        return w.getvalue()

    def flatten(self, parent_index: int = -1) -> list[tuple["N3Joint", int]]:
        """Flatten tree to list of (joint, parent_index) pairs."""
        result: list[tuple["N3Joint", int]] = []
        self._flatten_into(result, parent_index)
        return result

    def _flatten_into(self, result: list[tuple["N3Joint", int]], parent_index: int):
        my_index = len(result)
        result.append((self, parent_index))
        for child in self.children:
            child._flatten_into(result, my_index)

    def bone_count(self) -> int:
        return 1 + sum(c.bone_count() for c in self.children)

    def find_bone(self, name: str) -> "N3Joint | None":
        if self.name == name:
            return self
        for child in self.children:
            found = child.find_bone(name)
            if found:
                return found
        return None


def read_joint(path: str | Path) -> N3Joint:
    with open(str(path), "rb") as f:
        data = f.read()
    return N3Joint.from_reader(BinaryReader(data))


def write_joint(joint: N3Joint, path: str | Path):
    with open(str(path), "wb") as f:
        f.write(joint.to_bytes())
