"""N3Skin — Skinned mesh with bone weights, extends N3IMesh.

After N3IMesh data, for each vertex:
  [float3]  vOrigin (12 bytes)
  [int32]   nAffect (bone count)
  [int32]   pnJoints_placeholder (ignored, set to 0)
  [float]   pfWeights_placeholder (ignored, set to 0)
  if nAffect > 1:
    [int32[nAffect]]   bone joint indices
    [float[nAffect]]   bone weights
  elif nAffect == 1:
    [int32]            single bone index
"""

from dataclasses import dataclass, field
from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from .imesh import N3IMesh


@dataclass
class SkinVertex:
    """Per-vertex skinning data."""
    origin: tuple[float, float, float]
    joint_indices: list[int] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)


@dataclass
class N3Skin:
    """Skinned mesh with bone weight data."""
    mesh: N3IMesh
    skin_vertices: list[SkinVertex] = field(default_factory=list)

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3Skin":
        mesh = N3IMesh.from_reader(reader)
        skin_verts = []

        for _ in range(mesh.vertex_count):
            ox, oy, oz = reader.read_float3()
            n_affect = reader.read_int32()
            reader.skip(4)  # pnJoints placeholder
            reader.skip(4)  # pfWeights placeholder

            joints = []
            weights = []
            if n_affect > 1:
                joints = [reader.read_int32() for _ in range(n_affect)]
                weights = [reader.read_float() for _ in range(n_affect)]
            elif n_affect == 1:
                joints = [reader.read_int32()]
                weights = [1.0]

            skin_verts.append(SkinVertex(origin=(ox, oy, oz), joint_indices=joints, weights=weights))

        return cls(mesh=mesh, skin_vertices=skin_verts)

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_bytes(self.mesh.to_bytes())
        for sv in self.skin_vertices:
            w.write_float3(*sv.origin)
            w.write_int32(len(sv.joint_indices))
            w.write_int32(0)  # placeholder
            w.write_float(0.0)  # placeholder
            if len(sv.joint_indices) > 1:
                for j in sv.joint_indices:
                    w.write_int32(j)
                for wt in sv.weights:
                    w.write_float(wt)
            elif len(sv.joint_indices) == 1:
                w.write_int32(sv.joint_indices[0])
        return w.getvalue()
