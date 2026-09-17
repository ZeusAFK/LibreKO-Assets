"""N3CPart — Character part (material + texture + skins reference).

Binary layout:
  [N3 name string]
  [int32]   version   (0 = original; 1 = adds a second texture path)
  [Material] material  (92 bytes)
  [path]    texture_path (.dxt)
  if version == 1:
    [path]  texture_diffuse_path (.dxt — additional diffuse texture)
  [path]    skins_path (.n3cskins)
"""

from dataclasses import dataclass, field
from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..formats.material import Material


@dataclass
class N3CPart:
    """Character body part definition."""
    name: str = ""
    version: int = 0
    material: Material = field(default_factory=Material)
    texture_path: str = ""
    texture_diffuse_path: str = ""  # only present when version == 1
    skins_path: str = ""

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3CPart":
        name = reader.read_n3_name()
        version = reader.read_dword()
        material = Material.read(reader)
        texture_path = reader.read_n3_path()
        texture_diffuse_path = reader.read_n3_path() if version == 1 else ""
        skins_path = reader.read_n3_path()
        return cls(name=name, version=version, material=material,
                   texture_path=texture_path,
                   texture_diffuse_path=texture_diffuse_path,
                   skins_path=skins_path)

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_n3_name(self.name)
        w.write_dword(self.version)
        self.material.write(w)
        w.write_n3_path(self.texture_path)
        if self.version == 1:
            w.write_n3_path(self.texture_diffuse_path)
        w.write_n3_path(self.skins_path)
        return w.getvalue()
