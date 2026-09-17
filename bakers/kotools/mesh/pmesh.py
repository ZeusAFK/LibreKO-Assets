"""N3PMesh — Progressive Mesh format (.n3pmesh).

Binary layout (after the N3 name string):
  [int32] numCollapses
  [int32] totalIndexChanges
  [int32] maxNumVertices
  [int32] maxNumIndices
  [int32] minNumVertices
  [int32] minNumIndices
  [VertexT1[maxNumVertices]]  — 32 bytes each (pos + normal + uv)
  [uint16[maxNumIndices]]     — triangle indices
  [EdgeCollapse[numCollapses]] — 20 bytes each (if numCollapses > 0)
  [int32[totalIndexChanges]]   — (if totalIndexChanges > 0)
  [int32] lodCtrlCount
  [LODCtrlValue[lodCtrlCount]] — 8 bytes each (if count > 0)
"""

from dataclasses import dataclass, field
from pathlib import Path

from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter


@dataclass
class Vertex:
    """__VertexT1: position + normal + UV (32 bytes)."""
    x: float; y: float; z: float
    nx: float; ny: float; nz: float
    tu: float; tv: float

    @classmethod
    def read(cls, r: BinaryReader) -> "Vertex":
        return cls(*r.read_float_array(8))

    def write(self, w: BinaryWriter):
        w.write_float_array([self.x, self.y, self.z, self.nx, self.ny, self.nz, self.tu, self.tv])


@dataclass
class EdgeCollapse:
    """Edge collapse record (20 bytes)."""
    num_indices_to_lose: int
    num_indices_to_change: int
    num_vertices_to_lose: int
    index_changes_offset: int
    collapse_to: int

    @classmethod
    def read(cls, r: BinaryReader) -> "EdgeCollapse":
        return cls(r.read_int32(), r.read_int32(), r.read_int32(), r.read_int32(), r.read_int32())

    def write(self, w: BinaryWriter):
        w.write_int32(self.num_indices_to_lose)
        w.write_int32(self.num_indices_to_change)
        w.write_int32(self.num_vertices_to_lose)
        w.write_int32(self.index_changes_offset)
        w.write_int32(self.collapse_to)


@dataclass
class LODCtrlValue:
    """LOD distance threshold (8 bytes)."""
    distance: float
    num_vertices: int

    @classmethod
    def read(cls, r: BinaryReader) -> "LODCtrlValue":
        return cls(r.read_float(), r.read_int32())

    def write(self, w: BinaryWriter):
        w.write_float(self.distance)
        w.write_int32(self.num_vertices)


@dataclass
class N3PMesh:
    """Progressive mesh with LOD support."""
    name: str
    vertices: list[Vertex] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    collapses: list[EdgeCollapse] = field(default_factory=list)
    index_changes: list[int] = field(default_factory=list)
    lod_ctrl: list[LODCtrlValue] = field(default_factory=list)
    max_vertices: int = 0
    max_indices: int = 0
    min_vertices: int = 0
    min_indices: int = 0

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3PMesh":
        name = reader.read_n3_name()
        num_collapses = reader.read_int32()
        total_idx_changes = reader.read_int32()
        max_verts = reader.read_int32()
        max_idxs = reader.read_int32()
        min_verts = reader.read_int32()
        min_idxs = reader.read_int32()

        vertices = [Vertex.read(reader) for _ in range(max_verts)]
        indices = [reader.read_uint16() for _ in range(max_idxs)]

        collapses = []
        if num_collapses > 0:
            collapses = [EdgeCollapse.read(reader) for _ in range(num_collapses)]

        index_changes = []
        if total_idx_changes > 0:
            index_changes = [reader.read_int32() for _ in range(total_idx_changes)]

        lod_ctrl = []
        if reader.remaining() >= 4:
            lod_count = reader.read_int32()
            if lod_count > 0 and reader.remaining() >= lod_count * 8:
                lod_ctrl = [LODCtrlValue.read(reader) for _ in range(lod_count)]

        return cls(
            name=name, vertices=vertices, indices=indices,
            collapses=collapses, index_changes=index_changes, lod_ctrl=lod_ctrl,
            max_vertices=max_verts, max_indices=max_idxs,
            min_vertices=min_verts, min_indices=min_idxs,
        )

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_n3_name(self.name)
        w.write_int32(len(self.collapses))
        w.write_int32(len(self.index_changes))
        w.write_int32(len(self.vertices))
        w.write_int32(len(self.indices))
        # When no collapse records exist, the game client uses min as the
        # initial (and only) LOD level — so min must equal max.
        min_v = self.min_vertices if self.collapses else len(self.vertices)
        min_i = self.min_indices if self.collapses else len(self.indices)
        w.write_int32(min_v)
        w.write_int32(min_i)
        for v in self.vertices:
            v.write(w)
        for idx in self.indices:
            w.write_uint16(idx)
        for c in self.collapses:
            c.write(w)
        for ic in self.index_changes:
            w.write_int32(ic)
        w.write_int32(len(self.lod_ctrl))
        for lod in self.lod_ctrl:
            lod.write(w)
        return w.getvalue()


def read_pmesh(path: str | Path) -> N3PMesh:
    with open(str(path), "rb") as f:
        data = f.read()
    return N3PMesh.from_reader(BinaryReader(data))


def write_pmesh(mesh: N3PMesh, path: str | Path):
    with open(str(path), "wb") as f:
        f.write(mesh.to_bytes())
