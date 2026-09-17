"""N3IMesh — Indexed Mesh (base for the skinned mesh).

Binary layout:
  [N3 name string]
  [int32] faceCount
  [int32] vertexCount
  [int32] uvCount
  if faceCount > 0 and vertexCount > 0:
    [VertexXyzNormal[vertexCount]]  — 24 bytes each
    [uint16[faceCount * 3]]         — vertex indices
  if uvCount > 0:
    [float[uvCount * 2]]            — UV coordinates
    [uint16[faceCount * 3]]         — UV indices
"""

from dataclasses import dataclass, field
from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter


@dataclass
class VertexXyzNormal:
    """Position + Normal (24 bytes)."""
    x: float; y: float; z: float
    nx: float; ny: float; nz: float

    @classmethod
    def read(cls, r: BinaryReader) -> "VertexXyzNormal":
        return cls(*r.read_float_array(6))

    def write(self, w: BinaryWriter):
        w.write_float_array([self.x, self.y, self.z, self.nx, self.ny, self.nz])


@dataclass
class N3IMesh:
    """Indexed mesh with vertices, faces, and UV data."""
    name: str
    face_count: int = 0
    vertex_count: int = 0
    uv_count: int = 0
    vertices: list[VertexXyzNormal] = field(default_factory=list)
    vertex_indices: list[int] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    uv_indices: list[int] = field(default_factory=list)

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3IMesh":
        name = reader.read_n3_name()
        fc = reader.read_int32()
        vc = reader.read_int32()
        uvc = reader.read_int32()

        vertices = []
        vertex_indices = []
        if fc > 0 and vc > 0:
            vertices = [VertexXyzNormal.read(reader) for _ in range(vc)]
            vertex_indices = [reader.read_uint16() for _ in range(fc * 3)]

        uvs = []
        uv_indices = []
        if uvc > 0:
            uvs = [(reader.read_float(), reader.read_float()) for _ in range(uvc)]
            uv_indices = [reader.read_uint16() for _ in range(fc * 3)]

        return cls(name=name, face_count=fc, vertex_count=vc, uv_count=uvc,
                   vertices=vertices, vertex_indices=vertex_indices, uvs=uvs, uv_indices=uv_indices)

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_n3_name(self.name)
        # Derive counts from actual data so header always matches content
        fc = len(self.vertex_indices) // 3
        vc = len(self.vertices)
        uvc = len(self.uvs)
        w.write_int32(fc)
        w.write_int32(vc)
        w.write_int32(uvc)
        if fc > 0 and vc > 0:
            for v in self.vertices:
                v.write(w)
            for idx in self.vertex_indices:
                w.write_uint16(idx)
        if uvc > 0:
            for u, v in self.uvs:
                w.write_float(u)
                w.write_float(v)
            for idx in self.uv_indices:
                w.write_uint16(idx)
        return w.getvalue()
