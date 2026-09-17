"""N3CPartSkins — 4 LOD levels of skinned mesh."""

from dataclasses import dataclass, field
from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..mesh.imesh import N3IMesh
from ..mesh.skin import N3Skin

MAX_CHR_LOD = 4
LOD_NAMES = ("high", "middle", "low", "lowest")


@dataclass
class N3CPartSkins:
    """Character part skins — 4 LOD levels."""
    name: str = ""
    lod_skins: list[N3Skin] = field(default_factory=list)

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3CPartSkins":
        name = reader.read_n3_name()
        skins = [N3Skin.from_reader(reader) for _ in range(MAX_CHR_LOD)]
        return cls(name=name, lod_skins=skins)

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_n3_name(self.name)
        # Game client always reads exactly MAX_CHR_LOD skins;
        # pad with empty skins if fewer are provided.
        for i in range(MAX_CHR_LOD):
            if i < len(self.lod_skins):
                w.write_bytes(self.lod_skins[i].to_bytes())
            else:
                empty = N3Skin(mesh=N3IMesh(name=LOD_NAMES[i]), skin_vertices=[])
                w.write_bytes(empty.to_bytes())
        return w.getvalue()

    @property
    def highest_lod(self) -> N3Skin | None:
        for skin in self.lod_skins:
            if skin.mesh.vertex_count > 0:
                return skin
        return None
